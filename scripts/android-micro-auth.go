package main

import (
	"bufio"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type GuestTokenResponse struct {
	Token string `json:"token"`
}

type TokenExchangeResponse struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	AccountId    int64  `json:"account_id"`
	AccountLogin string `json:"login"`
	AccountPass  string `json:"password"`
	ExpiresIn    int    `json:"expires_in"`
}

type AndroidDeviceProfile struct {
	Model       string
	Fingerprint string
	AndroidId   string
	UserAgent   string
}

func GetDefaultAndroidProfile() AndroidDeviceProfile {
	return AndroidDeviceProfile{
		Model:       "SM-A515F",
		Fingerprint: "samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU4CUB1:user/release-keys",
		AndroidId:   "3a7f8e9b0c1d2e3f",
		UserAgent:   "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
	}
}

func generatePKCE() (string, string) {
	b := make([]byte, 32)
	_, _ = rand.Read(b)
	verifier := base64.RawURLEncoding.EncodeToString(b)
	h := sha256.Sum256([]byte(verifier))
	challenge := base64.RawURLEncoding.EncodeToString(h[:])
	return verifier, challenge
}

func appendAccount(accountsFile, login, pass string) error {
	f, err := os.OpenFile(accountsFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	line := fmt.Sprintf("%s:%s\n", strings.TrimSpace(login), strings.TrimSpace(pass))
	_, err = f.WriteString(line)
	return err
}

func countAccounts(accountsFile string) int {
	f, err := os.Open(accountsFile)
	if err != nil {
		return 0
	}
	defer f.Close()

	count := 0
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		l := strings.TrimSpace(sc.Text())
		if len(l) > 0 && !strings.HasPrefix(l, "#") {
			count++
		}
	}
	return count
}

func buildHttpClient(proxyUrl string) (*http.Client, error) {
	jar, _ := cookiejar.New(nil)
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: false,
			MinVersion:         tls.VersionTLS12,
		},
		DisableKeepAlives: false,
		MaxIdleConns:      10,
		IdleConnTimeout:   30 * time.Second,
	}

	if proxyUrl != "" && !strings.EqualFold(proxyUrl, "DIRECT") {
		u, err := url.Parse(proxyUrl)
		if err != nil {
			return nil, fmt.Errorf("invalid proxy url: %v", err)
		}
		transport.Proxy = http.ProxyURL(u)
	}

	client := &http.Client{
		Jar:       jar,
		Transport: transport,
		Timeout:   20 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if strings.HasPrefix(req.URL.String(), "dofustouch://") ||
				strings.Contains(req.URL.String(), "login-authorized") {
				return http.ErrUseLastResponse
			}
			return nil
		},
	}
	return client, nil
}

func RequestGuestToken(client *http.Client, profile AndroidDeviceProfile) (string, error) {
	form := url.Values{}
	form.Set("client_id", "18")

	req, err := http.NewRequest("POST", "https://auth.ankama.com/token/create-guest", strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}

	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("User-Agent", profile.UserAgent)
	req.Header.Set("Origin", "https://www.dofus-touch.com")
	req.Header.Set("Referer", "https://www.dofus-touch.com/")
	req.Header.Set("X-Requested-With", "com.ankama.dofustouch")

	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("create-guest request error: %v", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("reading response: %v", err)
	}

	if resp.StatusCode == 403 {
		return "", fmt.Errorf("HTTP 403 CloudFront WAF Block (proxy ASN blacklisted or datacenter IP)")
	}

	if resp.StatusCode != 200 {
		return "", fmt.Errorf("HTTP %d unexpected status: %s", resp.StatusCode, string(body))
	}

	var parsed GuestTokenResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return "", fmt.Errorf("parsing token response: %v", err)
	}

	if parsed.Token == "" {
		return "", fmt.Errorf("empty guest token received")
	}

	return parsed.Token, nil
}

func ExchangeAuthCode(client *http.Client, code, verifier, redirectUri string, profile AndroidDeviceProfile) (*TokenExchangeResponse, error) {
	form := url.Values{}
	form.Set("grant_type", "authorization_code")
	form.Set("client_id", "18")
	form.Set("code", code)
	form.Set("code_verifier", verifier)
	form.Set("redirect_uri", redirectUri)

	req, err := http.NewRequest("POST", "https://auth.ankama.com/token", strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("User-Agent", profile.UserAgent)
	req.Header.Set("Origin", "https://www.dofus-touch.com")
	req.Header.Set("Referer", "https://www.dofus-touch.com/")
	req.Header.Set("X-Requested-With", "com.ankama.dofustouch")

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("exchange request failed: %v", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading exchange response: %v", err)
	}

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("exchange returned HTTP %d: %s", resp.StatusCode, string(body))
	}

	var tokenResp TokenExchangeResponse
	if err := json.Unmarshal(body, &tokenResp); err != nil {
		return nil, fmt.Errorf("parsing exchange json: %v", err)
	}

	return &tokenResp, nil
}

