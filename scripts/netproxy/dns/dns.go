// Package dns is the relay's resolver: a TTL cache in front of the system
// resolver (or a pinned custom resolver), with singleflight so a burst of
// parallel dials for one host produces one lookup. It returns IPv4 only,
// because the game endpoints and the proxy chain are IPv4.
package dns

import (
	"context"
	"maps"
	"net"
	"slices"
	"sync"
	"time"

	"golang.org/x/sync/singleflight"
)

type CacheEntry struct {
	IPs       []net.IP
	ExpiresAt time.Time // fully expired: don't serve, block and refresh
	StaleAt   time.Time // start serving stale while refreshing in background
	Negative  bool      // true if this is a negative cache entry (lookup failed)
}

type lookupCall struct {
	wg  sync.WaitGroup
	ips []net.IP
	err error
}

type Resolver struct {
	mu          sync.RWMutex
	cache       map[string]CacheEntry
	inflight    map[string]*lookupCall
	sf          singleflight.Group
	rr          uint64
	ttl         time.Duration
	negativeTTL time.Duration // TTL for negative (failed lookup) cache entries
	customDNS   string
	netResolve  *net.Resolver
}

func NewResolver(customDNS string, ttl time.Duration) *Resolver {
	return NewResolverWithNegTTL(customDNS, ttl, 30*time.Second)
}

func NewResolverWithNegTTL(customDNS string, ttl, negativeTTL time.Duration) *Resolver {
	r := &Resolver{
		cache:       make(map[string]CacheEntry, 128),
		inflight:    make(map[string]*lookupCall),
		ttl:         ttl,
		negativeTTL: negativeTTL,
		customDNS:   customDNS,
		netResolve:  net.DefaultResolver,
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

func (r *Resolver) pickIP(ips []net.IP) []net.IP {
	if len(ips) <= 1 {
		return slices.Clone(ips)
	}
	r.mu.Lock()
	idx := r.rr % uint64(len(ips))
	r.rr++
	r.mu.Unlock()
	return []net.IP{ips[idx]}
}

func (r *Resolver) Lookup(ctx context.Context, host string) ([]net.IP, error) {
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
		// Fresh entry: serve immediately.
		if now.Before(entry.StaleAt) {
			return r.pickIP(entry.IPs), nil
		}
		// Stale but still within TTL: serve the stale IPs immediately and
		// kick off a non-blocking background refresh. This is stale-while-revalidate.
		// Negative entries are not refreshed in the background.
		if !entry.Negative {
			go r.refreshInBackground(ctx, host)
		}
		return r.pickIP(entry.IPs), nil
	}

	// Not cached or fully expired: block on a singleflight lookup.
	v, err, _ := r.sf.Do(host, func() (any, error) {
		ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
		if err != nil && r.netResolve != net.DefaultResolver {
			ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
		}
		r.mu.Lock()
		defer r.mu.Unlock()
		if len(r.cache) >= 1024 {
			maps.DeleteFunc(r.cache, func(_ string, e CacheEntry) bool { return now.After(e.ExpiresAt) })
			if len(r.cache) >= 1024 {
				clear(r.cache)
			}
		}
		if err == nil {
			staleAt := float64Duration(r.ttl, 0.8) // last 20% is the SWR window
			r.cache[host] = CacheEntry{IPs: ips, ExpiresAt: now.Add(r.ttl), StaleAt: now.Add(staleAt)}
		} else {
			// Negative cache: failed lookups get a short TTL so a flapping
			// or dead host doesn't stall every dial.
			r.cache[host] = CacheEntry{Negative: true, ExpiresAt: now.Add(r.negativeTTL), StaleAt: now.Add(r.negativeTTL)}
		}
		return ips, err
	})
	if err != nil {
		return nil, err
	}
	return r.pickIP(v.([]net.IP)), nil
}

// refreshInBackground performs a non-blocking lookup and updates the cache.
// Callers already received the stale entry, so this only warms the cache for
// the next request.
func (r *Resolver) refreshInBackground(ctx context.Context, host string) {
	ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
	if err != nil && r.netResolve != net.DefaultResolver {
		ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
	}
	now := time.Now()
	r.mu.Lock()
	defer r.mu.Unlock()
	if err == nil {
		staleAt := float64Duration(r.ttl, 0.8)
		r.cache[host] = CacheEntry{IPs: ips, ExpiresAt: now.Add(r.ttl), StaleAt: now.Add(staleAt)}
	} else {
		r.cache[host] = CacheEntry{Negative: true, ExpiresAt: now.Add(r.negativeTTL), StaleAt: now.Add(r.negativeTTL)}
	}
}

// float64Duration multiplies a time.Duration by a float64 factor.
func float64Duration(d time.Duration, f float64) time.Duration {
	return time.Duration(float64(d) * f)
}

// ResolveAddr rewrites host:port to ip:port when the host resolves. A failed
// lookup returns the original address so the dialer's own resolver can still
// try, and the error is not hidden from the connection attempt.
func (r *Resolver) ResolveAddr(ctx context.Context, addr, defPort string) string {
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

func ensureHostPort(addr, defPort string) string {
	if !hasPort(addr) {
		return net.JoinHostPort(addr, defPort)
	}
	return addr
}

func hasPort(addr string) bool {
	if _, _, err := net.SplitHostPort(addr); err == nil {
		return true
	}
	// A bare IPv6 literal has colons but no port; SplitHostPort rejects it.
	return false
}
