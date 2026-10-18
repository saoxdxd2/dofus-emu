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

func TestMapJSONID(t *testing.T) {
	cases := map[string]int64{
		"/assets/3.3.9_znDI1I3e76AsBAbi3QoRVb_02piQ7P1x/maps/123995138.json": 123995138,
		"/assets/v/maps/262.json":   262,
		"/assets/v/maps/262.json.bak": 0,
		"/assets/v/gfx/world/png/7715.png": 0,
		"/assets/v/maps/abc.json":   0,
	}
	for path, want := range cases {
		if got := mapJSONID(path); got != want {
			t.Errorf("mapJSONID(%q) = %d, want %d", path, got, want)
		}
	}
}

func TestMapJSONNeighbours(t *testing.T) {
	body := []byte(`{"id":123995138,"topNeighbourId":123995137,"bottomNeighbourId":123995139,"leftNeighbourId":123994626,"rightNeighbourId":123995650,"shadowBonusOnEntities":-1,"cells":[{"l":66}]}`)
	got := mapJSONNeighbours(body)
	want := []int64{123995137, 123995139, 123994626, 123995650}
	if len(got) != len(want) {
		t.Fatalf("neighbours = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("neighbours = %v, want %v", got, want)
		}
	}
	edge := []byte(`{"id":262,"topNeighbourId":263,"bottomNeighbourId":261,"leftNeighbourId":-1,"rightNeighbourId":0}`)
	if got := mapJSONNeighbours(edge); len(got) != 2 || got[0] != 263 || got[1] != 261 {
		t.Errorf("edge neighbours = %v, want [263 261]", got)
	}
	if got := mapJSONNeighbours([]byte(`{"cells":[]}`)); got != nil {
		t.Errorf("no-header neighbours = %v, want nil", got)
	}
}

func TestMapTileURLs(t *testing.T) {
	body := []byte(`{"id":1,"atlasLayout":{"width":10,"height":10,"graphicsPositions":{"7715":{"sx":1258,"sy":394,"sw":30,"sh":16,"cw":201,"ch":106},"200406":{"sx":1,"sy":2,"sw":3,"sh":4,"jpg":1}}},"midgroundLayer":{"181":[{"g":7715,"x":1,"y":2}]},"cells":[{"l":66}]}`)
	got := mapTileURLs(body, "https://cdn.example/assets/v")
	want := []string{
		"https://cdn.example/assets/v/gfx/world/png/7715.png",
		"https://cdn.example/assets/v/gfx/world/jpg/200406.jpg",
	}
	if len(got) != len(want) {
		t.Fatalf("tileURLs = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("tileURLs = %v, want %v", got, want)
		}
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
