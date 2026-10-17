package main

// Benchmarks for the hottest per-request paths of dofus-net-proxy.
// Run with:
//   cd scripts && GO111MODULE=off go test -bench=. -benchmem -run=NONE \
//     dofus-net-proxy.go dofus_net_proxy_bench_test.go
// (benchmark_proxy.go also defines main, so pass the file list explicitly.)

import (
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

var benchUAStrings = []string{
	"Dalvik/2.1.0 (Linux; U; Android 10; SM-A515F Build/QP1A.190711.020)",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Safari/537.36",
	"Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36",
	"ok-client/1.2.3",
}

func BenchmarkReLeakUA(b *testing.B) {
	ua := benchUAStrings[1]
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = uaLeaksDesktop(ua)
	}
}

func newBenchProxy() *ProxyServer {
	return NewProxyServer(NewFastDNSResolver("", 0), "", "", true, 8880, "", "", "", "SM-A515F", "samsung", benchUAStrings[3], "ARM", "Mali-G72 MP3", "8192")
}

func BenchmarkSanitizeRequestHeaders(b *testing.B) {
	p := newBenchProxy()
	h := make(http.Header, 8)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		for _, ua := range benchUAStrings {
			h.Set("User-Agent", ua)
			h.Set("Sec-CH-UA-Arch", `"x86"`)
			h.Set("Sec-CH-UA-Platform", `"Win32"`)
			h.Set("X-Forwarded-For", "1.2.3.4")
			h.Set("Accept-Language", "en-US")
			p.sanitizeRequestHeaders(h)
		}
	}
}

func BenchmarkStripWebViewTokens(b *testing.B) {
	ua := benchUAStrings[2]
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		clean := strings.TrimSpace(stripWebViewTokens(ua))
		_ = clean
	}
}

var cacheAssetURLs = []string{
	"https://static.ankama.com/assets/game/packs/123456/pak_0.dat",
	"https://static.ankama.com/dofus-touch/www/bundle.js",
	"https://static.ankama.com/dofus-touch/www/styles.css",
	"https://api.ankama.com/dofus/touch/v1/monsters?limit=50",
	"https://static.ankama.com/img/background.png",
}

func BenchmarkCacheAssetRegex(b *testing.B) {
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		for _, u := range cacheAssetURLs {
			_ = isCacheAssetURL(u)
		}
	}
}

func BenchmarkHTMLInjectionFullRead(b *testing.B) {
	p := newBenchProxy()
	// Simulated HTML document body (~64 KB) as returned by an upstream page.
	head := []byte("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>t</title></head><body>")
	page := make([]byte, 0, 64*1024)
	page = append(page, head...)
	for len(page) < 64*1024 {
		page = append(page, "<div class=\"row\">lorem ipsum dolor sit amet consectetur</div>"...)
	}
	page = append(page, "</body></html>"...)

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = p.injectDisguise(page)
	}
}

func BenchmarkCopyResponseHeaders(b *testing.B) {
	resp := &http.Response{Header: http.Header{
		"Content-Type":            {"text/html; charset=utf-8"},
		"Content-Length":          {"65536"},
		"Content-Encoding":        {"gzip"},
		"X-Frame-Options":         {"SAMEORIGIN"},
		"Set-Cookie":              {"a=b", "c=d"},
		"Content-Security-Policy": {"default-src 'self'"},
		"X-Request-Id":            {"abc-123"},
		"Cache-Control":           {"public, max-age=3600"},
	}}
	rec := httptest.NewRecorder()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		wh := rec.Header()
		copyHeaderFiltered(wh, resp.Header, hopRespHeaders)
	}
}

func BenchmarkAcceptEncodingFilter(b *testing.B) {
	ae := "gzip, deflate, br, zstd"
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = filterAcceptEncoding(ae)
	}
}

func BenchmarkDNSCacheHit(b *testing.B) {
	r := NewFastDNSResolver("", 0)
	ctx := httptest.NewRequest(http.MethodGet, "http://example.com/", nil).Context()
	// Seed the cache directly so the benchmark measures the hit path only
	// and never touches the network.
	r.cache["example.com"] = DNSCacheEntry{
		IPs:       []net.IP{net.ParseIP("93.184.216.34")},
		ExpiresAt: time.Now().Add(time.Hour),
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = r.Lookup(ctx, "example.com:443")
	}
}