package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
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
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

var (
	bufPool = sync.Pool{
		New: func() interface{} { return make([]byte, 32*1024) },
	}
	reHeadOpen   = regexp.MustCompile(`(?i)<head[^>]*>`)
	reHeadClose  = regexp.MustCompile(`(?i)</head>`)
	reHtmlOpen   = regexp.MustCompile(`(?i)<html[^>]*>`)
	reOsVer      = regexp.MustCompile(`Android\s+([0-9.]+)`)
	reBuildId    = regexp.MustCompile(`Build/([^\s;)]+)`)
	reLeakUA     = regexp.MustCompile(`(?i)x86|sdk|emulator|goldfish|ranchu|mozilla/`)
	reCacheAsset = regexp.MustCompile(`(?i)\.(?:png|jpe?g|webp|mp3|ogg|wasm|zip|pak|dat)$|/assets/`)

	tunnelEstablished = []byte("HTTP/1.1 200 Connection Established\r\n\r\n")

	stripReqHeaders = []string{
		"Proxy-Connection", "Proxy-Authenticate", "Proxy-Authorization",
		"Via", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
		"Forwarded", "X-Real-IP", "Client-IP", "X-Requested-With",
	}
	reWebViewToken = regexp.MustCompile(`(?i);\s*wv\b|Version/[0-9.]+\s*`)
	hopReqHeaders = map[string]struct{}{
		"connection": {}, "keep-alive": {}, "te": {}, "trailers": {}, "transfer-encoding": {}, "upgrade": {},
	}
	hopRespHeaders = map[string]struct{}{
		"connection": {}, "keep-alive": {}, "proxy-authenticate": {}, "proxy-authorization": {},
		"te": {}, "trailers": {}, "transfer-encoding": {}, "upgrade": {},
		"content-security-policy": {}, "content-security-policy-report-only": {},
	}
	extMimeMap = map[string]string{
		".wasm": "application/wasm",
		".json": "application/json; charset=utf-8",
		".png":  "image/png",
		".jpg":  "image/jpeg",
		".jpeg": "image/jpeg",
		".webp": "image/webp",
		".mp3":  "audio/mpeg",
		".ogg":  "audio/ogg",
		".js":   "application/javascript; charset=utf-8",
		".css":  "text/css; charset=utf-8",
		".html": "text/html; charset=utf-8",
		".htm":  "text/html; charset=utf-8",
	}
)

type closeWriter interface {
	CloseWrite() error
}

type bufferedConn struct {
	io.Reader
	net.Conn
}

func (b *bufferedConn) Read(p []byte) (int, error) { return b.Reader.Read(p) }

type DNSCacheEntry struct {
	IPs       []net.IP
	ExpiresAt time.Time
}

type FastDNSResolver struct {
	mu         sync.RWMutex
	cache      map[string]DNSCacheEntry
	ttl        time.Duration
	customDNS  string
	netResolve *net.Resolver
}

func NewFastDNSResolver(customDNS string, ttl time.Duration) *FastDNSResolver {
	r := &FastDNSResolver{
		cache:      make(map[string]DNSCacheEntry, 128),
		ttl:        ttl,
		customDNS:  customDNS,
		netResolve: net.DefaultResolver,
	}
	if customDNS != "" {
		if !strings.Contains(customDNS, ":") {
			customDNS += ":53"
		}
		d := net.Dialer{Timeout: 3 * time.Second}
		r.netResolve = &net.Resolver{
			PreferGo: true,
			Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return d.DialContext(ctx, "udp", customDNS)
			},
		}
	}
	return r
}

