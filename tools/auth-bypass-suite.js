/**
 * Dofus Touch Auth Direct Injector & Split-Routing Diagnostic
 * 
 * Bypasses the /ankama-guest/authorize CloudFront webview and per-IP guest creation
 * quotas by directly managing session credentials and localStorage injection.
 */

const fs = require('fs');
const path = require('path');
const { postForm } = require('./https-post');

class AuthBypassSuite {
    constructor(baseDir) {
        this.baseDir = baseDir || path.resolve(__dirname, '..');
        this.cacheDir = path.join(this.baseDir, 'asset_cache');
    }

    /**
     * Inspects stored accounts in WebView2 per-instance User Data profiles.
     */
    scanStoredCredentials() {
        console.log('[+] Scanning local instance storage for saved guest credentials...');
        const userDataRoot = path.join(process.env.LOCALAPPDATA || '', 'DofusFarm', 'WebView2');
        const found = [];

        if (fs.existsSync(userDataRoot)) {
            const slots = fs.readdirSync(userDataRoot);
            for (const slot of slots) {
                const lsPath = path.join(userDataRoot, slot, 'Default', 'Local Storage', 'leveldb');
                if (fs.existsSync(lsPath)) {
                    found.push({ slot, path: lsPath });
                }
            }
        }
        console.log(`[+] Found ${found.length} active instance storage profiles.`);
        return found;
    }

    /**
     * Generates a direct script injection payload to bypass the OAuth popup entirely.
     * When injected into WebView2 before loginScreen displays, it sets the guestAccount
     * preferences so the client executes playWithGuest() -> direct socket auth.
     */
    generateDirectAuthPayload(login, password) {
        return `(function() {
    try {
        if (!window.localStorage) return false;
        var creds = { login: "${login}", password: "${password}" };
        localStorage.setItem("guestAccount", JSON.stringify(creds));
        localStorage.setItem("WEBAUTH_TYPE", "guest");
        console.log("[AUTH-BYPASS] Injected credentials directly into localStorage. Skipping /ankama-guest/authorize.");
        if (window.gui && window.gui.loginScreen) {
            window.gui.loginScreen.playWithGuest();
        }
        return true;
    } catch(e) {
        console.error("[AUTH-BYPASS] Injection failed:", e);
        return false;
    }
})();`;
    }

    /**
     * Direct Haapi Token Authenticator:
     * Obtains an API key using raw login/password over Haapi HTTPS API
     * without ever loading the CloudFront webauth popup.
     */
    requestHaapiApiKey(login, password, proxyUrl) {
        const postData = `login=${encodeURIComponent(login)}&password=${encodeURIComponent(password)}&long_life_token=0`;
        return postForm({
            hostname: 'haapi.ankama.com',
            path: '/json/Ankama/v5/Account/CreateApiKey',
            headers: { 'apiKey': '18' }
        }, postData, json => json, {
            http: 'Haapi responded with HTTP ',
            parse: 'Invalid JSON from Haapi: '
        });
    }
}

if (require.main === module) {
    const suite = new AuthBypassSuite();
    console.log('==========================================================');
    console.log('   DOFUS TOUCH DIRECT AUTH & GUEST BYPASS SUITE           ');
    console.log('==========================================================');
    suite.scanStoredCredentials();
    console.log('\n[!] Architectural Reality of Server-Side Verification:');
    console.log('    1. Server-side checks at auth.ankama.com/login/ankama-guest/authorize');
    console.log('       execute on Ankama/AWS CloudFront servers, not in the client bundle.');
    console.log('    2. Datacenter IPs are statically blocked by CloudFront WAF at layer 7.');
    console.log('    3. Clean bypass relies on either:');
    console.log('       a) Split routing: mobile hotspot (residential/carrier ASN) for auth');
    console.log('       b) Direct credentials/tokens: bypasses /ankama-guest/authorize entirely.');
}

module.exports = AuthBypassSuite;
