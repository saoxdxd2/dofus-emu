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
	"runtime/debug"
	"regexp"
	"sort"
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
	// Logged once, the first time the upstream fails at runtime.
	upstreamFallbackOnce sync.Once
	// Logged once, the first time the virtual host is remapped.
	virtualHostRewriteOnce sync.Once
	// Where `dofus.local` is sent. Overridden by -login-host.
	loginHost = "dt-proxy-production-login.ankama-games.com"
	reHeadOpen   = regexp.MustCompile(`(?i)<head[^>]*>`)
	reHeadClose  = regexp.MustCompile(`(?i)</head>`)
	reHtmlOpen   = regexp.MustCompile(`(?i)<html[^>]*>`)
	reOsVer      = regexp.MustCompile(`Android\s+([0-9.]+)`)
	reBuildId    = regexp.MustCompile(`Build/([^\s;)]+)`)

	tunnelEstablished = []byte("HTTP/1.1 200 Connection Established\r\n\r\n")

	stripReqHeaders = []string{
		"Proxy-Connection", "Proxy-Authenticate", "Proxy-Authorization",
		"Via", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
		"Forwarded", "X-Real-IP", "Client-IP", "X-Requested-With",
	}
	// Canonicalized lookup set built once at init. Deleting by exact header
	// name is case-insensitive in net/http, and map lookups avoid the
	// per-header strings.ToLower allocation the old code paid on every
	// proxied request.
	hopReqHeaders = func() map[string]struct{} {
		m := make(map[string]struct{}, len(stripReqHeaders)+5)
		for _, k := range stripReqHeaders {
			m[http.CanonicalHeaderKey(k)] = struct{}{}
		}
		for _, k := range []string{"Connection", "Keep-Alive", "Te", "Trailers", "Transfer-Encoding", "Upgrade"} {
			m[http.CanonicalHeaderKey(k)] = struct{}{}
		}
		return m
	}()
	hopRespHeaders = func() map[string]struct{} {
		m := make(map[string]struct{}, 10)
		for _, k := range []string{
			"Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
			"Te", "Trailers", "Transfer-Encoding", "Upgrade",
			"Content-Security-Policy", "Content-Security-Policy-Report-Only",
		} {
			m[http.CanonicalHeaderKey(k)] = struct{}{}
		}
		return m
	}()
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
	inflight   map[string]*dnsLookupCall
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

type dnsLookupCall struct {
	wg  sync.WaitGroup
	ips []net.IP
	err error
}

