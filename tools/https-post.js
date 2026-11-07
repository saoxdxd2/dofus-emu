// Shared HTTPS form POST for the direct-auth and visitor-account tools.

'use strict';

const https = require('https');

const MOBILE_UA = 'Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36';

function postForm(options, postData, parse, labels) {
    return new Promise((resolve, reject) => {
        const headers = Object.assign({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': Buffer.byteLength(postData),
            'User-Agent': MOBILE_UA
        }, options.headers || {});
        const req = https.request({
            hostname: options.hostname,
            port: 443,
            path: options.path,
            method: 'POST',
            headers
        }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                if (res.statusCode !== 200) {
                    reject(new Error(labels.http + res.statusCode + ': ' + data));
                    return;
                }
                try {
                    resolve(parse(JSON.parse(data)));
                } catch (e) {
                    reject(new Error(labels.parse + data));
                }
            });
        });
        req.on('error', reject);
        req.write(postData);
        req.end();
    });
}

module.exports = { postForm };