func (r *FastDNSResolver) Lookup(ctx context.Context, host string) ([]net.IP, error) {
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	if ip := net.ParseIP(host); ip != nil {
		return []net.IP{ip}, nil
	}

	now := time.Now()
	r.mu.RLock()
	entry, found := r.cache[host]
	r.mu.RUnlock()
	if found && now.Before(entry.ExpiresAt) {
		return entry.IPs, nil
	}

	ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
	if err != nil && r.netResolve != net.DefaultResolver {
		ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
	}
	if err != nil {
		return nil, err
	}

	r.mu.Lock()
	if len(r.cache) >= 1024 {
		for k, v := range r.cache {
			if now.After(v.ExpiresAt) {
				delete(r.cache, k)
			}
		}
		if len(r.cache) >= 1024 {
			r.cache = make(map[string]DNSCacheEntry, 128)
		}
	}
	r.cache[host] = DNSCacheEntry{IPs: ips, ExpiresAt: now.Add(r.ttl)}
	r.mu.Unlock()
	return ips, nil
}

func parseUpstreamProxy(raw string) *url.URL {
	if raw = strings.TrimSpace(raw); raw == "" {
		return nil
	}
	scheme, rest := "http", raw
	if idx := strings.Index(raw, "://"); idx != -1 {
		scheme, rest = strings.ToLower(raw[:idx]), raw[idx+3:]
	}
	if !strings.Contains(rest, "@") {
		if p := strings.Split(rest, ":"); len(p) == 4 {
			return &url.URL{Scheme: scheme, Host: p[0] + ":" + p[1], User: url.UserPassword(p[2], p[3])}
		}
	}
	u, err := url.Parse(scheme + "://" + rest)
	if err != nil {
		return nil
	}
	return u
}

func ensureHostPort(addr, defPort string) string {
	if !strings.Contains(addr, ":") {
		return addr + ":" + defPort
	}
	return addr
}

func dialUpstreamSocks5(proxyURL *url.URL, targetHostPort string, timeout time.Duration) (net.Conn, error) {
	proxyAddr := ensureHostPort(proxyURL.Host, "1080")
	targetHost, targetPortStr, err := net.SplitHostPort(targetHostPort)
	if err != nil {
		targetHost, targetPortStr = targetHostPort, "443"
	}
	targetPort, err := strconv.Atoi(targetPortStr)
	if err != nil || targetPort <= 0 || targetPort > 65535 {
		targetPort = 443
	}

	conn, err := net.DialTimeout("tcp", proxyAddr, timeout)
	if err != nil {
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(timeout))

	var user, pass string
	if proxyURL.User != nil {
		user = proxyURL.User.Username()
		pass, _ = proxyURL.User.Password()
	}

	greet := []byte{0x05, 0x01, 0x00}
	if user != "" {
		greet = []byte{0x05, 0x02, 0x00, 0x02}
	}
	if _, err = conn.Write(greet); err != nil {
		conn.Close()
		return nil, err
	}

	var buf [4]byte
	if _, err = io.ReadFull(conn, buf[:2]); err != nil || buf[0] != 0x05 {
		conn.Close()
		return nil, errors.New("invalid SOCKS5 greeting")
	}

	if buf[1] == 0x02 {
		if len(user) > 255 || len(pass) > 255 {
			conn.Close()
			return nil, errors.New("SOCKS5 credentials too long")
		}
		authReq := make([]byte, 0, 3+len(user)+len(pass))
		authReq = append(authReq, 0x01, byte(len(user)))
		authReq = append(authReq, user...)
		authReq = append(authReq, byte(len(pass)))
		authReq = append(authReq, pass...)
		if _, err = conn.Write(authReq); err != nil {
			conn.Close()
			return nil, err
		}
		if _, err = io.ReadFull(conn, buf[:2]); err != nil || buf[1] != 0x00 {
			conn.Close()
			return nil, errors.New("SOCKS5 authentication rejected")
		}
	} else if buf[1] != 0x00 {
		conn.Close()
		return nil, errors.New("SOCKS5 auth method unsupported")
	}

	req := make([]byte, 0, 7+len(targetHost))
	req = append(req, 0x05, 0x01, 0x00)
	if ip := net.ParseIP(targetHost); ip != nil && ip.To4() != nil {
		req = append(req, 0x01)
		req = append(req, ip.To4()...)
	} else {
		req = append(req, 0x03, byte(len(targetHost)))
		req = append(req, targetHost...)
	}
	req = append(req, byte(targetPort>>8), byte(targetPort&0xFF))

	if _, err = conn.Write(req); err != nil {
		conn.Close()
		return nil, err
	}
	if _, err = io.ReadFull(conn, buf[:4]); err != nil || buf[1] != 0x00 {
		conn.Close()
		return nil, fmt.Errorf("SOCKS5 connect failed (code %d)", buf[1])
	}

	var discardLen int
	switch buf[3] {
	case 0x01:
		discardLen = 6
	case 0x03:
		if _, err = io.ReadFull(conn, buf[:1]); err != nil {
			conn.Close()
			return nil, err
		}
		discardLen = int(buf[0]) + 2
	case 0x04:
		discardLen = 18
	}
	if discardLen > 0 {
		if _, err = io.CopyN(io.Discard, conn, int64(discardLen)); err != nil {
			conn.Close()
			return nil, err
		}
	}

	_ = conn.SetDeadline(time.Time{})
	return conn, nil
}