func (r *FastDNSResolver) Lookup(ctx context.Context, host string) ([]net.IP, error) {
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	if ip := net.ParseIP(host); ip != nil {
		return []net.IP{ip}, nil
	}

	now := time.Now()
	// Fast path: fresh cache entry. Copy the slice header so callers can
	// never alias into the cached backing array's length changes.
	r.mu.RLock()
	entry, found := r.cache[host]
	c, inflight := r.inflight[host]
	r.mu.RUnlock()
	if found && now.Before(entry.ExpiresAt) {
		if len(entry.IPs) > 1 {
			// Round-robin across resolved addresses: spreads fresh
			// connections (TLS handshakes, rate limits) across every
			// A record instead of hammering ips[0].
			r.mu.Lock()
			idx := r.rr % uint64(len(entry.IPs))
			r.rr++
			r.mu.Unlock()
			return []net.IP{entry.IPs[idx]}, nil
		}
		out := make([]net.IP, len(entry.IPs))
		copy(out, entry.IPs)
		return out, nil
	}

	// Coalesce concurrent misses on the same host into a single upstream
	// query. Under connection storms this turns an N-way DNS stampede into
	// one lookup plus N cheap waiters.
	if inflight {
		c.wg.Wait()
		if len(c.ips) > 1 {
			r.mu.Lock()
			idx := r.rr % uint64(len(c.ips))
			r.rr++
			r.mu.Unlock()
			return []net.IP{c.ips[idx]}, nil
		}
		return c.ips, c.err
	}

	// Register as the leader for this host before doing any I/O so that
	// any concurrent caller waits on our result instead of issuing its
	// own upstream query.
	call := new(dnsLookupCall)
	call.wg.Add(1)
	r.mu.Lock()
	if prev, ok := r.inflight[host]; ok {
		// Lost the race to register; wait on the existing leader.
		r.mu.Unlock()
		prev.wg.Wait()
		if len(prev.ips) > 1 {
			r.mu.Lock()
			idx := r.rr % uint64(len(prev.ips))
			r.rr++
			r.mu.Unlock()
			return []net.IP{prev.ips[idx]}, nil
		}
		return prev.ips, prev.err
	}
	r.inflight[host] = call
	r.mu.Unlock()

	ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
	if err != nil && r.netResolve != net.DefaultResolver {
		ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
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
	if err == nil {
		r.cache[host] = DNSCacheEntry{IPs: ips, ExpiresAt: now.Add(r.ttl)}
	}
	delete(r.inflight, host)
	call.ips, call.err = ips, err
	r.mu.Unlock()
	return ips, err
}

// stripWebViewTokens removes the "; wv" flag and the "Version/X" token from a
// User-Agent. This mirrors the regex `(?i);\s*wv\b|Version/[0-9.]+\s*` but
// avoids running a regexp on every proxied request (was ~6.5us + 5 allocs).
// Verified equivalent by FuzzStripWebViewTokensEquivalence.
func stripWebViewTokens(ua string) string {
	var b strings.Builder
	b.Grow(len(ua))
	i := 0
	for i < len(ua) {
		// Match "; wv" as a whole word (case-insensitive), allowing
		// whitespace between the ';' and 'wv'.
		if ua[i] == ';' {
			j := i + 1
			for j < len(ua) && (ua[j] == ' ' || ua[j] == '\t') {
				j++
			}
			if j+1 < len(ua) && (ua[j]|0x20) == 'w' && (ua[j+1]|0x20) == 'v' &&
				!isTokenCharUA(ua, j+2) {
				i = j + 2
				continue
			}
		}
		// Match "Version/<digits and dots>" followed by whitespace, and
		// consume the whole thing (token + trailing whitespace) exactly
		// like the regex `Version/[0-9.]+\s*` deleting its match did.
		if (ua[i]|0x20) == 'v' && len(ua)-i >= 8 &&
			strings.EqualFold(ua[i:i+8], "Version/") {
			j := i + 8
			for j < len(ua) && (ua[j] == '.' || (ua[j] >= '0' && ua[j] <= '9')) {
				j++
			}
			if j > i+8 {
				k := j
				for k < len(ua) && (ua[k] == ' ' || ua[k] == '\t' || ua[k] == '\n' || ua[k] == '\r' || ua[k] == '\f' || ua[k] == '\v') {
					k++
				}
				i = k // delete the whole "Version/X.Y<ws>" token
				continue
			}
		}
		b.WriteByte(ua[i])
		i++
	}
	return b.String()
}

func isTokenCharUA(s string, i int) bool {
	if i >= len(s) {
		return false
	}
	c := s[i]
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-'
}

// isCacheAssetURL mirrors the regex
// `(?i)\.(?:png|jpe?g|webp|mp3|ogg|wasm|zip|pak|dat|json)$|/assets/`
// with a single linear scan (was ~11us of regex backtracking per request).
// Verified equivalent by TestIsCacheAssetURLEquivalence.
func isCacheAssetURL(u string) bool {
	if strings.Contains(u, "/assets/") {
		return true
	}
	// Find the last '.' and make sure nothing but the extension follows.
	dot := strings.LastIndexByte(u, '.')
	if dot < 0 {
		return false
	}
	ext := u[dot+1:]
	switch len(ext) {
	case 3:
		return asciiEqualFold(ext, "png") || asciiEqualFold(ext, "jpg") ||
			asciiEqualFold(ext, "mp3") || asciiEqualFold(ext, "ogg") ||
			asciiEqualFold(ext, "zip") || asciiEqualFold(ext, "pak") ||
			asciiEqualFold(ext, "dat")
	case 4:
		return asciiEqualFold(ext, "jpeg") || asciiEqualFold(ext, "webp") ||
			asciiEqualFold(ext, "wasm") || asciiEqualFold(ext, "json")
	}
	return false
}

// asciiEqualFold reports whether s equals the ASCII literal t ignoring case.
// t must be a compile-time constant so the compiler can unroll it.
func asciiEqualFold(s, t string) bool {
	if len(s) != len(t) {
		return false
	}
	for i := 0; i < len(s); i++ {
		if (s[i] | 0x20) != (t[i] | 0x20) {
			return false
		}
	}
	return true
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

// upstreamHealthy mirrors whether the last probe of the SOCKS upstream succeeded.
// It exists so a dial can tell "the tunnel is up, this destination failed" from
// "the tunnel is down, do not even try" and log accordingly.
var upstreamHealthy atomic.Bool

// upstreamReachable probes the upstream's TCP port. It says nothing about whether
// the tunnel can carry traffic -- only that WARP's listener exists.
func upstreamReachable(u *url.URL, timeout time.Duration) (bool, error) {
	conn, err := net.DialTimeout("tcp", ensureHostPort(u.Host, "1080"), timeout)
	if err != nil {
		return false, err
	}
	conn.Close()
	return true, nil
}

// watchUpstream polls the upstream after a failed startup probe and logs the
// transitions, so proxy.log records "WARP came back at HH:MM:SS" instead of
// leaving a reader to guess whether an outage ended.
func watchUpstream(u *url.URL, raw string) {
	for {
		time.Sleep(5 * time.Second)
		ok, err := upstreamReachable(u, 1500*time.Millisecond)
		if ok && !upstreamHealthy.Swap(true) {
			log.Printf("[UPSTREAM] %s is reachable again; dials resume", raw)
		} else if !ok && upstreamHealthy.Swap(false) {
			log.Printf("[UPSTREAM] %s went away (%v); failing closed until it returns", raw, err)
		}
	}
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
	// Fixed-size ring buffer: the old append()+re-slice approach
	// re-allocated (and copied) the whole history on every overflow past 32
	// entries. A ring writes in place with zero allocations.
	rttHistory  [32]float64
	rttCount    int
	rttPos      int
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
	if t.rttCount > 0 {
		sum := 0.0
		// rttPos always points one past the newest sample; walk
		// backwards so the average covers the same "last N samples"
		// window the old append+re-slice logic did.
		for i := 1; i <= t.rttCount; i++ {
			sum += t.rttHistory[(t.rttPos-i+len(t.rttHistory))%len(t.rttHistory)]
		}
		avg = sum / float64(t.rttCount)
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
	// Every configured upstream, primary first. A single upstream means one dead proxy costs the
	// whole session: the relay dials through it, the dial fails, and the client is told the
	// server is unavailable until the supervisor notices and rebuilds. Trying the next candidate
	// on the same dial turns that into a retry instead of an outage.
	upstreams []*url.URL
	// The upstream that last carried a connection, tried first on every later dial.
	//
	// The chain is a fallback list, not a rotation. Picking a different exit per connection
	// would show the server several source addresses inside one session, which is exactly what
	// it treats as a hijacked account. One exit at a time, changed only when it fails.
	upstreamMu sync.Mutex
	lastGood   *url.URL
	// The fixed exit: the one address Ankama is allowed to see. Reached through any hop in
	// upstreams, so a hop can die without the session's identity changing.
	exitURL *url.URL
	// When to stop trusting that exit.
	//
	// An exit that no hop can reach must not be retried on every request: the page issues
	// hundreds of them, and each one paid the full timeout before falling back, which is a black
	// screen with the traffic trickling in behind it. After one failure the exit is skipped for
	// exitCooldown and the plain chain answers immediately.
	exitMu           sync.Mutex
	exitBlockedUntil time.Time

	// Which hops can actually reach the exit, measured in the background.
	//
	// Racing blind was the reason two thirds of connections failed: a hop has to accept
	// CONNECT exitHost:exitPort, and most public proxies only allow 443, so a random hop usually
	// cannot reach the exit at all. Testing that is one handshake, so it belongs in an index
	// that is refreshed continuously rather than paid for on every dial.
	hopMu  sync.Mutex
	hopIdx map[string]hopProbe
	// Where the index lives between runs, and whether it has changed since the last save.
	hopIdxPath string
	hopDirty   bool

	destMu sync.Mutex
	dests  []string
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
	referer        string
	refererRoot    string
	disguiseOnce   sync.Once
	cachedDisguise string
	disguiseBytes  []byte
	client         *http.Client
	telemetry      ProxyTelemetry

	// allowDirect is the opt-out from the fail-closed rule. It is off unless the
	// host passes -allow-direct (network.ini fail_closed=0). When on, a dead
	// upstream falls back to a direct dial instead of refusing the request --
	// which means Ankama sees the real IP. That is the user's call, not ours.
	allowDirect bool

	// Neighbour map prefetch (see scheduleMapPrefetch): when a map JSON is
	// served, the surrounding maps' JSONs and tiles are warmed into the same
	// on-disk cache so walking around never waits on the CDN.
	prefetch           bool
	prefetchRadius     int
	prefetchTilesDepth int
	prefetchWorkers    int
	prefetchOnce       sync.Once
	prefetchQ          chan prefetchJob
	prefetchMu         sync.Mutex
	prefetchSeen       map[int64]time.Time
	prefetchLogMu      sync.Mutex

	// Persistent append handles for the diagnostic logs. The old code did an
	// open+write+close (plus a Stat for rotation) per line, which turned map
	// load bursts (hundreds of cache hits) into thousands of syscalls and
	// serialized every request behind disk I/O. Handles stay open; requests.log
	// rotates at 16 MB by in-memory size instead of stat-per-line.
	reqLogFile      *os.File
	reqLogSize      int64
	reqLogRotateAt  int64
	prefetchLogFile *os.File

	// Asset mirror (see mirrorCachedFile): every cached asset is hard-linked
	// into <cacheDir>/files/<host>/<path>. WebView2 maps that tree to
	// https://assets.local/ so the client's wizAssets layer can load assets
	// straight from local files (no relay round-trip, no connection limits).
	mirrorMu    sync.Mutex
	mirrorIndex map[string]struct{}
	mirrorBuilt bool

	// In-flight de-duplication for warmAssetCacheDetail so the client and the
	// prefetcher never download the same asset from the CDN twice at once.
	warmMu       sync.Mutex
	warmInflight map[string]chan struct{}

	// Negative cache for deterministically denied assets (403/404): prewarm
	// and precache stop re-requesting targets that cannot succeed, and the
	// relay can answer them instantly without touching the pool.
	negMu    sync.Mutex
	negCache map[string]negEntry

	// Client-request priority: count of live client relay requests currently
	// doing upstream I/O. Prefetch workers yield while this is > 0 so the
	// game's loads never wait behind speculative neighbour traffic.
	clientInflight int64

	// Mods pipeline (see applyModRules): rules.json inside modsDir rewrites,
	// blocks or patches CDN responses on the fly. Rules hot-reload when the
	// file changes; the unpatched originals stay in the cache untouched.
	modsDir      string
	modsMu       sync.Mutex
	modsRules    []modRule
	modsRes      []*regexp.Regexp
	modsChecked  time.Time
	modsRulesMod time.Time
}

func NewProxyServer(resolver *FastDNSResolver, upstream, cacheDir string, quiet bool, port int, authToken, serial, uuid, model, manufacturer, ua, glVendor, glRenderer, maxTextureSize, referer string) *ProxyServer {
	if model == "" {
		model = "SM-A515F"
	}
	if manufacturer == "" {
		manufacturer = "samsung"
	}
	if ua == "" {
		ua = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36"
	}
	// Referer "https://www.dofus-touch.com/" is the origin the official web
	// client itself declares (config.webAuth.referer) and is what Ankama's CDN
	// hotlink checks accept for asset fetches.
	if referer == "" {
		referer = "https://www.dofus-touch.com/"
	}
	refererRoot := referer
	if u, err := url.Parse(referer); err == nil && u.Scheme != "" && u.Host != "" {
		refererRoot = u.Scheme + "://" + u.Host
	}
	osVer, buildId := "10", "QP1A.190711.020"
	if m := reOsVer.FindStringSubmatch(ua); len(m) > 1 {
		osVer = m[1]
	}
	if m := reBuildId.FindStringSubmatch(ua); len(m) > 1 {
		buildId = m[1]
	}

	// The upstream flag may be a comma-separated chain. Parse it once: the primary has to be a
	// real URL, not the parse of the whole string. Parsing the list as a single URL produced a
	// nonsense primary ("http://%2F%2F61.130.176.21:xxxxx@127.0.0.1:1,http") whose Redacted()
	// then appeared in the failover log and in every message about the primary.
	upstreamChain := parseUpstreamList(upstream)
	var upstreamPrimary *url.URL
	if len(upstreamChain) > 0 {
		upstreamPrimary = upstreamChain[0]
	}

	p := &ProxyServer{
		hopIdx: make(map[string]hopProbe),
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
	// hopReqHeaders already contains every entry of stripReqHeaders (plus the
	// hop-by-hop protocol headers), keyed by canonical MIME header name. Del
	// is case-insensitive, so this removes exactly the same set the old
	// per-name loop did, without the repeated canonicalization work.
	for k := range hopReqHeaders {
		h.Del(k)
	}

	curUA := h.Get("User-Agent")
	if strings.HasPrefix(curUA, "Dalvik/") {
		h.Set("User-Agent", p.ua)
	} else if curUA == "" || uaLeaksDesktop(curUA) {
		h.Set("User-Agent", p.ua)
	} else {
		// Strip embedded WebView flags (; wv and Version/X.X) that cause
		// Google to block OAuth with 'disallowed_useragent'.
		cleanUA := strings.TrimSpace(stripWebViewTokens(curUA))
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

// uaLeaksDesktop replaces the old `(?i)x86|sdk|emulator|goldfish|ranchu|mozilla/`
// regex with six ASCII substring checks. Every alternative in that regex is a
// plain substring, so Contains is exactly equivalent but avoids running the
// regex engine on every proxied request (was a full scan per UA).
func uaLeaksDesktop(ua string) bool {
	lower := asciiLower(ua)
	return strings.Contains(lower, "mozilla/") ||
		strings.Contains(lower, "x86") ||
		strings.Contains(lower, "sdk") ||
		strings.Contains(lower, "emulator") ||
		strings.Contains(lower, "goldfish") ||
		strings.Contains(lower, "ranchu")
}

// copyHeaderFiltered copies every header from src into dst except those in
// skip (keyed by canonical MIME header name). Canonical form matches the key
// net/http itself stores, so the membership test is a plain map lookup with
// no per-header strings.ToLower allocation.
func copyHeaderFiltered(dst, src http.Header, skip map[string]struct{}) {
	for k, vv := range src {
		if _, drop := skip[k]; !drop {
			dst[k] = append([]string(nil), vv...)
		}
	}
}

// filterAcceptEncoding removes "br" entries from an Accept-Encoding header
// without allocating intermediate slices for the common no-br case.
func filterAcceptEncoding(ae string) string {
	// Fast path: no "br" token and already in canonical form (", "-joined,
	// no empty entries) - return the input unchanged, zero allocations.
	if !hasBrToken(ae) && ae == joinAcceptEncodingTokens(ae) {
		return ae
	}
	var b strings.Builder
	b.Grow(len(ae))
	start := 0
	for i := 0; i <= len(ae); i++ {
		if i == len(ae) || ae[i] == ',' {
			token := strings.TrimSpace(ae[start:i])
			if token != "" {
				lower := asciiLower(token)
				if lower != "br" && !strings.HasPrefix(lower, "br;") && !strings.HasPrefix(lower, "br ") {
					if b.Len() > 0 {
						b.WriteString(", ")
					}
					b.WriteString(token)
				}
			}
			start = i + 1
		}
	}
	if b.Len() == 0 {
		return "gzip, deflate"
	}
	return b.String()
}

// joinAcceptEncodingTokens rebuilds the canonical ", "-joined token list so
// the fast path can detect inputs that are already normalized (no rewrite
// needed) versus ones the legacy code would have rewritten.
func joinAcceptEncodingTokens(ae string) string {
	var b strings.Builder
	b.Grow(len(ae))
	start := 0
	for i := 0; i <= len(ae); i++ {
		if i == len(ae) || ae[i] == ',' {
			token := strings.TrimSpace(ae[start:i])
			if token != "" {
				if b.Len() > 0 {
					b.WriteString(", ")
				}
				b.WriteString(token)
			}
			start = i + 1
		}
	}
	if b.Len() == 0 {
		return "gzip, deflate"
	}
	return b.String()
}

func hasBrToken(ae string) bool {
	start := 0
	for i := 0; i <= len(ae); i++ {
		if i == len(ae) || ae[i] == ',' {
			token := strings.TrimSpace(ae[start:i])
			if token != "" {
				lower := asciiLower(token)
				if lower == "br" || strings.HasPrefix(lower, "br;") || strings.HasPrefix(lower, "br ") {
					return true
				}
			}
			start = i + 1
		}
	}
	return false
}

func asciiLower(s string) string {
	hasUpper := false
	for i := 0; i < len(s); i++ {
		if s[i] >= 'A' && s[i] <= 'Z' {
			hasUpper = true
			break
		}
	}
	if !hasUpper {
		return s
	}
	b := []byte(s)
	for i := 0; i < len(b); i++ {
		if b[i] >= 'A' && b[i] <= 'Z' {
			b[i] += 'a' - 'A'
		}
	}
	return string(b)
}

// parseUpstreamList splits a comma-separated upstream value into usable URLs.
//
// The supervisor knows several candidates that passed its gate; handing them all to the relay
// lets it fail over per connection instead of per process.
func parseUpstreamList(raw string) []*url.URL {
	var out []*url.URL
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if u := parseUpstreamProxy(part); u != nil {
			out = append(out, u)
		}
	}
	return out
}

// readConnectResponse reads a CONNECT reply without reading ahead.
//
// http.ReadResponse goes through a bufio.Reader, which pulls whatever is available — and when the
// connection is a tunnel that is about to carry a second handshake, the bytes it pulled past the
// header belong to that handshake and are gone. That is a silent "unexpected EOF" a few lines
// later, and it is why chaining failed two times in six while the same proxies worked alone.
// One byte at a time is slow in principle and irrelevant here: it is a handshake header.
func readConnectResponse(conn net.Conn) error {
	var line []byte
	buf := make([]byte, 1)
	header := make([]byte, 0, 256)
	for {
		if _, err := conn.Read(buf); err != nil {
			return err
		}
		header = append(header, buf[0])
		if buf[0] == '\n' {
			trimmed := strings.TrimRight(string(line), "\r")
			if trimmed == "" {
				break
			}
			if strings.HasPrefix(trimmed, "HTTP/") {
				fields := strings.Fields(trimmed)
				if len(fields) >= 2 && fields[1] != "200" {
					return fmt.Errorf("proxy answered %s", trimmed)
				}
			}
			line = line[:0]
			continue
		}
		if buf[0] != '\r' {
			line = append(line, buf[0])
		}
		if len(header) > 8192 {
			return errors.New("CONNECT reply too long")
		}
	}
	return nil
}

// connectThrough performs a proxy handshake on a connection that is already open, so the same
// socket can be relayed a second time. This is what makes a fixed exit possible: the hop carries
// us to the exit proxy, and the exit proxy carries us to the target.
func connectThrough(conn net.Conn, scheme, host string, port int, userinfo, ua string) error {
	// A handshake needs a deadline. Without one a slow proxy blocks this goroutine forever, and
	// because the race returns on the first success, "forever" means the whole dial never
	// finishes — the client hangs instead of failing over. The deadline is cleared on success so
	// the tunnel itself is not limited by it.
	if err := conn.SetDeadline(time.Now().Add(6 * time.Second)); err != nil {
		return err
	}
	defer func() { _ = conn.SetDeadline(time.Time{}) }()

	if strings.HasPrefix(strings.ToLower(scheme), "socks5") {
		var user, pass string
		if userinfo != "" {
			user, pass, _ = strings.Cut(userinfo, ":")
		}
		greet := []byte{0x05, 0x01, 0x00}
		if user != "" {
			greet = []byte{0x05, 0x02, 0x00, 0x02}
		}
		if _, err := conn.Write(greet); err != nil {
			return err
		}
		var buf [4]byte
		if _, err := io.ReadFull(conn, buf[:2]); err != nil || buf[0] != 0x05 {
			return errors.New("invalid SOCKS5 greeting")
		}
		if buf[1] == 0x02 {
			if len(user) > 255 || len(pass) > 255 {
				return errors.New("SOCKS5 credentials too long")
			}
			req := append([]byte{0x01, byte(len(user))}, user...)
			req = append(req, byte(len(pass)))
			req = append(req, pass...)
			if _, err := conn.Write(req); err != nil {
				return err
			}
			if _, err := io.ReadFull(conn, buf[:2]); err != nil || buf[1] != 0x00 {
				return errors.New("SOCKS5 authentication rejected")
			}
		}
		name := []byte(host)
		if len(name) > 255 {
			return errors.New("host too long for SOCKS5")
		}
		req := append([]byte{0x05, 0x01, 0x00, 0x03, byte(len(name))}, name...)
		req = append(req, byte(port>>8), byte(port&0xFF))
		if _, err := conn.Write(req); err != nil {
			return err
		}
		if _, err := io.ReadFull(conn, buf[:4]); err != nil || buf[1] != 0x00 {
			return fmt.Errorf("SOCKS5 connect failed (code %d)", buf[1])
		}
		switch buf[3] {
		case 0x01:
			_, err := io.ReadFull(conn, make([]byte, 4+2))
			return err
		case 0x04:
			_, err := io.ReadFull(conn, make([]byte, 16+2))
			return err
		case 0x03:
			if _, err := io.ReadFull(conn, buf[:1]); err != nil {
				return err
			}
			_, err := io.ReadFull(conn, make([]byte, int(buf[0])+2))
			return err
		}
		return nil
	}

	dest := net.JoinHostPort(host, strconv.Itoa(port))
	auth := ""
	if userinfo != "" {
		auth = "Proxy-Authorization: Basic " +
			base64.StdEncoding.EncodeToString([]byte(userinfo)) + "\r\n"
	}
	req := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n%s\r\n",
		dest, dest, ua, auth)
	if _, err := io.WriteString(conn, req); err != nil {
		return err
	}
	return readConnectResponse(conn)
}

// dialViaExit opens the target through a fixed exit proxy, reaching that exit through any of the
// hop proxies.
//
// This is the difference between a session that survives and one that does not. With a single
// upstream, every failover changes the address Ankama sees, and an address change mid-session is
// what it treats as a hijacked account. Here the exit is the identity and the hops are
// disposable: the hop can die, be replaced, be raced against others, and the far side still sees
// one address — the exit's.
//
// Measured before building it: dialling 182.18.83.37:7890 and asking it to reach
// 47.91.110.148:1000, then asking that one for api.ipify.org, returns 47.91.110.148 — exactly
// what the exit reports on its own.
// hopProbe is what the background index knows about one hop -> exit pair.
type hopProbe struct {
	ok      bool
	latency time.Duration
	checked time.Time
}

const (
	hopIndexEvery = 10 * time.Second
	exitCooldown  = 90 * time.Second
	warmTTL       = 15 * time.Second
	warmPerDest   = 2
	warmMaxDests  = 6
)

// rememberDest keeps the small set of destinations worth warming.
func (p *ProxyServer) rememberDest(dest string) {
	p.destMu.Lock()
	defer p.destMu.Unlock()
	for _, d := range p.dests {
		if d == dest {
			return
		}
	}
	if len(p.dests) >= warmMaxDests {
		p.dests = p.dests[1:]
	}
	p.dests = append(p.dests, dest)
}

// The index is built from real dials, never from background probes.
//
// It started as a goroutine that tested every hop every ten seconds, which is the obvious design
// and the wrong one here: a public proxy often allows a single concurrent connection, so a probe
// competes with the traffic it is supposed to be helping. Measured with the probe running, three
// requests took 25 s, 25 s and 7 s — against 5.5 s with no index at all. Every dial already
// reveals whether its hop worked and how fast, so the index is updated from that and costs
// nothing. First contact with a hop is a guess; every contact after it is informed.

// hopOrder puts hops that are known to reach the exit first, fastest first, then the rest.
func (p *ProxyServer) hopOrder() []*url.URL {
	exit := p.exitURL
	p.hopMu.Lock()
	defer p.hopMu.Unlock()

	var good, unknown, bad []*url.URL
	for _, u := range p.upstreams {
		if u == nil || (exit != nil && u.String() == exit.String()) {
			continue
		}
		pr, seen := p.hopIdx[u.String()]
		switch {
		case !seen:
			unknown = append(unknown, u)
		case pr.ok:
			good = append(good, u)
		default:
			bad = append(bad, u)
		}
	}
	sort.SliceStable(good, func(i, j int) bool {
		return p.hopIdx[good[i].String()].latency < p.hopIdx[good[j].String()].latency
	})
	// Bad hops are kept last rather than dropped: the index may be stale, and a hop that failed
	// ten seconds ago can be the only working one now.
	return append(append(good, unknown...), bad...)
}

// refillWarm tops the pre-opened tunnels back up for the destinations in use.

// openViaHop performs the two handshakes: hop -> exit, then exit -> target.
func (p *ProxyServer) openViaHop(hop, exit *url.URL, dest string, timeout time.Duration) (net.Conn, error) {
	exitPort := 1080
	if s := exit.Port(); s != "" {
		if n, err := strconv.Atoi(s); err == nil && n > 0 {
			exitPort = n
		}
	}
	targetHost, targetPortStr, err := net.SplitHostPort(dest)
	if err != nil {
		targetHost, targetPortStr = dest, "443"
	}
	targetPort, err := strconv.Atoi(targetPortStr)
	if err != nil || targetPort <= 0 || targetPort > 65535 {
		targetPort = 443
	}
	exitUser := ""
	if exit.User != nil {
		exitUser = exit.User.String()
	}

	exitAddr := net.JoinHostPort(exit.Hostname(), strconv.Itoa(exitPort))
	started := time.Now()
	conn, err := p.dialOne(hop, exitAddr, exitAddr, timeout)
	if err != nil {
		p.recordHop(hop, false, time.Since(started))
		return nil, err
	}
	if err = connectThrough(conn, exit.Scheme, targetHost, targetPort, exitUser, p.ua); err != nil {
		conn.Close()
		p.recordHop(hop, false, time.Since(started))
		return nil, err
	}
	p.recordHop(hop, true, time.Since(started))
	return conn, nil
}

// hopIndexPath is where the index is kept. A relay is started per instance and restarted often,
// so an index that dies with the process would leave every start as blind as the first one.
func hopIndexPath(cacheDir string) string {
	if cacheDir == "" {
		return ""
	}
	return filepath.Join(cacheDir, "hop_index.json")
}

// loadHopIndex reads the previous run's measurements.
//
// This is the difference between a cold start and a warm one: the first dial after a restart
// already knows which hops reached the exit last time, so it does not have to discover that by
// failing. Entries are kept even when they are old — an older measurement is better than none,
// and every real dial overwrites it.
func (p *ProxyServer) loadHopIndex() {
	if p.hopIdxPath == "" {
		return
	}
	raw, err := os.ReadFile(p.hopIdxPath)
	if err != nil {
		return
	}
	var stored map[string]struct {
		Ok      bool  `json:"ok"`
		Latency int64 `json:"latency_ms"`
		At      int64 `json:"at"`
	}
	if err := json.Unmarshal(raw, &stored); err != nil {
		return
	}
	p.hopMu.Lock()
	defer p.hopMu.Unlock()
	if p.hopIdx == nil {
		p.hopIdx = map[string]hopProbe{}
	}
	for key, v := range stored {
		p.hopIdx[key] = hopProbe{
			ok:      v.Ok,
			latency: time.Duration(v.Latency) * time.Millisecond,
			checked: time.Unix(v.At, 0),
		}
	}
	if len(stored) > 0 {
		log.Printf("[UPSTREAM] loaded %d hop measurement(s) from %s", len(stored), p.hopIdxPath)
	}
}

// saveHopIndex writes the index if anything changed.
func (p *ProxyServer) saveHopIndex() {
	if p.hopIdxPath == "" {
		return
	}
	p.hopMu.Lock()
	if !p.hopDirty || len(p.hopIdx) == 0 {
		p.hopMu.Unlock()
		return
	}
	out := make(map[string]struct {
		Ok      bool  `json:"ok"`
		Latency int64 `json:"latency_ms"`
		At      int64 `json:"at"`
	}, len(p.hopIdx))
	for key, v := range p.hopIdx {
		out[key] = struct {
			Ok      bool  `json:"ok"`
			Latency int64 `json:"latency_ms"`
			At      int64 `json:"at"`
		}{Ok: v.ok, Latency: v.latency.Milliseconds(), At: v.checked.Unix()}
	}
	p.hopDirty = false
	p.hopMu.Unlock()

	raw, err := json.Marshal(out)
	if err != nil {
		return
	}
	tmp := p.hopIdxPath + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return
	}
	_ = os.Rename(tmp, p.hopIdxPath)
}

// validateExitAtStartup drops the fixed exit if no hop can reach it.
//
// The supervisor picks a proxy that works when dialled directly, which says nothing about whether
// another proxy can reach it. Discovering that on the first page request costs the page six
// seconds of blank screen, so it is checked once at startup — with connections that would have
// been spent anyway — and the exit is dropped if it cannot be reached.
func (p *ProxyServer) validateExitAtStartup() {
	exit := p.exitURL
	if exit == nil {
		return
	}
	hops := p.hopOrder()
	if len(hops) == 0 {
		log.Printf("[UPSTREAM] fixed exit %s dropped: no hop available to reach it", exit.Redacted())
		p.exitURL = nil
		return
	}
	// Check the whole path, not just the exit's port.
	//
	// A TCP connect through the hop to the exit's address proves the port answers; it does not
	// prove the exit will carry a request. Measured: the weaker check passed and the very first
	// page request then failed the exit's handshake and paid ten seconds for it. The honest test
	// is the traffic this relay exists to carry, so it opens the login host through hop -> exit.
	// Parallel and short: the app polls the listen port for about five seconds before deciding the
	// relay never started, and this check runs before Listen. A sequential two-hop check took
	// seven seconds and would have made every launch look like a dead relay.
	probeDest := net.JoinHostPort(loginHost, "443")
	if len(hops) > 2 {
		hops = hops[:2]
	}
	found := make(chan *url.URL, len(hops))
	for _, hop := range hops {
		go func(hop *url.URL) {
			conn, err := p.openViaHop(hop, exit, probeDest, 3*time.Second)
			if err == nil {
				conn.Close()
				found <- hop
			}
		}(hop)
	}
	select {
	case hop := <-found:
		log.Printf("[UPSTREAM] fixed exit %s carries %s through %s", exit.Redacted(), loginHost, hop.Redacted())
		return
	case <-time.After(3500 * time.Millisecond):
	}
	log.Printf("[UPSTREAM] fixed exit %s dropped: no hop could carry a request through it. Using the plain chain.",
		exit.Redacted())
	p.exitURL = nil
}

// recordHop notes what a real dial taught us about this hop.
func (p *ProxyServer) recordHop(hop *url.URL, ok bool, took time.Duration) {
	if hop == nil {
		return
	}
	p.hopMu.Lock()
	if p.hopIdx == nil {
		p.hopIdx = map[string]hopProbe{}
	}
	p.hopIdx[hop.String()] = hopProbe{ok: ok, latency: took, checked: time.Now()}
	p.hopDirty = true
	p.hopMu.Unlock()
}

// takeWarm hands out a pre-opened tunnel for dest, if one is standing by.

func (p *ProxyServer) dialViaExit(ctx context.Context, exit *url.URL, dest string, timeout time.Duration) (net.Conn, *url.URL, error) {
	p.rememberDest(dest)

	// The index decides the order; a short race settles it.
	//
	// There used to be a warm pool here, pre-opening hop->exit->target tunnels so a new
	// connection would cost nothing. It made everything slower: public proxies allow one or two
	// concurrent connections, so tunnels that nobody consumed took the slots the real requests
	// needed. Measured cold 20 s, then 6/15/16 s with the pool running, against 5.5 s without.
	// Prefetching only pays when the far end allows many connections.
	hops := p.hopOrder()
	if len(hops) == 0 {
		conn, err := p.dialOne(exit, dest, dest, timeout)
		if err != nil {
			return nil, nil, err
		}
		return conn, exit, nil
	}

	// Race a few, not all: the index already put the likely winners in front, and a race across
	// twenty hops would open twenty tunnels to the same exit.
	if len(hops) > 4 {
		hops = hops[:4]
	}

	type result struct {
		conn net.Conn
		err  error
	}
	results := make(chan result, len(hops))
	for _, hop := range hops {
		go func(hop *url.URL) {
			conn, err := p.openViaHop(hop, exit, dest, timeout)
			results <- result{conn: conn, err: err}
		}(hop)
	}

	var lastErr error
	for i := 0; i < len(hops); i++ {
		r := <-results
		if r.err == nil {
			go func(remaining int) {
				for j := 0; j < remaining; j++ {
					if late := <-results; late.conn != nil {
						late.conn.Close()
					}
				}
			}(len(hops) - i - 1)
			p.upstreamMu.Lock()
			p.lastGood = exit
			p.upstreamMu.Unlock()
			return r.conn, exit, nil
		}
		lastErr = r.err
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("no hop could reach the exit")
	}
	return nil, nil, lastErr
}

// dialUpstreamHttp opens a tunnel through an HTTP proxy with CONNECT.
func (p *ProxyServer) dialUpstreamHttp(u *url.URL, dest string, timeout time.Duration) (net.Conn, error) {
	defPort := "80"
	if strings.EqualFold(u.Scheme, "https") {
		defPort = "443"
	}
	conn, err := net.DialTimeout("tcp", ensureHostPort(u.Host, defPort), timeout)
	if err != nil {
		return nil, err
	}
	authHeader := ""
	if u.User != nil {
		authHeader = "Proxy-Authorization: Basic " +
			base64.StdEncoding.EncodeToString([]byte(u.User.String())) + "\r\n"
	}
	req := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n%s\r\n",
		dest, dest, p.ua, authHeader)
	if _, err = io.WriteString(conn, req); err != nil {
		conn.Close()
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(timeout))
	if err := readConnectResponse(conn); err != nil {
		conn.Close()
		return nil, err
	}
	_ = conn.SetDeadline(time.Time{})
	return conn, nil
}

// dialUpstreamAny tries every configured upstream, in order, on one dial.
//
// The two schemes need different targets: WARP's SOCKS proxy cannot resolve Ankama's game
// domains, so it gets an IP, while an HTTP proxy gets the hostname and resolves it itself.
func (p *ProxyServer) dialUpstreamAny(ctx context.Context, dest string, timeout time.Duration) (net.Conn, *url.URL, error) {
	socksTarget := dest
	if host, port, splitErr := net.SplitHostPort(dest); splitErr == nil {
		if ips, lookupErr := p.resolver.Lookup(ctx, host); lookupErr == nil && len(ips) > 0 {
			socksTarget = net.JoinHostPort(ips[0].String(), port)
		}
	}

	p.upstreamMu.Lock()
	preferred := p.lastGood
	p.upstreamMu.Unlock()

	order := make([]*url.URL, 0, len(p.upstreams)+1)
	if preferred != nil {
		order = append(order, preferred)
	}
	for _, u := range p.upstreams {
		if u == nil {
			continue
		}
		if preferred != nil && u.String() == preferred.String() {
			continue
		}
		order = append(order, u)
	}

	// Fast path: the exit we are already using. One dial, no goroutines, and the same source
	// address the session has been using.
	if preferred != nil {
		started := time.Now()
		if conn, err := p.dialOne(preferred, dest, socksTarget, timeout); err == nil {
			p.recordHop(preferred, true, time.Since(started))
			return conn, preferred, nil
		} else {
			p.recordHop(preferred, false, time.Since(started))

			// Never move the exit IP while a session is live.
			//
			// Failing over here is invisible locally and fatal remotely: the server sees the
			// same account reconnect from a different address mid-session and closes it as a
			// hijacked connection. Measured: three exit changes in one test, and the player was
			// thrown out of the game each time while nothing in the local log looked wrong —
			// zero relay rebuilds, because this path never touches the relay, it just picks a
			// different upstream on the next dial.
			//
			// A failed dial on a dead exit is the honest outcome: this connection fails, the
			// session keeps its address, and the supervisor can rebuild between sessions when
			// the client is not connected.
			if active := atomic.LoadInt64(&p.telemetry.connsActive); active > 0 {
				log.Printf("[UPSTREAM] %s failed with %d live connection(s); refusing to switch exit IP mid-session",
					preferred.Redacted(), active)
				return nil, nil, err
			}
		}
	}

	// The exit is gone, or we never had one. Race the rest.
	//
	// Sequentially this was up to len(chain) x timeout: four dead candidates at six seconds
	// each is a twenty-four second stall, which the client experiences as a dead server. Racing
	// costs one timeout regardless of how many candidates there are, and the first upstream to
	// open a tunnel wins.
	//
	// Racing is only ever used to *establish* an exit. Once one works it is pinned (lastGood) and
	// the fast path above keeps every later dial on it, because a session that changes source
	// address mid-flight is what the server treats as hijacked.
	return p.raceUpstreams(ctx, order, dest, socksTarget, timeout)
}

// dialOne opens a tunnel through a single upstream, choosing the handshake by scheme.
func (p *ProxyServer) dialOne(u *url.URL, dest, socksTarget string, timeout time.Duration) (net.Conn, error) {
	if u == nil {
		return nil, fmt.Errorf("nil upstream")
	}
	if strings.HasPrefix(strings.ToLower(u.Scheme), "socks5") {
		return dialUpstreamSocks5(u, socksTarget, timeout)
	}
	return p.dialUpstreamHttp(u, dest, timeout)
}

// raceUpstreams dials every candidate at once and returns the first that opens.
//
// The losers are closed rather than leaked: a race where three goroutines each hold an open
// tunnel to Ankama would look, from the far side, like three connections from three addresses
// that never sent a byte.
func (p *ProxyServer) raceUpstreams(ctx context.Context, order []*url.URL, dest, socksTarget string,
	timeout time.Duration) (net.Conn, *url.URL, error) {
	if len(order) == 0 {
		return nil, nil, fmt.Errorf("no upstream configured")
	}

	type result struct {
		conn net.Conn
		url  *url.URL
		err  error
		took time.Duration
	}
	results := make(chan result, len(order))
	for _, u := range order {
		go func(u *url.URL) {
			started := time.Now()
			conn, err := p.dialOne(u, dest, socksTarget, timeout)
			results <- result{conn: conn, url: u, err: err, took: time.Since(started)}
		}(u)
	}

	var lastErr error
	for i := 0; i < len(order); i++ {
		r := <-results
		if r.err == nil {
			p.recordHop(r.url, true, r.took)
			// Drain the stragglers in the background so their sockets are closed.
			go func(remaining int) {
				for j := 0; j < remaining; j++ {
					if late := <-results; late.conn != nil {
						late.conn.Close()
					}
				}
			}(len(order) - i - 1)

			p.upstreamMu.Lock()
			changed := p.lastGood == nil || p.lastGood.String() != r.url.String()
			p.lastGood = r.url
			p.upstreamMu.Unlock()
			if changed {
				log.Printf("[UPSTREAM] carrying %s over %s (exit changed)", dest, r.url.Redacted())
			}
			return r.conn, r.url, nil
		}
		if r.url != nil {
			p.recordHop(r.url, false, r.took)
		}
		lastErr = r.err
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("no upstream configured")
	}
	return nil, nil, lastErr
}

func (p *ProxyServer) dialUpstreamOrDirect(ctx context.Context, dest string) (net.Conn, error) {
	start := time.Now()
	conn, err := p.doDial(ctx, dest)
	rttMs := float64(time.Since(start).Microseconds()) / 1000.0
	p.telemetry.recordDial(rttMs, err)
	return conn, err
}

func (p *ProxyServer) doDial(ctx context.Context, dest string) (net.Conn, error) {
	// The client resolves its socket target against the page origin, which in
	// this host is the virtual host `https://dofus.local/`. So the game asks the
	// proxy to open `dofus.local:443` -- a name that exists only inside WebView2's
	// resource mapping and can never be dialled. WARP answers SOCKS5 code 1 and
	// the game reports a bare LOGIN_ERROR.
	//
	// This is the whole login failure: the destination is the emulator's own
	// virtual host, not an Ankama address. Map it to the real login host, which
	// is where the client's config.json points anyway.
	if host, port, splitErr := net.SplitHostPort(dest); splitErr == nil {
		switch strings.ToLower(host) {
		case "dofus.local", "dofus.localhost", "localhost":
			if host != "localhost" {
				mapped := net.JoinHostPort(loginHost, port)
				virtualHostRewriteOnce.Do(func() {
					log.Printf("[ROUTE] %s -> %s (the client resolved its socket against the page origin)",
						dest, mapped)
				})
				dest = mapped
			}
		}
	}

	// The fixed exit comes first: it is the identity the session is supposed to keep.
	//
	// Hops are raced and can be replaced freely; the exit is what Ankama sees. Only if no hop can
	// reach the exit at all do we fall through to the plain chain, and that fallback changes the
	// exit IP, so it says so out loud.
	if p.exitURL != nil {
		p.exitMu.Lock()
		blocked := time.Now().Before(p.exitBlockedUntil)
		p.exitMu.Unlock()

		if !blocked {
			conn, _, err := p.dialViaExit(ctx, p.exitURL, dest, 6*time.Second)
			if err == nil {
				upstreamHealthy.Store(true)
				return conn, nil
			}
			p.exitMu.Lock()
			p.exitBlockedUntil = time.Now().Add(exitCooldown)
			p.exitMu.Unlock()
			upstreamHealthy.Store(false)
			log.Printf("[UPSTREAM] fixed exit %s unreachable through any hop (%v); using the plain chain for the next %s (the exit IP will change)",
				p.exitURL.Redacted(), err, exitCooldown)
		}
	}

	if p.upstreamURL != nil {
		scheme := strings.ToLower(p.upstreamURL.Scheme)
		if len(p.upstreams) > 0 {
			// Try every configured upstream on this dial, whichever scheme each one speaks.
			//
			// A single upstream is a single point of failure for a whole session: a public proxy
			// that answered the supervisor's probe and died thirty seconds later would take the
			// game connection with it, and the client would report the server as unavailable
			// after a login that had just worked. With a chain, the cost of a dead candidate is
			// one dial attempt instead of an outage.
			//
			// Retry the whole chain briefly before declaring it dead: a network change
			// (wifi -> ethernet, VPN re-handshake) drops the tunnel for a few seconds and the
			// very next dial fails. Three attempts, ~4.5 s worst case, still never a direct dial.
			var conn net.Conn
			var err error
			for attempt := 0; attempt < 3; attempt++ {
				if attempt > 0 {
					time.Sleep(time.Duration(attempt) * 1500 * time.Millisecond)
				}
				var used *url.URL
				conn, used, err = p.dialUpstreamAny(ctx, dest, 6*time.Second)
				if err == nil {
					upstreamHealthy.Store(true)
					_ = used
					return conn, nil
				}
			}
			upstreamHealthy.Store(false)
			// Fail closed by default. WARP exists to keep the real IP away from
			// Ankama, so a dead upstream must NOT be papered over with a direct
			// connection -- that would silently trade the user's IP for uptime.
			//
			// -allow-direct (network.ini fail_closed=0) is the explicit opt-out.
			// It still logs loudly every time it happens, because the whole point
			// of this proxy is that the user can tell the difference.
			if p.allowDirect {
				upstreamFallbackOnce.Do(func() {
					log.Printf("[UPSTREAM] dial to %s via %s failed (%v); -allow-direct is set, so falling "+
						"back to a DIRECT connection. Ankama will see this machine's real IP.",
						dest, p.upstreamURL.Redacted(), err)
				})
				host, portStr, splitErr := net.SplitHostPort(dest)
				if splitErr != nil {
					host, portStr = dest, "443"
				}
				target := dest
				if ips, lookupErr := p.resolver.Lookup(ctx, host); lookupErr == nil && len(ips) > 0 {
					target = net.JoinHostPort(ips[0].String(), portStr)
				}
				return (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(ctx, "tcp", target)
			}
			upstreamFallbackOnce.Do(func() {
				log.Printf("[UPSTREAM] dial to %s via %s failed (%v); refusing to fall back to a direct "+
					"connection because that would expose the real IP. Start WARP in proxy mode "+
					"(warp-cli mode proxy && warp-cli connect) and relaunch.",
					dest, p.upstreamURL.Redacted(), err)
			})
			return nil, fmt.Errorf("upstream %s unavailable and direct dial disabled: %w",
				p.upstreamURL.Redacted(), err)
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
	// Loopback-only CDN relay for the WebView2 client (see handleCDNRelay).
	// Exempt from proxy auth: it is reachable only from the local machine and
	// is called by plain browser XHRs that cannot carry proxy credentials.
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

// injectDisguise splices the mobile-disguise script into an HTML document,
// working directly on the byte slice. The previous implementation copied the
// whole body to a string, spliced (a second full copy), then copied back to
// bytes - three passes over the document per HTML response.
func (p *ProxyServer) injectDisguise(bodyBytes []byte) []byte {
	code := p.getDisguiseContent()
	var injBuf strings.Builder
	injBuf.Grow(64 + len(code))
	if code != "" {
		injBuf.WriteString("\n\t\t<script type=\"text/javascript\">\n")
		injBuf.WriteString(code)
		injBuf.WriteString("\n\t\t</script>\n")
	} else {
		injBuf.WriteString(fmt.Sprintf("\n\t\t<script type=\"text/javascript\" src=\"http://10.0.2.2:%d/__disguise.js\"></script>\n", p.port))
	}
	injection := injBuf.String()

	// Grow a single destination buffer once, then copy prefix +
	// injection + suffix into it. This replaces the three chained
	// append() calls that re-allocated on every concatenation.
	dst := make([]byte, 0, len(bodyBytes)+len(injection)+14)
	switch {
	case reHeadOpen.Match(bodyBytes):
		loc := reHeadOpen.Find(bodyBytes)
		dst = append(dst, bodyBytes[:loc[1]]...)
		dst = append(dst, injection...)
		dst = append(dst, bodyBytes[loc[1]:]...)
	case reHeadClose.Match(bodyBytes):
		loc := reHeadClose.Find(bodyBytes)
		dst = append(dst, bodyBytes[:loc[0]]...)
		dst = append(dst, injection...)
		dst = append(dst, bodyBytes[loc[0]:]...)
	case reHtmlOpen.Match(bodyBytes):
		loc := reHtmlOpen.Find(bodyBytes)
		dst = append(dst, bodyBytes[:loc[1]]...)
		dst = append(dst, "<head>"...)
		dst = append(dst, injection...)
		dst = append(dst, "</head>"...)
		dst = append(dst, bodyBytes[loc[1]:]...)
	default:
		dst = append(dst, "<head>"...)
		dst = append(dst, injection...)
		dst = append(dst, "</head>"...)
		dst = append(dst, bodyBytes...)
	}
	return dst
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

// ---- CDN relay + shared asset cache ----------------------------------------
//
// GET http://127.0.0.1:<port>/__cdn/<host>/<path>[?query] fetches
// https://<host>/<path> with server-side hotlink-safe headers (Referer/Origin
// of the official web client, mobile UA) and serves it to the caller with CORS.
// Responses reuse the same on-disk cache as the regular proxy path (identical
// sha256 keys), so a map fetched once is served from disk for both Android and
// WebView2 clients. Loopback-only: /__cdn/ is unreachable from the network.

const assetCacheTTL = 7 * 24 * time.Hour

func isLocalRequestHost(host string) bool {
	if host == "" {
		return true
	}
	host = strings.ToLower(host)
	return strings.HasPrefix(host, "127.0.0.1") ||
		strings.HasPrefix(host, "localhost") ||
		strings.HasPrefix(host, "[::1]")
}

// isCdnAssetHost reports whether host is an Ankama asset CDN host
// (*.cdn.ankama.com / *.cdn.ankama-games.com). Deliberately narrow: API and
// login hosts must never have asset headers injected.
func isCdnAssetHost(host string) bool {
	if i := strings.IndexByte(host, ':'); i != -1 {
		host = host[:i]
	}
	host = strings.ToLower(host)
	return host == "cdn.ankama.com" || host == "cdn.ankama-games.com" ||
		strings.HasSuffix(host, ".cdn.ankama.com") || strings.HasSuffix(host, ".cdn.ankama-games.com")
}

// isStaticAnkamaHost allows the Ankama static asset host: web-shop artwork
// (/shops/img/...) and store/news banners (/ankama/cms/images/...). The
// virtual-origin Referer gets 403 from this host, so every relayed request
// is re-issued with the official web-client Referer/Origin. The client only
// fetches images from here; GET/HEAD repairs only.
func isStaticAnkamaHost(host string) bool {
	if i := strings.IndexByte(host, ':'); i != -1 {
		host = host[:i]
	}
	return strings.EqualFold(host, "static.ankama.com")
}

// isVolatileCacheURL blocks long-term caching of bootstrap/auth JSON served by
// the login proxies (config.json, getLanguage.json, version.json): a stale
// session/login config would break the client for up to assetCacheTTL.
func isVolatileCacheURL(u string) bool {
	if strings.Contains(u, "production-login") {
		return true
	}
	noQuery := u
	if i := strings.IndexAny(u, "?#"); i != -1 {
		noQuery = u[:i]
	}
	return strings.HasSuffix(noQuery, "/config.json") ||
		strings.HasSuffix(noQuery, "/getLanguage.json") ||
		strings.HasSuffix(noQuery, "/version.json")
}

// isEmulatorOrigin reports whether a Referer/Origin value comes from the
// emulator/webview environment rather than a real client.
func isEmulatorOrigin(v string) bool {
	if v == "" {
		return true
	}
	l := strings.ToLower(v)
	return strings.Contains(l, "dofus.local") || strings.Contains(l, "localhost") ||
		strings.Contains(l, "127.0.0.1") || strings.HasPrefix(l, "file:")
}

// applyHotlinkHeaders replaces emulator-origin Referer/Origin headers on CDN
// asset requests with the official web-client origin. Absent headers (native
// mobile clients) are left untouched so the Android path is bit-for-bit
// unchanged.
func (p *ProxyServer) applyHotlinkHeaders(h http.Header, host string) {
	if !isCdnAssetHost(host) {
		return
	}
	if ref := h.Get("Referer"); ref != "" && isEmulatorOrigin(ref) {
		h.Set("Referer", p.referer)
	}
	if org := h.Get("Origin"); org != "" && isEmulatorOrigin(org) {
		h.Set("Origin", p.refererRoot)
	}
}

// assetCachePath maps a target URL to its on-disk cache entry (16-byte sha256
// prefix, hex). Shared by handleHTTP, the relay and the precache warmer.
func (p *ProxyServer) assetCachePath(target string) string {
	sum := sha256.Sum256([]byte(target))
	return filepath.Join(p.cacheDir, hex.EncodeToString(sum[:16]))
}

// setLongAssetCacheHeaders marks static CDN asset responses as immutable for a
// week. Asset paths are version-hashed, so this is safe, and it lets both the
// WebView2 HTTP cache and intermediate layers absorb repeat loads (map
// navigation previously re-fetched every sprite through the relay).
func setLongAssetCacheHeaders(h http.Header) {
	h.Set("Cache-Control", "public, max-age=604800, immutable")
}

// serveCachedAsset streams a cached asset response (CORS + cached
// Content-Type + Range/304 via http.ServeContent) and reports whether the
// cache was served. Expired entries are evicted.
func (p *ProxyServer) serveCachedAsset(w http.ResponseWriter, r *http.Request, cacheFilePath, fallbackPath string) bool {
	fi, err := os.Stat(cacheFilePath)
	if err != nil {
		return false
	}
	if time.Since(fi.ModTime()) > assetCacheTTL {
		_ = os.Remove(cacheFilePath)
		_ = os.Remove(cacheFilePath + ".meta")
		return false
	}
	f, err := os.Open(cacheFilePath)
	if err != nil {
		return false
	}
	defer f.Close()
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
	h.Set("Access-Control-Allow-Headers", "*")
	setLongAssetCacheHeaders(h)
	mimeType := ""
	if metaBytes, metaErr := os.ReadFile(cacheFilePath + ".meta"); metaErr == nil {
		mimeType = strings.TrimSpace(string(metaBytes))
	}
	if mimeType == "" {
		mimeType = detectMimeType(fallbackPath)
	}
	if mimeType != "" {
		h.Set("Content-Type", mimeType)
	}
	http.ServeContent(w, r, fallbackPath, fi.ModTime(), f)
	return true
}

// ---------------------------------------------------------------------------
// Asset mirror for the wizAssets local-file layer
//
// Every cached asset is hard-linked into <cacheDir>/files/<host>/<path>. The
// WebView2 client maps that tree to https://assets.local/ so its wizAssets
// shim can hand the game local URLs, mirroring how the original mobile client
// persists assets to device storage and loads them back from files.
// ---------------------------------------------------------------------------

// mirrorRelPath returns "<host>/<url-path>" (forward slashes) for a plain
// http(s) asset URL, or "" when the URL is unusable as a mirror path.
func mirrorRelPath(target string) string {
	u, err := url.Parse(target)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return ""
	}
	host := strings.ToLower(u.Hostname())
	if host == "" || strings.ContainsAny(host, `<>:"|?*\`) {
		return ""
	}
	assetPath := u.Path
	if assetPath == "" || assetPath == "/" || strings.Contains(assetPath, "..") ||
		strings.ContainsAny(assetPath, "<>:\"|*\\") {
		return ""
	}
	return host + assetPath
}

// mirrorPathFor maps a CDN asset URL to its mirrored on-disk location below
// <cacheDir>/files.
func (p *ProxyServer) mirrorPathFor(target string) string {
	if p.cacheDir == "" {
		return ""
	}
	rel := mirrorRelPath(target)
	if rel == "" {
		return ""
	}
	return filepath.Join(p.cacheDir, "files", filepath.FromSlash(rel))
}

// mirrorCachedFile hard-links a cached file into the mirror tree (falling
// back to a copy where links are unsupported). Existing entries are left
// alone: asset URLs are version-hashed, so their content never changes.
func (p *ProxyServer) mirrorCachedFile(target, cacheFilePath string) {
	mirror := p.mirrorPathFor(target)
	if mirror == "" {
		return
	}
	if _, err := os.Stat(mirror); err == nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(mirror), 0755); err != nil {
		return
	}
	if os.Link(cacheFilePath, mirror) == nil {
		p.mirrorIndexAdd(mirrorRelPath(target))
		return
	}
	in, err := os.Open(cacheFilePath)
	if err != nil {
		return
	}
	defer in.Close()
	out, err := os.Create(mirror)
	if err != nil {
		return
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		_ = os.Remove(mirror)
		return
	}
	_ = out.Close()
	p.mirrorIndexAdd(mirrorRelPath(target))
}

func (p *ProxyServer) mirrorIndexAdd(rel string) {
	if rel == "" {
		return
	}
	p.mirrorMu.Lock()
	if p.mirrorBuilt && p.mirrorIndex != nil {
		p.mirrorIndex[rel] = struct{}{}
	}
	p.mirrorMu.Unlock()
}

// mirrorIndexSnapshot lists the mirrored rel paths, walking the mirror tree
// on first use and caching the index afterwards.
func (p *ProxyServer) mirrorIndexSnapshot() []string {
	p.mirrorMu.Lock()
	defer p.mirrorMu.Unlock()
	if !p.mirrorBuilt {
		p.mirrorIndex = make(map[string]struct{}, 4096)
		root := filepath.Join(p.cacheDir, "files")
		_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			rel, relErr := filepath.Rel(root, path)
			if relErr != nil {
				return nil
			}
			p.mirrorIndex[filepath.ToSlash(rel)] = struct{}{}
			return nil
		})
		p.mirrorBuilt = true
	}
	out := make([]string, 0, len(p.mirrorIndex))
	for rel := range p.mirrorIndex {
		out = append(out, rel)
	}
	sort.Strings(out)
	return out
}

// handleAssetStore serves the loopback-only wizAssets support endpoints used
// by the client shim:
//
//	GET /__files          -> JSON array of mirrored "<host>/<path>" entries
//	GET /__warm?u=<url>   -> ensures the asset is cached + mirrored
//	GET /__delete?p=<path>-> removes mirror + cache entries for a path
func (p *ProxyServer) handleAssetStore(w http.ResponseWriter, r *http.Request) {
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", "GET, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "*")
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
		files := p.mirrorIndexSnapshot()
		payload, err := json.Marshal(files)
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
		if !p.quiet {
			log.Printf("[WARM] %s", target)
		}
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
			if id := mapJSONID(u.Path); id > 0 {
				p.scheduleMapPrefetch(target, id)
			}
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
			if rel != needle && !strings.HasSuffix(rel, "/"+needle) {
				continue
			}
			_ = os.Remove(filepath.Join(p.cacheDir, "files", filepath.FromSlash(rel)))
			target := "https://" + rel
			_ = os.Remove(p.assetCachePath(target))
			_ = os.Remove(p.assetCachePath(target) + ".meta")
			p.mirrorMu.Lock()
			if p.mirrorIndex != nil {
				delete(p.mirrorIndex, rel)
			}
			p.mirrorMu.Unlock()
			removed++
		}
		h.Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"ok":true,"removed":%d}`, removed)
	default:
		http.Error(w, "Not Found", http.StatusNotFound)
	}
}

// ---------------------------------------------------------------------------
// Mods pipeline: rules.json in modsDir rewrites CDN responses on the fly.
//
// Rules match (in order) against the full upstream asset URL:
//
//	{"match": "regex", "action": "block",    "status": 204}
//	{"match": "regex", "action": "override", "file": "local/file.png"}
//	{"match": "regex", "action": "patch",    "patch": [{"op":"set","path":"/a/b","value":1}]}
//
// block returns a synthetic status; override serves a local file from the
// mods directory; patch applies RFC 6901-style edits (set/remove) to the JSON
// body. Patches are applied per request on top of the unmodified cached
// original, so rules can be edited or deleted at any time without touching
// the cache.
// ---------------------------------------------------------------------------

type modPatchOp struct {
	Op    string      `json:"op"`
	Path  string      `json:"path"`
	Value interface{} `json:"value"`
}

type modRule struct {
	Match       string       `json:"match"`
	Action      string       `json:"action"`
	Status      int          `json:"status"`
	File        string       `json:"file"`
	ContentType string       `json:"contentType"`
	Patch       []modPatchOp `json:"patch"`
}

// loadModsIfChanged (re)loads rules.json when its mtime changes, throttled to
// one stat every two seconds. Called per relay request; cheap when idle.
func (p *ProxyServer) loadModsIfChanged() {
	if p.modsDir == "" {
		return
	}
	p.modsMu.Lock()
	defer p.modsMu.Unlock()
	if time.Since(p.modsChecked) < 2*time.Second {
		return
	}
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
	if fi.ModTime().Equal(p.modsRulesMod) {
		return
	}
	raw, err := os.ReadFile(rulesPath)
	if err != nil {
		return
	}
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

// applyModRules runs the first matching rule for an upstream asset URL and
// reports whether the response was fully handled (block/override/patch).
func (p *ProxyServer) applyModRules(w http.ResponseWriter, r *http.Request, targetStr string) bool {
	if p.modsDir == "" {
		return false
	}
	p.loadModsIfChanged()
	p.modsMu.Lock()
	rules, res := p.modsRules, p.modsRes
	p.modsMu.Unlock()
	for i, rule := range rules {
		if i >= len(res) || res[i] == nil || !res[i].MatchString(targetStr) {
			continue
		}
		switch rule.Action {
		case "block":
			status := rule.Status
			if status < 100 || status > 599 {
				status = http.StatusNoContent
			}
			w.WriteHeader(status)
			p.requestLog("MODBLOCK %d %s", status, targetStr)
			return true
		case "override":
			if p.serveModOverride(w, r, rule, targetStr) {
				return true
			}
		case "patch":
			if p.serveModPatched(w, r, rule, targetStr) {
				return true
			}
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
	if err != nil || fi.IsDir() {
		return false
	}
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "*")
	setLongAssetCacheHeaders(h)
	ct := rule.ContentType
	if ct == "" {
		ct = detectMimeType(full)
	}
	if ct != "" {
		h.Set("Content-Type", ct)
	}
	http.ServeContent(w, r, filepath.Base(full), fi.ModTime(), f)
	p.requestLog("MODOVER %s", targetStr)
	return true
}

func (p *ProxyServer) serveModPatched(w http.ResponseWriter, r *http.Request, rule modRule, targetStr string) bool {
	body, ct, ok := p.patchedAssetBody(r.Context(), targetStr, rule)
	if !ok {
		return false
	}
	h := w.Header()
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "*")
	setLongAssetCacheHeaders(h)
	if rule.ContentType != "" {
		h.Set("Content-Type", rule.ContentType)
	} else if ct != "" {
		h.Set("Content-Type", ct)
	} else {
		h.Set("Content-Type", "application/json")
	}
	h.Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = w.Write(body)
	}
	p.requestLog("MODPATCH %s (%d bytes)", targetStr, len(body))
	return true
}

// patchedAssetBody materializes the cache entry for targetStr (warming it
// first when needed) and applies the rule's JSON patches in memory. The
// cached file itself is never modified.
func (p *ProxyServer) patchedAssetBody(ctx context.Context, targetStr string, rule modRule) ([]byte, string, bool) {
	cacheFilePath := p.assetCachePath(targetStr)
	if _, err := os.Stat(cacheFilePath); err != nil {
		ok, _, status, errText := p.warmAssetCacheDetail(ctx, targetStr)
		if !ok {
			p.prefetchLog("[MODS] patch base fetch failed status=%d err=%q %s", status, errText, targetStr)
			return nil, "", false
		}
	}
	f, err := os.Open(cacheFilePath)
	if err != nil {
		return nil, "", false
	}
	raw, err := io.ReadAll(io.LimitReader(f, 64<<20))
	_ = f.Close()
	if err != nil {
		return nil, "", false
	}
	ct := ""
	if metaBytes, metaErr := os.ReadFile(cacheFilePath + ".meta"); metaErr == nil {
		ct = strings.TrimSpace(string(metaBytes))
	}
	var doc interface{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, "", false // not JSON: patch rules do not apply
	}
	for _, op := range rule.Patch {
		switch strings.ToLower(strings.TrimSpace(op.Op)) {
		case "set":
			doc, _ = jsonPointerSet(doc, op.Path, op.Value)
		case "remove", "delete":
			doc, _ = jsonPointerRemove(doc, op.Path)
		}
	}
	out, err := json.Marshal(doc)
	if err != nil {
		return nil, "", false
	}
	return out, ct, true
}

// jsonPointerTokens splits an RFC 6901 pointer into unescaped tokens.
func jsonPointerTokens(pointer string) []string {
	pointer = strings.TrimPrefix(pointer, "/")
	if pointer == "" {
		return nil
	}
	parts := strings.Split(pointer, "/")
	for i, s := range parts {
		s = strings.ReplaceAll(s, "~1", "/")
		s = strings.ReplaceAll(s, "~0", "~")
		parts[i] = s
	}
	return parts
}

// jsonPointerSet writes value at pointer, auto-creating missing object keys
// along the way. "-" appends to arrays. Returns the (possibly new) document.
func jsonPointerSet(doc interface{}, pointer string, value interface{}) (interface{}, bool) {
	tokens := jsonPointerTokens(pointer)
	if len(tokens) == 0 {
		return value, true
	}
	return pointerSetWalk(doc, tokens, value, 0)
}

func pointerSetWalk(node interface{}, tokens []string, value interface{}, idx int) (interface{}, bool) {
	tok := tokens[idx]
	last := idx == len(tokens)-1
	switch cur := node.(type) {
	case map[string]interface{}:
		if last {
			cur[tok] = value
			return cur, true
		}
		child, ok := cur[tok]
		if !ok || child == nil {
			child = map[string]interface{}{}
		}
		updated, ok2 := pointerSetWalk(child, tokens, value, idx+1)
		if !ok2 {
			return cur, false
		}
		cur[tok] = updated
		return cur, true
	case []interface{}:
		if last && tok == "-" {
			return append(cur, value), true
		}
		n, err := strconv.Atoi(tok)
		if err != nil || n < 0 || n >= len(cur) {
			return cur, false
		}
		if last {
			cur[n] = value
			return cur, true
		}
		updated, ok2 := pointerSetWalk(cur[n], tokens, value, idx+1)
		if !ok2 {
			return cur, false
		}
		cur[n] = updated
		return cur, true
	default:
		return node, false
	}
}

// jsonPointerRemove deletes the node at pointer (splicing arrays). Returns
// the (possibly new) document.
func jsonPointerRemove(doc interface{}, pointer string) (interface{}, bool) {
	tokens := jsonPointerTokens(pointer)
	if len(tokens) == 0 {
		return doc, false
	}
	return pointerRemoveWalk(doc, tokens, 0)
}

func pointerRemoveWalk(node interface{}, tokens []string, idx int) (interface{}, bool) {
	tok := tokens[idx]
	last := idx == len(tokens)-1
	switch cur := node.(type) {
	case map[string]interface{}:
		if last {
			if _, ok := cur[tok]; !ok {
				return cur, false
			}
			delete(cur, tok)
			return cur, true
		}
		child, ok := cur[tok]
		if !ok {
			return cur, false
		}
		updated, ok2 := pointerRemoveWalk(child, tokens, idx+1)
		if !ok2 {
			return cur, false
		}
		cur[tok] = updated
		return cur, true
	case []interface{}:
		n, err := strconv.Atoi(tok)
		if err != nil || n < 0 || n >= len(cur) {
			return cur, false
		}
		if last {
			return append(cur[:n], cur[n+1:]...), true
		}
		updated, ok2 := pointerRemoveWalk(cur[n], tokens, idx+1)
		if !ok2 {
			return cur, false
		}
		cur[n] = updated
		return cur, true
	default:
		return node, false
	}
}

func (p *ProxyServer) handleCDNRelay(w http.ResponseWriter, r *http.Request) {
	setCORS := func() {
		h := w.Header()
		h.Set("Access-Control-Allow-Origin", "*")
		h.Set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
		h.Set("Access-Control-Allow-Headers", "*")
	}
	setCORS()

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
	if r.URL.RawQuery != "" {
		targetStr += "?" + r.URL.RawQuery
	}
	if p.applyModRules(w, r, targetStr) {
		return
	}
	reqStart := time.Now()

	cacheable := p.cacheDir != "" && isCacheAssetURL(targetStr) && !isVolatileCacheURL(targetStr)
	var cacheFilePath string
	if cacheable {
		cacheFilePath = p.assetCachePath(targetStr)
		if p.serveCachedAsset(w, r, cacheFilePath, assetPath) {
			p.mirrorCachedFile(targetStr, cacheFilePath)
			if !p.quiet {
				log.Printf("[RELAY] HIT %s", targetStr)
			}
			p.requestLog("HIT %s", targetStr)
			if id := mapJSONID(assetPath); id > 0 {
				p.scheduleMapPrefetch(targetStr, id)
			}
			return
		}
	}

	// Recently denied upstream (403/404): answer instantly from the negative
	// cache instead of burning a pool slot on a request that cannot succeed
	// (some map ids answer 403 AccessDenied on every path).
	if status, bad := p.negCacheHit(targetStr); bad {
		setCORS()
		w.WriteHeader(status)
		p.requestLog("%d 0ms negative-cache %s", status, targetStr)
		return
	}

	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetStr, nil)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	outReq.Header.Set("User-Agent", p.ua)
	outReq.Header.Set("Referer", p.referer)
	outReq.Header.Set("Origin", p.refererRoot)
	outReq.Header.Set("Accept", "*/*")
	if c := r.Header.Get("Cookie"); c != "" {
		outReq.Header.Set("Cookie", c)
	}
	if v := r.Header.Get("Range"); v != "" {
		outReq.Header.Set("Range", v)
	}
	if v := r.Header.Get("If-None-Match"); v != "" {
		outReq.Header.Set("If-None-Match", v)
	}
	if v := r.Header.Get("If-Modified-Since"); v != "" {
		outReq.Header.Set("If-Modified-Since", v)
	}
	// No explicit Accept-Encoding: Go's transport then negotiates gzip and
	// decodes it transparently, so bodies stream clean (O(32KB) chunks) into
	// both the client and the cache file with no full-body buffering.

	// Mark live client traffic: prefetch workers hold back while this is > 0
	// (decrement runs after the response body has been streamed through).
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
	setCORS()
	if resp.StatusCode == http.StatusOK {
		setLongAssetCacheHeaders(wh)
		p.negCacheClear(targetStr)
	} else if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusNotFound {
		p.negCacheStore(targetStr, resp.StatusCode)
	}
	if !p.quiet {
		log.Printf("[RELAY] %d %s", resp.StatusCode, targetStr)
	}
	p.requestLog("%d %dms %s", resp.StatusCode, time.Since(reqStart).Milliseconds(), targetStr)

	w.WriteHeader(resp.StatusCode)
	buf := bufPool.Get().([]byte)
	defer bufPool.Put(buf)

	if cacheFilePath == "" || resp.StatusCode != http.StatusOK || r.Method != http.MethodGet {
		_, _ = io.CopyBuffer(w, resp.Body, buf)
		return
	}

	// Cache miss on a cacheable asset: stream to client and temp file at once.
	tmpPath := fmt.Sprintf("%s.%d.%d.tmp", cacheFilePath, os.Getpid(), time.Now().UnixNano())
	tf, err := os.Create(tmpPath)
	if err != nil {
		_, _ = io.CopyBuffer(w, resp.Body, buf)
		return
	}
	written, copyErr := io.CopyBuffer(io.MultiWriter(w, tf), resp.Body, buf)
	_ = tf.Close()
	if copyErr != nil || (resp.ContentLength > 0 && written < resp.ContentLength) {
		_ = os.Remove(tmpPath)
		return
	}
	if atomicReplaceFile(tmpPath, cacheFilePath) == nil {
		if ct := resp.Header.Get("Content-Type"); ct != "" {
			_ = os.WriteFile(cacheFilePath+".meta", []byte(ct), 0644)
		}
		p.mirrorCachedFile(targetStr, cacheFilePath)
		if id := mapJSONID(assetPath); id > 0 {
			p.scheduleMapPrefetch(targetStr, id)
		}
	} else {
		_ = os.Remove(tmpPath)
	}
}

// warmAssetCache fetches a cacheable asset through the proxy transport and
// stores it in the shared cache. Returns (ok, alreadyCached).
func (p *ProxyServer) warmAssetCache(ctx context.Context, target string) (bool, bool) {
	ok, already, _, _ := p.warmAssetCacheDetail(ctx, target)
	return ok, already
}

// warmAssetCacheDetail is warmAssetCache with failure diagnostics: status is
// the upstream HTTP status (0 when the request never completed) and errText
// describes transport or cache-write errors. Concurrent warms of the same
// asset are de-duplicated so the client and the prefetcher can race without
// downloading the same file twice.
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

	// Recently denied upstream (403/404): no amount of retrying helps within
	// the TTL, so fail fast without touching the connection pool.
	if status, bad := p.negCacheHit(target); bad {
		return false, false, status, "negative-cached (recent upstream 4xx)"
	}

	// De-duplicate concurrent warms of the same target.
	p.warmMu.Lock()
	if p.warmInflight == nil {
		p.warmInflight = make(map[string]chan struct{})
	}
	if ch, ok := p.warmInflight[target]; ok {
		p.warmMu.Unlock()
		select {
		case <-ch:
		case <-ctx.Done():
			return false, false, 0, "canceled while waiting for in-flight warm"
		}
		if fi, err := os.Stat(cacheFilePath); err == nil && time.Since(fi.ModTime()) <= assetCacheTTL {
			p.mirrorCachedFile(target, cacheFilePath)
			return true, true, 200, ""
		}
		return false, false, 0, "in-flight warm did not produce a cache entry"
	}
	inflightCh := make(chan struct{})
	p.warmInflight[target] = inflightCh
	p.warmMu.Unlock()
	defer func() {
		p.warmMu.Lock()
		delete(p.warmInflight, target)
		p.warmMu.Unlock()
		close(inflightCh)
	}()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return false, false, 0, "request: " + err.Error()
	}
	req.Header.Set("User-Agent", p.ua)
	req.Header.Set("Referer", p.referer)
	req.Header.Set("Origin", p.refererRoot)
	req.Header.Set("Accept", "*/*")
	// Client priority: while the game has relay requests in flight, hold
	// speculative warm fetches back so a slow or blocked asset can never
	// delay a gameplay load. Bounded wait, then proceed rather than stall
	// the warm forever.
	for i := 0; i < 100 && atomic.LoadInt64(&p.clientInflight) > 0; i++ {
		select {
		case <-ctx.Done():
			return false, false, 0, "canceled while yielding to client traffic"
		case <-time.After(50 * time.Millisecond):
		}
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return false, false, 0, "fetch: " + err.Error()
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		p.negCacheStore(target, resp.StatusCode)
		return false, false, resp.StatusCode, ""
	}
	p.negCacheClear(target)
	tmpPath := fmt.Sprintf("%s.%d.%d.tmp", cacheFilePath, os.Getpid(), time.Now().UnixNano())
	tf, err := os.Create(tmpPath)
	if err != nil {
		return false, false, resp.StatusCode, "create tmp: " + err.Error()
	}
	written, copyErr := io.Copy(tf, io.LimitReader(resp.Body, 64<<20))
	_ = tf.Close()
	if copyErr != nil || written == 0 || (resp.ContentLength > 0 && written != resp.ContentLength) {
		_ = os.Remove(tmpPath)
		return false, false, resp.StatusCode, fmt.Sprintf("copy: err=%v written=%d contentLength=%d", copyErr, written, resp.ContentLength)
	}
	if err := atomicReplaceFile(tmpPath, cacheFilePath); err != nil {
		_ = os.Remove(tmpPath)
		return false, false, resp.StatusCode, "replace: " + err.Error()
	}
	if ct := resp.Header.Get("Content-Type"); ct != "" {
		_ = os.WriteFile(cacheFilePath+".meta", []byte(ct), 0644)
	}
	p.mirrorCachedFile(target, cacheFilePath)
	return true, false, resp.StatusCode, ""
}

// ---------------------------------------------------------------------------
// Neighbour map prefetch
//
// The client renders a map from three things fetched over HTTP: the map JSON
// (maps/<id>.json), the ground/decor tiles stitched from atlasLayout
// (gfx/world/png/<gid>.png, or jpg/) and assorted sprites. The map JSON
// already contains the exact neighbours of the map (top/bottom/left/right),
// so when a map is loaded we can warm the cache for the maps reachable by
// walking: their JSONs (a few KB) plus, for direct neighbours, the tile PNGs
// that make first entry slow. Everything lands in the same shared disk cache
// used by the relay, so the client's own requests hit the cache (~ms) instead
// of round-tripping to the CDN, and repeated visits stay in the browser cache.
// ---------------------------------------------------------------------------

const (
	prefetchCooldown        = 20 * time.Second
	prefetchQueueSize       = 1024
	prefetchMaxTilesPerMap  = 600
	prefetchMapJSONReadMax  = 4 << 20
	// prefetchNegativeTTL is how long a deterministic upstream denial (403
	// S3 AccessDenied / 404) is remembered before the target is retried. Some
	// map ids answer 403 on every path, so re-requesting them only steals
	// connection slots from live client traffic.
	prefetchNegativeTTL = 10 * time.Minute
)

var (
	reMapJSONPath = regexp.MustCompile(`/maps/(\d+)\.json$`)
	reMapHeader   = regexp.MustCompile(`^\{"id":(\d+),"topNeighbourId":(-?\d+),"bottomNeighbourId":(-?\d+),"leftNeighbourId":(-?\d+),"rightNeighbourId":(-?\d+)`)
	reAtlasEntry  = regexp.MustCompile(`"(\d+)":\{([^{}]*)\}`)
)

type prefetchJob struct {
	target string
	id     int64
	depth  int
}

// negEntry is one negative-cache record: the upstream status that failed and
// when it was observed.
type negEntry struct {
	status int
	at     time.Time
}

// mapJSONID extracts the numeric map id from an asset path ending in
// /maps/<id>.json. Returns 0 when the path is not a map JSON.
func mapJSONID(path string) int64 {
	if m := reMapJSONPath.FindStringSubmatch(path); m != nil {
		if id, err := strconv.ParseInt(m[1], 10, 64); err == nil {
			return id
		}
	}
	return 0
}

// mapJSONNeighbours returns the walkable neighbours (top, bottom, left,
// right) declared in the map JSON header, deduplicated and in that order.
func mapJSONNeighbours(body []byte) []int64 {
	head := body
	if len(head) > 300 {
		head = head[:300]
	}
	m := reMapHeader.FindSubmatch(head)
	if m == nil {
		return nil
	}
	var out []int64
	seen := make(map[int64]struct{}, 4)
	for _, g := range m[2:] {
		id, err := strconv.ParseInt(string(g), 10, 64)
		if err != nil || id <= 0 {
			continue
		}
		if _, dup := seen[id]; dup {
			continue
		}
		seen[id] = struct{}{}
		out = append(out, id)
	}
	return out
}

// mapTileURLs lists the tile image URLs a map's atlasLayout references.
// atlasLayout.graphicsPositions is a map of "<gid>":{"sx":..} entries; the
// renderer loads each as gfx/world/<png|jpg>/<gid>.<png|jpg> ("jpg":1 entries
// live under jpg/). prefix is the asset base of the owning map, e.g.
// https://host/assets/<version>.
func mapTileURLs(body []byte, prefix string) []string {
	matches := reAtlasEntry.FindAllSubmatch(body, prefetchMaxTilesPerMap+1)
	out := make([]string, 0, len(matches))
	for _, m := range matches {
		if !bytes.Contains(m[2], []byte(`"sx"`)) {
			continue
		}
		dir, ext := "png", ".png"
		if bytes.Contains(m[2], []byte(`"jpg"`)) {
			dir, ext = "jpg", ".jpg"
		}
		out = append(out, prefix+"/gfx/world/"+dir+"/"+string(m[1])+ext)
		if len(out) == prefetchMaxTilesPerMap {
			break
		}
	}
	return out
}

func (p *ProxyServer) prefetchLog(format string, args ...interface{}) {
	line := fmt.Sprintf(format, args...)
	if !p.quiet {
		log.Printf("%s", line)
	}
	if p.cacheDir == "" {
		return
	}
	p.prefetchLogMu.Lock()
	defer p.prefetchLogMu.Unlock()
	if p.prefetchLogFile == nil {
		f, err := os.OpenFile(filepath.Join(p.cacheDir, "prefetch.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil {
			return
		}
		p.prefetchLogFile = f
	}
	_, err := p.prefetchLogFile.WriteString(time.Now().Format("2006-01-02 15:04:05") + " " + line + "\n")
	if err != nil {
		_ = p.prefetchLogFile.Close()
		p.prefetchLogFile = nil
	}
}

// requestLog appends one line per CDN asset request handled by the relay to
// <cacheDir>/requests.log (rotated past 16 MB). This is the ground truth for
// post-session debugging: cache hits, slow misses, 403/404 storms and stalls
// all show up with the exact URL. A persistent O_APPEND handle keeps the hot
// path at one write per line; rotation is tracked in memory and applied on
// the next open, so no stat is needed per line.
func (p *ProxyServer) requestLog(format string, args ...interface{}) {
	if p.cacheDir == "" {
		return
	}
	logPath := filepath.Join(p.cacheDir, "requests.log")
	p.prefetchLogMu.Lock()
	defer p.prefetchLogMu.Unlock()
	if p.reqLogFile == nil {
		var size int64
		if fi, err := os.Stat(logPath); err == nil {
			size = fi.Size()
		}
		if size > 16<<20 {
			_ = os.Remove(logPath + ".1")
			if err := os.Rename(logPath, logPath+".1"); err == nil {
				size = 0
				p.reqLogRotateAt = 16 << 20
			} else {
				// Cannot rotate (e.g. another relay instance holds the file):
				// keep appending but retry only after another 4 MB instead of
				// closing and re-opening on every line.
				p.reqLogRotateAt = size + 4<<20
			}
		} else {
			p.reqLogRotateAt = 16 << 20
		}
		f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil {
			return
		}
		p.reqLogFile = f
		p.reqLogSize = size
	}
	line := time.Now().Format("15:04:05.000") + " " + fmt.Sprintf(format, args...) + "\n"
	n, err := p.reqLogFile.WriteString(line)
	if err != nil {
		_ = p.reqLogFile.Close()
		p.reqLogFile = nil
		return
	}
	p.reqLogSize += int64(n)
	if p.reqLogSize > p.reqLogRotateAt {
		// Close now; the next call re-opens and retries the rename rotation.
		_ = p.reqLogFile.Close()
		p.reqLogFile = nil
	}
}

// negCacheHit reports whether target recently failed upstream with a
// deterministic 4xx (403/404), returning the status that was seen. Entries
// expire after prefetchNegativeTTL.
func (p *ProxyServer) negCacheHit(target string) (int, bool) {
	p.negMu.Lock()
	defer p.negMu.Unlock()
	e, ok := p.negCache[target]
	if !ok {
		return 0, false
	}
	if time.Since(e.at) > prefetchNegativeTTL {
		delete(p.negCache, target)
		return 0, false
	}
	return e.status, true
}

// negCacheStore records a deterministic upstream denial (403/404 only).
func (p *ProxyServer) negCacheStore(target string, status int) {
	if status != http.StatusForbidden && status != http.StatusNotFound {
		return
	}
	now := time.Now()
	p.negMu.Lock()
	defer p.negMu.Unlock()
	if p.negCache == nil {
		p.negCache = make(map[string]negEntry)
	}
	if len(p.negCache) > 4096 {
		for k, v := range p.negCache {
			if now.Sub(v.at) > prefetchNegativeTTL {
				delete(p.negCache, k)
			}
		}
		if len(p.negCache) > 4096 {
			p.negCache = make(map[string]negEntry)
		}
	}
	p.negCache[target] = negEntry{status: status, at: now}
}

// negCacheClear drops a negative entry (any successful fetch of the target).
func (p *ProxyServer) negCacheClear(target string) {
	p.negMu.Lock()
	delete(p.negCache, target)
	p.negMu.Unlock()
}

// scheduleMapPrefetch enqueues a map node for warming. Called whenever a map
// JSON travels through the proxy/relay; a cooldown per map id keeps loops
// (root -> neighbour -> root) from re-enqueueing the same node in a burst.
// Recently denied ids are skipped outright (negative cache).
func (p *ProxyServer) scheduleMapPrefetch(target string, id int64) {
	if !p.prefetch || p.cacheDir == "" || id <= 0 {
		return
	}
	if _, bad := p.negCacheHit(target); bad {
		return
	}
	p.prefetchMu.Lock()
	if p.prefetchSeen == nil {
		p.prefetchSeen = make(map[int64]time.Time)
	}
	if last, ok := p.prefetchSeen[id]; ok && time.Since(last) < prefetchCooldown {
		p.prefetchMu.Unlock()
		return
	}
	if len(p.prefetchSeen) > 8192 {
		now := time.Now()
		for k, v := range p.prefetchSeen {
			if now.Sub(v) > time.Hour {
				delete(p.prefetchSeen, k)
			}
		}
	}
	p.prefetchSeen[id] = time.Now()
	p.prefetchMu.Unlock()

	p.prefetchOnce.Do(p.startPrefetchWorkers)
	select {
	case p.prefetchQ <- prefetchJob{target: target, id: id, depth: 0}:
	default: // queue full: drop, the map was served moments ago anyway
	}
}

func (p *ProxyServer) startPrefetchWorkers() {
	p.prefetchQ = make(chan prefetchJob, prefetchQueueSize)
	n := p.prefetchWorkers
	if n < 1 {
		n = 1
	}
	for i := 0; i < n; i++ {
		go p.prefetchWorker()
	}
}

func (p *ProxyServer) prefetchWorker() {
	ctx := context.Background()
	for job := range p.prefetchQ {
		p.processPrefetchJob(ctx, job)
	}
}

// processPrefetchJob warms one node of the neighbour graph: ensures the map
// JSON is cached, warms its tiles (depth 0 = the map being loaded, up to
// prefetchTilesDepth), then enqueues its own neighbours until prefetchRadius
// is reached.
func (p *ProxyServer) processPrefetchJob(ctx context.Context, job prefetchJob) {
	mapURL := job.target
	cacheFilePath := p.assetCachePath(mapURL)
	if _, err := os.Stat(cacheFilePath); err != nil {
		ok, _, status, errText := p.warmAssetCacheDetail(ctx, mapURL)
		if !ok {
			p.prefetchLog("[PREFETCH] map %d json fetch failed status=%d err=%q", job.id, status, errText)
			return
		}
	}
	f, err := os.Open(cacheFilePath)
	if err != nil {
		return
	}
	body, err := io.ReadAll(io.LimitReader(f, prefetchMapJSONReadMax))
	_ = f.Close()
	if err != nil || len(body) == 0 {
		return
	}

	prefix := strings.TrimSuffix(mapURL, "/maps/"+strconv.FormatInt(job.id, 10)+".json")
	neighbours := mapJSONNeighbours(body)

	newTiles, cachedTiles, totalTiles := 0, 0, 0
	if job.depth <= p.prefetchTilesDepth {
		for _, tileURL := range mapTileURLs(body, prefix) {
			totalTiles++
			ok, already := p.warmAssetCache(ctx, tileURL)
			if already {
				cachedTiles++
			} else if ok {
				newTiles++
			}
		}
	}
	p.prefetchLog("[PREFETCH] map %d d%d neighbours=%d tiles new=%d cached=%d total=%d",
		job.id, job.depth, len(neighbours), newTiles, cachedTiles, totalTiles)

	if job.depth >= p.prefetchRadius {
		return
	}
	for _, nid := range neighbours {
		p.prefetchMu.Lock()
		last, ok := p.prefetchSeen[nid]
		skip := ok && time.Since(last) < prefetchCooldown
		if !skip {
			p.prefetchSeen[nid] = time.Now()
		}
		p.prefetchMu.Unlock()
		if skip {
			continue
		}
		nTarget := prefix + "/maps/" + strconv.FormatInt(nid, 10) + ".json"
		select {
		case p.prefetchQ <- prefetchJob{target: nTarget, id: nid, depth: job.depth + 1}:
		default:
		}
	}
}

// feedMapIDs iterates the map id ranges in spec ("1-20000,7442,9000-9100"),
// calling fn for each id until fn returns false. Lazy: O(1) extra space
// regardless of range size.
func feedMapIDs(spec string, fn func(id int) bool) {
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		lo, hi := 0, 0
		if dash := strings.IndexByte(part, '-'); dash > 0 {
			lo, _ = strconv.Atoi(strings.TrimSpace(part[:dash]))
			hi, _ = strconv.Atoi(strings.TrimSpace(part[dash+1:]))
		} else {
			lo, _ = strconv.Atoi(part)
			hi = lo
		}
		if lo < 1 {
			continue
		}
		if hi < lo {
			hi = lo
		}
		if hi > 2000000000 {
			hi = 2000000000
		}
		if hi-lo > 200000 {
			hi = lo + 200000
		}
		for id := lo; id <= hi; id++ {
			if !fn(id) {
				return
			}
		}
	}
}

// precacheMaps warms the shared asset cache with every map JSON in spec using
// a small worker pool. Already-cached maps are skipped with a single os.Stat,
// so repeat runs are nearly free. Runs until done or ctx cancellation.
func (p *ProxyServer) precacheMaps(ctx context.Context, assetsURL, spec string, workers int) {
	if assetsURL == "" || spec == "" {
		return
	}
	if workers < 1 {
		workers = 1
	}
	base := strings.TrimRight(assetsURL, "/")
	log.Printf("[PRECACHE] Warming maps from %s/maps/*.json (workers=%d)", base, workers)

	jobs := make(chan int)
	var fetched, cached, failed, total int64
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for id := range jobs {
				if ctx.Err() != nil {
					continue // drain so the feeder never blocks
				}
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
			}
		}()
	}
	feedMapIDs(spec, func(id int) bool {
		if ctx.Err() != nil {
			return false
		}
		jobs <- id
		return true
	})
	close(jobs)
	wg.Wait()
	log.Printf("[PRECACHE] done: total=%d fetched=%d cached=%d failed=%d",
		atomic.LoadInt64(&total), atomic.LoadInt64(&fetched), atomic.LoadInt64(&cached), atomic.LoadInt64(&failed))
}

