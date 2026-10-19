/**
 * Dofus Touch Visitor (Guest) Account Generator & Credential Pool Manager
 * 
 * Automates the creation of guest accounts, handles the PKCE authorization cycle,
 * extracts permanent {login, password} credentials, and appends them to accounts.txt
 * so instances can log in directly without going through the webview popup.
 */

const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const url = require('url');

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
        return new Promise((resolve, reject) => {
            const postData = 'client_id=18';
            const options = {
                hostname: 'auth.ankama.com',
                port: 443,
                path: '/token/create-guest',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': Buffer.byteLength(postData),
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36',
                    'Origin': 'https://www.dofus-touch.com',
                    'Referer': 'https://www.dofus-touch.com/'
                }
            };

            const req = https.request(options, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    if (res.statusCode === 200) {
                        try {
                            const json = JSON.parse(data);
                            resolve(json.token);
                        } catch (e) {
                            reject(new Error('Failed to parse guest token: ' + data));
                        }
                    } else {
                        reject(new Error(`create-guest returned HTTP ${res.statusCode}: ${data}`));
                    }
                });
            });

            req.on('error', reject);
            req.write(postData);
            req.end();
        });
    }

    /**
     * Exchange authorization code for permanent guest credentials.
     */
    exchangeCode(code, verifier, redirectUri) {
        return new Promise((resolve, reject) => {
            const postData = `grant_type=authorization_code&client_id=18&code=${encodeURIComponent(code)}&code_verifier=${encodeURIComponent(verifier)}&redirect_uri=${encodeURIComponent(redirectUri)}`;
            const options = {
                hostname: 'auth.ankama.com',
                port: 443,
                path: '/token',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Content-Length': Buffer.byteLength(postData),
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36'
                }
            };

            const req = https.request(options, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    if (res.statusCode === 200) {
                        try {
                            const json = JSON.parse(data);
                            resolve(json);
                        } catch (e) {
                            reject(new Error('Failed to parse exchange JSON: ' + data));
                        }
                    } else {
                        reject(new Error(`Token exchange failed with HTTP ${res.statusCode}: ${data}`));
                    }
                });
            });

            req.on('error', reject);
            req.write(postData);
            req.end();
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
