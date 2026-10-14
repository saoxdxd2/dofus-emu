package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
)

var (
	bufPool = sync.Pool{
		New: func() interface{} {
			return make([]byte, 32*1024)
		},
	}
	reHeadOpen  = regexp.MustCompile(`(?i)<head[^>]*>`)
	reHeadClose = regexp.MustCompile(`(?i)</head>`)
	reHtmlOpen  = regexp.MustCompile(`(?i)<html[^>]*>`)
	reOsVer     = regexp.MustCompile(`Android\s+([0-9.]+)`)
	reBuildId   = regexp.MustCompile(`Build/([^\s;)]+)`)
)

// DNSCacheEntry holds cached IP addresses and expiry time
type DNSCacheEntry struct {
	IPs       []net.IP
	ExpiresAt time.Time
}

// FastDNSResolver provides in-memory thread-safe DNS caching
type FastDNSResolver struct {
	mu         sync.RWMutex
	cache      map[string]DNSCacheEntry
	ttl        time.Duration
	customDNS  string
	netResolve *net.Resolver
}

func NewFastDNSResolver(customDNS string, ttl time.Duration) *FastDNSResolver {
	r := &FastDNSResolver{
		cache:     make(map[string]DNSCacheEntry),
		ttl:       ttl,
		customDNS: customDNS,
	}

	if customDNS != "" {
		if !strings.Contains(customDNS, ":") {
			customDNS += ":53"
		}
		r.netResolve = &net.Resolver{
			PreferGo: true,
			Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
				d := net.Dialer{Timeout: 3 * time.Second}
				return d.DialContext(ctx, "udp", customDNS)
			},
		}
	} else {
		r.netResolve = net.DefaultResolver
	}
	return r
}

func (r *FastDNSResolver) Lookup(ctx context.Context, host string) ([]net.IP, error) {
	h, _, err := net.SplitHostPort(host)
	if err == nil {
		host = h
	}

	if ip := net.ParseIP(host); ip != nil {
		return []net.IP{ip}, nil
	}

	r.mu.RLock()
	entry, found := r.cache[host]
	r.mu.RUnlock()

	if found && time.Now().Before(entry.ExpiresAt) {
		return entry.IPs, nil
	}

	ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
	if err != nil {
		if r.netResolve != net.DefaultResolver {
			ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
		}
		if err != nil {
			return nil, err
		}
	}

	r.mu.Lock()
	if len(r.cache) >= 1024 {
		now := time.Now()
		for k, v := range r.cache {
			if now.After(v.ExpiresAt) {
				delete(r.cache, k)
			}
		}
		if len(r.cache) >= 1024 {
			r.cache = make(map[string]DNSCacheEntry)
		}
	}
	r.cache[host] = DNSCacheEntry{
		IPs:       ips,
		ExpiresAt: time.Now().Add(r.ttl),
	}
	r.mu.Unlock()

	return ips, nil
}

// parseUpstreamProxy normalizes any proxy string format:
// - host:port:user:pass
// - socks5://host:port:user:pass
// - user:pass@host:port
// - http://user:pass@host:port
// - socks5://user:pass@host:port
func parseUpstreamProxy(raw string) *url.URL {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	scheme := "http"
	rest := raw
	if idx := strings.Index(raw, "://"); idx != -1 {
		scheme = strings.ToLower(raw[:idx])
		rest = raw[idx+3:]
	}
	if !strings.Contains(rest, "@") {
		parts := strings.Split(rest, ":")
		if len(parts) == 4 {
			// host:port:user:pass -> scheme://user:pass@host:port
			u := &url.URL{
				Scheme: scheme,
				Host:   parts[0] + ":" + parts[1],
				User:   url.UserPassword(parts[2], parts[3]),
			}
			return u
		}
	}
	u, err := url.Parse(scheme + "://" + rest)
	if err != nil {
		return nil
	}
	return u
}