// sleepCtx sleeps for d unless ctx is cancelled first; reports whether the
// sleep completed.
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

// parseCrawlSeeds expands a map id spec ("150737668,7442,1-50") into a seed
// slice for -crawl. Note: map ids in Dofus Touch are 9-digit, so this cannot
// reuse feedMapIDs (which clamps to 2,000,000 for the legacy -precache-maps
// ranges).
func parseCrawlSeeds(spec string) []int64 {
	var out []int64
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if dash := strings.IndexByte(part, '-'); dash > 0 {
			lo, errLo := strconv.ParseInt(strings.TrimSpace(part[:dash]), 10, 64)
			hi, errHi := strconv.ParseInt(strings.TrimSpace(part[dash+1:]), 10, 64)
			if errLo != nil || errHi != nil || lo < 1 {
				continue
			}
			if hi < lo {
				hi = lo
			}
			if hi-lo > 4096 {
				hi = lo + 4096
			}
			for id := lo; id <= hi; id++ {
				out = append(out, id)
				if len(out) >= 4096 {
					return out
				}
			}
		} else {
			id, err := strconv.ParseInt(part, 10, 64)
			if err != nil || id < 1 {
				continue
			}
			out = append(out, id)
			if len(out) >= 4096 {
				return out
			}
		}
	}
	return out
}

