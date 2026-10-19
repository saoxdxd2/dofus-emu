package main

import (
	"bufio"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

type ProxySource struct {
	Scheme string
	URL    string
}

func main() {
	sources := []ProxySource{
		{"http", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"},
		{"socks5", "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt"},
		{"http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"},
		{"socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"},
		{"socks5", "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"},
	}

	client := &http.Client{Timeout: 10 * time.Second}
	seen := make(map[string]bool)
	var httpList []string
	var socks5List []string

	fmt.Println("[1/3] Downloading proxy lists from raw endpoints...")
	for _, src := range sources {
		resp, err := client.Get(src.URL)
		if err != nil {
			fmt.Printf("  [-] Failed to fetch %s: %v\n", src.URL, err)
			continue
		}
		data, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			continue
		}

		lines := strings.Split(string(data), "\n")
		count := 0
		for _, line := range lines {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			parts := strings.Split(line, ":")
			if len(parts) == 2 {
				proxyUrl := fmt.Sprintf("%s://%s", src.Scheme, line)
				if !seen[proxyUrl] {
					seen[proxyUrl] = true
					if src.Scheme == "http" {
						httpList = append(httpList, proxyUrl)
					} else {
						socks5List = append(socks5List, proxyUrl)
					}
					count++
				}
			}
		}
		fmt.Printf("  [+] Fetched %d unique proxies from %s\n", count, src.URL)
	}

	total := len(httpList) + len(socks5List)
	fmt.Printf("[2/3] Total unique raw proxies parsed: %d (HTTP: %d, SOCKS5: %d)\n", total, len(httpList), len(socks5List))
	if total == 0 {
		fmt.Println("No proxies found.")
		return
	}

	// Shuffle candidates for fair geographic/network sampling
	r := rand.New(rand.NewSource(time.Now().UnixNano()))
	r.Shuffle(len(httpList), func(i, j int) { httpList[i], httpList[j] = httpList[j], httpList[i] })
	r.Shuffle(len(socks5List), func(i, j int) { socks5List[i], socks5List[j] = socks5List[j], socks5List[i] })

	var toTest []string
	takeH := 200
	if len(httpList) < takeH {
		takeH = len(httpList)
	}
	toTest = append(toTest, httpList[:takeH]...)

	takeS := 250
	if len(socks5List) < takeS {
		takeS = len(socks5List)
	}
	toTest = append(toTest, socks5List[:takeS]...)

	fmt.Printf("[3/3] Probing %d candidates (HTTP: %d, SOCKS5: %d) with 2.5s TCP timeout...\n", len(toTest), takeH, takeS)

	var liveHttp []string
	var liveSocks5 []string
	var mu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, 80)

	for _, prx := range toTest {
		wg.Add(1)
		go func(target string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			trimmed := target
			if idx := strings.Index(trimmed, "://"); idx >= 0 {
				trimmed = trimmed[idx+3:]
			}
			conn, err := net.DialTimeout("tcp", trimmed, 2500*time.Millisecond)
			if err == nil {
				conn.Close()
				mu.Lock()
				if strings.HasPrefix(target, "socks5://") {
					liveSocks5 = append(liveSocks5, target)
				} else {
					liveHttp = append(liveHttp, target)
				}
				mu.Unlock()
			}
		}(prx)
	}

	wg.Wait()
	fmt.Printf("  [+] Probing completed: %d live HTTP, %d live SOCKS5 (Total %d responsive).\n",
		len(liveHttp), len(liveSocks5), len(liveHttp)+len(liveSocks5))

	outPath := "proxies.txt"
	f, err := os.Create(outPath)
	if err != nil {
		fmt.Printf("Failed to create %s: %v\n", outPath, err)
		return
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	w.WriteString("# proxies.txt - Downloaded & Verified Proxy Pool\n")
	w.WriteString(fmt.Sprintf("# Updated: %s UTC · %d verified responsive (SOCKS5: %d, HTTP: %d) · %d raw pool entries\n",
		time.Now().UTC().Format(time.RFC3339), len(liveHttp)+len(liveSocks5), len(liveSocks5), len(liveHttp), total))
	w.WriteString("# Formats: http://host:port, socks5://host:port, http://user:pass@host:port, socks5://user:pass@host:port\n")
	w.WriteString("# The launcher automatically samples from this list randomly on every launch by default.\n\n")

	w.WriteString("# --- VERIFIED SOCKS5 PROXIES ---\n")
	for _, p := range liveSocks5 {
		w.WriteString(p + "\n")
	}

	w.WriteString("\n# --- VERIFIED HTTP PROXIES ---\n")
	for _, p := range liveHttp {
		w.WriteString(p + "\n")
	}

	w.WriteString("\n# --- ADDITIONAL RAW POOL (AUTOMATIC FAILOVER) ---\n")
	rawAdded := 0
	for _, p := range socks5List[takeS:] {
		if rawAdded >= 150 {
			break
		}
		w.WriteString(p + "\n")
		rawAdded++
	}
	for _, p := range httpList[takeH:] {
		if rawAdded >= 300 {
			break
		}
		w.WriteString(p + "\n")
		rawAdded++
	}

	w.Flush()
	fmt.Printf("Successfully saved %s with %d verified active and %d backup pool proxies.\n",
		outPath, len(liveHttp)+len(liveSocks5), rawAdded)
}
