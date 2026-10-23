package main

import (
	"bufio"
	"bytes"
	"cmp"
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
	"maps"
	"mime"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	xproxy "golang.org/x/net/proxy"
	"golang.org/x/sync/errgroup"
	"golang.org/x/sync/singleflight"
	"golang.org/x/time/rate"
)

var (
	bufPool                = sync.Pool{New: func() any { return make([]byte, 32*1024) }}
	upstreamFallbackOnce   sync.Once
	virtualHostRewriteOnce sync.Once
	loginHost              = "dt-proxy-production-login.ankama-games.com"
	reHeadOpen             = regexp.MustCompile(`(?i)<head[^>]*>`)
	reHeadClose            = regexp.MustCompile(`(?i)</head>`)
	reHtmlOpen             = regexp.MustCompile(`(?i)<html[^>]*>`)
	reOsVer                = regexp.MustCompile(`Android\s+([0-9.]+)`)
	reBuildId              = regexp.MustCompile(`Build/([^\s;)]+)`)
	tunnelEstablished      = []byte("HTTP/1.1 200 Connection Established\r\n\r\n")
	upstreamHealthy        atomic.Bool
	assetProxyCursor       uint32

	stripReqHeaders = []string{
		"Proxy-Connection", "Proxy-Authenticate", "Proxy-Authorization",
		"Via", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
		"Forwarded", "X-Real-IP", "Client-IP", "X-Requested-With",
	}
	hopReqHeaders = headerSet(append(slices.Clone(stripReqHeaders),
		"Connection", "Keep-Alive", "Te", "Trailers", "Transfer-Encoding", "Upgrade")...)
	hopRespHeaders = headerSet(
		"Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
		"Te", "Trailers", "Transfer-Encoding", "Upgrade",
		"Content-Security-Policy", "Content-Security-Policy-Report-Only",
	)
	extMimeMap = map[string]string{
		".wasm": "application/wasm", ".json": "application/json; charset=utf-8",
		".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
		".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".js": "application/javascript; charset=utf-8",
		".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
	}
	reMapJSONPath = regexp.MustCompile(`/maps/(\d+)\.json$`)
	reMapHeader   = regexp.MustCompile(`^\{"id":(\d+),"topNeighbourId":(-?\d+),"bottomNeighbourId":(-?\d+),"leftNeighbourId":(-?\d+),"rightNeighbourId":(-?\d+)`)
	reAtlasEntry  = regexp.MustCompile(`"(\d+)":\{([^{}]*)\}`)
)

const (
	hopIndexEvery          = 10 * time.Second
	exitCooldown           = 90 * time.Second
	warmTTL                = 15 * time.Second
	warmPerDest            = 2
	warmMaxDests           = 6
	assetCacheTTL          = 7 * 24 * time.Hour
	prefetchCooldown       = 20 * time.Second
	prefetchQueueSize      = 1024
	prefetchMaxTilesPerMap = 600
	prefetchMapJSONReadMax = 4 << 20
	prefetchNegativeTTL    = 10 * time.Minute
)

func headerSet(keys ...string) map[string]struct{} {
	m := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		m[http.CanonicalHeaderKey(k)] = struct{}{}
	}
	return m
}

type closeWriter interface{ CloseWrite() error }

type bufferedConn struct {
	io.Reader
	net.Conn
}

func (b *bufferedConn) Read(p []byte) (int, error) { return b.Reader.Read(p) }

type connDialer struct{ net.Conn }

func (d connDialer) Dial(_, _ string) (net.Conn, error) { return d.Conn, nil }

type DNSCacheEntry struct {
	IPs       []net.IP
	ExpiresAt time.Time
}

type dnsLookupCall struct {
	wg  sync.WaitGroup
	ips []net.IP
	err error
}

type FastDNSResolver struct {
	mu         sync.RWMutex
	cache      map[string]DNSCacheEntry
	inflight   map[string]*dnsLookupCall
	sf         singleflight.Group
	rr         uint64
	ttl        time.Duration
	customDNS  string
	netResolve *net.Resolver
}

func NewFastDNSResolver(customDNS string, ttl time.Duration) *FastDNSResolver {
	r := &FastDNSResolver{
		cache:      make(map[string]DNSCacheEntry, 128),
		inflight:   make(map[string]*dnsLookupCall),
		ttl:        ttl,
		customDNS:  customDNS,
		netResolve: net.DefaultResolver,
	}
	if customDNS != "" {
		customDNS = ensureHostPort(customDNS, "53")
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

func (r *FastDNSResolver) pickIP(ips []net.IP) []net.IP {
	if len(ips) <= 1 { return slices.Clone(ips) }
	r.mu.Lock()
	idx := r.rr % uint64(len(ips))
	r.rr++
	r.mu.Unlock()
	return []net.IP{ips[idx]}
}

func (r *FastDNSResolver) Lookup(ctx context.Context, host string) ([]net.IP, error) {
	if h, _, err := net.SplitHostPort(host); err == nil { host = h }
	if ip := net.ParseIP(host); ip != nil { return []net.IP{ip}, nil }
	now := time.Now()
	r.mu.RLock()
	entry, found := r.cache[host]
	r.mu.RUnlock()
	if found && now.Before(entry.ExpiresAt) { return r.pickIP(entry.IPs), nil }
	v, err, _ := r.sf.Do(host, func() (any, error) {
		ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
		if err != nil && r.netResolve != net.DefaultResolver {
			ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
		}
		r.mu.Lock()
		defer r.mu.Unlock()
		if len(r.cache) >= 1024 {
			maps.DeleteFunc(r.cache, func(_ string, e DNSCacheEntry) bool { return now.After(e.ExpiresAt) })
			if len(r.cache) >= 1024 { clear(r.cache) }
		}
		if err == nil { r.cache[host] = DNSCacheEntry{IPs: ips, ExpiresAt: now.Add(r.ttl)} }
		return ips, err
	})
	if err != nil { return nil, err }
	return r.pickIP(v.([]net.IP)), nil
}

func (r *FastDNSResolver) resolveAddr(ctx context.Context, addr, defPort string) string {
	host, portStr, err := net.SplitHostPort(addr)
	if err != nil {
		host, portStr = addr, defPort
	}
	if r != nil {
		if ips, err := r.Lookup(ctx, host); err == nil && len(ips) > 0 {
			return net.JoinHostPort(ips[0].String(), portStr)
		}
	}
	return addr
}

func stripWebViewTokens(ua string) string {
	var b strings.Builder
	b.Grow(len(ua))
	for i := 0; i < len(ua); {
		if ua[i] == ';' {
			j := i + 1
			for j < len(ua) && (ua[j] == ' ' || ua[j] == '\t') {
				j++
			}
			if j+1 < len(ua) && (ua[j]|0x20) == 'w' && (ua[j+1]|0x20) == 'v' && !isTokenCharUA(ua, j+2) {
				i = j + 2
				continue
			}
		}
		if (ua[i]|0x20) == 'v' && len(ua)-i >= 8 && strings.EqualFold(ua[i:i+8], "Version/") {
			j := i + 8
			for j < len(ua) && (ua[j] == '.' || (ua[j] >= '0' && ua[j] <= '9')) {
				j++
			}
			if j > i+8 {
				for j < len(ua) && strings.IndexByte(" \t\n\r\f\v", ua[j]) >= 0 {
					j++
				}
				i = j
				continue
			}
		}
		b.WriteByte(ua[i])
		i++
	}
	return b.String()
}

func isTokenCharUA(s string, i int) bool {
	if i >= len(s) { return false }
	c := s[i]
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-'
}

func isCacheAssetURL(u string) bool {
	if strings.Contains(u, "/assets/") { return true }
	dot := strings.LastIndexByte(u, '.')
	if dot < 0 { return false }
	switch asciiLower(u[dot+1:]) {
	case "png", "jpg", "mp3", "ogg", "zip", "pak", "dat", "jpeg", "webp", "wasm", "json":
		return true
	}
	return false
}

func asciiEqualFold(s, t string) bool { return len(s) == len(t) && strings.EqualFold(s, t) }

func parseUpstreamProxy(raw string) *url.URL {
	if raw = strings.TrimSpace(raw); raw == "" { return nil }
	scheme, rest := "http", raw
	if s, r, ok := strings.Cut(raw, "://"); ok {
		scheme, rest = strings.ToLower(s), r
	}
	if !strings.Contains(rest, "@") {
		if p := strings.Split(rest, ":"); len(p) == 4 {
			return &url.URL{Scheme: scheme, Host: p[0] + ":" + p[1], User: url.UserPassword(p[2], p[3])}
		}
	}
	u, _ := url.Parse(scheme + "://" + rest)
	return u
}

func ensureHostPort(addr, defPort string) string {
	if !strings.Contains(addr, ":") { return addr + ":" + defPort }
	return addr
}

func upstreamReachable(u *url.URL, timeout time.Duration) (bool, error) {
	conn, err := net.DialTimeout("tcp", ensureHostPort(u.Host, "1080"), timeout)
	if err != nil { return false, err }
	_ = conn.Close()
	return true, nil
}

func watchUpstream(u *url.URL, raw string) {
	for {
		time.Sleep(5 * time.Second)
		if ok, err := upstreamReachable(u, 1500*time.Millisecond); ok && !upstreamHealthy.Swap(true) {
			log.Printf("[UPSTREAM] %s is reachable again; dials resume", raw)
		} else if !ok && upstreamHealthy.Swap(false) {
			log.Printf("[UPSTREAM] %s went away (%v); failing closed until it returns", raw, err)
		}
	}
}

func socksAuth(u *url.URL) *xproxy.Auth {
	if u == nil || u.User == nil { return nil }
	pass, _ := u.User.Password()
	return &xproxy.Auth{User: u.User.Username(), Password: pass}
}

func dialUpstreamSocks5(proxyURL *url.URL, targetHostPort string, timeout time.Duration) (net.Conn, error) {
	base := &net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}
	dialer, err := xproxy.SOCKS5("tcp", ensureHostPort(proxyURL.Host, "1080"), socksAuth(proxyURL), base)
	if err != nil { return nil, err }
	if cd, ok := dialer.(xproxy.ContextDialer); ok {
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		return cd.DialContext(ctx, "tcp", targetHostPort)
	}
	return dialer.Dial("tcp", targetHostPort)
}

type ProxyTelemetry struct {
	mu                                            sync.RWMutex
	lastRTTMs                                     float64
	lastRTTTime                                   time.Time
	rttHistory                                    [32]float64
	rttCount, rttPos                              int
	connsTotal, connsFailed, bytesSent, bytesRecv uint64
	connsActive                                   int64
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
	t.lastRTTMs, t.lastRTTTime = rttMs, time.Now()
	t.rttHistory[t.rttPos] = rttMs
	t.rttPos = (t.rttPos + 1) % len(t.rttHistory)
	if t.rttCount < len(t.rttHistory) {
		t.rttCount++
	}
}

func (t *ProxyTelemetry) recordClose(sent, recv int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.connsActive > 0 {
		t.connsActive--
	}
	if sent > 0 { t.bytesSent += uint64(sent) }
	if recv > 0 { t.bytesRecv += uint64(recv) }
}

type TelemetrySnapshot struct {
	lastRTTMs, avgRTTMs                           float64
	connsTotal, connsFailed, bytesSent, bytesRecv uint64
	connsActive                                   int64
}

func (t *ProxyTelemetry) snapshot() TelemetrySnapshot {
	t.mu.RLock()
	defer t.mu.RUnlock()
	avg := 0.0
	if t.rttCount > 0 {
		sum := 0.0
		for i := 1; i <= t.rttCount; i++ {
			sum += t.rttHistory[(t.rttPos-i+len(t.rttHistory))%len(t.rttHistory)]
		}
		avg = sum / float64(t.rttCount)
	}
	return TelemetrySnapshot{
		lastRTTMs: t.lastRTTMs, avgRTTMs: avg,
		connsTotal: t.connsTotal, connsActive: t.connsActive, connsFailed: t.connsFailed,
		bytesSent: t.bytesSent, bytesRecv: t.bytesRecv,
	}
}

type hopProbe struct {
	ok      bool
	latency time.Duration
	checked time.Time
}

type prefetchJob struct {
	target string
	id     int64
	depth  int
}

type negEntry struct {
	status int
	at     time.Time
}

type modPatchOp struct {
	Op    string `json:"op"`
	Path  string `json:"path"`
	Value any    `json:"value"`
}

type modRule struct {
	Match       string       `json:"match"`
	Action      string       `json:"action"`
	Status      int          `json:"status"`
	File        string       `json:"file"`
	ContentType string       `json:"contentType"`
	Patch       []modPatchOp `json:"patch"`
}

type ProxyServer struct {
	resolver                                                   *FastDNSResolver
	upstreamURL, lastGood, exitURL, cdnUpstream                *url.URL
	upstreams                                                  []*url.URL
	upstreamMu, exitMu, hopMu, destMu                          sync.Mutex
	prefetchMu, prefetchLogMu, mirrorMu, warmMu, negMu, modsMu sync.Mutex
	exitBlockedUntil, modsChecked, modsRulesMod                time.Time
	cacheHits, cacheMisses                                     uint64
	ticks, port                                                int
	prefetchRadius, prefetchTilesDepth, prefetchWorkers        int
	hopIdx                                                     map[string]hopProbe
	hopIdxPath, cacheDir, authToken, modsDir                   string
	serial, uuid, model, manufacturer, ua, dalvikUA            string
	osVer, buildId, glVendor, glRenderer                       string
	maxTextureSize, referer, refererRoot                       string
	hopDirty, quiet, allowDirect, prefetch, mirrorBuilt        bool
	dests                                                      []string
	disguiseOnce, prefetchOnce                                 sync.Once
	cachedDisguise                                             string
	disguiseBytes                                              []byte
	client                                                     *http.Client
	telemetry                                                  ProxyTelemetry
	prefetchQ                                                  chan prefetchJob
	prefetchSeen                                               map[int64]time.Time
	reqLogFile, prefetchLogFile                                *os.File
	reqLogSize, reqLogRotateAt, clientInflight                 int64
	mirrorIndex                                                map[string]struct{}
	warmInflight                                               map[string]chan struct{}
	warmSF                                                     singleflight.Group
	negCache                                                   map[string]negEntry
	modsRules                                                  []modRule
	modsRes                                                    []*regexp.Regexp
}

func NewProxyServer(resolver *FastDNSResolver, upstream, cacheDir string, quiet bool, port int, authToken, serial, uuid, model, manufacturer, ua, glVendor, glRenderer, maxTextureSize, referer string) *ProxyServer {
	model = cmp.Or(model, "SM-A515F")
	manufacturer = cmp.Or(manufacturer, "samsung")
	ua = cmp.Or(ua, "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36")
	referer = cmp.Or(referer, "https://www.dofus-touch.com/")
	refererRoot := referer
	if u, err := url.Parse(referer); err == nil && u.Scheme != "" && u.Host != "" {
		refererRoot = u.Scheme + "://" + u.Host
	}
	osVer, buildId := "10", "QP1A.190711.020"
	if m := reOsVer.FindStringSubmatch(ua); len(m) > 1 { osVer = m[1] }
	if m := reBuildId.FindStringSubmatch(ua); len(m) > 1 { buildId = m[1] }
	upstreamChain := parseUpstreamList(upstream)
	var upstreamPrimary *url.URL
	if len(upstreamChain) > 0 { upstreamPrimary = upstreamChain[0] }
	if cacheDir != "" { _ = os.MkdirAll(cacheDir, 0755) }
	isSocks := upstreamPrimary != nil && strings.HasPrefix(strings.ToLower(upstreamPrimary.Scheme), "socks5")
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	transport := &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 50,
		IdleConnTimeout:     90 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			if isSocks { return dialUpstreamSocks5(upstreamPrimary, addr, 6*time.Second) }
			return dialer.DialContext(ctx, network, resolver.resolveAddr(ctx, addr, "80"))
		},
	}
	if upstreamPrimary != nil && !isSocks { transport.Proxy = http.ProxyURL(upstreamPrimary) }
	return &ProxyServer{
		hopIdx:         make(map[string]hopProbe),
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
		referer:        referer,
		refererRoot:    refererRoot,
		upstreamURL:    upstreamPrimary,
		upstreams:      upstreamChain,
		client: &http.Client{
			Transport:     transport,
			Timeout:       60 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}
}

