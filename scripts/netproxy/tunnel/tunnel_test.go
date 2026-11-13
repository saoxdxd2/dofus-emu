package tunnel

import (
	"bufio"
	"errors"
	"io"
	"net"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// pipeConnPair returns two connected in-memory net.Conns.
func pipeConnPair() (net.Conn, net.Conn) {
	return net.Pipe()
}

func TestGameTunnelTouchAndReceivedSince(t *testing.T) {
	g := &GameTunnel{}
	if g.ReceivedSince(time.Now().UnixNano()) {
		t.Fatal("fresh tunnel should not have received anything yet")
	}
	g.Touch()
	now := time.Now().UnixNano()
	g.TouchReceived()
	if !g.ReceivedSince(now) {
		t.Fatal("TouchReceived should mark ReceivedSince true")
	}
	future := time.Now().Add(time.Hour).UnixNano()
	if g.ReceivedSince(future) {
		t.Fatal("future cutoff should not be satisfied")
	}
}

func TestActivityWriterWriteTouches(t *testing.T) {
	var touched atomic.Int32
	aw := ActivityWriter{W: io.Discard, Touch: func() { touched.Add(1) }}
	if _, err := aw.Write([]byte("hello")); err != nil {
		t.Fatalf("Write failed: %v", err)
	}
	if touched.Load() != 1 {
		t.Fatalf("expected Touch called once, got %d", touched.Load())
	}
}

func TestActivityWriterWriteNoTouchWhenNil(t *testing.T) {
	aw := ActivityWriter{W: io.Discard}
	if _, err := aw.Write([]byte("hello")); err != nil {
		t.Fatalf("Write failed: %v", err)
	}
}

func TestPreludeRoundTrip(t *testing.T) {
	p := Prelude{ToServer: []byte("client hello"), ToClient: []byte("server hello")}
	if string(p.ToServer) != "client hello" {
		t.Fatalf("ToServer mismatch: %q", p.ToServer)
	}
	if string(p.ToClient) != "server hello" {
		t.Fatalf("ToClient mismatch: %q", p.ToClient)
	}
}

func TestTuneTCPConn(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c1.Close()
	defer c2.Close()
	TuneTCPConn(c1)
	TuneTCPConn(c2)
}

func TestHalfCloseOrClose(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c1.Close()
	defer c2.Close()
	HalfCloseOrClose(c1)
	buf := make([]byte, 1)
	if _, err := c2.Read(buf); err != nil && !errors.Is(err, io.EOF) && !strings.Contains(err.Error(), "use of closed") {
		t.Fatalf("unexpected read error after close: %v", err)
	}
}

func TestReadConnectResponseSuccess(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c2.Close()
	go func() {
		c2.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
		c2.Close()
	}()
	if err := ReadConnectResponse(c1); err != nil {
		t.Fatalf("ReadConnectResponse failed: %v", err)
	}
}

func TestReadConnectResponseError(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c2.Close()
	go func() {
		c2.Write([]byte("HTTP/1.1 403 Forbidden\r\n\r\n"))
		c2.Close()
	}()
	if err := ReadConnectResponse(c1); err == nil {
		t.Fatal("expected error for non-200 response")
	} else if !strings.Contains(err.Error(), "403") {
		t.Fatalf("expected 403 in error, got %v", err)
	}
}

func TestReadConnectResponseEOF(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c2.Close()
	done := make(chan error, 1)
	go func() { done <- ReadConnectResponse(c1) }()
	c2.Close()
	select {
	case err := <-done:
		if err == nil && !errors.Is(err, io.EOF) && !strings.Contains(err.Error(), "use of closed") {
			t.Fatalf("expected EOF or closed error, got %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ReadConnectResponse did not return after peer closed")
	}
}

func TestSocksAuth(t *testing.T) {
	if u, _ := url.Parse("socks5://proxy.example:1080"); SocksAuth(u) != nil {
		t.Fatal("expected nil auth for no userinfo")
	}
	u, _ := url.Parse("socks5://user:pass@proxy.example:1080")
	auth := SocksAuth(u)
	if auth == nil {
		t.Fatal("expected non-nil auth for userinfo")
	}
	if auth.User != "user" {
		t.Fatalf("expected user 'user', got %q", auth.User)
	}
	if auth.Password != "pass" {
		t.Fatalf("expected password 'pass', got %q", auth.Password)
	}
}

func TestProveRoute(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c2.Close()
	payload := []byte("ping")
	go func() {
		// Consume the entire payload before replying, mirroring a real peer.
		total := 0
		tmp := make([]byte, 16)
		for total < len(payload) {
			n, err := c2.Read(tmp)
			total += n
			if err != nil {
				break
			}
		}
		c2.Write([]byte("pong"))
		c2.Close()
	}()
	out, ok := ProveRoute(c1, payload, 5*time.Second)
	if !ok {
		t.Fatal("ProveRoute should succeed")
	}
	if string(out) != "pong" {
		t.Fatalf("expected 'pong', got %q", out)
	}
}

func TestProveRouteTimeout(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c2.Close()
	// Never respond.
	if _, ok := ProveRoute(c1, []byte("ping"), 50*time.Millisecond); ok {
		t.Fatal("ProveRoute should fail on timeout")
	}
}

func TestReadClientPrelude(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c2.Close()
	go func() {
		c2.Write([]byte{0x16, 0x03, 0x01, 0x00, 0x0a, 'h', 'e', 'l', 'l', 'o', ' ', 'w', 'o', 'r', 'l', 'd'})
		c2.Close()
	}()
	out := ReadClientPrelude(c1, nil, 5*time.Second)
	expected := []byte{0x16, 0x03, 0x01, 0x00, 0x0a, 'h', 'e', 'l', 'l', 'o', ' ', 'w', 'o', 'r', 'l', 'd'}
	if string(out) != string(expected) {
		t.Fatalf("unexpected prelude: %q", out)
	}
}

func TestReadClientPreludeBuffered(t *testing.T) {
	c1, c2 := pipeConnPair()
	defer c1.Close()
	defer c2.Close()
	go func() {
		c2.Write([]byte("buffered prelude"))
		c2.Close()
	}()
	// Prime the bufio.Reader with a small peek; the rest stays buffered.
	rw := bufio.NewReadWriter(bufio.NewReader(c1), bufio.NewWriter(io.Discard))
	peek := make([]byte, 5)
	if _, err := rw.Reader.Read(peek); err != nil {
		t.Fatalf("peek read failed: %v", err)
	}
	// ReadClientPrelude should return the remaining buffered bytes without
	// reading from c1 again.
	out := ReadClientPrelude(c1, rw, 5*time.Second)
	if string(out) != "red prelude" {
		t.Fatalf("expected buffered prelude remainder, got %q", out)
	}
}
