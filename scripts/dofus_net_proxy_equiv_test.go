package main

// Equivalence tests: prove the hand-rolled hot-path helpers behave exactly
// like the regex/split implementations they replaced.

import (
	"math/rand"
	"net/http"
	"regexp"
	"strings"
	"testing"
)

var (
	legacyReWebViewToken = regexp.MustCompile(`(?i);\s*wv\b|Version/[0-9.]+\s*`)
	legacyReCacheAsset   = regexp.MustCompile(`(?i)\.(?:png|jpe?g|webp|mp3|ogg|wasm|zip|pak|dat|json)$|/assets/`)
	legacyReLeakUA       = regexp.MustCompile(`(?i)x86|sdk|emulator|goldfish|ranchu|mozilla/`)
)

func legacyStripWebViewTokens(ua string) string {
	return legacyReWebViewToken.ReplaceAllString(ua, "")
}

func TestStripWebViewTokensEquivalence(t *testing.T) {
	cases := []string{
		"Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36",
		"Dalvik/2.1.0 (Linux; U; Android 10; SM-A515F Build/QP1A.190711.020)",
		"ok-client/1.2.3",
		"test; WV more",
		"test;wv",
		"test;wvx",
		"Version/4.0 rest",
		"version/12.34.56rest",
		"Version/",
		"Version/4.",
		"no-version-no-wv",
		"; wv",
		"a;   wv   b",
		"Version/1.0",
		"xVersion/1.0",
		"Version/1.0Version/2.0",
	}
	for _, c := range cases {
		want := legacyStripWebViewTokens(c)
		got := stripWebViewTokens(c)
		if want != got {
			t.Errorf("stripWebViewTokens(%q) = %q, want %q", c, got, want)
		}
	}
}

func TestStripWebViewTokensFuzzEquivalence(t *testing.T) {
	rng := rand.New(rand.NewSource(42))
	alphabet := []byte("abcdefgwvV; /.0123456789\t\nVersionxWV")
	for i := 0; i < 20000; i++ {
		n := rng.Intn(40)
		b := make([]byte, n)
		for j := range b {
			b[j] = alphabet[rng.Intn(len(alphabet))]
		}
		s := string(b)
		want := legacyStripWebViewTokens(s)
		got := stripWebViewTokens(s)
		if want != got {
			t.Fatalf("stripWebViewTokens mismatch on %q: got %q want %q", s, got, want)
		}
	}
}

func legacyIsCacheAssetURL(u string) bool {
	return legacyReCacheAsset.MatchString(u)
}

func TestUaLeaksDesktopEquivalence(t *testing.T) {
	cases := []string{
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Safari/537.36",
		"Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36",
		"Dalvik/2.1.0 (Linux; U; Android 10; SM-A515F Build/QP1A.190711.020)",
		"ok-client/1.2.3",
		"test sdk build",
		"test emulator thing",
		"goldfish device",
		"ranchu phone",
		"x86_64 guest",
		"",
		"SDK",
		"MOZILLA/5.0",
	}
	for _, c := range cases {
		want := legacyReLeakUA.MatchString(c)
		got := uaLeaksDesktop(c)
		if want != got {
			t.Errorf("uaLeaksDesktop(%q) = %v, want %v", c, got, want)
		}
	}
}

func TestIsCacheAssetURLEquivalence(t *testing.T) {
	cases := []string{
		"https://static.ankama.com/assets/game/packs/123456/pak_0.dat",
		"https://static.ankama.com/dofus-touch/www/bundle.js",
		"https://static.ankama.com/img/background.png",
		"https://static.ankama.com/img/photo.JPG",
		"https://static.ankama.com/img/photo.jpeg",
		"https://static.ankama.com/img/photo.WebP",
		"https://static.ankama.com/audio/song.mp3",
		"https://static.ankama.com/audio/song.ogg",
		"https://static.ankama.com/mod.wasm",
		"https://static.ankama.com/pack.zip",
		"https://static.ankama.com/data.dat",
		"https://static.ankama.com/data.datx",
		"https://static.ankama.com/data.js",
		"https://api.ankama.com/dofus/touch/v1/monsters?limit=50",
		"https://static.ankama.com/dofus-touch/www/script.js.map",
		"https://static.ankama.com/assets/",
		"https://static.ankama.com/no-assets-here/",
		".png",
		"file.PNG",
		"file.jpeg?q=1",
		"file.jpg ",
		"file.pak",
		"file.dat",
		"file.zip",
		"file.mp3",
		"file.ogg",
		"file.webp",
		"file.wasm",
		"file.json",
		"file.JSON",
		"file.jsonx",
		"file.nojson",
		"https://dofustouch.cdn.ankama.com/assets/3.3.9_abc/maps/7442.json",
		"a/b.json?v=1",
		"file.json ",
		".json",
		"config.json",
		"file.bmp",
		"file.tar.gz",
		"file.pn",
		"file.pngg",
		"",
		".",
		"..png",
		"a/./b.png",
		"a/.png",
		"a/b.png#frag",
		"a/b.png?x=1",
	}
	for _, c := range cases {
		want := legacyIsCacheAssetURL(c)
		got := isCacheAssetURL(c)
		if want != got {
			t.Errorf("isCacheAssetURL(%q) = %v, want %v", c, got, want)
		}
	}
}

