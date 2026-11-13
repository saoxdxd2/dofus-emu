// Package cache owns the relay's disk and memory budget. Every body copied
// through the proxy is bounded by a per-instance budget (200 MB by default),
// buffers come from a sync.Pool, and the small path/mime helpers live here so
// the HTTP handlers do not each carry their own copy.
package cache

import (
	"cmp"
	"fmt"
	"io"
	"mime"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"crypto/sha256"
	"encoding/hex"
)

const (
	DefaultBudget int64 = 200 << 20 // 200 MB per proxy instance
	BufferSize          = 64 << 10
)

// Budget is a soft per-instance cap on bytes held by in-flight copies. It is
// deliberately not a hard allocator: a request that would exceed the cap is
// truncated by LimitReader rather than blocking the data plane.
type Budget struct {
	mu   sync.Mutex
	max  int64
	used int64
}

func NewBudget(max int64) *Budget {
	if max <= 0 {
		max = DefaultBudget
	}
	return &Budget{max: max}
}

func (b *Budget) Max() int64 {
	if b == nil {
		return DefaultBudget
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.max
}

func (b *Budget) Used() int64 {
	if b == nil {
		return 0
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.used
}

// Reserve claims up to n bytes and returns the granted amount. The caller must
// Release exactly what was granted.
func (b *Budget) Reserve(n int64) int64 {
	if b == nil || n <= 0 {
		return 0
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	left := b.max - b.used
	if left <= 0 {
		return 0
	}
	if n > left {
		n = left
	}
	b.used += n
	return n
}

func (b *Budget) Release(n int64) {
	if b == nil || n <= 0 {
		return
	}
	b.mu.Lock()
	b.used -= n
	if b.used < 0 {
		b.used = 0
	}
	b.mu.Unlock()
}

// LimitReader caps a single copy at min(requested, remaining budget). When the
// budget is exhausted the reader returns EOF instead of letting one response
// grow without bound.
func (b *Budget) LimitReader(r io.Reader, requested int64) (io.Reader, func()) {
	if requested <= 0 {
		requested = DefaultBudget
	}
	granted := b.Reserve(requested)
	if granted <= 0 {
		return strings.NewReader(""), func() {}
	}
	return io.LimitReader(r, granted), func() { b.Release(granted) }
}

// BufferPool reuses copy buffers. A sync.Pool is not a cache of unbounded
// slices: Put only returns the canonical size, so a large caller buffer cannot
// pin memory for the life of the process.
type BufferPool struct {
	pool sync.Pool
	size int
}

func NewBufferPool(size int) *BufferPool {
	if size <= 0 {
		size = BufferSize
	}
	return &BufferPool{
		size: size,
		pool: sync.Pool{New: func() any { return make([]byte, size) }},
	}
}

func (p *BufferPool) Get() []byte {
	if p == nil {
		return make([]byte, BufferSize)
	}
	b := p.pool.Get().([]byte)
	if cap(b) < p.size {
		return make([]byte, p.size)
	}
	return b[:p.size]
}

func (p *BufferPool) Put(b []byte) {
	if p == nil || b == nil || cap(b) < p.size {
		return
	}
	p.pool.Put(b[:p.size])
}

// CopyBounded copies src to dst through the budget and the shared buffer pool.
// It returns the number of bytes written and whether the budget truncated the
// stream (n == granted while the source still had bytes).
func CopyBounded(dst io.Writer, src io.Reader, budget *Budget, pool *BufferPool, max int64) (int64, error) {
	limited, release := budget.LimitReader(src, max)
	defer release()
	buf := pool.Get()
	defer pool.Put(buf)
	return io.CopyBuffer(dst, limited, buf)
}

func AtomicReplaceFile(src, dst string) error {
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
	oldPath := dst + ".old." + fmt.Sprint(time.Now().UnixNano())
	if os.Rename(dst, oldPath) == nil {
		_ = os.Remove(oldPath)
		if os.Rename(src, dst) == nil {
			return nil
		}
	}
	return err
}

func DetectMimeType(path string) string {
	ext := strings.ToLower(filepath.Ext(path))
	if m, ok := extMime[ext]; ok {
		return m
	}
	return cmp.Or(mime.TypeByExtension(ext), "application/octet-stream")
}

var extMime = map[string]string{
	".wasm": "application/wasm", ".json": "application/json; charset=utf-8",
	".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
	".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".js": "application/javascript; charset=utf-8",
	".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
}

func ReadFileLimited(path string, maxBytes int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, maxBytes))
}

func AssetPath(dir, target string) string {
	sum := sha256.Sum256([]byte(target))
	return filepath.Join(dir, hex.EncodeToString(sum[:16]))
}

// MirrorRelPath returns the host/path key used by the shared file mirror, or ""
// when the URL is not a safe relative path. The checks are deliberately strict:
// the value becomes a filesystem path under the cache directory.
func MirrorRelPath(target string) string {
	u, err := url.Parse(target)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return ""
	}
	host := strings.ToLower(u.Hostname())
	if host == "" || strings.ContainsAny(host, `<>:"|?*\`) || u.Path == "" || u.Path == "/" ||
		strings.Contains(u.Path, "..") || strings.ContainsAny(u.Path, `<>:"|*\`) {
		return ""
	}
	return host + u.Path
}

func MirrorPathFor(dir, target string) string {
	if dir == "" {
		return ""
	}
	if rel := MirrorRelPath(target); rel != "" {
		return filepath.Join(dir, "files", filepath.FromSlash(rel))
	}
	return ""
}

// NegativeCache remembers 403/404 answers so a prefetch walk does not retry a
// key that is simply not shipped.
type NegativeCache struct {
	mu    sync.Mutex
	ttl   time.Duration
	items map[string]entry
}

type entry struct {
	status int
	at     time.Time
}

func NewNegativeCache(ttl time.Duration) *NegativeCache {
	if ttl <= 0 {
		ttl = 10 * time.Minute
	}
	return &NegativeCache{ttl: ttl, items: make(map[string]entry)}
}

func (n *NegativeCache) Hit(target string) (int, bool) {
	if n == nil {
		return 0, false
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	e, ok := n.items[target]
	if !ok {
		return 0, false
	}
	if time.Since(e.at) > n.ttl {
		delete(n.items, target)
		return 0, false
	}
	return e.status, true
}

func (n *NegativeCache) Store(target string, status int) {
	if n == nil || (status != 403 && status != 404) {
		return
	}
	now := time.Now()
	n.mu.Lock()
	defer n.mu.Unlock()
	if len(n.items) > 4096 {
		for k, v := range n.items {
			if now.Sub(v.at) > n.ttl {
				delete(n.items, k)
			}
		}
		if len(n.items) > 4096 {
			clear(n.items)
		}
	}
	n.items[target] = entry{status: status, at: now}
}

func (n *NegativeCache) Clear(target string) {
	if n == nil {
		return
	}
	n.mu.Lock()
	delete(n.items, target)
	n.mu.Unlock()
}