// runMapCrawler walks the map neighbour graph breadth-first from seeds,
// warming each map's JSON (and optionally its tiles) into the shared cache at
// a paced rate so the CDN sees a friendly, steady request stream. Already
// cached maps are skipped with a stat, so repeat runs are nearly free and a
// later run only fetches what is still missing. Runs until the queue drains,
// -crawl-max maps are visited, or the process is stopped.
func (p *ProxyServer) runMapCrawler(ctx context.Context, assetsURL string, seeds []int64, maxMaps int, rate float64, withTiles bool) {
	if p.cacheDir == "" {
		p.prefetchLog("[CRAWL] no -cache directory; skipping")
		return
	}
	base := strings.TrimRight(assetsURL, "/")
	if base == "" {
		return
	}
	if rate <= 0 {
		rate = 1
	}
	if rate > 50 {
		rate = 50
	}
	interval := time.Duration(float64(time.Second) / rate)
	if interval < 20*time.Millisecond {
		interval = 20 * time.Millisecond
	}
	p.prefetchLog("[CRAWL] start base=%s seeds=%d max=%d rate=%.1f/s tiles=%t", base, len(seeds), maxMaps, rate, withTiles)

	visited := make(map[int64]struct{}, 8192)
	queue := append([]int64(nil), seeds...)
	fetched, reused, failed := 0, 0, 0
	for len(queue) > 0 && ctx.Err() == nil {
		if maxMaps > 0 && len(visited) >= maxMaps {
			break
		}
		id := queue[0]
		queue = queue[1:]
		if id <= 0 {
			continue
		}
		if _, dup := visited[id]; dup {
			continue
		}
		visited[id] = struct{}{}

		mapURL := base + "/maps/" + strconv.FormatInt(id, 10) + ".json"
		cacheFilePath := p.assetCachePath(mapURL)
		if _, err := os.Stat(cacheFilePath); err != nil {
			if !sleepCtx(ctx, interval) {
				break
			}
			ok, _, status, errText := p.warmAssetCacheDetail(ctx, mapURL)
			if !ok {
				failed++
				p.prefetchLog("[CRAWL] map %d json failed status=%d err=%q", id, status, errText)
				continue
			}
			fetched++
		} else {
			reused++
			p.mirrorCachedFile(mapURL, cacheFilePath)
		}

		f, err := os.Open(cacheFilePath)
		if err != nil {
			continue
		}
		body, err := io.ReadAll(io.LimitReader(f, prefetchMapJSONReadMax))
		_ = f.Close()
		if err != nil || len(body) == 0 {
			continue
		}
		for _, nid := range mapJSONNeighbours(body) {
			if _, dup := visited[nid]; !dup {
				queue = append(queue, nid)
			}
		}
		if withTiles {
			prefix := strings.TrimSuffix(mapURL, "/maps/"+strconv.FormatInt(id, 10)+".json")
			for _, tileURL := range mapTileURLs(body, prefix) {
				if ctx.Err() != nil {
					break
				}
				if _, err := os.Stat(p.assetCachePath(tileURL)); err == nil {
					continue
				}
				if !sleepCtx(ctx, interval) {
					break
				}
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
	if p.cacheDir != "" && r.Method == http.MethodGet && isCacheAssetURL(targetStr) && !isVolatileCacheURL(targetStr) {
		cacheFilePath = p.assetCachePath(targetStr)
		// serveCachedAsset handles Content-Length, Range requests,
		// If-None-Match/If-Modified-Since (ETag derived from
		// modtime+size) and streams from the open file in pooled
		// chunks - no extra Stat, no manual Content-Length math, and
		// repeat visits can be answered with a 304.
		if p.serveCachedAsset(w, r, cacheFilePath, targetURL.Path) {
			p.mirrorCachedFile(targetStr, cacheFilePath)
			if id := mapJSONID(targetURL.Path); id > 0 {
				p.scheduleMapPrefetch(targetStr, id)
			}
			return
		}
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
	if resp.StatusCode == http.StatusOK && cacheFilePath != "" {
		setLongAssetCacheHeaders(wh)
	}

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
				p.mirrorCachedFile(targetStr, cacheFilePath)
				if id := mapJSONID(targetURL.Path); id > 0 {
					p.scheduleMapPrefetch(targetStr, id)
				}
			} else {
				_ = os.Remove(tmpPath)
			}
			return
		}
	}

	_, _ = io.CopyBuffer(w, bodyReader, buf)
}

// configArgValue finds "-name value" or "-name=value" in args. Used only for
// -config, which has to be read before flag.Parse so its values can become the
// defaults of every other flag.
func configArgValue(args []string, name string) string {
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "-"+name || a == "--"+name {
			if i+1 < len(args) {
				return args[i+1]
			}
		}
		for _, prefix := range []string{"-" + name + "=", "--" + name + "="} {
			if strings.HasPrefix(a, prefix) {
				return strings.TrimPrefix(a, prefix)
			}
		}
	}
	return ""
}

