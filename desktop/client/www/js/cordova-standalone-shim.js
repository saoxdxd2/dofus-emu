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

    window.appInfo = {
        version: '3.14.2',
        identifier: 'com.ankama.dofustouch',
        build: '3.14.2',
        versionCode: '3014002'
    };

    safeSetNav('appInfo', {
        version: '3.14.2',
        identifier: 'com.ankama.dofustouch',
        build: '3.14.2',
        versionCode: '3014002',
        getAppInfo: function (success, error) {
            if (typeof success === 'function') {
                success({
                    version: '3.14.2',
                    identifier: 'com.ankama.dofustouch',
                    build: '3.14.2',
                    versionCode: '3014002'
                });
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
        close: function () {}
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
})();