// dialUpstreamSocks5 performs an RFC 1928 / RFC 1929 SOCKS5 handshake (with domain-name resolution on the proxy to prevent DNS leaks)
func dialUpstreamSocks5(proxyURL *url.URL, targetHostPort string, timeout time.Duration) (net.Conn, error) {
	proxyAddr := proxyURL.Host
	if !strings.Contains(proxyAddr, ":") {
		proxyAddr += ":1080"
	}
	targetHost, targetPortStr, err := net.SplitHostPort(targetHostPort)
	if err != nil {
		targetHost = targetHostPort
		targetPortStr = "443"
	}
	var targetPort int
	_, _ = fmt.Sscanf(targetPortStr, "%d", &targetPort)
	if targetPort <= 0 || targetPort > 65535 {
		targetPort = 443
	}

	conn, err := net.DialTimeout("tcp", proxyAddr, timeout)
	if err != nil {
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(timeout))

	var user, pass string
	hasAuth := false
	if proxyURL.User != nil {
		user = proxyURL.User.Username()
		pass, _ = proxyURL.User.Password()
		if user != "" {
			hasAuth = true
		}
	}

	if hasAuth {
		_, err = conn.Write([]byte{0x05, 0x02, 0x00, 0x02})
	} else {
		_, err = conn.Write([]byte{0x05, 0x01, 0x00})
	}
	if err != nil {
		conn.Close()
		return nil, err
	}

	resp := make([]byte, 2)
	if _, err = io.ReadFull(conn, resp); err != nil {
		conn.Close()
		return nil, err
	}
	if resp[0] != 0x05 {
		conn.Close()
		return nil, errors.New("invalid SOCKS5 version")
	}

	if resp[1] == 0x02 {
		if len(user) > 255 || len(pass) > 255 {
			conn.Close()
			return nil, errors.New("SOCKS5 credentials too long")
		}
		authReq := make([]byte, 0, 3+len(user)+len(pass))
		authReq = append(authReq, 0x01, byte(len(user)))
		authReq = append(authReq, []byte(user)...)
		authReq = append(authReq, byte(len(pass)))
		authReq = append(authReq, []byte(pass)...)
		if _, err = conn.Write(authReq); err != nil {
			conn.Close()
			return nil, err
		}
		authResp := make([]byte, 2)
		if _, err = io.ReadFull(conn, authResp); err != nil {
			conn.Close()
			return nil, err
		}
		if authResp[1] != 0x00 {
			conn.Close()
			return nil, errors.New("SOCKS5 authentication rejected")
		}
	} else if resp[1] != 0x00 {
		conn.Close()
		return nil, errors.New("SOCKS5 auth method unsupported")
	}

	// Send CONNECT request using domain name (0x03) so DNS resolves remotely on the proxy
	req := make([]byte, 0, 7+len(targetHost))
	req = append(req, 0x05, 0x01, 0x00)
	if ip := net.ParseIP(targetHost); ip != nil && ip.To4() != nil {
		req = append(req, 0x01)
		req = append(req, ip.To4()...)
	} else {
		req = append(req, 0x03, byte(len(targetHost)))
		req = append(req, []byte(targetHost)...)
	}
	req = append(req, byte(targetPort>>8), byte(targetPort&0xFF))

	if _, err = conn.Write(req); err != nil {
		conn.Close()
		return nil, err
	}

	head := make([]byte, 4)
	if _, err = io.ReadFull(conn, head); err != nil {
		conn.Close()
		return nil, err
	}
	if head[1] != 0x00 {
		conn.Close()
		return nil, fmt.Errorf("SOCKS5 connect failed (code %d)", head[1])
	}

	var discardLen int
	switch head[3] {
	case 0x01:
		discardLen = 4 + 2
	case 0x03:
		lenByte := make([]byte, 1)
		if _, err = io.ReadFull(conn, lenByte); err != nil {
			conn.Close()
			return nil, err
		}
		discardLen = int(lenByte[0]) + 2
	case 0x04:
		discardLen = 16 + 2
	}
	if discardLen > 0 {
		trash := make([]byte, discardLen)
		if _, err = io.ReadFull(conn, trash); err != nil {
			conn.Close()
			return nil, err
		}
	}

	_ = conn.SetDeadline(time.Time{})
	return conn, nil
}

// ProxyServer handles HTTP, WebSocket, and HTTPS CONNECT proxy requests with full network spoofing
type ProxyServer struct {
	resolver       *FastDNSResolver
	upstreamURL    *url.URL
	cacheDir       string
	quiet          bool
	port           int
	authToken      string
	serial         string
	uuid           string
	model          string
	manufacturer   string
	ua             string
	osVer          string
	buildId        string
	glVendor       string
	glRenderer     string
	maxTextureSize string
	disguiseOnce   sync.Once
	cachedDisguise string
	client         *http.Client
}