type ProxyTelemetry struct {
	mu          sync.RWMutex
	lastRTTMs   float64
	lastRTTTime time.Time
	rttHistory  []float64
	connsTotal  uint64
	connsActive int64
	connsFailed uint64
	bytesSent   uint64
	bytesRecv   uint64
}

func (t *ProxyTelemetry) recordDial(rttMs float64, err error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.connsTotal++
	if err != nil {
		t.connsFailed++
		return
	}
	t.connsActive++
	t.lastRTTMs = rttMs
	t.lastRTTTime = time.Now()
	t.rttHistory = append(t.rttHistory, rttMs)
	if len(t.rttHistory) > 32 {
		t.rttHistory = t.rttHistory[len(t.rttHistory)-32:]
	}
}

func (t *ProxyTelemetry) recordClose(sent, recv int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.connsActive > 0 {
		t.connsActive--
	}
	if sent > 0 {
		t.bytesSent += uint64(sent)
	}
	if recv > 0 {
		t.bytesRecv += uint64(recv)
	}
}

type TelemetrySnapshot struct {
	lastRTTMs   float64
	avgRTTMs    float64
	connsTotal  uint64
	connsActive int64
	connsFailed uint64
	bytesSent   uint64
	bytesRecv   uint64
}

func (t *ProxyTelemetry) snapshot() TelemetrySnapshot {
	t.mu.RLock()
	defer t.mu.RUnlock()
	avg := 0.0
	if len(t.rttHistory) > 0 {
		sum := 0.0
		for _, v := range t.rttHistory {
			sum += v
		}
		avg = sum / float64(len(t.rttHistory))
	}
	return TelemetrySnapshot{
		lastRTTMs:   t.lastRTTMs,
		avgRTTMs:    avg,
		connsTotal:  t.connsTotal,
		connsActive: t.connsActive,
		connsFailed: t.connsFailed,
		bytesSent:   t.bytesSent,
		bytesRecv:   t.bytesRecv,
	}
}

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
	dalvikUA       string
	osVer          string
	buildId        string
	glVendor       string
	glRenderer     string
	maxTextureSize string
	disguiseOnce   sync.Once
	cachedDisguise string
	disguiseBytes  []byte
	client         *http.Client
	telemetry      ProxyTelemetry
}

func NewProxyServer(resolver *FastDNSResolver, upstream, cacheDir string, quiet bool, port int, authToken, serial, uuid, model, manufacturer, ua, glVendor, glRenderer, maxTextureSize string) *ProxyServer {
	if model == "" {
		model = "SM-A515F"
	}
	if manufacturer == "" {
		manufacturer = "samsung"
	}
	if ua == "" {
		ua = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36"
	}
	osVer, buildId := "10", "QP1A.190711.020"
	if m := reOsVer.FindStringSubmatch(ua); len(m) > 1 {
		osVer = m[1]
	}
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
		dalvikUA:       fmt.Sprintf("Dalvik/2.1.0 (Linux; U; Android %s; %s Build/%s)", osVer, model, buildId),
		osVer:          osVer,
		buildId:        buildId,
		glVendor:       glVendor,
		glRenderer:     glRenderer,
		maxTextureSize: maxTextureSize,
		upstreamURL:    parseUpstreamProxy(upstream),
	}

	if cacheDir != "" {
		_ = os.MkdirAll(cacheDir, 0755)
	}

	isSocks := p.upstreamURL != nil && strings.HasPrefix(strings.ToLower(p.upstreamURL.Scheme), "socks5")
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}

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
				host, portStr = addr, "80"
			}
			if ips, err := resolver.Lookup(ctx, host); err == nil && len(ips) > 0 {
				addr = net.JoinHostPort(ips[0].String(), portStr)
			}
			return dialer.DialContext(ctx, network, addr)
		},
	}
	if p.upstreamURL != nil && !isSocks {
		transport.Proxy = http.ProxyURL(p.upstreamURL)
	}

	p.client = &http.Client{
		Transport: transport,
		Timeout:   60 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return p
}

