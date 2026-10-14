package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/base64"
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
	// Strip port if present
	h, _, err := net.SplitHostPort(host)
	if err == nil {
		host = h
	}

	// Check if already an IP
	if ip := net.ParseIP(host); ip != nil {
		return []net.IP{ip}, nil
	}

	// Check cache
	r.mu.RLock()
	entry, found := r.cache[host]
	r.mu.RUnlock()

	if found && time.Now().Before(entry.ExpiresAt) {
		return entry.IPs, nil
	}

	// Resolve
	ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
	if err != nil {
		// Fallback to default resolver if custom DNS timed out
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

// ProxyServer handles HTTP and HTTPS CONNECT proxy requests
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
	glVendor       string
	glRenderer     string
	maxTextureSize string
	disguiseOnce   sync.Once
	cachedDisguise string
	client         *http.Client
}

func NewProxyServer(resolver *FastDNSResolver, upstream string, cacheDir string, quiet bool, port int, authToken string, serial string, uuid string, model string, manufacturer string, ua string, glVendor string, glRenderer string, maxTextureSize string) *ProxyServer {
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
		glVendor:       glVendor,
		glRenderer:     glRenderer,
		maxTextureSize: maxTextureSize,
	}


	if cacheDir != "" {
		_ = os.MkdirAll(cacheDir, 0755)
	}

	if upstream != "" {
		if !strings.Contains(upstream, "://") {
			upstream = "http://" + upstream
		}
		u, err := url.Parse(upstream)
		if err == nil {
			p.upstreamURL = u
		}
	}

	transport := &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 50,
		IdleConnTimeout:     90 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(addr)
			if err != nil {
				host = addr
				port = "80"
			}
			ips, err := resolver.Lookup(ctx, host)
			if err != nil || len(ips) == 0 {
				return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, network, addr)
			}
			targetAddr := net.JoinHostPort(ips[0].String(), port)
			return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, network, targetAddr)
		},
	}

	if p.upstreamURL != nil {
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

	// Direct health-check endpoint: always accessible without proxy routing
	if r.Method == http.MethodGet && r.URL.Path == "/__health" && (r.Host == "" || strings.HasPrefix(r.Host, "127.0.0.1") || strings.HasPrefix(r.Host, "localhost")) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintf(w, `{"status":"ok","port":%d}`, p.port)
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

	host, port, err := net.SplitHostPort(dest)
	if err != nil {
		host = dest
		port = "443"
	}

	dialCtx, dialCancel := context.WithTimeout(context.Background(), 7*time.Second)
	defer dialCancel()

	var destConn net.Conn
	if p.upstreamURL != nil {
		// Route CONNECT via upstream proxy
		upstreamHost := p.upstreamURL.Host
		if !strings.Contains(upstreamHost, ":") {
			if strings.EqualFold(p.upstreamURL.Scheme, "https") {
				upstreamHost += ":443"
			} else {
				upstreamHost += ":80"
			}
		}
		destConn, err = net.DialTimeout("tcp", upstreamHost, 4*time.Second)
		if err != nil {
			http.Error(w, "Upstream Proxy Unreachable: "+err.Error(), http.StatusBadGateway)
			return
		}
		// Send CONNECT to upstream (with Proxy-Authorization if authenticated - NET-AUTH-01)
		authHeader := ""
		if p.upstreamURL.User != nil {
			auth := base64.StdEncoding.EncodeToString([]byte(p.upstreamURL.User.String()))
			authHeader = fmt.Sprintf("Proxy-Authorization: Basic %s\r\n", auth)
		}
		connectReq := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\n%s\r\n", dest, dest, authHeader)
		_, err = destConn.Write([]byte(connectReq))
		if err != nil {
			destConn.Close()
			http.Error(w, "Upstream CONNECT handshake failed", http.StatusBadGateway)
			return
		}
		// Read upstream response safely using bufio.Reader (BUG-16)
		br := bufio.NewReader(destConn)
		resp, err := http.ReadResponse(br, &http.Request{Method: "CONNECT"})
		if err != nil {
			destConn.Close()
			http.Error(w, "Upstream CONNECT failed to respond: "+err.Error(), http.StatusBadGateway)
			return
		}
		if resp.StatusCode != 200 {
			destConn.Close()
			http.Error(w, "Upstream rejected CONNECT: "+resp.Status, http.StatusBadGateway)
			return
		}
		// Discard any unexpected bytes before client's initial TLS ClientHello
		if br.Buffered() > 0 {
			_, _ = br.Discard(br.Buffered())
		}
	} else {
		// Direct connection with fast DNS caching
		ips, err := p.resolver.Lookup(dialCtx, host)
		target := dest
		if err == nil && len(ips) > 0 {
			target = net.JoinHostPort(ips[0].String(), port)
		}

		dialer := net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
		destConn, err = dialer.DialContext(dialCtx, "tcp", target)
		if err != nil {
			http.Error(w, "Connection Failed: "+err.Error(), http.StatusBadGateway)
			return
		}
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

	// Clear deadlines
	_ = clientConn.SetDeadline(time.Time{})
	_ = destConn.SetDeadline(time.Time{})

	// Enable TCP_NODELAY for minimum latency and immediate packet delivery
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

	// Respond 200 Connection Established to client
	_, err = clientConn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	if err != nil {
		destConn.Close()
		clientConn.Close()
		return
	}

	if !p.quiet && strings.Contains(dest, "ankama") {
		log.Printf("[TUNNEL] CONNECT -> %s (Fast Path Active)", dest)
	}

	// Bi-directional stream splice supporting TCP half-close (NET-01)
	type closeWriter interface {
		CloseWrite() error
	}

	done := make(chan struct{}, 2)
	go func() {
		buf := bufPool.Get().([]byte)
		defer bufPool.Put(buf)
		// Wrap clientBuf to preserve TLS early data pipelined in CONNECT segment (BUG-36)
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

	// Wait for first stream to complete, then give second stream up to 4s to drain or terminate (BUG-05)
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
	host, port, _ := net.SplitHostPort(dest)
	if host == "" {
		host = r.Host
		port = "80"
	}

	dialCtx, dialCancel := context.WithTimeout(context.Background(), 7*time.Second)
	defer dialCancel()

	var destConn net.Conn
	if p.upstreamURL != nil {
		upstreamHost := p.upstreamURL.Host
		if !strings.Contains(upstreamHost, ":") {
			upstreamHost += ":80"
		}
		destConn, err = net.DialTimeout("tcp", upstreamHost, 4*time.Second)
		if err != nil {
			return
		}
		authHeader := ""
		if p.upstreamURL.User != nil {
			auth := base64.StdEncoding.EncodeToString([]byte(p.upstreamURL.User.String()))
			authHeader = fmt.Sprintf("Proxy-Authorization: Basic %s\r\n", auth)
		}
		connectReq := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\n%s\r\n", dest, dest, authHeader)
		_, _ = destConn.Write([]byte(connectReq))
		br := bufio.NewReader(destConn)
		resp, err := http.ReadResponse(br, &http.Request{Method: "CONNECT"})
		if err != nil || resp.StatusCode != 200 {
			destConn.Close()
			return
		}
	} else {
		ips, err := p.resolver.Lookup(dialCtx, host)
		target := dest
		if err == nil && len(ips) > 0 {
			target = net.JoinHostPort(ips[0].String(), port)
		}
		dialer := net.Dialer{Timeout: 5 * time.Second}
		destConn, err = dialer.DialContext(dialCtx, "tcp", target)
		if err != nil {
			return
		}
	}
	defer destConn.Close()

	// Clear absolute URI to satisfy RFC 6455 absolute-path requirement (BUG-31)
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
		// Unified single upstream reader using clientBuf to eliminate stream stall / double read (BUG-32)
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
	case strings.HasSuffix(lower, ".zip"), strings.HasSuffix(lower, ".pak"), strings.HasSuffix(lower, ".dat"):
		return "application/octet-stream"
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

	// 1. Direct serve of local stealth disguise & behavior scripts
	if strings.HasSuffix(targetURL.Path, "/__disguise.js") ||
		strings.HasSuffix(targetURL.Path, "/mobile-disguise.js") {
		code := p.getDisguiseContent()
		if code != "" {
			w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
			w.Header().Set("Access-Control-Allow-Origin", "*")
			w.Header().Set("Cache-Control", "no-cache")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(code))
			return
		}
	}

	// 2. High-speed local disk cache for static Ankama assets (tiles, textures, sounds, wasm)
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
				// Enforce cache TTL (7 days max age) to prevent serving outdated assets after game patches (NET-03)
				if time.Since(fi.ModTime()) > 7*24*time.Hour {
					_ = os.Remove(cacheFilePath)
					_ = os.Remove(cacheFilePath + ".meta")
				} else {
					// Conditional revalidation if older than 24 hours (BUG-15)
					stale := false
					if time.Since(fi.ModTime()) > 24*time.Hour {
						headReq, headErr := http.NewRequestWithContext(r.Context(), "HEAD", targetURL.String(), nil)
						if headErr == nil {
							headReq.Header.Set("If-Modified-Since", fi.ModTime().UTC().Format(http.TimeFormat))
							headResp, headDoErr := p.client.Do(headReq)
							if headDoErr == nil {
								_ = headResp.Body.Close()
								if headResp.StatusCode == http.StatusOK {
									_ = os.Remove(cacheFilePath)
									_ = os.Remove(cacheFilePath + ".meta")
									stale = true
								} else if headResp.StatusCode == http.StatusNotModified {
									now := time.Now()
									_ = os.Chtimes(cacheFilePath, now, now)
								}
							}
						}
					}
					if !stale {
						if f, err := os.Open(cacheFilePath); err == nil {
							defer f.Close()
							w.Header().Set("X-Cache", "HIT-LOCAL-DISK")
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
	}

	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetURL.String(), r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	for k, vv := range r.Header {
		if strings.EqualFold(k, "Proxy-Connection") ||
			strings.EqualFold(k, "Connection") ||
			strings.EqualFold(k, "Keep-Alive") ||
			strings.EqualFold(k, "Proxy-Authenticate") ||
			strings.EqualFold(k, "Proxy-Authorization") ||
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

	if ae := outReq.Header.Get("Accept-Encoding"); ae != "" {
		// Strip Brotli cleanly without corrupting quality weights (e.g. br;q=1.0) into orphaned semicolons (VULN-NEW-06)
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

	// 3. Inject disguise script into HTML bootstrap if needed (NET-02)
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

	// 4. Thread-safe, non-colliding atomic cache writer (Windows NTFS safe)
	// Canonical uncompressed data is written to disk, guaranteeing clean decoding on cache hits
	if cacheFilePath != "" && resp.StatusCode == http.StatusOK {
		tmpPath := fmt.Sprintf("%s.%d.%d.tmp", cacheFilePath, os.Getpid(), time.Now().UnixNano())
		if tf, err := os.Create(tmpPath); err == nil {
			mw := io.MultiWriter(w, tf)
			written, copyErr := io.CopyBuffer(mw, bodyReader, buf)
			_ = tf.Close()

			// Check for partial or aborted writes (NET-VULN-03)
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
	upstream := flag.String("upstream", "", "Upstream proxy URL (optional, e.g. http://ip:port)")
	dnsServer := flag.String("dns", "", "Custom DNS server to query (optional, default: system DNS)")
	cacheDir := flag.String("cache", "proxy_cache", "Directory to cache static game assets (optional)")
	quiet := flag.Bool("quiet", false, "Quiet mode (suppress request logs)")
	token := flag.String("token", "", "Optional local bearer authentication token to prevent unauthorized loopback usage")
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
	fmt.Println("=====================================================================")
	fmt.Printf("   DOFUS TOUCH HIGH-PERFORMANCE GO NETWORK PROXY (%s:%d)\n", *host, *port)
	fmt.Println("=====================================================================")
	if *dnsServer != "" {
		log.Printf("[INIT] In-memory Predictive DNS Resolver active (custom: %s)", *dnsServer)
	} else {
		log.Printf("[INIT] In-memory Predictive DNS Resolver active (system OS DNS)")
	}
	if *upstream != "" {
		log.Printf("[INIT] Upstream Proxy configured: %s", *upstream)
	} else {
		log.Printf("[INIT] Direct Gigabit Routing active (zero-copy socket splicing)")
	}
	if *cacheDir != "" {
		log.Printf("[INIT] Static Game Asset Local Disk Cache active (%s)", *cacheDir)
	}
	if *token != "" {
		log.Printf("[INIT] Local Bearer Token Authentication active")
	}
	if *serial != "" || *uuid != "" {
		log.Printf("[INIT] Device Identity Interpolator active (Serial: %s, UUID: %s, Model: %s)", *serial, *uuid, *model)
	}

	resolver := NewFastDNSResolver(*dnsServer, 10*time.Minute)
	proxy := NewProxyServer(resolver, *upstream, *cacheDir, *quiet, *port, *token, *serial, *uuid, *model, *manufacturer, *ua, *glVendor, *glRenderer, *maxTextureSize)


	server := &http.Server{
		Addr:         fmt.Sprintf("%s:%d", *host, *port),
		Handler:      proxy,
		ReadTimeout:  120 * time.Second,
		WriteTimeout: 120 * time.Second,
	}

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		log.Printf("[READY] Listening on http://%s:%d ...", *host, *port)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[FATAL] Proxy server failed: %v", err)
		}
	}()

	<-sigChan
	log.Println("[SHUTDOWN] Terminating Go network proxy cleanly...")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
	log.Println("[SHUTDOWN] Completed.")
}