func NewProxyServer(resolver *FastDNSResolver, upstream string, cacheDir string, quiet bool, port int, authToken string, serial string, uuid string, model string, manufacturer string, ua string, glVendor string, glRenderer string, maxTextureSize string) *ProxyServer {
	if model == "" {
		model = "SM-A515F"
	}
	if manufacturer == "" {
		manufacturer = "samsung"
	}
	if ua == "" {
		ua = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36"
	}
	osVer := "10"
	if m := reOsVer.FindStringSubmatch(ua); len(m) > 1 {
		osVer = m[1]
	}
	buildId := "QP1A.190711.020"
	if m := reBuildId.FindStringSubmatch(ua); len(m) > 1 {
		buildId = m[1]
	}

	p := &ProxyServer{
		resolver:       resolver,
		cacheDir:       cacheDir,
		quiet:          quiet,
		port:           port,
		authToken:      authToken,
		serial:         serial,
		uuid:           uuid,
		model:          model,
		manufacturer:   manufacturer,
		ua:             ua,
		osVer:          osVer,
		buildId:        buildId,
		glVendor:       glVendor,
		glRenderer:     glRenderer,
		maxTextureSize: maxTextureSize,
	}

	if cacheDir != "" {
		_ = os.MkdirAll(cacheDir, 0755)
	}

	p.upstreamURL = parseUpstreamProxy(upstream)
	isSocks := p.upstreamURL != nil && strings.HasPrefix(strings.ToLower(p.upstreamURL.Scheme), "socks5")

	transport := &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 50,
		IdleConnTimeout:     90 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			if isSocks {
				return dialUpstreamSocks5(p.upstreamURL, addr, 6*time.Second)
			}
			host, portStr, err := net.SplitHostPort(addr)
			if err != nil {
				host = addr
				portStr = "80"
			}
			ips, err := resolver.Lookup(ctx, host)
			if err != nil || len(ips) == 0 {
				return (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(ctx, network, addr)
			}
			targetAddr := net.JoinHostPort(ips[0].String(), portStr)
			return (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(ctx, network, targetAddr)
		},
	}

	if p.upstreamURL != nil && !isSocks {
		transport.Proxy = http.ProxyURL(p.upstreamURL)
	}

	p.client = &http.Client{
		Transport: transport,
		Timeout:   60 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}

	return p
}

// sanitizeRequestHeaders spoofs User-Agent, mobile Client Hints, and strips proxy/emulator leak headers
func (p *ProxyServer) sanitizeRequestHeaders(h http.Header) {
	// Strip proxy-revealing headers
	for _, k := range []string{
		"Proxy-Connection", "Proxy-Authenticate", "Proxy-Authorization",
		"Via", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
		"Forwarded", "X-Real-IP", "Client-IP",
	} {
		h.Del(k)
	}

	// Spoof User-Agent for both native Dalvik HTTP clients and WebView
	curUA := h.Get("User-Agent")
	lowerUA := strings.ToLower(curUA)
	if strings.HasPrefix(curUA, "Dalvik/") {
		h.Set("User-Agent", fmt.Sprintf("Dalvik/2.1.0 (Linux; U; Android %s; %s Build/%s)", p.osVer, p.model, p.buildId))
	} else if curUA == "" || strings.Contains(lowerUA, "x86") || strings.Contains(lowerUA, "sdk") || strings.Contains(lowerUA, "emulator") || strings.Contains(lowerUA, "goldfish") || strings.Contains(lowerUA, "ranchu") || strings.Contains(lowerUA, "mozilla/") {
		h.Set("User-Agent", p.ua)
	}

	// Normalize Chromium Client Hints if present so x86 architecture never leaks over HTTP
	if h.Get("Sec-CH-UA-Arch") != "" {
		h.Set("Sec-CH-UA-Arch", `"arm"`)
	}
	if h.Get("Sec-CH-UA-Platform") != "" {
		h.Set("Sec-CH-UA-Platform", `"Android"`)
	}
	if h.Get("Sec-CH-UA-Mobile") != "" {
		h.Set("Sec-CH-UA-Mobile", "?1")
	}
	if h.Get("Sec-CH-UA-Model") != "" {
		h.Set("Sec-CH-UA-Model", fmt.Sprintf(`"%s"`, p.model))
	}
	if h.Get("Accept-Language") == "" {
		h.Set("Accept-Language", "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")
	}
}