func main() {
	proxyFlag := flag.String("proxy", "", "Upstream proxy (http://..., socks5://..., or empty for DIRECT)")
	accountsFlag := flag.String("accounts", "accounts.txt", "Path to accounts.txt target file")
	authCodeFlag := flag.String("exchange", "", "Exchange authorization code directly")
	verifierFlag := flag.String("verifier", "", "PKCE verifier corresponding to authorization code")
	redirectFlag := flag.String("redirect", "dofustouch://authorized", "OAuth redirect URI")
	flag.Parse()

	fmt.Println("==========================================================")
	fmt.Println("   DOFUS TOUCH ANDROID MICRO-AUTH PROVISIONER (AOSP/CORDOVA) ")
	fmt.Println("==========================================================")

	profile := GetDefaultAndroidProfile()
	absAccounts, _ := filepath.Abs(*accountsFlag)
	initialCount := countAccounts(absAccounts)
	fmt.Printf("[*] Android Identity : %s (Android 10, Cordova 3.2.7)\n", profile.Model)
	fmt.Printf("[*] Accounts Pool    : %s (Current: %d accounts)\n", absAccounts, initialCount)
	if *proxyFlag != "" {
		fmt.Printf("[*] Network Egress   : %s\n", *proxyFlag)
	} else {
		fmt.Println("[*] Network Egress   : DIRECT (Local machine interface)")
	}

	client, err := buildHttpClient(*proxyFlag)
	if err != nil {
		fmt.Printf("[-] Failed to initialize HTTP client: %v\n", err)
		os.Exit(1)
	}

	if *authCodeFlag != "" && *verifierFlag != "" {
		fmt.Println("\n[*] Direct Code Exchange Mode Activated...")
		res, err := ExchangeAuthCode(client, *authCodeFlag, *verifierFlag, *redirectFlag, profile)
		if err != nil {
			fmt.Printf("[-] Exchange error: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("[+] Permanent Credentials Recovered: Login=%s, AccountId=%d\n", res.AccountLogin, res.AccountId)
		if res.AccountLogin != "" && res.AccountPass != "" {
			_ = appendAccount(absAccounts, res.AccountLogin, res.AccountPass)
			fmt.Printf("[+] Written to %s\n", absAccounts)
		}
		return
	}

	fmt.Println("\n[1/3] Step 1: Requesting Guest Provisioning Token (client_id=18)...")
	token, err := RequestGuestToken(client, profile)
	if err != nil {
		fmt.Printf("[-] Step 1 Failed: %v\n", err)
		if strings.Contains(err.Error(), "403") {
			fmt.Println("[!] Resolution: Datacenter IP blocked by CloudFront WAF. Use residential SOCKS5 or mobile egress.")
		}
		os.Exit(1)
	}
	fmt.Printf("[+] Step 1 Succeeded: Guest Token = %s\n", token)

	verifier, challenge := generatePKCE()
	fmt.Printf("[+] Step 2 Generated PKCE: verifier=%s challenge=%s\n", verifier, challenge)

	authUrl := fmt.Sprintf(
		"https://auth.ankama.com/login/ankama-guest?client_id=18&code_challenge=%s&redirect_uri=%s&origin_tracker=https://www.dofus-touch.com/&token=%s",
		challenge, url.QueryEscape(*redirectFlag), token,
	)
	fmt.Printf("[*] Step 2 Authorize URL: %s\n", authUrl)

	reqAuth, _ := http.NewRequest("GET", authUrl, nil)
	reqAuth.Header.Set("User-Agent", profile.UserAgent)
	reqAuth.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
	reqAuth.Header.Set("X-Requested-With", "com.ankama.dofustouch")

	respAuth, err := client.Do(reqAuth)
	if err != nil {
		fmt.Printf("[-] Step 2 Error: %v\n", err)
		os.Exit(1)
	}
	defer respAuth.Body.Close()
	bodyAuth, _ := io.ReadAll(respAuth.Body)

	fmt.Printf("[*] Step 2 Response Code: %d (URL: %s)\n", respAuth.StatusCode, respAuth.Request.URL.String())

	if respAuth.StatusCode == 202 {
		fmt.Println("[!] Intercepted CloudFront AWS WAF JavaScript Challenge (window.gokuProps).")
		fmt.Println("    In WebView2/Android InAppBrowser, the DOM renders the JS solve automatically.")
		fmt.Println("    On successful challenge solve, the browser redirects to /login-authorized?code=...")
		fmt.Println("    Once the code is captured, run with: -exchange <code> -verifier <verifier>")
	} else if respAuth.StatusCode == 403 {
		fmt.Println("[-] HTTP 403: Ankama backend account creation throttle active on this IP.")
		fmt.Println("    Ankama limits guest creations to 1-2 per 24h per IP.")
		fmt.Println("    Resolution: Rotate IP via Phone USB Tethering (Airplane mode toggle) or SOCKS5 proxy.")
	} else if respAuth.StatusCode == 302 || respAuth.StatusCode == 301 {
		loc := respAuth.Header.Get("Location")
		fmt.Printf("[+] Redirected to: %s\n", loc)
	} else {
		preview := string(bodyAuth)
		if len(preview) > 300 {
			preview = preview[:300] + "..."
		}
		fmt.Printf("[*] Body Preview: %s\n", preview)
	}
}
