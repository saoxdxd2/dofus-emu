// Package tunnel holds the connection-level pieces of the relay: the game
// session's liveness marker, the client/upstream prelude proof, CONNECT and
// SOCKS5 handshakes, and the bounded two-way pipe. Keeping them here lets the
// cache and patcher packages stay out of the dial path, and gives the pipe a
// single place to enforce its buffer budget.
package tunnel

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	xproxy "golang.org/x/net/proxy"
)

// CloseWriter is implemented by net.TCPConn and by the test doubles that need
// to half-close without dropping the read side.
type CloseWriter interface{ CloseWrite() error }

type BufferedConn struct {
	io.Reader
	net.Conn
}

func (b *BufferedConn) Read(p []byte) (int, error) { return b.Reader.Read(p) }

type ConnDialer struct{ net.Conn }

func (d ConnDialer) Dial(_, _ string) (net.Conn, error) { return d.Conn, nil }

// GameTunnel is one game connection's last sign of life. Registration is not
// activity: a tunnel that has not moved a byte must not protect a dead pin.
type GameTunnel struct {
	LastActivity atomic.Int64
	LastReceived atomic.Int64
}

func (g *GameTunnel) Touch() { g.LastActivity.Store(time.Now().UnixNano()) }

func (g *GameTunnel) TouchReceived() {
	now := time.Now().UnixNano()
	g.LastActivity.Store(now)
	g.LastReceived.Store(now)
}

func (g *GameTunnel) ReceivedSince(cutoff int64) bool {
	return g.LastReceived.Load() >= cutoff
}

// ActivityWriter marks the session alive only when the peer accepted bytes.
// Reading from the client into a dead upstream never reaches this, which is
// the distinction the pin needs: a half-open socket must not look alive.
type ActivityWriter struct {
	W     io.Writer
	Touch func()
}

func (a ActivityWriter) Write(b []byte) (int, error) {
	n, err := a.W.Write(b)
	if n > 0 && a.Touch != nil {
		a.Touch()
	}
	return n, err
}

// Prelude carries bytes the first-byte proof moved before full piping began.
type Prelude struct {
	ToServer []byte
	ToClient []byte
}

func TuneTCPConn(c net.Conn) {
	_ = c.SetDeadline(time.Time{})
	if tc, ok := c.(*net.TCPConn); ok {
		_ = tc.SetKeepAlive(true)
		_ = tc.SetKeepAlivePeriod(30 * time.Second)
		_ = tc.SetNoDelay(true)
	}
}

func HalfCloseOrClose(c net.Conn) {
	if cw, ok := c.(CloseWriter); ok {
		_ = cw.CloseWrite()
	} else {
		_ = c.Close()
	}
}

func ReadConnectResponse(conn net.Conn) error {
	var buf [1]byte
	var cur []byte
	for total := 0; total <= 8192; {
		if _, err := conn.Read(buf[:]); err != nil {
			return err
		}
		total++
		switch buf[0] {
		case '\r':
		case '\n':
			if len(cur) == 0 {
				return nil
			}
			if s := string(cur); strings.HasPrefix(s, "HTTP/") {
				if f := strings.Fields(s); len(f) >= 2 && f[1] != "200" {
					return fmt.Errorf("proxy answered %s", s)
				}
			}
			cur = cur[:0]
		default:
			cur = append(cur, buf[0])
		}
	}
	return errors.New("CONNECT reply too long")
}

func HTTPConnect(conn net.Conn, dest, userinfo, ua string, timeout time.Duration) error {
	if err := conn.SetDeadline(time.Now().Add(timeout)); err != nil {
		return err
	}
	defer func() { _ = conn.SetDeadline(time.Time{}) }()
	auth := ""
	if userinfo != "" {
		auth = "Proxy-Authorization: Basic " + base64.StdEncoding.EncodeToString([]byte(userinfo)) + "\r\n"
	}
	req := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n%s\r\n", dest, dest, ua, auth)
	if _, err := io.WriteString(conn, req); err != nil {
		return err
	}
	return ReadConnectResponse(conn)
}