func (p *ProxyServer) dialUpstreamOrDirect(ctx context.Context, dest string) (net.Conn, error) {
	if p.upstreamURL != nil {
		scheme := strings.ToLower(p.upstreamURL.Scheme)
		if strings.HasPrefix(scheme, "socks5") {
			return dialUpstreamSocks5(p.upstreamURL, dest, 6*time.Second)
		}
		upstreamHost := p.upstreamURL.Host
		if !strings.Contains(upstreamHost, ":") {
			if scheme == "https" {
				upstreamHost += ":443"
			} else {
				upstreamHost += ":80"
			}
		}
		destConn, err := net.DialTimeout("tcp", upstreamHost, 5*time.Second)
		if err != nil {
			return nil, err
		}
		authHeader := ""
		if p.upstreamURL.User != nil {
			auth := base64.StdEncoding.EncodeToString([]byte(p.upstreamURL.User.String()))
			authHeader = fmt.Sprintf("Proxy-Authorization: Basic %s\r\n", auth)
		}
		connectReq := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n%s\r\n", dest, dest, p.ua, authHeader)
		if _, err = destConn.Write([]byte(connectReq)); err != nil {
			destConn.Close()
			return nil, err
		}
		br := bufio.NewReader(destConn)
		resp, err := http.ReadResponse(br, &http.Request{Method: "CONNECT"})
		if err != nil {
			destConn.Close()
			return nil, err
		}
		if resp.StatusCode != 200 {
			destConn.Close()
			return nil, fmt.Errorf("upstream rejected CONNECT: %s", resp.Status)
		}
		if br.Buffered() > 0 {
			_, _ = br.Discard(br.Buffered())
		}
		return destConn, nil
	}

	host, portStr, err := net.SplitHostPort(dest)
	if err != nil {
		host = dest
		portStr = "443"
	}
	ips, err := p.resolver.Lookup(ctx, host)
	target := dest
	if err == nil && len(ips) > 0 {
		target = net.JoinHostPort(ips[0].String(), portStr)
	}
	dialer := net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	return dialer.DialContext(ctx, "tcp", target)
}

func (p *ProxyServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if p.authToken != "" {
		auth := r.Header.Get("Proxy-Authorization")
		if auth == "" {
			auth = r.Header.Get("Authorization")
		}
		if auth == "" {
			auth = r.Header.Get("X-Proxy-Token")
		}
		expected := "Bearer " + p.authToken
		if auth != p.authToken && auth != expected {
			http.Error(w, "Proxy Authentication Required", http.StatusProxyAuthRequired)
			return
		}
	}

	if r.Method == http.MethodGet && r.URL.Path == "/__health" && (r.Host == "" || strings.HasPrefix(r.Host, "127.0.0.1") || strings.HasPrefix(r.Host, "localhost")) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintf(w, `{"status":"ok","port":%d,"model":"%s"}`, p.port, p.model)
		return
	}

	if r.Method == http.MethodConnect {
		p.handleConnect(w, r)
	} else if strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		p.handleWebSocket(w, r)
	} else {
		p.handleHTTP(w, r)
	}
}

func (p *ProxyServer) handleConnect(w http.ResponseWriter, r *http.Request) {
	dest := r.Host
	if !strings.Contains(dest, ":") {
		dest += ":443"
	}

	dialCtx, dialCancel := context.WithTimeout(context.Background(), 7*time.Second)
	defer dialCancel()

	destConn, err := p.dialUpstreamOrDirect(dialCtx, dest)
	if err != nil {
		http.Error(w, "Connection Failed: "+err.Error(), http.StatusBadGateway)
		return
	}

	hijacker, ok := w.(http.Hijacker)
	if !ok {
		destConn.Close()
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}

	clientConn, clientBuf, err := hijacker.Hijack()
	if err != nil {
		destConn.Close()
		http.Error(w, "Hijack failed: "+err.Error(), http.StatusServiceUnavailable)
		return
	}

	_ = clientConn.SetDeadline(time.Time{})
	_ = destConn.SetDeadline(time.Time{})

	if tc, ok := clientConn.(*net.TCPConn); ok {
		_ = tc.SetKeepAlive(true)
		_ = tc.SetKeepAlivePeriod(30 * time.Second)
		_ = tc.SetNoDelay(true)
	}
	if tc, ok := destConn.(*net.TCPConn); ok {
		_ = tc.SetKeepAlive(true)
		_ = tc.SetKeepAlivePeriod(30 * time.Second)
		_ = tc.SetNoDelay(true)
	}

	_, err = clientConn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	if err != nil {
		destConn.Close()
		clientConn.Close()
		return
	}

	if !p.quiet && strings.Contains(dest, "ankama") {
		log.Printf("[TUNNEL] CONNECT -> %s (%s)", dest, p.model)
	}

	type closeWriter interface {
		CloseWrite() error
	}

	done := make(chan struct{}, 2)
	go func() {
		buf := bufPool.Get().([]byte)
		defer bufPool.Put(buf)
		var clientReader io.Reader = clientConn
		if clientBuf != nil {
			clientReader = clientBuf
		}
		_, _ = io.CopyBuffer(destConn, clientReader, buf)
		if cw, ok := destConn.(closeWriter); ok {
			_ = cw.CloseWrite()
		} else {
			_ = destConn.Close()
		}
		done <- struct{}{}
	}()

	go func() {
		buf := bufPool.Get().([]byte)
		defer bufPool.Put(buf)
		_, _ = io.CopyBuffer(clientConn, destConn, buf)
		if cw, ok := clientConn.(closeWriter); ok {
			_ = cw.CloseWrite()
		} else {
			_ = clientConn.Close()
		}
		done <- struct{}{}
	}()

	<-done
	drainTimer := time.NewTimer(4 * time.Second)
	select {
	case <-done:
		drainTimer.Stop()
	case <-drainTimer.C:
	}
	_ = destConn.Close()
	_ = clientConn.Close()
}

