package patcher

import (
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// chunkReader returns data in artificially small or uneven chunks to stress boundary carry.
type chunkReader struct {
	r         io.Reader
	chunkSize int
}

func (c *chunkReader) Read(p []byte) (int, error) {
	limit := len(p)
	if c.chunkSize > 0 && c.chunkSize < limit {
		limit = c.chunkSize
	}
	return c.r.Read(p[:limit])
}

// TestBoundaryStraddling verifies that match tokens straddling the exact 65536 (streamBufferSize)
// boundary are reliably detected and replaced without corrupting preceding or succeeding data.
func TestBoundaryStraddling(t *testing.T) {
	find := "MAGIC_REPLACE_TOKEN_12345"
	repl := "SUCCESSFUL_SWAP"

	for offset := -len(find) + 1; offset < len(find); offset++ {
		t.Run(fmt.Sprintf("offset_%d", offset), func(t *testing.T) {
			tokenStart := streamBufferSize + offset

			var input bytes.Buffer
			// Fill up to tokenStart with padding
			paddingA := strings.Repeat("A", tokenStart)
			input.WriteString(paddingA)
			input.WriteString(find)
			// Trailing padding to ensure at least two full buffers are read
			paddingB := strings.Repeat("B", streamBufferSize)
			input.WriteString(paddingB)

			var output bytes.Buffer
			n, err := StreamReplace(&output, bytes.NewReader(input.Bytes()), find, repl, 0)
			if err != nil {
				t.Fatalf("StreamReplace failed: %v", err)
			}

			expected := paddingA + repl + paddingB
			if output.String() != expected {
				t.Fatalf("boundary mismatch at offset %d: expected len %d, got len %d (wrote %d)",
					offset, len(expected), output.Len(), n)
			}
		})
	}
}

// TestMultiByteUTF8BoundaryStraddling ensures multi-byte UTF-8 sequences (3-byte and 4-byte runes)
// that straddle the 65536-byte boundary are not fragmented or corrupted.
func TestMultiByteUTF8BoundaryStraddling(t *testing.T) {
	// A 4-byte emoji and 3-byte Cyrillic/CJK runes
	find := "⚡Привет_世界_🔥_DofusTouch"
	repl := "✨Salut_Monde_🌟_JeuModifie"

	for offset := -len(find) + 1; offset < len(find); offset++ {
		t.Run(fmt.Sprintf("utf8_offset_%d", offset), func(t *testing.T) {
			tokenStart := streamBufferSize + offset

			var input bytes.Buffer
			paddingA := strings.Repeat("x", tokenStart)
			input.WriteString(paddingA)
			input.WriteString(find)
			paddingB := strings.Repeat("y", streamBufferSize+128)
			input.WriteString(paddingB)

			var output bytes.Buffer
			_, err := StreamReplace(&output, bytes.NewReader(input.Bytes()), find, repl, 0)
			if err != nil {
				t.Fatalf("StreamReplace failed: %v", err)
			}

			expected := paddingA + repl + paddingB
			if output.String() != expected {
				t.Fatalf("UTF-8 boundary mismatch at offset %d: expected output length %d, got %d",
					offset, len(expected), output.Len())
			}
		})
	}
}

// TestAdjacentAndOverlappingMatches tests multiple consecutive match tokens touching the 64 KB seam.
func TestAdjacentAndOverlappingMatches(t *testing.T) {
	find := "PATCH_ME"
	repl := "DONE"

	// Place 3 adjacent tokens right across the seam: token 1 before, token 2 straddling, token 3 after.
	tokenStart := streamBufferSize - len(find) - 2

	var input bytes.Buffer
	paddingA := strings.Repeat("Z", tokenStart)
	input.WriteString(paddingA)
	input.WriteString(find) // token 1
	input.WriteString(find) // token 2 (straddles seam)
	input.WriteString(find) // token 3
	paddingB := strings.Repeat("W", streamBufferSize)
	input.WriteString(paddingB)

	var output bytes.Buffer
	_, err := StreamReplace(&output, bytes.NewReader(input.Bytes()), find, repl, 0)
	if err != nil {
		t.Fatalf("StreamReplace failed: %v", err)
	}

	expected := paddingA + repl + repl + repl + paddingB
	if output.String() != expected {
		t.Fatalf("adjacent matches mismatch across boundary: expected %d bytes, got %d",
			len(expected), output.Len())
	}
}

// TestUnevenChunkSizes verifies that arbitrary chunk read boundaries don't cause token loss.
func TestUnevenChunkSizes(t *testing.T) {
	find := "NEEDLE"
	repl := "THREAD"

	data := strings.Repeat("ABC", 30000) + find + strings.Repeat("XYZ", 30000)
	expected := strings.Repeat("ABC", 30000) + repl + strings.Repeat("XYZ", 30000)

	for _, chunkSize := range []int{1, 3, 7, 128, 1024, 65535, 65536, 65537} {
		cr := &chunkReader{r: strings.NewReader(data), chunkSize: chunkSize}
		var output bytes.Buffer
		_, err := StreamReplace(&output, cr, find, repl, 0)
		if err != nil {
			t.Fatalf("chunkSize %d failed: %v", chunkSize, err)
		}
		if output.String() != expected {
			t.Fatalf("chunkSize %d produced mismatch: got %d bytes, expected %d",
				chunkSize, output.Len(), len(expected))
		}
	}
}

// TestGzipStreamReplacement verifies end-to-end gzip decompression -> StreamReplace -> recompression,
// ensuring that partial-chunk rewrites never corrupt compressed script assets.
func TestGzipStreamReplacement(t *testing.T) {
	find := "window.ANKAMA_INITIAL_CONFIG"
	repl := "window.DOFUS_EMU_OVERRIDE_CFG"

	// Create a simulated 256 KB script.js bundle
	var originalScript bytes.Buffer
	for i := 0; i < 4000; i++ {
		fmt.Fprintf(&originalScript, "function step%d() { var x = %d; return x * 2; }\n", i, i)
		if i == 1200 { // Place token roughly at ~70 KB
			originalScript.WriteString("var config = " + find + ";\n")
		}
	}

	// Compress original script
	var gzipped bytes.Buffer
	gw := gzip.NewWriter(&gzipped)
	if _, err := gw.Write(originalScript.Bytes()); err != nil {
		t.Fatalf("gzip write error: %v", err)
	}
	if err := gw.Close(); err != nil {
		t.Fatalf("gzip close error: %v", err)
	}

	// Read and decompress
	gr, err := gzip.NewReader(&gzipped)
	if err != nil {
		t.Fatalf("gzip reader error: %v", err)
	}
	defer gr.Close()

	// Stream replace uncompressed body into a new gzip writer
	var patchedGzip bytes.Buffer
	newGw := gzip.NewWriter(&patchedGzip)

	written, err := StreamReplace(newGw, gr, find, repl, 0)
	if err != nil {
		t.Fatalf("StreamReplace during gzip failed: %v", err)
	}
	if err := newGw.Close(); err != nil {
		t.Fatalf("new gzip close error: %v", err)
	}

	if written == 0 {
		t.Fatalf("expected written > 0, got %d", written)
	}

	// Verify the patched gzipped payload decompresses cleanly
	verifyGr, err := gzip.NewReader(&patchedGzip)
	if err != nil {
		t.Fatalf("verification gzip reader error: %v", err)
	}
	defer verifyGr.Close()

	var decompressed bytes.Buffer
	if _, err := io.Copy(&decompressed, verifyGr); err != nil {
		t.Fatalf("decompression error: %v", err)
	}

	expectedScript := strings.ReplaceAll(originalScript.String(), find, repl)
	if decompressed.String() != expectedScript {
		t.Fatalf("decompressed script does not match expected patched script (len %d vs %d)",
			decompressed.Len(), len(expectedScript))
	}
}

// TestHTTPTransferEncodingAndContentLength verifies HTTP streaming semantics:
// When token replacement alters payload size, Content-Length must not be hardcoded to original length
// and Transfer-Encoding chunked handles the streaming replacement safely.
func TestHTTPTransferEncodingAndContentLength(t *testing.T) {
	find := "SHORT"
	repl := "MUCH_LONGER_EXPANDED_REPLACEMENT_TOKEN_STRING"

	inputBody := strings.Repeat("HEAD_", 13000) + find + strings.Repeat("_TAIL", 13000) // ~130 KB
	expectedBody := strings.ReplaceAll(inputBody, find, repl)

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Notice: when doing dynamic streaming replacement, omitting Content-Length allows
		// Go's HTTP server to automatically use chunked Transfer-Encoding.
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		w.WriteHeader(http.StatusOK)

		src := strings.NewReader(inputBody)
		_, err := StreamReplace(w, src, find, repl, 0)
		if err != nil {
			t.Errorf("StreamReplace HTTP error: %v", err)
		}
	}))
	defer ts.Close()

	resp, err := http.Get(ts.URL)
	if err != nil {
		t.Fatalf("HTTP GET failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("unexpected status code: %d", resp.StatusCode)
	}

	// Verify Transfer-Encoding was chunked (Content-Length == -1 in Go http client when chunked)
	if resp.ContentLength != -1 && resp.ContentLength != int64(len(expectedBody)) {
		t.Fatalf("expected chunked (-1) or accurate length, got ContentLength=%d", resp.ContentLength)
	}

	gotBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("failed reading response body: %v", err)
	}

	if string(gotBytes) != expectedBody {
		t.Fatalf("response body mismatch: got %d bytes, expected %d bytes", len(gotBytes), len(expectedBody))
	}
}

// TestMaxBytesLimit ensures maxBytes parameter properly truncates over-sized inputs.
func TestMaxBytesLimit(t *testing.T) {
	data := strings.Repeat("A", 1000) + "TARGET" + strings.Repeat("B", 1000)
	var out bytes.Buffer

	// Limit to 500 bytes (before TARGET)
	_, err := StreamReplace(&out, strings.NewReader(data), "TARGET", "REPLACED", 500)
	if err != nil {
		t.Fatalf("StreamReplace with maxBytes error: %v", err)
	}
	if out.Len() != 500 {
		t.Fatalf("expected 500 bytes, got %d", out.Len())
	}
	if strings.Contains(out.String(), "REPLACED") || strings.Contains(out.String(), "TARGET") {
		t.Fatalf("target should not have been reached under 500 byte limit")
	}
}