func TestIsCacheAssetURLFuzzEquivalence(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	alphabet := []byte("abcxyznPNGjpgwebpmw3ogipakdat/assets/.?#=&")
	for i := 0; i < 20000; i++ {
		n := rng.Intn(32)
		b := make([]byte, n)
		for j := range b {
			b[j] = alphabet[rng.Intn(len(alphabet))]
		}
		s := string(b)
		want := legacyIsCacheAssetURL(s)
		got := isCacheAssetURL(s)
		if want != got {
			t.Fatalf("isCacheAssetURL mismatch on %q: got %v want %v", s, got, want)
		}
	}
}

func legacyFilterAcceptEncoding(ae string) string {
	var kept []string
	for _, part := range strings.Split(ae, ",") {
		t := strings.TrimSpace(part)
		l := strings.ToLower(t)
		if t != "" && l != "br" && !strings.HasPrefix(l, "br;") && !strings.HasPrefix(l, "br ") {
			kept = append(kept, t)
		}
	}
	if len(kept) == 0 {
		return "gzip, deflate"
	}
	return strings.Join(kept, ", ")
}

func TestFilterAcceptEncodingEquivalence(t *testing.T) {
	cases := []string{
		"gzip, deflate, br, zstd",
		"gzip",
		"br",
		"br;q=1.0, gzip",
		"gzip, br ",
		"BR",
		"gzip, deflate",
		"",
		" , ",
		"gzip,,deflate",
		"identity",
	}
	for _, c := range cases {
		want := legacyFilterAcceptEncoding(c)
		got := filterAcceptEncoding(c)
		if want != got {
			t.Errorf("filterAcceptEncoding(%q) = %q, want %q", c, got, want)
		}
	}
}

func TestFilterAcceptEncodingFuzzEquivalence(t *testing.T) {
	rng := rand.New(rand.NewSource(99))
	alphabet := []byte("gzip, deflate;brqzstd identity\t\n")
	for i := 0; i < 20000; i++ {
		n := rng.Intn(24)
		b := make([]byte, n)
		for j := range b {
			b[j] = alphabet[rng.Intn(len(alphabet))]
		}
		s := string(b)
		want := legacyFilterAcceptEncoding(s)
		got := filterAcceptEncoding(s)
		if want != got {
			t.Fatalf("filterAcceptEncoding mismatch on %q: got %q want %q", s, got, want)
		}
	}
}

func TestHopHeaderSetsCoverOldBehavior(t *testing.T) {
	// Old code: hopReqHeaders keyed by lowercase name, matched against
	// strings.ToLower(headerKey). New code: canonical-keyed set matched
	// directly. Verify the same headers are removed.
	oldReq := map[string]struct{}{
		"proxy-connection": {}, "proxy-authenticate": {}, "proxy-authorization": {},
		"via": {}, "x-forwarded-for": {}, "x-forwarded-host": {}, "x-forwarded-proto": {},
		"forwarded": {}, "x-real-ip": {}, "client-ip": {}, "x-requested-with": {},
		"connection": {}, "keep-alive": {}, "te": {}, "trailers": {}, "transfer-encoding": {}, "upgrade": {},
	}
	for k := range oldReq {
		canon := http.CanonicalHeaderKey(k)
		if _, ok := hopReqHeaders[canon]; !ok {
			t.Errorf("hopReqHeaders missing %q (canonical %q)", k, canon)
		}
	}
	for k := range hopReqHeaders {
		if _, ok := oldReq[strings.ToLower(k)]; !ok {
			t.Errorf("hopReqHeaders has extra %q not in old set", k)
		}
	}
}