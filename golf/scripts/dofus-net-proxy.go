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
	xproxy "golang.org/x/net/proxy"
	"golang.org/x/sync/errgroup"
	"golang.org/x/sync/singleflight"
	"golang.org/x/time/rate"
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
)

const oe = 1<<63 - 1

var (
	bp            = sync.Pool{New: func() any { return make([]byte, 32*1024) }}
	qe            sync.Once
	vc            sync.Once
	gp            = "dt-proxy-production-login.ankama-games.com"
	tu            = regexp.MustCompile(`Android\s+([0-9.]+)`)
	tt            = regexp.MustCompile(`Build/([^\s;)]+)`)
	ux            = []byte("HTTP/1.1 200 Connection Established\r\n\r\n")
	dt            atomic.Bool
	qk            uint32
	hopReqHeaders = nf(
		"Proxy-Connection", "Proxy-Authenticate", "Proxy-Authorization",
		"Via", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
		"Forwarded", "X-Real-IP", "Client-IP", "X-Requested-With",
		"Connection", "Keep-Alive", "Te", "Trailers", "Transfer-Encoding", "Upgrade",
	)
	nh = nf(
		"Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
		"Te", "Trailers", "Transfer-Encoding", "Upgrade",
		"Content-Security-Policy", "Content-Security-Policy-Report-Only",
	)
	rm = map[string]string{
		".wasm": "application/wasm", ".json": "application/json; charset=utf-8",
		".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
		".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".js": "application/javascript; charset=utf-8",
		".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
	}
	rp = [...]string{".ankama.com", ".ankama-games.com", ".dofus-touch.com"}
	qo = [...]string{"Cookie", "Range", "If-None-Match", "If-Modified-Since"}
	uy = [...]string{"mozilla/", "x86", "sdk", "emulator", "goldfish", "ranchu"}
)

const (
	mt = 90 * time.Second
	vd = 6
	je = 7 * 24 * time.Hour
	pb = 20 * time.Second
	tp = 1024
	kl = 600
	pc = 4 << 20
	pd = 10 * time.Minute
)

func nf(ns ...string) map[string]struct{} {
	m := make(map[string]struct{}, len(ns))
	for _, k := range ns {
		m[http.CanonicalHeaderKey(k)] = struct{}{}
	}
	return m
}
func ie(c byte) byte {
	if c >= 'A' && c <= 'Z' {
		return c + 'a' - 'A'
	}
	return c
}
func nj(s, cz string) int {
	n := len(cz)
	for i := 0; i+n <= len(s); i++ {
		if ie(s[i]) != cz[0] {
			continue
		}
		j := 1
		for ; j < n && ie(s[i+j]) == cz[j]; j++ {
		}
		if j == n {
			return i
		}
	}
	return -1
}
func qv(s, cz string) bool { return nj(s, cz) >= 0 }
func sa(s, lc string) bool {
	return len(s) >= len(lc) && nj(s[len(s)-len(lc):], lc) == 0
}
func ju(b []byte, cz string) int {
	n := len(cz)
	for i := 0; i+n <= len(b); i++ {
		if ie(b[i]) != cz[0] {
			continue
		}
		j := 1
		for ; j < n && ie(b[i+j]) == cz[j]; j++ {
		}
		if j == n {
			return i
		}
	}
	return -1
}
func mc(b []byte, s string) ([]byte, bool) {
	if len(b) < len(s) {
		return b, false
	}
	for i := 0; i < len(s); i++ {
		if b[i] != s[i] {
			return b, false
		}
	}
	return b[len(s):], true
}
func pq(b []byte, ul bool) (int64, []byte, bool) {
	ol := false
	if ul && len(b) > 0 && b[0] == '-' {
		ol, b = true, b[1:]
	}
	i, v, bk := 0, int64(0), false
	for i < len(b) && b[i] >= '0' && b[i] <= '9' {
		d := int64(b[i] - '0')
		if bk || v > (oe-d)/10 {
			bk = true
		} else {
			v = v*10 + d
		}
		i++
	}
	if i == 0 {
		return 0, b, false
	}
	if bk {
		return -1, b[i:], true
	}
	if ol {
		v = -v
	}
	return v, b[i:], true
}
func hz(dp string) bool {
	return len(dp) >= 6 && strings.EqualFold(dp[:6], "socks5")
}

type qt interface{ CloseWrite() error }
type lw struct{ net.Conn }

func (d lw) Dial(_, _ string) (net.Conn, error) { return d.Conn, nil }

type DNSCacheEntry struct {
	IPs       []net.IP
	ExpiresAt time.Time
}
type FastDNSResolver struct {
	mu sync.RWMutex
	dy map[string]DNSCacheEntry
	sf singleflight.Group
	rr uint64
	jc time.Duration
	ed string
	ik *net.Resolver
}