func (p *ProxyServer) sanitizeRequestHeaders(h http.Header) {
	for k := range hopReqHeaders {
		h.Del(k)
	}
	switch curUA := h.Get("User-Agent"); {
	case curUA == "" || strings.HasPrefix(curUA, "Dalvik/") || uaLeaksDesktop(curUA):
		h.Set("User-Agent", p.ua)
	default:
		h.Set("User-Agent", strings.TrimSpace(stripWebViewTokens(curUA)))
	}
	for k, v := range map[string]string{
		"Sec-CH-UA-Arch":     `"arm"`,
		"Sec-CH-UA-Platform": `"Android"`,
		"Sec-CH-UA-Mobile":   "?1",
		"Sec-CH-UA-Model":    `"` + p.model + `"`,
	} {
		if h.Get(k) != "" { h.Set(k, v) }
	}
	if h.Get("Accept-Language") == "" {
		h.Set("Accept-Language", "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")
	}
}

func uaLeaksDesktop(ua string) bool {
	lower := asciiLower(ua)
	for _, kw := range []string{"mozilla/", "x86", "sdk", "emulator", "goldfish", "ranchu"} {
		if strings.Contains(lower, kw) { return true }
	}
	return false
}

func copyHeaderFiltered(dst, src http.Header, skip map[string]struct{}) {
	for k, vv := range src {
		if _, drop := skip[k]; !drop { dst[k] = slices.Clone(vv) }
	}
}

func isBrCoding(tok string) bool {
	l := asciiLower(tok)
	return l == "br" || strings.HasPrefix(l, "br;") || strings.HasPrefix(l, "br ")
}

func buildAcceptEncoding(ae string, dropBr bool) string {
	var b strings.Builder
	b.Grow(len(ae))
	for _, raw := range strings.Split(ae, ",") {
		if tok := strings.TrimSpace(raw); tok != "" && (!dropBr || !isBrCoding(tok)) {
			if b.Len() > 0 { b.WriteString(", ") }
			b.WriteString(tok)
		}
	}
	return cmp.Or(b.String(), "gzip, deflate")
}

func filterAcceptEncoding(ae string) string {
	if !hasBrToken(ae) && ae == joinAcceptEncodingTokens(ae) { return ae }
	return buildAcceptEncoding(ae, true)
}

func joinAcceptEncodingTokens(ae string) string { return buildAcceptEncoding(ae, false) }

func hasBrToken(ae string) bool {
	for _, raw := range strings.Split(ae, ",") {
		if tok := strings.TrimSpace(raw); tok != "" && isBrCoding(tok) { return true }
	}
	return false
}

func asciiLower(s string) string {
	for i := 0; i < len(s); i++ {
		if s[i] >= 'A' && s[i] <= 'Z' {
			b := []byte(s)
			for ; i < len(b); i++ {
				if b[i] >= 'A' && b[i] <= 'Z' { b[i] += 'a' - 'A' }
			}
			return string(b)
		}
	}
	return s
}

func newAssetClient(ups []*url.URL, resolver *FastDNSResolver) *http.Client {
	dialer := &net.Dialer{Timeout: 8 * time.Second, KeepAlive: 30 * time.Second}
	transport := &http.Transport{
		MaxIdleConns:        128,
		MaxIdleConnsPerHost: 32,
		IdleConnTimeout:     60 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			var u *url.URL
			if len(ups) == 1 {
				u = ups[0]
			} else if len(ups) > 1 {
				u = ups[int(atomic.AddUint32(&assetProxyCursor, 1))%len(ups)]
			}
			if u == nil { return dialer.DialContext(ctx, network, addr) }
			if strings.HasPrefix(strings.ToLower(u.Scheme), "socks5") {
				return dialUpstreamSocks5(u, addr, 8*time.Second)
			}
			host, portStr, err := net.SplitHostPort(addr)
			if err != nil {
				host, portStr = addr, "80"
			}
			conn, err := dialer.DialContext(ctx, network, ensureHostPort(u.Host, "80"))
			if err != nil { return nil, err }
			_ = conn.SetDeadline(time.Now().Add(8 * time.Second))
			if err := connectThrough(conn, u.Scheme, host, portStrInt(portStr), userOf(u), ""); err != nil {
				_ = conn.Close()
				return nil, err
			}
			_ = conn.SetDeadline(time.Time{})
			return conn, nil
		},
	}
	return &http.Client{Transport: transport, Timeout: 60 * time.Second}
}

func portStrInt(s string) int {
	if n, err := strconv.Atoi(s); err == nil && n > 0 && n <= 65535 { return n }
	return 80
}

func userOf(u *url.URL) string {
	if u != nil && u.User != nil { return u.User.String() }
	return ""
}

func parseUpstreamList(raw string) []*url.URL {
	var out []*url.URL
	for _, part := range strings.Split(raw, ",") {
		if u := parseUpstreamProxy(part); u != nil { out = append(out, u) }
	}
	return out
}

func readConnectResponse(conn net.Conn) error {
	var buf [1]byte
	var cur []byte
	for total := 0; total <= 8192; {
		if _, err := conn.Read(buf[:]); err != nil { return err }
		total++
		switch buf[0] {
		case '\r':
		case '\n':
			if len(cur) == 0 { return nil }
			if s := string(cur); strings.HasPrefix(s, "HTTP/") {
				if f := strings.Fields(s); len(f) >= 2 && f[1] != "200" {
					return fmt.Errorf("proxy answered %s", s)
				}
			}
			cur = cur[:0]
		default:
			cur = append(cur, buf[0])
		}
	}
	return errors.New("CONNECT reply too long")
}

func httpConnect(conn net.Conn, dest, userinfo, ua string, timeout time.Duration) error {
	if err := conn.SetDeadline(time.Now().Add(timeout)); err != nil { return err }
	defer func() { _ = conn.SetDeadline(time.Time{}) }()
	auth := ""
	if userinfo != "" {
		auth = "Proxy-Authorization: Basic " + base64.StdEncoding.EncodeToString([]byte(userinfo)) + "\r\n"
	}
	req := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n%s\r\n", dest, dest, ua, auth)
	if _, err := io.WriteString(conn, req); err != nil { return err }
	return readConnectResponse(conn)
}

func connectThrough(conn net.Conn, scheme, host string, port int, userinfo, ua string) error {
	dest := net.JoinHostPort(host, strconv.Itoa(port))
	if strings.HasPrefix(strings.ToLower(scheme), "socks5") {
		if err := conn.SetDeadline(time.Now().Add(6 * time.Second)); err != nil { return err }
		defer func() { _ = conn.SetDeadline(time.Time{}) }()
		var auth *xproxy.Auth
		if userinfo != "" {
			user, pass, _ := strings.Cut(userinfo, ":")
			auth = &xproxy.Auth{User: user, Password: pass}
		}
		d, err := xproxy.SOCKS5("tcp", "0.0.0.0:0", auth, connDialer{conn})
		if err != nil { return err }
		_, err = d.Dial("tcp", dest)
		return err
	}
	return httpConnect(conn, dest, userinfo, ua, 6*time.Second)
}

func (p *ProxyServer) rememberDest(dest string) {
	p.destMu.Lock()
	defer p.destMu.Unlock()
	if slices.Contains(p.dests, dest) { return }
	if len(p.dests) >= warmMaxDests { p.dests = p.dests[1:] }
	p.dests = append(p.dests, dest)
}

func (p *ProxyServer) hopOrder() []*url.URL {
	exit := p.exitURL
	p.hopMu.Lock()
	defer p.hopMu.Unlock()
	var good, unknown, bad []*url.URL
	for _, u := range p.upstreams {
		if u == nil || (exit != nil && u.String() == exit.String()) { continue }
		switch pr, seen := p.hopIdx[u.String()]; {
		case !seen:
			unknown = append(unknown, u)
		case pr.ok:
			good = append(good, u)
		default:
			bad = append(bad, u)
		}
	}
	slices.SortStableFunc(good, func(a, b *url.URL) int {
		return cmp.Compare(p.hopIdx[a.String()].latency, p.hopIdx[b.String()].latency)
	})
	return slices.Concat(good, unknown, bad)
}

func (p *ProxyServer) openViaHop(hop, exit *url.URL, dest string, timeout time.Duration) (net.Conn, error) {
	exitPort := 1080
	if n, err := strconv.Atoi(exit.Port()); err == nil && n > 0 { exitPort = n }
	targetHost, targetPortStr, err := net.SplitHostPort(dest)
	if err != nil {
		targetHost, targetPortStr = dest, "443"
	}
	targetPort := 443
	if n, err := strconv.Atoi(targetPortStr); err == nil && n > 0 && n <= 65535 {
		targetPort = n
	}
	exitAddr := net.JoinHostPort(exit.Hostname(), strconv.Itoa(exitPort))
	started := time.Now()
	conn, err := p.dialOne(hop, exitAddr, exitAddr, timeout)
	if err != nil {
		p.recordHop(hop, false, time.Since(started))
		return nil, err
	}
	if err = connectThrough(conn, exit.Scheme, targetHost, targetPort, userOf(exit), p.ua); err != nil {
		_ = conn.Close()
		p.recordHop(hop, false, time.Since(started))
		return nil, err
	}
	p.recordHop(hop, true, time.Since(started))
	return conn, nil
}

