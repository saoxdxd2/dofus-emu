package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"time"
)

func genPKCE() (string, string) {
	b := make([]byte, 32)
	rand.Read(b)
	verifier := base64.RawURLEncoding.EncodeToString(b)
	h := sha256.Sum256([]byte(verifier))
	challenge := base64.RawURLEncoding.EncodeToString(h[:])
	return verifier, challenge
}

func main() {
	jar, _ := cookiejar.New(nil)
	client := &http.Client{
		Jar:     jar,
		Timeout: 15 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			fmt.Printf("  [Redirect ->] %s\n", req.URL.String())
			return nil
		},
	}

	fmt.Println("==========================================================")
	fmt.Println("   ANKAMA GUEST PROVISIONING PROTOCOL TRACE               ")
	fmt.Println("==========================================================")

	// Step 1: create-guest token
	fmt.Println("\n[1/3] Calling https://auth.ankama.com/token/create-guest...")
	form := url.Values{}
	form.Set("client_id", "18")
	req, _ := http.NewRequest("POST", "https://auth.ankama.com/token/create-guest", strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
	req.Header.Set("Origin", "https://www.dofus-touch.com")
	req.Header.Set("Referer", "https://www.dofus-touch.com/")

	resp, err := client.Do(req)
	if err != nil {
		fmt.Printf("Step 1 failed: %v\n", err)
		return
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	fmt.Printf("  Status: %d\n  Body: %s\n", resp.StatusCode, string(body))

	if resp.StatusCode != 200 {
		fmt.Println("Failed to obtain guest token.")
		return
	}

	// Extract token: {"token":"..."}
	tokenStr := string(body)
	tIdx := strings.Index(tokenStr, `"token":"`)
	if tIdx == -1 {
		fmt.Println("Could not parse token from JSON.")
		return
	}
	tokenStr = tokenStr[tIdx+9:]
	tEnd := strings.Index(tokenStr, `"`)
	if tEnd == -1 {
		return
	}
	guestToken := tokenStr[:tEnd]
	fmt.Printf("[+] Guest Token Acquired: %s\n", guestToken)

	// Step 2: PKCE + /login/ankama-guest
	verifier, challenge := genPKCE()
	fmt.Printf("[+] Generated PKCE challenge: %s (verifier: %s)\n", challenge, verifier)

	step2Url := fmt.Sprintf(
		"https://auth.ankama.com/login/ankama-guest?client_id=18&code_challenge=%s&redirect_uri=dofustouch://authorized&origin_tracker=https://www.dofus-touch.com/&token=%s",
		challenge, guestToken,
	)

	fmt.Printf("\n[2/3] Calling %s...\n", step2Url)
	req2, _ := http.NewRequest("GET", step2Url, nil)
	req2.Header.Set("User-Agent", "Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
	req2.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")

	resp2, err := client.Do(req2)
	if err != nil {
		fmt.Printf("Step 2 failed: %v\n", err)
		return
	}
	body2, _ := io.ReadAll(resp2.Body)
	resp2.Body.Close()

	fmt.Printf("  Final URL   : %s\n", resp2.Request.URL.String())
	fmt.Printf("  Final Status: %d\n", resp2.StatusCode)
	bodyPreview := string(body2)
	if len(bodyPreview) > 400 {
		bodyPreview = bodyPreview[:400] + "..."
	}
	fmt.Printf("  Response Preview:\n%s\n", bodyPreview)
}
