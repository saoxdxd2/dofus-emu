package main

import (
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

type ProcessMemory struct {
	WorkingSetMB float64
	PrivateMB    float64
}

func getProcessMemory(pid int) ProcessMemory {
	cmd := exec.Command("powershell", "-NoProfile", "-Command",
		fmt.Sprintf("(Get-Process -Id %d | Select-Object WorkingSet64, PrivateMemorySize64) | ConvertTo-Json", pid))
	out, err := cmd.Output()
	if err != nil {
		return ProcessMemory{}
	}
	s := string(out)
	var ws, pm int64
	for _, line := range strings.Split(s, "\n") {
		line = strings.TrimSpace(line)
		if strings.Contains(line, "WorkingSet64") {
			parts := strings.Split(line, ":")
			if len(parts) > 1 {
				fmt.Sscanf(strings.Trim(parts[1], " ,\r\n"), "%d", &ws)
			}
		}
		if strings.Contains(line, "PrivateMemorySize64") {
			parts := strings.Split(line, ":")
			if len(parts) > 1 {
				fmt.Sscanf(strings.Trim(parts[1], " ,\r\n"), "%d", &pm)
			}
		}
	}
	return ProcessMemory{
		WorkingSetMB: float64(ws) / (1024 * 1024),
		PrivateMB:    float64(pm) / (1024 * 1024),
	}
}

func main() {
	fmt.Println("==========================================================")
	fmt.Println("   DOFUS TOUCH NETWORK PROXY - INDEPENDENT BENCHMARK     ")
	fmt.Println("==========================================================")

	proxyPort := 8899
	targetHTTPPort := 9123
	totalRequests := 1000
	concurrency := 10

	// 1. Start mock target servers
	mockReceivedHeaders := make(map[string]string)
	var headerMu sync.Mutex

	mockMux := http.NewServeMux()
	mockMux.HandleFunc("/assets/sprites.png", func(w http.ResponseWriter, r *http.Request) {
		headerMu.Lock()
		for k, v := range r.Header {
			if len(v) > 0 {
				mockReceivedHeaders[k] = v[0]
			}
		}
		headerMu.Unlock()
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		// 8 KB payload
		dummy := make([]byte, 8192)
		w.WriteHeader(http.StatusOK)
		w.Write(dummy)
	})
	mockMux.HandleFunc("/game/data.json", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"server":"Grandapan","status":"active","ping":28}`))
	})
	mockMux.HandleFunc("/index.html", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`<!DOCTYPE html><html><head><title>Dofus Touch</title></head><body><h1>Client</h1></body></html>`))
	})

	httpServer := &http.Server{
		Addr:    fmt.Sprintf("127.0.0.1:%d", targetHTTPPort),
		Handler: mockMux,
	}
	go func() {
		_ = httpServer.ListenAndServe()
	}()
	defer httpServer.Close()

	// Wait for mock server
	time.Sleep(200 * time.Millisecond)

	// 2. Start dofus-net-proxy.exe
	proxyExePath, _ := filepath.Abs("dofus-net-proxy.exe")
	if _, err := os.Stat(proxyExePath); os.IsNotExist(err) {
		proxyExePath, _ = filepath.Abs(filepath.Join("scripts", "dofus-net-proxy.exe"))
	}

	cacheDir, _ := os.MkdirTemp("", "proxy_bench_cache_*")
	defer os.RemoveAll(cacheDir)

	proxyCmd := exec.Command(proxyExePath,
		"-port", fmt.Sprintf("%d", proxyPort),
		"-cache", cacheDir,
		"-model", "SM-G998B",
		"-manufacturer", "samsung",
		"-ua", "Mozilla/5.0 (Linux; Android 12; SM-G998B Build/SP1A.210812.016) AppleWebKit/537.36",
		"-quiet",
	)
	proxyCmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	if err := proxyCmd.Start(); err != nil {
		fmt.Printf("ERROR: Failed to start proxy process: %v\n", err)
		return
	}
	defer func() {
		if proxyCmd.Process != nil {
			_ = proxyCmd.Process.Kill()
		}
	}()

	proxyPID := proxyCmd.Process.Pid
	fmt.Printf("[1/5] Proxy process launched (PID: %d) on port %d\n", proxyPID, proxyPort)

	// Wait for proxy to listen
	proxyReady := false
	for i := 0; i < 30; i++ {
		conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", proxyPort), 200*time.Millisecond)
		if err == nil {
			conn.Close()
			proxyReady = true
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !proxyReady {
		fmt.Println("ERROR: Proxy failed to start listening within 3 seconds.")
		return
	}

	// 3. Health check
	proxyURL, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", proxyPort))
	clientWithProxy := &http.Client{
		Transport: &http.Transport{
			Proxy: http.ProxyURL(proxyURL),
			DialContext: (&net.Dialer{
				Timeout:   3 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			MaxIdleConns:        100,
			MaxIdleConnsPerHost: 50,
			IdleConnTimeout:     90 * time.Second,
			TLSClientConfig:     &tls.Config{InsecureSkipVerify: true},
		},
		Timeout: 5 * time.Second,
	}

	directClient := &http.Client{Timeout: 2 * time.Second}
	healthResp, err := directClient.Get(fmt.Sprintf("http://127.0.0.1:%d/__health", proxyPort))
	if err != nil {
		fmt.Printf("WARNING: Health endpoint error: %v\n", err)
	} else {
		body, _ := io.ReadAll(healthResp.Body)
		healthResp.Body.Close()
		fmt.Printf("      Health Check OK: %s\n", strings.TrimSpace(string(body)))
	}

	memBefore := getProcessMemory(proxyPID)
	fmt.Printf("[2/5] Baseline Resource Footprint:\n")
	fmt.Printf("      Working Set:   %.2f MB\n", memBefore.WorkingSetMB)
	fmt.Printf("      Private Bytes: %.2f MB\n", memBefore.PrivateMB)

	// 4. Run Benchmark (1000 requests)
	fmt.Printf("[3/5] Executing %d requests with concurrency=%d...\n", totalRequests, concurrency)

	endpoints := []string{
		fmt.Sprintf("http://127.0.0.1:%d/assets/sprites.png", targetHTTPPort),
		fmt.Sprintf("http://127.0.0.1:%d/game/data.json", targetHTTPPort),
		fmt.Sprintf("http://127.0.0.1:%d/index.html", targetHTTPPort),
	}

	durations := make([]time.Duration, totalRequests)
	var wg sync.WaitGroup
	reqChan := make(chan int, totalRequests)
	for i := 0; i < totalRequests; i++ {
		reqChan <- i
	}
	close(reqChan)

	var successCount int64
	var errorCount int64
	var totalBytes int64
	var countMu sync.Mutex

	var peakMem ProcessMemory
	stopMonitor := make(chan struct{})
	go func() {
		ticker := time.NewTicker(50 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-stopMonitor:
				return
			case <-ticker.C:
				m := getProcessMemory(proxyPID)
				if m.WorkingSetMB > peakMem.WorkingSetMB {
					peakMem = m
				}
			}
		}
	}()

	benchStart := time.Now()

	for c := 0; c < concurrency; c++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for idx := range reqChan {
				urlTarget := endpoints[idx%len(endpoints)]
				t0 := time.Now()
				req, err := http.NewRequest("GET", urlTarget, nil)
				if err != nil {
					countMu.Lock()
					errorCount++
					countMu.Unlock()
					continue
				}
				req.Header.Set("User-Agent", "DofusTouch/Android")
				req.Header.Set("X-Forwarded-For", "192.168.1.100") // Should be stripped

				resp, err := clientWithProxy.Do(req)
				elapsed := time.Since(t0)
				durations[idx] = elapsed

				if err != nil {
					countMu.Lock()
					errorCount++
					countMu.Unlock()
					continue
				}
				b, _ := io.ReadAll(resp.Body)
				resp.Body.Close()

				countMu.Lock()
				if resp.StatusCode == http.StatusOK {
					successCount++
					totalBytes += int64(len(b))
				} else {
					errorCount++
				}
				countMu.Unlock()
			}
		}()
	}

	wg.Wait()
	totalBenchDuration := time.Since(benchStart)
	close(stopMonitor)

	// Let GC settle slightly
	time.Sleep(300 * time.Millisecond)
	memAfter := getProcessMemory(proxyPID)

	// 5. Compute Statistics
	fmt.Printf("[4/5] Benchmark completed in %v\n", totalBenchDuration.Round(time.Millisecond))

	sort.Slice(durations, func(i, j int) bool {
		return durations[i] < durations[j]
	})

	var sum time.Duration
	for _, d := range durations {
		sum += d
	}
	avgDuration := sum / time.Duration(totalRequests)
	minDuration := durations[0]
	maxDuration := durations[totalRequests-1]
	p50 := durations[int(float64(totalRequests)*0.50)]
	p90 := durations[int(float64(totalRequests)*0.90)]
	p95 := durations[int(float64(totalRequests)*0.95)]
	p99 := durations[int(float64(totalRequests)*0.99)]

	rps := float64(totalRequests) / totalBenchDuration.Seconds()
	mbPerSec := (float64(totalBytes) / (1024 * 1024)) / totalBenchDuration.Seconds()

	fmt.Println("\n==========================================================")
	fmt.Println("                  BENCHMARK RESULTS                       ")
	fmt.Println("==========================================================")
	fmt.Printf("Total Requests:       %d\n", totalRequests)
	fmt.Printf("Successful Requests:  %d (%.2f%%)\n", successCount, float64(successCount)/float64(totalRequests)*100)
	fmt.Printf("Failed Requests:      %d\n", errorCount)
	fmt.Printf("Total Elapsed Time:   %v\n", totalBenchDuration.Round(time.Millisecond))
	fmt.Printf("Throughput (RPS):     %.1f req/sec\n", rps)
	fmt.Printf("Bandwidth:            %.2f MB/sec (Total: %.2f MB)\n", mbPerSec, float64(totalBytes)/(1024*1024))
	fmt.Println("----------------------------------------------------------")
	fmt.Println("LATENCY DISTRIBUTION (RTT through proxy):")
	fmt.Printf("  Min:                %8.2f ms\n", float64(minDuration.Microseconds())/1000.0)
	fmt.Printf("  Avg:                %8.2f ms\n", float64(avgDuration.Microseconds())/1000.0)
	fmt.Printf("  Median (p50):       %8.2f ms\n", float64(p50.Microseconds())/1000.0)
	fmt.Printf("  90th percentile:    %8.2f ms\n", float64(p90.Microseconds())/1000.0)
	fmt.Printf("  95th percentile:    %8.2f ms\n", float64(p95.Microseconds())/1000.0)
	fmt.Printf("  99th percentile:    %8.2f ms\n", float64(p99.Microseconds())/1000.0)
	fmt.Printf("  Max:                %8.2f ms\n", float64(maxDuration.Microseconds())/1000.0)
	fmt.Println("----------------------------------------------------------")
	fmt.Println("RESOURCE CONSUMPTION (dofus-net-proxy.exe):")
	fmt.Printf("  Working Set (Start): %.2f MB\n", memBefore.WorkingSetMB)
	fmt.Printf("  Working Set (Peak):  %.2f MB (Delta: +%.2f MB)\n", peakMem.WorkingSetMB, peakMem.WorkingSetMB-memBefore.WorkingSetMB)
	fmt.Printf("  Working Set (End):   %.2f MB\n", memAfter.WorkingSetMB)
	fmt.Printf("  Private Bytes:       %.2f MB\n", memAfter.PrivateMB)
	fmt.Println("----------------------------------------------------------")
	fmt.Println("SECURITY & SPOOFING VERIFICATION:")
	headerMu.Lock()
	uaReceived := mockReceivedHeaders["User-Agent"]
	xffReceived := mockReceivedHeaders["X-Forwarded-For"]
	headerMu.Unlock()
	fmt.Printf("  User-Agent Injected:  %s\n", uaReceived)
	if xffReceived == "" {
		fmt.Printf("  Leak Header Stripped: [PASS] X-Forwarded-For correctly stripped\n")
	} else {
		fmt.Printf("  Leak Header Stripped: [FAIL] X-Forwarded-For leaked: %s\n", xffReceived)
	}

	// 6. Test Cache Acceleration on Static Assets
	fmt.Println("----------------------------------------------------------")
	fmt.Println("CACHE ACCELERATION TEST:")
	// Request the same static asset 5 times sequentially
	var cacheLatencies []time.Duration
	for i := 0; i < 5; i++ {
		t0 := time.Now()
		resp, err := clientWithProxy.Get(fmt.Sprintf("http://127.0.0.1:%d/assets/sprites.png", targetHTTPPort))
		elapsed := time.Since(t0)
		if err == nil {
			io.ReadAll(resp.Body)
			resp.Body.Close()
			cacheLatencies = append(cacheLatencies, elapsed)
		}
	}
	if len(cacheLatencies) >= 2 {
		fmt.Printf("  First Fetch (Network): %.2f ms\n", float64(cacheLatencies[0].Microseconds())/1000.0)
		fmt.Printf("  Cached Hits (Average): %.2f ms\n", float64((cacheLatencies[1]+cacheLatencies[2]+cacheLatencies[3]+cacheLatencies[4]).Microseconds())/4000.0)
	}
	fmt.Println("==========================================================")
}