func hopIndexPath(cacheDir string) string {
	if cacheDir == "" { return "" }
	return filepath.Join(cacheDir, "hop_index.json")
}

type hopDiskEntry struct {
	Ok      bool  `json:"ok"`
	Latency int64 `json:"latency_ms"`
	At      int64 `json:"at"`
}

func (p *ProxyServer) loadHopIndex() {
	if p.hopIdxPath == "" { return }
	raw, err := os.ReadFile(p.hopIdxPath)
	if err != nil { return }
	var stored map[string]hopDiskEntry
	if json.Unmarshal(raw, &stored) != nil { return }
	p.hopMu.Lock()
	if p.hopIdx == nil { p.hopIdx = map[string]hopProbe{} }
	for k, v := range stored {
		p.hopIdx[k] = hopProbe{ok: v.Ok, latency: time.Duration(v.Latency) * time.Millisecond, checked: time.Unix(v.At, 0)}
	}
	p.hopMu.Unlock()
	if len(stored) > 0 {
		log.Printf("[UPSTREAM] loaded %d hop measurement(s) from %s", len(stored), p.hopIdxPath)
	}
}

func (p *ProxyServer) saveHopIndex() {
	if p.hopIdxPath == "" { return }
	p.hopMu.Lock()
	if !p.hopDirty || len(p.hopIdx) == 0 {
		p.hopMu.Unlock()
		return
	}
	out := make(map[string]hopDiskEntry, len(p.hopIdx))
	for k, v := range p.hopIdx {
		out[k] = hopDiskEntry{Ok: v.ok, Latency: v.latency.Milliseconds(), At: v.checked.Unix()}
	}
	p.hopDirty = false
	p.hopMu.Unlock()
	if raw, err := json.Marshal(out); err == nil {
		tmp := p.hopIdxPath + ".tmp"
		if os.WriteFile(tmp, raw, 0o644) == nil { _ = os.Rename(tmp, p.hopIdxPath) }
	}
}

func isLoopbackHost(h string) bool {
	h = strings.ToLower(strings.TrimSpace(h))
	if h == "localhost" || h == "::1" || h == "0.0.0.0" { return true }
	addr, err := netip.ParseAddr(h)
	return err == nil && addr.IsLoopback()
}

func (p *ProxyServer) validateExitAtStartup() {
	exit := p.exitURL
	if exit == nil { return }
	hops := p.hopOrder()
	if len(hops) == 0 {
		log.Printf("[UPSTREAM] fixed exit %s dropped: no hop available to reach it", exit.Redacted())
		p.exitURL = nil
		return
	}
	if isLoopbackHost(exit.Hostname()) && slices.ContainsFunc(hops, func(h *url.URL) bool { return h != nil && !isLoopbackHost(h.Hostname()) }) {
		log.Printf("[UPSTREAM] fixed exit %s is local; a remote hop cannot reach it (it would dial its own localhost). Use it as the plain upstream instead of an exit.", exit.Redacted())
		p.exitURL = nil
		return
	}
	probeDest := net.JoinHostPort(loginHost, "443")
	hops = hops[:min(len(hops), 2)]
	found := make(chan *url.URL, len(hops))
	for _, hop := range hops {
		go func(hop *url.URL) {
			if conn, err := p.openViaHop(hop, exit, probeDest, 3*time.Second); err == nil {
				_ = conn.Close()
				found <- hop
			}
		}(hop)
	}
	select {
	case hop := <-found:
		log.Printf("[UPSTREAM] fixed exit %s carries %s through %s", exit.Redacted(), loginHost, hop.Redacted())
	case <-time.After(3500 * time.Millisecond):
		log.Printf("[UPSTREAM] fixed exit %s dropped: no hop could carry a request through it. Using the plain chain.", exit.Redacted())
		p.exitURL = nil
	}
}

func (p *ProxyServer) recordHop(hop *url.URL, ok bool, took time.Duration) {
	if hop == nil { return }
	p.hopMu.Lock()
	if p.hopIdx == nil { p.hopIdx = map[string]hopProbe{} }
	p.hopIdx[hop.String()] = hopProbe{ok: ok, latency: took, checked: time.Now()}
	p.hopDirty = true
	p.hopMu.Unlock()
}

func raceDials(n int, dialFn func(i int) (net.Conn, error)) (net.Conn, int, error) {
	type res struct {
		conn net.Conn
		idx  int
		err  error
	}
	ch := make(chan res, n)
	for i := 0; i < n; i++ {
		go func(i int) {
			c, err := dialFn(i)
			ch <- res{c, i, err}
		}(i)
	}
	var lastErr error
	for i := 0; i < n; i++ {
		r := <-ch
		if r.err == nil {
			go func(rem int) {
				for j := 0; j < rem; j++ {
					if late := <-ch; late.conn != nil { _ = late.conn.Close() }
				}
			}(n - i - 1)
			return r.conn, r.idx, nil
		}
		lastErr = r.err
	}
	return nil, -1, lastErr
}

func (p *ProxyServer) dialViaExit(ctx context.Context, exit *url.URL, dest string, timeout time.Duration) (net.Conn, *url.URL, error) {
	p.rememberDest(dest)
	hops := p.hopOrder()
	if len(hops) == 0 {
		conn, err := p.dialOne(exit, dest, dest, timeout)
		if err != nil { return nil, nil, err }
		return conn, exit, nil
	}
	hops = hops[:min(len(hops), 4)]
	conn, _, err := raceDials(len(hops), func(i int) (net.Conn, error) {
		return p.openViaHop(hops[i], exit, dest, timeout)
	})
	if err != nil { return nil, nil, cmp.Or(err, errors.New("no hop could reach the exit")) }
	p.upstreamMu.Lock()
	p.lastGood = exit
	p.upstreamMu.Unlock()
	return conn, exit, nil
}

func (p *ProxyServer) dialUpstreamHttp(u *url.URL, dest string, timeout time.Duration) (net.Conn, error) {
	defPort := "80"
	if strings.EqualFold(u.Scheme, "https") { defPort = "443" }
	conn, err := net.DialTimeout("tcp", ensureHostPort(u.Host, defPort), timeout)
	if err != nil { return nil, err }
	if err := httpConnect(conn, dest, userOf(u), p.ua, timeout); err != nil {
		_ = conn.Close()
		return nil, err
	}
	return conn, nil
}

func (p *ProxyServer) dialUpstreamAny(ctx context.Context, dest string, timeout time.Duration) (net.Conn, *url.URL, error) {
	socksTarget := p.resolver.resolveAddr(ctx, dest, "443")
	p.upstreamMu.Lock()
	preferred := p.lastGood
	p.upstreamMu.Unlock()

	order := make([]*url.URL, 0, len(p.upstreams)+1)
	if preferred != nil { order = append(order, preferred) }
	for _, u := range p.upstreams {
		if u != nil && (preferred == nil || u.String() != preferred.String()) {
			order = append(order, u)
		}
	}
	if preferred != nil {
		started := time.Now()
		conn, err := p.dialOne(preferred, dest, socksTarget, timeout)
		p.recordHop(preferred, err == nil, time.Since(started))
		if err == nil { return conn, preferred, nil }
		if active := atomic.LoadInt64(&p.telemetry.connsActive); active > 0 {
			log.Printf("[UPSTREAM] %s failed with %d live connection(s); refusing to switch exit IP mid-session", preferred.Redacted(), active)
			return nil, nil, err
		}
	}
	return p.raceUpstreams(ctx, order, dest, socksTarget, timeout)
}

func (p *ProxyServer) dialOne(u *url.URL, dest, socksTarget string, timeout time.Duration) (net.Conn, error) {
	if u == nil { return nil, fmt.Errorf("nil upstream") }
	if strings.HasPrefix(strings.ToLower(u.Scheme), "socks5") {
		return dialUpstreamSocks5(u, socksTarget, timeout)
	}
	return p.dialUpstreamHttp(u, dest, timeout)
}

func (p *ProxyServer) raceUpstreams(ctx context.Context, order []*url.URL, dest, socksTarget string, timeout time.Duration) (net.Conn, *url.URL, error) {
	if len(order) == 0 { return nil, nil, fmt.Errorf("no upstream configured") }
	conn, idx, err := raceDials(len(order), func(i int) (net.Conn, error) {
		u := order[i]
		started := time.Now()
		c, err := p.dialOne(u, dest, socksTarget, timeout)
		p.recordHop(u, err == nil, time.Since(started))
		return c, err
	})
	if err != nil { return nil, nil, cmp.Or(err, fmt.Errorf("no upstream configured")) }
	winner := order[idx]
	p.upstreamMu.Lock()
	changed := p.lastGood == nil || p.lastGood.String() != winner.String()
	p.lastGood = winner
	p.upstreamMu.Unlock()
	if changed {
		log.Printf("[UPSTREAM] carrying %s over %s (exit changed)", dest, winner.Redacted())
	}
	return conn, winner, nil
}

func (p *ProxyServer) dialUpstreamOrDirect(ctx context.Context, dest string) (net.Conn, error) {
	start := time.Now()
	conn, err := p.doDial(ctx, dest)
	p.telemetry.recordDial(float64(time.Since(start).Microseconds())/1000.0, err)
	return conn, err
}

