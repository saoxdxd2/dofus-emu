package dns

import (
	"context"
	"net"
	"testing"
	"time"
)

func TestNewResolverReturnsResolver(t *testing.T) {
	r := NewResolver("", time.Second)
	if r == nil {
		t.Fatal("NewResolver returned nil")
	}
}

func TestNewResolverWithNegTTL(t *testing.T) {
	r := NewResolverWithNegTTL("", time.Second, 30*time.Second)
	if r == nil {
		t.Fatal("NewResolverWithNegTTL returned nil")
	}
}

func TestLookupIPLiteral(t *testing.T) {
	r := NewResolver("", time.Second)
	ip := net.ParseIP("127.0.0.1")
	ips, err := r.Lookup(context.Background(), "127.0.0.1")
	if err != nil {
		t.Fatalf("Lookup IP literal failed: %v", err)
	}
	if len(ips) != 1 || !ips[0].Equal(ip) {
		t.Fatalf("expected [127.0.0.1], got %v", ips)
	}
}

func TestLookupLocalhost(t *testing.T) {
	r := NewResolver("", time.Second)
	ips, err := r.Lookup(context.Background(), "localhost")
	if err != nil {
		t.Fatalf("Lookup localhost failed: %v", err)
	}
	if len(ips) == 0 {
		t.Fatal("expected at least one IP for localhost")
	}
	for _, ip := range ips {
		if !ip.IsLoopback() {
			t.Fatalf("expected loopback IP, got %v", ip)
		}
	}
}

func TestResolveAddrIPLiteral(t *testing.T) {
	r := NewResolver("", time.Second)
	got := r.ResolveAddr(context.Background(), "127.0.0.1:8080", "80")
	if got != "127.0.0.1:8080" {
		t.Fatalf("expected 127.0.0.1:8080, got %q", got)
	}
}

func TestResolveAddrHostname(t *testing.T) {
	r := NewResolver("", time.Second)
	got := r.ResolveAddr(context.Background(), "localhost:8080", "80")
	if got == "" {
		t.Fatal("ResolveAddr returned empty for localhost")
	}
	host, _, err := net.SplitHostPort(got)
	if err != nil {
		t.Fatalf("ResolveAddr result not host:port: %q", got)
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		t.Fatalf("expected loopback IP, got %q", host)
	}
}

func TestFloat64Duration(t *testing.T) {
	if got := float64Duration(time.Second, 0.5); got != 500*time.Millisecond {
		t.Fatalf("expected 500ms, got %v", got)
	}
	if got := float64Duration(time.Second, 2.0); got != 2*time.Second {
		t.Fatalf("expected 2s, got %v", got)
	}
}
