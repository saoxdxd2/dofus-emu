(function () {
    'use strict';
    console.log('[CordovaShim] Standalone Cordova environment shim initializing...');

    window.device = window.device || {
        model: 'SM-A515F',
        manufacturer: 'samsung',
        isVirtual: false,
        platform: 'Android',
        version: '10',
        uuid: 'a51nsxx3f8291a0b5c12847',
        cordova: '10.0.0',
        serial: 'R58M12345678'
    };

    window.Connection = window.Connection || {
        UNKNOWN: 'unknown',
        ETHERNET: 'ethernet',
        WIFI: 'wifi',
        CELL_2G: '2g',
        CELL_3G: '3g',
        CELL_4G: '4g',
        CELL: 'cellular',
        NONE: 'none'
    };

    function safeSetNav(prop, val) {
        try {
            var target = (typeof Navigator !== 'undefined' && Navigator.prototype) ? Navigator.prototype : window.navigator;
            Object.defineProperty(target, prop, {
                value: val,
                configurable: true,
                writable: true,
                enumerable: true
            });
        } catch (e) {
            try { window.navigator[prop] = val; } catch (e2) {}
        }
    }

    safeSetNav('connection', {
        type: window.Connection.WIFI,
        effectiveType: '4g',
        rtt: 50,
        downlink: 10,
        saveData: false
    });

    // Safety guard for early scripts calling getComputedStyle before document.body finishes attaching
    var origGetComputedStyle = window.getComputedStyle;
    window.getComputedStyle = function (el, pseudo) {
        if (!el) el = document.body || document.documentElement || document.createElement('div');
        return origGetComputedStyle.call(window, el, pseudo);
    };

    safeSetNav('splashscreen', {
        show: function () { console.log('[Splashscreen] show'); },
        hide: function () { console.log('[Splashscreen] hide'); }
    });

    window.StatusBar = window.StatusBar || {
        hide: function () {},
        show: function () {},
        overlaysWebView: function () {}
    };

    safeSetNav('notification', {
        alert: function (message, callback, title, buttonName) {
            console.log('[Notification.alert] ' + title + ': ' + message);
            if (typeof callback === 'function') setTimeout(callback, 0);
        },
        confirm: function (message, callback, title, buttonLabels) {
            if (typeof callback === 'function') setTimeout(function () { callback(1); }, 0);
        }
    });

    var curAppInfo = window.appInfo || {
        version: '3.14.2',
        identifier: 'com.ankama.dofustouch',
        build: '3.14.2',
        versionCode: '3014002'
    };
    window.appInfo = curAppInfo;
    if (!window.buildVersion) window.buildVersion = '1.74.8';

    safeSetNav('appInfo', {
        version: curAppInfo.version,
        identifier: curAppInfo.identifier,
        build: curAppInfo.build,
        versionCode: curAppInfo.versionCode,
        getAppInfo: function (success, error) {
            if (typeof success === 'function') {
                success(curAppInfo);
            }
        }
    });

    window.AppSettings = window.AppSettings || {
        get: function (success, error, keys) {
            if (typeof success === 'function') {
                success({
                    server: 'https://dt-proxy-production-login.ankama-games.com'
                });
            }
        }
    };

    window.cordova = window.cordova || {};
    window.cordova.platformId = 'android';
    window.cordova.platformVersion = '10.0.0';
    window.cordova.version = '10.0.0';

    window.cordova.plugins = window.cordova.plugins || {};
    window.cordova.plugins.Keyboard = window.cordova.plugins.Keyboard || {
        hideKeyboardAccessoryBar: function () {},
        close: function () {},
        disableScroll: function () {},
        show: function () {},
        isVisible: false
    };

    window.cordova.plugins.screenorientation = window.cordova.plugins.screenorientation || {
        lock: function () {},
        unlock: function () {},
        setOrientation: function () {},
        type: 'landscape-primary',
        angle: 90
    };

    // Standalone Ionic Deeplink implementation for Ankama Connect OAuth
    window.IonicDeeplink = window.IonicDeeplink || {
        _callbacks: [],
        route: function (paths, cb) {
            if (typeof cb === 'function') this._callbacks.push(cb);
        },
        onDeepLink: function (cb) {
            if (typeof cb === 'function') this._callbacks.push(cb);
        },
        fire: function (data) {
            console.log('[IonicDeeplink] Dispatching deep link:', data);
            for (var i = 0; i < this._callbacks.length; i++) {
                try { this._callbacks[i](data); } catch (e) { console.error('[IonicDeeplink] Error:', e); }
            }
        }
    };

    window.__onDeepLinkCallback = function (urlStr) {
        try {
            console.log('[Auth] Processing DeepLink callback URL:', urlStr);
            var safeUrl = urlStr.replace('dofustouch://', 'https://dofus.local/');
            var parsedUrl = new URL(safeUrl);
            var host = parsedUrl.host || 'authorized';
            var path = parsedUrl.pathname || '';
            var queryString = parsedUrl.search ? parsedUrl.search.substring(1) : '';

            if (window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function') {
                window.IonicDeeplink.fire({
                    host: host,
                    path: path,
                    queryString: queryString,
                    url: urlStr
                });
            }
        } catch (e) {
            console.error('[Auth] Failed to parse callback URL:', e);
        }
    };

    function handleOAuthUrl(url) {
        window.__lastOAuthUrl = url;
        console.log('[Auth] Opening OAuth authorization URL in mobile container:', url);
        if (window.chrome && window.chrome.webview) {
            window.chrome.webview.postMessage({ type: 'open_oauth', url: url });
        } else {
            window.open(url, '_blank');
        }
    }

    window.cordova.plugins.browsertab = window.cordova.plugins.browsertab || {
        isAvailable: function (cb) { if (cb) cb(true); },
        openUrl: function (url, success, error) {
            handleOAuthUrl(url);
            if (typeof success === 'function') success();
        },
        close: function () {
            if (window.chrome && window.chrome.webview) {
                window.chrome.webview.postMessage({ type: 'close_oauth' });
            }
        }
    };

    window.cordova.InAppBrowser = window.cordova.InAppBrowser || {
        open: function (url, target, options) {
            handleOAuthUrl(url);
            return {
                addEventListener: function () {},
                removeEventListener: function () {},
                close: function () {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage({ type: 'close_oauth' });
                    }
                }
            };
        }
    };

    // Native bridge mock for cordova.exec
    window._cordovaNative = {
        exec: function (secret, service, action, callbackId, argsJson) {
            setTimeout(function () {
                if (!window.cordova || !window.cordova.callbackFromNative) return;
                if (service === 'AppSettings' && action === 'get') {
                    window.cordova.callbackFromNative(callbackId, true, 1, [{ server: 'https://dt-proxy-production-login.ankama-games.com' }], false);
                } else if (service === 'AppInfo' && action === 'getAppInfo') {
                    window.cordova.callbackFromNative(callbackId, true, 1, [{ version: '3.14.2', identifier: 'com.ankama.dofustouch', build: '3.14.2', versionCode: '3014002' }], false);
                } else if (service === 'Device' && action === 'getDeviceInfo') {
                    window.cordova.callbackFromNative(callbackId, true, 1, [window.device], false);
                } else if (service === 'NetworkStatus') {
                    window.cordova.callbackFromNative(callbackId, true, 1, ['wifi'], false);
                } else {
                    window.cordova.callbackFromNative(callbackId, true, 1, [null], false);
                }
            }, 0);
            return '';
        },
        setNativeToJsBridgeMode: function () {},
        retrieveJsMessages: function () { return ''; }
    };

    // Suppress prompt gap calls
    var origPrompt = window.prompt;
    window.prompt = function (message, defaultValue) {
        if (defaultValue && defaultValue.indexOf('gap_init:') === 0) return '123456';
        if (defaultValue && defaultValue.indexOf('gap:') === 0) return '';
        if (defaultValue && defaultValue.indexOf('gap_poll:') === 0) return '';
        return origPrompt ? origPrompt.apply(window, arguments) : '';
    };

    function triggerDeviceready() {
        console.log('[CordovaShim] Dispatching deviceready event...');
        try {
            var evt = document.createEvent('Event');
            evt.initEvent('deviceready', false, false);
            document.dispatchEvent(evt);
        } catch (e) {
            console.error('[CordovaShim] Error triggering deviceready:', e);
        }
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        setTimeout(triggerDeviceready, 50);
    } else {
        document.addEventListener('DOMContentLoaded', function () {
            setTimeout(triggerDeviceready, 50);
        }, false);
    }

    // ---- Temporary OAuth/auth diagnostics ----
    // Post-boot console output is swallowed by the game logger, so all diagnostics
    // go through chrome.webview.postMessage (logged by the host as JS->C# messages).
    var __authDiagSeq = 0;
    function authDiag(text) {
        try {
            __authDiagSeq++;
            if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
                window.chrome.webview.postMessage({ type: 'CDN_RELAY_DIAG', text: '[AUTH-DIAG#' + __authDiagSeq + '] ' + String(text).slice(0, 600) });
            }
        } catch (e) { }
    }
    window.__authDiag = authDiag;

    setTimeout(function () {
        authDiag('install: fetch=' + typeof window.fetch + ' webAuth=' + typeof window.__webAuth + ' ionic=' + typeof window.IonicDeeplink);

        try {
            var origFetch = window.fetch;
            if (typeof origFetch === 'function' && !window.__authFetchTap) {
                window.__authFetchTap = true;
                window.fetch = function (input, init) {
                    var url = '';
                    try { url = typeof input === 'string' ? input : (input && input.url) || ''; } catch (e) {}
                    var interesting = url.indexOf('auth.ankama.com') !== -1 || url.indexOf('/token') !== -1 || url.indexOf('dt-proxy') !== -1;
                    var bodyStr = '';
                    try { bodyStr = (init && typeof init.body === 'string') ? init.body : ''; } catch (e) {}
                    if (interesting) authDiag('fetch ' + ((init && init.method) || 'GET') + ' ' + url.slice(0, 200) + ' creds=' + ((init && init.credentials) || '(default)') + (bodyStr ? ' body=' + bodyStr.slice(0, 400) : ''));
                    var p;
                    try {
                        p = origFetch.apply(this, arguments);
                    } catch (e) {
                        authDiag('fetch THREW sync: ' + e);
                        throw e;
                    }
                    if (interesting && p && typeof p.then === 'function') {
                        p.then(function (res) {
                            authDiag('fetch RESP ' + res.status + ' ' + url.slice(0, 160));
                            try {
                                res.clone().text().then(function (t) {
                                    authDiag('fetch BODY ' + res.status + ': ' + String(t).slice(0, 300));
                                }, function () {});
                            } catch (e) {}
                            try {
                                var isTokenExchange = url.indexOf('auth.ankama.com/token') !== -1 && url.indexOf('create-guest') === -1 && url.indexOf('validate-guest') === -1;
                                if (isTokenExchange && res.status >= 400 && !window.__authTokenRetried) {
                                    window.__authTokenRetried = true;
                                    var ver = '';
                                    try { ver = new URLSearchParams(bodyStr).get('code_verifier') || ''; } catch (e) {}
                                    authDiag('token exchange failed ' + res.status + '; verifier=' + ver.slice(0, 128));
                                    try {
                                        if (ver && window.crypto && window.crypto.subtle) {
                                            window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(ver)).then(function (buf) {
                                                var arr = new Uint8Array(buf);
                                                var bin = '';
                                                for (var i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
                                                var h = btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
                                                var ch = null;
                                                try { var m = String(window.__lastOAuthUrl || '').match(/code_challenge=([^&]+)/); ch = m && decodeURIComponent(m[1]); } catch (e) {}
                                                authDiag('verifier-check match=' + (h === ch) + ' computed=' + h + ' challenge=' + ch);
                                            }, function (e) { authDiag('verifier-check digest error: ' + e); });
                                        }
                                    } catch (e) { authDiag('verifier-check error: ' + e); }
                                    try {
                                        var retryInit = {};
                                        if (init) { for (var k in init) { retryInit[k] = init[k]; } }
                                        retryInit.credentials = 'include';
                                        origFetch.call(window, input, retryInit).then(function (res2) {
                                            return res2.text().then(function (t2) {
                                                authDiag('token retry(incl) RESP ' + res2.status + ' body: ' + String(t2).slice(0, 300));
                                            }, function () { authDiag('token retry(incl) RESP ' + res2.status); });
                                        }, function (e2) {
                                            authDiag('token retry(incl) REJECTED ' + (e2 && (e2.name + ' ' + e2.message) || e2));
                                        });
                                    } catch (e) { authDiag('token retry error: ' + e); }
                                }
                            } catch (e) { authDiag('token diag error: ' + e); }
                            return res;
                        }, function (err) {
                            authDiag('fetch REJECTED ' + url.slice(0, 160) + ' :: ' + (err && (err.name + ' ' + err.message) || String(err)));
                            throw err;
                        });
                    }
                    return p;
                };
                authDiag('fetch tap installed');
            } else {
                authDiag('fetch tap skipped (fetch=' + typeof origFetch + ')');
            }
        } catch (e) {
            authDiag('fetch tap error: ' + e);
        }

        try {
            var wrapTries = 0;
            var iv = setInterval(function () {
                wrapTries++;
                var wa = window.__webAuth;
                if (wa && typeof wa.connectThroughIonicDeepLink === 'function' && !wa.__authWrapped) {
                    wa.__authWrapped = true;
                    var origConnect = wa.connectThroughIonicDeepLink;
                    wa.connectThroughIonicDeepLink = function (e) {
                        try {
                            var copy = {};
                            if (e) { for (var k in e) { if (Object.prototype.hasOwnProperty.call(e, k)) copy[k] = String(e[k]).slice(0, 120); } }
                            authDiag('connectThroughIonicDeepLink ' + JSON.stringify(copy));
                        } catch (err) {}
                        var r;
                        try {
                            r = origConnect.apply(this, arguments);
                        } catch (err) {
                            authDiag('connect THREW: ' + err);
                            throw err;
                        }
                        authDiag('connect returned');
                        return r;
                    };
                    authDiag('wrapped __webAuth.connectThroughIonicDeepLink');
                    clearInterval(iv);
                } else if (wrapTries > 600) {
                    authDiag('gave up wrapping __webAuth; typeof=' + typeof window.__webAuth);
                    clearInterval(iv);
                }
            }, 100);
        } catch (e) {
            authDiag('webAuth wrap error: ' + e);
        }

        try {
            if (window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function' && !window.IonicDeeplink.__authWrapped) {
                window.IonicDeeplink.__authWrapped = true;
                var origFire = window.IonicDeeplink.fire;
                window.IonicDeeplink.fire = function (data) {
                    try { authDiag('IonicDeeplink.fire url=' + String((data && data.url) || data).slice(0, 160)); } catch (e) {}
                    var r;
                    try {
                        r = origFire.apply(this, arguments);
                    } catch (e) {
                        authDiag('IonicDeeplink.fire THREW: ' + e);
                        throw e;
                    }
                    authDiag('IonicDeeplink.fire returned');
                    return r;
                };
                authDiag('wrapped IonicDeeplink.fire');
            } else {
                authDiag('IonicDeeplink.fire wrap skipped (ionic=' + typeof window.IonicDeeplink + ')');
            }
        } catch (e) {
            authDiag('fire wrap error: ' + e);
        }
    }, 0);

    // Guest exchange via host: the game's cross-site fetch cannot carry the
    // popup's SameSite=Lax aws-waf-token cookie, so for guest logins the host
    // performs the /token exchange and hands the raw token JSON back. Any
    // failure or 8s timeout falls back to the game's original in-page fetch.
    (function () {
        function installExchangeWrap() {
            var wa = window.__webAuth;
            if (!wa || typeof wa.requestWebAuthToken !== 'function' || wa.__exchangeWrapped) return false;
            wa.__exchangeWrapped = true;
            var orig = wa.requestWebAuthToken;
            wa.__exchangeWrappedOrig = orig;
            wa.requestWebAuthToken = function (code, cb) {
                var isGuest = false;
                try { isGuest = (localStorage.getItem('WEBAUTH_TYPE') === 'guest'); } catch (e) {}
                if (isGuest && window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
                    var pending = { code: code, cb: cb, done: false };
                    window.__webauthPending = pending;
                    pending.timer = setTimeout(function () {
                        if (pending.done) return;
                        pending.done = true;
                        window.__webauthPending = null;
                        authDiag('webauth_exchange host timeout; falling back to in-page fetch');
                        try { orig.call(wa, code, cb); } catch (e) { authDiag('fallback orig threw: ' + e); }
                    }, 8000);
                    try {
                        window.chrome.webview.postMessage({ type: 'webauth_exchange', code: code });
                        authDiag('requestWebAuthToken -> asked host for tokens (code len ' + String(code).length + ')');
                    } catch (e) {
                        clearTimeout(pending.timer);
                        pending.done = true;
                        window.__webauthPending = null;
                        return orig.apply(this, arguments);
                    }
                    return;
                }
                return orig.apply(this, arguments);
            };
            authDiag('wrapped requestWebAuthToken for guest host-exchange');
            return true;
        }
        var tries = 0;
        var iv = setInterval(function () {
            tries++;
            try {
                if (installExchangeWrap()) clearInterval(iv);
                else if (tries > 600) clearInterval(iv);
            } catch (e) {
                clearInterval(iv);
                authDiag('exchange wrap install error: ' + e);
            }
        }, 100);

        try {
            if (window.chrome && window.chrome.webview && window.chrome.webview.addEventListener) {
                window.chrome.webview.addEventListener('message', function (ev) {
                    try {
                        var msg = ev && ev.data;
                        if (!msg || msg.type !== 'webauth_tokens') return;
                        var pending = window.__webauthPending;
                        if (!pending || pending.done) { authDiag('webauth_tokens reply with no pending request'); return; }
                        if (pending.code !== msg.code) { authDiag('webauth_tokens code mismatch; ignoring'); return; }
                        pending.done = true;
                        clearTimeout(pending.timer);
                        window.__webauthPending = null;
                        if (msg.ok && msg.raw) {
                            try {
                                var r = JSON.parse(msg.raw);
                                authDiag('host tokens delivered: access_len=' + (r.access_token ? r.access_token.length : 0) + ' refresh_len=' + (r.refresh_token ? r.refresh_token.length : 0) + ' guest_creds=' + (r.guest_credentials ? 'yes' : 'no'));
                                pending.cb(null, r.access_token, r.refresh_token, r.guest_credentials);
                            } catch (e) {
                                authDiag('host tokens parse failed: ' + e + '; falling back');
                                try { window.__webAuth.__exchangeWrappedOrig.call(window.__webAuth, pending.code, pending.cb); } catch (e2) { authDiag('fallback threw: ' + e2); }
                            }
                        } else {
                            authDiag('host had no tokens; falling back to in-page fetch');
                            try { window.__webAuth.__exchangeWrappedOrig.call(window.__webAuth, pending.code, pending.cb); } catch (e) { authDiag('fallback threw: ' + e); }
                        }
                    } catch (e) {
                        try { authDiag('webauth_tokens handler error: ' + e); } catch (e2) {}
                    }
                });
                authDiag('installed chrome.webview message listener for webauth_tokens');
            }
        } catch (e) {
            authDiag('webauth_tokens listener install error: ' + e);
        }
    })();
})();