func ConnectThrough(conn net.Conn, scheme, host string, port int, userinfo, ua string) error {
	dest := net.JoinHostPort(host, strconv.Itoa(port))
	if strings.HasPrefix(strings.ToLower(scheme), "socks5") {
		if err := conn.SetDeadline(time.Now().Add(6 * time.Second)); err != nil {
			return err
		}
		defer func() { _ = conn.SetDeadline(time.Time{}) }()
		var auth *xproxy.Auth
		if userinfo != "" {
			user, pass, _ := strings.Cut(userinfo, ":")
			auth = &xproxy.Auth{User: user, Password: pass}
		}
		d, err := xproxy.SOCKS5("tcp", "0.0.0.0:0", auth, ConnDialer{conn})
		if err != nil {
			return err
		}
		_, err = d.Dial("tcp", dest)
		return err
	}
	return HTTPConnect(conn, dest, userinfo, ua, 6*time.Second)
}

func SocksAuth(u *url.URL) *xproxy.Auth {
	if u == nil || u.User == nil {
		return nil
	}
	pass, _ := u.User.Password()
	return &xproxy.Auth{User: u.User.Username(), Password: pass}
}

func DialSocks5(proxyURL *url.URL, targetHostPort string, timeout time.Duration) (net.Conn, error) {
	base := &net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}
	dialer, err := xproxy.SOCKS5("tcp", ensureHostPort(proxyURL.Host, "1080"), SocksAuth(proxyURL), base)
	if err != nil {
		return nil, err
	}
	if cd, ok := dialer.(xproxy.ContextDialer); ok {
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		return cd.DialContext(ctx, "tcp", targetHostPort)
	}
	return dialer.Dial("tcp", targetHostPort)
}

// ReadClientPrelude reads the client's opening payload. For a tunneled TLS
// connection that is the ClientHello, and no upstream can answer before the
// whole record arrived: after the first read, finish the record so the proof
// waits for an answer that can actually come instead of timing out on a
// half-delivered handshake.
func ReadClientPrelude(c net.Conn, clientBuf *bufio.ReadWriter, firstWait time.Duration) []byte {
	var out []byte
	if clientBuf != nil && clientBuf.Reader.Buffered() > 0 {
		n := clientBuf.Reader.Buffered()
		b := make([]byte, n)
		if _, err := io.ReadFull(clientBuf, b); err == nil {
			out = b
		}
	}
	if len(out) == 0 {
		_ = c.SetReadDeadline(time.Now().Add(firstWait))
		b := make([]byte, 8192)
		n, _ := c.Read(b)
		_ = c.SetReadDeadline(time.Time{})
		if n > 0 {
			out = b[:n]
		}
	}
	if len(out) >= 5 && out[0] == 0x16 {
		if need := 5 + int(binary.BigEndian.Uint16(out[3:5])); need > len(out) && need <= 5+18432 {
			rest := make([]byte, need-len(out))
			_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
			n, _ := io.ReadFull(c, rest)
			_ = c.SetReadDeadline(time.Time{})
			if n > 0 {
				out = append(out, rest[:n]...)
			}
		}
	}
	return out
}

// ProveRoute sends the client's opening bytes down an established tunnel and
// waits for the first byte back. A CONNECT that only reached the proxy's front
// door looks the same as a working one until this read.
func ProveRoute(destConn net.Conn, payload []byte, proveTimeout time.Duration) ([]byte, bool) {
	_ = destConn.SetWriteDeadline(time.Now().Add(3 * time.Second))
	if _, err := destConn.Write(payload); err != nil {
		return nil, false
	}
	_ = destConn.SetDeadline(time.Now().Add(proveTimeout))
	buf := make([]byte, 8192)
	n, _ := destConn.Read(buf)
	_ = destConn.SetDeadline(time.Time{})
	if n > 0 {
		return buf[:n], true
	}
	return nil, false
}

func ensureHostPort(addr, defPort string) string {
	if !strings.Contains(addr, ":") {
		return net.JoinHostPort(addr, defPort)
	}
	return addr
}
