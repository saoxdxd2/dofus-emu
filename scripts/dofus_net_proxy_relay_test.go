package main

// Unit tests for the CDN relay helpers and the precache id feeder.
// Run with:
//   cd scripts && GO111MODULE=off go test dofus-net-proxy.go \
//     dofus_net_proxy_equiv_test.go dofus_net_proxy_relay_test.go

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestIsCdnAssetHost(t *testing.T) {
	allowed := []string{
		"dofustouch.cdn.ankama.com",
		"cdn.ankama.com",
		"dofustouch.cdn.ankama-games.com",
		"CDN.ANKAMA.COM",
		"dofustouch.cdn.ankama.com:443",
	}
	blocked := []string{
		"static.ankama.com",
		"api.ankama.com",
		"cdn.ankama.com.evil.io",
		"evilcdn.ankama.com",
		"ankama.com",
		"",
	}
	for _, h := range allowed {
		if !isCdnAssetHost(h) {
			t.Errorf("isCdnAssetHost(%q) = false, want true", h)
		}
	}
	for _, h := range blocked {
		if isCdnAssetHost(h) {
			t.Errorf("isCdnAssetHost(%q) = true, want false", h)
		}
	}
}

func TestIsVolatileCacheURL(t *testing.T) {
	volatile := []string{
		"https://dt-proxy-production-login.ankama-games.com/config.json?lang=en",
		"https://x.ankama.com/config.json",
		"https://x.ankama.com/version.json?build=1",
		"https://x.ankama.com/getLanguage.json",
	}
	stable := []string{
		"https://dofustouch.cdn.ankama.com/assets/3.3.9_x/maps/7442.json",
		"https://dofustouch.cdn.ankama.com/assets/3.3.9_x/gfx/world/7442.jpg",
	}
	for _, u := range volatile {
		if !isVolatileCacheURL(u) {
			t.Errorf("isVolatileCacheURL(%q) = false, want true", u)
		}
	}
	for _, u := range stable {
		if isVolatileCacheURL(u) {
			t.Errorf("isVolatileCacheURL(%q) = true, want false", u)
		}
	}
}

func TestFeedMapIDs(t *testing.T) {
	var got []int
	feedMapIDs("1-5,10,12-14", func(id int) bool { got = append(got, id); return true })
	want := []int{1, 2, 3, 4, 5, 10, 12, 13, 14}
	if len(got) != len(want) {
		t.Fatalf("feedMapIDs gave %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("feedMapIDs gave %v, want %v", got, want)
		}
	}

	// Early stop: fn returning false must halt the feed.
	count := 0
	feedMapIDs("1-1000000", func(id int) bool { count++; return count < 3 })
	if count != 3 {
		t.Fatalf("early stop: processed %d ids, want 3", count)
	}

	// Malformed tokens are skipped, single ids pass through, reversed ranges
	// collapse to their lower bound.
	got = got[:0]
	feedMapIDs("abc, ,0-3,-5,7-2,9", func(id int) bool { got = append(got, id); return true })
	want = []int{7, 9}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] {
		t.Fatalf("feedMapIDs malformed gave %v, want %v", got, want)
	}
}

func TestRelayProbeAndAllowlist(t *testing.T) {
	p := &ProxyServer{}

	// Probe: 204 without touching the network.
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8880/__cdn/__probe", nil)
	p.handleCDNRelay(rec, req)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("probe status = %d, want 204", rec.Code)
	}
	if rec.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Fatalf("probe missing CORS header")
	}

	// OPTIONS: 204 CORS preflight.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodOptions, "http://127.0.0.1:8880/__cdn/dofustouch.cdn.ankama.com/assets/x.png", nil)
	p.handleCDNRelay(rec, req)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("OPTIONS status = %d, want 204", rec.Code)
	}

	// Disallowed host: 403 and no upstream fetch (client is nil, would panic
	// if the host check failed).
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8880/__cdn/evil.com/assets/x.png", nil)
	p.handleCDNRelay(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("disallowed host status = %d, want 403", rec.Code)
	}

	// Missing path after host: 400.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8880/__cdn/evil.com", nil)
	p.handleCDNRelay(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("bad target status = %d, want 400", rec.Code)
	}

	// Non-GET/HEAD/OPTIONS: 405.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8880/__cdn/dofustouch.cdn.ankama.com/assets/x.png", nil)
	p.handleCDNRelay(rec, req)
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("POST status = %d, want 405", rec.Code)
	}
}

func TestIsLocalRequestHost(t *testing.T) {
	if !isLocalRequestHost("") || !isLocalRequestHost("127.0.0.1:8880") ||
		!isLocalRequestHost("localhost:8880") || !isLocalRequestHost("[::1]:8880") {
		t.Fatal("loopback hosts must be allowed")
	}
	if isLocalRequestHost("10.0.0.5:8880") || isLocalRequestHost("example.com") {
		t.Fatal("non-loopback hosts must be rejected")
	}
}