func (p *ProxyServer) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "Webserver does not support hijacking", http.StatusInternalServerError)
		return
	}
	clientConn, clientBuf, err := hj.Hijack()
	if err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	defer clientConn.Close()

	dest := r.Host
	if !strings.Contains(dest, ":") {
		dest += ":80"
	}

	dialCtx, dialCancel := context.WithTimeout(context.Background(), 7*time.Second)
	defer dialCancel()

	destConn, err := p.dialUpstreamOrDirect(dialCtx, dest)
	if err != nil {
		return
	}
	defer destConn.Close()

	_ = clientConn.SetDeadline(time.Time{})
	_ = destConn.SetDeadline(time.Time{})

	p.sanitizeRequestHeaders(r.Header)
	r.RequestURI = ""
	if r.URL != nil {
		r.URL.Scheme = ""
		r.URL.Host = ""
	}

	if err := r.Write(destConn); err != nil {
		return
	}

	type closeWriter interface {
		CloseWrite() error
	}
	done := make(chan struct{}, 2)
	go func() {
		buf := bufPool.Get().([]byte)
		defer bufPool.Put(buf)
		var clientReader io.Reader = clientConn
		if clientBuf != nil {
			clientReader = clientBuf
		}
		_, _ = io.CopyBuffer(destConn, clientReader, buf)
		if cw, ok := destConn.(closeWriter); ok {
			_ = cw.CloseWrite()
		} else {
			_ = destConn.Close()
		}
		done <- struct{}{}
	}()
	go func() {
		buf := bufPool.Get().([]byte)
		defer bufPool.Put(buf)
		_, _ = io.CopyBuffer(clientConn, destConn, buf)
		if cw, ok := clientConn.(closeWriter); ok {
			_ = cw.CloseWrite()
		} else {
			_ = clientConn.Close()
		}
		done <- struct{}{}
	}()
	<-done
	drainTimer := time.NewTimer(4 * time.Second)
	select {
	case <-done:
		drainTimer.Stop()
	case <-drainTimer.C:
	}
}

func atomicReplaceFile(src, dst string) error {
	err := os.Rename(src, dst)
	if err == nil {
		return nil
	}
	for attempt := 0; attempt < 3; attempt++ {
		time.Sleep(20 * time.Millisecond)
		_ = os.Remove(dst)
		if err = os.Rename(src, dst); err == nil {
			return nil
		}
	}
	oldPath := fmt.Sprintf("%s.old.%d", dst, time.Now().UnixNano())
	if moveErr := os.Rename(dst, oldPath); moveErr == nil {
		_ = os.Remove(oldPath)
		if renErr := os.Rename(src, dst); renErr == nil {
			return nil
		}
	}
	return err
}

func detectMimeType(path string) string {
	lower := strings.ToLower(path)
	switch {
	case strings.HasSuffix(lower, ".wasm"):
		return "application/wasm"
	case strings.HasSuffix(lower, ".json"):
		return "application/json; charset=utf-8"
	case strings.HasSuffix(lower, ".png"):
		return "image/png"
	case strings.HasSuffix(lower, ".jpg"), strings.HasSuffix(lower, ".jpeg"):
		return "image/jpeg"
	case strings.HasSuffix(lower, ".webp"):
		return "image/webp"
	case strings.HasSuffix(lower, ".mp3"):
		return "audio/mpeg"
	case strings.HasSuffix(lower, ".ogg"):
		return "audio/ogg"
	case strings.HasSuffix(lower, ".js"):
		return "application/javascript; charset=utf-8"
	case strings.HasSuffix(lower, ".css"):
		return "text/css; charset=utf-8"
	case strings.HasSuffix(lower, ".html"), strings.HasSuffix(lower, ".htm"):
		return "text/html; charset=utf-8"
	default:
		return "application/octet-stream"
	}
}

