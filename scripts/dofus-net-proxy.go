package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

// DNSCacheEntry holds cached IP addresses and expiry time
type DNSCacheEntry struct {
	IPs       []net.IP
	ExpiresAt time.Time
}

// FastDNSResolver provides in-memory thread-safe DNS caching
type FastDNSResolver struct {
	mu         sync.RWMutex
	cache      map[string]DNSCacheEntry
	ttl        time.Duration
	customDNS  string
	netResolve *net.Resolver
}

func NewFastDNSResolver(customDNS string, ttl time.Duration) *FastDNSResolver {
	r := &FastDNSResolver{
		cache:     make(map[string]DNSCacheEntry),
		ttl:       ttl,
		customDNS: customDNS,
	}

	if customDNS != "" {
		if !strings.Contains(customDNS, ":") {
			customDNS += ":53"
		}
		r.netResolve = &net.Resolver{
			PreferGo: true,
			Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
				d := net.Dialer{Timeout: 3 * time.Second}
				return d.DialContext(ctx, "udp", customDNS)
			},
		}
	} else {
		r.netResolve = net.DefaultResolver
	}
	return r
}

func (r *FastDNSResolver) Lookup(ctx context.Context, host string) ([]net.IP, error) {
	// Strip port if present
	h, _, err := net.SplitHostPort(host)
	if err == nil {
		host = h
	}

	// Check if already an IP
	if ip := net.ParseIP(host); ip != nil {
		return []net.IP{ip}, nil
	}

	// Check cache
	r.mu.RLock()
	entry, found := r.cache[host]
	r.mu.RUnlock()

	if found && time.Now().Before(entry.ExpiresAt) {
		return entry.IPs, nil
	}

	// Resolve
	ips, err := r.netResolve.LookupIP(ctx, "ip4", host)
	if err != nil {
		// Fallback to default resolver if custom DNS timed out
		if r.netResolve != net.DefaultResolver {
			ips, err = net.DefaultResolver.LookupIP(ctx, "ip4", host)
		}
		if err != nil {
			return nil, err
		}
	}

	r.mu.Lock()
	r.cache[host] = DNSCacheEntry{
		IPs:       ips,
		ExpiresAt: time.Now().Add(r.ttl),
	}
	r.mu.Unlock()

	return ips, nil
}

// ProxyServer handles HTTP and HTTPS CONNECT proxy requests
type ProxyServer struct {
	resolver    *FastDNSResolver
	upstreamURL *url.URL
	quiet       bool
	client      *http.Client
}

func NewProxyServer(resolver *FastDNSResolver, upstream string, quiet bool) *ProxyServer {
	p := &ProxyServer{
		resolver: resolver,
		quiet:    quiet,
	}

	if upstream != "" {
		if !strings.Contains(upstream, "://") {
			upstream = "http://" + upstream
		}
		u, err := url.Parse(upstream)
		if err == nil {
			p.upstreamURL = u
		}
	}

	transport := &http.Transport{
		MaxIdleConns:        200,
		MaxIdleConnsPerHost: 50,
		IdleConnTimeout:     90 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(addr)
			if err != nil {
				host = addr
				port = "80"
			}
			ips, err := resolver.Lookup(ctx, host)
			if err != nil || len(ips) == 0 {
				return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, network, addr)
			}
			targetAddr := net.JoinHostPort(ips[0].String(), port)
			return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, network, targetAddr)
		},
	}

	if p.upstreamURL != nil {
		transport.Proxy = http.ProxyURL(p.upstreamURL)
	}

	p.client = &http.Client{
		Transport: transport,
		Timeout:   60 * time.Second,
	}

	return p
}

func (p *ProxyServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodConnect {
		p.handleConnect(w, r)
	} else {
		p.handleHTTP(w, r)
	}
}

