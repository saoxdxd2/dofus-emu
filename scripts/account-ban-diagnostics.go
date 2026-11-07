package main

import (
	"crypto/tls"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type BanClassification int

const (
	BanUnknown BanClassification = iota
	BanNone
	BanIpWaf
	BanDeviceFingerprint
	BanServerAccountPerm
)

type AccountStatusResult struct {
	Classification BanClassification `json:"classification"`
	StatusText     string            `json:"status_text"`
	CanBypassLocally bool            `json:"can_bypass_locally"`
	RemedyAction   string            `json:"remedy_action"`
	RawStatusCode  int               `json:"raw_status_code"`
	RawResponse    string            `json:"raw_response"`
}

func main() {
	proxyFlag := flag.String("proxy", "", "Upstream proxy URL (http://... or socks5://...)")
	tokenFlag := flag.String("token", "", "Account session/OAuth token to diagnose")
	flag.Parse()

	fmt.Println("==========================================================")
	fmt.Println("   ANKAMA ACCOUNT BAN & ENFORCEMENT LEVEL DIAGNOSTIC      ")
	fmt.Println("==========================================================")

	targetEndpoint := "https://auth.ankama.com/login/ankama-guest/authorize"
	if *tokenFlag != "" {
		targetEndpoint = "https://haapi.ankama.com/json/Ankama/v5/Account/Status"
	}

	result := DiagnoseBanSurface(targetEndpoint, *tokenFlag, *proxyFlag)

	fmt.Printf("\n[+] Target Endpoint : %s\n", targetEndpoint)
	fmt.Printf("[+] Proxy Egress    : %s\n", func() string {
		if *proxyFlag == "" {
			return "DIRECT"
		}
		return *proxyFlag
	}())
	fmt.Printf("[+] HTTP Status     : %d\n", result.RawStatusCode)
	fmt.Printf("[+] Ban Level       : %s\n", result.StatusText)
	fmt.Printf("[+] Client Bypass?  : %v\n", result.CanBypassLocally)
	fmt.Printf("[+] Engineering Move: %s\n\n", result.RemedyAction)

	if len(result.RawResponse) > 0 {
		fmt.Printf("--- Raw Upstream Response (Truncated) ---\n%s\n", func() string {
			if len(result.RawResponse) > 300 {
				return result.RawResponse[:300] + "..."
			}
			return result.RawResponse
		}())
	}
}

func DiagnoseBanSurface(targetUrl, token, proxyStr string) AccountStatusResult {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: false},
	}

	if proxyStr != "" {
		proxyUrl, err := url.Parse(proxyStr)
		if err == nil {
			transport.Proxy = http.ProxyURL(proxyUrl)
		}
	}

	client := &http.Client{
		Transport: transport,
		Timeout:   10 * time.Second,
	}

	req, err := http.NewRequest("GET", targetUrl, nil)
	if err != nil {
		return AccountStatusResult{
			Classification: BanUnknown,
			StatusText:     "REQUEST_BUILD_ERROR",
			RemedyAction:   "Verify network stack and URI syntax.",
		}
	}

	req.Header.Set("User-Agent", "Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
	req.Header.Set("Accept", "application/json, text/plain, */*")
	if token != "" {
		req.Header.Set("APIKEY", token)
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := client.Do(req)
	if err != nil {
		errStr := err.Error()
		if strings.Contains(errStr, "proxyconnect") || strings.Contains(errStr, "refused") {
			return AccountStatusResult{
				Classification:   BanIpWaf,
				StatusText:       "EGRESS_PROXY_DEAD_OR_TIMEOUT",
				CanBypassLocally: true,
				RemedyAction:     "Swap upstream proxy in proxies.txt. Egress node unreachable.",
			}
		}
		return AccountStatusResult{
			Classification:   BanUnknown,
			StatusText:       "TRANSPORT_FAILED: " + errStr,
			CanBypassLocally: false,
			RemedyAction:     "Inspect network route and TLS handshake.",
		}
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(resp.Body)
	bodyStr := string(bodyBytes)

	// Evaluate Enforcement Tier:
	// Tier 1: CloudFront WAF IP Throttle (403 with AWS/CloudFront body)
	if resp.StatusCode == 403 && (strings.Contains(bodyStr, "CloudFront") || strings.Contains(bodyStr, "AccessDenied") || strings.Contains(bodyStr, "blocked")) {
		return AccountStatusResult{
			Classification:   BanIpWaf,
			StatusText:       "TIER 1: IP / WAF REPUTATION BAN",
			CanBypassLocally: true,
			RemedyAction:     "Fully bypassable client-side. Rotate egress IP via proxies.txt or mobile tethering.",
			RawStatusCode:    resp.StatusCode,
			RawResponse:      bodyStr,
		}
	}

	// Tier 2: Device Identifier Flag (Payload rejection based on uuid/serial)
	if strings.Contains(bodyStr, "DEVICE_LOCKED") || strings.Contains(bodyStr, "SUSPICIOUS_CLIENT") {
		return AccountStatusResult{
			Classification:   BanDeviceFingerprint,
			StatusText:       "TIER 2: DEVICE FINGERPRINT BAN (HARDWARE HASH)",
			CanBypassLocally: true,
			RemedyAction:     "Fully bypassable client-side. Regenerate serial, MAC, Android UUID, and WebGL canvas hash via instance settings.",
			RawStatusCode:    resp.StatusCode,
			RawResponse:      bodyStr,
		}
	}

	// Tier 3: Server Database Record Flag (Account-level definitive ban)
	if strings.Contains(bodyStr, "ACCOUNT_BANNED") || strings.Contains(bodyStr, "DEFINITIVE_BAN") || strings.Contains(bodyStr, "ui.webauth.errorOnLogin") || strings.Contains(bodyStr, "errorOnTokenValidation") {
		return AccountStatusResult{
			Classification:   BanServerAccountPerm,
			StatusText:       "TIER 3: PERMANENT SERVER-SIDE ACCOUNT BAN",
			CanBypassLocally: false,
			RemedyAction:     "IMMUTABLE CLIENT-SIDE: Ban flag is stored in Ankama's PostgreSQL database table. Client cannot overwrite remote SQL records. Replace account: trigger guest generator on clean slot.",
			RawStatusCode:    resp.StatusCode,
			RawResponse:      bodyStr,
		}
	}

	if resp.StatusCode == 200 || resp.StatusCode == 302 {
		return AccountStatusResult{
			Classification:   BanNone,
			StatusText:       "CLEAN / AUTHORIZED",
			CanBypassLocally: true,
			RemedyAction:     "Account and egress are valid. No restriction active.",
			RawStatusCode:    resp.StatusCode,
			RawResponse:      bodyStr,
		}
	}

	return AccountStatusResult{
		Classification:   BanUnknown,
		StatusText:       fmt.Sprintf("AMBIGUOUS_RESPONSE (HTTP %d)", resp.StatusCode),
		CanBypassLocally: false,
		RemedyAction:     "Evaluate raw JSON payload against authentication dictionary.",
		RawStatusCode:    resp.StatusCode,
		RawResponse:      bodyStr,
	}
}