// loadIniConfig reads a flat key=value file. Sections ([proxy]) are accepted and
// ignored, so the same file can hold other settings. Missing file = empty map,
// never an error: -config is a convenience, the flags still work alone.
func loadIniConfig(path string) map[string]string {
	out := map[string]string{}
	if path == "" {
		return out
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		log.Printf("[CONFIG] %s not readable (%v); using flags only", path, err)
		return out
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") || strings.HasPrefix(line, "[") {
			continue
		}
		eq := strings.Index(line, "=")
		if eq <= 0 {
			continue
		}
		key := strings.ToLower(strings.TrimSpace(line[:eq]))
		val := strings.TrimSpace(line[eq+1:])
		out[key] = val
	}
	return out
}

func main() {
	// -config lets one file describe the whole relay, so it can be configured
	// without the GUI (and without editing the launch command).
	cfgPath := configArgValue(os.Args[1:], "config")
	cfg := loadIniConfig(cfgPath)
	defStr := func(key, fallback string) string {
		if v, ok := cfg[key]; ok && v != "" {
			return v
		}
		return fallback
	}
	defInt := func(key string, fallback int) int {
		if v, ok := cfg[key]; ok && v != "" {
			if n, err := strconv.Atoi(v); err == nil {
				return n
			}
		}
		return fallback
	}
	defBool := func(key string, fallback bool) bool {
		if v, ok := cfg[key]; ok && v != "" {
			switch strings.ToLower(v) {
			case "1", "true", "yes", "on":
				return true
			case "0", "false", "no", "off":
				return false
			}
		}
		return fallback
	}

	port := flag.Int("port", defInt("port", 8880), "Local listening port for HTTP/CONNECT proxy")
	host := flag.String("host", defStr("host", "127.0.0.1"), "Host address to bind the proxy server to")
	upstream := flag.String("upstream", defStr("upstream", ""), "Upstream proxy URL, or a comma-separated chain of them (http://ip:port or socks5://ip:port)")
	exitProxy := flag.String("exit-proxy", defStr("exit_proxy", ""), "Fixed exit proxy reached through -upstream hops. Keeps one exit IP across hop failures.")
	dnsServer := flag.String("dns", defStr("dns", ""), "Custom DNS server to query (optional, default: system DNS)")
	cacheDir := flag.String("cache", defStr("cache", "proxy_cache"), "Directory to cache static game assets (optional)")
	quiet := flag.Bool("quiet", defBool("quiet", false), "Quiet mode (suppress request logs)")
	logPath := flag.String("log", defStr("log", ""), "Explicit log file (default: <cache>/proxy.log when -quiet is set)")
	allowDirect := flag.Bool("allow-direct", defBool("allow_direct", false),
		"Fall back to a DIRECT connection when the upstream dies (EXPOSES THE REAL IP; off by default)")
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
	if *loginHostFlag != "" {
		loginHost = *loginHostFlag
	}

	log.SetFlags(log.Ldate | log.Ltime)
	// -quiet silences the console, which is right for a background proxy but made
	// every failure invisible: the host launches this with -quiet, so an upstream
	// refusal left no trace anywhere. Keep it quiet on the console and write to a
	// file next to the cache instead.
	if *configFlag != "" {
		log.Printf("[CONFIG] loaded %s", *configFlag)
	}
	target := *logPath
	if target == "" && *quiet && *cacheDir != "" {
		target = filepath.Join(*cacheDir, "proxy.log")
	}
	if target != "" {
		if dir := filepath.Dir(target); dir != "" {
			_ = os.MkdirAll(dir, 0o755)
		}
		if f, err := os.OpenFile(target, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644); err == nil {
			log.SetOutput(f)
			log.Printf("[CONFIG] logging to %s", target)
		} else {
			log.Printf("[CONFIG] cannot open %s: %v", target, err)
		}
	}
	resolver := NewFastDNSResolver(*dnsServer, 10*time.Minute)

	// Upstream health check. The upstream is Cloudflare WARP's SOCKS proxy on
	// 127.0.0.1:40000, which only listens while WARP is connected *and* in proxy
	// mode. It is not optional: the whole point of WARP here is to keep the real
	// IP away from Ankama, so a missing upstream must stop the proxy rather than
	// silently connect direct. Failing loudly costs a relaunch; failing open
	// costs the user's address.
	// Upstream health check.
	//
	// The upstream is Cloudflare WARP's SOCKS proxy on 127.0.0.1:40000, which only
	// listens while WARP is connected *and* in proxy mode. It is not optional:
	// the whole point of WARP here is to keep the real IP away from Ankama.
	//
	// It used to `os.Exit(2)` when the upstream was down. That was wrong in one
	// specific, repeatable way: WARP drops its tunnel whenever the machine changes
	// network (wifi <-> ethernet), then takes tens of seconds to reconnect. Exiting
	// turned a 40-second outage into a dead instance that showed the client's
	// "Server busy" screen, and the user had to relaunch by hand.
	//
	// Now: keep listening and keep failing closed. The port is open, so the host's
	// readiness probe succeeds and no in-process fallback is started; every dial is
	// still refused while the upstream is down, so nothing can leak; and the
	// instance starts working by itself the moment WARP returns.
	// With a fixed exit the chain decides reachability per dial, so this single-upstream gate
	// must not run: `-upstream` is a comma-separated hop list now, url.Parse on the whole string
	// fails, and the relay would mark itself unreachable at boot and refuse every dial — which
	// is a black screen with no explanation anywhere except this log.
	if *upstream != "" && strings.TrimSpace(*exitProxy) == "" {
		firstUpstream := *upstream
		if i := strings.Index(firstUpstream, ","); i >= 0 {
			firstUpstream = firstUpstream[:i]
		}
		if u, err := url.Parse(strings.TrimSpace(firstUpstream)); err == nil {
			if ok, dialErr := upstreamReachable(u, 1500*time.Millisecond); ok {
				upstreamHealthy.Store(true)
				log.Printf("[UPSTREAM] %s reachable", *upstream)
			} else {
				upstreamHealthy.Store(false)
				log.Printf("[UPSTREAM] %s is NOT reachable (%v).", *upstream, dialErr)
				log.Printf("[UPSTREAM] Listening anyway and failing closed: every dial is refused until " +
					"WARP is back, so no request can go direct and the real IP stays hidden.")
				log.Printf("[UPSTREAM] WARP drops its tunnel on a network change (wifi/ethernet) and usually " +
					"returns within a minute. Fix now: warp-cli mode proxy && warp-cli connect")
				go watchUpstream(u, *upstream)
			}
		}
	}
	proxy := NewProxyServer(resolver, *upstream, *cacheDir, *quiet, *port, *token, *serial, *uuid, *model, *manufacturer, *ua, *glVendor, *glRenderer, *maxTextureSize, *referer)
	// The hop index is loaded before the first request, so a restart does not start blind.
	proxy.hopIdxPath = hopIndexPath(*cacheDir)
	proxy.loadHopIndex()
	go func() {
		for {
			time.Sleep(10 * time.Second)
			proxy.saveHopIndex()
		}
	}()
	defer proxy.saveHopIndex()

	// Bound the heap instead of letting it grow between collections.
	//
	// The relay caches static assets and prefetches neighbours, so the live heap is mostly
	// reachable data and the default collector happily doubles it before running. That is the
	// spike: a soft memory limit makes the collector work earlier as the heap approaches it, and
	// a lower GOGC keeps the steady state tighter. The cost is a little more CPU, which this
	// process has spare.
	// Limit only, no GOGC override.
	//
	// Setting GOGC to 50 as well bounded the peak but made long sessions slower: with a large
	// live asset cache the collector ran constantly and every request paid for it. The soft
	// memory limit alone does what is actually wanted — normal collection until the heap
	// approaches the ceiling, then collect harder.
	debug.SetMemoryLimit(384 << 20)

	if trimmed := strings.TrimSpace(*exitProxy); trimmed != "" {
		proxy.exitURL = parseUpstreamProxy(trimmed)
		if proxy.exitURL == nil {
			log.Printf("[UPSTREAM] -exit-proxy %q is not a usable proxy URL; ignoring it", trimmed)
		} else {
			proxy.validateExitAtStartup()
		}
	}
	proxy.allowDirect = *allowDirect
	if *allowDirect {
		log.Printf("[UPSTREAM] -allow-direct is ON: if the tunnel dies, traffic will go direct and " +
			"Ankama will see this machine's real IP.")
	}
	proxy.prefetch = *prefetchNeighbours
	proxy.prefetchRadius = *prefetchRadius
	if proxy.prefetchRadius < 1 {
		proxy.prefetchRadius = 1
	}
	proxy.prefetchTilesDepth = *prefetchTilesDepth
	if proxy.prefetchTilesDepth < 0 {
		proxy.prefetchTilesDepth = 0
	}
	proxy.prefetchWorkers = *prefetchWorkers
	if proxy.prefetchWorkers < 1 {
		proxy.prefetchWorkers = 1
	}
	if proxy.prefetchWorkers > 16 {
		proxy.prefetchWorkers = 16
	}
	if proxy.prefetch && proxy.cacheDir != "" {
		log.Printf("[PREFETCH] Neighbour prefetch enabled (radius=%d tiles-depth=%d workers=%d)",
			proxy.prefetchRadius, proxy.prefetchTilesDepth, proxy.prefetchWorkers)
	}

	runCtx, runCancel := context.WithCancel(context.Background())
	defer runCancel()
	if *precacheSpec != "" {
		if *assetsURL == "" {
			log.Printf("[PRECACHE] -precache-maps set without -assets-url; skipping")
		} else {
			go proxy.precacheMaps(runCtx, *assetsURL, *precacheSpec, *precacheWorkers)
		}
	}

	if *modsDir == "" && *cacheDir != "" {
		*modsDir = filepath.Join(*cacheDir, "mods")
	}
	proxy.modsDir = *modsDir
	if *modsDir != "" {
		proxy.loadModsIfChanged()
	}

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

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		log.Printf("[READY] Listening on http://%s:%d (Model: %s) ...", *host, *port, *model)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[FATAL] Proxy server failed: %v", err)
		}
	}()

	<-sigChan
	runCancel()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
}