func (p *ProxyServer) sanitizeRequestHeaders(h http.Header) {
	for _, k := range stripReqHeaders {
		h.Del(k)
	}
	h.Del("X-Requested-With")

	curUA := h.Get("User-Agent")
	if strings.HasPrefix(curUA, "Dalvik/") {
		h.Set("User-Agent", p.dalvikUA)
	} else if curUA == "" || reLeakUA.MatchString(curUA) {
		h.Set("User-Agent", p.ua)
	} else {
		// Strip embedded WebView flags (; wv and Version/X.X) that cause Google to block OAuth with 'disallowed_useragent'
		cleanUA := reWebViewToken.ReplaceAllString(curUA, "")
		cleanUA = strings.TrimSpace(cleanUA)
		h.Set("User-Agent", cleanUA)
	}

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
		h.Set("Sec-CH-UA-Model", `"`+p.model+`"`)
	}
	if h.Get("Accept-Language") == "" {
		h.Set("Accept-Language", "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")
	}
}

func (p *ProxyServer) dialUpstreamOrDirect(ctx context.Context, dest string) (net.Conn, error) {
	start := time.Now()
	conn, err := p.doDial(ctx, dest)
	rttMs := float64(time.Since(start).Microseconds()) / 1000.0
	p.telemetry.recordDial(rttMs, err)
	return conn, err
}