func NewFastDNSResolver(ed string, jc time.Duration) *FastDNSResolver {
	r := &FastDNSResolver{
		dy: make(map[string]DNSCacheEntry, 128),
		jc: jc,
		ed: ed,
		ik: net.DefaultResolver,
	}
	if ed != "" {
		ed = eh(ed, "53")
		d := net.Dialer{Timeout: 3 * time.Second}
		r.ik = &net.Resolver{
			PreferGo: true,
			Dial: func(aa context.Context, _, _ string) (net.Conn, error) {
				return d.DialContext(aa, "udp", ed)
			},
		}
	}
	return r
}
func (r *FastDNSResolver) oy(by []net.IP) []net.IP {
	if len(by) <= 1 {
		return slices.Clone(by)
	}
	r.mu.Lock()
	ba := r.rr % uint64(len(by))
	r.rr++
	r.mu.Unlock()
	return []net.IP{by[ba]}
}
func (r *FastDNSResolver) Lookup(aa context.Context, ad string) ([]net.IP, error) {
	if h, _, y := net.SplitHostPort(ad); y == nil {
		ad = h
	}
	if ip := net.ParseIP(ad); ip != nil {
		return []net.IP{ip}, nil
	}
	cj := time.Now()
	r.mu.RLock()
	mo, el := r.dy[ad]
	r.mu.RUnlock()
	if el && cj.Before(mo.ExpiresAt) {
		return r.oy(mo.IPs), nil
	}
	v, y, _ := r.sf.Do(ad, func() (any, error) {
		by, y := r.ik.LookupIP(aa, "ip4", ad)
		if y != nil && r.ik != net.DefaultResolver {
			by, y = net.DefaultResolver.LookupIP(aa, "ip4", ad)
		}
		r.mu.Lock()
		defer r.mu.Unlock()
		if len(r.dy) >= 1024 {
			maps.DeleteFunc(r.dy, func(_ string, e DNSCacheEntry) bool { return cj.After(e.ExpiresAt) })
			if len(r.dy) >= 1024 {
				clear(r.dy)
			}
		}
		if y == nil {
			r.dy[ad] = DNSCacheEntry{IPs: by, ExpiresAt: cj.Add(r.jc)}
		}
		return by, y
	})
	if y != nil {
		return nil, y
	}
	return r.oy(v.([]net.IP)), nil
}
func (r *FastDNSResolver) kq(aa context.Context, av, de string) string {
	ad, gx, y := net.SplitHostPort(av)
	if y != nil {
		ad, gx = av, de
	}
	if r != nil {
		if by, y := r.Lookup(aa, ad); y == nil && len(by) > 0 {
			return net.JoinHostPort(by[0].String(), gx)
		}
	}
	return av
}
func stripWebViewTokens(ua string) string {
	if !strings.ContainsAny(ua, ";vV") {
		return ua
	}
	var b strings.Builder
	b.Grow(len(ua))
	for i := 0; i < len(ua); {
		if ua[i] == ';' {
			j := i + 1
			for j < len(ua) && strings.IndexByte(" \t\n\f\r", ua[j]) >= 0 {
				j++
			}
			if j+1 < len(ua) && (ua[j]|0x20) == 'w' && (ua[j+1]|0x20) == 'v' && !si(ua, j+2) {
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
				for j < len(ua) && strings.IndexByte(" \t\n\f\r", ua[j]) >= 0 {
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
func si(s string, i int) bool {
	if i >= len(s) {
		return false
	}
	c := s[i]
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_'
}
func isCacheAssetURL(u string) bool {
	if strings.Contains(u, "/assets/") {
		return true
	}
	ml := strings.LastIndexByte(u, '.')
	if ml < 0 {
		return false
	}
	cr := u[ml+1:]
	switch len(cr) {
	case 3:
		switch bv(cr) {
		case "png", "jpg", "mp3", "ogg", "zip", "pak", "dat":
			return true
		}
	case 4:
		switch bv(cr) {
		case "jpeg", "webp", "wasm", "json":
			return true
		}
	}
	return false
}
func ox(aq string) *url.URL {
	if aq = strings.TrimSpace(aq); aq == "" {
		return nil
	}
	dp, be := "http", aq
	if s, r, z := strings.Cut(aq, "://"); z {
		dp, be = strings.ToLower(s), r
	}
	if !strings.Contains(be, "@") {
		if p := strings.Split(be, ":"); len(p) == 4 {
			return &url.URL{Scheme: dp, Host: p[0] + ":" + p[1], User: url.UserPassword(p[2], p[3])}
		}
	}
	u, _ := url.Parse(dp + "://" + be)
	return u
}
func eh(av, de string) string {
	if !strings.Contains(av, ":") {
		return av + ":" + de
	}
	return av
}
func qf(u *url.URL, an time.Duration) (bool, error) {
	ac, y := net.DialTimeout("tcp", eh(u.Host, "1080"), an)
	if y != nil {
		return false, y
	}
	_ = ac.Close()
	return true, nil
}
func vf(u *url.URL, aq string) {
	for {
		time.Sleep(5 * time.Second)
		if z, y := qf(u, 1500*time.Millisecond); z && !dt.Swap(true) {
			log.Printf("[UPSTREAM] %s is reachable again; dials resume", aq)
		} else if !z && dt.Swap(false) {
			log.Printf("[UPSTREAM] %s went away (%v); failing closed until it returns", aq, y)
		}
	}
}
func uo(u *url.URL) *xproxy.Auth {
	if u == nil || u.User == nil {
		return nil
	}
	kg, _ := u.User.Password()
	return &xproxy.Auth{User: u.User.Username(), Password: kg}
}
func jm(pe *url.URL, pw string, an time.Duration) (net.Conn, error) {
	dc := &net.Dialer{Timeout: an, KeepAlive: 30 * time.Second}
	ee, y := xproxy.SOCKS5("tcp", eh(pe.Host, "1080"), uo(pe), dc)
	if y != nil {
		return nil, y
	}
	if cd, z := ee.(xproxy.ContextDialer); z {
		aa, gc := context.WithTimeout(context.Background(), an)
		defer gc()
		return cd.DialContext(aa, "tcp", pw)
	}
	return ee.Dial("tcp", pw)
}

type ProxyTelemetry struct {
	mu             sync.RWMutex
	gn             float64
	sm             time.Time
	fp             [32]float64
	ks, iv         int
	gg, gf, gb, ga uint64
	ec             int64
}

func (t *ProxyTelemetry) ty(pn float64, y error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.gg++
	if y != nil {
		t.gf++
		return
	}
	t.ec++
	t.gn, t.sm = pn, time.Now()
	t.fp[t.iv] = pn
	t.iv = (t.iv + 1) % len(t.fp)
	if t.ks < len(t.fp) {
		t.ks++
	}
}
func (t *ProxyTelemetry) tx(ps, pg int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.ec > 0 {
		t.ec--
	}
	if ps > 0 {
		t.gb += uint64(ps)
	}
	if pg > 0 {
		t.ga += uint64(pg)
	}
}

type TelemetrySnapshot struct {
	gn, ln         float64
	gg, gf, gb, ga uint64
	ec             int64
}

func (t *ProxyTelemetry) un() TelemetrySnapshot {
	t.mu.RLock()
	defer t.mu.RUnlock()
	lm := 0.0
	if n := t.ks; n > 0 {
		ix := 0.0
		for i := 1; i <= n; i++ {
			ix += t.fp[(t.iv-i+len(t.fp))%len(t.fp)]
		}
		lm = ix / float64(n)
	}
	return TelemetrySnapshot{
		gn: t.gn, ln: lm,
		gg: t.gg, ec: t.ec, gf: t.gf,
		gb: t.gb, ga: t.ga,
	}
}

type ff struct {
	z  bool
	ib time.Duration
	ji time.Time
}
type gy struct {
	af string
	id int64
	ey int
}
type ij struct {
	am int
	at time.Time
}
type sy struct {
	Op    string `json:"op"`
	Path  string `json:"path"`
	Value any    `json:"value"`
}
type gs struct {
	Match       string `json:"match"`
	Action      string `json:"action"`
	Status      int    `json:"status"`
	File        string `json:"file"`
	ContentType string `json:"contentType"`
	Patch       []sy   `json:"patch"`
}
type ProxyServer struct {
	cl                     *FastDNSResolver
	fs, fh, bm, qq         *url.URL
	da                     []*url.URL
	cm, hw, cu, me         sync.Mutex
	gw                     sync.Mutex
	iq                     sync.Mutex
	gv                     map[string]time.Time
	fc                     sync.Mutex
	gk                     map[*dg]struct{}
	hb, ir, fk, fl, ig     sync.Mutex
	ms, oi, oj             time.Time
	lq, lr                 uint64
	pz, bc                 int
	hc, hd, he             int
	bx                     map[string]ff
	em, ao, ew, cg         string
	fq, ft, bb, dh, ua, qz string
	gu, fz, fe, fd         string
	fj, cb, fo             string
	js, bt, fx, kk, kb     bool
	ez                     []string
	rf, to                 sync.Once
	jg                     string
	jn                     []byte
	dz                     *http.Client
	ld                     ProxyTelemetry
	is                     chan gy
	dl                     map[int64]time.Time
	hg, ha                 *os.File
	kp, ko, jj             int64
	eo                     map[string]struct{}
	ve                     singleflight.Group
	ci                     map[string]ij
	ih                     []gs
	kc                     []*regexp.Regexp
}

func NewProxyServer(cl *FastDNSResolver, ds, ao string, bt bool, bc int, ew, fq, ft, bb, dh, ua, fe, fd, fj, cb string) *ProxyServer {
	bb = cmp.Or(bb, "SM-A515F")
	dh = cmp.Or(dh, "samsung")
	ua = cmp.Or(ua, "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36")
	cb = cmp.Or(cb, "https://www.dofus-touch.com/")
	fo := cb
	if u, y := url.Parse(cb); y == nil && u.Scheme != "" && u.Host != "" {
		fo = u.Scheme + "://" + u.Host
	}
	gu, fz := "10", "QP1A.190711.020"
	if m := tu.FindStringSubmatch(ua); len(m) > 1 {
		gu = m[1]
	}
	if m := tt.FindStringSubmatch(ua); len(m) > 1 {
		fz = m[1]
	}
	lh := ow(ds)
	var et *url.URL
	if len(lh) > 0 {
		et = lh[0]
	}
	if ao != "" {
		_ = os.MkdirAll(ao, 0755)
	}
	nq := et != nil && hz(et.Scheme)
	ee := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	jb := &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 50,
		IdleConnTimeout:     90 * time.Second,
		DialContext: func(aa context.Context, il, av string) (net.Conn, error) {
			if nq {
				return jm(et, av, 6*time.Second)
			}
			return ee.DialContext(aa, il, cl.kq(aa, av, "80"))
		},
	}
	if et != nil && !nq {
		jb.Proxy = http.ProxyURL(et)
	}
	return &ProxyServer{
		bx: make(map[string]ff),
		cl: cl,
		ao: ao,
		bt: bt,
		bc: bc,
		ew: ew,
		fq: fq,
		ft: ft,
		bb: bb,
		dh: dh,
		ua: ua,
		qz: fmt.Sprintf("Dalvik/2.1.0 (Linux; U; Android %s; %s Build/%s)", gu, bb, fz),
		gu: gu,
		fz: fz,
		fe: fe,
		fd: fd,
		fj: fj,
		cb: cb,
		fo: fo,
		fs: et,
		da: lh,
		dz: &http.Client{
			Transport:     jb,
			Timeout:       60 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}
}
func (p *ProxyServer) po(h http.Header) {
	for k := range hopReqHeaders {
		h.Del(k)
	}
	hu := h.Get("User-Agent")
	if hu == "" || strings.HasPrefix(hu, "Dalvik/") || uaLeaksDesktop(hu) {
		h.Set("User-Agent", p.ua)
	} else {
		h.Set("User-Agent", strings.TrimSpace(stripWebViewTokens(hu)))
	}
	if v := h["Sec-Ch-Ua-Arch"]; len(v) > 0 && v[0] != "" {
		h.Set("Sec-Ch-Ua-Arch", `"arm"`)
	}
	if v := h["Sec-Ch-Ua-Platform"]; len(v) > 0 && v[0] != "" {
		h.Set("Sec-Ch-Ua-Platform", `"Android"`)
	}
	if v := h["Sec-Ch-Ua-Mobile"]; len(v) > 0 && v[0] != "" {
		h.Set("Sec-Ch-Ua-Mobile", "?1")
	}
	if v := h["Sec-Ch-Ua-Model"]; len(v) > 0 && v[0] != "" {
		h.Set("Sec-Ch-Ua-Model", `"`+p.bb+`"`)
	}
	if h.Get("Accept-Language") == "" {
		h.Set("Accept-Language", "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")
	}
}
func uaLeaksDesktop(ua string) bool {
	for _, kw := range uy {
		if qv(ua, kw) {
			return true
		}
	}
	return false
}
func jk(al, cy http.Header, cx map[string]struct{}) {
	for k, vv := range cy {
		if _, ri := cx[k]; !ri {
			al[k] = vv
		}
	}
}
func nk(aw string) bool {
	l := bv(aw)
	return l == "br" || strings.HasPrefix(l, "br;") || strings.HasPrefix(l, "br ")
}
func lp(ae string, rj bool) string {
	var b strings.Builder
	b.Grow(len(ae))
	for len(ae) > 0 {
		var aq string
		if i := strings.IndexByte(ae, ','); i >= 0 {
			aq, ae = ae[:i], ae[i+1:]
		} else {
			aq, ae = ae, ""
		}
		aw := strings.TrimSpace(aq)
		if aw == "" || (rj && nk(aw)) {
			continue
		}
		if b.Len() > 0 {
			b.WriteString(", ")
		}
		b.WriteString(aw)
	}
	return cmp.Or(b.String(), "gzip, deflate")
}
func filterAcceptEncoding(ae string) string {
	if !rz(ae) && ae == sj(ae) {
		return ae
	}
	return lp(ae, true)
}
func sj(ae string) string { return lp(ae, false) }
func rz(ae string) bool {
	for len(ae) > 0 {
		var aq string
		if i := strings.IndexByte(ae, ','); i >= 0 {
			aq, ae = ae[:i], ae[i+1:]
		} else {
			aq, ae = ae, ""
		}
		if aw := strings.TrimSpace(aq); aw != "" && nk(aw) {
			return true
		}
	}
	return false
}
func bv(s string) string {
	for i := 0; i < len(s); i++ {
		if s[i] >= 'A' && s[i] <= 'Z' {
			b := []byte(s)
			for ; i < len(b); i++ {
				if b[i] >= 'A' && b[i] <= 'Z' {
					b[i] += 'a' - 'A'
				}
			}
			return string(b)
		}
	}
	return s
}
func td(hp []*url.URL, cl *FastDNSResolver) *http.Client {
	ee := &net.Dialer{Timeout: 8 * time.Second, KeepAlive: 30 * time.Second}
	jb := &http.Transport{
		MaxIdleConns:        128,
		MaxIdleConnsPerHost: 32,
		IdleConnTimeout:     60 * time.Second,
		DialContext: func(aa context.Context, il, av string) (net.Conn, error) {
			var u *url.URL
			if len(hp) == 1 {
				u = hp[0]
			} else if len(hp) > 1 {
				u = hp[int(atomic.AddUint32(&qk, 1))%len(hp)]
			}
			if u == nil {
				return ee.DialContext(aa, il, av)
			}
			if hz(u.Scheme) {
				return jm(u, av, 8*time.Second)
			}
			ad, gx, y := net.SplitHostPort(av)
			if y != nil {
				ad, gx = av, "80"
			}
			ac, y := ee.DialContext(aa, il, eh(u.Host, "80"))
			if y != nil {
				return nil, y
			}
			_ = ac.SetDeadline(time.Now().Add(8 * time.Second))
			if y := lx(ac, u.Scheme, ad, tk(gx), li(u), ""); y != nil {
				_ = ac.Close()
				return nil, y
			}
			_ = ac.SetDeadline(time.Time{})
			return ac, nil
		},
	}
	return &http.Client{Transport: jb, Timeout: 60 * time.Second}
}
func tk(s string) int {
	if n, y := strconv.Atoi(s); y == nil && n > 0 && n <= 65535 {
		return n
	}
	return 80
}
func li(u *url.URL) string {
	if u != nil && u.User != nil {
		return u.User.String()
	}
	return ""
}
func ow(aq string) []*url.URL {
	var aj []*url.URL
	for _, eq := range strings.Split(aq, ",") {
		if u := ox(eq); u != nil {
			aj = append(aj, u)
		}
	}
	return aj
}
func tv(ac net.Conn) error {
	var ax [1]byte
	ck := bp.Get().([]byte)
	lb := ck[:8192]
	defer bp.Put(ck)
	n := 0
	for ho := 0; ho <= 8192; ho++ {
		if _, y := ac.Read(ax[:]); y != nil {
			return y
		}
		switch c := ax[0]; c {
		case '\r':
		case '\n':
			if n == 0 {
				return nil
			}
			bn := lb[:n]
			if len(bn) >= 5 && bytes.Equal(bn[:5], []byte("HTTP/")) {
				s := string(bn)
				if f := strings.Fields(s); len(f) >= 2 && f[1] != "200" {
					return fmt.Errorf("proxy answered %s", s)
				}
			}
			n = 0
		default:
			if n < len(lb) {
				lb[n] = c
			}
			n++
		}
	}
	return errors.New("CONNECT reply too long")
}
func ni(ac net.Conn, ab, eu, ua string, an time.Duration) error {
	if y := ac.SetDeadline(time.Now().Add(an)); y != nil {
		return y
	}
	defer func() { _ = ac.SetDeadline(time.Time{}) }()
	b := make([]byte, 0, len(ab)*2+len(ua)+len(eu)*2+96)
	b = append(b, "CONNECT "...)
	b = append(b, ab...)
	b = append(b, " HTTP/1.1\r\nHost: "...)
	b = append(b, ab...)
	b = append(b, "\r\nUser-Agent: "...)
	b = append(b, ua...)
	b = append(b, "\r\n"...)
	if eu != "" {
		b = append(b, "Proxy-Authorization: Basic "...)
		b = base64.StdEncoding.AppendEncode(b, []byte(eu))
		b = append(b, "\r\n"...)
	}
	b = append(b, "\r\n"...)
	if _, y := ac.Write(b); y != nil {
		return y
	}
	return tv(ac)
}
func lx(ac net.Conn, dp, ad string, bc int, eu, ua string) error {
	ab := net.JoinHostPort(ad, strconv.Itoa(bc))
	if hz(dp) {
		if y := ac.SetDeadline(time.Now().Add(6 * time.Second)); y != nil {
			return y
		}
		defer func() { _ = ac.SetDeadline(time.Time{}) }()
		var fy *xproxy.Auth
		if eu != "" {
			va, kg, _ := strings.Cut(eu, ":")
			fy = &xproxy.Auth{User: va, Password: kg}
		}
		d, y := xproxy.SOCKS5("tcp", "0.0.0.0:0", fy, lw{ac})
		if y != nil {
			return y
		}
		_, y = d.Dial("tcp", ab)
		return y
	}
	return ni(ac, ab, eu, ua, 6*time.Second)
}
func (p *ProxyServer) ud(ab string) {
	p.me.Lock()
	defer p.me.Unlock()
	if slices.Contains(p.ez, ab) {
		return
	}
	if len(p.ez) >= vd {
		p.ez = p.ez[1:]
	}
	p.ez = append(p.ez, ab)
}
func (p *ProxyServer) ng() []*url.URL {
	as := p.bm
	p.cu.Lock()
	defer p.cu.Unlock()
	mv := ""
	if as != nil {
		mv = as.String()
	}
	type le struct {
		u *url.URL
		d time.Duration
	}
	var gl, lg, bk []*url.URL
	hn := make([]le, 0, len(p.da))
	for _, u := range p.da {
		if u == nil {
			continue
		}
		k := u.String()
		if as != nil && k == mv {
			continue
		}
		switch pr, uf := p.bx[k]; {
		case !uf:
			lg = append(lg, u)
		case pr.z:
			gl = append(gl, u)
			hn = append(hn, le{u, pr.ib})
		default:
			bk = append(bk, u)
		}
	}
	if len(hn) > 1 {
		slices.SortStableFunc(hn, func(a, b le) int { return cmp.Compare(a.d, b.d) })
		for i := range gl {
			gl[i] = hn[i].u
		}
	}
	return slices.Concat(gl, lg, bk)
}
func (p *ProxyServer) ou(bg, as *url.URL, ab string, an time.Duration) (net.Conn, error) {
	mw := 1080
	if n, y := strconv.Atoi(as.Port()); y == nil && n > 0 {
		mw = n
	}
	pv, py, y := net.SplitHostPort(ab)
	if y != nil {
		pv, py = ab, "443"
	}
	px := 443
	if n, y := strconv.Atoi(py); y == nil && n > 0 && n <= 65535 {
		px = n
	}
	mr := net.JoinHostPort(as.Hostname(), strconv.Itoa(mw))
	cc := time.Now()
	ac, y := p.fa(bg, mr, mr, an)
	if y != nil {
		p.er(bg, false, time.Since(cc))
		return nil, y
	}
	if y = lx(ac, as.Scheme, pv, px, li(as), p.ua); y != nil {
		_ = ac.Close()
		p.er(bg, false, time.Since(cc))
		return nil, y
	}
	p.er(bg, true, time.Since(cc))
	return ac, nil
}
func sc(ao string) string {
	if ao == "" {
		return ""
	}
	return filepath.Join(ao, "hop_index.json")
}

type jt struct {
	Ok      bool  `json:"ok"`
	Latency int64 `json:"latency_ms"`
	At      int64 `json:"at"`
}

func (p *ProxyServer) so() {
	if p.em == "" {
		return
	}
	aq, y := os.ReadFile(p.em)
	if y != nil {
		return
	}
	var iw map[string]jt
	if json.Unmarshal(aq, &iw) != nil {
		return
	}
	p.cu.Lock()
	if p.bx == nil {
		p.bx = map[string]ff{}
	}
	for k, v := range iw {
		p.bx[k] = ff{z: v.Ok, ib: time.Duration(v.Latency) * time.Millisecond, ji: time.Unix(v.At, 0)}
	}
	p.cu.Unlock()
	if len(iw) > 0 {
		log.Printf("[UPSTREAM] loaded %d hop measurement(s) from %s", len(iw), p.em)
	}
}
func (p *ProxyServer) pp() {
	if p.em == "" {
		return
	}
	p.cu.Lock()
	if !p.js || len(p.bx) == 0 {
		p.cu.Unlock()
		return
	}
	aj := make(map[string]jt, len(p.bx))
	for k, v := range p.bx {
		aj[k] = jt{Ok: v.z, Latency: v.ib.Milliseconds(), At: v.ji.Unix()}
	}
	p.js = false
	p.cu.Unlock()
	if aq, y := json.Marshal(aj); y == nil {
		qa := p.em + ".tmp"
		if os.WriteFile(qa, aq, 0o644) == nil {
			_ = os.Rename(qa, p.em)
		}
	}
}
func np(h string) bool {
	h = strings.ToLower(strings.TrimSpace(h))
	if h == "localhost" || h == "::1" || h == "0.0.0.0" {
		return true
	}
	av, y := netip.ParseAddr(h)
	return y == nil && av.IsLoopback()
}
func (p *ProxyServer) vb() {
	as := p.bm
	if as == nil {
		return
	}
	bh := p.ng()
	if len(bh) == 0 {
		log.Printf("[UPSTREAM] fixed exit %s dropped: no hop available to reach it", as.Redacted())
		p.bm = nil
		return
	}
	if np(as.Hostname()) && slices.ContainsFunc(bh, func(h *url.URL) bool { return h != nil && !np(h.Hostname()) }) {
		log.Printf("[UPSTREAM] fixed exit %s is local; a remote hop cannot reach it (it would dial its own localhost). Use it as the plain upstream instead of an exit.", as.Redacted())
		p.bm = nil
		return
	}
	tr := net.JoinHostPort(gp, "443")
	bh = bh[:min(len(bh), 2)]
	el := make(chan *url.URL, len(bh))
	for _, bg := range bh {
		go func(bg *url.URL) {
			if ac, y := p.ou(bg, as, tr, 3*time.Second); y == nil {
				_ = ac.Close()
				el <- bg
			}
		}(bg)
	}
	select {
	case bg := <-el:
		log.Printf("[UPSTREAM] fixed exit %s carries %s through %s", as.Redacted(), gp, bg.Redacted())
	case <-time.After(3500 * time.Millisecond):
		log.Printf("[UPSTREAM] fixed exit %s dropped: no hop could carry a request through it. Using the plain chain.", as.Redacted())
		p.bm = nil
	}
}
func (p *ProxyServer) er(bg *url.URL, z bool, uw time.Duration) {
	if bg == nil {
		return
	}
	p.cu.Lock()
	if p.bx == nil {
		p.bx = map[string]ff{}
	}
	p.bx[bg.String()] = ff{z: z, ib: uw, ji: time.Now()}
	p.js = true
	p.cu.Unlock()
}
func pf(n int, rb func(i int) (net.Conn, error)) (net.Conn, int, error) {
	type bd struct {
		ac net.Conn
		ba int
		y  error
	}
	ch := make(chan bd, n)
	for i := 0; i < n; i++ {
		go func(i int) {
			c, y := rb(i)
			ch <- bd{c, i, y}
		}(i)
	}
	var nv error
	for i := 0; i < n; i++ {
		r := <-ch
		if r.y == nil {
			go func(uc int) {
				for j := 0; j < uc; j++ {
					if nw := <-ch; nw.ac != nil {
						_ = nw.ac.Close()
					}
				}
			}(n - i - 1)
			return r.ac, r.ba, nil
		}
		nv = r.y
	}
	return nil, -1, nv
}
func (p *ProxyServer) rd(aa context.Context, as *url.URL, ab string, an time.Duration) (net.Conn, *url.URL, error) {
	p.ud(ab)
	bh := p.ng()
	if len(bh) == 0 {
		ac, y := p.fa(as, ab, ab, an)
		if y != nil {
			return nil, nil, y
		}
		return ac, as, nil
	}
	bh = bh[:min(len(bh), 4)]
	ac, _, y := pf(len(bh), func(i int) (net.Conn, error) {
		return p.ou(bh[i], as, ab, an)
	})
	if y != nil {
		return nil, nil, cmp.Or(y, errors.New("no hop could reach the exit"))
	}
	p.ky(as)
	return ac, as, nil
}
func (p *ProxyServer) mi(u *url.URL, ab string, an time.Duration) (net.Conn, error) {
	de := "80"
	if strings.EqualFold(u.Scheme, "https") {
		de = "443"
	}
	ac, y := net.DialTimeout("tcp", eh(u.Host, de), an)
	if y != nil {
		return nil, y
	}
	if y := ni(ac, ab, li(u), p.ua, an); y != nil {
		_ = ac.Close()
		return nil, y
	}
	return ac, nil
}

const nc = 60 * time.Second
const ti = 30 * time.Second

func (p *ProxyServer) jy(u *url.URL) {
	if u == nil {
		return
	}
	ia := u.String()
	p.iq.Lock()
	if p.gv == nil {
		p.gv = make(map[string]time.Time)
	}
	p.gv[ia] = time.Now().Add(ti)
	p.iq.Unlock()
}
func (p *ProxyServer) tj(u *url.URL) bool {
	if u == nil {
		return false
	}
	ia := u.String()
	cj := time.Now()
	p.iq.Lock()
	uz, z := p.gv[ia]
	if z && !cj.Before(uz) {
		delete(p.gv, ia)
		z = false
	}
	p.iq.Unlock()
	return z
}

type dg struct {
	nu atomic.Int64
}

func (g *dg) ja() {
	g.nu.Store(time.Now().UnixNano())
}

type ll struct {
	w  io.Writer
	ja func()
}

func (a ll) Write(b []byte) (int, error) {
	n, y := a.w.Write(b)
	if n > 0 {
		a.ja()
	}
	return n, y
}
func (p *ProxyServer) pj() (*dg, func()) {
	t := &dg{}
	p.fc.Lock()
	if p.gk == nil {
		p.gk = make(map[*dg]struct{})
	}
	p.gk[t] = struct{}{}
	p.fc.Unlock()
	return t, func() {
		p.fc.Lock()
		delete(p.gk, t)
		p.fc.Unlock()
	}
}
func (p *ProxyServer) ny(vg time.Duration) int {
	qy := time.Now().Add(-vg).UnixNano()
	p.fc.Lock()
	defer p.fc.Unlock()
	ic := 0
	for t := range p.gk {
		if t.nu.Load() >= qy {
			ic++
		}
	}
	return ic
}
func (p *ProxyServer) ky(u *url.URL) {
	if u == nil {
		return
	}
	p.cm.Lock()
	if p.fh == nil {
		p.fh = u
	}
	p.cm.Unlock()
}
func (p *ProxyServer) jq(aa context.Context, ab, az string, an time.Duration, cx *url.URL, it bool) (net.Conn, *url.URL, error) {
	ca := make([]*url.URL, 0, len(p.da))
	for _, u := range p.da {
		if u == nil || (u == cx || (cx != nil && u.String() == cx.String())) || p.tj(u) {
			continue
		}
		ca = append(ca, u)
	}
	if len(ca) == 0 {
		return nil, nil, fmt.Errorf("no upstream configured")
	}
	ek := ca[0]
	fw := "the supervisor's pick"
	if cx != nil {
		fw = "the dead address was skipped"
	}
	cc := time.Now()
	ac, y := p.fa(ek, ab, az, an)
	p.er(ek, y == nil, time.Since(cc))
	if y == nil {
		p.ky(ek)
		p.oa(ek, it, fw)
		return ac, ek, nil
	}
	p.jy(ek)
	if len(ca) == 1 {
		return nil, nil, y
	}
	log.Printf("[UPSTREAM] %s %s did not carry the first dial (%v); choosing another", fw, ek.Redacted(), y)
	ac, bj, hh := p.km(aa, ca[1:], ab, az, an)
	if hh != nil {
		return nil, nil, hh
	}
	p.ky(bj)
	p.oa(bj, it, "the first choice was unreachable")
	return ac, bj, nil
}
func (p *ProxyServer) oa(bj *url.URL, it bool, fw string) {
	if it {
		log.Printf("[UPSTREAM] session egress re-pinned to %s (%s; the previous address is dead and no game traffic is live — this is a new session)", bj.Redacted(), fw)
		return
	}
	log.Printf("[UPSTREAM] session egress pinned to %s (%s; one address for this session)", bj.Redacted(), fw)
}
func (p *ProxyServer) tz(aa context.Context, ab, az string, an time.Duration, gh *url.URL) (net.Conn, *url.URL, bool, error) {
	p.gw.Lock()
	defer p.gw.Unlock()
	if p.ny(nc) > 0 {
		return nil, nil, false, nil
	}
	p.jy(gh)
	p.cm.Lock()
	co := p.fh
	p.cm.Unlock()
	if co == nil {
		ac, bj, y := p.jq(aa, ab, az, an, gh, true)
		return ac, bj, true, y
	}
	if gh != nil && gh != co && co.String() != gh.String() {
		cc := time.Now()
		ac, y := p.fa(co, ab, az, an)
		p.er(co, y == nil, time.Since(cc))
		if y != nil {
			p.jy(co)
		}
		return ac, co, true, y
	}
	log.Printf("[UPSTREAM] pinned egress %s is dead and no game traffic is live; the session it protected is gone, so a new one may use another address", co.Redacted())
	p.cm.Lock()
	p.fh = nil
	p.cm.Unlock()
	ac, bj, y := p.jq(aa, ab, az, an, co, true)
	return ac, bj, true, y
}
func (p *ProxyServer) rc(aa context.Context, ab string, an time.Duration) (net.Conn, *url.URL, error) {
	az := p.cl.kq(aa, ab, "443")
	p.cm.Lock()
	br := p.fh
	p.cm.Unlock()
	if br == nil && fg(ab) {
		p.gw.Lock()
		p.cm.Lock()
		br = p.fh
		p.cm.Unlock()
		if br == nil {
			ac, bj, y := p.jq(aa, ab, az, an, nil, false)
			p.gw.Unlock()
			return ac, bj, y
		}
		p.gw.Unlock()
	}
	if br != nil {
		cc := time.Now()
		ac, y := p.fa(br, ab, az, an)
		p.er(br, y == nil, time.Since(cc))
		if y == nil {
			return ac, br, nil
		}
		if fg(ab) {
			ac, bj, sz, hh := p.tz(aa, ab, az, an, br)
			if sz {
				if hh == nil {
					return ac, bj, nil
				}
				return nil, nil, hh
			}
			ic := p.ny(nc)
			log.Printf("[UPSTREAM] pinned egress %s failed for %s with %d live game connection(s); refusing to move the exit IP mid-session (%v)",
				br.Redacted(), ab, ic, y)
			return nil, nil, y
		}
		be := make([]*url.URL, 0, len(p.da))
		for _, u := range p.da {
			if u != nil && u != br && u.String() != br.String() {
				be = append(be, u)
			}
		}
		if len(be) == 0 {
			return nil, nil, y
		}
		log.Printf("[UPSTREAM] pinned egress %s failed for background %s; using another hop (the session pin does not move)", br.Redacted(), ab)
		return p.km(aa, be, ab, az, an)
	}
	return p.km(aa, p.da, ab, az, an)
}
func (p *ProxyServer) fa(u *url.URL, ab, az string, an time.Duration) (net.Conn, error) {
	if u == nil {
		return nil, fmt.Errorf("nil upstream")
	}
	if hz(u.Scheme) {
		return jm(u, az, an)
	}
	return p.mi(u, ab, an)
}
func fg(ab string) bool {
	ad := ab
	if h, _, y := net.SplitHostPort(ab); y == nil {
		ad = h
	}
	ad = strings.ToLower(strings.TrimSuffix(ad, "."))
	if ad == "dofus.local" || ad == "dofus.localhost" {
		return true
	}
	for _, us := range rp {
		if strings.HasSuffix(ad, us) {
			return true
		}
	}
	return false
}
func (p *ProxyServer) km(aa context.Context, ca []*url.URL, ab, az string, an time.Duration) (net.Conn, *url.URL, error) {
	if len(ca) == 0 {
		return nil, nil, fmt.Errorf("no upstream configured")
	}
	ac, ba, y := pf(len(ca), func(i int) (net.Conn, error) {
		u := ca[i]
		cc := time.Now()
		c, y := p.fa(u, ab, az, an)
		p.er(u, y == nil, time.Since(cc))
		return c, y
	})
	if y != nil {
		return nil, nil, cmp.Or(y, fmt.Errorf("no upstream configured"))
	}
	return ac, ca[ba], nil
}
func (p *ProxyServer) mj(aa context.Context, ab string) (net.Conn, error) {
	fr := time.Now()
	ac, y := p.rh(aa, ab)
	p.ld.ty(float64(time.Since(fr).Microseconds())/1000.0, y)
	return ac, y
}
func (p *ProxyServer) mg(aa context.Context, ab string) (net.Conn, error) {
	af := p.cl.kq(aa, ab, "443")
	return (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(aa, "tcp", af)
}
func (p *ProxyServer) rh(aa context.Context, ab string) (net.Conn, error) {
	if ad, bc, y := net.SplitHostPort(ab); y == nil {
		if h := strings.ToLower(ad); h == "dofus.local" || h == "dofus.localhost" {
			od := net.JoinHostPort(gp, bc)
			vc.Do(func() {
				log.Printf("[ROUTE] %s -> %s (the client resolved its socket against the page origin)", ab, od)
			})
			ab = od
		}
	}
	if p.bm != nil {
		p.hw.Lock()
		qn := time.Now().Before(p.ms)
		p.hw.Unlock()
		if !qn {
			if ac, _, y := p.rd(aa, p.bm, ab, 6*time.Second); y == nil {
				dt.Store(true)
				return ac, nil
			} else {
				p.hw.Lock()
				p.ms = time.Now().Add(mt)
				p.hw.Unlock()
				dt.Store(false)
				if fg(ab) {
					log.Printf("[UPSTREAM] fixed exit %s unreachable (%v); refusing to dial %s over a different address", p.bm.Redacted(), y, ab)
					return nil, fmt.Errorf("fixed exit %s unavailable; refusing to change the session's exit IP: %w", p.bm.Redacted(), y)
				}
				log.Printf("[UPSTREAM] fixed exit %s unreachable through any hop (%v); background traffic uses the plain chain for the next %s",
					p.bm.Redacted(), y, mt)
			}
		} else if fg(ab) {
			return nil, fmt.Errorf("fixed exit %s is cooling down after a failure; refusing to dial %s over a different address", p.bm.Redacted(), ab)
		}
	}
	if p.fs != nil {
		if len(p.da) > 0 {
			var y error
			for dx := 0; dx < 3; dx++ {
				if dx > 0 {
					time.Sleep(time.Duration(dx) * 1500 * time.Millisecond)
				}
				if ac, _, hv := p.rc(aa, ab, 6*time.Second); hv == nil {
					dt.Store(true)
					return ac, nil
				} else {
					y = hv
				}
			}
			dt.Store(false)
			if p.fx {
				qe.Do(func() {
					log.Printf("[UPSTREAM] dial to %s via %s failed (%v); -allow-direct is set, so falling back to a DIRECT connection. Ankama will see this machine's real IP.",
						ab, p.fs.Redacted(), y)
				})
				return p.mg(aa, ab)
			}
			qe.Do(func() {
				log.Printf("[UPSTREAM] dial to %s via %s failed (%v); refusing to fall back to a direct connection because that would expose the real IP. Start WARP in proxy mode (warp-cli mode proxy && warp-cli connect) and relaunch.",
					ab, p.fs.Redacted(), y)
			})
			return nil, fmt.Errorf("upstream %s unavailable and direct dial disabled: %w", p.fs.Redacted(), y)
		}
		return p.mi(p.fs, ab, 5*time.Second)
	}
	return p.mg(aa, ab)
}
func (p *ProxyServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if sg(r.Host) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/__cdn/"):
			p.rv(w, r)
			return
		case r.URL.Path == "/__files" || r.URL.Path == "/__warm" || r.URL.Path == "/__delete":
			p.ru(w, r)
			return
		}
	}
	if p.ew != "" {
		fy := cmp.Or(r.Header.Get("Proxy-Authorization"), r.Header.Get("Authorization"), r.Header.Get("X-Proxy-Token"))
		if fy != p.ew && fy != "Bearer "+p.ew {
			http.Error(w, "Proxy Authentication Required", http.StatusProxyAuthRequired)
			return
		}
	}
	se := r.Host == "" || strings.HasPrefix(r.Host, "127.0.0.1") || strings.HasPrefix(r.Host, "localhost")
	if r.Method == http.MethodGet && se {
		switch r.URL.Path {
		case "/__health", "/__telemetry":
			w.Header().Set("Content-Type", "application/json")
			s := p.ld.un()
			_, _ = fmt.Fprintf(w, `{"status":"ok","port":%d,"model":"%s","rtt_ms":%.2f,"rtt_avg_ms":%.2f,"sent_bytes":%d,"recv_bytes":%d,"conns_total":%d,"conns_failed":%d,"conns_active":%d,"timestamp":%d}`,
				p.bc, p.bb, s.gn, s.ln, s.gb, s.ga, s.gg, s.gf, s.ec, time.Now().Unix())
			return
		case "/__audit":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write(p.qm())
			return
		}
	}
	if r.Method == http.MethodGet && (r.URL.Path == "/authorized" || r.URL.Path == "/login-authorized" || strings.HasPrefix(r.URL.Path, "/oauth/callback")) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		qs := ""
		if r.URL.RawQuery != "" {
			qs = "?" + r.URL.RawQuery
		}
		_, _ = fmt.Fprintf(w, `<!DOCTYPE html><html><head><title>Authorization Success</title></head><body><script>if(window.opener&&window.opener.__onDeepLinkCallback){window.opener.__onDeepLinkCallback(window.location.href);}window.location.href="dofustouch://authorized%s";</script><h2>Authorization Successful</h2><p>Redirecting back to Dofus Touch...</p></body></html>`, qs)
		return
	}
	switch {
	case r.Method == http.MethodConnect:
		p.rw(w, r)
	case strings.EqualFold(r.Header.Get("Upgrade"), "websocket"):
		p.ry(w, r)
	default:
		p.rx(w, r)
	}
}
func (p *ProxyServer) qm() []byte {
	mp := func(ta, fu string) []byte {
		bd, _ := json.Marshal(map[string]any{
			"status": "error", "port": p.bc, "model": p.bb,
			"message": ta, "verdict": fu, "risk_score": 50,
		})
		return bd
	}
	dz := &http.Client{
		Transport: &http.Transport{
			DialContext:           func(aa context.Context, _, av string) (net.Conn, error) { return p.mj(aa, av) },
			ResponseHeaderTimeout: 5 * time.Second,
		},
		Timeout: 6 * time.Second,
	}
	ak, y := dz.Get("http://ip-api.com/json/?fields=status,message,country,city,isp,org,as,query,mobile,proxy,hosting")
	if y != nil {
		return mp(y.Error(), "LOOKUP_OFFLINE")
	}
	defer ak.Body.Close()
	ai, _ := io.ReadAll(ak.Body)
	var ay struct {
		Status, Query, ISP, Org, AS, Country, City, Message string
		Mobile, Proxy, Hosting                              bool
	}
	if json.Unmarshal(ai, &ay) != nil || ay.Status != "success" {
		return mp("upstream_ip_api_error", "UNKNOWN")
	}
	iu, fu := 0, "SAFE_RESIDENTIAL"
	switch {
	case ay.Hosting:
		iu, fu = 85, "DANGEROUS_DATACENTER"
	case ay.Mobile:
		iu, fu = 0, "PRISTINE_CELLULAR_4G"
	case ay.Proxy:
		iu, fu = 45, "SUSPICIOUS_PUBLIC_PROXY"
	}
	bd, _ := json.Marshal(map[string]any{
		"status": "ok", "port": p.bc, "model": p.bb, "outbound_ip": ay.Query,
		"isp": ay.ISP, "org": ay.Org, "asn": ay.AS, "country": ay.Country, "city": ay.City,
		"is_cellular": ay.Mobile, "is_hosting": ay.Hosting, "is_proxy": ay.Proxy,
		"risk_score": iu, "verdict": fu,
	})
	return bd
}
func qd(c net.Conn) {
	_ = c.SetDeadline(time.Time{})
	if tc, z := c.(*net.TCPConn); z {
		_ = tc.SetKeepAlive(true)
		_ = tc.SetKeepAlivePeriod(30 * time.Second)
		_ = tc.SetNoDelay(true)
	}
}
func rt(c net.Conn) {
	if cw, z := c.(qt); z {
		_ = cw.CloseWrite()
	} else {
		_ = c.Close()
	}
}
func (p *ProxyServer) oz(bq net.Conn, ge *bufio.ReadWriter, bl net.Conn, dr *dg) {
	qd(bq)
	qd(bl)
	var pt, ph int64
	jo := make(chan struct{}, 2)
	lz := func(al, cy net.Conn, mb *int64, hs *bufio.ReadWriter) {
		aj := io.Writer(al)
		if dr != nil {
			aj = ll{w: al, ja: dr.ja}
		}
		if hs != nil && hs.Reader.Buffered() > 0 {
			n, _ := io.CopyN(aj, hs, int64(hs.Reader.Buffered()))
			atomic.AddInt64(mb, n)
		}
		ax := bp.Get().([]byte)
		n, _ := io.CopyBuffer(aj, cy, ax)
		atomic.AddInt64(mb, n)
		bp.Put(ax)
		rt(al)
		jo <- struct{}{}
	}
	go lz(bl, bq, &pt, ge)
	go lz(bq, bl, &ph, nil)
	<-jo
	mm := time.NewTimer(4 * time.Second)
	select {
	case <-jo:
		mm.Stop()
	case <-mm.C:
	}
	_ = bl.Close()
	_ = bq.Close()
	p.ld.tx(atomic.LoadInt64(&pt), atomic.LoadInt64(&ph))
}
func (p *ProxyServer) mh(ad, de string) (net.Conn, error) {
	aa, gc := context.WithTimeout(context.Background(), 7*time.Second)
	defer gc()
	return p.mj(aa, eh(ad, de))
}
func (p *ProxyServer) rw(w http.ResponseWriter, r *http.Request) {
	ab := eh(r.Host, "443")
	bl, y := p.mh(ab, "443")
	if y != nil {
		http.Error(w, "Connection Failed: "+y.Error(), http.StatusBadGateway)
		return
	}
	sb, z := w.(http.Hijacker)
	if !z {
		_ = bl.Close()
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}
	bq, ge, y := sb.Hijack()
	if y != nil {
		_ = bl.Close()
		http.Error(w, "Hijack failed: "+y.Error(), http.StatusServiceUnavailable)
		return
	}
	if _, y = bq.Write(ux); y != nil {
		_ = bl.Close()
		_ = bq.Close()
		return
	}
	if !p.bt && strings.Contains(ab, "ankama") {
		log.Printf("[TUNNEL] CONNECT -> %s (%s)", ab, p.bb)
	}
	var dr *dg
	if fg(ab) {
		var hf func()
		dr, hf = p.pj()
		defer hf()
	}
	p.oz(bq, ge, bl, dr)
}
func (p *ProxyServer) ry(w http.ResponseWriter, r *http.Request) {
	hj, z := w.(http.Hijacker)
	if !z {
		http.Error(w, "Webserver does not support hijacking", http.StatusInternalServerError)
		return
	}
	bq, ge, y := hj.Hijack()
	if y != nil {
		http.Error(w, y.Error(), http.StatusServiceUnavailable)
		return
	}
	bl, y := p.mh(r.Host, "80")
	if y != nil {
		_ = bq.Close()
		return
	}
	p.po(r.Header)
	r.RequestURI = ""
	if r.URL != nil {
		r.URL.Scheme, r.URL.Host = "", ""
	}
	if y := r.Write(bl); y != nil {
		_ = bl.Close()
		_ = bq.Close()
		return
	}
	var dr *dg
	if fg(r.Host) {
		var hf func()
		dr, hf = p.pj()
		defer hf()
	}
	p.oz(bq, ge, bl, dr)
}
func ql(cy, al string) error {
	if y := os.Rename(cy, al); y == nil {
		return nil
	}
	var y error
	for dx := 0; dx < 3; dx++ {
		time.Sleep(20 * time.Millisecond)
		_ = os.Remove(al)
		if y = os.Rename(cy, al); y == nil {
			return nil
		}
	}
	ot := al + ".old." + strconv.FormatInt(time.Now().UnixNano(), 10)
	if os.Rename(al, ot) == nil {
		_ = os.Remove(ot)
		if os.Rename(cy, al) == nil {
			return nil
		}
	}
	return y
}
func mf(bi string) string {
	cr := strings.ToLower(filepath.Ext(bi))
	if m, z := rm[cr]; z {
		return m
	}
	return cmp.Or(mime.TypeByExtension(cr), "application/octet-stream")
}
func (p *ProxyServer) nd() string {
	p.rf.Do(func() {
		p.jg = p.sn()
		p.jn = []byte(p.jg)
	})
	return p.jg
}
func (p *ProxyServer) sd(ai []byte) []byte {
	lu := p.nd()
	gm := fmt.Sprintf("\n\t\t<script type=\"text/javascript\" src=\"http://10.0.2.2:%d/__disguise.js\"></script>\n", p.bc)
	if lu != "" {
		gm = "\n\t\t<script type=\"text/javascript\">\n" + lu + "\n\t\t</script>\n"
	}
	al := make([]byte, 0, len(ai)+len(gm)+14)
	if i := ju(ai, "<head"); i >= 0 {
		if k := bytes.IndexByte(ai[i:], '>'); k >= 0 {
			j := i + k + 1
			al = append(al, ai[:j]...)
			al = append(al, gm...)
			return append(al, ai[j:]...)
		}
	}
	if i := ju(ai, "</head>"); i >= 0 {
		al = append(al, ai[:i]...)
		al = append(al, gm...)
		return append(al, ai[i:]...)
	}
	qh := "<head>" + gm + "</head>"
	if i := ju(ai, "<html"); i >= 0 {
		if k := bytes.IndexByte(ai[i:], '>'); k >= 0 {
			j := i + k + 1
			al = append(al, ai[:j]...)
			al = append(al, qh...)
			return append(al, ai[j:]...)
		}
	}
	al = append(al, qh...)
	return append(al, ai...)
}
func (p *ProxyServer) sn() string {
	mq := filepath.Dir(os.Args[0])
	for _, c := range []string{
		filepath.Join("scripts", "mobile-disguise.js"),
		"mobile-disguise.js",
		filepath.Join(os.Getenv("DOFUS_FARM_HOME"), "scripts", "mobile-disguise.js"),
		filepath.Join(mq, "scripts", "mobile-disguise.js"),
		filepath.Join(mq, "mobile-disguise.js"),
	} {
		b, y := os.ReadFile(c)
		if y != nil || len(b) == 0 {
			continue
		}
		var im []string
		for _, kv := range [][2]string{
			{"__DEVICE_SERIAL__", p.fq},
			{"__DEVICE_UUID__", p.ft},
			{"__DEVICE_MODEL__", p.bb},
			{"__DEVICE_MANUFACTURER__", p.dh},
			{"__DEVICE_UA__", p.ua},
			{"__DEVICE_GL_VENDOR__", p.fe},
			{"__DEVICE_GL_RENDERER__", p.fd},
			{"__DEVICE_MAX_TEXTURE_SIZE__", p.fj},
		} {
			if kv[1] != "" {
				im = append(im, kv[0], kv[1])
			}
		}
		if len(im) > 0 {
			return strings.NewReplacer(im...).Replace(string(b))
		}
		return string(b)
	}
	return ""
}
func sg(ad string) bool {
	h := strings.ToLower(ad)
	return h == "" || strings.HasPrefix(h, "127.0.0.1") || strings.HasPrefix(h, "localhost") || strings.HasPrefix(h, "[::1]")
}
func nl(ad string) bool {
	if h, _, z := strings.Cut(ad, ":"); z {
		ad = h
	}
	ad = strings.ToLower(ad)
	return ad == "cdn.ankama.com" || ad == "cdn.ankama-games.com" ||
		strings.HasSuffix(ad, ".cdn.ankama.com") || strings.HasSuffix(ad, ".cdn.ankama-games.com")
}
func sh(ad string) bool {
	if h, _, z := strings.Cut(ad, ":"); z {
		ad = h
	}
	return strings.EqualFold(ad, "static.ankama.com")
}
func jw(u string) bool {
	if strings.Contains(u, "production-login") {
		return true
	}
	if i := strings.IndexAny(u, "?#"); i != -1 {
		u = u[:i]
	}
	return strings.HasSuffix(u, "/config.json") || strings.HasSuffix(u, "/getLanguage.json") || strings.HasSuffix(u, "/version.json")
}
func nm(v string) bool {
	l := strings.ToLower(v)
	return v == "" || strings.Contains(l, "dofus.local") || strings.Contains(l, "localhost") ||
		strings.Contains(l, "127.0.0.1") || strings.HasPrefix(l, "file:")
}
func (p *ProxyServer) qi(h http.Header, ad string) {
	if !nl(ad) {
		return
	}
	if pi := h.Get("Referer"); pi != "" && nm(pi) {
		h.Set("Referer", p.cb)
	}
	if ov := h.Get("Origin"); ov != "" && nm(ov) {
		h.Set("Origin", p.fo)
	}
}
func (p *ProxyServer) db(af string) string {
	ix := sha256.Sum256([]byte(af))
	var mn [32]byte
	hex.Encode(mn[:], ix[:16])
	return filepath.Join(p.ao, string(mn[:]))
}
func hl(h http.Header) {
	h.Set("Cache-Control", "public, max-age=604800, immutable")
}
func es(h http.Header, sw string) {
	h.Set("Access-Control-Allow-Origin", "*")
	h.Set("Access-Control-Allow-Methods", sw)
	h.Set("Access-Control-Allow-Headers", "*")
}
func (p *ProxyServer) ug(w http.ResponseWriter, r *http.Request, ag, mz string) (uk bool) {
	defer func() {
		if uk {
			atomic.AddUint64(&p.lq, 1)
		} else {
			atomic.AddUint64(&p.lr, 1)
		}
	}()
	fi, y := os.Stat(ag)
	if y != nil {
		return false
	}
	if time.Since(fi.ModTime()) > je {
		_ = os.Remove(ag)
		_ = os.Remove(ag + ".meta")
		return false
	}
	f, y := os.Open(ag)
	if y != nil {
		return false
	}
	defer f.Close()
	h := w.Header()
	es(h, "GET, POST, OPTIONS, HEAD")
	hl(h)
	ka, _ := os.ReadFile(ag + ".meta")
	if of := cmp.Or(string(bytes.TrimSpace(ka)), mf(mz)); of != "" {
		h.Set("Content-Type", of)
	}
	http.ServeContent(w, r, mz, fi.ModTime(), f)
	return true
}
func (p *ProxyServer) la(ah, ce, ag, ct string, r io.Reader, jr int64, al io.Writer) error {
	iz := ag + "." + strconv.Itoa(os.Getpid()) + "." + strconv.FormatInt(time.Now().UnixNano(), 10) + ".tmp"
	tf, y := os.Create(iz)
	if y != nil {
		if al != nil {
			ax := bp.Get().([]byte)
			_, _ = io.CopyBuffer(al, r, ax)
			bp.Put(ax)
		}
		return y
	}
	var w io.Writer = tf
	if al != nil {
		w = io.MultiWriter(al, tf)
	}
	ax := bp.Get().([]byte)
	lk, ma := io.CopyBuffer(w, r, ax)
	bp.Put(ax)
	_ = tf.Close()
	if ma != nil || (al == nil && lk == 0) || (jr > 0 && lk < jr) {
		_ = os.Remove(iz)
		return fmt.Errorf("copy: err=%v written=%d contentLength=%d", ma, lk, jr)
	}
	if y := ql(iz, ag); y != nil {
		_ = os.Remove(iz)
		return y
	}
	if ct != "" {
		_ = os.WriteFile(ag+".meta", []byte(ct), 0644)
	}
	p.gr(ah, ag)
	if ce != "" {
		if id := jx(ce); id > 0 {
			p.kx(ah, id)
		}
	}
	return nil
}
func sx(af string) string {
	u, y := url.Parse(af)
	if y != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return ""
	}
	ad := strings.ToLower(u.Hostname())
	if ad == "" || strings.ContainsAny(ad, `<>:"|?*\`) || u.Path == "" || u.Path == "/" ||
		strings.Contains(u.Path, "..") || strings.ContainsAny(u.Path, `<>:"|*\`) {
		return ""
	}
	return ad + u.Path
}
func (p *ProxyServer) gr(af, ag string) {
	if p.ao == "" {
		return
	}
	au := sx(af)
	if au == "" {
		return
	}
	gq := filepath.Join(p.ao, "files", filepath.FromSlash(au))
	if _, y := os.Stat(gq); y == nil {
		return
	}
	if os.MkdirAll(filepath.Dir(gq), 0755) != nil {
		return
	}
	if os.Link(ag, gq) == nil {
		p.og(au)
		return
	}
	in, y := os.Open(ag)
	if y != nil {
		return
	}
	defer in.Close()
	aj, y := os.Create(gq)
	if y != nil {
		return
	}
	if _, y := io.Copy(aj, in); y != nil {
		_ = aj.Close()
		_ = os.Remove(gq)
		return
	}
	_ = aj.Close()
	p.og(au)
}
func (p *ProxyServer) og(au string) {
	if au == "" {
		return
	}
	p.fk.Lock()
	if p.kb && p.eo != nil {
		p.eo[au] = struct{}{}
	}
	p.fk.Unlock()
}
func (p *ProxyServer) oh() []string {
	p.fk.Lock()
	defer p.fk.Unlock()
	if !p.kb {
		p.eo = make(map[string]struct{}, 4096)
		pm := filepath.Join(p.ao, "files")
		_ = filepath.Walk(pm, func(bi string, ay os.FileInfo, y error) error {
			if y == nil && ay != nil && !ay.IsDir() {
				if au, ub := filepath.Rel(pm, bi); ub == nil {
					p.eo[filepath.ToSlash(au)] = struct{}{}
				}
			}
			return nil
		})
		p.kb = true
	}
	aj := make([]string, 0, len(p.eo))
	for au := range p.eo {
		aj = append(aj, au)
	}
	slices.Sort(aj)
	return aj
}
func (p *ProxyServer) ru(w http.ResponseWriter, r *http.Request) {
	h := w.Header()
	es(h, "GET, OPTIONS")
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
		kh, y := json.Marshal(p.oh())
		if y != nil {
			http.Error(w, "encode error", http.StatusInternalServerError)
			return
		}
		h.Set("Content-Type", "application/json")
		if len(kh) > 32<<10 && strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			h.Set("Content-Encoding", "gzip")
			w.WriteHeader(http.StatusOK)
			gz := gzip.NewWriter(w)
			_, _ = gz.Write(kh)
			_ = gz.Close()
			return
		}
		_, _ = w.Write(kh)
	case "/__warm":
		af := r.URL.Query().Get("u")
		if !isCacheAssetURL(af) {
			http.Error(w, "Bad target", http.StatusBadRequest)
			return
		}
		if !p.bt {
			log.Printf("[WARM] %s", af)
		}
		z, dv, am, df := p.hq(r.Context(), af)
		h.Set("Content-Type", "application/json")
		if !z {
			p.bs("[WARM] failed status=%d err=%q %s", am, df, af)
			w.WriteHeader(http.StatusBadGateway)
			_, _ = fmt.Fprintf(w, `{"ok":false,"status":%d,"err":%q}`, am, df)
			return
		}
		p.do("WARM %s", af)
		if u, y := url.Parse(af); y == nil {
			if id := jx(u.Path); id > 0 {
				p.kx(af, id)
			}
		}
		_, _ = fmt.Fprintf(w, `{"ok":true,"cached":%t}`, dv)
	case "/__delete":
		kd := strings.Trim(r.URL.Query().Get("p"), "/")
		if kd == "" {
			http.Error(w, "Bad path", http.StatusBadRequest)
			return
		}
		pk := 0
		for _, au := range p.oh() {
			if au != kd && !strings.HasSuffix(au, "/"+kd) {
				continue
			}
			_ = os.Remove(filepath.Join(p.ao, "files", filepath.FromSlash(au)))
			cp := p.db("https://" + au)
			_ = os.Remove(cp)
			_ = os.Remove(cp + ".meta")
			p.fk.Lock()
			delete(p.eo, au)
			p.fk.Unlock()
			pk++
		}
		h.Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"ok":true,"removed":%d}`, pk)
	default:
		http.Error(w, "Not Found", http.StatusNotFound)
	}
}
func (p *ProxyServer) nz() {
	if p.cg == "" {
		return
	}
	p.ig.Lock()
	defer p.ig.Unlock()
	if time.Since(p.oi) < 2*time.Second {
		return
	}
	p.oi = time.Now()
	kt := filepath.Join(p.cg, "rules.json")
	fi, y := os.Stat(kt)
	if y != nil {
		if len(p.ih) > 0 {
			p.ih, p.kc = nil, nil
			log.Printf("[MODS] rules.json removed; mods disabled")
		}
		return
	}
	if fi.ModTime().Equal(p.oj) {
		return
	}
	aq, y := os.ReadFile(kt)
	if y != nil {
		return
	}
	var cv []gs
	if y := json.Unmarshal(aq, &cv); y != nil {
		log.Printf("[MODS] rules.json parse error: %v", y)
		return
	}
	bd := make([]*regexp.Regexp, len(cv))
	for i := range cv {
		re, y := regexp.Compile(cv[i].Match)
		if y != nil {
			log.Printf("[MODS] rule %d has invalid match %q: %v", i, cv[i].Match, y)
			continue
		}
		bd[i] = re
	}
	p.ih, p.kc, p.oj = cv, bd, fi.ModTime()
	log.Printf("[MODS] loaded %d rule(s) from %s", len(cv), kt)
}
func (p *ProxyServer) qj(w http.ResponseWriter, r *http.Request, ah string) bool {
	if p.cg == "" {
		return false
	}
	p.nz()
	p.ig.Lock()
	cv, bd := p.ih, p.kc
	p.ig.Unlock()
	for i, bo := range cv {
		if i >= len(bd) || bd[i] == nil || !bd[i].MatchString(ah) {
			continue
		}
		switch bo.Action {
		case "block":
			am := bo.Status
			if am < 100 || am > 599 {
				am = http.StatusNoContent
			}
			w.WriteHeader(am)
			p.do("MODBLOCK %d %s", am, ah)
			return true
		case "override":
			if p.uh(w, r, bo, ah) {
				return true
			}
		case "patch":
			if p.uj(w, r, bo, ah) {
				return true
			}
		}
	}
	return false
}
func (p *ProxyServer) uh(w http.ResponseWriter, r *http.Request, bo gs, ah string) bool {
	au := strings.TrimSpace(bo.File)
	if au == "" || filepath.IsAbs(au) || strings.Contains(au, "..") {
		p.bs("[MODS] override rule for %s has invalid file %q", ah, bo.File)
		return false
	}
	hy := filepath.Join(p.cg, filepath.FromSlash(au))
	f, y := os.Open(hy)
	if y != nil {
		p.bs("[MODS] override file missing: %s", hy)
		return false
	}
	defer f.Close()
	fi, y := f.Stat()
	if y != nil || fi.IsDir() {
		return false
	}
	h := w.Header()
	es(h, "GET, HEAD, OPTIONS")
	hl(h)
	if ct := cmp.Or(bo.ContentType, mf(hy)); ct != "" {
		h.Set("Content-Type", ct)
	}
	http.ServeContent(w, r, filepath.Base(hy), fi.ModTime(), f)
	p.do("MODOVER %s", ah)
	return true
}
func (p *ProxyServer) uj(w http.ResponseWriter, r *http.Request, bo gs, ah string) bool {
	ai, ct, z := p.tg(r.Context(), ah, bo)
	if !z {
		return false
	}
	h := w.Header()
	es(h, "GET, HEAD, OPTIONS")
	hl(h)
	h.Set("Content-Type", cmp.Or(bo.ContentType, ct, "application/json"))
	h.Set("Content-Length", strconv.Itoa(len(ai)))
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = w.Write(ai)
	}
	p.do("MODPATCH %s (%d bytes)", ah, len(ai))
	return true
}
func kn(bi string, sr int64) ([]byte, error) {
	f, y := os.Open(bi)
	if y != nil {
		return nil, y
	}
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, sr))
}
func (p *ProxyServer) tg(aa context.Context, ah string, bo gs) ([]byte, string, bool) {
	ag := p.db(ah)
	if _, y := os.Stat(ag); y != nil {
		if z, _, am, df := p.hq(aa, ah); !z {
			p.bs("[MODS] patch base fetch failed status=%d err=%q %s", am, df, ah)
			return nil, "", false
		}
	}
	aq, y := kn(ag, 64<<20)
	if y != nil {
		return nil, "", false
	}
	ka, _ := os.ReadFile(ag + ".meta")
	ct := string(bytes.TrimSpace(ka))
	var bw any
	if json.Unmarshal(aq, &bw) != nil {
		return nil, "", false
	}
	for _, op := range bo.Patch {
		switch strings.ToLower(strings.TrimSpace(op.Op)) {
		case "set":
			bw, _ = sl(bw, op.Path, op.Value)
		case "remove", "delete":
			bw, _ = sk(bw, op.Path)
		}
	}
	aj, y := json.Marshal(bw)
	if y != nil {
		return nil, "", false
	}
	return aj, ct, true
}
func nr(dk string) []string {
	dk = strings.TrimPrefix(dk, "/")
	if dk == "" {
		return nil
	}
	kf := strings.Split(dk, "/")
	for i, s := range kf {
		kf[i] = strings.ReplaceAll(strings.ReplaceAll(s, "~1", "/"), "~0", "~")
	}
	return kf
}
func sl(bw any, dk string, du any) (any, bool) {
	bf := nr(dk)
	if len(bf) == 0 {
		return du, true
	}
	return kj(bw, bf, du, 0)
}
func kj(gt any, bf []string, du any, ba int) (any, bool) {
	aw, cf := bf[ba], ba == len(bf)-1
	switch ar := gt.(type) {
	case map[string]any:
		if cf {
			ar[aw] = du
			return ar, true
		}
		gd := ar[aw]
		if gd == nil {
			gd = map[string]any{}
		}
		v, z := kj(gd, bf, du, ba+1)
		if z {
			ar[aw] = v
		}
		return ar, z
	case []any:
		if cf && aw == "-" {
			return append(ar, du), true
		}
		n, y := strconv.Atoi(aw)
		if y != nil || n < 0 || n >= len(ar) {
			return ar, false
		}
		if cf {
			ar[n] = du
			return ar, true
		}
		v, z := kj(ar[n], bf, du, ba+1)
		if z {
			ar[n] = v
		}
		return ar, z
	}
	return gt, false
}
func sk(bw any, dk string) (any, bool) {
	bf := nr(dk)
	if len(bf) == 0 {
		return bw, false
	}
	return ki(bw, bf, 0)
}
func ki(gt any, bf []string, ba int) (any, bool) {
	aw, cf := bf[ba], ba == len(bf)-1
	switch ar := gt.(type) {
	case map[string]any:
		gd, z := ar[aw]
		if !z {
			return ar, false
		}
		if cf {
			delete(ar, aw)
			return ar, true
		}
		v, z := ki(gd, bf, ba+1)
		if z {
			ar[aw] = v
		}
		return ar, z
	case []any:
		n, y := strconv.Atoi(aw)
		if y != nil || n < 0 || n >= len(ar) {
			return ar, false
		}
		if cf {
			return slices.Delete(ar, n, n+1), true
		}
		v, z := ki(ar[n], bf, ba+1)
		if z {
			ar[n] = v
		}
		return ar, z
	}
	return gt, false
}
func (p *ProxyServer) oq(aa context.Context, sv, ah string, cy http.Header) (*http.Request, error) {
	dn, y := http.NewRequestWithContext(aa, sv, ah, nil)
	if y != nil {
		return nil, y
	}
	dn.Header.Set("User-Agent", p.ua)
	dn.Header.Set("Referer", p.cb)
	dn.Header.Set("Origin", p.fo)
	dn.Header.Set("Accept", "*/*")
	for _, k := range qo {
		if v := cy.Get(k); v != "" {
			dn.Header.Set(k, v)
		}
	}
	return dn, nil
}
func (p *ProxyServer) qc(w http.ResponseWriter, r *http.Request, ah, ce, ag string) bool {
	if !p.ug(w, r, ag, ce) {
		return false
	}
	p.gr(ah, ag)
	if id := jx(ce); id > 0 {
		p.kx(ah, id)
	}
	return true
}
func (p *ProxyServer) rv(w http.ResponseWriter, r *http.Request) {
	es(w.Header(), "GET, HEAD, OPTIONS")
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}
	be := strings.TrimPrefix(r.URL.Path, "/__cdn/")
	if be == "" || be == "__probe" {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	kz := strings.IndexByte(be, '/')
	if kz <= 0 {
		http.Error(w, "Bad relay target", http.StatusBadRequest)
		return
	}
	ad, ce := be[:kz], be[kz:]
	if !nl(ad) && !sh(ad) {
		http.Error(w, "Host not allowed", http.StatusForbidden)
		return
	}
	var tb strings.Builder
	tb.Grow(len(ad) + len(ce) + 9 + len(r.URL.RawQuery))
	tb.WriteString("https://")
	tb.WriteString(ad)
	tb.WriteString(ce)
	if q := r.URL.RawQuery; q != "" {
		tb.WriteByte('?')
		tb.WriteString(q)
	}
	ah := tb.String()
	if p.qj(w, r, ah) {
		return
	}
	pl := time.Now()
	var ag string
	if p.ao != "" && isCacheAssetURL(ah) && !jw(ah) {
		ag = p.db(ah)
		if p.qc(w, r, ah, ce, ag) {
			if !p.bt {
				log.Printf("[RELAY] HIT %s", ah)
			}
			p.do("HIT %s", ah)
			return
		}
	}
	if am, bk := p.ke(ah); bk {
		es(w.Header(), "GET, HEAD, OPTIONS")
		w.WriteHeader(am)
		p.do("%d 0ms negative-cache %s", am, ah)
		return
	}
	dj, y := p.oq(r.Context(), r.Method, ah, r.Header)
	if y != nil {
		http.Error(w, y.Error(), http.StatusBadRequest)
		return
	}
	atomic.AddInt64(&p.jj, 1)
	defer atomic.AddInt64(&p.jj, -1)
	ak, y := p.dz.Do(dj)
	if y != nil {
		p.do("ERR %dms %s (%v)", time.Since(pl).Milliseconds(), ah, y)
		http.Error(w, "Relay Error: "+y.Error(), http.StatusBadGateway)
		return
	}
	defer ak.Body.Close()
	wh := w.Header()
	jk(wh, ak.Header, nh)
	es(wh, "GET, HEAD, OPTIONS")
	switch ak.StatusCode {
	case http.StatusOK:
		hl(wh)
		p.om(ah)
	case http.StatusForbidden, http.StatusNotFound:
		p.on(ah, ak.StatusCode)
	}
	if !p.bt {
		log.Printf("[RELAY] %d %s", ak.StatusCode, ah)
	}
	p.do("%d %dms %s", ak.StatusCode, time.Since(pl).Milliseconds(), ah)
	w.WriteHeader(ak.StatusCode)
	if ag == "" || ak.StatusCode != http.StatusOK || r.Method != http.MethodGet {
		ax := bp.Get().([]byte)
		_, _ = io.CopyBuffer(w, ak.Body, ax)
		bp.Put(ax)
		return
	}
	_ = p.la(ah, ce, ag, ak.Header.Get("Content-Type"), ak.Body, ak.ContentLength, w)
}
func (p *ProxyServer) lj(aa context.Context, af string) (bool, bool) {
	z, dv, _, _ := p.hq(aa, af)
	return z, dv
}
func (p *ProxyServer) hq(aa context.Context, af string) (bool, bool, int, string) {
	if p.ao == "" || !isCacheAssetURL(af) || jw(af) {
		return false, false, 0, "not cacheable"
	}
	ag := p.db(af)
	if fi, y := os.Stat(ag); y == nil {
		if time.Since(fi.ModTime()) <= je {
			p.gr(af, ag)
			return true, true, 200, ""
		}
		_ = os.Remove(ag)
		_ = os.Remove(ag + ".meta")
	}
	if am, bk := p.ke(af); bk {
		return false, false, am, "negative-cached (recent upstream 4xx)"
	}
	type ev struct {
		qr atomic.Bool
		z  bool
		am int
		gj string
	}
	ch := p.ve.DoChan(af, func() (any, error) {
		dn, y := p.oq(context.WithoutCancel(aa), http.MethodGet, af, nil)
		if y != nil {
			return &ev{z: false, am: 0, gj: "request: " + y.Error()}, nil
		}
		for i := 0; i < 100 && atomic.LoadInt64(&p.jj) > 0; i++ {
			if !um(aa, 50*time.Millisecond) {
				return &ev{z: false, am: 0, gj: "canceled while yielding to client traffic"}, nil
			}
		}
		ak, y := p.dz.Do(dn)
		if y != nil {
			return &ev{z: false, am: 0, gj: "fetch: " + y.Error()}, nil
		}
		defer ak.Body.Close()
		if ak.StatusCode != http.StatusOK {
			p.on(af, ak.StatusCode)
			return &ev{z: false, am: ak.StatusCode}, nil
		}
		p.om(af)
		if y := p.la(af, "", ag, ak.Header.Get("Content-Type"), io.LimitReader(ak.Body, 64<<20), ak.ContentLength, nil); y != nil {
			return &ev{z: false, am: ak.StatusCode, gj: y.Error()}, nil
		}
		return &ev{z: true, am: ak.StatusCode}, nil
	})
	select {
	case bd := <-ch:
		wr := bd.Val.(*ev)
		if wr.qr.CompareAndSwap(false, true) {
			return wr.z, false, wr.am, wr.gj
		}
		if fi, y := os.Stat(ag); y == nil && time.Since(fi.ModTime()) <= je {
			p.gr(af, ag)
			return true, true, 200, ""
		}
		return false, false, 0, "in-flight warm did not produce a cache entry"
	case <-aa.Done():
		return false, false, 0, "canceled while waiting for in-flight warm"
	}
}
func jx(bi string) int64 {
	if !strings.HasSuffix(bi, ".json") {
		return 0
	}
	i := strings.LastIndex(bi, "/maps/")
	if i < 0 {
		return 0
	}
	j, ef := i+6, len(bi)-5
	if j >= ef {
		return 0
	}
	var id int64
	for ; j < ef; j++ {
		c := bi[j]
		if c < '0' || c > '9' {
			return 0
		}
		d := int64(c - '0')
		if id > (oe-d)/10 {
			return 0
		}
		id = id*10 + d
	}
	return id
}
func ob(ai []byte) []int64 {
	b := ai[:min(len(ai), 300)]
	b, z := mc(b, `{"id":`)
	if !z {
		return nil
	}
	if _, b, z = pq(b, false); !z {
		return nil
	}
	uu := [...]string{`,"topNeighbourId":`, `,"bottomNeighbourId":`, `,"leftNeighbourId":`, `,"rightNeighbourId":`}
	var aj []int64
	for _, ut := range uu {
		var v int64
		if b, z = mc(b, ut); !z {
			return nil
		}
		if v, b, z = pq(b, true); !z {
			return nil
		}
		if v > 0 && !slices.Contains(aj, v) {
			aj = append(aj, v)
		}
	}
	return aj
}
func oc(ai []byte, fm string) []string {
	aj := make([]string, 0, kl)
	el := 0
	for i := 0; i < len(ai); {
		j := bytes.IndexByte(ai[i:], '"')
		if j < 0 {
			break
		}
		fr := i + j
		k := fr + 1
		for k < len(ai) && ai[k] >= '0' && ai[k] <= '9' {
			k++
		}
		if k > fr+1 && k+2 < len(ai) && ai[k] == '"' && ai[k+1] == ':' && ai[k+2] == '{' {
			cs := k + 3
			ef := -1
			for t := cs; t < len(ai); t++ {
				if ai[t] == '}' {
					ef = t
					break
				}
				if ai[t] == '{' {
					break
				}
			}
			if ef >= 0 {
				el++
				ly := ai[cs:ef]
				if bytes.Contains(ly, []byte(`"sx"`)) {
					gi, cr := "png", ".png"
					if bytes.Contains(ly, []byte(`"jpg"`)) {
						gi, cr = "jpg", ".jpg"
					}
					aj = append(aj, fm+"/gfx/world/"+gi+"/"+string(ai[fr+1:k])+cr)
					if len(aj) == kl {
						break
					}
				}
				if el > kl {
					break
				}
				i = ef + 1
				continue
			}
		}
		i = fr + 1
	}
	return aj
}
func (p *ProxyServer) bs(hx string, dw ...any) {
	bn := fmt.Sprintf(hx, dw...)
	if !p.bt {
		log.Printf("%s", bn)
	}
	if p.ao == "" {
		return
	}
	p.ir.Lock()
	defer p.ir.Unlock()
	if p.ha == nil {
		f, y := os.OpenFile(filepath.Join(p.ao, "prefetch.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if y != nil {
			return
		}
		p.ha = f
	}
	var b strings.Builder
	b.Grow(len(bn) + 24)
	b.WriteString(time.Now().Format("2006-01-02 15:04:05"))
	b.WriteByte(' ')
	b.WriteString(bn)
	b.WriteByte('\n')
	if _, y := p.ha.WriteString(b.String()); y != nil {
		_ = p.ha.Close()
		p.ha = nil
	}
}
func (p *ProxyServer) do(hx string, dw ...any) {
	if p.ao == "" {
		return
	}
	en := filepath.Join(p.ao, "requests.log")
	p.ir.Lock()
	defer p.ir.Unlock()
	if p.hg == nil {
		var hm int64
		if fi, y := os.Stat(en); y == nil {
			hm = fi.Size()
		}
		p.ko = 16 << 20
		if hm > 16<<20 {
			_ = os.Remove(en + ".1")
			if os.Rename(en, en+".1") == nil {
				hm = 0
			} else {
				p.ko = hm + 4<<20
			}
		}
		f, y := os.OpenFile(en, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if y != nil {
			return
		}
		p.hg, p.kp = f, hm
	}
	var b strings.Builder
	b.Grow(len(hx) + 32)
	b.WriteString(time.Now().Format("15:04:05.000"))
	b.WriteByte(' ')
	fmt.Fprintf(&b, hx, dw...)
	b.WriteByte('\n')
	n, y := p.hg.WriteString(b.String())
	if y != nil || p.kp+int64(n) > p.ko {
		_ = p.hg.Close()
		p.hg = nil
		return
	}
	p.kp += int64(n)
}
func (p *ProxyServer) ke(af string) (int, bool) {
	p.fl.Lock()
	defer p.fl.Unlock()
	e, z := p.ci[af]
	if !z {
		return 0, false
	}
	if time.Since(e.at) > pd {
		delete(p.ci, af)
		return 0, false
	}
	return e.am, true
}
func (p *ProxyServer) on(af string, am int) {
	if am != http.StatusForbidden && am != http.StatusNotFound {
		return
	}
	cj := time.Now()
	p.fl.Lock()
	defer p.fl.Unlock()
	if p.ci == nil {
		p.ci = make(map[string]ij)
	}
	if len(p.ci) > 4096 {
		maps.DeleteFunc(p.ci, func(_ string, v ij) bool { return cj.Sub(v.at) > pd })
		if len(p.ci) > 4096 {
			clear(p.ci)
		}
	}
	p.ci[af] = ij{am: am, at: cj}
}
func (p *ProxyServer) om(af string) {
	p.fl.Lock()
	delete(p.ci, af)
	p.fl.Unlock()
}
func (p *ProxyServer) kx(af string, id int64) {
	if !p.kk || p.ao == "" || id <= 0 {
		return
	}
	if _, bk := p.ke(af); bk {
		return
	}
	p.hb.Lock()
	if p.dl == nil {
		p.dl = make(map[int64]time.Time)
	}
	if cf, z := p.dl[id]; z && time.Since(cf) < pb {
		p.hb.Unlock()
		return
	}
	if len(p.dl) > 8192 {
		cj := time.Now()
		maps.DeleteFunc(p.dl, func(_ int64, v time.Time) bool { return cj.Sub(v) > time.Hour })
	}
	p.dl[id] = time.Now()
	p.hb.Unlock()
	p.to.Do(p.up)
	select {
	case p.is <- gy{af: af, id: id, ey: 0}:
	default:
	}
}
func (p *ProxyServer) up() {
	p.is = make(chan gy, tp)
	for i := 0; i < max(p.he, 1); i++ {
		go p.tq()
	}
}
func (p *ProxyServer) tq() {
	aa := context.Background()
	for bz := range p.is {
		p.ts(aa, bz)
	}
}
func (p *ProxyServer) ts(aa context.Context, bz gy) {
	di := bz.af
	ag := p.db(di)
	if _, y := os.Stat(ag); y != nil {
		if z, _, am, df := p.hq(aa, di); !z {
			if am == http.StatusForbidden || am == http.StatusNotFound {
				p.bs("[PREFETCH] map %d not present upstream (status=%d)", bz.id, am)
			} else {
				p.bs("[PREFETCH] map %d json fetch failed status=%d err=%q", bz.id, am, df)
			}
			return
		}
	}
	ai, y := kn(ag, pc)
	if y != nil || len(ai) == 0 {
		return
	}
	fm := strings.TrimSuffix(di, "/maps/"+strconv.FormatInt(bz.id, 10)+".json")
	oo := ob(ai)
	or, ls, qb := 0, 0, 0
	if bz.ey <= p.hd {
		for _, iy := range oc(ai, fm) {
			qb++
			if z, dv := p.lj(aa, iy); dv {
				ls++
			} else if z {
				or++
			}
		}
	}
	p.bs("[PREFETCH] map %d d%d neighbours=%d tiles new=%d cached=%d total=%d",
		bz.id, bz.ey, len(oo), or, ls, qb)
	if bz.ey >= p.hc {
		return
	}
	for _, ep := range oo {
		p.hb.Lock()
		cf, z := p.dl[ep]
		cx := z && time.Since(cf) < pb
		if !cx {
			p.dl[ep] = time.Now()
		}
		p.hb.Unlock()
		if !cx {
			select {
			case p.is <- gy{af: fm + "/maps/" + strconv.FormatInt(ep, 10) + ".json", id: ep, ey: bz.ey + 1}:
			default:
			}
		}
	}
}
func nb(dq string, su, st int64, ur bool, fn func(int64) bool) {
	for _, eq := range strings.Split(dq, ",") {
		if eq = strings.TrimSpace(eq); eq == "" {
			continue
		}
		var lo, hi int64
		if nt, rs, z := strings.Cut(eq, "-"); z && nt != "" {
			l, rl := strconv.ParseInt(strings.TrimSpace(nt), 10, 64)
			h, rk := strconv.ParseInt(strings.TrimSpace(rs), 10, 64)
			if rl != nil || (ur && rk != nil) {
				continue
			}
			lo, hi = l, h
		} else if v, y := strconv.ParseInt(eq, 10, 64); y == nil {
			lo, hi = v, v
		}
		if lo < 1 {
			continue
		}
		hi = min(max(hi, lo), su, lo+st)
		for id := lo; id <= hi; id++ {
			if !fn(id) {
				return
			}
		}
	}
}
func rn(dq string, fn func(id int) bool) {
	nb(dq, 2_000_000_000, 200_000, false, func(id int64) bool { return fn(int(id)) })
}
func (p *ProxyServer) tl(aa context.Context, cn, dq string, jd int) {
	if cn == "" || dq == "" {
		return
	}
	jd = max(jd, 1)
	dc := strings.TrimRight(cn, "/")
	log.Printf("[PRECACHE] Warming maps from %s/maps/*.json (workers=%d)", dc, jd)
	var ej, jf, ei, ho int64
	var eg errgroup.Group
	eg.SetLimit(jd)
	rn(dq, func(id int) bool {
		if aa.Err() != nil {
			return false
		}
		eg.Go(func() error {
			if aa.Err() != nil {
				return nil
			}
			z, dv := p.lj(aa, dc+"/maps/"+strconv.Itoa(id)+".json")
			n := atomic.AddInt64(&ho, 1)
			switch {
			case dv:
				atomic.AddInt64(&jf, 1)
			case z:
				atomic.AddInt64(&ej, 1)
			default:
				atomic.AddInt64(&ei, 1)
			}
			if !p.bt && n%500 == 0 {
				log.Printf("[PRECACHE] %d processed (fetched=%d cached=%d failed=%d)",
					n, atomic.LoadInt64(&ej), atomic.LoadInt64(&jf), atomic.LoadInt64(&ei))
			}
			return nil
		})
		return true
	})
	_ = eg.Wait()
	log.Printf("[PRECACHE] done: total=%d fetched=%d cached=%d failed=%d",
		atomic.LoadInt64(&ho), atomic.LoadInt64(&ej), atomic.LoadInt64(&jf), atomic.LoadInt64(&ei))
}
func um(aa context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-aa.Done():
		return false
	}
}
func te(dq string) []int64 {
	var aj []int64
	nb(dq, 1<<62, 4096, true, func(id int64) bool {
		aj = append(aj, id)
		return len(aj) < 4096
	})
	return aj
}
func (p *ProxyServer) ue(aa context.Context, cn string, hk []int64, jz int, dd float64, qg bool) {
	if p.ao == "" {
		p.bs("[CRAWL] no -cache directory; skipping")
		return
	}
	dc := strings.TrimRight(cn, "/")
	if dc == "" {
		return
	}
	if dd <= 0 {
		dd = 1
	} else if dd > 50 {
		dd = 50
	}
	nx := rate.NewLimiter(rate.Limit(dd), 1)
	p.bs("[CRAWL] start base=%s seeds=%d max=%d rate=%.1f/s tiles=%t", dc, len(hk), jz, dd, qg)
	fv := make(map[int64]struct{}, 8192)
	dm := slices.Clone(hk)
	ej, kr, ei := 0, 0, 0
	for len(dm) > 0 && aa.Err() == nil {
		if jz > 0 && len(fv) >= jz {
			break
		}
		id := dm[0]
		dm = dm[1:]
		if id <= 0 {
			continue
		}
		if _, jp := fv[id]; jp {
			continue
		}
		fv[id] = struct{}{}
		di := dc + "/maps/" + strconv.FormatInt(id, 10) + ".json"
		ag := p.db(di)
		if _, y := os.Stat(ag); y != nil {
			if nx.Wait(aa) != nil {
				break
			}
			if z, _, am, df := p.hq(aa, di); !z {
				ei++
				p.bs("[CRAWL] map %d json failed status=%d err=%q", id, am, df)
				continue
			}
			ej++
		} else {
			kr++
			p.gr(di, ag)
		}
		ai, y := kn(ag, pc)
		if y != nil || len(ai) == 0 {
			continue
		}
		for _, ep := range ob(ai) {
			if _, jp := fv[ep]; !jp {
				dm = append(dm, ep)
			}
		}
		if qg {
			fm := strings.TrimSuffix(di, "/maps/"+strconv.FormatInt(id, 10)+".json")
			for _, iy := range oc(ai, fm) {
				if aa.Err() != nil {
					break
				}
				if _, y := os.Stat(p.db(iy)); y == nil {
					continue
				}
				if nx.Wait(aa) != nil {
					break
				}
				p.lj(aa, iy)
			}
		}
		if n := len(fv); n%100 == 0 {
			p.bs("[CRAWL] visited=%d queued=%d current=%d fetched=%d reused=%d failed=%d",
				n, len(dm), id, ej, kr, ei)
		}
	}
	p.bs("[CRAWL] done visited=%d queued=%d fetched=%d reused=%d failed=%d", len(fv), len(dm), ej, kr, ei)
}
func (p *ProxyServer) rx(w http.ResponseWriter, r *http.Request) {
	bu := r.URL
	bu.Scheme = cmp.Or(bu.Scheme, "http")
	bu.Host = cmp.Or(bu.Host, r.Host)
	if bu.Host == "" {
		http.Error(w, "Bad Request: Missing host", http.StatusBadRequest)
		return
	}
	if (strings.HasSuffix(bu.Path, "/__disguise.js") || strings.HasSuffix(bu.Path, "/mobile-disguise.js")) && p.nd() != "" {
		h := w.Header()
		h.Set("Content-Type", "application/javascript; charset=utf-8")
		h.Set("Access-Control-Allow-Origin", "*")
		h.Set("Cache-Control", "no-cache")
		h.Set("Content-Length", strconv.Itoa(len(p.jn)))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(p.jn)
		return
	}
	ah := bu.String()
	var ag string
	if p.ao != "" && r.Method == http.MethodGet && isCacheAssetURL(ah) && !jw(ah) {
		ag = p.db(ah)
		if p.qc(w, r, ah, bu.Path, ag) {
			return
		}
	}
	dj, y := http.NewRequestWithContext(r.Context(), r.Method, ah, r.Body)
	if y != nil {
		http.Error(w, y.Error(), http.StatusBadRequest)
		return
	}
	jk(dj.Header, r.Header, hopReqHeaders)
	p.po(dj.Header)
	p.qi(dj.Header, bu.Host)
	if ae := dj.Header.Get("Accept-Encoding"); ae != "" {
		if na := filterAcceptEncoding(ae); na != ae {
			dj.Header.Set("Accept-Encoding", na)
		}
	}
	ak, y := p.dz.Do(dj)
	if y != nil {
		http.Error(w, "Proxy Error: "+y.Error(), http.StatusBadGateway)
		return
	}
	defer ak.Body.Close()
	wh := w.Header()
	jk(wh, ak.Header, nh)
	wh.Set("Access-Control-Allow-Origin", "*")
	if ak.StatusCode == http.StatusOK && ag != "" {
		hl(wh)
	}
	ct := ak.Header.Get("Content-Type")
	no := (strings.Contains(ct, "text/html") || sa(bu.Path, ".html")) && ak.StatusCode == http.StatusOK
	var hr io.Reader = ak.Body
	jv := strings.EqualFold(ak.Header.Get("Content-Encoding"), "gzip")
	if jv && (no || ag != "") {
		if ne, rq := gzip.NewReader(ak.Body); rq == nil {
			defer ne.Close()
			hr = ne
			wh.Del("Content-Encoding")
			wh.Del("Content-Length")
			jv = false
		}
	}
	if no {
		wh.Del("Content-Security-Policy")
		wh.Del("Content-Security-Policy-Report-Only")
		if ex, tw := io.ReadAll(hr); tw == nil {
			if !bytes.Contains(ex, []byte("mobile-disguise.js")) && !bytes.Contains(ex, []byte("__disguise.js")) {
				ex = p.sd(ex)
			}
			wh.Set("Content-Length", strconv.Itoa(len(ex)))
			w.WriteHeader(ak.StatusCode)
			_, _ = w.Write(ex)
			return
		}
	}
	w.WriteHeader(ak.StatusCode)
	if ag != "" && ak.StatusCode == http.StatusOK && !jv {
		my := ak.ContentLength
		if ak.Header.Get("Content-Encoding") != "" {
			my = -1
		}
		_ = p.la(ah, bu.Path, ag, ct, hr, my, w)
		return
	}
	ax := bp.Get().([]byte)
	_, _ = io.CopyBuffer(w, hr, ax)
	bp.Put(ax)
}
func qu(dw []string, ii string) string {
	for i, a := range dw {
		if (a == "-"+ii || a == "--"+ii) && i+1 < len(dw) {
			return dw[i+1]
		}
		for _, th := range []string{"-" + ii + "=", "--" + ii + "="} {
			if v, z := strings.CutPrefix(a, th); z {
				return v
			}
		}
	}
	return ""
}
func sp(bi string) map[string]string {
	aj := map[string]string{}
	if bi == "" {
		return aj
	}
	aq, y := os.ReadFile(bi)
	if y != nil {
		log.Printf("[CONFIG] %s not readable (%v); using flags only", bi, y)
		return aj
	}
	for _, bn := range strings.Split(string(aq), "\n") {
		bn = strings.TrimSpace(bn)
		if bn == "" || strings.ContainsAny(bn[:1], "#;[") {
			continue
		}
		if k, v, z := strings.Cut(bn, "="); z && strings.TrimSpace(k) != "" {
			aj[strings.ToLower(strings.TrimSpace(k))] = strings.TrimSpace(v)
		}
	}
	return aj
}
func main() {
	lt := qu(os.Args[1:], "config")
	ht := sp(lt)
	cq := func(k, fb string) string { return cmp.Or(ht[k], fb) }
	ra := func(k string, fb int) int {
		if n, y := strconv.Atoi(ht[k]); y == nil && ht[k] != "" {
			return n
		}
		return fb
	}
	md := func(k string, fb bool) bool {
		switch strings.ToLower(ht[k]) {
		case "1", "true", "yes", "on":
			return true
		case "0", "false", "no", "off":
			return false
		}
		return fb
	}
	bc := flag.Int("port", ra("port", 8880), "Local listening port for HTTP/CONNECT proxy")
	ad := flag.String("host", cq("host", "127.0.0.1"), "Host address to bind the proxy server to")
	ds := flag.String("upstream", cq("upstream", ""), "Upstream proxy URL, or a comma-separated chain of them (http://ip:port or socks5://ip:port)")
	qp := flag.String("cdn-upstream", cq("cdn_upstream", ""), "Separate upstream for asset downloads, so they do not queue behind the game socket")
	mx := flag.String("exit-proxy", cq("exit_proxy", ""), "Fixed exit proxy reached through -upstream hops. Keeps one exit IP across hop failures.")
	rg := flag.String("dns", cq("dns", ""), "Custom DNS server to query (optional, default: system DNS)")
	ao := flag.String("cache", cq("cache", "proxy_cache"), "Directory to cache static game assets (optional)")
	bt := flag.Bool("quiet", md("quiet", false), "Quiet mode (suppress request logs)")
	en := flag.String("log", cq("log", ""), "Explicit log file (default: <cache>/proxy.log when -quiet is set)")
	fx := flag.Bool("allow-direct", md("allow_direct", false), "Fall back to a DIRECT connection when the upstream dies (EXPOSES THE REAL IP; off by default)")
	lv := flag.String("config", lt, "INI file with defaults for every flag (key=value, sections ignored)")
	uv := flag.String("token", "", "Optional local bearer authentication token")
	fq := flag.String("serial", "", "Hardware serial number to interpolate into disguise scripts")
	ft := flag.String("uuid", "", "Device UUID to interpolate into disguise scripts")
	bb := flag.String("model", "", "Device model name to interpolate into disguise scripts (e.g. SM-A515F)")
	dh := flag.String("manufacturer", "", "Device manufacturer to interpolate (e.g. samsung)")
	ua := flag.String("ua", "", "Device User-Agent string to interpolate into disguise scripts")
	fe := flag.String("gl-vendor", "", "OpenGL vendor string to interpolate (e.g. Qualcomm)")
	fd := flag.String("gl-renderer", "", "OpenGL renderer string to interpolate (e.g. Adreno (TM) 620)")
	fj := flag.String("texture-size", "", "Maximum GL texture size to interpolate (e.g. 16384)")
	cb := flag.String("referer", cq("referer", "https://www.dofus-touch.com/"), "Referer/Origin sent on CDN asset fetches (hotlink protection bypass)")
	cn := flag.String("assets-url", "", "Assets base URL for map precaching, e.g. https://dofustouch.cdn.ankama.com/assets/<version>")
	pa := flag.String("precache-maps", "", "Map IDs to precache into -cache, e.g. \"1-20000\" or \"7442,10354\" (requires -assets-url)")
	tm := flag.Int("precache-workers", 4, "Parallel workers for -precache-maps")
	tn := flag.Bool("prefetch-neighbours", true, "Warm neighbour map JSONs and tiles into -cache whenever a map is loaded")
	hc := flag.Int("prefetch-radius", 2, "Map graph depth warmed for JSON (1=direct neighbours, 2=neighbours of neighbours)")
	hd := flag.Int("prefetch-tiles-depth", 1, "Graph depth at which map tile images (gfx/world/*) are warmed")
	he := flag.Int("prefetch-workers", 3, "Parallel workers for neighbour prefetch")
	jl := flag.String("crawl", "", "Graph crawl seeds: BFS the map neighbour graph from these map IDs, warming JSON (+tiles with -crawl-tiles) into -cache (requires -assets-url)")
	qw := flag.Int("crawl-max", 0, "Stop the crawl after this many maps (0 = unlimited)")
	dd := flag.Float64("crawl-rate", 3, "Max upstream CDN requests per second during the crawl")
	qx := flag.Bool("crawl-tiles", false, "Also warm tile images for every crawled map")
	cg := flag.String("mods-dir", cq("mods_dir", ""), "Directory containing a mods rules.json (default: <cache>/mods)")
	sq := flag.String("login-host", "", "Host that the client's virtual origin (dofus.local) is mapped to (default: the production login proxy)")
	flag.Parse()
	gp = cmp.Or(*sq, gp)
	log.SetFlags(log.Ldate | log.Ltime)
	if *lv != "" {
		log.Printf("[CONFIG] loaded %s", *lv)
	}
	af := *en
	if af == "" && *bt && *ao != "" {
		af = filepath.Join(*ao, "proxy.log")
	}
	if af != "" {
		if gi := filepath.Dir(af); gi != "" {
			_ = os.MkdirAll(gi, 0o755)
		}
		if f, y := os.OpenFile(af, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644); y == nil {
			log.SetOutput(f)
			log.Printf("[CONFIG] logging to %s", af)
		} else {
			log.Printf("[CONFIG] cannot open %s: %v", af, y)
		}
	}
	cl := NewFastDNSResolver(*rg, 10*time.Minute)
	if *ds != "" && strings.TrimSpace(*mx) == "" {
		ro, _, _ := strings.Cut(*ds, ",")
		if u, y := url.Parse(strings.TrimSpace(ro)); y == nil {
			if z, hv := qf(u, 1500*time.Millisecond); z {
				dt.Store(true)
				log.Printf("[UPSTREAM] %s reachable", *ds)
			} else {
				dt.Store(false)
				log.Printf("[UPSTREAM] %s is NOT reachable (%v).", *ds, hv)
				log.Printf("[UPSTREAM] Listening anyway and failing closed: every dial is refused until WARP is back, so no request can go direct and the real IP stays hidden.")
				log.Printf("[UPSTREAM] WARP drops its tunnel on a network change (wifi/ethernet) and usually returns within a minute. Fix now: warp-cli mode proxy && warp-cli connect")
				go vf(u, *ds)
			}
		}
	}
	ap := NewProxyServer(cl, *ds, *ao, *bt, *bc, *uv, *fq, *ft, *bb, *dh, *ua, *fe, *fd, *fj, *cb)
	ap.em = sc(*ao)
	ap.so()
	go func() {
		for {
			time.Sleep(10 * time.Second)
			ap.pp()
			if ap.pz++; ap.pz%6 == 0 {
				h, m := atomic.LoadUint64(&ap.lq), atomic.LoadUint64(&ap.lr)
				if h+m > 0 {
					log.Printf("[CACHE] %d hits, %d misses (%.0f%% served from disk)", h, m, 100.0*float64(h)/float64(h+m))
				}
			}
		}
	}()
	defer ap.pp()
	debug.SetMemoryLimit(384 << 20)
	if jh := strings.TrimSpace(*qp); jh != "" {
		if ck := ow(jh); len(ck) > 0 {
			ap.dz = td(ck, ap.cl)
			ap.qq = ck[0]
			if len(ck) == 1 {
				log.Printf("[UPSTREAM] asset downloads use their own egress: %s", ck[0].Redacted())
			} else {
				log.Printf("[UPSTREAM] asset downloads spread over %d egresses: %s ...", len(ck), ck[0].Redacted())
			}
		} else {
			log.Printf("[UPSTREAM] -cdn-upstream %q is not a usable proxy URL; ignoring it", jh)
		}
	}
	if lf := strings.TrimSpace(*mx); lf != "" {
		if ap.bm = ox(lf); ap.bm == nil {
			log.Printf("[UPSTREAM] -exit-proxy %q is not a usable proxy URL; ignoring it", lf)
		} else {
			ap.vb()
		}
	}
	ap.fx = *fx
	if *fx {
		log.Printf("[UPSTREAM] -allow-direct is ON: if the tunnel dies, traffic will go direct and Ankama will see this machine's real IP.")
	}
	ap.kk = *tn
	ap.hc = max(*hc, 1)
	ap.hd = max(*hd, 0)
	ap.he = min(max(*he, 1), 16)
	if ap.kk && ap.ao != "" {
		log.Printf("[PREFETCH] Neighbour prefetch enabled (radius=%d tiles-depth=%d workers=%d)",
			ap.hc, ap.hd, ap.he)
	}
	ku, uq := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer uq()
	if *pa != "" {
		if *cn == "" {
			log.Printf("[PRECACHE] -precache-maps set without -assets-url; skipping")
		} else {
			go ap.tl(ku, *cn, *pa, *tm)
		}
	}
	if *cg == "" && *ao != "" {
		*cg = filepath.Join(*ao, "mods")
	}
	if ap.cg = *cg; *cg != "" {
		ap.nz()
	}
	if *jl != "" {
		if *cn == "" {
			log.Printf("[CRAWL] -crawl set without -assets-url; skipping")
		} else if hk := te(*jl); len(hk) > 0 {
			go ap.ue(ku, *cn, hk, *qw, *dd, *qx)
		} else {
			log.Printf("[CRAWL] no valid seeds parsed from %q", *jl)
		}
	}
	pu := &http.Server{
		Addr:              net.JoinHostPort(*ad, strconv.Itoa(*bc)),
		Handler:           ap,
		ReadHeaderTimeout: 30 * time.Second,
		IdleTimeout:       300 * time.Second,
	}
	go func() {
		log.Printf("[READY] Listening on http://%s:%d (Model: %s) ...", *ad, *bc, *bb)
		if y := pu.ListenAndServe(); y != nil && y != http.ErrServerClosed {
			log.Fatalf("[FATAL] Proxy server failed: %v", y)
		}
	}()
	<-ku.Done()
	aa, gc := context.WithTimeout(context.Background(), 2*time.Second)
	defer gc()
	_ = pu.Shutdown(aa)
}