func (p *ProxyServer) dialDirect(ctx context.Context, dest string) (net.Conn, error) {
	target := p.resolver.resolveAddr(ctx, dest, "443")
	return (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(ctx, "tcp", target)
}

func (p *ProxyServer) doDial(ctx context.Context, dest string) (net.Conn, error) {
	if host, port, err := net.SplitHostPort(dest); err == nil {
		if h := strings.ToLower(host); h == "dofus.local" || h == "dofus.localhost" {
			mapped := net.JoinHostPort(loginHost, port)
			virtualHostRewriteOnce.Do(func() {
				log.Printf("[ROUTE] %s -> %s (the client resolved its socket against the page origin)", dest, mapped)
			})
			dest = mapped
		}
	}
	if p.exitURL != nil {
		p.exitMu.Lock()
		blocked := time.Now().Before(p.exitBlockedUntil)
		p.exitMu.Unlock()
		if !blocked {
			if conn, _, err := p.dialViaExit(ctx, p.exitURL, dest, 6*time.Second); err == nil {
				upstreamHealthy.Store(true)
				return conn, nil
			} else {
				p.exitMu.Lock()
				p.exitBlockedUntil = time.Now().Add(exitCooldown)
				p.exitMu.Unlock()
				upstreamHealthy.Store(false)
				log.Printf("[UPSTREAM] fixed exit %s unreachable through any hop (%v); using the plain chain for the next %s (the exit IP will change)",
					p.exitURL.Redacted(), err, exitCooldown)
			}
		}
	}
	if p.upstreamURL != nil {
		if len(p.upstreams) > 0 {
			var err error
			for attempt := 0; attempt < 3; attempt++ {
				if attempt > 0 { time.Sleep(time.Duration(attempt) * 1500 * time.Millisecond) }
				if conn, _, dialErr := p.dialUpstreamAny(ctx, dest, 6*time.Second); dialErr == nil {
					upstreamHealthy.Store(true)
					return conn, nil
				} else {
					err = dialErr
				}
			}
			upstreamHealthy.Store(false)
			if p.allowDirect {
				upstreamFallbackOnce.Do(func() {
					log.Printf("[UPSTREAM] dial to %s via %s failed (%v); -allow-direct is set, so falling back to a DIRECT connection. Ankama will see this machine's real IP.",
						dest, p.upstreamURL.Redacted(), err)
				})
				return p.dialDirect(ctx, dest)
			}
			upstreamFallbackOnce.Do(func() {
				log.Printf("[UPSTREAM] dial to %s via %s failed (%v); refusing to fall back to a direct connection because that would expose the real IP. Start WARP in proxy mode (warp-cli mode proxy && warp-cli connect) and relaunch.",
					dest, p.upstreamURL.Redacted(), err)
			})
			return nil, fmt.Errorf("upstream %s unavailable and direct dial disabled: %w", p.upstreamURL.Redacted(), err)
		}
		return p.dialUpstreamHttp(p.upstreamURL, dest, 5*time.Second)
	}
	return p.dialDirect(ctx, dest)
}

func (p *ProxyServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if isLocalRequestHost(r.Host) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/__cdn/"):
			p.handleCDNRelay(w, r)
			return
		case r.URL.Path == "/__files" || r.URL.Path == "/__warm" || r.URL.Path == "/__delete":
			p.handleAssetStore(w, r)
			return
		}
	}
	if p.authToken != "" {
		auth := cmp.Or(r.Header.Get("Proxy-Authorization"), r.Header.Get("Authorization"), r.Header.Get("X-Proxy-Token"))
		if auth != p.authToken && auth != "Bearer "+p.authToken {
			http.Error(w, "Proxy Authentication Required", http.StatusProxyAuthRequired)
			return
		}
	}
	isLocal := r.Host == "" || strings.HasPrefix(r.Host, "127.0.0.1") || strings.HasPrefix(r.Host, "localhost")
	if r.Method == http.MethodGet && isLocal {
		switch r.URL.Path {
		case "/__health", "/__telemetry":
			w.Header().Set("Content-Type", "application/json")
			s := p.telemetry.snapshot()
			_, _ = fmt.Fprintf(w, `{"status":"ok","port":%d,"model":"%s","rtt_ms":%.2f,"rtt_avg_ms":%.2f,"sent_bytes":%d,"recv_bytes":%d,"conns_total":%d,"conns_failed":%d,"conns_active":%d,"timestamp":%d}`,
				p.port, p.model, s.lastRTTMs, s.avgRTTMs, s.bytesSent, s.bytesRecv, s.connsTotal, s.connsFailed, s.connsActive, time.Now().Unix())
			return
		case "/__audit":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write(p.auditOutboundIP())
			return
		}
	}
	if r.Method == http.MethodGet && (r.URL.Path == "/authorized" || r.URL.Path == "/login-authorized" || strings.HasPrefix(r.URL.Path, "/oauth/callback")) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		qs := ""
		if r.URL.RawQuery != "" { qs = "?" + r.URL.RawQuery }
		_, _ = fmt.Fprintf(w, `<!DOCTYPE html><html><head><title>Authorization Success</title></head><body><script>if(window.opener&&window.opener.__onDeepLinkCallback){window.opener.__onDeepLinkCallback(window.location.href);}window.location.href="dofustouch://authorized%s";</script><h2>Authorization Successful</h2><p>Redirecting back to Dofus Touch...</p></body></html>`, qs)
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
	errResp := func(msg, verdict string) []byte {
		res, _ := json.Marshal(map[string]any{
			"status": "error", "port": p.port, "model": p.model,
			"message": msg, "verdict": verdict, "risk_score": 50,
		})
		return res
	}
	client := &http.Client{
		Transport: &http.Transport{
			DialContext:           func(ctx context.Context, _, addr string) (net.Conn, error) { return p.dialUpstreamOrDirect(ctx, addr) },
			ResponseHeaderTimeout: 5 * time.Second,
		},
		Timeout: 6 * time.Second,
	}
	resp, err := client.Get("http://ip-api.com/json/?fields=status,message,country,city,isp,org,as,query,mobile,proxy,hosting")
	if err != nil { return errResp(err.Error(), "LOOKUP_OFFLINE") }
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var info struct {
		Status, Query, ISP, Org, AS, Country, City, Message string
		Mobile, Proxy, Hosting                              bool
	}
	if json.Unmarshal(body, &info) != nil || info.Status != "success" {
		return errResp("upstream_ip_api_error", "UNKNOWN")
	}
	riskScore, verdict := 0, "SAFE_RESIDENTIAL"
	switch {
	case info.Hosting:
		riskScore, verdict = 85, "DANGEROUS_DATACENTER"
	case info.Mobile:
		riskScore, verdict = 0, "PRISTINE_CELLULAR_4G"
	case info.Proxy:
		riskScore, verdict = 45, "SUSPICIOUS_PUBLIC_PROXY"
	}
	res, _ := json.Marshal(map[string]any{
		"status": "ok", "port": p.port, "model": p.model, "outbound_ip": info.Query,
		"isp": info.ISP, "org": info.Org, "asn": info.AS, "country": info.Country, "city": info.City,
		"is_cellular": info.Mobile, "is_hosting": info.Hosting, "is_proxy": info.Proxy,
		"risk_score": riskScore, "verdict": verdict,
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
	copyDir := func(dst, src net.Conn, counter *int64, pre *bufio.ReadWriter) {
		if pre != nil && pre.Reader.Buffered() > 0 {
			n, _ := io.CopyN(dst, pre, int64(pre.Reader.Buffered()))
			atomic.AddInt64(counter, n)
		}
		buf := bufPool.Get().([]byte)
		n, _ := io.CopyBuffer(dst, src, buf)
		atomic.AddInt64(counter, n)
		bufPool.Put(buf)
		halfCloseOrClose(dst)
		done <- struct{}{}
	}
	go copyDir(destConn, clientConn, &sentBytes, clientBuf)
	go copyDir(clientConn, destConn, &recvBytes, nil)
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

func (p *ProxyServer) dialTunnel(host, defPort string) (net.Conn, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 7*time.Second)
	defer cancel()
	return p.dialUpstreamOrDirect(ctx, ensureHostPort(host, defPort))
}

func (p *ProxyServer) handleConnect(w http.ResponseWriter, r *http.Request) {
	dest := ensureHostPort(r.Host, "443")
	destConn, err := p.dialTunnel(r.Host, "443")
	if err != nil {
		http.Error(w, "Connection Failed: "+err.Error(), http.StatusBadGateway)
		return
	}
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		_ = destConn.Close()
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}
	clientConn, clientBuf, err := hijacker.Hijack()
	if err != nil {
		_ = destConn.Close()
		http.Error(w, "Hijack failed: "+err.Error(), http.StatusServiceUnavailable)
		return
	}
	if _, err = clientConn.Write(tunnelEstablished); err != nil {
		_ = destConn.Close()
		_ = clientConn.Close()
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
	destConn, err := p.dialTunnel(r.Host, "80")
	if err != nil {
		_ = clientConn.Close()
		return
	}
	p.sanitizeRequestHeaders(r.Header)
	r.RequestURI = ""
	if r.URL != nil {
		r.URL.Scheme, r.URL.Host = "", ""
	}
	if err := r.Write(destConn); err != nil {
		_ = destConn.Close()
		_ = clientConn.Close()
		return
	}
	p.pipeTunnel(clientConn, clientBuf, destConn)
}

func atomicReplaceFile(src, dst string) error {
	if err := os.Rename(src, dst); err == nil { return nil }
	var err error
	for attempt := 0; attempt < 3; attempt++ {
		time.Sleep(20 * time.Millisecond)
		_ = os.Remove(dst)
		if err = os.Rename(src, dst); err == nil { return nil }
	}
	oldPath := dst + ".old." + strconv.FormatInt(time.Now().UnixNano(), 10)
	if os.Rename(dst, oldPath) == nil {
		_ = os.Remove(oldPath)
		if os.Rename(src, dst) == nil { return nil }
	}
	return err
}

func detectMimeType(path string) string {
	ext := strings.ToLower(filepath.Ext(path))
	if m, ok := extMimeMap[ext]; ok { return m }
	return cmp.Or(mime.TypeByExtension(ext), "application/octet-stream")
}

func (p *ProxyServer) getDisguiseContent() string {
	p.disguiseOnce.Do(func() {
		p.cachedDisguise = p.loadDisguiseFromDisk()
		p.disguiseBytes = []byte(p.cachedDisguise)
	})
	return p.cachedDisguise
}

func (p *ProxyServer) injectDisguise(bodyBytes []byte) []byte {
	code := p.getDisguiseContent()
	injection := fmt.Sprintf("\n\t\t<script type=\"text/javascript\" src=\"http://10.0.2.2:%d/__disguise.js\"></script>\n", p.port)
	if code != "" {
		injection = "\n\t\t<script type=\"text/javascript\">\n" + code + "\n\t\t</script>\n"
	}
	dst := make([]byte, 0, len(bodyBytes)+len(injection)+14)
	if loc := reHeadOpen.FindIndex(bodyBytes); loc != nil {
		return append(append(append(dst, bodyBytes[:loc[1]]...), injection...), bodyBytes[loc[1]:]...)
	}
	if loc := reHeadClose.FindIndex(bodyBytes); loc != nil {
		return append(append(append(dst, bodyBytes[:loc[0]]...), injection...), bodyBytes[loc[0]:]...)
	}
	wrapped := "<head>" + injection + "</head>"
	if loc := reHtmlOpen.FindIndex(bodyBytes); loc != nil {
		return append(append(append(dst, bodyBytes[:loc[1]]...), wrapped...), bodyBytes[loc[1]:]...)
	}
	return append(append(dst, wrapped...), bodyBytes...)
}

func (p *ProxyServer) loadDisguiseFromDisk() string {
	exeDir := filepath.Dir(os.Args[0])
	for _, c := range []string{
		filepath.Join("scripts", "mobile-disguise.js"),
		"mobile-disguise.js",
		filepath.Join(os.Getenv("DOFUS_FARM_HOME"), "scripts", "mobile-disguise.js"),
		filepath.Join(exeDir, "scripts", "mobile-disguise.js"),
		filepath.Join(exeDir, "mobile-disguise.js"),
	} {
		b, err := os.ReadFile(c)
		if err != nil || len(b) == 0 { continue }
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
			if kv[1] != "" { pairs = append(pairs, kv[0], kv[1]) }
		}
		if len(pairs) > 0 { return strings.NewReplacer(pairs...).Replace(string(b)) }
		return string(b)
	}
	return ""
}

func isLocalRequestHost(host string) bool {
	h := strings.ToLower(host)
	return h == "" || strings.HasPrefix(h, "127.0.0.1") || strings.HasPrefix(h, "localhost") || strings.HasPrefix(h, "[::1]")
}

func isCdnAssetHost(host string) bool {
	if h, _, ok := strings.Cut(host, ":"); ok { host = h }
	host = strings.ToLower(host)
	return host == "cdn.ankama.com" || host == "cdn.ankama-games.com" ||
		strings.HasSuffix(host, ".cdn.ankama.com") || strings.HasSuffix(host, ".cdn.ankama-games.com")
}

func isStaticAnkamaHost(host string) bool {
	if h, _, ok := strings.Cut(host, ":"); ok { host = h }
	return strings.EqualFold(host, "static.ankama.com")
}

func isVolatileCacheURL(u string) bool {
	if strings.Contains(u, "production-login") { return true }
	if i := strings.IndexAny(u, "?#"); i != -1 { u = u[:i] }
	return strings.HasSuffix(u, "/config.json") || strings.HasSuffix(u, "/getLanguage.json") || strings.HasSuffix(u, "/version.json")
}

func isEmulatorOrigin(v string) bool {
	l := strings.ToLower(v)
	return v == "" || strings.Contains(l, "dofus.local") || strings.Contains(l, "localhost") ||
		strings.Contains(l, "127.0.0.1") || strings.HasPrefix(l, "file:")
}

func (p *ProxyServer) applyHotlinkHeaders(h http.Header, host string) {
	if !isCdnAssetHost(host) { return }
	if ref := h.Get("Referer"); ref != "" && isEmulatorOrigin(ref) {
		h.Set("Referer", p.referer)
	}
	if org := h.Get("Origin"); org != "" && isEmulatorOrigin(org) {
		h.Set("Origin", p.refererRoot)
	}
}

func (p *ProxyServer) assetCachePath(target string) string {
	sum := sha256.Sum256([]byte(target))
	return filepath.Join(p.cacheDir, hex.EncodeToString(sum[:16]))
}

func setLongAssetCacheHeaders(h http.Header) {
	h.Set("Cache-Control", "public, max-age=604800, immutable")
}

func setCORSHeaders(h http.Header, methods string) {
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", methods)
	h.Set("Access-Control-Allow-Headers", "*")
}

func (p *ProxyServer) serveCachedAsset(w http.ResponseWriter, r *http.Request, cacheFilePath, fallbackPath string) (served bool) {
	defer func() {
		if served {
			atomic.AddUint64(&p.cacheHits, 1)
		} else {
			atomic.AddUint64(&p.cacheMisses, 1)
		}
	}()
	fi, err := os.Stat(cacheFilePath)
	if err != nil { return false }
	if time.Since(fi.ModTime()) > assetCacheTTL {
		_ = os.Remove(cacheFilePath)
		_ = os.Remove(cacheFilePath + ".meta")
		return false
	}
	f, err := os.Open(cacheFilePath)
	if err != nil { return false }
	defer f.Close()
	h := w.Header()
	setCORSHeaders(h, "GET, POST, OPTIONS, HEAD")
	setLongAssetCacheHeaders(h)
	metaBytes, _ := os.ReadFile(cacheFilePath + ".meta")
	if mimeType := cmp.Or(strings.TrimSpace(string(metaBytes)), detectMimeType(fallbackPath)); mimeType != "" {
		h.Set("Content-Type", mimeType)
	}
	http.ServeContent(w, r, fallbackPath, fi.ModTime(), f)
	return true
}