func (p *ProxyServer) getDisguiseContent() string {
	p.disguiseOnce.Do(func() {
		p.cachedDisguise = p.loadDisguiseFromDisk()
	})
	return p.cachedDisguise
}

func (p *ProxyServer) loadDisguiseFromDisk() string {
	candidates := []string{
		filepath.Join("scripts", "mobile-disguise.js"),
		"mobile-disguise.js",
		filepath.Join(os.Getenv("DOFUS_FARM_HOME"), "scripts", "mobile-disguise.js"),
		filepath.Join(filepath.Dir(os.Args[0]), "scripts", "mobile-disguise.js"),
		filepath.Join(filepath.Dir(os.Args[0]), "mobile-disguise.js"),
	}
	for _, c := range candidates {
		if b, err := os.ReadFile(c); err == nil && len(b) > 0 {
			content := string(b)
			if p.serial != "" {
				content = strings.ReplaceAll(content, "__DEVICE_SERIAL__", p.serial)
			}
			if p.uuid != "" {
				content = strings.ReplaceAll(content, "__DEVICE_UUID__", p.uuid)
			}
			if p.model != "" {
				content = strings.ReplaceAll(content, "__DEVICE_MODEL__", p.model)
			}
			if p.manufacturer != "" {
				content = strings.ReplaceAll(content, "__DEVICE_MANUFACTURER__", p.manufacturer)
			}
			if p.ua != "" {
				content = strings.ReplaceAll(content, "__DEVICE_UA__", p.ua)
			}
			if p.glVendor != "" {
				content = strings.ReplaceAll(content, "__DEVICE_GL_VENDOR__", p.glVendor)
			}
			if p.glRenderer != "" {
				content = strings.ReplaceAll(content, "__DEVICE_GL_RENDERER__", p.glRenderer)
			}
			if p.maxTextureSize != "" {
				content = strings.ReplaceAll(content, "__DEVICE_MAX_TEXTURE_SIZE__", p.maxTextureSize)
			}
			return content
		}
	}
	return ""
}