func (p *ProxyServer) handleConnect(w http.ResponseWriter, r *http.Request) {
	dest := r.Host
	if !strings.Contains(dest, ":") {
		dest += ":443"
	}

	host, port, err := net.SplitHostPort(dest)
	if err != nil {
		host = dest
		port = "443"
	}

	dialCtx, dialCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer dialCancel()

	var destConn net.Conn
	if p.upstreamURL != nil {
		// Route CONNECT via upstream proxy
		destConn, err = net.DialTimeout("tcp", p.upstreamURL.Host, 5*time.Second)
		if err != nil {
			http.Error(w, "Upstream Proxy Unreachable: "+err.Error(), http.StatusBadGateway)
			return
		}
		// Send CONNECT to upstream
		connectReq := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n", dest, dest)
		_, err = destConn.Write([]byte(connectReq))
		if err != nil {
			destConn.Close()
			http.Error(w, "Upstream CONNECT handshake failed", http.StatusBadGateway)
			return
		}
		// Read upstream response
		buf := make([]byte, 1024)
		n, err := destConn.Read(buf)
		if err != nil || !strings.Contains(string(buf[:n]), "200") {
			destConn.Close()
			http.Error(w, "Upstream rejected CONNECT", http.StatusBadGateway)
			return
		}
	} else {
		// Direct connection with fast DNS caching
		ips, err := p.resolver.Lookup(dialCtx, host)
		target := dest
		if err == nil && len(ips) > 0 {
			target = net.JoinHostPort(ips[0].String(), port)
		}

		dialer := net.Dialer{Timeout: 8 * time.Second, KeepAlive: 30 * time.Second}
		destConn, err = dialer.DialContext(dialCtx, "tcp", target)
		if err != nil {
			http.Error(w, "Connection Failed: "+err.Error(), http.StatusBadGateway)
			return
		}
	}
	defer destConn.Close()

	hijacker, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}

	clientConn, _, err := hijacker.Hijack()
	if err != nil {
		http.Error(w, "Hijack failed: "+err.Error(), http.StatusServiceUnavailable)
		return
	}
	defer clientConn.Close()

	// Respond 200 Connection Established to client
	_, err = clientConn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	if err != nil {
		return
	}

	if !p.quiet && strings.Contains(dest, "ankama") {
		log.Printf("[TUNNEL] CONNECT -> %s (Fast Path Active)", dest)
	}

	// Bi-directional stream splice with 32KB buffers
	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		buf := make([]byte, 32*1024)
		_, _ = io.CopyBuffer(destConn, clientConn, buf)
		if tc, ok := destConn.(*net.TCPConn); ok {
			_ = tc.CloseWrite()
		}
	}()

	go func() {
		defer wg.Done()
		buf := make([]byte, 32*1024)
		_, _ = io.CopyBuffer(clientConn, destConn, buf)
		if tc, ok := clientConn.(*net.TCPConn); ok {
			_ = tc.CloseWrite()
		}
	}()

	wg.Wait()
}

func (p *ProxyServer) handleHTTP(w http.ResponseWriter, r *http.Request) {
	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, r.RequestURI, r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	for k, vv := range r.Header {
		if strings.EqualFold(k, "Proxy-Connection") {
			continue
		}
		for _, v := range vv {
			outReq.Header.Add(k, v)
		}
	}

	resp, err := p.client.Do(outReq)
	if err != nil {
		http.Error(w, "Proxy Error: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for k, vv := range resp.Header {
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	buf := make([]byte, 32*1024)
	_, _ = io.CopyBuffer(w, resp.Body, buf)
}

func main() {
	port := flag.Int("port", 8880, "Local listening port for HTTP/CONNECT proxy")
	upstream := flag.String("upstream", "", "Upstream proxy URL (optional, e.g. http://ip:port)")
	dnsServer := flag.String("dns", "", "Custom DNS server to query (optional, default: system DNS)")
	quiet := flag.Bool("quiet", false, "Quiet mode (suppress request logs)")
	flag.Parse()

	log.SetFlags(log.Ldate | log.Ltime)
	fmt.Println("=====================================================================")
	fmt.Printf("   DOFUS TOUCH HIGH-PERFORMANCE GO NETWORK PROXY (PORT %d)\n", *port)
	fmt.Println("=====================================================================")
	if *dnsServer != "" {
		log.Printf("[INIT] In-memory Predictive DNS Resolver active (custom: %s)", *dnsServer)
	} else {
		log.Printf("[INIT] In-memory Predictive DNS Resolver active (system OS DNS)")
	}
	if *upstream != "" {
		log.Printf("[INIT] Upstream Proxy configured: %s", *upstream)
	} else {
		log.Printf("[INIT] Direct Gigabit Routing active (zero-copy socket splicing)")
	}

	resolver := NewFastDNSResolver(*dnsServer, 10*time.Minute)
	proxy := NewProxyServer(resolver, *upstream, *quiet)

	server := &http.Server{
		Addr:         fmt.Sprintf("127.0.0.1:%d", *port),
		Handler:      proxy,
		ReadTimeout:  120 * time.Second,
		WriteTimeout: 120 * time.Second,
	}

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		log.Printf("[READY] Listening on http://127.0.0.1:%d ...", *port)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[FATAL] Proxy server failed: %v", err)
		}
	}()

	<-sigChan
	log.Println("[SHUTDOWN] Terminating Go network proxy cleanly...")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
	log.Println("[SHUTDOWN] Completed.")
}