func (p *ProxyServer) storeCacheEntry(targetStr, assetPath, cacheFilePath, ct string, r io.Reader, expectedLen int64, dst io.Writer) error {
	tmpPath := fmt.Sprintf("%s.%d.%d.tmp", cacheFilePath, os.Getpid(), time.Now().UnixNano())
	tf, err := os.Create(tmpPath)
	if err != nil {
		if dst != nil {
			buf := bufPool.Get().([]byte)
			_, _ = io.CopyBuffer(dst, r, buf)
			bufPool.Put(buf)
		}
		return err
	}
	var w io.Writer = tf
	if dst != nil { w = io.MultiWriter(dst, tf) }
	buf := bufPool.Get().([]byte)
	written, copyErr := io.CopyBuffer(w, r, buf)
	bufPool.Put(buf)
	_ = tf.Close()
	if copyErr != nil || (dst == nil && written == 0) || (expectedLen > 0 && written < expectedLen) {
		_ = os.Remove(tmpPath)
		return fmt.Errorf("copy: err=%v written=%d contentLength=%d", copyErr, written, expectedLen)
	}
	if err := atomicReplaceFile(tmpPath, cacheFilePath); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	if ct != "" { _ = os.WriteFile(cacheFilePath+".meta", []byte(ct), 0644) }
	p.mirrorCachedFile(targetStr, cacheFilePath)
	if assetPath != "" {
		if id := mapJSONID(assetPath); id > 0 { p.scheduleMapPrefetch(targetStr, id) }
	}
	return nil
}

func mirrorRelPath(target string) string {
	u, err := url.Parse(target)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" { return "" }
	host := strings.ToLower(u.Hostname())
	if host == "" || strings.ContainsAny(host, `<>:"|?*\`) || u.Path == "" || u.Path == "/" ||
		strings.Contains(u.Path, "..") || strings.ContainsAny(u.Path, `<>:"|*\`) {
		return ""
	}
	return host + u.Path
}

func (p *ProxyServer) mirrorPathFor(target string) string {
	if p.cacheDir == "" { return "" }
	if rel := mirrorRelPath(target); rel != "" {
		return filepath.Join(p.cacheDir, "files", filepath.FromSlash(rel))
	}
	return ""
}

func (p *ProxyServer) mirrorCachedFile(target, cacheFilePath string) {
	mirror := p.mirrorPathFor(target)
	if mirror == "" { return }
	if _, err := os.Stat(mirror); err == nil { return }
	if os.MkdirAll(filepath.Dir(mirror), 0755) != nil { return }
	rel := mirrorRelPath(target)
	if os.Link(cacheFilePath, mirror) == nil {
		p.mirrorIndexAdd(rel)
		return
	}
	in, err := os.Open(cacheFilePath)
	if err != nil { return }
	defer in.Close()
	out, err := os.Create(mirror)
	if err != nil { return }
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		_ = os.Remove(mirror)
		return
	}
	_ = out.Close()
	p.mirrorIndexAdd(rel)
}

func (p *ProxyServer) mirrorIndexAdd(rel string) {
	if rel == "" { return }
	p.mirrorMu.Lock()
	if p.mirrorBuilt && p.mirrorIndex != nil { p.mirrorIndex[rel] = struct{}{} }
	p.mirrorMu.Unlock()
}

func (p *ProxyServer) mirrorIndexSnapshot() []string {
	p.mirrorMu.Lock()
	defer p.mirrorMu.Unlock()
	if !p.mirrorBuilt {
		p.mirrorIndex = make(map[string]struct{}, 4096)
		root := filepath.Join(p.cacheDir, "files")
		_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err == nil && info != nil && !info.IsDir() {
				if rel, relErr := filepath.Rel(root, path); relErr == nil {
					p.mirrorIndex[filepath.ToSlash(rel)] = struct{}{}
				}
			}
			return nil
		})
		p.mirrorBuilt = true
	}
	out := make([]string, 0, len(p.mirrorIndex))
	for rel := range p.mirrorIndex {
		out = append(out, rel)
	}
	slices.Sort(out)
	return out
}