func (p *ProxyServer) doDial(ctx context.Context, dest string) (net.Conn, error) {
	if p.upstreamURL != nil {
		scheme := strings.ToLower(p.upstreamURL.Scheme)
		if strings.HasPrefix(scheme, "socks5") {
			return dialUpstreamSocks5(p.upstreamURL, dest, 6*time.Second)
		}
		defPort := "80"
		if scheme == "https" {
			defPort = "443"
		}
		destConn, err := net.DialTimeout("tcp", ensureHostPort(p.upstreamURL.Host, defPort), 5*time.Second)
		if err != nil {
			return nil, err
		}
		authHeader := ""
		if p.upstreamURL.User != nil {
			authHeader = "Proxy-Authorization: Basic " + base64.StdEncoding.EncodeToString([]byte(p.upstreamURL.User.String())) + "\r\n"
		}
		connectReq := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n%s\r\n", dest, dest, p.ua, authHeader)
		if _, err = io.WriteString(destConn, connectReq); err != nil {
			destConn.Close()
			return nil, err
		}
		br := bufio.NewReader(destConn)
		resp, err := http.ReadResponse(br, &http.Request{Method: http.MethodConnect})
		if err != nil {
			destConn.Close()
			return nil, err
		}
		if resp.StatusCode != http.StatusOK {
			destConn.Close()
			return nil, fmt.Errorf("upstream rejected CONNECT: %s", resp.Status)
		}
		if br.Buffered() > 0 {
			return &bufferedConn{Reader: io.MultiReader(br, destConn), Conn: destConn}, nil
		}
		return destConn, nil
	}

	host, portStr, err := net.SplitHostPort(dest)
	if err != nil {
		host, portStr = dest, "443"
	}
	target := dest
	if ips, err := p.resolver.Lookup(ctx, host); err == nil && len(ips) > 0 {
		target = net.JoinHostPort(ips[0].String(), portStr)
	}
	return (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(ctx, "tcp", target)
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
		if auth != p.authToken && auth != "Bearer "+p.authToken {
			http.Error(w, "Proxy Authentication Required", http.StatusProxyAuthRequired)
			return
		}
	}

	if r.Method == http.MethodGet && (r.URL.Path == "/__health" || r.URL.Path == "/__telemetry") && (r.Host == "" || strings.HasPrefix(r.Host, "127.0.0.1") || strings.HasPrefix(r.Host, "localhost")) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		snap := p.telemetry.snapshot()
		_, _ = fmt.Fprintf(w, `{"status":"ok","port":%d,"model":"%s","rtt_ms":%.2f,"rtt_avg_ms":%.2f,"sent_bytes":%d,"recv_bytes":%d,"conns_total":%d,"conns_failed":%d,"conns_active":%d,"timestamp":%d}`,
			p.port, p.model, snap.lastRTTMs, snap.avgRTTMs, snap.bytesSent, snap.bytesRecv, snap.connsTotal, snap.connsFailed, snap.connsActive, time.Now().Unix())
		return
	}

	if r.Method == http.MethodGet && r.URL.Path == "/__audit" && (r.Host == "" || strings.HasPrefix(r.Host, "127.0.0.1") || strings.HasPrefix(r.Host, "localhost")) {
		w.Header().Set("Content-Type", "application/json")
		auditData := p.auditOutboundIP()
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(auditData)
		return
	}

	// Intercept Ankama/Google OAuth callback on proxy if redirected here:
	if r.Method == http.MethodGet && (r.URL.Path == "/authorized" || r.URL.Path == "/login-authorized" || strings.HasPrefix(r.URL.Path, "/oauth/callback")) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		qs := r.URL.RawQuery
		if qs != "" {
			qs = "?" + qs
		}
		fmt.Fprintf(w, `<!DOCTYPE html><html><head><title>Authorization Success</title></head><body>
		<script>
			if (window.opener && window.opener.__onDeepLinkCallback) {
				window.opener.__onDeepLinkCallback(window.location.href);
			}
			window.location.href = "dofustouch://authorized%s";
		</script>
		<h2>Authorization Successful</h2>
		<p>Redirecting back to Dofus Touch...</p>
		</body></html>`, qs)
		return
	}

	switch {
	case r.Method == http.MethodConnect:
		p.handleConnect(w, r)
	case strings.EqualFold(r.Header.Get("Upgrade"), "websocket"):
		p.handleWebSocket(w, r)
	default:
		p.handleHTTP(w, r)
	}
}