func (p *ProxyServer) handleHTTP(w http.ResponseWriter, r *http.Request) {
	targetURL := r.URL
	if targetURL.Scheme == "" {
		targetURL.Scheme = "http"
	}
	if targetURL.Host == "" {
		targetURL.Host = r.Host
	}
	if targetURL.Host == "" {
		http.Error(w, "Bad Request: Missing host", http.StatusBadRequest)
		return
	}

	// 1. Direct serve of local stealth disguise script
	if strings.HasSuffix(targetURL.Path, "/__disguise.js") ||
		strings.HasSuffix(targetURL.Path, "/mobile-disguise.js") {
		code := p.getDisguiseContent()
		if code != "" {
			w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
			w.Header().Set("Access-Control-Allow-Origin", "*")
			w.Header().Set("Cache-Control", "no-cache")
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(code)))
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(code))
			return
		}
	}

	// 2. High-speed local disk cache for static Ankama assets (without any X-Cache header leak)
	var cacheFilePath string
	if p.cacheDir != "" && r.Method == http.MethodGet {
		lowerURL := strings.ToLower(targetURL.String())
		if strings.HasSuffix(lowerURL, ".png") ||
			strings.HasSuffix(lowerURL, ".jpg") ||
			strings.HasSuffix(lowerURL, ".jpeg") ||
			strings.HasSuffix(lowerURL, ".webp") ||
			strings.HasSuffix(lowerURL, ".mp3") ||
			strings.HasSuffix(lowerURL, ".ogg") ||
			strings.HasSuffix(lowerURL, ".wasm") ||
			strings.HasSuffix(lowerURL, ".zip") ||
			strings.HasSuffix(lowerURL, ".pak") ||
			strings.HasSuffix(lowerURL, ".dat") ||
			strings.Contains(lowerURL, "/assets/") {
			h := sha256.Sum256([]byte(targetURL.String()))
			cacheFilePath = filepath.Join(p.cacheDir, fmt.Sprintf("%x", h[:16]))
			if fi, err := os.Stat(cacheFilePath); err == nil {
				if time.Since(fi.ModTime()) > 7*24*time.Hour {
					_ = os.Remove(cacheFilePath)
					_ = os.Remove(cacheFilePath + ".meta")
				} else {
					if f, err := os.Open(cacheFilePath); err == nil {
						defer f.Close()
						w.Header().Set("Access-Control-Allow-Origin", "*")
						w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
						w.Header().Set("Access-Control-Allow-Headers", "*")
						mimeType := ""
						if metaBytes, metaErr := os.ReadFile(cacheFilePath + ".meta"); metaErr == nil {
							mimeType = strings.TrimSpace(string(metaBytes))
						}
						if mimeType == "" {
							mimeType = detectMimeType(targetURL.Path)
						}
						if mimeType != "" {
							w.Header().Set("Content-Type", mimeType)
						}
						w.Header().Set("Content-Length", fmt.Sprintf("%d", fi.Size()))
						w.WriteHeader(http.StatusOK)
						buf := bufPool.Get().([]byte)
						defer bufPool.Put(buf)
						_, _ = io.CopyBuffer(w, f, buf)
						return
					}
				}
			}
		}
	}

	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetURL.String(), r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	for k, vv := range r.Header {
		if strings.EqualFold(k, "Connection") ||
			strings.EqualFold(k, "Keep-Alive") ||
			strings.EqualFold(k, "TE") ||
			strings.EqualFold(k, "Trailers") ||
			strings.EqualFold(k, "Transfer-Encoding") ||
			strings.EqualFold(k, "Upgrade") {
			continue
		}
		for _, v := range vv {
			outReq.Header.Add(k, v)
		}
	}

	p.sanitizeRequestHeaders(outReq.Header)

	if ae := outReq.Header.Get("Accept-Encoding"); ae != "" {
		parts := strings.Split(ae, ",")
		var kept []string
		for _, part := range parts {
			trimmed := strings.TrimSpace(part)
			lower := strings.ToLower(trimmed)
			if lower == "br" || strings.HasPrefix(lower, "br;") || strings.HasPrefix(lower, "br ") {
				continue
			}
			if trimmed != "" {
				kept = append(kept, trimmed)
			}
		}
		if len(kept) == 0 {
			outReq.Header.Set("Accept-Encoding", "gzip, deflate")
		} else {
			outReq.Header.Set("Accept-Encoding", strings.Join(kept, ", "))
		}
	}

	resp, err := p.client.Do(outReq)
	if err != nil {
		http.Error(w, "Proxy Error: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for k, vv := range resp.Header {
		if strings.EqualFold(k, "Connection") ||
			strings.EqualFold(k, "Keep-Alive") ||
			strings.EqualFold(k, "Proxy-Authenticate") ||
			strings.EqualFold(k, "Proxy-Authorization") ||
			strings.EqualFold(k, "TE") ||
			strings.EqualFold(k, "Trailers") ||
			strings.EqualFold(k, "Transfer-Encoding") ||
			strings.EqualFold(k, "Upgrade") ||
			strings.EqualFold(k, "Content-Security-Policy") ||
			strings.EqualFold(k, "Content-Security-Policy-Report-Only") {
			continue
		}
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.Header().Set("Access-Control-Allow-Origin", "*")

	var bodyReader io.Reader = resp.Body
	if strings.EqualFold(resp.Header.Get("Content-Encoding"), "gzip") {
		gzReader, gzErr := gzip.NewReader(resp.Body)
		if gzErr == nil {
			defer gzReader.Close()
			bodyReader = gzReader
			w.Header().Del("Content-Encoding")
			w.Header().Del("Content-Length")
		}
	}

	ct := resp.Header.Get("Content-Type")
	if (strings.Contains(ct, "text/html") || strings.HasSuffix(strings.ToLower(targetURL.Path), ".html")) && resp.StatusCode == http.StatusOK {
		w.Header().Del("Content-Security-Policy")
		w.Header().Del("Content-Security-Policy-Report-Only")

		bodyBytes, readErr := io.ReadAll(bodyReader)
		if readErr == nil {
			bodyStr := string(bodyBytes)
			if !strings.Contains(bodyStr, "mobile-disguise.js") && !strings.Contains(bodyStr, "__disguise.js") {
				disguiseCode := p.getDisguiseContent()
				var injection string
				if disguiseCode != "" {
					injection = fmt.Sprintf("\n\t\t<script type=\"text/javascript\">\n%s\n\t\t</script>\n", disguiseCode)
				} else {
					injection = fmt.Sprintf("\n\t\t<script type=\"text/javascript\" src=\"http://10.0.2.2:%d/__disguise.js\"></script>\n", p.port)
				}

				if loc := reHeadOpen.FindStringIndex(bodyStr); loc != nil {
					bodyStr = bodyStr[:loc[1]] + injection + bodyStr[loc[1]:]
				} else if loc := reHeadClose.FindStringIndex(bodyStr); loc != nil {
					bodyStr = bodyStr[:loc[0]] + injection + bodyStr[loc[0]:]
				} else if loc := reHtmlOpen.FindStringIndex(bodyStr); loc != nil {
					bodyStr = bodyStr[:loc[1]] + "<head>" + injection + "</head>" + bodyStr[loc[1]:]
				} else {
					bodyStr = "<head>" + injection + "</head>" + bodyStr
				}
				bodyBytes = []byte(bodyStr)
			}
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(bodyBytes)))
			w.WriteHeader(resp.StatusCode)
			_, _ = w.Write(bodyBytes)
			return
		}
	}

	w.WriteHeader(resp.StatusCode)

	buf := bufPool.Get().([]byte)
	defer bufPool.Put(buf)

	if cacheFilePath != "" && resp.StatusCode == http.StatusOK {
		tmpPath := fmt.Sprintf("%s.%d.%d.tmp", cacheFilePath, os.Getpid(), time.Now().UnixNano())
		if tf, err := os.Create(tmpPath); err == nil {
			mw := io.MultiWriter(w, tf)
			written, copyErr := io.CopyBuffer(mw, bodyReader, buf)
			_ = tf.Close()

			isTruncated := (resp.ContentLength > 0 && written < resp.ContentLength)
			if copyErr != nil || isTruncated {
				_ = os.Remove(tmpPath)
				return
			}

			if repErr := atomicReplaceFile(tmpPath, cacheFilePath); repErr == nil {
				if ct := resp.Header.Get("Content-Type"); ct != "" {
					_ = os.WriteFile(cacheFilePath+".meta", []byte(ct), 0644)
				}
			} else {
				_ = os.Remove(tmpPath)
			}
			return
		}
	}

	_, _ = io.CopyBuffer(w, bodyReader, buf)
}