func (p *ProxyServer) handleAssetStore(w http.ResponseWriter, r *http.Request) {
	h := w.Header()
	setCORSHeaders(h, "GET, OPTIONS")
	h.Set("Cache-Control", "no-store")
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}
	switch r.URL.Path {
	case "/__files":
		payload, err := json.Marshal(p.mirrorIndexSnapshot())
		if err != nil {
			http.Error(w, "encode error", http.StatusInternalServerError)
			return
		}
		h.Set("Content-Type", "application/json")
		if len(payload) > 32<<10 && strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			h.Set("Content-Encoding", "gzip")
			w.WriteHeader(http.StatusOK)
			gz := gzip.NewWriter(w)
			_, _ = gz.Write(payload)
			_ = gz.Close()
			return
		}
		_, _ = w.Write(payload)
	case "/__warm":
		target := r.URL.Query().Get("u")
		if !isCacheAssetURL(target) {
			http.Error(w, "Bad target", http.StatusBadRequest)
			return
		}
		if !p.quiet { log.Printf("[WARM] %s", target) }
		ok, already, status, errText := p.warmAssetCacheDetail(r.Context(), target)
		h.Set("Content-Type", "application/json")
		if !ok {
			p.prefetchLog("[WARM] failed status=%d err=%q %s", status, errText, target)
			w.WriteHeader(http.StatusBadGateway)
			_, _ = fmt.Fprintf(w, `{"ok":false,"status":%d,"err":%q}`, status, errText)
			return
		}
		p.requestLog("WARM %s", target)
		if u, err := url.Parse(target); err == nil {
			if id := mapJSONID(u.Path); id > 0 { p.scheduleMapPrefetch(target, id) }
		}
		_, _ = fmt.Fprintf(w, `{"ok":true,"cached":%t}`, already)
	case "/__delete":
		needle := strings.Trim(r.URL.Query().Get("p"), "/")
		if needle == "" {
			http.Error(w, "Bad path", http.StatusBadRequest)
			return
		}
		removed := 0
		for _, rel := range p.mirrorIndexSnapshot() {
			if rel != needle && !strings.HasSuffix(rel, "/"+needle) { continue }
			_ = os.Remove(filepath.Join(p.cacheDir, "files", filepath.FromSlash(rel)))
			cp := p.assetCachePath("https://" + rel)
			_ = os.Remove(cp)
			_ = os.Remove(cp + ".meta")
			p.mirrorMu.Lock()
			delete(p.mirrorIndex, rel)
			p.mirrorMu.Unlock()
			removed++
		}
		h.Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"ok":true,"removed":%d}`, removed)
	default:
		http.Error(w, "Not Found", http.StatusNotFound)
	}
}

func (p *ProxyServer) loadModsIfChanged() {
	if p.modsDir == "" { return }
	p.modsMu.Lock()
	defer p.modsMu.Unlock()
	if time.Since(p.modsChecked) < 2*time.Second { return }
	p.modsChecked = time.Now()
	rulesPath := filepath.Join(p.modsDir, "rules.json")
	fi, err := os.Stat(rulesPath)
	if err != nil {
		if len(p.modsRules) > 0 {
			p.modsRules, p.modsRes = nil, nil
			log.Printf("[MODS] rules.json removed; mods disabled")
		}
		return
	}
	if fi.ModTime().Equal(p.modsRulesMod) { return }
	raw, err := os.ReadFile(rulesPath)
	if err != nil { return }
	var rules []modRule
	if err := json.Unmarshal(raw, &rules); err != nil {
		log.Printf("[MODS] rules.json parse error: %v", err)
		return
	}
	res := make([]*regexp.Regexp, len(rules))
	for i := range rules {
		re, err := regexp.Compile(rules[i].Match)
		if err != nil {
			log.Printf("[MODS] rule %d has invalid match %q: %v", i, rules[i].Match, err)
			continue
		}
		res[i] = re
	}
	p.modsRules, p.modsRes, p.modsRulesMod = rules, res, fi.ModTime()
	log.Printf("[MODS] loaded %d rule(s) from %s", len(rules), rulesPath)
}

func (p *ProxyServer) applyModRules(w http.ResponseWriter, r *http.Request, targetStr string) bool {
	if p.modsDir == "" { return false }
	p.loadModsIfChanged()
	p.modsMu.Lock()
	rules, res := p.modsRules, p.modsRes
	p.modsMu.Unlock()
	for i, rule := range rules {
		if i >= len(res) || res[i] == nil || !res[i].MatchString(targetStr) { continue }
		switch rule.Action {
		case "block":
			status := rule.Status
			if status < 100 || status > 599 { status = http.StatusNoContent }
			w.WriteHeader(status)
			p.requestLog("MODBLOCK %d %s", status, targetStr)
			return true
		case "override":
			if p.serveModOverride(w, r, rule, targetStr) { return true }
		case "patch":
			if p.serveModPatched(w, r, rule, targetStr) { return true }
		}
	}
	return false
}

func (p *ProxyServer) serveModOverride(w http.ResponseWriter, r *http.Request, rule modRule, targetStr string) bool {
	rel := strings.TrimSpace(rule.File)
	if rel == "" || filepath.IsAbs(rel) || strings.Contains(rel, "..") {
		p.prefetchLog("[MODS] override rule for %s has invalid file %q", targetStr, rule.File)
		return false
	}
	full := filepath.Join(p.modsDir, filepath.FromSlash(rel))
	f, err := os.Open(full)
	if err != nil {
		p.prefetchLog("[MODS] override file missing: %s", full)
		return false
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil || fi.IsDir() { return false }
	h := w.Header()
	setCORSHeaders(h, "GET, HEAD, OPTIONS")
	setLongAssetCacheHeaders(h)
	if ct := cmp.Or(rule.ContentType, detectMimeType(full)); ct != "" {
		h.Set("Content-Type", ct)
	}
	http.ServeContent(w, r, filepath.Base(full), fi.ModTime(), f)
	p.requestLog("MODOVER %s", targetStr)
	return true
}

func (p *ProxyServer) serveModPatched(w http.ResponseWriter, r *http.Request, rule modRule, targetStr string) bool {
	body, ct, ok := p.patchedAssetBody(r.Context(), targetStr, rule)
	if !ok { return false }
	h := w.Header()
	setCORSHeaders(h, "GET, HEAD, OPTIONS")
	setLongAssetCacheHeaders(h)
	h.Set("Content-Type", cmp.Or(rule.ContentType, ct, "application/json"))
	h.Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = w.Write(body)
	}
	p.requestLog("MODPATCH %s (%d bytes)", targetStr, len(body))
	return true
}

func readFileLimited(path string, maxBytes int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil { return nil, err }
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, maxBytes))
}

func (p *ProxyServer) patchedAssetBody(ctx context.Context, targetStr string, rule modRule) ([]byte, string, bool) {
	cacheFilePath := p.assetCachePath(targetStr)
	if _, err := os.Stat(cacheFilePath); err != nil {
		if ok, _, status, errText := p.warmAssetCacheDetail(ctx, targetStr); !ok {
			p.prefetchLog("[MODS] patch base fetch failed status=%d err=%q %s", status, errText, targetStr)
			return nil, "", false
		}
	}
	raw, err := readFileLimited(cacheFilePath, 64<<20)
	if err != nil { return nil, "", false }
	metaBytes, _ := os.ReadFile(cacheFilePath + ".meta")
	ct := strings.TrimSpace(string(metaBytes))
	var doc any
	if json.Unmarshal(raw, &doc) != nil { return nil, "", false }
	for _, op := range rule.Patch {
		switch strings.ToLower(strings.TrimSpace(op.Op)) {
		case "set":
			doc, _ = jsonPointerSet(doc, op.Path, op.Value)
		case "remove", "delete":
			doc, _ = jsonPointerRemove(doc, op.Path)
		}
	}
	out, err := json.Marshal(doc)
	if err != nil { return nil, "", false }
	return out, ct, true
}

func jsonPointerTokens(pointer string) []string {
	pointer = strings.TrimPrefix(pointer, "/")
	if pointer == "" { return nil }
	parts := strings.Split(pointer, "/")
	for i, s := range parts {
		parts[i] = strings.ReplaceAll(strings.ReplaceAll(s, "~1", "/"), "~0", "~")
	}
	return parts
}

func jsonPointerSet(doc any, pointer string, value any) (any, bool) {
	tokens := jsonPointerTokens(pointer)
	if len(tokens) == 0 { return value, true }
	return pointerSetWalk(doc, tokens, value, 0)
}

func pointerSetWalk(node any, tokens []string, value any, idx int) (any, bool) {
	tok, last := tokens[idx], idx == len(tokens)-1
	switch cur := node.(type) {
	case map[string]any:
		if last {
			cur[tok] = value
			return cur, true
		}
		child := cur[tok]
		if child == nil { child = map[string]any{} }
		v, ok := pointerSetWalk(child, tokens, value, idx+1)
		if ok { cur[tok] = v }
		return cur, ok
	case []any:
		if last && tok == "-" { return append(cur, value), true }
		n, err := strconv.Atoi(tok)
		if err != nil || n < 0 || n >= len(cur) { return cur, false }
		if last {
			cur[n] = value
			return cur, true
		}
		v, ok := pointerSetWalk(cur[n], tokens, value, idx+1)
		if ok { cur[n] = v }
		return cur, ok
	}
	return node, false
}

func jsonPointerRemove(doc any, pointer string) (any, bool) {
	tokens := jsonPointerTokens(pointer)
	if len(tokens) == 0 { return doc, false }
	return pointerRemoveWalk(doc, tokens, 0)
}

func pointerRemoveWalk(node any, tokens []string, idx int) (any, bool) {
	tok, last := tokens[idx], idx == len(tokens)-1
	switch cur := node.(type) {
	case map[string]any:
		child, ok := cur[tok]
		if !ok { return cur, false }
		if last {
			delete(cur, tok)
			return cur, true
		}
		v, ok := pointerRemoveWalk(child, tokens, idx+1)
		if ok { cur[tok] = v }
		return cur, ok
	case []any:
		n, err := strconv.Atoi(tok)
		if err != nil || n < 0 || n >= len(cur) { return cur, false }
		if last { return slices.Delete(cur, n, n+1), true }
		v, ok := pointerRemoveWalk(cur[n], tokens, idx+1)
		if ok { cur[n] = v }
		return cur, ok
	}
	return node, false
}

func (p *ProxyServer) newCDNRequest(ctx context.Context, method, targetStr string, src http.Header) (*http.Request, error) {
	req, err := http.NewRequestWithContext(ctx, method, targetStr, nil)
	if err != nil { return nil, err }
	req.Header.Set("User-Agent", p.ua)
	req.Header.Set("Referer", p.referer)
	req.Header.Set("Origin", p.refererRoot)
	req.Header.Set("Accept", "*/*")
	for _, k := range []string{"Cookie", "Range", "If-None-Match", "If-Modified-Since"} {
		if v := src.Get(k); v != "" { req.Header.Set(k, v) }
	}
	return req, nil
}

func (p *ProxyServer) tryServeCached(w http.ResponseWriter, r *http.Request, targetStr, assetPath, cacheFilePath string) bool {
	if !p.serveCachedAsset(w, r, cacheFilePath, assetPath) { return false }
	p.mirrorCachedFile(targetStr, cacheFilePath)
	if id := mapJSONID(assetPath); id > 0 { p.scheduleMapPrefetch(targetStr, id) }
	return true
}

func (p *ProxyServer) handleCDNRelay(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w.Header(), "GET, HEAD, OPTIONS")
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}
	rest := strings.TrimPrefix(r.URL.Path, "/__cdn/")
	if rest == "" || rest == "__probe" {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	slash := strings.IndexByte(rest, '/')
	if slash <= 0 {
		http.Error(w, "Bad relay target", http.StatusBadRequest)
		return
	}
	host, assetPath := rest[:slash], rest[slash:]
	if !isCdnAssetHost(host) && !isStaticAnkamaHost(host) {
		http.Error(w, "Host not allowed", http.StatusForbidden)
		return
	}
	targetStr := "https://" + host + assetPath
	if r.URL.RawQuery != "" { targetStr += "?" + r.URL.RawQuery }
	if p.applyModRules(w, r, targetStr) { return }
	reqStart := time.Now()
	var cacheFilePath string
	if p.cacheDir != "" && isCacheAssetURL(targetStr) && !isVolatileCacheURL(targetStr) {
		cacheFilePath = p.assetCachePath(targetStr)
		if p.tryServeCached(w, r, targetStr, assetPath, cacheFilePath) {
			if !p.quiet { log.Printf("[RELAY] HIT %s", targetStr) }
			p.requestLog("HIT %s", targetStr)
			return
		}
	}
	if status, bad := p.negCacheHit(targetStr); bad {
		setCORSHeaders(w.Header(), "GET, HEAD, OPTIONS")
		w.WriteHeader(status)
		p.requestLog("%d 0ms negative-cache %s", status, targetStr)
		return
	}
	outReq, err := p.newCDNRequest(r.Context(), r.Method, targetStr, r.Header)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	atomic.AddInt64(&p.clientInflight, 1)
	defer atomic.AddInt64(&p.clientInflight, -1)
	resp, err := p.client.Do(outReq)
	if err != nil {
		p.requestLog("ERR %dms %s (%v)", time.Since(reqStart).Milliseconds(), targetStr, err)
		http.Error(w, "Relay Error: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	wh := w.Header()
	copyHeaderFiltered(wh, resp.Header, hopRespHeaders)
	setCORSHeaders(wh, "GET, HEAD, OPTIONS")
	switch resp.StatusCode {
	case http.StatusOK:
		setLongAssetCacheHeaders(wh)
		p.negCacheClear(targetStr)
	case http.StatusForbidden, http.StatusNotFound:
		p.negCacheStore(targetStr, resp.StatusCode)
	}
	if !p.quiet { log.Printf("[RELAY] %d %s", resp.StatusCode, targetStr) }
	p.requestLog("%d %dms %s", resp.StatusCode, time.Since(reqStart).Milliseconds(), targetStr)
	w.WriteHeader(resp.StatusCode)
	if cacheFilePath == "" || resp.StatusCode != http.StatusOK || r.Method != http.MethodGet {
		buf := bufPool.Get().([]byte)
		_, _ = io.CopyBuffer(w, resp.Body, buf)
		bufPool.Put(buf)
		return
	}
	_ = p.storeCacheEntry(targetStr, assetPath, cacheFilePath, resp.Header.Get("Content-Type"), resp.Body, resp.ContentLength, w)
}

func (p *ProxyServer) warmAssetCache(ctx context.Context, target string) (bool, bool) {
	ok, already, _, _ := p.warmAssetCacheDetail(ctx, target)
	return ok, already
}

func (p *ProxyServer) warmAssetCacheDetail(ctx context.Context, target string) (bool, bool, int, string) {
	if p.cacheDir == "" || !isCacheAssetURL(target) || isVolatileCacheURL(target) {
		return false, false, 0, "not cacheable"
	}
	cacheFilePath := p.assetCachePath(target)
	if fi, err := os.Stat(cacheFilePath); err == nil {
		if time.Since(fi.ModTime()) <= assetCacheTTL {
			p.mirrorCachedFile(target, cacheFilePath)
			return true, true, 200, ""
		}
		_ = os.Remove(cacheFilePath)
		_ = os.Remove(cacheFilePath + ".meta")
	}
	if status, bad := p.negCacheHit(target); bad {
		return false, false, status, "negative-cached (recent upstream 4xx)"
	}
	type warmRes struct {
		claimed atomic.Bool
		ok      bool
		status  int
		errTxt  string
	}
	ch := p.warmSF.DoChan(target, func() (any, error) {
		req, err := p.newCDNRequest(context.WithoutCancel(ctx), http.MethodGet, target, http.Header{})
		if err != nil { return &warmRes{ok: false, status: 0, errTxt: "request: " + err.Error()}, nil }
		for i := 0; i < 100 && atomic.LoadInt64(&p.clientInflight) > 0; i++ {
			if !sleepCtx(ctx, 50*time.Millisecond) {
				return &warmRes{ok: false, status: 0, errTxt: "canceled while yielding to client traffic"}, nil
			}
		}
		resp, err := p.client.Do(req)
		if err != nil { return &warmRes{ok: false, status: 0, errTxt: "fetch: " + err.Error()}, nil }
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			p.negCacheStore(target, resp.StatusCode)
			return &warmRes{ok: false, status: resp.StatusCode}, nil
		}
		p.negCacheClear(target)
		if err := p.storeCacheEntry(target, "", cacheFilePath, resp.Header.Get("Content-Type"), io.LimitReader(resp.Body, 64<<20), resp.ContentLength, nil); err != nil {
			return &warmRes{ok: false, status: resp.StatusCode, errTxt: err.Error()}, nil
		}
		return &warmRes{ok: true, status: resp.StatusCode}, nil
	})
	select {
	case res := <-ch:
		wr := res.Val.(*warmRes)
		if wr.claimed.CompareAndSwap(false, true) { return wr.ok, false, wr.status, wr.errTxt }
		if fi, err := os.Stat(cacheFilePath); err == nil && time.Since(fi.ModTime()) <= assetCacheTTL {
			p.mirrorCachedFile(target, cacheFilePath)
			return true, true, 200, ""
		}
		return false, false, 0, "in-flight warm did not produce a cache entry"
	case <-ctx.Done():
		return false, false, 0, "canceled while waiting for in-flight warm"
	}
}

func mapJSONID(path string) int64 {
	if m := reMapJSONPath.FindStringSubmatch(path); m != nil {
		if id, err := strconv.ParseInt(m[1], 10, 64); err == nil { return id }
	}
	return 0
}

func mapJSONNeighbours(body []byte) []int64 {
	m := reMapHeader.FindSubmatch(body[:min(len(body), 300)])
	if m == nil { return nil }
	var out []int64
	for _, g := range m[2:] {
		if id, err := strconv.ParseInt(string(g), 10, 64); err == nil && id > 0 && !slices.Contains(out, id) {
			out = append(out, id)
		}
	}
	return out
}

func mapTileURLs(body []byte, prefix string) []string {
	matches := reAtlasEntry.FindAllSubmatch(body, prefetchMaxTilesPerMap+1)
	out := make([]string, 0, len(matches))
	for _, m := range matches {
		if !bytes.Contains(m[2], []byte(`"sx"`)) { continue }
		dir, ext := "png", ".png"
		if bytes.Contains(m[2], []byte(`"jpg"`)) {
			dir, ext = "jpg", ".jpg"
		}
		out = append(out, prefix+"/gfx/world/"+dir+"/"+string(m[1])+ext)
		if len(out) == prefetchMaxTilesPerMap { break }
	}
	return out
}

func (p *ProxyServer) prefetchLog(format string, args ...any) {
	line := fmt.Sprintf(format, args...)
	if !p.quiet { log.Printf("%s", line) }
	if p.cacheDir == "" { return }
	p.prefetchLogMu.Lock()
	defer p.prefetchLogMu.Unlock()
	if p.prefetchLogFile == nil {
		f, err := os.OpenFile(filepath.Join(p.cacheDir, "prefetch.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil { return }
		p.prefetchLogFile = f
	}
	if _, err := p.prefetchLogFile.WriteString(time.Now().Format("2006-01-02 15:04:05") + " " + line + "\n"); err != nil {
		_ = p.prefetchLogFile.Close()
		p.prefetchLogFile = nil
	}
}

func (p *ProxyServer) requestLog(format string, args ...any) {
	if p.cacheDir == "" { return }
	logPath := filepath.Join(p.cacheDir, "requests.log")
	p.prefetchLogMu.Lock()
	defer p.prefetchLogMu.Unlock()
	if p.reqLogFile == nil {
		var size int64
		if fi, err := os.Stat(logPath); err == nil { size = fi.Size() }
		p.reqLogRotateAt = 16 << 20
		if size > 16<<20 {
			_ = os.Remove(logPath + ".1")
			if os.Rename(logPath, logPath+".1") == nil {
				size = 0
			} else {
				p.reqLogRotateAt = size + 4<<20
			}
		}
		f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil { return }
		p.reqLogFile, p.reqLogSize = f, size
	}
	n, err := p.reqLogFile.WriteString(time.Now().Format("15:04:05.000") + " " + fmt.Sprintf(format, args...) + "\n")
	if err != nil || p.reqLogSize+int64(n) > p.reqLogRotateAt {
		_ = p.reqLogFile.Close()
		p.reqLogFile = nil
		return
	}
	p.reqLogSize += int64(n)
}

func (p *ProxyServer) negCacheHit(target string) (int, bool) {
	p.negMu.Lock()
	defer p.negMu.Unlock()
	e, ok := p.negCache[target]
	if !ok { return 0, false }
	if time.Since(e.at) > prefetchNegativeTTL {
		delete(p.negCache, target)
		return 0, false
	}
	return e.status, true
}

func (p *ProxyServer) negCacheStore(target string, status int) {
	if status != http.StatusForbidden && status != http.StatusNotFound { return }
	now := time.Now()
	p.negMu.Lock()
	defer p.negMu.Unlock()
	if p.negCache == nil { p.negCache = make(map[string]negEntry) }
	if len(p.negCache) > 4096 {
		maps.DeleteFunc(p.negCache, func(_ string, v negEntry) bool { return now.Sub(v.at) > prefetchNegativeTTL })
		if len(p.negCache) > 4096 { clear(p.negCache) }
	}
	p.negCache[target] = negEntry{status: status, at: now}
}

func (p *ProxyServer) negCacheClear(target string) {
	p.negMu.Lock()
	delete(p.negCache, target)
	p.negMu.Unlock()
}

func (p *ProxyServer) scheduleMapPrefetch(target string, id int64) {
	if !p.prefetch || p.cacheDir == "" || id <= 0 { return }
	if _, bad := p.negCacheHit(target); bad { return }
	p.prefetchMu.Lock()
	if p.prefetchSeen == nil { p.prefetchSeen = make(map[int64]time.Time) }
	if last, ok := p.prefetchSeen[id]; ok && time.Since(last) < prefetchCooldown {
		p.prefetchMu.Unlock()
		return
	}
	if len(p.prefetchSeen) > 8192 {
		now := time.Now()
		maps.DeleteFunc(p.prefetchSeen, func(_ int64, v time.Time) bool { return now.Sub(v) > time.Hour })
	}
	p.prefetchSeen[id] = time.Now()
	p.prefetchMu.Unlock()

	p.prefetchOnce.Do(p.startPrefetchWorkers)
	select {
	case p.prefetchQ <- prefetchJob{target: target, id: id, depth: 0}:
	default:
	}
}

func (p *ProxyServer) startPrefetchWorkers() {
	p.prefetchQ = make(chan prefetchJob, prefetchQueueSize)
	for i := 0; i < max(p.prefetchWorkers, 1); i++ {
		go p.prefetchWorker()
	}
}

func (p *ProxyServer) prefetchWorker() {
	ctx := context.Background()
	for job := range p.prefetchQ {
		p.processPrefetchJob(ctx, job)
	}
}

func (p *ProxyServer) processPrefetchJob(ctx context.Context, job prefetchJob) {
	mapURL := job.target
	cacheFilePath := p.assetCachePath(mapURL)
	if _, err := os.Stat(cacheFilePath); err != nil {
		if ok, _, status, errText := p.warmAssetCacheDetail(ctx, mapURL); !ok {
			p.prefetchLog("[PREFETCH] map %d json fetch failed status=%d err=%q", job.id, status, errText)
			return
		}
	}
	body, err := readFileLimited(cacheFilePath, prefetchMapJSONReadMax)
	if err != nil || len(body) == 0 { return }
	prefix := strings.TrimSuffix(mapURL, "/maps/"+strconv.FormatInt(job.id, 10)+".json")
	neighbours := mapJSONNeighbours(body)
	newTiles, cachedTiles, totalTiles := 0, 0, 0
	if job.depth <= p.prefetchTilesDepth {
		for _, tileURL := range mapTileURLs(body, prefix) {
			totalTiles++
			if ok, already := p.warmAssetCache(ctx, tileURL); already {
				cachedTiles++
			} else if ok {
				newTiles++
			}
		}
	}
	p.prefetchLog("[PREFETCH] map %d d%d neighbours=%d tiles new=%d cached=%d total=%d",
		job.id, job.depth, len(neighbours), newTiles, cachedTiles, totalTiles)
	if job.depth >= p.prefetchRadius { return }
	for _, nid := range neighbours {
		p.prefetchMu.Lock()
		last, ok := p.prefetchSeen[nid]
		skip := ok && time.Since(last) < prefetchCooldown
		if !skip { p.prefetchSeen[nid] = time.Now() }
		p.prefetchMu.Unlock()
		if !skip {
			select {
			case p.prefetchQ <- prefetchJob{target: prefix + "/maps/" + strconv.FormatInt(nid, 10) + ".json", id: nid, depth: job.depth + 1}:
			default:
			}
		}
	}
}

func forEachIDRange(spec string, maxVal, maxSpan int64, strictHi bool, fn func(int64) bool) {
	for _, part := range strings.Split(spec, ",") {
		if part = strings.TrimSpace(part); part == "" { continue }
		var lo, hi int64
		if lStr, hStr, ok := strings.Cut(part, "-"); ok && lStr != "" {
			l, errL := strconv.ParseInt(strings.TrimSpace(lStr), 10, 64)
			h, errH := strconv.ParseInt(strings.TrimSpace(hStr), 10, 64)
			if errL != nil || (strictHi && errH != nil) { continue }
			lo, hi = l, h
		} else if v, err := strconv.ParseInt(part, 10, 64); err == nil {
			lo, hi = v, v
		}
		if lo < 1 { continue }
		hi = min(max(hi, lo), maxVal, lo+maxSpan)
		for id := lo; id <= hi; id++ {
			if !fn(id) { return }
		}
	}
}

func feedMapIDs(spec string, fn func(id int) bool) {
	forEachIDRange(spec, 2_000_000_000, 200_000, false, func(id int64) bool { return fn(int(id)) })
}

func (p *ProxyServer) precacheMaps(ctx context.Context, assetsURL, spec string, workers int) {
	if assetsURL == "" || spec == "" { return }
	workers = max(workers, 1)
	base := strings.TrimRight(assetsURL, "/")
	log.Printf("[PRECACHE] Warming maps from %s/maps/*.json (workers=%d)", base, workers)
	var fetched, cached, failed, total int64
	var eg errgroup.Group
	eg.SetLimit(workers)
	feedMapIDs(spec, func(id int) bool {
		if ctx.Err() != nil { return false }
		eg.Go(func() error {
			if ctx.Err() != nil { return nil }
			ok, already := p.warmAssetCache(ctx, base+"/maps/"+strconv.Itoa(id)+".json")
			n := atomic.AddInt64(&total, 1)
			switch {
			case already:
				atomic.AddInt64(&cached, 1)
			case ok:
				atomic.AddInt64(&fetched, 1)
			default:
				atomic.AddInt64(&failed, 1)
			}
			if !p.quiet && n%500 == 0 {
				log.Printf("[PRECACHE] %d processed (fetched=%d cached=%d failed=%d)",
					n, atomic.LoadInt64(&fetched), atomic.LoadInt64(&cached), atomic.LoadInt64(&failed))
			}
			return nil
		})
		return true
	})
	_ = eg.Wait()
	log.Printf("[PRECACHE] done: total=%d fetched=%d cached=%d failed=%d",
		atomic.LoadInt64(&total), atomic.LoadInt64(&fetched), atomic.LoadInt64(&cached), atomic.LoadInt64(&failed))
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-ctx.Done():
		return false
	}
}

func parseCrawlSeeds(spec string) []int64 {
	var out []int64
	forEachIDRange(spec, 1<<62, 4096, true, func(id int64) bool {
		out = append(out, id)
		return len(out) < 4096
	})
	return out
}

func (p *ProxyServer) runMapCrawler(ctx context.Context, assetsURL string, seeds []int64, maxMaps int, crawlRate float64, withTiles bool) {
	if p.cacheDir == "" {
		p.prefetchLog("[CRAWL] no -cache directory; skipping")
		return
	}
	base := strings.TrimRight(assetsURL, "/")
	if base == "" { return }
	if crawlRate <= 0 {
		crawlRate = 1
	} else if crawlRate > 50 {
		crawlRate = 50
	}
	limiter := rate.NewLimiter(rate.Limit(crawlRate), 1)
	p.prefetchLog("[CRAWL] start base=%s seeds=%d max=%d rate=%.1f/s tiles=%t", base, len(seeds), maxMaps, crawlRate, withTiles)

	visited := make(map[int64]struct{}, 8192)
	queue := slices.Clone(seeds)
	fetched, reused, failed := 0, 0, 0
	for len(queue) > 0 && ctx.Err() == nil {
		if maxMaps > 0 && len(visited) >= maxMaps { break }
		id := queue[0]
		queue = queue[1:]
		if id <= 0 { continue }
		if _, dup := visited[id]; dup { continue }
		visited[id] = struct{}{}

		mapURL := base + "/maps/" + strconv.FormatInt(id, 10) + ".json"
		cacheFilePath := p.assetCachePath(mapURL)
		if _, err := os.Stat(cacheFilePath); err != nil {
			if limiter.Wait(ctx) != nil { break }
			if ok, _, status, errText := p.warmAssetCacheDetail(ctx, mapURL); !ok {
				failed++
				p.prefetchLog("[CRAWL] map %d json failed status=%d err=%q", id, status, errText)
				continue
			}
			fetched++
		} else {
			reused++
			p.mirrorCachedFile(mapURL, cacheFilePath)
		}

		body, err := readFileLimited(cacheFilePath, prefetchMapJSONReadMax)
		if err != nil || len(body) == 0 { continue }
		for _, nid := range mapJSONNeighbours(body) {
			if _, dup := visited[nid]; !dup { queue = append(queue, nid) }
		}
		if withTiles {
			prefix := strings.TrimSuffix(mapURL, "/maps/"+strconv.FormatInt(id, 10)+".json")
			for _, tileURL := range mapTileURLs(body, prefix) {
				if ctx.Err() != nil { break }
				if _, err := os.Stat(p.assetCachePath(tileURL)); err == nil { continue }
				if limiter.Wait(ctx) != nil { break }
				p.warmAssetCache(ctx, tileURL)
			}
		}
		if n := len(visited); n%100 == 0 {
			p.prefetchLog("[CRAWL] visited=%d queued=%d current=%d fetched=%d reused=%d failed=%d",
				n, len(queue), id, fetched, reused, failed)
		}
	}
	p.prefetchLog("[CRAWL] done visited=%d queued=%d fetched=%d reused=%d failed=%d", len(visited), len(queue), fetched, reused, failed)
}

func (p *ProxyServer) handleHTTP(w http.ResponseWriter, r *http.Request) {
	targetURL := r.URL
	targetURL.Scheme = cmp.Or(targetURL.Scheme, "http")
	targetURL.Host = cmp.Or(targetURL.Host, r.Host)
	if targetURL.Host == "" {
		http.Error(w, "Bad Request: Missing host", http.StatusBadRequest)
		return
	}
	if (strings.HasSuffix(targetURL.Path, "/__disguise.js") || strings.HasSuffix(targetURL.Path, "/mobile-disguise.js")) && p.getDisguiseContent() != "" {
		h := w.Header()
		h.Set("Content-Type", "application/javascript; charset=utf-8")
		h.Set("Access-Control-Allow-Origin", "*")
		h.Set("Cache-Control", "no-cache")
		h.Set("Content-Length", strconv.Itoa(len(p.disguiseBytes)))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(p.disguiseBytes)
		return
	}
	targetStr := targetURL.String()
	var cacheFilePath string
	if p.cacheDir != "" && r.Method == http.MethodGet && isCacheAssetURL(targetStr) && !isVolatileCacheURL(targetStr) {
		cacheFilePath = p.assetCachePath(targetStr)
		if p.tryServeCached(w, r, targetStr, targetURL.Path, cacheFilePath) { return }
	}
	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetStr, r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	copyHeaderFiltered(outReq.Header, r.Header, hopReqHeaders)
	p.sanitizeRequestHeaders(outReq.Header)
	p.applyHotlinkHeaders(outReq.Header, targetURL.Host)
	if ae := outReq.Header.Get("Accept-Encoding"); ae != "" {
		if filtered := filterAcceptEncoding(ae); filtered != ae {
			outReq.Header.Set("Accept-Encoding", filtered)
		}
	}
	resp, err := p.client.Do(outReq)
	if err != nil {
		http.Error(w, "Proxy Error: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	wh := w.Header()
	copyHeaderFiltered(wh, resp.Header, hopRespHeaders)
	wh.Set("Access-Control-Allow-Origin", "*")
	if resp.StatusCode == http.StatusOK && cacheFilePath != "" { setLongAssetCacheHeaders(wh) }
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
				bodyBytes = p.injectDisguise(bodyBytes)
			}
			wh.Set("Content-Length", strconv.Itoa(len(bodyBytes)))
			w.WriteHeader(resp.StatusCode)
			_, _ = w.Write(bodyBytes)
			return
		}
	}
	w.WriteHeader(resp.StatusCode)
	if cacheFilePath != "" && resp.StatusCode == http.StatusOK && !isGzip {
		expLen := resp.ContentLength
		if resp.Header.Get("Content-Encoding") != "" { expLen = -1 }
		_ = p.storeCacheEntry(targetStr, targetURL.Path, cacheFilePath, ct, bodyReader, expLen, w)
		return
	}
	buf := bufPool.Get().([]byte)
	_, _ = io.CopyBuffer(w, bodyReader, buf)
	bufPool.Put(buf)
}

func configArgValue(args []string, name string) string {
	for i, a := range args {
		if (a == "-"+name || a == "--"+name) && i+1 < len(args) { return args[i+1] }
		for _, pfx := range []string{"-" + name + "=", "--" + name + "="} {
			if v, ok := strings.CutPrefix(a, pfx); ok { return v }
		}
	}
	return ""
}

func loadIniConfig(path string) map[string]string {
	out := map[string]string{}
	if path == "" { return out }
	raw, err := os.ReadFile(path)
	if err != nil {
		log.Printf("[CONFIG] %s not readable (%v); using flags only", path, err)
		return out
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.ContainsAny(line[:1], "#;[") { continue }
		if k, v, ok := strings.Cut(line, "="); ok && strings.TrimSpace(k) != "" {
			out[strings.ToLower(strings.TrimSpace(k))] = strings.TrimSpace(v)
		}
	}
	return out
}

func main() {
	cfgPath := configArgValue(os.Args[1:], "config")
	cfg := loadIniConfig(cfgPath)
	defStr := func(k, fb string) string { return cmp.Or(cfg[k], fb) }
	defInt := func(k string, fb int) int {
		if n, err := strconv.Atoi(cfg[k]); err == nil && cfg[k] != "" { return n }
		return fb
	}
	defBool := func(k string, fb bool) bool {
		switch strings.ToLower(cfg[k]) {
		case "1", "true", "yes", "on":
			return true
		case "0", "false", "no", "off":
			return false
		}
		return fb
	}

	port := flag.Int("port", defInt("port", 8880), "Local listening port for HTTP/CONNECT proxy")
	host := flag.String("host", defStr("host", "127.0.0.1"), "Host address to bind the proxy server to")
	upstream := flag.String("upstream", defStr("upstream", ""), "Upstream proxy URL, or a comma-separated chain of them (http://ip:port or socks5://ip:port)")
	cdnProxy := flag.String("cdn-upstream", defStr("cdn_upstream", ""), "Separate upstream for asset downloads, so they do not queue behind the game socket")
	exitProxy := flag.String("exit-proxy", defStr("exit_proxy", ""), "Fixed exit proxy reached through -upstream hops. Keeps one exit IP across hop failures.")
	dnsServer := flag.String("dns", defStr("dns", ""), "Custom DNS server to query (optional, default: system DNS)")
	cacheDir := flag.String("cache", defStr("cache", "proxy_cache"), "Directory to cache static game assets (optional)")
	quiet := flag.Bool("quiet", defBool("quiet", false), "Quiet mode (suppress request logs)")
	logPath := flag.String("log", defStr("log", ""), "Explicit log file (default: <cache>/proxy.log when -quiet is set)")
	allowDirect := flag.Bool("allow-direct", defBool("allow_direct", false), "Fall back to a DIRECT connection when the upstream dies (EXPOSES THE REAL IP; off by default)")
	configFlag := flag.String("config", cfgPath, "INI file with defaults for every flag (key=value, sections ignored)")
	token := flag.String("token", "", "Optional local bearer authentication token")
	serial := flag.String("serial", "", "Hardware serial number to interpolate into disguise scripts")
	uuid := flag.String("uuid", "", "Device UUID to interpolate into disguise scripts")
	model := flag.String("model", "", "Device model name to interpolate into disguise scripts (e.g. SM-A515F)")
	manufacturer := flag.String("manufacturer", "", "Device manufacturer to interpolate (e.g. samsung)")
	ua := flag.String("ua", "", "Device User-Agent string to interpolate into disguise scripts")
	glVendor := flag.String("gl-vendor", "", "OpenGL vendor string to interpolate (e.g. Qualcomm)")
	glRenderer := flag.String("gl-renderer", "", "OpenGL renderer string to interpolate (e.g. Adreno (TM) 620)")
	maxTextureSize := flag.String("texture-size", "", "Maximum GL texture size to interpolate (e.g. 16384)")
	referer := flag.String("referer", defStr("referer", "https://www.dofus-touch.com/"), "Referer/Origin sent on CDN asset fetches (hotlink protection bypass)")
	assetsURL := flag.String("assets-url", "", "Assets base URL for map precaching, e.g. https://dofustouch.cdn.ankama.com/assets/<version>")
	precacheSpec := flag.String("precache-maps", "", "Map IDs to precache into -cache, e.g. \"1-20000\" or \"7442,10354\" (requires -assets-url)")
	precacheWorkers := flag.Int("precache-workers", 4, "Parallel workers for -precache-maps")
	prefetchNeighbours := flag.Bool("prefetch-neighbours", true, "Warm neighbour map JSONs and tiles into -cache whenever a map is loaded")
	prefetchRadius := flag.Int("prefetch-radius", 2, "Map graph depth warmed for JSON (1=direct neighbours, 2=neighbours of neighbours)")
	prefetchTilesDepth := flag.Int("prefetch-tiles-depth", 1, "Graph depth at which map tile images (gfx/world/*) are warmed")
	prefetchWorkers := flag.Int("prefetch-workers", 3, "Parallel workers for neighbour prefetch")
	crawlSpec := flag.String("crawl", "", "Graph crawl seeds: BFS the map neighbour graph from these map IDs, warming JSON (+tiles with -crawl-tiles) into -cache (requires -assets-url)")
	crawlMax := flag.Int("crawl-max", 0, "Stop the crawl after this many maps (0 = unlimited)")
	crawlRate := flag.Float64("crawl-rate", 3, "Max upstream CDN requests per second during the crawl")
	crawlTiles := flag.Bool("crawl-tiles", false, "Also warm tile images for every crawled map")
	modsDir := flag.String("mods-dir", defStr("mods_dir", ""), "Directory containing a mods rules.json (default: <cache>/mods)")
	loginHostFlag := flag.String("login-host", "", "Host that the client's virtual origin (dofus.local) is mapped to (default: the production login proxy)")
	flag.Parse()
	loginHost = cmp.Or(*loginHostFlag, loginHost)

	log.SetFlags(log.Ldate | log.Ltime)
	if *configFlag != "" { log.Printf("[CONFIG] loaded %s", *configFlag) }
	target := *logPath
	if target == "" && *quiet && *cacheDir != "" {
		target = filepath.Join(*cacheDir, "proxy.log")
	}
	if target != "" {
		if dir := filepath.Dir(target); dir != "" { _ = os.MkdirAll(dir, 0o755) }
		if f, err := os.OpenFile(target, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644); err == nil {
			log.SetOutput(f)
			log.Printf("[CONFIG] logging to %s", target)
		} else {
			log.Printf("[CONFIG] cannot open %s: %v", target, err)
		}
	}
	resolver := NewFastDNSResolver(*dnsServer, 10*time.Minute)

	if *upstream != "" && strings.TrimSpace(*exitProxy) == "" {
		firstUpstream, _, _ := strings.Cut(*upstream, ",")
		if u, err := url.Parse(strings.TrimSpace(firstUpstream)); err == nil {
			if ok, dialErr := upstreamReachable(u, 1500*time.Millisecond); ok {
				upstreamHealthy.Store(true)
				log.Printf("[UPSTREAM] %s reachable", *upstream)
			} else {
				upstreamHealthy.Store(false)
				log.Printf("[UPSTREAM] %s is NOT reachable (%v).", *upstream, dialErr)
				log.Printf("[UPSTREAM] Listening anyway and failing closed: every dial is refused until WARP is back, so no request can go direct and the real IP stays hidden.")
				log.Printf("[UPSTREAM] WARP drops its tunnel on a network change (wifi/ethernet) and usually returns within a minute. Fix now: warp-cli mode proxy && warp-cli connect")
				go watchUpstream(u, *upstream)
			}
		}
	}
	proxy := NewProxyServer(resolver, *upstream, *cacheDir, *quiet, *port, *token, *serial, *uuid, *model, *manufacturer, *ua, *glVendor, *glRenderer, *maxTextureSize, *referer)
	proxy.hopIdxPath = hopIndexPath(*cacheDir)
	proxy.loadHopIndex()
	go func() {
		for {
			time.Sleep(10 * time.Second)
			proxy.saveHopIndex()
			if proxy.ticks++; proxy.ticks%6 == 0 {
				h, m := atomic.LoadUint64(&proxy.cacheHits), atomic.LoadUint64(&proxy.cacheMisses)
				if h+m > 0 {
					log.Printf("[CACHE] %d hits, %d misses (%.0f%% served from disk)", h, m, 100.0*float64(h)/float64(h+m))
				}
			}
		}
	}()
	defer proxy.saveHopIndex()
	debug.SetMemoryLimit(384 << 20)

	if cdn := strings.TrimSpace(*cdnProxy); cdn != "" {
		if pool := parseUpstreamList(cdn); len(pool) > 0 {
			proxy.client = newAssetClient(pool, proxy.resolver)
			proxy.cdnUpstream = pool[0]
			if len(pool) == 1 {
				log.Printf("[UPSTREAM] asset downloads use their own egress: %s", pool[0].Redacted())
			} else {
				log.Printf("[UPSTREAM] asset downloads spread over %d egresses: %s ...", len(pool), pool[0].Redacted())
			}
		} else {
			log.Printf("[UPSTREAM] -cdn-upstream %q is not a usable proxy URL; ignoring it", cdn)
		}
	}
	if trimmed := strings.TrimSpace(*exitProxy); trimmed != "" {
		if proxy.exitURL = parseUpstreamProxy(trimmed); proxy.exitURL == nil {
			log.Printf("[UPSTREAM] -exit-proxy %q is not a usable proxy URL; ignoring it", trimmed)
		} else {
			proxy.validateExitAtStartup()
		}
	}
	proxy.allowDirect = *allowDirect
	if *allowDirect {
		log.Printf("[UPSTREAM] -allow-direct is ON: if the tunnel dies, traffic will go direct and Ankama will see this machine's real IP.")
	}
	proxy.prefetch = *prefetchNeighbours
	proxy.prefetchRadius = max(*prefetchRadius, 1)
	proxy.prefetchTilesDepth = max(*prefetchTilesDepth, 0)
	proxy.prefetchWorkers = min(max(*prefetchWorkers, 1), 16)
	if proxy.prefetch && proxy.cacheDir != "" {
		log.Printf("[PREFETCH] Neighbour prefetch enabled (radius=%d tiles-depth=%d workers=%d)",
			proxy.prefetchRadius, proxy.prefetchTilesDepth, proxy.prefetchWorkers)
	}

	runCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if *precacheSpec != "" {
		if *assetsURL == "" {
			log.Printf("[PRECACHE] -precache-maps set without -assets-url; skipping")
		} else {
			go proxy.precacheMaps(runCtx, *assetsURL, *precacheSpec, *precacheWorkers)
		}
	}
	if *modsDir == "" && *cacheDir != "" { *modsDir = filepath.Join(*cacheDir, "mods") }
	if proxy.modsDir = *modsDir; *modsDir != "" { proxy.loadModsIfChanged() }
	if *crawlSpec != "" {
		if *assetsURL == "" {
			log.Printf("[CRAWL] -crawl set without -assets-url; skipping")
		} else if seeds := parseCrawlSeeds(*crawlSpec); len(seeds) > 0 {
			go proxy.runMapCrawler(runCtx, *assetsURL, seeds, *crawlMax, *crawlRate, *crawlTiles)
		} else {
			log.Printf("[CRAWL] no valid seeds parsed from %q", *crawlSpec)
		}
	}

	server := &http.Server{
		Addr:              net.JoinHostPort(*host, strconv.Itoa(*port)),
		Handler:           proxy,
		ReadHeaderTimeout: 30 * time.Second,
		IdleTimeout:       300 * time.Second,
	}
	go func() {
		log.Printf("[READY] Listening on http://%s:%d (Model: %s) ...", *host, *port, *model)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[FATAL] Proxy server failed: %v", err)
		}
	}()
	<-runCtx.Done()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
}