func (p *ProxyServer) auditOutboundIP() []byte {
	tr := &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			return p.dialUpstreamOrDirect(ctx, addr)
		},
		ResponseHeaderTimeout: 5 * time.Second,
	}
	client := &http.Client{Transport: tr, Timeout: 6 * time.Second}
	resp, err := client.Get("http://ip-api.com/json/?fields=status,message,country,city,isp,org,as,query,mobile,proxy,hosting")
	if err != nil {
		res, _ := json.Marshal(map[string]interface{}{
			"status":     "error",
			"port":       p.port,
			"model":      p.model,
			"message":    err.Error(),
			"verdict":    "LOOKUP_OFFLINE",
			"risk_score": 50,
		})
		return res
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	var info struct {
		Status  string `json:"status"`
		Query   string `json:"query"`
		ISP     string `json:"isp"`
		Org     string `json:"org"`
		AS      string `json:"as"`
		Country string `json:"country"`
		City    string `json:"city"`
		Mobile  bool   `json:"mobile"`
		Proxy   bool   `json:"proxy"`
		Hosting bool   `json:"hosting"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &info); err != nil || info.Status != "success" {
		res, _ := json.Marshal(map[string]interface{}{
			"status":     "error",
			"port":       p.port,
			"model":      p.model,
			"message":    "upstream_ip_api_error",
			"verdict":    "UNKNOWN",
			"risk_score": 50,
		})
		return res
	}

	riskScore := 0
	verdict := "SAFE_RESIDENTIAL"
	if info.Hosting {
		riskScore = 85
		verdict = "DANGEROUS_DATACENTER"
	} else if info.Mobile {
		riskScore = 0
		verdict = "PRISTINE_CELLULAR_4G"
	} else if info.Proxy {
		riskScore = 45
		verdict = "SUSPICIOUS_PUBLIC_PROXY"
	}

	res, _ := json.Marshal(map[string]interface{}{
		"status":      "ok",
		"port":        p.port,
		"model":       p.model,
		"outbound_ip": info.Query,
		"isp":         info.ISP,
		"org":         info.Org,
		"asn":         info.AS,
		"country":     info.Country,
		"city":        info.City,
		"is_cellular": info.Mobile,
		"is_hosting":  info.Hosting,
		"is_proxy":    info.Proxy,
		"risk_score":  riskScore,
		"verdict":     verdict,
	})
	return res
}

func tuneTCPConn(c net.Conn) {
	_ = c.SetDeadline(time.Time{})
	if tc, ok := c.(*net.TCPConn); ok {
		_ = tc.SetKeepAlive(true)
		_ = tc.SetKeepAlivePeriod(30 * time.Second)
		_ = tc.SetNoDelay(true)
	}
}

func halfCloseOrClose(c net.Conn) {
	if cw, ok := c.(closeWriter); ok {
		_ = cw.CloseWrite()
	} else {
		_ = c.Close()
	}
}

func (p *ProxyServer) pipeTunnel(clientConn net.Conn, clientBuf *bufio.ReadWriter, destConn net.Conn) {
	tuneTCPConn(clientConn)
	tuneTCPConn(destConn)

	var sentBytes, recvBytes int64
	done := make(chan struct{}, 2)
	go func() {
		if clientBuf != nil && clientBuf.Reader.Buffered() > 0 {
			n, _ := io.CopyN(destConn, clientBuf, int64(clientBuf.Reader.Buffered()))
			atomic.AddInt64(&sentBytes, n)
		}
		buf := bufPool.Get().([]byte)
		n, _ := io.CopyBuffer(destConn, clientConn, buf)
		atomic.AddInt64(&sentBytes, n)
		bufPool.Put(buf)
		halfCloseOrClose(destConn)
		done <- struct{}{}
	}()

	go func() {
		buf := bufPool.Get().([]byte)
		n, _ := io.CopyBuffer(clientConn, destConn, buf)
		atomic.AddInt64(&recvBytes, n)
		bufPool.Put(buf)
		halfCloseOrClose(clientConn)
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
	p.telemetry.recordClose(atomic.LoadInt64(&sentBytes), atomic.LoadInt64(&recvBytes))
}

func (p *ProxyServer) handleConnect(w http.ResponseWriter, r *http.Request) {
	dest := ensureHostPort(r.Host, "443")
	dialCtx, dialCancel := context.WithTimeout(context.Background(), 7*time.Second)
	destConn, err := p.dialUpstreamOrDirect(dialCtx, dest)
	dialCancel()
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

	if _, err = clientConn.Write(tunnelEstablished); err != nil {
		destConn.Close()
		clientConn.Close()
		return
	}
	if !p.quiet && strings.Contains(dest, "ankama") {
		log.Printf("[TUNNEL] CONNECT -> %s (%s)", dest, p.model)
	}
	p.pipeTunnel(clientConn, clientBuf, destConn)
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

	dest := ensureHostPort(r.Host, "80")
	dialCtx, dialCancel := context.WithTimeout(context.Background(), 7*time.Second)
	destConn, err := p.dialUpstreamOrDirect(dialCtx, dest)
	dialCancel()
	if err != nil {
		clientConn.Close()
		return
	}

	p.sanitizeRequestHeaders(r.Header)
	r.RequestURI = ""
	if r.URL != nil {
		r.URL.Scheme, r.URL.Host = "", ""
	}
	if err := r.Write(destConn); err != nil {
		destConn.Close()
		clientConn.Close()
		return
	}
	p.pipeTunnel(clientConn, clientBuf, destConn)
}

func atomicReplaceFile(src, dst string) error {
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	var err error
	for attempt := 0; attempt < 3; attempt++ {
		time.Sleep(20 * time.Millisecond)
		_ = os.Remove(dst)
		if err = os.Rename(src, dst); err == nil {
			return nil
		}
	}
	oldPath := dst + ".old." + strconv.FormatInt(time.Now().UnixNano(), 10)
	if os.Rename(dst, oldPath) == nil {
		_ = os.Remove(oldPath)
		if os.Rename(src, dst) == nil {
			return nil
		}
	}
	return err
}

func detectMimeType(path string) string {
	if m, ok := extMimeMap[strings.ToLower(filepath.Ext(path))]; ok {
		return m
	}
	return "application/octet-stream"
}

func (p *ProxyServer) getDisguiseContent() string {
	p.disguiseOnce.Do(func() {
		p.cachedDisguise = p.loadDisguiseFromDisk()
		p.disguiseBytes = []byte(p.cachedDisguise)
	})
	return p.cachedDisguise
}

func (p *ProxyServer) loadDisguiseFromDisk() string {
	exeDir := filepath.Dir(os.Args[0])
	candidates := []string{
		filepath.Join("scripts", "mobile-disguise.js"),
		"mobile-disguise.js",
		filepath.Join(os.Getenv("DOFUS_FARM_HOME"), "scripts", "mobile-disguise.js"),
		filepath.Join(exeDir, "scripts", "mobile-disguise.js"),
		filepath.Join(exeDir, "mobile-disguise.js"),
	}
	for _, c := range candidates {
		if b, err := os.ReadFile(c); err == nil && len(b) > 0 {
			var pairs []string
			for _, kv := range [][2]string{
				{"__DEVICE_SERIAL__", p.serial},
				{"__DEVICE_UUID__", p.uuid},
				{"__DEVICE_MODEL__", p.model},
				{"__DEVICE_MANUFACTURER__", p.manufacturer},
				{"__DEVICE_UA__", p.ua},
				{"__DEVICE_GL_VENDOR__", p.glVendor},
				{"__DEVICE_GL_RENDERER__", p.glRenderer},
				{"__DEVICE_MAX_TEXTURE_SIZE__", p.maxTextureSize},
			} {
				if kv[1] != "" {
					pairs = append(pairs, kv[0], kv[1])
				}
			}
			if len(pairs) > 0 {
				return strings.NewReplacer(pairs...).Replace(string(b))
			}
			return string(b)
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

	if strings.HasSuffix(targetURL.Path, "/__disguise.js") || strings.HasSuffix(targetURL.Path, "/mobile-disguise.js") {
		if p.getDisguiseContent() != "" {
			h := w.Header()
			h.Set("Content-Type", "application/javascript; charset=utf-8")
			h.Set("Access-Control-Allow-Origin", "*")
			h.Set("Cache-Control", "no-cache")
			h.Set("Content-Length", strconv.Itoa(len(p.disguiseBytes)))
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(p.disguiseBytes)
			return
		}
	}

	targetStr := targetURL.String()
	var cacheFilePath string
	if p.cacheDir != "" && r.Method == http.MethodGet && reCacheAsset.MatchString(targetStr) {
		sum := sha256.Sum256([]byte(targetStr))
		cacheFilePath = filepath.Join(p.cacheDir, hex.EncodeToString(sum[:16]))
		if fi, err := os.Stat(cacheFilePath); err == nil {
			if time.Since(fi.ModTime()) > 7*24*time.Hour {
				_ = os.Remove(cacheFilePath)
				_ = os.Remove(cacheFilePath + ".meta")
			} else if f, err := os.Open(cacheFilePath); err == nil {
				defer f.Close()
				h := w.Header()
				h.Set("Access-Control-Allow-Origin", "*")
				h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
				h.Set("Access-Control-Allow-Headers", "*")
				mimeType := ""
				if metaBytes, metaErr := os.ReadFile(cacheFilePath + ".meta"); metaErr == nil {
					mimeType = strings.TrimSpace(string(metaBytes))
				}
				if mimeType == "" {
					mimeType = detectMimeType(targetURL.Path)
				}
				if mimeType != "" {
					h.Set("Content-Type", mimeType)
				}
				h.Set("Content-Length", strconv.FormatInt(fi.Size(), 10))
				w.WriteHeader(http.StatusOK)
				buf := bufPool.Get().([]byte)
				_, _ = io.CopyBuffer(w, f, buf)
				bufPool.Put(buf)
				return
			}
		}
	}

	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetStr, r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	for k, vv := range r.Header {
		if _, skip := hopReqHeaders[strings.ToLower(k)]; !skip {
			outReq.Header[k] = append([]string(nil), vv...)
		}
	}
	p.sanitizeRequestHeaders(outReq.Header)

	if ae := outReq.Header.Get("Accept-Encoding"); ae != "" {
		var kept []string
		for _, part := range strings.Split(ae, ",") {
			t := strings.TrimSpace(part)
			l := strings.ToLower(t)
			if t != "" && l != "br" && !strings.HasPrefix(l, "br;") && !strings.HasPrefix(l, "br ") {
				kept = append(kept, t)
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

	wh := w.Header()
	for k, vv := range resp.Header {
		if _, skip := hopRespHeaders[strings.ToLower(k)]; !skip {
			wh[k] = append([]string(nil), vv...)
		}
	}
	wh.Set("Access-Control-Allow-Origin", "*")

	ct := resp.Header.Get("Content-Type")
	isHTML := (strings.Contains(ct, "text/html") || strings.HasSuffix(strings.ToLower(targetURL.Path), ".html")) && resp.StatusCode == http.StatusOK

	var bodyReader io.Reader = resp.Body
	isGzip := strings.EqualFold(resp.Header.Get("Content-Encoding"), "gzip")
	if isGzip && (isHTML || cacheFilePath != "") {
		if gzReader, gzErr := gzip.NewReader(resp.Body); gzErr == nil {
			defer gzReader.Close()
			bodyReader = gzReader
			wh.Del("Content-Encoding")
			wh.Del("Content-Length")
			isGzip = false
		}
	}

	if isHTML {
		wh.Del("Content-Security-Policy")
		wh.Del("Content-Security-Policy-Report-Only")

		if bodyBytes, readErr := io.ReadAll(bodyReader); readErr == nil {
			if !bytes.Contains(bodyBytes, []byte("mobile-disguise.js")) && !bytes.Contains(bodyBytes, []byte("__disguise.js")) {
				bodyStr := string(bodyBytes)
				disguiseCode := p.getDisguiseContent()
				var injection string
				if disguiseCode != "" {
					injection = "\n\t\t<script type=\"text/javascript\">\n" + disguiseCode + "\n\t\t</script>\n"
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
			wh.Set("Content-Length", strconv.Itoa(len(bodyBytes)))
			w.WriteHeader(resp.StatusCode)
			_, _ = w.Write(bodyBytes)
			return
		}
	}

	w.WriteHeader(resp.StatusCode)

	buf := bufPool.Get().([]byte)
	defer bufPool.Put(buf)

	if cacheFilePath != "" && resp.StatusCode == http.StatusOK && !isGzip {
		tmpPath := fmt.Sprintf("%s.%d.%d.tmp", cacheFilePath, os.Getpid(), time.Now().UnixNano())
		if tf, err := os.Create(tmpPath); err == nil {
			written, copyErr := io.CopyBuffer(io.MultiWriter(w, tf), bodyReader, buf)
			_ = tf.Close()

			if copyErr != nil || (resp.ContentLength > 0 && resp.Header.Get("Content-Encoding") == "" && written < resp.ContentLength) {
				_ = os.Remove(tmpPath)
				return
			}
			if atomicReplaceFile(tmpPath, cacheFilePath) == nil {
				if ct != "" {
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

	server := &http.Server{
		Addr:              net.JoinHostPort(*host, strconv.Itoa(*port)),
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
