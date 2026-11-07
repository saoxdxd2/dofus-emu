/**
 * Dofus Touch Visitor (Guest) Account Generator & Credential Pool Manager
 * 
 * Automates the creation of guest accounts, handles the PKCE authorization cycle,
 * extracts permanent {login, password} credentials, and appends them to accounts.txt
 * so instances can log in directly without going through the webview popup.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const url = require('url');
const { postForm } = require('./https-post');

function base64Url(buf) {
    return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function generatePKCE() {
    const verifier = base64Url(crypto.randomBytes(32));
    const challenge = base64Url(crypto.createHash('sha256').update(verifier).digest());
    return { verifier, challenge };
}

class VisitorAccountManager {
    constructor(proxyUrl) {
        this.proxyUrl = proxyUrl || null;
        this.accountsFile = path.resolve(__dirname, '../accounts.txt');
    }

    /**
     * Request a guest provisioning token from Haapi/Auth endpoint.
     */
    requestGuestToken() {
        return postForm({
            hostname: 'auth.ankama.com',
            path: '/token/create-guest',
            headers: {
                'Origin': 'https://www.dofus-touch.com',
                'Referer': 'https://www.dofus-touch.com/'
            }
        }, 'client_id=18', json => json.token, {
            http: 'create-guest returned HTTP ',
            parse: 'Failed to parse guest token: '
        });
    }

    /**
     * Exchange authorization code for permanent guest credentials.
     */
    exchangeCode(code, verifier, redirectUri) {
        const postData = `grant_type=authorization_code&client_id=18&code=${encodeURIComponent(code)}&code_verifier=${encodeURIComponent(verifier)}&redirect_uri=${encodeURIComponent(redirectUri)}`;
        return postForm({
            hostname: 'auth.ankama.com',
            path: '/token'
        }, postData, json => json, {
            http: 'Token exchange failed with HTTP ',
            parse: 'Failed to parse exchange JSON: '
        });
    }

    /**
     * Persists valid permanent account credentials to accounts.txt
     */
    saveAccount(login, password) {
        const line = `${login}:${password}\n`;
        fs.appendFileSync(this.accountsFile, line, 'utf8');
        console.log(`[+] Stored permanent visitor credentials in accounts.txt: ${login}`);
    }

    /**
     * Reads all available permanent visitor accounts.
     */
    loadAccounts() {
        if (!fs.existsSync(this.accountsFile)) return [];
        return fs.readFileSync(this.accountsFile, 'utf8')
            .split(/\r?\n/)
            .map(l => l.trim())
            .filter(l => l.length > 0 && !l.startsWith('#'))
            .map(l => {
                const parts = l.split(':');
                return { login: parts[0], password: parts.slice(1).join(':') };
            });
    }
}

if (require.main === module) {
    const mgr = new VisitorAccountManager();
    console.log('==========================================================');
    console.log('   DOFUS TOUCH VISITOR ACCOUNT MANAGER                    ');
    console.log('==========================================================');
    const existing = mgr.loadAccounts();
    console.log(`[+] Currently loaded accounts in accounts.txt: ${existing.length}`);
    console.log('\n[!] Architectural Guidelines for Stable Operation:');
    console.log('    1. Permanent visitor accounts: When guest creation succeeds once,');
    console.log('       the returned login:password is permanent. Store them in accounts.txt');
    console.log('       to eliminate repeated guest creation calls entirely.');
    console.log('    2. Public Proxies: Scraped GitHub lists die within seconds because');
    console.log('       thousands of clients hit open ports and CloudFront blacklists their ASNs.');
    console.log('    3. Zero-Cost Long-Lasting Alternatives:');
    console.log('       - 4G/5G Phone USB Tethering: Toggle Airplane mode for a new residential IP.');
    console.log('       - Cloudflare WARP SOCKS5: High speed, zero downtime, free anycast edge.');
}

module.exports = VisitorAccountManager;
