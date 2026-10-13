/**
 * Stealth Mobile Environment & WebGL Disguise Shim - Dynamic Edition
 * Normalizes WebView DOM, WebGL rendering context, Navigator, Battery/Thermal dynamics,
 * Sensor micro-tremor (IMU), Touch/Cursor interactions (zero-hover), Network Information,
 * AudioContext, and Cordova APIs to appear as an authentic physical Samsung Galaxy A51
 * (ARM Mali-G72 MP3) mobile device with zero desktop cursor leaks.
 */
(function() {
    'use strict';

    // --- 0. Top-Level Idempotency Guard (VULN-NEW-04) ---
    if (typeof window !== 'undefined') {
        if (window.__DOFUS_DISGUISE_LOADED__) return;
        try {
            Object.defineProperty(window, '__DOFUS_DISGUISE_LOADED__', {
                value: true,
                writable: false,
                enumerable: false,
                configurable: false
            });
        } catch (e) {
            window.__DOFUS_DISGUISE_LOADED__ = true;
        }
    }

    // --- 1. Function toString Cloaking ---
    var nativeToString = Function.prototype.toString;
    var hookedFunctions = new WeakMap();

    function makeNative(fn, name) {
        if (typeof fn !== 'function') return fn;
        var fnName = name || fn.name || '';
        var methodObj = {
            [fnName](...args) {
                return fn.apply(this, args);
            }
        };
        var nativeFn = methodObj[fnName];
        try {
            Object.defineProperty(nativeFn, 'length', { value: fn.length, configurable: true });
        } catch (e) {}
        hookedFunctions.set(nativeFn, 'function ' + fnName + '() { [native code] }');
        return nativeFn;
    }

    var customToString = makeNative(function toString() {
        if (typeof this !== 'function') {
            return nativeToString.apply(this, arguments);
        }
        if (hookedFunctions.has(this)) {
            return hookedFunctions.get(this);
        }
        return nativeToString.apply(this, arguments);
    }, 'toString');
    Function.prototype.toString = customToString;

    // --- Intl.DateTimeFormat resolvedOptions Stealth Disguise (BUG-14) ---
    try {
        if (typeof Intl !== 'undefined' && Intl.DateTimeFormat && Intl.DateTimeFormat.prototype) {
            var origResolved = Intl.DateTimeFormat.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = makeNative(function resolvedOptions() {
                var res = origResolved.apply(this, arguments);
                try { res.timeZone = 'Europe/Paris'; } catch (e) {}
                return res;
            }, 'resolvedOptions');
        }
    } catch (e) {}

    // --- Clean Iframe Disguise Engine (Eliminates all clean-frame leaks) ---
    function disguiseFrameWindow(targetWin) {
        if (!targetWin || targetWin === window) return;
        try {
            if (targetWin.__DOFUS_FRAME_DISGUISE__) return;
            try {
                Object.defineProperty(targetWin, '__DOFUS_FRAME_DISGUISE__', {
                    value: true,
                    writable: false,
                    enumerable: false,
                    configurable: false
                });
            } catch (e) {
                targetWin.__DOFUS_FRAME_DISGUISE__ = true;
            }

            // 1. Function.prototype.toString cloaking
            if (targetWin.Function && targetWin.Function.prototype) {
                targetWin.Function.prototype.toString = customToString;
            }

            // 2. Navigator Properties on Frame's Navigator.prototype & Instance
            var tNav = targetWin.navigator;
            var tNavProto = (targetWin.Navigator && targetWin.Navigator.prototype) ? targetWin.Navigator.prototype : tNav;
            if (tNavProto) {
                var props = {
                    platform: MOBILE_PLATFORM,
                    userAgent: MOBILE_UA,
                    appVersion: MOBILE_APP_VERSION,
                    maxTouchPoints: 5,
                    hardwareConcurrency: 8,
                    deviceMemory: 4,
                    vendor: 'Google Inc.',
                    webdriver: false,
                    onLine: true
                };
                for (var k in props) {
                    (function(p, val) {
                        try { delete tNav[p]; } catch (e) {}
                        try {
                            Object.defineProperty(tNavProto, p, {
                                get: makeNative(function() { return val; }, 'get ' + p),
                                configurable: true,
                                enumerable: true
                            });
                        } catch (e) {
                            try { tNav[p] = val; } catch (e2) {}
                        }
                        if (tNav && tNav !== tNavProto) {
                            try {
                                Object.defineProperty(tNav, p, {
                                    get: makeNative(function() { return val; }, 'get ' + p),
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (e) {}
                        }
                    })(k, props[k]);
                }

                try {
                    tNavProto.vibrate = makeNative(function vibrate(pattern) { return true; }, 'vibrate');
                    if (tNav && tNav !== tNavProto) { tNav.vibrate = tNavProto.vibrate; }
                } catch (e) {}

                try {
                    if (typeof emptyPluginArray !== 'undefined') {
                        try { delete tNav.plugins; } catch (e) {}
                        Object.defineProperty(tNavProto, 'plugins', {
                            get: makeNative(function() { return emptyPluginArray; }, 'get plugins'),
                            configurable: true,
                            enumerable: true
                        });
                        if (tNav && tNav !== tNavProto) {
                            try {
                                Object.defineProperty(tNav, 'plugins', {
                                    get: makeNative(function() { return emptyPluginArray; }, 'get plugins'),
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (e) {}
                        }
                    }
                    if (typeof emptyMimeTypeArray !== 'undefined') {
                        try { delete tNav.mimeTypes; } catch (e) {}
                        Object.defineProperty(tNavProto, 'mimeTypes', {
                            get: makeNative(function() { return emptyMimeTypeArray; }, 'get mimeTypes'),
                            configurable: true,
                            enumerable: true
                        });
                        if (tNav && tNav !== tNavProto) {
                            try {
                                Object.defineProperty(tNav, 'mimeTypes', {
                                    get: makeNative(function() { return emptyMimeTypeArray; }, 'get mimeTypes'),
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (e) {}
                        }
                    }
                    if (typeof connectionObj !== 'undefined') {
                        try { delete tNav.connection; } catch (e) {}
                        Object.defineProperty(tNavProto, 'connection', {
                            get: makeNative(function() { return connectionObj; }, 'get connection'),
                            configurable: true,
                            enumerable: true
                        });
                        if (tNav && tNav !== tNavProto) {
                            try {
                                Object.defineProperty(tNav, 'connection', {
                                    get: makeNative(function() { return connectionObj; }, 'get connection'),
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (e) {}
                        }
                    }
                    if (typeof getBatteryMethod !== 'undefined') {
                        try { delete tNav.getBattery; } catch (e) {}
                        Object.defineProperty(tNavProto, 'getBattery', {
                            value: getBatteryMethod,
                            writable: true,
                            enumerable: true,
                            configurable: true
                        });
                        if (tNav && tNav !== tNavProto) {
                            try { tNav.getBattery = getBatteryMethod; } catch (e) {}
                        }
                    }
                    if (typeof fakeBattery !== 'undefined') {
                        try { delete tNav.battery; } catch (e) {}
                        Object.defineProperty(tNav, 'battery', {
                            get: makeNative(function() { return fakeBattery; }, 'get battery'),
                            configurable: true,
                            enumerable: true
                        });
                    }
                } catch (e) {}
            }

            // 3. Screen Properties on Frame's Screen.prototype & Instance
            var tScr = targetWin.screen;
            var tScrProto = (targetWin.Screen && targetWin.Screen.prototype) ? targetWin.Screen.prototype : tScr;
            if (tScrProto) {
                var sProps = {
                    width: 914,
                    height: 411,
                    availWidth: 914,
                    availHeight: 411,
                    colorDepth: 24,
                    pixelDepth: 24
                };
                for (var sk in sProps) {
                    (function(p, val) {
                        try { delete tScr[p]; } catch (e) {}
                        try {
                            Object.defineProperty(tScrProto, p, {
                                get: makeNative(function() { return val; }, 'get ' + p),
                                configurable: true,
                                enumerable: true
                            });
                        } catch (e) {
                            try { tScr[p] = val; } catch (e2) {}
                        }
                        if (tScr && tScr !== tScrProto) {
                            try {
                                Object.defineProperty(tScr, p, {
                                    get: makeNative(function() { return val; }, 'get ' + p),
                                    configurable: true,
                                    enumerable: true
                                });
                            } catch (e) {}
                        }
                    })(sk, sProps[sk]);
                }
                var ori = tScrProto.orientation || (tScr && tScr.orientation);
                if (ori) {
                    try {
                        Object.defineProperty(ori, 'type', {
                            get: makeNative(function() { return 'landscape-primary'; }, 'get type'),
                            enumerable: true,
                            configurable: true
                        });
                        Object.defineProperty(ori, 'angle', {
                            get: makeNative(function() { return 90; }, 'get angle'),
                            enumerable: true,
                            configurable: true
                        });
                    } catch (e) {}
                }
            }

            // 4. devicePixelRatio
            try {
                delete targetWin.devicePixelRatio;
                Object.defineProperty(targetWin, 'devicePixelRatio', {
                    get: makeNative(function() { return 2.625; }, 'get devicePixelRatio'),
                    configurable: true,
                    enumerable: true
                });
            } catch (e) {}

            // 5. PointerEvent
            if (targetWin.PointerEvent && targetWin.PointerEvent.prototype) {
                try {
                    delete targetWin.PointerEvent.prototype.pointerType;
                    Object.defineProperty(targetWin.PointerEvent.prototype, 'pointerType', {
                        get: makeNative(function() { return 'touch'; }, 'get pointerType'),
                        configurable: true,
                        enumerable: true
                    });
                } catch (e) {}
            }

            // 6. WebGL Context Interception in Frame
            if (typeof wrapContext === 'function') {
                if (targetWin.WebGLRenderingContext && targetWin.WebGLRenderingContext.prototype) {
                    wrapContext(targetWin.WebGLRenderingContext.prototype);
                }
                if (targetWin.WebGL2RenderingContext && targetWin.WebGL2RenderingContext.prototype) {
                    wrapContext(targetWin.WebGL2RenderingContext.prototype);
                }
            }

            // 7. RTCPeerConnection WebRTC candidate masking
            var framePeerConn = targetWin.RTCPeerConnection || targetWin.webkitRTCPeerConnection;
            if (framePeerConn && framePeerConn.prototype && typeof _fakeLocalIp !== 'undefined') {
                var origCO = framePeerConn.prototype.createOffer;
                if (origCO) {
                    framePeerConn.prototype.createOffer = makeNative(function(options) {
                        return origCO.apply(this, arguments).then(function(offer) {
                            if (offer && offer.sdp) {
                                offer.sdp = offer.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp);
                            }
                            return offer;
                        });
                    }, 'createOffer');
                }
            }

            // 8. Intl.DateTimeFormat resolvedOptions timeZone disguise (BUG-14)
            if (targetWin.Intl && targetWin.Intl.DateTimeFormat && targetWin.Intl.DateTimeFormat.prototype) {
                try {
                    var fOrigResolved = targetWin.Intl.DateTimeFormat.prototype.resolvedOptions;
                    targetWin.Intl.DateTimeFormat.prototype.resolvedOptions = makeNative(function resolvedOptions() {
                        var res = fOrigResolved.apply(this, arguments);
                        try { res.timeZone = 'Europe/Paris'; } catch (e) {}
                        return res;
                    }, 'resolvedOptions');
                } catch (e) {}
            }
        } catch (err) {}
    }

    try {
        if (typeof HTMLIFrameElement !== 'undefined' && HTMLIFrameElement.prototype) {
            var origIframeCWDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
            if (origIframeCWDesc && origIframeCWDesc.get) {
                var origCWGet = origIframeCWDesc.get;
                Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                    get: makeNative(function() {
                        var cw = origCWGet.apply(this, arguments);
                        if (cw) disguiseFrameWindow(cw);
                        return cw;
                    }, 'get contentWindow'),
                    set: origIframeCWDesc.set,
                    configurable: true,
                    enumerable: true
                });
            }

            var origIframeCDDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentDocument');
            if (origIframeCDDesc && origIframeCDDesc.get) {
                var origCDGet = origIframeCDDesc.get;
                Object.defineProperty(HTMLIFrameElement.prototype, 'contentDocument', {
                    get: makeNative(function() {
                        var cd = origCDGet.apply(this, arguments);
                        if (cd && cd.defaultView) disguiseFrameWindow(cd.defaultView);
                        return cd;
                    }, 'get contentDocument'),
                    set: origIframeCDDesc.set,
                    configurable: true,
                    enumerable: true
                });
            }
        }

        if (typeof Node !== 'undefined' && Node.prototype) {
            var origAppendChild = Node.prototype.appendChild;
            Node.prototype.appendChild = makeNative(function(child) {
                var res = origAppendChild.apply(this, arguments);
                if (child && (child.tagName === 'IFRAME' || child.tagName === 'FRAME')) {
                    try { if (child.contentWindow) disguiseFrameWindow(child.contentWindow); } catch (e) {}
                }
                return res;
            }, 'appendChild');

            var origInsertBefore = Node.prototype.insertBefore;
            Node.prototype.insertBefore = makeNative(function(newNode, refNode) {
                var res = origInsertBefore.apply(this, arguments);
                if (newNode && (newNode.tagName === 'IFRAME' || newNode.tagName === 'FRAME')) {
                    try { if (newNode.contentWindow) disguiseFrameWindow(newNode.contentWindow); } catch (e) {}
                }
                return res;
            }, 'insertBefore');
        }
    } catch (e) {}

    // Helper to safely redefine properties with native getter masking on Prototype (enforcing Web IDL brand checks)
    function defineProp(obj, prop, valueGetter) {
        try {
            var target = obj;
            var brandCheck = null;
            if (obj === navigator && typeof Navigator !== 'undefined' && Navigator.prototype) {
                target = Navigator.prototype;
                brandCheck = Navigator;
            } else if (typeof Screen !== 'undefined' && obj === window.screen && Screen.prototype) {
                target = Screen.prototype;
                brandCheck = Screen;
            } else if ((obj === window || obj === global) && typeof Window !== 'undefined' && Window.prototype) {
                target = Window.prototype;
                brandCheck = Window;
                try { delete obj[prop]; } catch (e) {}
            }
            Object.defineProperty(target, prop, {
                get: makeNative(function() {
                    if (brandCheck && !(this instanceof brandCheck)) {
                        throw new TypeError('Illegal invocation');
                    }
                    return typeof valueGetter === 'function' ? valueGetter() : valueGetter;
                }, 'get ' + prop),
                configurable: true,
                enumerable: true
            });
        } catch (e) {}
    }

    // --- 1.1 Transient Network Reload Dialog Silencing ---
    try {
        var winAlertProto = (typeof Window !== 'undefined' && Window.prototype) ? Window.prototype : window;
        var originalAlert = winAlertProto.alert || window.alert;
        var wrappedAlert = makeNative(function(msg) {
            if (msg && typeof msg === 'string') {
                var lower = msg.toLowerCase();
                if (lower.indexOf('server cannot be reached') !== -1 || lower.indexOf('check your internet') !== -1) {
                    return;
                }
            }
            if (originalAlert) { return originalAlert.apply(this, arguments); }
        }, 'alert');

        if (typeof Window !== 'undefined' && Window.prototype && Window.prototype.alert) {
            Window.prototype.alert = wrappedAlert;
        } else {
            window.alert = wrappedAlert;
        }

        document.addEventListener('deviceready', function() {
            if (navigator.notification && navigator.notification.alert) {
                var origCdvAlert = navigator.notification.alert;
                navigator.notification.alert = makeNative(function(msg, callback, title, buttonName) {
                    if (msg && typeof msg === 'string') {
                        var lower = msg.toLowerCase();
                        if (lower.indexOf('server cannot be reached') !== -1 || lower.indexOf('check your internet') !== -1) {
                            if (typeof callback === 'function') { callback(); }
                            return;
                        }
                    }
                    return origCdvAlert.apply(this, arguments);
                }, 'alert');
            }
        }, false);
    } catch (e) {}

    // --- 2. Navigator Properties ---
    var MOBILE_UA = (function() {
        var ua = '__DEVICE_UA__';
        return (ua === '__' + 'DEVICE_UA__') ? 'Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36' : ua;
    })();
    var MOBILE_APP_VERSION = MOBILE_UA.replace(/^Mozilla\//, '');
    var MOBILE_PLATFORM = 'Linux armv8l';

    defineProp(navigator, 'platform', MOBILE_PLATFORM);
    defineProp(navigator, 'userAgent', MOBILE_UA);
    defineProp(navigator, 'appVersion', MOBILE_APP_VERSION);
    defineProp(navigator, 'maxTouchPoints', 5);
    defineProp(navigator, 'hardwareConcurrency', 8);
    defineProp(navigator, 'deviceMemory', 4);
    defineProp(navigator, 'vendor', 'Google Inc.');
    defineProp(navigator, 'webdriver', false);
    try {
        if (typeof Navigator !== 'undefined' && Navigator.prototype) {
            Navigator.prototype.vibrate = makeNative(function vibrate(pattern) { return true; }, 'vibrate');
            if (typeof navigator !== 'undefined' && navigator !== Navigator.prototype) {
                try { navigator.vibrate = Navigator.prototype.vibrate; } catch (e) {}
            }
        } else if (typeof navigator !== 'undefined') {
            navigator.vibrate = makeNative(function vibrate(pattern) { return true; }, 'vibrate');
        }
    } catch (e) {}

    // Plugins & MimeTypes: empty on real mobile webviews (VULN-NEW-02)
    try {
        var protoPlugin = (typeof PluginArray !== 'undefined' && PluginArray.prototype) 
            ? Object.create(PluginArray.prototype) 
            : Object.create(Object.prototype);
        var protoMime = (typeof MimeTypeArray !== 'undefined' && MimeTypeArray.prototype) 
            ? Object.create(MimeTypeArray.prototype) 
            : Object.create(Object.prototype);

        var emptyPluginArray = protoPlugin;
        Object.defineProperty(emptyPluginArray, 'length', { value: 0, writable: false, enumerable: false, configurable: true });
        emptyPluginArray.item = makeNative(function item(index) { return null; }, 'item');
        emptyPluginArray.namedItem = makeNative(function namedItem(name) { return null; }, 'namedItem');
        emptyPluginArray.refresh = makeNative(function refresh() {}, 'refresh');
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
            try {
                Object.defineProperty(emptyPluginArray, Symbol.toStringTag, { value: 'PluginArray', configurable: true });
            } catch (e) {}
        }

        var emptyMimeTypeArray = protoMime;
        Object.defineProperty(emptyMimeTypeArray, 'length', { value: 0, writable: false, enumerable: false, configurable: true });
        emptyMimeTypeArray.item = makeNative(function item(index) { return null; }, 'item');
        emptyMimeTypeArray.namedItem = makeNative(function namedItem(name) { return null; }, 'namedItem');
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
            try {
                Object.defineProperty(emptyMimeTypeArray, Symbol.toStringTag, { value: 'MimeTypeArray', configurable: true });
            } catch (e) {}
        }

        defineProp(navigator, 'plugins', emptyPluginArray);
        defineProp(navigator, 'mimeTypes', emptyMimeTypeArray);
    } catch (e) {}

    // Network Information API (Dynamic 4G LTE cellular connection) (VULN-NEW-03)
    var connProto = (typeof NetworkInformation !== 'undefined' && NetworkInformation.prototype)
        ? Object.create(NetworkInformation.prototype)
        : ((typeof EventTarget !== 'undefined' && EventTarget.prototype)
            ? Object.create(EventTarget.prototype)
            : Object.create(Object.prototype));

    var connectionObj = connProto;
    connectionObj.effectiveType = '4g';
    connectionObj.type = 'cellular';
    connectionObj.saveData = false;
    connectionObj.onchange = null;
    if (!('addEventListener' in connectionObj)) {
        connectionObj.addEventListener = makeNative(function() {}, 'addEventListener');
        connectionObj.removeEventListener = makeNative(function() {}, 'removeEventListener');
        connectionObj.dispatchEvent = makeNative(function() { return false; }, 'dispatchEvent');
    }
    if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
        try {
            Object.defineProperty(connectionObj, Symbol.toStringTag, { value: 'NetworkInformation', configurable: true });
        } catch (e) {}
    }
    Object.defineProperty(connectionObj, 'rtt', {
        get: makeNative(function() { return 50 + Math.floor((Math.random() - 0.5) * 8); }, 'get rtt'),
        enumerable: true
    });
    Object.defineProperty(connectionObj, 'downlink', {
        get: makeNative(function() { return +(15.2 + (Math.random() - 0.5) * 1.5).toFixed(1); }, 'get downlink'),
        enumerable: true
    });
    defineProp(navigator, 'connection', connectionObj);
    defineProp(navigator, 'mozConnection', connectionObj);
    defineProp(navigator, 'webkitConnection', connectionObj);

    // Guaranteed Online State & Suppression of False Offline Triggers
    defineProp(navigator, 'onLine', true);
    var suppressOffline = function(e) {
        if (e && (e.type === 'offline' || e.type === 'error')) {
            e.stopImmediatePropagation();
            e.preventDefault();
        }
    };
    try {
        window.addEventListener('offline', suppressOffline, true);
        document.addEventListener('offline', suppressOffline, true);
    } catch (e) {}

    var _deviceModel = (function() {
        var m = '__DEVICE_MODEL__';
        return (m === '__' + 'DEVICE_MODEL__') ? 'SM-A515F' : m;
    })();
    var _deviceManufacturer = (function() {
        var m = '__DEVICE_MANUFACTURER__';
        return (m === '__' + 'DEVICE_MANUFACTURER__') ? 'samsung' : m;
    })();

    // Client Hints (User-Agent Data)
    if (navigator.userAgentData || window.NavigatorUAData) {
        var _osVerMatch = MOBILE_UA.match(/Android\s+([0-9]+)/);
        var _chromeVerMatch = MOBILE_UA.match(/Chrome\/([0-9.]+)/);
        var _majorVer = _chromeVerMatch ? _chromeVerMatch[1].split('.')[0] : '83';
        var _fullVer = _chromeVerMatch ? _chromeVerMatch[1] : '83.0.4103.106';
        var _osVer = _osVerMatch ? _osVerMatch[1] : '10';

        var uaData = {
            brands: [
                { brand: 'Android WebView', version: _majorVer },
                { brand: 'Chromium', version: _majorVer },
                { brand: 'Not(A:Brand', version: '99' }
            ],
            mobile: true,
            platform: 'Android',
            getHighEntropyValues: makeNative(function(hints) {
                return Promise.resolve({
                    architecture: 'arm',
                    bitness: '64',
                    brands: uaData.brands,
                    formFactor: 'mobile',
                    mobile: true,
                    model: _deviceModel,
                    platform: 'Android',
                    platformVersion: _osVer,
                    uaFullVersion: _fullVer
                });
            }, 'getHighEntropyValues'),
            toJSON: makeNative(function() {
                return {
                    brands: uaData.brands,
                    mobile: true,
                    platform: 'Android'
                };
            }, 'toJSON')
        };
        try {
            var targetUaData = (typeof Navigator !== 'undefined' && Navigator.prototype) ? Navigator.prototype : navigator;
            Object.defineProperty(targetUaData, 'userAgentData', {
                get: makeNative(function() { return uaData; }, 'get userAgentData'),
                configurable: true,
                enumerable: true
            });
        } catch (e) {}
    }

    // --- 3. WebGL & WebGL2 Interception ---
    var GL_VENDOR = (function() {
        var v = '__DEVICE_GL_VENDOR__';
        return (v === '__' + 'DEVICE_GL_VENDOR__') ? 'ARM' : v;
    })();
    var GL_RENDERER = (function() {
        var r = '__DEVICE_GL_RENDERER__';
        return (r === '__' + 'DEVICE_GL_RENDERER__') ? 'Mali-G72 MP3' : r;
    })();
    var MAX_TEX_SIZE = (function() {
        var s = '__DEVICE_MAX_TEXTURE_SIZE__';
        var n = parseInt(s, 10);
        return isNaN(n) ? 8192 : n;
    })();

    var UNMASKED_VENDOR_WEBGL = 0x9245;
    var UNMASKED_RENDERER_WEBGL = 0x9246;

    function wrapContext(ctxProto) {
        if (!ctxProto) return;
        var origGetParameter = ctxProto.getParameter;
        var origGetExtension = ctxProto.getExtension;
        var origGetSupportedExtensions = ctxProto.getSupportedExtensions;
        var origGetShaderPrecisionFormat = ctxProto.getShaderPrecisionFormat;

        ctxProto.getParameter = makeNative(function(param) {
            // UNMASKED_VENDOR_WEBGL (0x9245)
            if (param === UNMASKED_VENDOR_WEBGL) return GL_VENDOR;
            // UNMASKED_RENDERER_WEBGL (0x9246)
            if (param === UNMASKED_RENDERER_WEBGL) return GL_RENDERER;
            // 0x1F00 (VENDOR)
            if (param === 0x1F00) return GL_VENDOR;
            // 0x1F01 (RENDERER)
            if (param === 0x1F01) return GL_RENDERER;
            // 0x1F02 (VERSION) - Scrub desktop translator leaks and match Chromium on Mali (VULN-02)
            if (param === 0x1F02) {
                var isGL2 = (typeof WebGL2RenderingContext !== 'undefined' && this instanceof WebGL2RenderingContext);
                return isGL2 ? 'WebGL 2.0 (OpenGL ES 3.0 Chromium)' : 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
            }
            // 0x8B8C (SHADING_LANGUAGE_VERSION) - Scrub desktop driver leaks (VULN-02)
            if (param === 0x8B8C) {
                var isGL2Sh = (typeof WebGL2RenderingContext !== 'undefined' && this instanceof WebGL2RenderingContext);
                return isGL2Sh ? 'WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)' : 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
            }
            // MAX_TEXTURE_SIZE (0x0D33)
            if (param === 0x0D33) return MAX_TEX_SIZE;
            // MAX_CUBE_MAP_TEXTURE_SIZE (0x851C)
            if (param === 0x851C) return MAX_TEX_SIZE;
            // MAX_RENDERBUFFER_SIZE (0x84E8)
            if (param === 0x84E8) return MAX_TEX_SIZE;
            // MAX_VIEWPORT_DIMS (0x0D3A)
            if (param === 0x0D3A) return new Int32Array([MAX_TEX_SIZE, MAX_TEX_SIZE]);
            // ALIASED_LINE_WIDTH_RANGE (0x846E)
            if (param === 0x846E) return new Float32Array([1, 7]);
            // ALIASED_POINT_SIZE_RANGE (0x846D)
            if (param === 0x846D) return new Float32Array([1, 1024]);
            // MAX_VERTEX_ATTRIBS (0x8869)
            if (param === 0x8869) return 16;
            // MAX_VERTEX_UNIFORM_VECTORS (0x8DFB)
            if (param === 0x8DFB) return 1024;
            // MAX_VARYING_VECTORS (0x8DFC)
            if (param === 0x8DFC) return 30;
            // MAX_COMBINED_TEXTURE_IMAGE_UNITS (0x8B4D)
            if (param === 0x8B4D) return 96;
            // MAX_VERTEX_TEXTURE_IMAGE_UNITS (0x8B4C)
            if (param === 0x8B4C) return 16;
            // MAX_TEXTURE_IMAGE_UNITS (0x8872)
            if (param === 0x8872) return 16;
            // MAX_FRAGMENT_UNIFORM_VECTORS (0x8DFD)
            if (param === 0x8DFD) return 1024;

            return origGetParameter.apply(this, arguments);
        }, 'getParameter');

        if (origGetExtension) {
            ctxProto.getExtension = makeNative(function(name) {
                if (name === 'WEBGL_debug_renderer_info') {
                    return {
                        UNMASKED_VENDOR_WEBGL: UNMASKED_VENDOR_WEBGL,
                        UNMASKED_RENDERER_WEBGL: UNMASKED_RENDERER_WEBGL
                    };
                }
                if (name === 'WEBGL_compressed_texture_astc') {
                    var nativeAstc = origGetExtension.apply(this, arguments);
                    if (nativeAstc) return nativeAstc;
                    return {
                        COMPRESSED_RGBA_ASTC_4x4_KHR: 0x93B0,
                        COMPRESSED_RGBA_ASTC_5x4_KHR: 0x93B1,
                        COMPRESSED_RGBA_ASTC_5x5_KHR: 0x93B2,
                        COMPRESSED_RGBA_ASTC_6x5_KHR: 0x93B3,
                        COMPRESSED_RGBA_ASTC_6x6_KHR: 0x93B4,
                        COMPRESSED_RGBA_ASTC_8x5_KHR: 0x93B5,
                        COMPRESSED_RGBA_ASTC_8x6_KHR: 0x93B6,
                        COMPRESSED_RGBA_ASTC_8x8_KHR: 0x93B7,
                        COMPRESSED_RGBA_ASTC_10x5_KHR: 0x93B8,
                        COMPRESSED_RGBA_ASTC_10x6_KHR: 0x93B9,
                        COMPRESSED_RGBA_ASTC_10x8_KHR: 0x93BA,
                        COMPRESSED_RGBA_ASTC_10x10_KHR: 0x93BB,
                        COMPRESSED_RGBA_ASTC_12x10_KHR: 0x93BC,
                        COMPRESSED_RGBA_ASTC_12x12_KHR: 0x93BD,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_4x4_KHR: 0x93D0,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_5x4_KHR: 0x93D1,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_5x5_KHR: 0x93D2,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_6x5_KHR: 0x93D3,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_6x6_KHR: 0x93D4,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_8x5_KHR: 0x93D5,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_8x6_KHR: 0x93D6,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_8x8_KHR: 0x93D7,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_10x5_KHR: 0x93D8,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_10x6_KHR: 0x93D9,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_10x8_KHR: 0x93DA,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_10x10_KHR: 0x93DB,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_12x10_KHR: 0x93DC,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_12x12_KHR: 0x93DD,
                        getSupportedProfiles: makeNative(function() { return ['ldr']; }, 'getSupportedProfiles')
                    };
                }
                if (name === 'WEBGL_compressed_texture_etc' || name === 'WEBGL_compressed_texture_etc1') {
                    return origGetExtension.apply(this, arguments);
                }
                // Mask ALL desktop-only texture formats (S3TC, BPTC, RGTC)
                var lower = (name || '').toLowerCase();
                if (lower.indexOf('s3tc') !== -1 || lower.indexOf('bptc') !== -1 || lower.indexOf('rgtc') !== -1 || lower === 'webgl_polygon_mode') {
                    return null;
                }
                return origGetExtension.apply(this, arguments);
            }, 'getExtension');
        }

        if (origGetSupportedExtensions) {
            ctxProto.getSupportedExtensions = makeNative(function() {
                var list = origGetSupportedExtensions.apply(this, arguments) || [];
                var arr = Array.from(list).filter(function(ext) {
                    var lower = (ext || '').toLowerCase();
                    return lower.indexOf('s3tc') === -1 && lower.indexOf('bptc') === -1 && lower.indexOf('rgtc') === -1 && lower !== 'webgl_polygon_mode';
                });
                if (arr.indexOf('WEBGL_debug_renderer_info') === -1) arr.push('WEBGL_debug_renderer_info');
                if (arr.indexOf('KHR_parallel_shader_compile') === -1) arr.push('KHR_parallel_shader_compile');
                if (arr.indexOf('WEBGL_compressed_texture_astc') === -1) arr.push('WEBGL_compressed_texture_astc');
                return arr;
            }, 'getSupportedExtensions');
        }

        if (origGetShaderPrecisionFormat) {
            ctxProto.getShaderPrecisionFormat = makeNative(function(shaderType, precisionType) {
                // Per WebGL spec: return null if precisionType or shaderType is invalid (BUG-18)
                if (precisionType !== 0x8DF0 && precisionType !== 0x8DF1 && precisionType !== 0x8DF2 &&
                    precisionType !== 0x8DF3 && precisionType !== 0x8DF4 && precisionType !== 0x8DF5) {
                    return null;
                }
                if (shaderType !== 0x8B30 && shaderType !== 0x8B31) {
                    return null;
                }

                // Canonical ARM Mali-G72 MP3 precision format lookup
                // LOW_FLOAT (0x8DF0), MEDIUM_FLOAT (0x8DF1), HIGH_FLOAT (0x8DF2)
                // LOW_INT (0x8DF3), MEDIUM_INT (0x8DF4), HIGH_INT (0x8DF5)
                var rMin = 127, rMax = 127, prec = 23;
                if (precisionType === 0x8DF2) { // HIGH_FLOAT
                    rMin = 127; rMax = 127; prec = 23;
                } else if (precisionType === 0x8DF1 || precisionType === 0x8DF0) { // MEDIUM_FLOAT or LOW_FLOAT
                    rMin = 15; rMax = 15; prec = 10;
                } else if (precisionType === 0x8DF5) { // HIGH_INT
                    rMin = 31; rMax = 30; prec = 0;
                } else if (precisionType === 0x8DF4) { // MEDIUM_INT
                    rMin = 15; rMax = 14; prec = 0;
                } else if (precisionType === 0x8DF3) { // LOW_INT
                    rMin = 7; rMax = 6; prec = 0;
                }

                var nativeFmt = null;
                try {
                    nativeFmt = origGetShaderPrecisionFormat.call(this, shaderType, precisionType);
                } catch (e) {}

                var target = nativeFmt;
                if (!target) {
                    var proto = (typeof WebGLShaderPrecisionFormat !== 'undefined' && WebGLShaderPrecisionFormat.prototype) ?
                        WebGLShaderPrecisionFormat.prototype : Object.prototype;
                    target = Object.create(proto);
                }

                try {
                    Object.defineProperties(target, {
                        rangeMin: { value: rMin, configurable: true, enumerable: true },
                        rangeMax: { value: rMax, configurable: true, enumerable: true },
                        precision: { value: prec, configurable: true, enumerable: true }
                    });
                } catch (e) {}
                return target;
            }, 'getShaderPrecisionFormat');
        }
    }

    if (window.WebGLRenderingContext) {
        wrapContext(WebGLRenderingContext.prototype);
    }
    if (window.WebGL2RenderingContext) {
        wrapContext(WebGL2RenderingContext.prototype);
    }
    if (typeof OffscreenCanvas !== 'undefined' && OffscreenCanvas.prototype.getContext) {
        var origOffscreenGetContext = OffscreenCanvas.prototype.getContext;
        OffscreenCanvas.prototype.getContext = makeNative(function(type) {
            var ctx = origOffscreenGetContext.apply(this, arguments);
            if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {
                wrapContext(ctx.__proto__);
            }
            return ctx;
        }, 'getContext');
    }

    // --- 4. Dynamic Battery & Thermal API Lifecycle ---
    var batteryListeners = { levelchange: [], chargingchange: [] };
    var battEventHandlers = {
        chargingchange: null,
        chargingtimechange: null,
        dischargingtimechange: null,
        levelchange: null
    };
    var currentBattLevel = 0.85;
    var currentBattTemp = 28.5;
    var battStartTime = Date.now();

    var battProto;
    if (typeof BatteryManager !== 'undefined' && BatteryManager.prototype) {
        battProto = BatteryManager.prototype;
    } else {
        var baseProto = (typeof EventTarget !== 'undefined' && EventTarget.prototype) ?
            EventTarget.prototype : Object.prototype;
        battProto = Object.create(baseProto);
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
            try {
                Object.defineProperty(battProto, Symbol.toStringTag, {
                    value: 'BatteryManager',
                    configurable: true
                });
            } catch (e) {}
        }
    }

    // Define getters on prototype (W3C specification conformant, VULN-04)
    function defineBattGetter(prop, getterFn) {
        Object.defineProperty(battProto, prop, {
            get: makeNative(function() {
                if (typeof BatteryManager !== 'undefined' && !(this instanceof BatteryManager) && this !== fakeBattery) {
                    throw new TypeError('Illegal invocation');
                }
                return getterFn();
            }, 'get ' + prop),
            set: undefined,
            enumerable: true,
            configurable: true
        });
    }

    function defineBattHandler(prop) {
        var evType = prop.slice(2); // 'onlevelchange' -> 'levelchange'
        Object.defineProperty(battProto, prop, {
            get: makeNative(function() {
                if (typeof BatteryManager !== 'undefined' && !(this instanceof BatteryManager) && this !== fakeBattery) {
                    throw new TypeError('Illegal invocation');
                }
                return battEventHandlers[evType] || null;
            }, 'get ' + prop),
            set: makeNative(function(fn) {
                if (typeof BatteryManager !== 'undefined' && !(this instanceof BatteryManager) && this !== fakeBattery) {
                    throw new TypeError('Illegal invocation');
                }
                battEventHandlers[evType] = typeof fn === 'function' ? fn : null;
            }, 'set ' + prop),
            enumerable: true,
            configurable: true
        });
    }

    defineBattGetter('charging', function() { return false; });
    defineBattGetter('chargingTime', function() { return Infinity; });
    defineBattGetter('dischargingTime', function() { return Math.round(currentBattLevel * 4 * 3600); });
    defineBattGetter('level', function() { return currentBattLevel; });

    defineBattHandler('onchargingchange');
    defineBattHandler('onchargingtimechange');
    defineBattHandler('ondischargingtimechange');
    defineBattHandler('onlevelchange');

    var origEventTargetDispatch = (typeof EventTarget !== 'undefined' && EventTarget.prototype.dispatchEvent)
        ? EventTarget.prototype.dispatchEvent
        : null;
    var origEventTargetAdd = (typeof EventTarget !== 'undefined' && EventTarget.prototype.addEventListener)
        ? EventTarget.prototype.addEventListener
        : null;
    var origEventTargetRemove = (typeof EventTarget !== 'undefined' && EventTarget.prototype.removeEventListener)
        ? EventTarget.prototype.removeEventListener
        : null;

    if (!battProto.addEventListener || battProto === Object.prototype) {
        battProto.addEventListener = makeNative(function(type, fn, opts) {
            if (batteryListeners[type]) batteryListeners[type].push(fn);
            if (origEventTargetAdd) {
                try { origEventTargetAdd.apply(this, arguments); } catch (e) {}
            }
        }, 'addEventListener');
        battProto.removeEventListener = makeNative(function(type, fn, opts) {
            if (batteryListeners[type]) {
                var idx = batteryListeners[type].indexOf(fn);
                if (idx !== -1) batteryListeners[type].splice(idx, 1);
            }
            if (origEventTargetRemove) {
                try { origEventTargetRemove.apply(this, arguments); } catch (e) {}
            }
        }, 'removeEventListener');
    }

    battProto.dispatchEvent = makeNative(function(evt) {
        var type = evt ? evt.type : '';
        if (battEventHandlers[type]) {
            try { battEventHandlers[type].call(this, evt); } catch (e) {}
        }
        if (batteryListeners[type]) {
            for (var i = 0; i < batteryListeners[type].length; i++) {
                try { batteryListeners[type][i].call(this, evt); } catch (e) {}
            }
        }
        if (origEventTargetDispatch) {
            try { origEventTargetDispatch.call(this, evt); } catch (e) {}
        }
        return true;
    }, 'dispatchEvent');

    var fakeBattery = Object.create(battProto);

    // Dynamic battery discharge and thermal progression over time (every 10s)
    setInterval(function() {
        var elapsedSec = (Date.now() - battStartTime) / 1000;
        // Slow discharge: realistic gaming load
        var newLevel = Math.max(0.15, +(0.85 - (elapsedSec / 60) * 0.00015).toFixed(4));
        if (newLevel !== currentBattLevel) {
            currentBattLevel = newLevel;
            fakeBattery.dispatchEvent(new Event('levelchange'));

            // Cordova & standard DOM batterystatus event
            var battEvent = new CustomEvent('batterystatus', {
                detail: { level: Math.round(currentBattLevel * 100), isPlugged: false }
            });
            if (window.dispatchEvent) window.dispatchEvent(battEvent);
            if (typeof document !== 'undefined' && document && document.dispatchEvent) document.dispatchEvent(battEvent);
            if (window.cordova && typeof window.cordova.fireWindowEvent === 'function') {
                window.cordova.fireWindowEvent('batterystatus', {
                    level: Math.round(currentBattLevel * 100),
                    isPlugged: false
                });
            }
        }
        // Thermal curve: realistic oscillation between 28.4 and 29.6 C
        currentBattTemp = 28.5 + 0.6 * Math.sin(elapsedSec / 60) + (Math.random() - 0.5) * 0.08;
    }, 10000);

    var getBatteryMethod = makeNative(function getBattery() {
        if (typeof Navigator !== 'undefined' && !(this instanceof Navigator) && (typeof navigator === 'undefined' || this !== navigator)) {
            throw new TypeError('Illegal invocation');
        }
        return Promise.resolve(fakeBattery);
    }, 'getBattery');

    if (typeof Navigator !== 'undefined' && Navigator.prototype) {
        try {
            Object.defineProperty(Navigator.prototype, 'getBattery', {
                value: getBatteryMethod,
                writable: true,
                enumerable: true,
                configurable: true
            });
            if (typeof navigator !== 'undefined' && navigator !== Navigator.prototype) {
                try { navigator.getBattery = getBatteryMethod; } catch (e) {}
            }
        } catch (e) {
            defineProp(navigator, 'getBattery', function() { return getBatteryMethod; });
        }
    } else if (typeof navigator !== 'undefined') {
        navigator.getBattery = getBatteryMethod;
    }
    defineProp(navigator, 'battery', fakeBattery);

    // --- 5. Touch Events & Capacitive Dynamics ---
    try {
        if (!('ontouchstart' in window)) {
            window.ontouchstart = null;
            window.ontouchend = null;
            window.ontouchmove = null;
            window.ontouchcancel = null;
        }

        // Dynamic finger contact patch generator with WeakMap idempotency caching
        var eventContactCache = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;

        function getEventContact(ev) {
            if (eventContactCache && ev && typeof ev === 'object') {
                var cached = eventContactCache.get(ev);
                if (cached) return cached;
            }
            var bm = (typeof getBehaviorModel === 'function') ? getBehaviorModel() : null;
            var fatigue = bm ? bm.getFatigueFactor() : 0.0;
            var scatter = (bm && bm.currentState && bm.currentState.spatialScatter) ? bm.currentState.spatialScatter : 5.0;
            var r = 16 + Math.sin(Date.now() / 60) * 3 + (Math.random() - 0.5) * (scatter * 0.4);
            var p = 0.62 + (Math.random() - 0.5) * 0.12 - (fatigue * 0.04);
            var result = {
                radius: Math.max(12, Math.min(24, Math.round(r))),
                pressure: +Math.max(0.3, Math.min(0.95, p)).toFixed(2)
            };
            if (eventContactCache && ev && typeof ev === 'object') {
                eventContactCache.set(ev, result);
            }
            return result;
        }

        if (window.PointerEvent) {
            try {
                Object.defineProperty(PointerEvent.prototype, 'pointerType', {
                    get: makeNative(function() {
                        if (!(this instanceof PointerEvent)) throw new TypeError('Illegal invocation');
                        return 'touch';
                    }, 'get pointerType'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'width', {
                    get: makeNative(function() {
                        if (!(this instanceof PointerEvent)) throw new TypeError('Illegal invocation');
                        if (this.buttons === 0 && this.type !== 'pointerdown') return 1;
                        return getEventContact(this).radius * 2;
                    }, 'get width'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'height', {
                    get: makeNative(function() {
                        if (!(this instanceof PointerEvent)) throw new TypeError('Illegal invocation');
                        if (this.buttons === 0 && this.type !== 'pointerdown') return 1;
                        return getEventContact(this).radius * 2;
                    }, 'get height'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'pressure', {
                    get: makeNative(function() {
                        if (!(this instanceof PointerEvent)) throw new TypeError('Illegal invocation');
                        if (this.type === 'pointerup' || this.type === 'pointercancel') return 0;
                        if (this.buttons === 0 && this.type !== 'pointerdown') return 0;
                        return getEventContact(this).pressure;
                    }, 'get pressure'),
                    configurable: true,
                    enumerable: true
                });
            } catch (err) {}
        }

        if (typeof Touch !== 'undefined' && Touch.prototype) {
            try {
                Object.defineProperty(Touch.prototype, 'radiusX', {
                    get: makeNative(function() {
                        if (!(this instanceof Touch)) throw new TypeError('Illegal invocation');
                        return getEventContact(this).radius;
                    }, 'get radiusX'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(Touch.prototype, 'radiusY', {
                    get: makeNative(function() {
                        if (!(this instanceof Touch)) throw new TypeError('Illegal invocation');
                        return getEventContact(this).radius + 1;
                    }, 'get radiusY'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(Touch.prototype, 'force', {
                    get: makeNative(function() {
                        if (!(this instanceof Touch)) throw new TypeError('Illegal invocation');
                        return getEventContact(this).pressure;
                    }, 'get force'),
                    configurable: true,
                    enumerable: true
                });
            } catch (err) {}
        }

        // Touch Media Queries
        if (window.matchMedia) {
            var origMatchMedia = window.matchMedia;
            window.matchMedia = makeNative(function(query) {
                var q = query.toLowerCase();
                if (q.indexOf('hover: none') !== -1 || q.indexOf('any-hover: none') !== -1 ||
                    q.indexOf('pointer: coarse') !== -1 || q.indexOf('any-pointer: coarse') !== -1) {
                    return {
                        matches: true,
                        media: query,
                        onchange: null,
                        addListener: function() {},
                        removeListener: function() {},
                        addEventListener: function() {},
                        removeEventListener: function() {},
                        dispatchEvent: function() { return false; }
                    };
                }
                if (q.indexOf('hover: hover') !== -1 || q.indexOf('any-hover: hover') !== -1 ||
                    q.indexOf('pointer: fine') !== -1 || q.indexOf('any-pointer: fine') !== -1) {
                    return {
                        matches: false,
                        media: query,
                        onchange: null,
                        addListener: function() {},
                        removeListener: function() {},
                        addEventListener: function() {},
                        removeEventListener: function() {},
                        dispatchEvent: function() { return false; }
                    };
                }
                return origMatchMedia.apply(this, arguments);
            }, 'matchMedia');
        }
    } catch (e) {}

    // --- 6. Sensor Dynamics (Handheld IMU Micro-Tremor Emulation) ---
    try {
        var motionListeners = [];
        var orientationListeners = [];
        var onDeviceMotionHandler = null;
        var onDeviceOrientationHandler = null;

        var targetProto = (typeof EventTarget !== 'undefined' && EventTarget.prototype) ? EventTarget.prototype : window;
        var origAddEventListener = targetProto.addEventListener || window.addEventListener;
        var origRemoveEventListener = targetProto.removeEventListener || window.removeEventListener;

        var wrappedAddEventListener = makeNative(function(type, listener, options) {
            if (this === window || (typeof Window !== 'undefined' && this instanceof Window)) {
                if (type === 'devicemotion' && listener && motionListeners.indexOf(listener) === -1) motionListeners.push(listener);
                if (type === 'deviceorientation' && listener && orientationListeners.indexOf(listener) === -1) orientationListeners.push(listener);
            }
            if (origAddEventListener) {
                return origAddEventListener.apply(this, arguments);
            }
        }, 'addEventListener');

        var wrappedRemoveEventListener = makeNative(function(type, listener, options) {
            if (this === window || (typeof Window !== 'undefined' && this instanceof Window)) {
                if (type === 'devicemotion') {
                    var idx = motionListeners.indexOf(listener);
                    if (idx !== -1) motionListeners.splice(idx, 1);
                }
                if (type === 'deviceorientation') {
                    var idxO = orientationListeners.indexOf(listener);
                    if (idxO !== -1) orientationListeners.splice(idxO, 1);
                }
            }
            if (origRemoveEventListener) {
                return origRemoveEventListener.apply(this, arguments);
            }
        }, 'removeEventListener');

        if (typeof EventTarget !== 'undefined' && EventTarget.prototype) {
            EventTarget.prototype.addEventListener = wrappedAddEventListener;
            EventTarget.prototype.removeEventListener = wrappedRemoveEventListener;
        } else {
            window.addEventListener = wrappedAddEventListener;
            window.removeEventListener = wrappedRemoveEventListener;
        }

        // Support inline ondevicemotion and ondeviceorientation without polluting window own-properties (BUG-30)
        var winProto = (typeof Window !== 'undefined' && Window.prototype) ? Window.prototype : window;
        Object.defineProperty(winProto, 'ondevicemotion', {
            get: makeNative(function() { return onDeviceMotionHandler; }, 'get ondevicemotion'),
            set: makeNative(function(fn) { onDeviceMotionHandler = fn; }, 'set ondevicemotion'),
            configurable: true,
            enumerable: true
        });
        Object.defineProperty(winProto, 'ondeviceorientation', {
            get: makeNative(function() { return onDeviceOrientationHandler; }, 'get ondeviceorientation'),
            set: makeNative(function(fn) { onDeviceOrientationHandler = fn; }, 'set ondeviceorientation'),
            configurable: true,
            enumerable: true
        });

        if (window.DeviceMotionEvent && !window.DeviceMotionEvent.requestPermission) {
            window.DeviceMotionEvent.requestPermission = makeNative(function() {
                return Promise.resolve('granted');
            }, 'requestPermission');
        }
        if (window.DeviceOrientationEvent && !window.DeviceOrientationEvent.requestPermission) {
            window.DeviceOrientationEvent.requestPermission = makeNative(function() {
                return Promise.resolve('granted');
            }, 'requestPermission');
        }

        function createDeviceMotionEvent(acc, accG, rot, interval) {
            var evt = null;
            if (typeof DeviceMotionEvent === 'function') {
                try {
                    evt = new DeviceMotionEvent('devicemotion', {
                        acceleration: acc,
                        accelerationIncludingGravity: accG,
                        rotationRate: rot,
                        interval: interval
                    });
                } catch (e) {}
            }
            if (!evt) {
                try {
                    evt = document.createEvent('DeviceMotionEvent');
                    evt.initDeviceMotionEvent('devicemotion', false, false, acc, accG, rot, interval);
                } catch (e) {}
            }
            if (!evt) {
                evt = new Event('devicemotion');
            }
            try {
                Object.defineProperty(evt, 'acceleration', { value: acc, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'accelerationIncludingGravity', { value: accG, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'rotationRate', { value: rot, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'interval', { value: interval, configurable: true, enumerable: true });
            } catch (e) {}
            return evt;
        }

        function createDeviceOrientationEvent(alpha, beta, gamma, absolute) {
            var evt = null;
            if (typeof DeviceOrientationEvent === 'function') {
                try {
                    evt = new DeviceOrientationEvent('deviceorientation', {
                        alpha: alpha,
                        beta: beta,
                        gamma: gamma,
                        absolute: absolute
                    });
                } catch (e) {}
            }
            if (!evt) {
                try {
                    evt = document.createEvent('DeviceOrientationEvent');
                    evt.initDeviceOrientationEvent('deviceorientation', false, false, alpha, beta, gamma, absolute);
                } catch (e) {}
            }
            if (!evt) {
                evt = new Event('deviceorientation');
            }
            try {
                Object.defineProperty(evt, 'alpha', { value: alpha, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'beta', { value: beta, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'gamma', { value: gamma, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'absolute', { value: absolute, configurable: true, enumerable: true });
            } catch (e) {}
            return evt;
        }

        // 20Hz Handheld Sensor Loop: dispatches authentic IMU noise (SPOOF-02)
        setInterval(function() {
            var hasMotion = motionListeners.length > 0 || typeof onDeviceMotionHandler === 'function';
            var hasOrient = orientationListeners.length > 0 || typeof onDeviceOrientationHandler === 'function';
            if (!hasMotion && !hasOrient) return;

            // In landscape: gravity is primarily on Y/X vector
            var gx = 0.08 + (Math.random() - 0.5) * 0.04;
            var gy = 9.80 + (Math.random() - 0.5) * 0.06;
            var gz = 0.42 + (Math.random() - 0.5) * 0.04;

            if (hasMotion) {
                var acc = {
                    x: (Math.random() - 0.5) * 0.02,
                    y: (Math.random() - 0.5) * 0.02,
                    z: (Math.random() - 0.5) * 0.02
                };
                var accG = { x: gx, y: gy, z: gz };
                var rot = {
                    alpha: (Math.random() - 0.5) * 0.1,
                    beta: (Math.random() - 0.5) * 0.1,
                    gamma: (Math.random() - 0.5) * 0.1
                };
                var motionEvt = createDeviceMotionEvent(acc, accG, rot, 50);
                for (var m = 0; m < motionListeners.length; m++) {
                    try { motionListeners[m].call(window, motionEvt); } catch (e) {}
                }
                if (typeof onDeviceMotionHandler === 'function') {
                    try { onDeviceMotionHandler.call(window, motionEvt); } catch (e) {}
                }
            }

            if (hasOrient) {
                var alpha = +(180.2 + (Math.random() - 0.5) * 0.2).toFixed(2);
                var beta = +(45.1 + (Math.random() - 0.5) * 0.2).toFixed(2);
                var gamma = +((Math.random() - 0.5) * 0.2).toFixed(2);
                var orientEvt = createDeviceOrientationEvent(alpha, beta, gamma, true);
                for (var o = 0; o < orientationListeners.length; o++) {
                    try { orientationListeners[o].call(window, orientEvt); } catch (e) {}
                }
                if (typeof onDeviceOrientationHandler === 'function') {
                    try { onDeviceOrientationHandler.call(window, orientEvt); } catch (e) {}
                }
            }
        }, 50);
    } catch (e) {}

    // --- 7. Screen & Audio Normalization ---
    try {
        if (window.screen && window.screen.orientation) {
            Object.defineProperty(window.screen.orientation, 'type', {
                get: makeNative(function() { return 'landscape-primary'; }, 'get type'),
                enumerable: true
            });
            Object.defineProperty(window.screen.orientation, 'angle', {
                get: makeNative(function() { return 90; }, 'get angle'),
                enumerable: true
            });
        }
    } catch (e) {}

    try {
        if (window.AudioContext || window.webkitAudioContext) {
            var Actx = window.AudioContext || window.webkitAudioContext;
            Object.defineProperty(Actx.prototype, 'sampleRate', {
                get: makeNative(function() { return 48000; }, 'get sampleRate'),
                enumerable: true
            });
        }
    } catch (e) {}

    // Clean up any legacy disguise keys from localStorage
    try {
        if (typeof localStorage !== 'undefined') {
            localStorage.removeItem('__dofus_device_uuid__');
            localStorage.removeItem('__dofus_device_serial__');
            localStorage.removeItem('__dofus_lan_ip__');
        }
    } catch (e) {}

    // --- 8. Cordova Device Plugin Interception (SPOOF-01) ---
    var _deviceUuid = (function() {
        var configured = '__DEVICE_UUID__';
        if (configured && !configured.startsWith('__')) {
            return configured;
        }
        if (typeof window !== 'undefined' && window.device && window.device.uuid) {
            return window.device.uuid;
        }
        try {
            var hostPort = (typeof location !== 'undefined' && location.port) ? parseInt(location.port, 10) : 0;
            if (hostPort > 0) {
                var hex = ((hostPort * 2654435761) >>> 0).toString(16);
                return 'ca8ea76b' + hex.padStart(8, '0');
            }
        } catch (e) {}
        return 'ca8ea76b0fe32f85';
    })();

    var _deviceSerial = (function() {
        var configured = '__DEVICE_SERIAL__';
        if (configured && !configured.startsWith('__')) {
            return configured;
        }
        if (typeof window !== 'undefined' && window.device && window.device.serial) {
            return window.device.serial;
        }
        try {
            var hostPort = (typeof location !== 'undefined' && location.port) ? parseInt(location.port, 10) : 0;
            if (hostPort > 0) {
                var hex = ((hostPort * 1103515245) >>> 0).toString(16).toUpperCase();
                return 'R58M' + hex.padStart(8, '0');
            }
        } catch (e) {}
        return 'R58M45F41EFD';
    })();

    function patchCordovaDevice() {
        try {
            if (window.device) {
                window.device.model = _deviceModel;
                window.device.manufacturer = _deviceManufacturer;
                window.device.isVirtual = false;
                window.device.platform = 'Android';
                window.device.version = '10';
                window.device.uuid = _deviceUuid;
                window.device.serial = _deviceSerial;
            }
        } catch (e) {}
    }

    // Call immediately in case deviceready already fired before injection (CDP-02)
    patchCordovaDevice();
    if (typeof document !== 'undefined' && document.addEventListener) {
        document.addEventListener('deviceready', patchCordovaDevice, false);
    }

    // Intercept window.device in case Cordova assigns it asynchronously
    try {
        var _curDev = window.device;
        Object.defineProperty(window, 'device', {
            get: makeNative(function() {
                if (_curDev) {
                    _curDev.model = _deviceModel;
                    _curDev.manufacturer = _deviceManufacturer;
                    _curDev.isVirtual = false;
                    _curDev.platform = 'Android';
                    _curDev.version = '10';
                    _curDev.uuid = _deviceUuid;
                    _curDev.serial = _deviceSerial;
                }
                return _curDev;
            }, 'get device'),
            set: makeNative(function(val) {
                _curDev = val;
                if (_curDev) {
                    _curDev.model = _deviceModel;
                    _curDev.manufacturer = _deviceManufacturer;
                    _curDev.isVirtual = false;
                    _curDev.platform = 'Android';
                    _curDev.version = '10';
                    _curDev.uuid = _deviceUuid;
                    _curDev.serial = _deviceSerial;
                }
            }, 'set device'),
            configurable: true,
            enumerable: true
        });
    } catch (e) {}
    if (window.cordova) {
        var origDefine = window.cordova.define;
        if (origDefine) {
            window.cordova.define = makeNative(function(id, factory) {
                if (id === 'cordova-plugin-device.device') {
                    var wrappedFactory = function(require, exports, module) {
                        factory(require, exports, module);
                        var dev = module.exports;
                        if (dev) {
                            dev.model = _deviceModel;
                            dev.manufacturer = _deviceManufacturer;
                            dev.isVirtual = false;
                            dev.platform = 'Android';
                            dev.version = '10';
                            dev.uuid = _deviceUuid;
                            dev.serial = _deviceSerial;
                            if (dev.getInfo) {
                                var origGetInfo = dev.getInfo;
                                dev.getInfo = makeNative(function(success, error) {
                                    origGetInfo.call(dev, function(info) {
                                        info.model = _deviceModel;
                                        info.manufacturer = _deviceManufacturer;
                                        info.isVirtual = false;
                                        info.platform = 'Android';
                                        info.version = '10';
                                        info.uuid = _deviceUuid;
                                        info.serial = _deviceSerial;
                                        success(info);
                                    }, error);
                                }, 'getInfo');
                            }
                        }
                    };
                    return origDefine.call(window.cordova, id, wrappedFactory);
                }
                return origDefine.apply(this, arguments);
            }, 'define');
        }
    }

    // --- 9. Screen Depth, Pixel Ratio & WebRTC Protection ---
    try {
        if (window.screen) {
            defineProp(window.screen, 'colorDepth', 24);
            defineProp(window.screen, 'pixelDepth', 24);
            defineProp(window.screen, 'width', 914);
            defineProp(window.screen, 'height', 411);
            defineProp(window.screen, 'availWidth', 914);
            defineProp(window.screen, 'availHeight', 411);
        }
        defineProp(window, 'devicePixelRatio', 2.625);
    } catch (e) {}

    // WebRTC IP leak protection: prevents host private IP & QEMU 10.0.2.15 leakage (NET-02, Item 6)
    try {
        var _fakeLocalIp = (function() {
            var seed = (_deviceSerial || 'R58M45F41EFD') + (_deviceUuid || 'ca8ea76b0fe32f85');
            var hash = 0;
            for (var i = 0; i < seed.length; i++) {
                hash = ((hash << 5) - hash) + seed.charCodeAt(i);
                hash |= 0;
            }
            var h = Math.abs(hash);
            var o2 = 64 + (h % 64);              // 64..127 (CGNAT RFC 6598: 100.64.0.0/10)
            var o3 = ((h >> 6) % 254) + 1;       // 1..254
            var o4 = ((h >> 12) % 253) + 2;      // 2..254
            return '100.' + o2 + '.' + o3 + '.' + o4;
        })();

        var PeerConn = window.RTCPeerConnection || window.webkitRTCPeerConnection;
        if (PeerConn) {
            var origCreateOffer = PeerConn.prototype.createOffer;
            if (origCreateOffer) {
                PeerConn.prototype.createOffer = makeNative(function(options) {
                    return origCreateOffer.apply(this, arguments).then(function(offer) {
                        if (offer && offer.sdp) {
                            offer.sdp = offer.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp);
                        }
                        return offer;
                    });
                }, 'createOffer');
            }

            var origCreateAnswer = PeerConn.prototype.createAnswer;
            if (origCreateAnswer) {
                PeerConn.prototype.createAnswer = makeNative(function(options) {
                    return origCreateAnswer.apply(this, arguments).then(function(answer) {
                        if (answer && answer.sdp) {
                            answer.sdp = answer.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp);
                        }
                        return answer;
                    });
                }, 'createAnswer');
            }

            function scrubCandidate(candidate) {
                if (!candidate) return candidate;
                if (candidate.candidate && candidate.candidate.indexOf('10.0.2.15') !== -1) {
                    candidate.candidate = candidate.candidate.replace(/10\.0\.2\.15/g, _fakeLocalIp);
                }
                if (candidate.address === '10.0.2.15') {
                    try {
                        Object.defineProperty(candidate, 'address', { value: _fakeLocalIp, configurable: true, enumerable: true });
                    } catch (e) {}
                }
                return candidate;
            }

            var origAddIceCandidate = PeerConn.prototype.addIceCandidate;
            if (origAddIceCandidate) {
                PeerConn.prototype.addIceCandidate = makeNative(function(candidate) {
                    scrubCandidate(candidate);
                    return origAddIceCandidate.apply(this, arguments);
                }, 'addIceCandidate');
            }

            var pcListenerWrappers = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;

            var origAddEventListenerPC = PeerConn.prototype.addEventListener;
            if (origAddEventListenerPC) {
                PeerConn.prototype.addEventListener = makeNative(function(type, listener, options) {
                    if (type === 'icecandidate' && typeof listener === 'function') {
                        var wrapped = function(evt) {
                            if (evt && evt.candidate) {
                                scrubCandidate(evt.candidate);
                            }
                            return listener.apply(this, arguments);
                        };
                        if (pcListenerWrappers) {
                            var map = pcListenerWrappers.get(this);
                            if (!map) {
                                map = (typeof Map !== 'undefined') ? new Map() : {};
                                pcListenerWrappers.set(this, map);
                            }
                            if (map instanceof Map) {
                                map.set(listener, wrapped);
                            } else {
                                map[listener] = wrapped;
                            }
                        }
                        return origAddEventListenerPC.call(this, type, wrapped, options);
                    }
                    return origAddEventListenerPC.apply(this, arguments);
                }, 'addEventListener');
            }

            var origRemoveEventListenerPC = PeerConn.prototype.removeEventListener;
            if (origRemoveEventListenerPC) {
                PeerConn.prototype.removeEventListener = makeNative(function(type, listener, options) {
                    if (type === 'icecandidate' && typeof listener === 'function') {
                        var targetListener = listener;
                        if (pcListenerWrappers) {
                            var map = pcListenerWrappers.get(this);
                            if (map) {
                                if (map instanceof Map && map.has(listener)) {
                                    targetListener = map.get(listener);
                                    map.delete(listener);
                                } else if (map[listener]) {
                                    targetListener = map[listener];
                                    delete map[listener];
                                }
                            }
                        }
                        return origRemoveEventListenerPC.call(this, type, targetListener, options);
                    }
                    return origRemoveEventListenerPC.apply(this, arguments);
                }, 'removeEventListener');
            }

            var origOnIceCandidateDesc = Object.getOwnPropertyDescriptor(PeerConn.prototype, 'onicecandidate');
            Object.defineProperty(PeerConn.prototype, 'onicecandidate', {
                get: makeNative(function() {
                    return this._onicecandidate || null;
                }, 'get onicecandidate'),
                set: makeNative(function(fn) {
                    if (typeof fn === 'function') {
                        this._onicecandidate = fn;
                        var self = this;
                        var wrapped = function(evt) {
                            if (evt && evt.candidate) {
                                scrubCandidate(evt.candidate);
                            }
                            return fn.call(self, evt);
                        };
                        if (origOnIceCandidateDesc && origOnIceCandidateDesc.set) {
                            origOnIceCandidateDesc.set.call(this, wrapped);
                        }
                    } else {
                        this._onicecandidate = null;
                        if (origOnIceCandidateDesc && origOnIceCandidateDesc.set) {
                            origOnIceCandidateDesc.set.call(this, null);
                        }
                    }
                }, 'set onicecandidate'),
                configurable: true,
                enumerable: true
            });
        }
    } catch (e) {}

    // --- 10. Font Probing, Offline Audio & Locale Defenses ---
    try {
        // A. Font Probing Normalization: ensure desktop Windows fonts return false/fallback
        if (document.fonts && document.fonts.check) {
            var origFontsCheck = document.fonts.check;
            var desktopFonts = ['segoe ui', 'calibri', 'consolas', 'cambria', 'ms gothic', 'arial black', 'tahoma', 'lucida console', 'comic sans ms'];
            document.fonts.check = makeNative(function(font, text) {
                var fLower = (font || '').toLowerCase();
                for (var df = 0; df < desktopFonts.length; df++) {
                    if (fLower.indexOf(desktopFonts[df]) !== -1) {
                        return false;
                    }
                }
                return origFontsCheck.apply(this, arguments);
            }, 'check');
        }

        // C. OfflineAudioContext floating-point signature normalization
        if (window.OfflineAudioContext) {
            var origStartRendering = OfflineAudioContext.prototype.startRendering;
            OfflineAudioContext.prototype.startRendering = makeNative(function() {
                return origStartRendering.apply(this, arguments).then(function(buffer) {
                    if (buffer && buffer.getChannelData) {
                        var chan = buffer.getChannelData(0);
                        if (chan && chan.length > 10) {
                            for (var j = 0; j < chan.length; j += 128) {
                                chan[j] += 0.00000001; // subtle ARM NEON FPU jitter
                            }
                        }
                    }
                    return buffer;
                });
            }, 'startRendering');
        }

        // D. Language & Locale normalization for genuine European/French mobile client
        defineProp(navigator, 'language', 'fr-FR');
        defineProp(navigator, 'languages', ['fr-FR', 'fr', 'en-US', 'en']);
    } catch (e) {}

    // --- 11. Cognitive Human Behavior & Fatigue Modeling Engine ---
    try {
        var RandomDist = {
            uniform: function(min, max) {
                min = min || 0; max = max === undefined ? 1 : max;
                return min + Math.random() * (max - min);
            },
            gaussian: function(mean, stdDev) {
                mean = mean || 0; stdDev = stdDev === undefined ? 1 : stdDev;
                var u1 = Math.random(), u2 = Math.random();
                while (u1 <= 1e-15) u1 = Math.random();
                return mean + Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2) * stdDev;
            },
            exponential: function(tau) {
                var u = Math.random();
                while (u <= 1e-15) u = Math.random();
                return -Math.log(u) * tau;
            },
            exGaussian: function(mu, sigma, tau) {
                return this.gaussian(mu, sigma) + this.exponential(tau);
            },
            weibull: function(scale, shape) {
                var u = Math.random();
                while (u <= 1e-15) u = Math.random();
                return scale * Math.pow(-Math.log(u), 1.0 / shape);
            }
        };

        var BEHAVIOR_STATES = {
            FOCUSED_COMBAT: { name: 'FOCUSED_COMBAT', baseMu: 210, baseSigma: 35, baseTau: 75, spatialScatter: 5.0, hesitationProb: 0.04 },
            TACTICAL_DELIBERATION: { name: 'TACTICAL_DELIBERATION', baseMu: 650, baseSigma: 120, baseTau: 380, spatialScatter: 7.5, hesitationProb: 0.25 },
            EXPLORATION_FLOW: { name: 'EXPLORATION_FLOW', baseMu: 320, baseSigma: 55, baseTau: 120, spatialScatter: 9.0, hesitationProb: 0.08 },
            INVENTORY_MANAGEMENT: { name: 'INVENTORY_MANAGEMENT', baseMu: 380, baseSigma: 70, baseTau: 190, spatialScatter: 6.0, hesitationProb: 0.15 },
            MICRO_DISTRACTION: { name: 'MICRO_DISTRACTION', baseMu: 2500, baseSigma: 600, baseTau: 1800, spatialScatter: 12.0, hesitationProb: 0.80 }
        };

        function HumanFatigueModel(config) {
            config = config || {};
            this.sessionStartTime = Date.now();
            this.actionsPerformed = 0;
            this.currentState = BEHAVIOR_STATES.EXPLORATION_FLOW;
            this.timeDecayMinutes = config.timeDecayMinutes || 90;
            this.circadianBaseline = config.circadianBaseline || 1.0;
            this.consecutiveActionBurst = 0;
            this.lastActionTimestamp = Date.now();
            this.history = [];
            this.maxHistory = 10;
        }

        HumanFatigueModel.prototype.getSessionDurationMinutes = function() {
            return (Date.now() - this.sessionStartTime) / (1000 * 60);
        };

        HumanFatigueModel.prototype.getFatigueFactor = function() {
            var elapsedMin = this.getSessionDurationMinutes();
            var timeFatigue = 1.0 - Math.exp(-elapsedMin / this.timeDecayMinutes);
            var actionFatigue = Math.min(0.4, (this.actionsPerformed / 2500) * 0.4);
            var total = (timeFatigue * 0.7 + actionFatigue) * this.circadianBaseline;
            return Math.min(1.0, Math.max(0.0, total));
        };

        HumanFatigueModel.prototype.updateState = function(stateHint) {
            if (stateHint && BEHAVIOR_STATES[stateHint]) {
                this.currentState = BEHAVIOR_STATES[stateHint];
                return;
            }
            var fatigue = this.getFatigueFactor();
            var roll = Math.random();
            var distractionThreshold = 0.02 + (fatigue * 0.08);
            var deliberationThreshold = distractionThreshold + 0.15 + (fatigue * 0.10);
            if (roll < distractionThreshold) {
                this.currentState = BEHAVIOR_STATES.MICRO_DISTRACTION;
            } else if (roll < deliberationThreshold) {
                this.currentState = BEHAVIOR_STATES.TACTICAL_DELIBERATION;
            } else if (roll < 0.65) {
                this.currentState = BEHAVIOR_STATES.EXPLORATION_FLOW;
            } else {
                this.currentState = BEHAVIOR_STATES.FOCUSED_COMBAT;
            }
        };

        HumanFatigueModel.prototype.generateReactionDelay = function(opts) {
            opts = opts || {};
            this.actionsPerformed++;
            this.consecutiveActionBurst++;
            var fatigue = this.getFatigueFactor();
            var st = this.currentState;
            var mu = (opts.mu || st.baseMu) * (1.0 + (fatigue * 0.18));
            var sigma = (opts.sigma || st.baseSigma) * (1.0 + (fatigue * 0.35));
            var tau = (opts.tau || st.baseTau) * (1.0 + (fatigue * 1.25));
            var delay = RandomDist.exGaussian(mu, sigma, tau);
            if (delay < 185) {
                delay = 185 + RandomDist.exponential(35);
            }
            if (Math.random() < (st.hesitationProb + fatigue * 0.10)) {
                delay += RandomDist.exGaussian(400, 80, 250);
            }
            this.history.push({ delay: delay, fatigue: fatigue, state: st.name, timestamp: Date.now() });
            if (this.history.length > this.maxHistory) this.history.shift();
            this.lastActionTimestamp = Date.now();
            return Math.round(delay);
        };

        HumanFatigueModel.prototype.generateTrajectory = function(startX, startY, targetX, targetY, targetWidth) {
            targetWidth = targetWidth || 32;
            var dx = targetX - startX, dy = targetY - startY;
            var distance = Math.sqrt(dx * dx + dy * dy);
            var id = Math.log2((2.0 * Math.max(1, distance)) / targetWidth);
            var mt = 130 + 115 * Math.max(0.5, id);
            var duration = this.generateReactionDelay({ mu: mt, sigma: mt * 0.18, tau: mt * 0.25 });
            var fatigue = this.getFatigueFactor();
            var scatterSigma = this.currentState.spatialScatter * (1.0 + fatigue * 0.8);
            var finalX = Math.round(targetX + RandomDist.gaussian(0, scatterSigma));
            var finalY = Math.round(targetY + RandomDist.gaussian(0, scatterSigma));
            var perpX = -dy / (distance || 1), perpY = dx / (distance || 1);
            var arcMagnitude = (RandomDist.gaussian(0, 1) * distance * 0.12);
            var cp1X = startX + (dx * 0.25) + (perpX * arcMagnitude);
            var cp1Y = startY + (dy * 0.25) + (perpY * arcMagnitude);
            var cp2X = startX + (dx * 0.75) + (perpX * (arcMagnitude * 0.6));
            var cp2Y = startY + (dy * 0.75) + (perpY * (arcMagnitude * 0.6));
            var stepCount = Math.max(6, Math.min(40, Math.floor(duration / 16)));
            var points = [];
            for (var i = 0; i <= stepCount; i++) {
                var rawT = i / stepCount;
                var t = 10 * Math.pow(rawT, 3) - 15 * Math.pow(rawT, 4) + 6 * Math.pow(rawT, 5);
                var oneMinusT = 1 - t;
                var bx = Math.pow(oneMinusT, 3) * startX + 3 * Math.pow(oneMinusT, 2) * t * cp1X + 3 * oneMinusT * Math.pow(t, 2) * cp2X + Math.pow(t, 3) * finalX;
                var by = Math.pow(oneMinusT, 3) * startY + 3 * Math.pow(oneMinusT, 2) * t * cp1Y + 3 * oneMinusT * Math.pow(t, 2) * cp2Y + Math.pow(t, 3) * finalY;
                var tremor = (Math.sin(rawT * Math.PI * 8) * 0.45);
                points.push({ x: +(bx + tremor).toFixed(1), y: +(by + tremor).toFixed(1), timeFraction: +rawT.toFixed(3) });
            }
            return { startX: startX, startY: startY, targetX: finalX, targetY: finalY, durationMs: duration, points: points, fatigueFactor: +fatigue.toFixed(3), state: this.currentState.name };
        };

        HumanFatigueModel.prototype.checkBiologicalPause = function() {
            var elapsedMin = this.getSessionDurationMinutes();
            var fatigue = this.getFatigueFactor();

            // Micro-pause: looking away, reading chat, drinking water (20 - 75 seconds)
            if (this.consecutiveActionBurst > 45 && Math.random() < (0.04 + fatigue * 0.08)) {
                this.consecutiveActionBurst = 0;
                var microBreak = Math.round(RandomDist.weibull(35, 1.8) + 15) * 1000;
                return {
                    type: 'MICRO_PAUSE',
                    durationMs: Math.min(90000, microBreak),
                    reason: 'Cognitive rest / re-orientation'
                };
            }

            // Biological break: AFK bathroom, stretch, snack (2 - 7 minutes)
            // Probability grows with continuous session time
            if (elapsedMin > 40 && Math.random() < (0.008 * (elapsedMin / 30))) {
                this.consecutiveActionBurst = 0;
                var bioBreak = Math.round(RandomDist.weibull(240, 2.2) + 60) * 1000;
                return {
                    type: 'BIOLOGICAL_AFK',
                    durationMs: Math.min(480000, bioBreak),
                    reason: 'Biological break / ergonomics'
                };
            }

            return null;
        };

        HumanFatigueModel.prototype.isPaused = function() {
            return this.activePauseUntil ? Date.now() < this.activePauseUntil : false;
        };

        HumanFatigueModel.prototype.getRemainingPauseMs = function() {
            return this.activePauseUntil ? Math.max(0, this.activePauseUntil - Date.now()) : 0;
        };

        HumanFatigueModel.prototype.enforcePause = function() {
            var p = this.checkBiologicalPause();
            if (p && p.durationMs > 0) {
                this.activePauseUntil = Date.now() + p.durationMs;
                this.currentPause = p;
                return p;
            }
            return null;
        };

        var _humanBehaviorModel = new HumanFatigueModel();
        function getBehaviorModel() {
            if (!_humanBehaviorModel) _humanBehaviorModel = new HumanFatigueModel();
            return _humanBehaviorModel;
        }

        HumanFatigueModel.getInstance = function() {
            return getBehaviorModel();
        };

        HumanFatigueModel.isPaused = function() {
            return getBehaviorModel().isPaused();
        };

        HumanFatigueModel.getRemainingPauseMs = function() {
            return getBehaviorModel().getRemainingPauseMs();
        };

        HumanFatigueModel.enforcePause = function() {
            return getBehaviorModel().enforcePause();
        };

        HumanFatigueModel.getFatigueFactor = function() {
            return getBehaviorModel().getFatigueFactor();
        };

        var _lastDownTime = 0;
        var _targetDwellMs = 85;

        function emitTouch(type, el, x, y) {
            if (typeof window !== 'undefined' && window.TouchEvent && el && el.dispatchEvent) {
                try {
                    var touch = null;
                    if (typeof Touch === 'function') {
                        touch = new Touch({
                            identifier: 1,
                            target: el,
                            clientX: x,
                            clientY: y,
                            screenX: x,
                            screenY: y,
                            pageX: x,
                            pageY: y
                        });
                    }
                    var te = new TouchEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        touches: type === 'touchend' ? [] : (touch ? [touch] : []),
                        targetTouches: type === 'touchend' ? [] : (touch ? [touch] : []),
                        changedTouches: touch ? [touch] : []
                    });
                    el.dispatchEvent(te);
                } catch (err) {}
            }
        }

        HumanFatigueModel.dispatchHumanAction = function(target, targetX, targetY, callback) {
            target = target || (document.elementFromPoint ? document.elementFromPoint(targetX, targetY) : null) || (document.body || window);
            var bm = getBehaviorModel();
            if (bm.isPaused()) {
                var rem = bm.getRemainingPauseMs();
                setTimeout(function() {
                    HumanFatigueModel.dispatchHumanAction(target, targetX, targetY, callback);
                }, Math.min(5000, rem));
                return;
            }
            bm.updateState();
            var startX = _lastPointerX;
            var startY = _lastPointerY;
            var traj = bm.generateTrajectory(startX, startY, targetX, targetY, 32);
            var pts = (traj && traj.points) ? traj.points : [{ x: targetX, y: targetY }];
            var totalPts = pts.length;
            var stepDelay = Math.max(12, Math.floor((traj.durationMs || 250) / Math.max(1, totalPts)));

            // 1. Initial contact: pointerdown and touchstart MUST occur before any motion (BUG-29)
            var firstPt = pts[0];
            _lastPointerX = firstPt.x;
            _lastPointerY = firstPt.y;
            _lastDownTime = Date.now();
            _targetDwellMs = Math.round(RandomDist.gaussian(85, 12));
            if (_targetDwellMs < 65) _targetDwellMs = 65;
            if (_targetDwellMs > 125) _targetDwellMs = 125;

            if (window.PointerEvent && target.dispatchEvent) {
                try {
                    target.dispatchEvent(new PointerEvent('pointerdown', {
                        bubbles: true,
                        cancelable: true,
                        clientX: firstPt.x,
                        clientY: firstPt.y,
                        screenX: firstPt.x,
                        screenY: firstPt.y,
                        pointerId: 1,
                        pointerType: 'touch',
                        isPrimary: true,
                        pressure: 0.7
                    }));
                } catch (e) {}
            }
            emitTouch('touchstart', target, firstPt.x, firstPt.y);

            // 2. Intermediate path points dispatch pointermove and touchmove
            var idx = 1;
            function stepMove() {
                if (idx < totalPts) {
                    var p = pts[idx];
                    _lastPointerX = p.x;
                    _lastPointerY = p.y;
                    if (window.PointerEvent && target.dispatchEvent) {
                        try {
                            target.dispatchEvent(new PointerEvent('pointermove', {
                                bubbles: true,
                                cancelable: true,
                                clientX: p.x,
                                clientY: p.y,
                                screenX: p.x,
                                screenY: p.y,
                                pointerId: 1,
                                pointerType: 'touch',
                                isPrimary: true,
                                pressure: 0.5
                            }));
                        } catch (e) {}
                    }
                    emitTouch('touchmove', target, p.x, p.y);
                    idx++;
                    setTimeout(stepMove, stepDelay);
                } else {
                    // 3. Conclude at destination with pointerup, touchend, and simulated click
                    var destPt = pts[totalPts - 1];
                    var destX = destPt ? destPt.x : targetX;
                    var destY = destPt ? destPt.y : targetY;
                    _lastPointerX = destX;
                    _lastPointerY = destY;

                    setTimeout(function() {
                        if (window.PointerEvent && target.dispatchEvent) {
                            try {
                                target.dispatchEvent(new PointerEvent('pointerup', {
                                    bubbles: true,
                                    cancelable: true,
                                    clientX: destX,
                                    clientY: destY,
                                    screenX: destX,
                                    screenY: destY,
                                    pointerId: 1,
                                    pointerType: 'touch',
                                    isPrimary: true,
                                    pressure: 0
                                }));
                            } catch (e) {}
                        }
                        emitTouch('touchend', target, destX, destY);

                        // W3C sequencing: dispatch native click event on target
                        if (typeof MouseEvent === 'function' && target.dispatchEvent) {
                            try {
                                target.dispatchEvent(new MouseEvent('click', {
                                    bubbles: true,
                                    cancelable: true,
                                    clientX: destX,
                                    clientY: destY,
                                    screenX: destX,
                                    screenY: destY
                                }));
                            } catch (e) {}
                        }

                        if (typeof callback === 'function') {
                            try { callback(); } catch (e) {}
                        }
                    }, _targetDwellMs);
                }
            }

            if (totalPts > 1) {
                setTimeout(stepMove, stepDelay);
            } else {
                stepMove();
            }
        };

        if (typeof window !== 'undefined' && !window.__HumanFatigueModel) {
            try {
                Object.defineProperty(window, '__HumanFatigueModel', {
                    value: HumanFatigueModel,
                    writable: false,
                    enumerable: false,
                    configurable: false
                });
            } catch (e) {
                window.__HumanFatigueModel = HumanFatigueModel;
            }
        }

        // Track last pointer coordinates for external motor trajectory calculation (screen center: 914/2, 411/2)
        var _lastPointerX = 457;
        var _lastPointerY = 205;

        // Passive telemetry listeners: non-intrusive, never cancel or delay native DOM events
        window.addEventListener('pointerdown', function(e) {
            var curX = e.clientX || e.pageX || 457;
            var curY = e.clientY || e.pageY || 205;
            try {
                var bm = getBehaviorModel();
                bm.generateReactionDelay();
                var p = bm.checkBiologicalPause();
                if (p) bm.enforcePause();
            } catch (err) {}
            _lastPointerX = curX;
            _lastPointerY = curY;
        }, { capture: true, passive: true });

        window.addEventListener('touchstart', function(e) {
            try {
                if (e.touches && e.touches.length > 0) {
                    var t = e.touches[0];
                    var curX = t.clientX || t.pageX || _lastPointerX;
                    var curY = t.clientY || t.pageY || _lastPointerY;
                    var bm = getBehaviorModel();
                    bm.generateReactionDelay();
                    var p = bm.checkBiologicalPause();
                    if (p) bm.enforcePause();
                    _lastPointerX = curX;
                    _lastPointerY = curY;
                }
            } catch (err) {}
        }, { capture: true, passive: true });
    } catch (e) {}
})();
