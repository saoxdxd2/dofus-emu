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
	ExpiresAt time.Time
}

type lookupCall struct {
	wg  sync.WaitGroup
	ips []net.IP
	err error
}

type Resolver struct {
	mu         sync.RWMutex
	cache      map[string]CacheEntry
	inflight   map[string]*lookupCall
	sf         singleflight.Group
	rr         uint64
	ttl        time.Duration
	customDNS  string
	netResolve *net.Resolver
}

func NewResolver(customDNS string, ttl time.Duration) *Resolver {
	r := &Resolver{
		cache:      make(map[string]CacheEntry, 128),
		inflight:   make(map[string]*lookupCall),
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
		return r.pickIP(entry.IPs), nil
	}
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
			r.cache[host] = CacheEntry{IPs: ips, ExpiresAt: now.Add(r.ttl)}
		}
		return ips, err
	})
	if err != nil {
		return nil, err
	}
	return r.pickIP(v.([]net.IP)), nil
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