func main() {
	port := flag.Int("port", 8880, "Local listening port for HTTP/CONNECT proxy")
	host := flag.String("host", "127.0.0.1", "Host address to bind the proxy server to")
	upstream := flag.String("upstream", "", "Upstream proxy URL (optional, e.g. http://ip:port or socks5://ip:port)")
	dnsServer := flag.String("dns", "", "Custom DNS server to query (optional, default: system DNS)")
	cacheDir := flag.String("cache", "proxy_cache", "Directory to cache static game assets (optional)")
	quiet := flag.Bool("quiet", false, "Quiet mode (suppress request logs)")
	token := flag.String("token", "", "Optional local bearer authentication token")
	serial := flag.String("serial", "", "Hardware serial number to interpolate into disguise scripts")
	uuid := flag.String("uuid", "", "Device UUID to interpolate into disguise scripts")
	model := flag.String("model", "", "Device model name to interpolate into disguise scripts (e.g. SM-A515F)")
	manufacturer := flag.String("manufacturer", "", "Device manufacturer to interpolate (e.g. samsung)")
	ua := flag.String("ua", "", "Device User-Agent string to interpolate into disguise scripts")
	glVendor := flag.String("gl-vendor", "", "OpenGL vendor string to interpolate (e.g. Qualcomm)")
	glRenderer := flag.String("gl-renderer", "", "OpenGL renderer string to interpolate (e.g. Adreno (TM) 620)")
	maxTextureSize := flag.String("texture-size", "", "Maximum GL texture size to interpolate (e.g. 16384)")
	flag.Parse()

	log.SetFlags(log.Ldate | log.Ltime)
	resolver := NewFastDNSResolver(*dnsServer, 10*time.Minute)
	proxy := NewProxyServer(resolver, *upstream, *cacheDir, *quiet, *port, *token, *serial, *uuid, *model, *manufacturer, *ua, *glVendor, *glRenderer, *maxTextureSize)

	// No 120s ReadTimeout/WriteTimeout so long-lived streams and large asset downloads never drop at 120s
	server := &http.Server{
		Addr:              fmt.Sprintf("%s:%d", *host, *port),
		Handler:           proxy,
		ReadHeaderTimeout: 30 * time.Second,
		IdleTimeout:       300 * time.Second,
	}

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		log.Printf("[READY] Listening on http://%s:%d (Model: %s) ...", *host, *port, *model)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[FATAL] Proxy server failed: %v", err)
		}
	}()

	<-sigChan
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
}
