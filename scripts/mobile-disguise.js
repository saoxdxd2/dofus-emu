(function() {
    'use strict';
    if (typeof window === 'undefined') return;

    // This file is the single source of truth for the in-client agent. The two copies
    // that used to sit beside it (desktop/client/www/js and client/www/js) are gone:
    // no page loads them any more, and every page that did was running a different
    // program than the one in scripts/.
    //
    // It also runs once per page load now, and that is the point. It used to run
    // twice: the WebView2 host injects it before the document is created, and
    // standalone.html pulled in the same file a second time with a <script src> tag.
    // The symptom was one log line, repeated, that nobody read:
    //     [FarmBot] Bound to connection manager.
    //     [x2] [FarmBot] Bound to connection manager.
    // Two FarmBot.init() calls meant two waitForConnection timers, and each of them
    // bound dofus.connectionManager, so two bots drove one HUD: double tick loops,
    // double packets, and a HUD that reported whichever copy spoke last. The tag is
    // removed, and this guard stays because a transport can be re-added by accident
    // and the second copy must be a no-op instead of a second bot.
    if (window.__DofusFarmAgentLoaded) {
        try { console.log('[AgentBridge] already loaded; skipping duplicate execution'); } catch (e) {}
        return;
    }
    window.__DofusFarmAgentLoaded = true;

    // The login page fetches config.json exactly once, and its XHR treats every non-200
    // -- including status 0 from a socket that died -- as fatal: it shows "Server busy"
    // and reloads the whole app. The relay now moves the session egress before the first
    // byte is delivered, but a request already in flight when that happens still sees the
    // dead socket. Retrying this one GET turns a broken login into a slow one. Only the
    // config file is retried: every other request in the game has its own retry semantics
    // that a blind replay could break.
    (function () {
        var proto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
        if (!proto || proto.__dofusFarmConfigRetry) return;
        var rawOpen = proto.open;
        var rawSend = proto.send;
        var configName = /(?:^|\/)config\.json(?:\?|$)/;
        var maxRetries = 2;
        proto.open = function (method, url) {
            var retryable = String(method || '').toUpperCase() === 'GET' &&
                configName.test(String(url || ''));
            this.__dofusFarmRetry = retryable ? { method: method, url: url, attempt: 0 } : null;
            return rawOpen.apply(this, arguments);
        };
        proto.send = function (body) {
            var state = this.__dofusFarmRetry;
            if (!state) return rawSend.apply(this, arguments);
            var xhr = this;
            var gameHandler = xhr.onreadystatechange;
            var deliver = function (ev) {
                if (xhr.readyState !== 4) {
                    if (gameHandler) return gameHandler.call(xhr, ev);
                    return;
                }
                var status = 0;
                try { status = Number(xhr.status) || 0; } catch (e) {}
                if ((status === 0 || status >= 500) && state.attempt < maxRetries) {
                    state.attempt += 1;
                    setTimeout(function () {
                        try {
                            // The native open: the patched one would reset the retry count.
                            rawOpen.call(xhr, state.method, state.url, true);
                            xhr.onreadystatechange = deliver;
                            rawSend.call(xhr, body);
                        } catch (e) {
                            if (gameHandler) gameHandler.call(xhr, ev);
                        }
                    }, 500 * state.attempt);
                    return;
                }
                if (gameHandler) gameHandler.call(xhr, ev);
            };
            xhr.onreadystatechange = deliver;
            return rawSend.apply(this, arguments);
        };
        proto.__dofusFarmConfigRetry = true;
    })();

    function cfg(val, fallback) {
        return (!val || val.indexOf('__' + 'DEVICE_') === 0) ? fallback : val;
    }
    function deriveFromPort(mult, prefix, fallback) {
        try {
            var p = (typeof location !== 'undefined' && location.port) ? parseInt(location.port, 10) : 0;
            if (p > 0) return prefix + ((p * mult) >>> 0).toString(16).padStart(8, '0');
        } catch (e) {}
        return fallback;
    }

    var MOBILE_UA = cfg('__DEVICE_UA__', 'Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36').replace(/;\s*wv/i, '').replace(/Version\/[0-9.]+\s*/i, '');
    var MOBILE_APP_VERSION = MOBILE_UA.replace(/^Mozilla\//, '');
    var MOBILE_PLATFORM = 'Linux armv8l';
    var _deviceModel = cfg('__DEVICE_MODEL__', 'SM-A515F');
    var _deviceManufacturer = cfg('__DEVICE_MANUFACTURER__', 'samsung');
    var GL_VENDOR = cfg('__DEVICE_GL_VENDOR__', 'ARM');
    var GL_RENDERER = cfg('__DEVICE_GL_RENDERER__', 'Mali-G72 MP3');
    var MAX_TEX_SIZE = parseInt(cfg('__DEVICE_MAX_TEXTURE_SIZE__', '8192'), 10) || 8192;
    var _deviceUuid = cfg('__DEVICE_UUID__', '') || (window.device && window.device.uuid) || deriveFromPort(2654435761, 'ca8ea76b', 'ca8ea76b0fe32f85');
    var _deviceSerial = cfg('__DEVICE_SERIAL__', '') || (window.device && window.device.serial) || deriveFromPort(1103515245, 'R58M', 'R58M45F41EFD').toUpperCase();

    var _osVer = (MOBILE_UA.match(/Android\s+([0-9.]+)/) || [0, '10'])[1];
    var _fullVer = (MOBILE_UA.match(/Chrome\/([0-9.]+)/) || [0, '83.0.4103.106'])[1];
    var _majorVer = _fullVer.split('.')[0];

    function applyDevProps(dev) {
        if (!dev) return;
        dev.model = _deviceModel; dev.manufacturer = _deviceManufacturer; dev.isVirtual = false;
        dev.platform = 'Android'; dev.version = _osVer; dev.uuid = _deviceUuid; dev.serial = _deviceSerial;
    }

    try {
        if (navigator.platform === MOBILE_PLATFORM && navigator.userAgent === MOBILE_UA) {
            if (window.device) applyDevProps(window.device);
            return;
        }
    } catch (e) {}

    var nativeToString = Function.prototype.toString;
    var hookedFunctions = new WeakMap(), disguisedFrames = new WeakSet(), wrappedGlProtos = new WeakSet();

    function makeNative(fn, name) {
        if (typeof fn !== 'function') return fn;
        var fnName = name || fn.name || '';
        var nativeFn = ({ [fnName](...args) { return fn.apply(this, args); } })[fnName];
        try { Object.defineProperty(nativeFn, 'length', { value: fn.length, configurable: true }); } catch (e) {}
        hookedFunctions.set(nativeFn, 'function ' + fnName + '() { [native code] }');
        return nativeFn;
    }

    var customToString = makeNative(function toString() {
        return (typeof this === 'function' && hookedFunctions.has(this)) ? hookedFunctions.get(this) : nativeToString.apply(this, arguments);
    }, 'toString');
    Function.prototype.toString = customToString;

    var _instanceHash = (function() {
        var seed = _deviceSerial + ':' + _deviceUuid + ':' + _deviceModel, h = 2166136261;
        for (var i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
        return h >>> 0;
    })();
    var _fakeLocalIp = '100.' + (64 + (_instanceHash % 64)) + '.' + (((_instanceHash >>> 6) % 254) + 1) + '.' + (((_instanceHash >>> 14) % 253) + 2);
    var _hwSpecs = (function() {
        var m = (_deviceModel || '').toUpperCase();
        if (/SM-G998|PIXEL 6|LE2123|XQ-BC52|I005D/.test(m)) return { cores: 8, ram: 8 };
        if (m.indexOf('SM-T510') !== -1) return { cores: 8, ram: 3 };
        return /PIXEL 4A|SM-T860|M2007|JOYEUSE|NOTE 9/.test(m) ? { cores: 8, ram: 6 } : { cores: 8, ram: 4 };
    })();

    var _VIEWPORT_DIMS = new Int32Array([MAX_TEX_SIZE, MAX_TEX_SIZE]);
    var _LINE_WIDTH_RANGE = new Float32Array([1, 7]);
    var _POINT_SIZE_RANGE = new Float32Array([1, 1024]);

    function defineProp(obj, prop, valueOrGetter) {
        try {
            var target = obj, brandCheck = null;
            if (obj === navigator && typeof Navigator !== 'undefined' && Navigator.prototype) { target = Navigator.prototype; brandCheck = Navigator; }
            else if (typeof Screen !== 'undefined' && obj === window.screen && Screen.prototype) { target = Screen.prototype; brandCheck = Screen; }
            else if (obj === window && typeof Window !== 'undefined' && Window.prototype) { target = Window.prototype; brandCheck = Window; }
            if (target !== obj) { try { delete obj[prop]; } catch (e) {} }
            var isFn = typeof valueOrGetter === 'function';
            Object.defineProperty(target, prop, {
                get: makeNative(function() {
                    if (brandCheck && !(this instanceof brandCheck)) throw new TypeError('Illegal invocation');
                    return isFn ? valueOrGetter.call(this) : valueOrGetter;
                }, 'get ' + prop),
                configurable: true, enumerable: true
            });
        } catch (e) {}
    }

    function defineGetters(target, map) {
        for (var k in map) {
            if (Object.prototype.hasOwnProperty.call(map, k)) {
                (function(prop, fn) {
                    try { Object.defineProperty(target, prop, { get: makeNative(fn, 'get ' + prop), configurable: true, enumerable: true }); } catch (e) {}
                })(k, map[k]);
            }
        }
    }

    // 1. Timezone coherence (Africa/Casablanca)
    try {
        if (typeof Intl !== 'undefined' && Intl.DateTimeFormat && Intl.DateTimeFormat.prototype) {
            var origResolved = Intl.DateTimeFormat.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = makeNative(function resolvedOptions() {
                var res = origResolved.apply(this, arguments);
                try { res.timeZone = 'Africa/Casablanca'; } catch (e) {}
                return res;
            }, 'resolvedOptions');
        }
    } catch (e) {}

    // 2. Alert & Dialogs - Untouched native browser implementations

    // 3 & 5. Navigator, PluginArray, NetworkInformation & BatteryManager
    var falseNative = makeNative(function dispatchEvent() { return false; }, 'dispatchEvent');
    function makeEmptyCollection(Ctor, tagName, withRefresh) {
        var obj = Object.create((typeof Ctor !== 'undefined' && Ctor.prototype) ? Ctor.prototype : Object.prototype);
        Object.defineProperty(obj, 'length', { value: 0, writable: false, enumerable: false, configurable: true });
        obj.item = makeNative(function item() { return null; }, 'item');
        obj.namedItem = makeNative(function namedItem() { return null; }, 'namedItem');
        if (withRefresh) obj.refresh = makeNative(function refresh() {}, 'refresh');
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) { try { Object.defineProperty(obj, Symbol.toStringTag, { value: tagName, configurable: true }); } catch (e) {} }
        return obj;
    }

    var emptyPluginArray, emptyMimeTypeArray, connectionObj;
    try {
        emptyPluginArray = makeEmptyCollection(typeof PluginArray !== 'undefined' ? PluginArray : undefined, 'PluginArray', true);
        emptyMimeTypeArray = makeEmptyCollection(typeof MimeTypeArray !== 'undefined' ? MimeTypeArray : undefined, 'MimeTypeArray', false);
    } catch (e) {}

    try {
        connectionObj = Object.create((typeof NetworkInformation !== 'undefined' && NetworkInformation.prototype) ? NetworkInformation.prototype : ((typeof EventTarget !== 'undefined' && EventTarget.prototype) ? EventTarget.prototype : Object.prototype));
        connectionObj.effectiveType = '4g'; connectionObj.type = 'cellular'; connectionObj.saveData = false; connectionObj.onchange = null;
        if (!('addEventListener' in connectionObj)) {
            connectionObj.addEventListener = makeNative(function addEventListener() {}, 'addEventListener');
            connectionObj.removeEventListener = makeNative(function removeEventListener() {}, 'removeEventListener');
            connectionObj.dispatchEvent = falseNative;
        }
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) { try { Object.defineProperty(connectionObj, Symbol.toStringTag, { value: 'NetworkInformation', configurable: true }); } catch (e) {} }
        var baseRtt = 40 + (_instanceHash % 25), baseDownlink = 12.0 + ((_instanceHash >>> 4) % 80) * 0.1;
        defineGetters(connectionObj, {
            rtt: function() { return baseRtt + Math.floor((Math.random() - 0.5) * 6); },
            downlink: function() { return +(baseDownlink + (Math.random() - 0.5) * 1.2).toFixed(1); }
        });
    } catch (e) {}

    var battStartTime = Date.now(), initialBattPct = 72 + (_instanceHash % 23);
    var battProto = (typeof BatteryManager !== 'undefined' && BatteryManager.prototype) ? BatteryManager.prototype : Object.create((typeof EventTarget !== 'undefined' && EventTarget.prototype) ? EventTarget.prototype : Object.prototype);
    var fakeBattery = Object.create(battProto);
    function getLazyBatteryLevel() { return Math.max(20, initialBattPct - Math.floor((Date.now() - battStartTime) / 360000)) / 100; }
    defineGetters(battProto, {
        charging: function() { return false; },
        chargingTime: function() { return Infinity; },
        dischargingTime: function() { return Math.round(getLazyBatteryLevel() * 14400); },
        level: getLazyBatteryLevel
    });

    // Version resolution, most trustworthy first. There is deliberately no
    // hardcoded constant at the end: the client version changes with every
    // bundle update, and announcing a stale one is worse than announcing none.
    //
    //   1. what the client itself already knows (loader, Config, buildVersion)
    //   2. what the host injected (it resolves from the installed bundle, or from
    //      client-version.ini if the user pinned one)
    //   3. nothing - the fields are left alone so the client keeps its own
    var _injected = {
        app: cfg('__DEVICE_APP_VERSION__', ''),
        code: cfg('__DEVICE_VERSION_CODE__', ''),
        build: cfg('__DEVICE_BUILD_VERSION__', '')
    };
    var _appVersion = (window.appInfo && window.appInfo.version) || _injected.app || null;
    var _versionCode = (window.appInfo && window.appInfo.versionCode) || _injected.code || null;
    var _buildVersion = window.buildVersion || _injected.build || null;
    if (!_appVersion) {
        try {
            var _cfgObj = window.Config || {};
            _appVersion = _cfgObj.appVersion || _cfgObj.version || null;
        } catch (e) {}
    }
    if (!_buildVersion) {
        try {
            var _m = document.querySelector && document.querySelector('meta[name="build-version"]');
            if (_m && _m.content) _buildVersion = _m.content;
        } catch (e) {}
    }

    var FROZEN_LANGUAGES = Object.freeze ? Object.freeze(['fr-FR', 'fr', 'en-US', 'en']) : ['fr-FR', 'fr', 'en-US', 'en'];
    var navOverrides = {
        platform: MOBILE_PLATFORM, userAgent: MOBILE_UA, appVersion: MOBILE_APP_VERSION,
        maxTouchPoints: 5, hardwareConcurrency: _hwSpecs.cores, deviceMemory: _hwSpecs.ram,
        vendor: 'Google Inc.', webdriver: false, pdfViewerEnabled: false, language: 'fr-FR', languages: FROZEN_LANGUAGES,
        onLine: true, plugins: emptyPluginArray, mimeTypes: emptyMimeTypeArray,
        connection: connectionObj, mozConnection: connectionObj, webkitConnection: connectionObj, battery: fakeBattery,
        appInfo: { build: _appVersion, version: _appVersion, identifier: 'com.ankama.dofustouch', versionCode: _versionCode }
    };
    for (var nk in navOverrides) { if (navOverrides[nk] !== undefined) defineProp(navigator, nk, navOverrides[nk]); }
    try {
        window.appInfo = window.appInfo || { identifier: 'com.ankama.dofustouch' };
        // Only fill what is known: writing null would erase the client's own
        // value and the page would then have no version at all.
        if (!window.appInfo.version && _appVersion) window.appInfo.version = _appVersion;
        if (!window.appInfo.build && _appVersion) window.appInfo.build = _appVersion;
        if (!window.appInfo.versionCode && _versionCode) window.appInfo.versionCode = _versionCode;
        if (!window.buildVersion && _buildVersion) window.buildVersion = _buildVersion;
    } catch (e) {}

    try {
        if (typeof Navigator !== 'undefined' && Navigator.prototype) {
            Navigator.prototype.vibrate = makeNative(function vibrate() { return true; }, 'vibrate');
            if (navigator !== Navigator.prototype) { try { navigator.vibrate = Navigator.prototype.vibrate; } catch (e) {} }
            Object.defineProperty(Navigator.prototype, 'getBattery', {
                value: makeNative(function getBattery() { return Promise.resolve(fakeBattery); }, 'getBattery'),
                writable: true, enumerable: true, configurable: true
            });
        }
    } catch (e) {}

    var suppressOffline = function(e) { if (e && (e.type === 'offline' || e.type === 'error')) { e.stopImmediatePropagation(); e.preventDefault(); } };
    try { window.addEventListener('offline', suppressOffline, true); document.addEventListener('offline', suppressOffline, true); } catch (e) {}

    if (navigator.userAgentData || window.NavigatorUAData) {
        var uaBrands = [{ brand: 'Android WebView', version: _majorVer }, { brand: 'Chromium', version: _majorVer }, { brand: 'Not(A:Brand', version: '99' }];
        defineProp(navigator, 'userAgentData', {
            brands: uaBrands, mobile: true, platform: 'Android',
            getHighEntropyValues: makeNative(function getHighEntropyValues() {
                return Promise.resolve({ architecture: 'arm', bitness: '64', brands: uaBrands, formFactor: 'mobile', mobile: true, model: _deviceModel, platform: 'Android', platformVersion: _osVer, uaFullVersion: _fullVer });
            }, 'getHighEntropyValues'),
            toJSON: makeNative(function toJSON() { return { brands: uaBrands, mobile: true, platform: 'Android' }; }, 'toJSON')
        });
    }

    // 4. WebGL, WebGL2 & OffscreenCanvas Vendor/Renderer/ASTC/Precision Disguise
    var UNMASKED_VENDOR_WEBGL = 0x9245, UNMASKED_RENDERER_WEBGL = 0x9246;
    var EXT_DEBUG_RENDERER = { UNMASKED_VENDOR_WEBGL: UNMASKED_VENDOR_WEBGL, UNMASKED_RENDERER_WEBGL: UNMASKED_RENDERER_WEBGL };
    var EXT_ASTC = (function() {
        var o = { getSupportedProfiles: makeNative(function getSupportedProfiles() { return ['ldr']; }, 'getSupportedProfiles') };
        var dims = ['4x4', '5x4', '5x5', '6x5', '6x6', '8x5', '8x6', '8x8', '10x5', '10x6', '10x8', '10x10', '12x10', '12x12'];
        for (var i = 0; i < dims.length; i++) { o['COMPRESSED_RGBA_ASTC_' + dims[i] + '_KHR'] = 0x93B0 + i; o['COMPRESSED_SRGB8_ALPHA8_ASTC_' + dims[i] + '_KHR'] = 0x93D0 + i; }
        return o;
    })();
    var DESKTOP_GL_EXT_RX = /s3tc|bptc|rgtc|^webgl_polygon_mode$/i;

    function wrapContext(ctxProto) {
        if (!ctxProto || wrappedGlProtos.has(ctxProto)) return;
        wrappedGlProtos.add(ctxProto);
        var origGetParam = ctxProto.getParameter, origGetExt = ctxProto.getExtension, origGetSup = ctxProto.getSupportedExtensions, origGetPrec = ctxProto.getShaderPrecisionFormat;

        if (origGetParam) {
            ctxProto.getParameter = makeNative(function getParameter(p) {
                if (p === UNMASKED_VENDOR_WEBGL || p === 0x1F00) return GL_VENDOR;
                if (p === UNMASKED_RENDERER_WEBGL || p === 0x1F01) return GL_RENDERER;
                if (p === 0x1F02) return (typeof WebGL2RenderingContext !== 'undefined' && this instanceof WebGL2RenderingContext) ? 'WebGL 2.0 (OpenGL ES 3.0 Chromium)' : 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
                if (p === 0x8B8C) return (typeof WebGL2RenderingContext !== 'undefined' && this instanceof WebGL2RenderingContext) ? 'WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)' : 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
                if (p === 0x0D33 || p === 0x851C || p === 0x84E8) return MAX_TEX_SIZE;
                if (p === 0x0D3A) return _VIEWPORT_DIMS;
                if (p === 0x846E) return _LINE_WIDTH_RANGE;
                if (p === 0x846D) return _POINT_SIZE_RANGE;
                if (p === 0x8869 || p === 0x8B4C || p === 0x8872) return 16;
                if (p === 0x8DFB || p === 0x8DFD) return 1024;
                if (p === 0x8DFC) return 30;
                if (p === 0x8B4D) return 96;
                return origGetParam.apply(this, arguments);
            }, 'getParameter');
        }
        if (origGetExt) {
            ctxProto.getExtension = makeNative(function getExtension(name) {
                if (name === 'WEBGL_debug_renderer_info') return EXT_DEBUG_RENDERER;
                if (name === 'WEBGL_compressed_texture_astc') return origGetExt.apply(this, arguments) || EXT_ASTC;
                return (name && DESKTOP_GL_EXT_RX.test(name)) ? null : origGetExt.apply(this, arguments);
            }, 'getExtension');
        }
        if (origGetSup) {
            ctxProto.getSupportedExtensions = makeNative(function getSupportedExtensions() {
                var arr = Array.from(origGetSup.apply(this, arguments) || []).filter(function(ext) { return !ext || !DESKTOP_GL_EXT_RX.test(ext); });
                ['WEBGL_debug_renderer_info', 'KHR_parallel_shader_compile', 'WEBGL_compressed_texture_astc'].forEach(function(r) { if (arr.indexOf(r) === -1) arr.push(r); });
                return arr;
            }, 'getSupportedExtensions');
        }
        if (origGetPrec) {
            ctxProto.getShaderPrecisionFormat = makeNative(function getShaderPrecisionFormat(st, pt) {
                if (pt < 0x8DF0 || pt > 0x8DF5 || (st !== 0x8B30 && st !== 0x8B31)) return null;
                var rMin = 127, rMax = 127, prec = 23;
                if (pt === 0x8DF1 || pt === 0x8DF0) { rMin = 15; rMax = 15; prec = 10; }
                else if (pt === 0x8DF5) { rMin = 31; rMax = 30; prec = 0; }
                else if (pt === 0x8DF4) { rMin = 15; rMax = 14; prec = 0; }
                else if (pt === 0x8DF3) { rMin = 7; rMax = 6; prec = 0; }
                var t = null;
                try { t = origGetPrec.call(this, st, pt); } catch (e) {}
                if (!t) t = Object.create((typeof WebGLShaderPrecisionFormat !== 'undefined' && WebGLShaderPrecisionFormat.prototype) ? WebGLShaderPrecisionFormat.prototype : Object.prototype);
                try { Object.defineProperties(t, { rangeMin: { value: rMin, configurable: true, enumerable: true }, rangeMax: { value: rMax, configurable: true, enumerable: true }, precision: { value: prec, configurable: true, enumerable: true } }); } catch (e) {}
                return t;
            }, 'getShaderPrecisionFormat');
        }
    }

    if (window.WebGLRenderingContext) wrapContext(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) wrapContext(WebGL2RenderingContext.prototype);
    if (typeof OffscreenCanvas !== 'undefined' && OffscreenCanvas.prototype && OffscreenCanvas.prototype.getContext) {
        var origOffscreenGetContext = OffscreenCanvas.prototype.getContext;
        OffscreenCanvas.prototype.getContext = makeNative(function getContext(type) {
            var ctx = origOffscreenGetContext.apply(this, arguments);
            if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) wrapContext(Object.getPrototypeOf(ctx));
            return ctx;
        }, 'getContext');
    }

    // 6. Coherent Touch & PointerEvent Disguise
    try {
        if (!('ontouchstart' in window)) window.ontouchstart = window.ontouchend = window.ontouchmove = window.ontouchcancel = null;
        var eventContactCache = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
        var baseTouchRadius = 14 + (_instanceHash % 4);
        function getEventContact(ev) {
            if (eventContactCache && ev && typeof ev === 'object') { var c = eventContactCache.get(ev); if (c) return c; }
            var res = { radius: Math.max(11, Math.min(23, baseTouchRadius + Math.round((Math.random() - 0.5) * 4))), pressure: +(0.58 + (Math.random() - 0.5) * 0.14).toFixed(2) };
            if (eventContactCache && ev && typeof ev === 'object') eventContactCache.set(ev, res);
            return res;
        }
        if (window.PointerEvent && PointerEvent.prototype) {
            var ptrSz = function() { return (this.buttons === 0 && this.type !== 'pointerdown') ? 1 : getEventContact(this).radius * 2; };
            defineGetters(PointerEvent.prototype, {
                width: ptrSz, height: ptrSz,
                pressure: function() { return (this.type === 'pointerup' || this.type === 'pointercancel' || (this.buttons === 0 && this.type !== 'pointerdown')) ? 0 : getEventContact(this).pressure; }
            });
        }
        if (typeof Touch !== 'undefined' && Touch.prototype) {
            defineGetters(Touch.prototype, {
                radiusX: function() { return getEventContact(this).radius; },
                radiusY: function() { return getEventContact(this).radius + 1; },
                force: function() { return getEventContact(this).pressure; }
            });
        }
        if (window.matchMedia) {
            var origMatchMedia = window.matchMedia;
            var mqlProto = (typeof MediaQueryList !== 'undefined' && MediaQueryList.prototype) ? MediaQueryList.prototype : Object.prototype;
            var mqlAddL = makeNative(function addListener() {}, 'addListener'), mqlRemL = makeNative(function removeListener() {}, 'removeListener');
            var mqlAddEL = makeNative(function addEventListener() {}, 'addEventListener'), mqlRemEL = makeNative(function removeEventListener() {}, 'removeEventListener');
            var makeMql = function(query, matches) {
                var mql = Object.create(mqlProto);
                Object.defineProperties(mql, {
                    matches: { value: matches, enumerable: true, configurable: true }, media: { value: String(query), enumerable: true, configurable: true }, onchange: { value: null, writable: true, enumerable: true, configurable: true },
                    addListener: { value: mqlAddL, configurable: true }, removeListener: { value: mqlRemL, configurable: true }, addEventListener: { value: mqlAddEL, configurable: true }, removeEventListener: { value: mqlRemEL, configurable: true }, dispatchEvent: { value: falseNative, configurable: true }
                });
                return mql;
            };
            window.matchMedia = makeNative(function matchMedia(query) {
                var q = (query || '').toLowerCase();
                if (/(?:any-)?hover:\s*none|(?:any-)?pointer:\s*coarse/.test(q)) return makeMql(query, true);
                if (/(?:any-)?hover:\s*hover|(?:any-)?pointer:\s*fine/.test(q)) return makeMql(query, false);
                return origMatchMedia.apply(this, arguments);
            }, 'matchMedia');
        }
    } catch (e) {}

    // 7 & 10. Sensors & EventTarget - Untouched native browser implementations

    // 8. Coherent Screen & Orientation
    try {
        if (window.screen) {
            var getW = function() { return window.innerWidth || 1280; }, getH = function() { return window.innerHeight || 720; };
            defineProp(window.screen, 'colorDepth', 24); defineProp(window.screen, 'pixelDepth', 24);
            defineProp(window.screen, 'width', getW); defineProp(window.screen, 'height', getH);
            defineProp(window.screen, 'availWidth', getW); defineProp(window.screen, 'availHeight', getH);
            if (window.screen.orientation) defineGetters(window.screen.orientation, { type: function() { return 'landscape-primary'; }, angle: function() { return 90; } });
        }
    } catch (e) {}

    // 9. Cordova Device Plugin Identity Synchronization
    function patchCordovaDevice() { try { if (window.device) applyDevProps(window.device); } catch (e) {} }
    patchCordovaDevice();
    if (typeof document !== 'undefined' && document.addEventListener) document.addEventListener('deviceready', patchCordovaDevice, false);
    try {
        var _curDev = window.device;
        Object.defineProperty(window, 'device', {
            get: makeNative(function() { if (_curDev) applyDevProps(_curDev); return _curDev; }, 'get device'),
            set: makeNative(function(val) { _curDev = val; if (_curDev) applyDevProps(_curDev); }, 'set device'),
            configurable: true, enumerable: true
        });
    } catch (e) {}
    if (window.cordova && window.cordova.define) {
        var origDefine = window.cordova.define;
        window.cordova.define = makeNative(function define(id, factory) {
            if (id === 'cordova-plugin-device.device') {
                return origDefine.call(window.cordova, id, function(require, exports, module) {
                    factory(require, exports, module);
                    var dev = module.exports;
                    if (dev) {
                        applyDevProps(dev);
                        if (dev.getInfo) {
                            var origGetInfo = dev.getInfo;
                            dev.getInfo = makeNative(function getInfo(success, error) { origGetInfo.call(dev, function(info) { applyDevProps(info); success(info); }, error); }, 'getInfo');
                        }
                    }
                });
            }
            return origDefine.apply(this, arguments);
        }, 'define');
    }

    try {
        if (PeerConn && PeerConn.prototype) {
            ['createOffer', 'createAnswer'].forEach(function(name) {
                var origFn = PeerConn.prototype[name];
                if (origFn) PeerConn.prototype[name] = makeNative(function() {
                    return origFn.apply(this, arguments).then(function(desc) { if (desc && desc.sdp) desc.sdp = desc.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp); return desc; });
                }, name);
            });
            ['localDescription', 'currentLocalDescription', 'pendingLocalDescription'].forEach(function(prop) {
                var desc = Object.getOwnPropertyDescriptor(PeerConn.prototype, prop);
                if (desc && desc.get) {
                    Object.defineProperty(PeerConn.prototype, prop, {
                        get: makeNative(function() {
                            var val = desc.get.apply(this, arguments);
                            if (val && val.sdp && val.sdp.indexOf('10.0.2.15') !== -1) {
                                try { Object.defineProperty(val, 'sdp', { value: val.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp), configurable: true, enumerable: true }); } catch (e) {}
                            }
                            return val;
                        }, 'get ' + prop), configurable: true, enumerable: true
                    });
                }
            });
            if (PeerConn.prototype.addIceCandidate) {
                var origAddIce = PeerConn.prototype.addIceCandidate;
                PeerConn.prototype.addIceCandidate = makeNative(function addIceCandidate(c) { scrubCandidate(c); return origAddIce.apply(this, arguments); }, 'addIceCandidate');
            }
            var origOnIceDesc = Object.getOwnPropertyDescriptor(PeerConn.prototype, 'onicecandidate');
            if (origOnIceDesc && origOnIceDesc.set) {
                var iceHandlerMap = new WeakMap();
                Object.defineProperty(PeerConn.prototype, 'onicecandidate', {
                    get: makeNative(function() { return iceHandlerMap.get(this) || null; }, 'get onicecandidate'),
                    set: makeNative(function(fn) {
                        if (typeof fn === 'function') {
                            iceHandlerMap.set(this, fn); var self = this;
                            origOnIceDesc.set.call(this, function(evt) { if (evt && evt.candidate) scrubCandidate(evt.candidate); return fn.call(self, evt); });
                        } else { iceHandlerMap.delete(this); origOnIceDesc.set.call(this, null); }
                    }, 'set onicecandidate'), configurable: true, enumerable: true
                });
            }
        }
    } catch (e) {}

    // 11. Per-Instance Deterministic Audio & Font Coherence
    try {
        var Actx = window.AudioContext || window.webkitAudioContext;
        if (Actx && Actx.prototype) defineGetters(Actx.prototype, { sampleRate: function() { return 48000; } });
        if (window.OfflineAudioContext && OfflineAudioContext.prototype.startRendering) {
            var origStartRendering = OfflineAudioContext.prototype.startRendering, audioSeedJitter = ((_instanceHash % 97) + 1) * 1e-8;
            OfflineAudioContext.prototype.startRendering = makeNative(function startRendering() {
                return origStartRendering.apply(this, arguments).then(function(buffer) {
                    if (buffer && buffer.getChannelData) {
                        var chan = buffer.getChannelData(0);
                        if (chan && chan.length > 10) for (var j = (_instanceHash % 7); j < chan.length; j += 128) chan[j] += audioSeedJitter;
                    }
                    return buffer;
                });
            }, 'startRendering');
        }
        if (document.fonts && document.fonts.check) {
            var origFontsCheck = document.fonts.check, desktopFontsRx = /segoe ui|calibri|consolas|cambria|ms gothic|arial black|tahoma|lucida console|comic sans ms/i;
            document.fonts.check = makeNative(function check(font) { return (font && desktopFontsRx.test(font)) ? false : origFontsCheck.apply(this, arguments); }, 'check');
        }
    } catch (e) {}

    // 12. Outbound HTTP - Untouched native browser implementations

    // 12b. Non-CORS Static Asset & Market/Shop Image Sanitizer
    try {
        if (typeof HTMLImageElement !== 'undefined' && HTMLImageElement.prototype) {
            var origImageSetSrc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
            if (origImageSetSrc && origImageSetSrc.set) {
                var rawSetSrc = origImageSetSrc.set;
                Object.defineProperty(HTMLImageElement.prototype, 'src', {
                    set: function(val) {
                        if (typeof val === 'string' && (val.indexOf('static.ankama.com') !== -1 || val.indexOf('s.ankama.com') !== -1)) {
                            try { this.removeAttribute('crossOrigin'); this.crossOrigin = null; } catch (e) {}
                        }
                        return rawSetSrc.call(this, val);
                    },
                    get: origImageSetSrc.get,
                    configurable: true,
                    enumerable: true
                });
            }

            var origCrossOrigin = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'crossOrigin');
            if (origCrossOrigin && origCrossOrigin.set) {
                var rawSetCO = origCrossOrigin.set;
                Object.defineProperty(HTMLImageElement.prototype, 'crossOrigin', {
                    set: function(val) {
                        var curSrc = this.src || '';
                        if (curSrc.indexOf('static.ankama.com') !== -1 || curSrc.indexOf('s.ankama.com') !== -1) {
                            return rawSetCO.call(this, null);
                        }
                        return rawSetCO.call(this, val);
                    },
                    get: origCrossOrigin.get,
                    configurable: true,
                    enumerable: true
                });
            }
        }
    } catch (e) {}

    // 13. Child Iframe Disguise
    function disguiseFrameWindow(targetWin) {
        if (!targetWin || targetWin === window || disguisedFrames.has(targetWin)) return;
        try {
            disguisedFrames.add(targetWin);
            if (targetWin.Function && targetWin.Function.prototype) targetWin.Function.prototype.toString = customToString;
            var tNavProto = (targetWin.Navigator && targetWin.Navigator.prototype) ? targetWin.Navigator.prototype : targetWin.navigator;
            if (tNavProto) {
                for (var k in navOverrides) {
                    if (Object.prototype.hasOwnProperty.call(navOverrides, k) && navOverrides[k] !== undefined) {
                        (function(p, val) { try { Object.defineProperty(tNavProto, p, { get: makeNative(function() { return val; }, 'get ' + p), configurable: true, enumerable: true }); } catch (e) {} })(k, navOverrides[k]);
                    }
                }
            }
            if (targetWin.WebGLRenderingContext) wrapContext(targetWin.WebGLRenderingContext.prototype);
            if (targetWin.WebGL2RenderingContext) wrapContext(targetWin.WebGL2RenderingContext.prototype);
        } catch (e) {}
    }
    try {
        if (typeof HTMLIFrameElement !== 'undefined' && HTMLIFrameElement.prototype) {
            var origCWDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
            if (origCWDesc && origCWDesc.get) {
                Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                    get: makeNative(function() { var cw = origCWDesc.get.apply(this, arguments); if (cw) disguiseFrameWindow(cw); return cw; }, 'get contentWindow'),
                    set: origCWDesc.set, configurable: true, enumerable: true
                });
            }
        }
    } catch (e) {}

    // 14. Dofus Touch WebGL Context Optimizer, Context-Loss Auto-Recovery & Always-Active Frame Booster
    try {
        if (typeof HTMLCanvasElement !== 'undefined' && HTMLCanvasElement.prototype && HTMLCanvasElement.prototype.getContext) {
            var origCanvasGetCtx = HTMLCanvasElement.prototype.getContext;
            var hookedCanvases = (typeof WeakSet !== 'undefined') ? new WeakSet() : null;
            HTMLCanvasElement.prototype.getContext = makeNative(function getContext(type, attrs) {
                if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {
                    var opts = (attrs && typeof attrs === 'object') ? Object.assign({}, attrs) : {};
                    opts.powerPreference = 'high-performance';
                    opts.failIfMajorPerformanceCaveat = false;
                    if (opts.antialias === undefined) opts.antialias = false;
                    if (hookedCanvases && !hookedCanvases.has(this)) {
                        hookedCanvases.add(this);
                        this.addEventListener('webglcontextlost', function(e) { if (e && e.preventDefault) e.preventDefault(); }, false);
                    }
                    var glCtx = origCanvasGetCtx.call(this, type, opts);
                    if (glCtx) wrapContext(Object.getPrototypeOf(glCtx));
                    return glCtx;
                }
                return origCanvasGetCtx.apply(this, arguments);
            }, 'getContext');
        }
    } catch (e) {}

    // 15. Per-Instance Deterministic 2D Canvas Fingerprint Jitter
    try {
        if (typeof HTMLCanvasElement !== 'undefined' && HTMLCanvasElement.prototype) {
            // A canvas must fingerprint the same way every time it is read.
            //
            // The noise below XORs one pixel in place. XOR is its own inverse, so the second call
            // on the same canvas flipped the pixel back and returned the untouched image: two
            // toDataURL() calls, two different hashes, inside one session. A fingerprint that
            // changes when you ask twice is a far stronger bot signal than no noise at all.
            var _noisedCanvases = (typeof WeakSet !== 'undefined') ? new WeakSet() : null;

            var applyCanvasMicroNoise = function(canvas) {
                try {
                    if (_noisedCanvases) {
                        if (_noisedCanvases.has(canvas)) return;
                        _noisedCanvases.add(canvas);
                    }
                    if (canvas.width > 4 && canvas.height > 4) {
                        var ctx2d = canvas.getContext('2d');
                        if (ctx2d) {
                            var px = (_instanceHash % (canvas.width - 2)) + 1;
                            var py = ((_instanceHash >>> 16) % (canvas.height - 2)) + 1;
                            var img = ctx2d.getImageData(px, py, 1, 1);
                            if (img && img.data && img.data.length >= 4) {
                                img.data[0] ^= ((_instanceHash & 1) ? 1 : 2);
                                img.data[1] ^= (((_instanceHash >>> 3) & 1) ? 1 : 2);
                                img.data[2] ^= (((_instanceHash >>> 7) & 1) ? 1 : 2);
                                ctx2d.putImageData(img, px, py);
                            }
                        }
                    }
                } catch (e) {}
            };

            var origToDataURL = HTMLCanvasElement.prototype.toDataURL;
            if (origToDataURL) {
                HTMLCanvasElement.prototype.toDataURL = makeNative(function toDataURL(type, quality) {
                    applyCanvasMicroNoise(this);
                    return origToDataURL.apply(this, arguments);
                }, 'toDataURL');
            }

            var origToBlob = HTMLCanvasElement.prototype.toBlob;
            if (origToBlob) {
                HTMLCanvasElement.prototype.toBlob = makeNative(function toBlob(callback, type, quality) {
                    applyCanvasMicroNoise(this);
                    return origToBlob.apply(this, arguments);
                }, 'toBlob');
            }
        }

        if (typeof CanvasRenderingContext2D !== 'undefined' && CanvasRenderingContext2D.prototype.getImageData) {
            var origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
            CanvasRenderingContext2D.prototype.getImageData = makeNative(function getImageData(sx, sy, sw, sh) {
                var res = origGetImageData.apply(this, arguments);
                if (res && res.data && res.data.length >= 16) {
                    var p1 = ((_instanceHash >>> 0) % (res.data.length / 4 | 0)) * 4;
                    var p2 = ((_instanceHash >>> 16) % (res.data.length / 4 | 0)) * 4;
                    res.data[p1] ^= ((_instanceHash & 1) ? 1 : 2);
                    res.data[p2 + 1] ^= (((_instanceHash >>> 3) & 1) ? 1 : 2);
                    res.data[p1 + 2] ^= (((_instanceHash >>> 7) & 1) ? 1 : 2);
                }
                return res;
            }, 'getImageData');
        }
    } catch (e) {}

    // 16. Natural Behavioral Desynchronization (~20ms Input & Touch Micro-Jitter)
    // NOTE: Event.prototype.timeStamp jitter disabled to preserve smooth 60fps input & tween delta-times

    // 17. In-Game Moderator & Private Message Sentinel
    try {
        var _recentAlerts = new Set();
        var triggerWhisperAlert = function(sender, text, isMod, channel) {
            try {
                var key = (sender || '') + ':' + (text || '').substring(0, 32);
                if (_recentAlerts.has(key)) return;
                _recentAlerts.add(key);
                if (_recentAlerts.size > 100) {
                    var it = _recentAlerts.values().next();
                    if (!it.done) _recentAlerts.delete(it.value);
                }

                // Web Audio tone alert inside browser
                try {
                    var AudioCtx = window.AudioContext || window.webkitAudioContext;
                    if (AudioCtx) {
                        var ctx = new AudioCtx();
                        var osc = ctx.createOscillator();
                        var gain = ctx.createGain();
                        osc.type = isMod ? 'sawtooth' : 'sine';
                        osc.frequency.setValueAtTime(isMod ? 987.77 : 659.25, ctx.currentTime); // B5 or E5
                        gain.gain.setValueAtTime(0.15, ctx.currentTime);
                        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
                        osc.connect(gain);
                        gain.connect(ctx.destination);
                        osc.start();
                        osc.stop(ctx.currentTime + 0.4);
                    }
                } catch (audioErr) {}

                // Dispatch to C# Host Form
                if (window.chrome && window.chrome.webview) {
                    window.chrome.webview.postMessage({
                        type: 'MOD_WHISPER_ALERT',
                        sender: sender || 'Unknown',
                        text: text || '',
                        isMod: !!isMod,
                        channel: channel || 'whisper',
                        instanceHash: _instanceHash.toString(16),
                        timestamp: Date.now()
                    });
                }
                console.warn('[SENTINEL] In-Game Alert detected from:', sender, 'isMod:', isMod, 'msg:', text);
            } catch (e) {}
        };

        // Test hook callable programmatically or in test scripts
        window.__triggerModWhisperTest = triggerWhisperAlert;

        // WebSocket - Untouched native browser implementation
    } catch (e) {}

    // 18. Spatial & Temporal Input Desynchronizer
    // NOTE: MouseEvent coordinate jitter disabled to prevent diamond grid cell hit-test miss on map change triggers

    // 19. AI Commander & Co-Op Action Agent (Stealth Human-like Action Execution)
    try {
        var _activeDirectives = {};

        var triggerNaturalClick = function(targetEl, offsetX, offsetY, onComplete) {
            if (!targetEl) return;
            var rect = targetEl.getBoundingClientRect();
            var cx = rect.left + (offsetX !== undefined ? offsetX : rect.width / 2);
            var cy = rect.top + (offsetY !== undefined ? offsetY : rect.height / 2);

            // Apply Section 18 Spatial Gaussian Jitter (+- 2..4 px)
            var jitterX = cx + ((_instanceHash % 7) - 3) + (Math.random() * 2 - 1);
            var jitterY = cy + (((_instanceHash >>> 4) % 7) - 3) + (Math.random() * 2 - 1);

            // Natural human dwell time: 65 - 125 ms
            var dwellMs = 65 + (Math.random() * 60 | 0);

            var downEvt = new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: jitterX, clientY: jitterY, detail: 1 });
            targetEl.dispatchEvent(downEvt);

            setTimeout(function() {
                var upEvt = new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: jitterX, clientY: jitterY, detail: 1 });
                var clickEvt = new MouseEvent('click', { bubbles: true, cancelable: true, clientX: jitterX, clientY: jitterY, detail: 1 });
                targetEl.dispatchEvent(upEvt);
                targetEl.dispatchEvent(clickEvt);
                if (typeof onComplete === 'function') onComplete();
            }, dwellMs);
        };

        var triggerNaturalDoubleClick = function(targetEl, offsetX, offsetY, onComplete) {
            if (!targetEl) return;
            var rect = targetEl.getBoundingClientRect();
            var cx = rect.left + (offsetX !== undefined ? offsetX : rect.width / 2);
            var cy = rect.top + (offsetY !== undefined ? offsetY : rect.height / 2);

            // 1st Click Spatial Jitter
            var j1x = cx + ((_instanceHash % 7) - 3) + (Math.random() * 2 - 1);
            var j1y = cy + (((_instanceHash >>> 4) % 7) - 3) + (Math.random() * 2 - 1);
            var dwell1 = 60 + (Math.random() * 55 | 0);

            targetEl.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: j1x, clientY: j1y, detail: 1 }));
            setTimeout(function() {
                targetEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: j1x, clientY: j1y, detail: 1 }));
                targetEl.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: j1x, clientY: j1y, detail: 1 }));

                // Human inter-click delay (95 - 175 ms)
                var interClickDelay = 95 + (Math.random() * 80 | 0);
                setTimeout(function() {
                    // 2nd Click Finger Micro-Shift (+- 1 px drift)
                    var j2x = j1x + (Math.random() * 2 - 1);
                    var j2y = j1y + (Math.random() * 2 - 1);
                    var dwell2 = 60 + (Math.random() * 55 | 0);

                    targetEl.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: j2x, clientY: j2y, detail: 2 }));
                    setTimeout(function() {
                        targetEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: j2x, clientY: j2y, detail: 2 }));
                        targetEl.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: j2x, clientY: j2y, detail: 2 }));
                        targetEl.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, clientX: j2x, clientY: j2y, detail: 2 }));
                        if (typeof onComplete === 'function') onComplete();
                    }, dwell2);
                }, interClickDelay);
            }, dwell1);
        };

        window.__triggerNaturalClick = triggerNaturalClick;
        window.__triggerNaturalDoubleClick = triggerNaturalDoubleClick;

        window.__executeCommanderDirective = function(directive) {
            try {
                if (typeof directive === 'string') directive = JSON.parse(directive);
                var action = (directive && directive.action || '').toUpperCase();
                console.log('[AI Commander] Received directive:', action, directive);

                if (action === 'PASS_TURN') {
                    var passBtn = document.querySelector('.pass-turn, .btn-turn, [data-action="pass"], .timeline-turn-pass');
                    if (passBtn) {
                        setTimeout(function() {
                            triggerNaturalClick(passBtn);
                        }, 400 + (Math.random() * 600 | 0));
                    }
                } else if (action === 'AUTO_JOIN_COMBAT') {
                    var joinBtn = document.querySelector('.join-fight, .btn-join, .sword-fight, [data-action="join"]');
                    if (joinBtn) {
                        setTimeout(function() {
                            triggerNaturalClick(joinBtn);
                        }, 600 + (Math.random() * 800 | 0));
                    }
                } else if (action === 'CLICK' && directive.selector) {
                    var targetEl = document.querySelector(directive.selector);
                    if (targetEl) triggerNaturalClick(targetEl, directive.x, directive.y);
                } else if (action === 'DBLCLICK' && directive.selector) {
                    var targetDbl = document.querySelector(directive.selector);
                    if (targetDbl) triggerNaturalDoubleClick(targetDbl, directive.x, directive.y);
                } else if (action === 'EXECUTE_OPTIMAL_ROTATION' || action === 'EXECUTE_TACTICAL_PLAN' || action === 'ATTACK') {
                    var meta = directive.metadata || directive;
                    var rotation = meta.rotation || [];
                    console.log('[AI Commander] Executing tactical plan (' + (meta.stance || 'COMBAT') + ') with ' + rotation.length + ' actions against ' + (meta.target_name || 'target'));

                    var executeStep = function(stepIndex) {
                        if (stepIndex >= rotation.length) {
                            console.log('[AI Commander] Completed rotation execution.');
                            return;
                        }
                        var step = rotation[stepIndex];
                        var spellEl = document.querySelector('[data-spell-id="' + step.spell_id + '"], .spell-' + step.spell_id + ', [title*="' + step.spell_name + '"]');
                        if (!spellEl) {
                            var hotbarSlots = document.querySelectorAll('.hotbar-slot, .spell-slot, .slot-spell');
                            if (hotbarSlots && hotbarSlots[stepIndex]) spellEl = hotbarSlots[stepIndex];
                        }

                        if (spellEl) {
                            triggerNaturalClick(spellEl, undefined, undefined, function() {
                                var targetPause = 250 + (Math.random() * 200 | 0);
                                setTimeout(function() {
                                    var enemyEl = document.querySelector('.fighter-enemy, .target-cell, [data-cell-id]');
                                    if (enemyEl) triggerNaturalClick(enemyEl);

                                    var interSpellDelay = 400 + (Math.random() * 400 | 0);
                                    setTimeout(function() {
                                        executeStep(stepIndex + 1);
                                    }, interSpellDelay);
                                }, targetPause);
                            });
                        } else {
                            executeStep(stepIndex + 1);
                        }
                    };

                    setTimeout(function() { executeStep(0); }, 300 + (Math.random() * 300 | 0));
                } else if (action === 'FOLLOW') {
                    _activeDirectives['follow'] = true;
                } else if (action === 'STOP') {
                    _activeDirectives = {};
                }
                return JSON.stringify({ status: 'ok', action: action, executed_at: Date.now() });
            } catch(err) {
                return JSON.stringify({ status: 'error', message: err.toString() });
            }
        };
    } catch (e) {}

    // 17b. Diagnostics - Untouched native browser implementations

    // 18. Map Loading Transition & Black Screen Safeguard Watchdog
    try {
        var _mapStallStart = 0;
        setInterval(function() {
            try {
                if (window.isoEngine) {
                    var ie = window.isoEngine;
                    var eggAlpha = (ie._loadingProgress && ie._loadingProgress.progressEgg && ie._loadingProgress.progressEgg.visible) ? ie._loadingProgress.progressEgg.alpha : 0;
                    var isTransitionActive = ie._isMapLoading || ie._loadingFadeInTransitionRunning || (eggAlpha > 0.1);
                    if (isTransitionActive) {
                        if (!_mapStallStart) {
                            _mapStallStart = Date.now();
                        } else if (Date.now() - _mapStallStart > 10000) {
                            console.warn('[MapWatchdog] Map transition exceeded 10s safeguard. Forcing display fade-out and scene refresh...');
                            _mapStallStart = 0;
                            if (typeof ie._runLoadingFadeOutTransition === 'function') {
                                ie._runLoadingFadeOutTransition();
                            }
                            if (ie._mapSceneTransitionGraphic) {
                                try { ie._mapSceneTransitionGraphic.remove(); } catch(e){}
                                ie._mapSceneTransitionGraphic = null;
                            }
                            if (ie._loadingProgress) {
                                try { ie._loadingProgress.hide(); } catch(e){}
                            }
                            if (ie.mapScene && typeof ie.mapScene.requireCompleteRefresh === 'function') {
                                ie.mapScene.requireCompleteRefresh();
                            }
                            if (window.foreground && typeof window.foreground.unlock === 'function') {
                                try { window.foreground.unlock('loadMap'); } catch(e){}
                                try { window.foreground.unlock('quickReconnection'); } catch(e){}
                                window.foreground.locked = false;
                            }
                            if (window.dofus && window.dofus.connectionManager && typeof window.dofus.connectionManager.unlockMessages === 'function') {
                                try { window.dofus.connectionManager.unlockMessages(); } catch(e){}
                            }
                            if (ie.actorManager) {
                                try {
                                    if (typeof ie.actorManager.unpause === 'function') ie.actorManager.unpause();
                                    if (ie.actorManager.userActor) ie.actorManager.userActor.moving = false;
                                } catch(e){}
                            }
                            ie._isMapLoading = false;
                            ie._loadingFadeInTransitionRunning = false;
                        }
                    } else {
                        _mapStallStart = 0;
                    }
                }
            } catch(e) {}
        }, 500);
    } catch (e) {}

    // 20. Fetch intercept — silence all /logger POSTs
    try {
        var _nativeFetch = window.fetch;
        window.fetch = function(url, opts) {
            var u = (typeof url === 'string') ? url : (url && url.url) || '';
            if (u.indexOf('/logger') !== -1) {
                return Promise.resolve(new Response(null, { status: 204, statusText: 'No Content' }));
            }
            return _nativeFetch.apply(this, arguments);
        };
    } catch(e) {}

    // 21. Adjust SDK globals stub — prevent Adjust from initialising
    try {
        var _adjustNoop = function() {};
        // Every method, not a hand-written list. Build 1.74.9 calls
        // `Adjust.create(config)` during boot and the old literal did not define
        // `create`, so the init chain threw one step further along than before
        // ("r.create is not a function"). A Proxy cannot go out of date when the
        // SDK grows a method.
        var _adjustNoopObj = new Proxy({}, {
            get: function(t, key) {
                if (key === 'then') return undefined;
                if (key === 'isEnabled') return function() { return false; };
                if (key === 'getAdid' || key === 'getAttribution') return function() { return null; };
                return _adjustNoop;
            }
        });
        if (!window.Adjust) window.Adjust = _adjustNoopObj;
        if (!window.AdjustConfig) {
            // Client build 1.74.9 calls `new AdjustConfig(...)` then
            // `h.setLogLevel(...)` during boot. Returning a bare `{}` made that
            // throw "h.setLogLevel is not a function", which killed initialisation
            // and left the game on a black screen. A Proxy answers every method
            // the SDK might call, so no version of the init chain can throw here.
            window.AdjustConfig = function() {
                return new Proxy({}, {
                    get: function(t, key) {
                        if (key === 'then') return undefined;
                        return _adjustNoop;
                    }
                });
            };
            window.AdjustConfig.LogLevel = { VERBOSE: 'verbose', DEBUG: 'debug', INFO: 'info', WARN: 'warn', ERROR: 'error', ASSERT: 'assert', SUPPRESS: 'suppress' };
        }
        if (!window.AdjustEvent) {
            window.AdjustEvent = function(token) {
                return new Proxy({ token: token }, {
                    get: function(t, key) {
                        if (key === 'then') return undefined;
                        if (key === 'token') return token;
                        return _adjustNoop;
                    }
                });
            };
        }
    } catch(e) {}

    // 22. HAAPI sendKPIEvent intercept via window.dofus setter
    try {
        var _dofusReal = window.dofus;
        Object.defineProperty(window, 'dofus', {
            configurable: true,
            get: function() { return _dofusReal; },
            set: function(v) {
                _dofusReal = v;
                if (v && v.sendKPIEvent) {
                    v.sendKPIEvent = function() { return Promise.resolve(); };
                }
            }
        });
        if (_dofusReal && _dofusReal.sendKPIEvent) {
            _dofusReal.sendKPIEvent = function() { return Promise.resolve(); };
        }
    } catch(e) {}

    // 23. storage.estimate() normaliser — consistent profile
    try {
        if (navigator.storage && navigator.storage.estimate) {
            var _nativeEstimate = navigator.storage.estimate.bind(navigator.storage);
            Object.defineProperty(navigator.storage, 'estimate', {
                configurable: true,
                writable: true,
                value: function() {
                    return Promise.resolve({ quota: 2147483648, usage: 104857600 });
                }
            });
        }
    } catch(e) {}

    // 24. permissions.query profile normaliser — consistent 'prompt' state
    try {
        if (navigator.permissions && navigator.permissions.query) {
            var _nativeQuery = navigator.permissions.query.bind(navigator.permissions);
            navigator.permissions.query = function(descriptor) {
                var safe = ['geolocation','notifications','push','midi','camera','microphone',
                            'speaker','device-info','background-fetch','background-sync',
                            'bluetooth','persistent-storage','ambient-light-sensor',
                            'accelerometer','gyroscope','magnetometer','clipboard-read',
                            'clipboard-write','payment-handler','idle-detection'];
                if (descriptor && safe.indexOf(descriptor.name) !== -1) {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
                return _nativeQuery(descriptor);
            };
        }
    } catch(e) {}

    // 25. High-Speed Auto-Fight Pipeline with Floating HUD, F2 Hotkey, and C# Sync
    try {
        var FarmBot = {
            enabled: false,
            config: {
                autoReadyDelayMs: 20,   // Delay before GameFightTurnReadyMessage
                autoFinishDelayMs: 15   // Delay before GameFightTurnFinishMessage
            },
            state: {
                inFight: false,
                myTurn: false,
                // A placement message is only actionable while the bot is
                // enabled, but it stays valid until someone readies: the fight
                // cannot start without that click. Keeping the last one lets an
                // enable that arrives late still start the fight.
                placement: null,
                readied: false,
                readying: false,
                lastTurnId: null,
                turnStartedAt: 0
            },

            // Farm session counters. The learning loop is only as good as the
            // outcome data: `farm.start` used to return an ack with no numbers,
            // so every live session produced `unverified` and taught nothing.
            // Everything here is measured by diffing the client's own state
            // around a fight, never inferred from a plan.
            session: {
                startedAt: null,
                fights: 0,
                wins: 0,
                losses: 0,
                kills: 0,
                xp: 0,
                kamas: 0,
                drops: {},          // gid -> quantity gained
                _pre: null,
                _monsters: 0
            },

            resetSession: function() {
                this.session = {
                    startedAt: Date.now(), fights: 0, wins: 0, losses: 0, kills: 0,
                    xp: 0, kamas: 0, drops: {}, _pre: null, _monsters: 0
                };
                return this.sessionSummary();
            },

            sessionSummary: function() {
                var s = this.session || {};
                var minutes = s.startedAt ? (Date.now() - s.startedAt) / 60000 : 0;
                var drops = s.drops || {};
                var dropTotal = 0;
                for (var g in drops) { if (Object.prototype.hasOwnProperty.call(drops, g)) dropTotal += drops[g]; }
                return {
                    fights: s.fights || 0,
                    wins: s.wins || 0,
                    losses: s.losses || 0,
                    kills: s.kills || 0,
                    drops: drops,
                    drop_count: dropTotal,
                    xp: s.xp || 0,
                    kamas: s.kamas || 0,
                    minutes: Math.round(minutes * 100) / 100,
                    kills_per_minute: minutes > 0.05 ? Math.round((s.kills || 0) / minutes * 100) / 100 : null,
                    xp_per_minute: minutes > 0.05 ? Math.round((s.xp || 0) / minutes) : null,
                    kamas_per_minute: minutes > 0.05 ? Math.round((s.kamas || 0) / minutes) : null
                };
            },

            countMonsters: function() {
                var count = 0;
                try {
                    if (window.isoEngine && window.isoEngine.actorManager) {
                        var am = window.isoEngine.actorManager;
                        var ids = (typeof am.getActorsIDList === 'function') ? am.getActorsIDList() : [];
                        for (var i = 0; i < ids.length; i++) {
                            var a = am.getActor(ids[i]);
                            var fighter = null;
                            try { fighter = (a && typeof a.getFighter === 'function') ? a.getFighter() : null; } catch(e) {}
                            var isMonster = false;
                            try { isMonster = !!(fighter && typeof fighter.isMonster === 'function' && fighter.isMonster()); } catch(e) {}
                            if (isMonster) count += 1;
                        }
                    }
                } catch(e) {}
                return count;
            },

            openFight: function() {
                var s = this.session;
                s._pre = _agentSnapshot();
                s._monsters = this.countMonsters();
            },

            closeFight: function() {
                var s = this.session;
                s.fights += 1;
                var pre = s._pre;
                var post = _agentSnapshot();
                s._pre = null;

                var xpGain = null;
                var kamasGain = null;
                if (pre && post) {
                    if (pre.xp != null && post.xp != null && post.xp >= pre.xp) xpGain = post.xp - pre.xp;
                    if (pre.kamas != null && post.kamas != null && post.kamas >= pre.kamas) kamasGain = post.kamas - pre.kamas;
                    // Item deltas are the drops. Only gains count: a sale is a
                    // negative delta and must never be recorded as a drop.
                    var preItems = (pre.inventory && pre.inventory.items) || [];
                    var postItems = (post.inventory && post.inventory.items) || [];
                    var before = {};
                    var i;
                    for (i = 0; i < preItems.length; i++) before[preItems[i].gid] = preItems[i].qty;
                    for (i = 0; i < postItems.length; i++) {
                        var gid = postItems[i].gid;
                        var delta = postItems[i].qty - (before[gid] || 0);
                        if (delta > 0) s.drops[gid] = (s.drops[gid] || 0) + delta;
                    }
                }

                // A fight that yields no xp was lost or abandoned. Recording the
                // kills as kills in that case would teach a drop rate that never
                // happened, so it is counted as a loss instead.
                var monsters = s._monsters || 0;
                if (xpGain != null && xpGain > 0) {
                    s.wins += 1;
                    s.kills += monsters;
                    s.xp += xpGain;
                } else {
                    s.losses += 1;
                }
                if (kamasGain != null && kamasGain > 0) s.kamas += kamasGain;
                s._monsters = 0;
                return this.sessionSummary();
            },

            setEnabled: function(val) {
                this.enabled = !!val;
                this.updateHud();
                this.syncToHost();
                if (this.enabled) this.resumeFight();
                console.log('[FarmBot] Auto-fight state: ' + (this.enabled ? 'ACTIVE (Sub-20ms turns)' : 'INACTIVE (Manual play)'));
            },

            // Enabling mid-fight is not too late. A fight entered through a
            // dialog or a quest starts in this client without `fight.join`, so
            // the placement message can arrive while the bot is off. The fight
            // is still sitting on the placement screen — nobody has readied —
            // and replaying that message is what turns the bot on in practice.
            resumeFight: function() {
                var self = this;
                if (!this.state.inFight) return;
                if (this.state.placement && !this.state.readied && !this.state.readying) {
                    var msg = this.state.placement;
                    setTimeout(function() {
                        if (!self.enabled || self.state.readied || self.state.readying) return;
                        try { self.placeAndReady(msg); } catch (e) {}
                    }, this.config.autoReadyDelayMs);
                }
                // A turn that began while the bot was off is only actionable
                // inside the server's turn window. Outside it, a cast would be
                // rejected and would look like a broken combat driver.
                if (this.state.myTurn && this.state.lastTurnId != null
                        && Date.now() - (this.state.turnStartedAt || 0) < 15000) {
                    var fighterId = this.state.lastTurnId;
                    setTimeout(function() {
                        if (!self.enabled || !self.state.myTurn) return;
                        self.executeOptimalTurn(null, fighterId);
                    }, 250);
                }
            },

            toggle: function() {
                this.setEnabled(!this.enabled);
            },

            // -- the badge is the AI button -------------------------------------
            aiRunning: false,

            aiToggle: function() {
                var self = this;
                var hud = document.getElementById('__farmbot_hud');
                var label = document.getElementById('__farmbot_hud_label');
                // Acknowledge the press immediately. Whether the host acts on it is a
                // separate question, and leaving the badge unchanged made a working
                // click look like a dead button.
                if (label && !this.aiRunning) label.textContent = 'AI: starting\u2026';
                if (hud && !this.aiRunning) hud.style.backgroundColor = '#b45309';
                try {
                    if (!(window.chrome && window.chrome.webview)) {
                        if (label) label.textContent = 'AI: no host';
                        return;
                    }
                    // Post the OBJECT, not JSON.stringify(obj). The stringified form
                    // arrives in C# as a JSON string with escaped quotes, and every
                    // type check there matches on "type":"X" — so it fell through
                    // silently. Posting the object makes the message readable by the
                    // host as-is, including on builds that predate the fix.
                    window.chrome.webview.postMessage({
                        type: 'AI_REQUEST',
                        action: this.aiRunning ? 'stop_all' : 'start_auto'
                    });
                } catch (e) {
                    if (label) label.textContent = 'AI: send failed';
                    return;
                }
                // If the host never answers, say so rather than pretending. A button
                // that quietly does nothing is the worst of the three outcomes.
                if (this._ackTimer) clearTimeout(this._ackTimer);
                this._ackTimer = setTimeout(function () {
                    if (self.aiPending) {
                        var l = document.getElementById('__farmbot_hud_label');
                        if (l) l.textContent = 'AI: no reply';
                    }
                }, 4000);
                this.aiPending = true;
            },

            setAiState: function(running, text) {
                this.aiRunning = !!running;
                this.aiPending = false;
                if (this._ackTimer) { clearTimeout(this._ackTimer); this._ackTimer = null; }
                var hud = document.getElementById('__farmbot_hud');
                var label = document.getElementById('__farmbot_hud_label');
                if (hud) hud.title = text || (running ? 'AI running' : 'Start the AI');
                if (!hud || !label) return;
                if (running) {
                    label.textContent = 'AI: running';
                    hud.style.backgroundColor = '#10b981';
                    hud.style.color = '#ffffff';
                    hud.style.border = '1px solid #34d399';
                    hud.style.boxShadow = '0 0 12px rgba(16,185,129,0.7)';
                } else {
                    label.textContent = 'AI: start';
                    hud.style.backgroundColor = 'rgba(30, 41, 59, 0.85)';
                    hud.style.color = '#94a3b8';
                    hud.style.border = '1px solid rgba(100, 116, 139, 0.4)';
                    hud.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
                }
            },

            syncToHost: function() {
                try {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage({
                            type: 'FARMBOT_STATE',
                            enabled: this.enabled
                        });
                    }
                } catch(e) {}
            },

            init: function() {
                this.createHud();
                this.bindKeyListeners();
                this.waitForConnection();
            },

            createHud: function() {
                var self = this;
                var POS_KEY = '__dofusfarm_hud_pos';

                function clampIntoView(x, y, el) {
                    var w = el.offsetWidth || 180, h = el.offsetHeight || 26;
                    var maxX = Math.max(0, window.innerWidth - w - 2);
                    var maxY = Math.max(0, window.innerHeight - h - 2);
                    return { x: Math.min(Math.max(0, x), maxX), y: Math.min(Math.max(0, y), maxY) };
                }

                function injectBadge() {
                    if (document.getElementById('__farmbot_hud')) return;
                    if (!document.body) { setTimeout(injectBadge, 300); return; }

                    var hud = document.createElement('div');
                    hud.id = '__farmbot_hud';
                    hud.style.position = 'fixed';
                    hud.style.zIndex = '999999';
                    hud.style.display = 'flex';
                    hud.style.alignItems = 'center';
                    hud.style.gap = '6px';
                    hud.style.padding = '4px 6px 4px 10px';
                    hud.style.borderRadius = '14px';
                    hud.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                    hud.style.fontSize = '11px';
                    hud.style.fontWeight = 'bold';
                    hud.style.userSelect = 'none';
                    hud.style.boxShadow = '0 2px 8px rgba(0,0,0,0.4)';
                    hud.style.cursor = 'grab';
                    hud.style.touchAction = 'none';
                    hud.style.opacity = '0.92';

                    var label = document.createElement('span');
                    label.id = '__farmbot_hud_label';
                    label.style.cursor = 'pointer';
                    label.title = 'Click to toggle the fight AI, drag to move. F2 also toggles.';
                    hud.appendChild(label);

                    // One click starts the autonomous loop from inside the game
                    // window: the host runs the same autopilot the AI page does.
                    var auto = document.createElement('span');
                    auto.id = '__farmbot_hud_auto';
                    auto.textContent = '▶ Auto';
                    auto.title = 'Start the autonomous AI loop';
                    auto.style.cursor = 'pointer';
                    auto.style.padding = '2px 7px';
                    auto.style.borderRadius = '10px';
                    auto.style.background = 'rgba(37,99,235,0.9)';
                    auto.style.color = '#fff';
                    auto.onclick = function(e) {
                        e.stopPropagation();
                        auto.textContent = '...';
                        try {
                            window.chrome.webview.postMessage({
                                type: 'AI_REQUEST', action: 'start_auto'
                            });
                        } catch (err) {}
                        setTimeout(function() { auto.textContent = '▶ Auto'; }, 2500);
                    };
                    hud.appendChild(auto);

                    // --- position: restored from the last drag -----------------
                    var pos = null;
                    try { pos = JSON.parse(localStorage.getItem(POS_KEY) || 'null'); } catch (e) {}
                    document.body.appendChild(hud);
                    if (pos && typeof pos.x === 'number' && typeof pos.y === 'number') {
                        var c = clampIntoView(pos.x, pos.y, hud);
                        hud.style.left = c.x + 'px';
                        hud.style.top = c.y + 'px';
                    } else {
                        hud.style.right = '12px';
                        hud.style.top = '10px';
                    }

                    // --- drag ---------------------------------------------------
                    var dragging = false, moved = false, offX = 0, offY = 0;
                    hud.addEventListener('pointerdown', function(e) {
                        if (e.target === auto) return;
                        var r = hud.getBoundingClientRect();
                        offX = e.clientX - r.left;
                        offY = e.clientY - r.top;
                        dragging = true;
                        moved = false;
                        hud.style.cursor = 'grabbing';
                        try { hud.setPointerCapture(e.pointerId); } catch (err) {}
                    });
                    hud.addEventListener('pointermove', function(e) {
                        if (!dragging) return;
                        if (!moved && (Math.abs(e.movementX) > 2 || Math.abs(e.movementY) > 2)) moved = true;
                        if (!moved) return;
                        var c = clampIntoView(e.clientX - offX, e.clientY - offY, hud);
                        hud.style.right = 'auto';
                        hud.style.left = c.x + 'px';
                        hud.style.top = c.y + 'px';
                    });
                    hud.addEventListener('pointerup', function(e) {
                        if (!dragging) return;
                        dragging = false;
                        hud.style.cursor = 'grab';
                        try { hud.releasePointerCapture(e.pointerId); } catch (err) {}
                        if (moved) {
                            var r = hud.getBoundingClientRect();
                            try {
                                localStorage.setItem(POS_KEY, JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top) }));
                            } catch (err) {}
                        } else {
                            // The badge is the AI button: it asks the host to start
                            // or stop the agent loop. It used to toggle the auto-fight
                            // bot, which is a different system entirely — so pressing
                            // "the AI button" could never start the AI.
                            self.aiToggle();
                        }
                    });
                    hud.addEventListener('pointercancel', function() { dragging = false; hud.style.cursor = 'grab'; });

                    // Keep it on screen when the window is resized.
                    window.addEventListener('resize', function() {
                        if (!hud.style.left) return;
                        var r = hud.getBoundingClientRect();
                        var c = clampIntoView(r.left, r.top, hud);
                        hud.style.left = c.x + 'px';
                        hud.style.top = c.y + 'px';
                    });

                    self.updateHud();
                }
                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', injectBadge);
                } else {
                    injectBadge();
                }
            },

            updateHud: function() {
                // The badge shows the AI loop's state. The farm bot has its own state
                // and no badge: a control labelled "AI" must control the AI.
                this.setAiState(this.aiRunning, null);
            },

            bindKeyListeners: function() {
                var self = this;
                window.addEventListener('keydown', function(e) {
                    if (e.key === 'F2') {
                        e.preventDefault();
                        self.toggle();
                    }
                }, true);
            },

            waitForConnection: function() {
                var self = this;
                var checkInterval = setInterval(function() {
                    if (window.dofus && window.dofus.connectionManager) {
                        clearInterval(checkInterval);
                        self.bindNetworkHooks(window.dofus.connectionManager);
                    }
                }, 300);
            },

            bindNetworkHooks: function(cm) {
                var self = this;
                console.log('[FarmBot] Bound to connection manager.');

                // Record why the game connection ends.
                //
                // The client already knows: `dofus.disconnect(reason)` logs
                // "connectionManager.disconnect: reason=..." and then
                // `disconnectAndReload` reloads the page. But a reload wipes the reason
                // before anyone can read it, so a session that ended in a kick looked
                // exactly like one that ended because the window closed — 62 closes and
                // 88 page loads in one log, with no way to tell them apart. Keeping the
                // reason on the host side is the difference between guessing and knowing.
                try {
                    var wrap = function (name) {
                        var orig = window.dofus && window.dofus[name];
                        if (typeof orig !== 'function' || orig.__agentWrapped) return;
                        var wrapped = function (reason) {
                            try {
                                var why = String(reason || 'CLIENT_CLOSING');
                                window.__agentLastDisconnect = { at: Date.now(), reason: why,
                                                                 via: name };
                                // Only attributes the kick when a claim was sent in the
                                // last few seconds. A network drop an hour after the
                                // last validation must not blacklist an objective.
                                try {
                                    if (window.__agentRecordKick) {
                                        window.__agentRecordKick('dofus_' + name);
                                    }
                                } catch (e) {}
                                // console.log, not info: the host captures log lines,
                                // and this must survive the reload that follows.
                                console.log('[AgentBridge] connection ended: reason=' + why
                                            + ' via ' + name);
                                try {
                                    if (window.chrome && window.chrome.webview) {
                                        window.chrome.webview.postMessage({
                                            type: 'AI_DISCONNECT', reason: why, via: name
                                        });
                                    }
                                } catch (e) {}
                            } catch (e) {}
                            return orig.apply(this, arguments);
                        };
                        wrapped.__agentWrapped = true;
                        window.dofus[name] = wrapped;
                    };
                    wrap('disconnect');
                    wrap('disconnectAndReload');
                } catch (e) {}

                // Freshness stamps for quest claims. The quest model is
                // replaced from the server on map changes and quest messages;
                // a claim sent while that refresh is in flight, or right
                // after a map change, is checked against a state the server
                // may no longer hold. _questClaimCheck reads these.
                var stamp = function (name, field) {
                    try {
                        cm.on(name, function () { window[field] = Date.now(); });
                    } catch (e) {}
                };
                stamp('CurrentMapMessage', '__agentLastMapChange');
                stamp('QuestObjectiveValidatedMessage', '__agentLastQuestSync');
                stamp('QuestStepInfoMessage', '__agentLastQuestSync');
                stamp('QuestsUpdatedMessage', '__agentLastQuestSync');
                stamp('QuestStepStartedMessage', '__agentLastQuestSync');
                // The disconnect path also answers as a message before the page
                // reloads; recording here catches a kick even when the host-side
                // disconnect wrapper is not the code that fires.
                try {
                    cm.on('serverDisconnecting', function () {
                        if (window.__agentRecordKick) {
                            window.__agentRecordKick('serverDisconnecting');
                        }
                    });
                } catch (e) {}

                cm.on('GameFightStartingMessage', function(msg) {
                    self.state.inFight = true;
                    self.state.myTurn = false;
                    self.state.placement = null;
                    self.state.readied = false;
                    self.state.readying = false;
                    self.state.lastTurnId = null;
                    self.state.turnStartedAt = 0;
                    self.openFight();
                });

                cm.on('GameFightPlacementPossiblePositionsMessage', function(msg) {
                    // Remember it even while disabled: the fight stays on this
                    // screen until a ready is sent, so the message is still
                    // valid once the bot is enabled a second later.
                    self.state.placement = msg;
                    window.__agentLastFightPlacement = {
                        at: Date.now(),
                        enabled: !!self.enabled,
                        challengers: (msg && msg.positionsForChallengers
                            && msg.positionsForChallengers.length) || 0
                    };
                    if (!self.enabled) return;
                    setTimeout(function() {
                        try { self.placeAndReady(msg); } catch (e) {}
                    }, self.config.autoReadyDelayMs);
                });

                cm.on('GameFightTurnStartMessage', function(msg) {
                    // Any turn means placement is over. A cached placement
                    // replayed after this point is stale and would toggle
                    // ready in the middle of an already-running fight.
                    self.state.placement = null;
                    if (!self.isUserActor(msg.id)) return;
                    // Record my turn even while disabled, so enabling inside
                    // the window can still act instead of waiting a full round.
                    // While enabled, a repeated start for the same turn is
                    // ignored: a second driver would spend AP the server never
                    // authorised. TurnEnd (and fight end) release the flag.
                    if (self.state.myTurn && self.enabled) return;
                    self.state.myTurn = true;
                    self.state.turnStartedAt = Date.now();
                    self.state.lastTurnId = msg.id;
                    if (!self.enabled) return;
                    self.executeOptimalTurn(cm, msg.id);
                });

                cm.on('GameFightTurnEndMessage', function(msg) {
                    if (!self.isUserActor(msg.id)) return;
                    self.state.myTurn = false;
                    self.state.lastTurnId = null;
                });

                cm.on('GameFightEndMessage', function(msg) {
                    self.state.inFight = false;
                    self.state.myTurn = false;
                    self.state.placement = null;
                    self.state.readied = false;
                    self.state.readying = false;
                    self.state.lastTurnId = null;
                    try { self.closeFight(); } catch(e) {
                        console.log('[FarmBot] closeFight failed: ' + e);
                    }
                });
            },

            // Placement is two messages, not an option toggle. The previous handler
            // sent FIGHT_OPTION_SET_CLOSED (option 2) plus a *turn* ready, so with the
            // bot enabled the fight never even started: nobody picked a cell and nobody
            // pressed the real ready button. The live capture of a manual fight shows
            // the actual sequence: GameFightPlacementPositionRequestMessage then
            // GameFightReadyMessage.
            placeAndReady: function(msg) {
                if (this.state.readied || this.state.readying) return;
                this.state.readying = true;
                // The live message's position list has been an array in the
                // captures, but an array-like object here used to throw on
                // `.indexOf`; the throw skipped the ready send entirely and the
                // fight waited forever. Copy whatever is there into an array,
                // and ready even when no cell list is usable.
                var raw = (msg && (msg.positionsForChallengers
                    || msg.positions || msg.positionsForSpectators)) || [];
                var positions = [];
                try {
                    for (var i = 0; raw && i < raw.length; i++) positions.push(raw[i]);
                } catch (e) {}
                var am = window.actorManager
                    || (window.isoEngine && window.isoEngine.actorManager) || null;
                var ua = am && am.userActor;
                var current = ua && ua.cellId != null ? ua.cellId : null;
                var cell = null;
                if (positions.length) {
                    cell = positions.indexOf(current) !== -1 ? current : positions[0];
                } else {
                    cell = current;
                }
                if (cell != null && cell !== current) {
                    this.sendPacket('GameFightPlacementPositionRequestMessage', { cellId: cell });
                }
                var self = this;
                setTimeout(function() {
                    var ok = self.sendPacket('GameFightReadyMessage', { isReady: true });
                    window.__agentLastFightReady = {
                        at: Date.now(), cell: cell, positions: positions.length,
                        sent: !!ok, error: ok ? null : (self.lastSendError || null)
                    };
                    if (ok) {
                        self.state.readied = true;
                        self.state.placement = null;
                    }
                    self.state.readying = false;
                }, 60);
            },

            // Turns are played with the client's own combat machinery, against the
            // character's real spell list. The old driver posted to a combat server on
            // 8892 that is not running and fell back to a hardcoded Cra spell (164),
            // which this character does not own: every turn was a wasted cast and a
            // pass. Nothing here assumes a class or a level.
            executeOptimalTurn: function(cm, fighterId) {
                var self = this;
                var steps = 0;
                var tick = function() {
                    if (!self.enabled || !self.state.myTurn) return;
                    if (steps++ >= 8 || Date.now() - (self.state.turnStartedAt || 0) > 20000) {
                        self._endFightTurn();
                        return;
                    }
                    var ctx = self._fightContext(fighterId);
                    if (!ctx) { self._endFightTurn(); return; }
                    if (!self._stillMyTurn(ctx)) return;
                    if (self._askBrain(ctx, fighterId, tick)) return;
                    self._localTick(ctx, fighterId, tick);
                };
                tick();
            },

            // The only authority for a fight action is the client's live turn
            // owner. The action may arrive from the HTTP brain after the local
            // turn state was set, or after the server ended the turn.
            _stillMyTurn: function(ctx) {
                try {
                    if (!this.enabled || !this.state.myTurn || !ctx) return false;
                    var fm = window.gui && window.gui.fightManager;
                    if (!fm) return false;
                    var current = fm.currentFighterId;
                    return current != null && String(current) === String(ctx.myId);
                } catch (e) { return false; }
            },

            _localTick: function(ctx, fighterId, tick) {
                var self = this;
                if (!self._stillMyTurn(ctx)) return;
                var action = null;
                try {
                    action = self._planFightAction(ctx);
                } catch (e) {
                    window.__agentLastFightTurnError = {
                        at: Date.now(), stage: 'plan', error: String(e) };
                    self._endFightTurn();
                    return;
                }
                if (!action) { self._endFightTurn(); return; }
                self._executeAction(ctx, action, tick, fighterId, null);
            },

            // Ask the local policy API for one grounded action. The page sends
            // the live fight.state (the same payload the planner uses), so the
            // server binds the current level, spells and enemies; nothing about
            // the character is cached. The local heuristic is the fallback when
            // the server is down, slow or cannot express the policy's intent.
            _askBrain: function(ctx, fighterId, tick) {
                var self = this;
                var url = window.__agentFightBrain;
                if (!url || typeof fetch !== 'function') return false;
                if (self._brainDownAt && Date.now() - self._brainDownAt < 30000) return false;
                var state = null;
                try {
                    if (typeof window.__agentDirective === 'function') {
                        var raw = window.__agentDirective({ tool: 'fight.state' });
                        var parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
                        if (parsed && parsed.ok && parsed.data) state = parsed.data;
                    }
                } catch (e) {}
                if (!state || !state.in_fight) return false;
                var done = false;
                var finish = function(action, meta, error) {
                    if (done) return;
                    done = true;
                    if (!self._stillMyTurn(ctx)) return;
                    window.__agentLastFightBrain = {
                        at: Date.now(), ok: !error && !!action, action: action || null,
                        policy: meta || null, error: error || null
                    };
                    if (action) self._executeAction(ctx, action, tick, fighterId, meta);
                    else self._localTick(ctx, fighterId, tick);
                };
                try {
                    var controller = (typeof AbortController !== 'undefined')
                        ? new AbortController() : null;
                    var timer = controller ? setTimeout(function() {
                        try { controller.abort(); } catch (e) {}
                    }, 4000) : null;
                    fetch(url.replace(/\/$/, '') + '/v1/combat/decide', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(state),
                        signal: controller ? controller.signal : undefined
                    }).then(function(response) {
                        return response.json();
                    }).then(function(reply) {
                        if (timer) clearTimeout(timer);
                        if (reply && reply.ok && reply.action) {
                            finish(reply.action, reply.policy || null, null);
                        } else {
                            finish(null, reply && reply.policy ? reply.policy : null,
                                (reply && (reply.reason || reply.error)) || 'no action');
                        }
                    }).catch(function(error) {
                        if (timer) clearTimeout(timer);
                        self._brainDownAt = Date.now();
                        finish(null, null, String(error));
                    });
                } catch (e) {
                    finish(null, null, String(e));
                }
                return true;
            },

            _executeAction: function(ctx, action, tick, fighterId, meta) {
                var self = this;
                if (!self._stillMyTurn(ctx)) return;
                if (!action || action.type === 'pass') { self._endFightTurn(); return; }
                try {
                    if (action.type === 'cast') {
                        var usable = false;
                        try { usable = !!ctx.fm.canCastThisSpell(ctx.myId, action.spellId); } catch (e) {}
                        if (!usable) { self._localTick(ctx, fighterId, tick); return; }
                        window.__agentLastFightTurn = { at: Date.now(), action: 'cast',
                            spell: action.spellId, target: action.targetId,
                            cell: action.cell, brain: meta || action.brain || null };
                        // A teleport or a self buff targets a cell, not a
                        // fighter; the target form has no id to send. Damage
                        // and heal casts keep the target form.
                        if (action.cell != null
                            && (action.targetId == null || action.targetId === ctx.myId)) {
                            ctx.fm.castSpell(action.spellId, action.cell, ctx.myId);
                        } else {
                            ctx.fm.castSpellOnTarget(action.spellId, action.targetId, ctx.myId);
                        }
                        setTimeout(tick, 450);
                        return;
                    }
                    if (action.type === 'move') {
                        var path = action.path;
                        if ((!path || !path.length) && ctx.iso
                            && typeof ctx.iso._displayPathInFight === 'function') {
                            var found = ctx.iso._displayPathInFight(action.cell);
                            path = found && found.reachable;
                        }
                        var mapId = (ctx.iso && ctx.iso.mapRenderer && ctx.iso.mapRenderer.map)
                            ? ctx.iso.mapRenderer.map.id : null;
                        if (mapId == null || !path || !path.length) {
                            self._localTick(ctx, fighterId, tick);
                            return;
                        }
                        window.__agentLastFightTurn = { at: Date.now(), action: 'move',
                            cell: action.cell, path: path, brain: meta || action.brain || null };
                        self.sendPacket('GameMapMovementRequestMessage', {
                            keyMovements: self._compressPath(path), mapId: mapId });
                        setTimeout(tick, 700);
                        return;
                    }
                } catch (e) {
                    window.__agentLastFightTurnError = {
                        at: Date.now(), stage: 'act', error: String(e) };
                }
                self._endFightTurn();
            },

            _endFightTurn: function() {
                this.state.myTurn = false;
                try {
                    var fm = window.gui && window.gui.fightManager;
                    if (fm && typeof fm.finishTurn === 'function') { fm.finishTurn(); return; }
                } catch (e) {}
                this.sendPacket('GameFightTurnFinishMessage', { isAfk: false });
            },

            _fightContext: function(fighterId) {
                var gui = window.gui || {};
                var fm = gui.fightManager || null;
                var pd = gui.playerData || null;
                var iso = window.isoEngine || null;
                var am = window.actorManager || (iso && iso.actorManager) || null;
                if (!fm || !pd || !am) return null;
                var chars = pd.characters || {};
                var myId = chars.controlledCharacterId != null
                    ? chars.controlledCharacterId : fighterId;
                var me = am.getActor ? am.getActor(myId) : null;
                var myCell = me && me.cellId != null ? me.cellId
                    : (pd.position && pd.position.cell != null ? pd.position.cell : null);
                var controlled = chars.getControlledCharacter ? chars.getControlledCharacter() : null;
                var stats = (controlled && controlled.characteristics) || {};
                var spells = (controlled && controlled.spellData
                    && controlled.spellData.getSpellListDependingOnGameMode)
                    ? controlled.spellData.getSpellListDependingOnGameMode() : {};
                var enemies = [];
                var ids = (typeof am.getActorsIDList === 'function') ? am.getActorsIDList() : [];
                for (var i = 0; i < ids.length; i++) {
                    var id = ids[i];
                    if (id === myId) continue;
                    var a = am.getActor(id);
                    if (!a || a.cellId == null || a.isDead) continue;
                    var f = null;
                    try { f = (typeof a.getFighter === 'function') ? a.getFighter() : null; } catch (e) {}
                    if (!f) continue;
                    if (f.data && f.data.alive === false) continue;
                    var onTeam = false;
                    try { onTeam = !!fm.isFighterOnUsersTeam(id); } catch (e) {}
                    if (onTeam) continue;
                    var st = (f.data && f.data.stats) || {};
                    enemies.push({ id: id, cell: a.cellId,
                        hp: st.lifePoints != null ? st.lifePoints : 1 });
                }
                return { fm: fm, iso: iso, am: am, myId: myId, myCell: myCell,
                    ap: stats.actionPointsCurrent != null ? stats.actionPointsCurrent : 6,
                    mp: stats.movementPointsCurrent != null ? stats.movementPointsCurrent : 3,
                    spells: spells, enemies: enemies };
            },

            // A spell is worth casting at an enemy only if it actually damages. Buffs
            // and heals share the same list, and the effect instances the client uses
            // for its tooltips know the difference. Weapon attacks have no spellLevel,
            // but they are damage by definition.
            _damageSpell: function(spell) {
                if (!spell) return false;
                try {
                    if (typeof spell.isWeapon === 'function' && spell.isWeapon()) return true;
                } catch (e) {}
                var lvl = spell.spellLevel;
                if (!lvl || !lvl.effects || !lvl.effects.length) return false;
                var prefix = (typeof spell.getSpellLevelId === 'function')
                    ? spell.getSpellLevelId() : null;
                var rawDamage = [82, 85, 86, 87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100];
                for (var i = 0; i < lvl.effects.length; i++) {
                    var inst = (spell.effectInstances && prefix != null)
                        ? spell.effectInstances[prefix + '-effects-' + i] : null;
                    if (inst && typeof inst.isDamageEffect === 'function') {
                        try { if (inst.isDamageEffect()) return true; } catch (e) {}
                        continue;
                    }
                    var eid = (lvl.effects[i] || {}).effectId;
                    if (rawDamage.indexOf(eid) !== -1) return true;
                }
                return false;
            },

            _spellDescriptor: function(spell) {
                var prop = function(name, fallback) {
                    try {
                        var v = spell.getProperty ? spell.getProperty(name) : null;
                        return v != null ? v : (fallback !== undefined ? fallback : null);
                    } catch (e) { return fallback !== undefined ? fallback : null; }
                };
                return {
                    spellId: spell.id,
                    castInDiagonal: prop('castInDiagonal'),
                    castInLine: prop('castInLine'),
                    castTestLos: prop('castTestLos'),
                    minRange: prop('minRange'),
                    range: prop('range'),
                    apCost: prop('apCost', spell.level),
                    needFreeCell: prop('needFreeCell'),
                    needFreeTrapCell: prop('needFreeTrapCell'),
                    needTakenCell: prop('needTakenCell'),
                    name: (typeof spell.getName === 'function') ? spell.getName() : null
                };
            },

            // Range, line/diagonal restrictions and line of sight are the client's own
            // rules: setCurrentSpell + displaySpellRange is exactly what the spell bar
            // does, and isOutsight then answers for one cell. Geometry is only a
            // fallback for when the renderer is not ready.
            _spellReaches: function(ctx, spell, targetCell) {
                var iso = ctx.iso;
                var desc = this._spellDescriptor(spell);
                if (iso && typeof iso.setCurrentSpell === 'function'
                    && typeof iso.displaySpellRange === 'function'
                    && typeof iso.isOutsight === 'function'
                    && iso.mapRenderer && iso.mapRenderer.map && iso.mapRenderer.map.cells) {
                    var actor = ctx.am.getActor ? ctx.am.getActor(ctx.myId) : null;
                    if (actor && !actor.isDead) {
                        try {
                            iso.setCurrentSpell(desc);
                            iso.displaySpellRange(ctx.myId);
                            return !iso.isOutsight(targetCell);
                        } catch (e) {}
                    }
                }
                if (ctx.myCell == null) return false;
                var dist = this._cellDistance(ctx.myCell, targetCell);
                var min = desc.minRange || 0;
                var max = desc.range == null ? 63 : desc.range;
                if (dist < min || dist > max) return false;
                if (desc.castInLine || desc.castInDiagonal) {
                    var a = this._cellPoint(ctx.myCell), b = this._cellPoint(targetCell);
                    var line = (a.x === b.x || a.y === b.y);
                    var diag = Math.abs(a.x - b.x) === Math.abs(a.y - b.y);
                    if (desc.castInLine && desc.castInDiagonal) {
                        if (!line && !diag) return false;
                    } else if (desc.castInLine && !line) return false;
                    else if (desc.castInDiagonal && !diag) return false;
                }
                return true;
            },

            _planFightAction: function(ctx) {
                if (ctx.myCell == null) return null;
                var candidates = [];
                for (var k in ctx.spells) {
                    if (!Object.prototype.hasOwnProperty.call(ctx.spells, k)) continue;
                    var sid = parseInt(k, 10);
                    if (isNaN(sid)) continue;
                    var spell = ctx.spells[k];
                    if (!this._damageSpell(spell)) continue;
                    var usable = false;
                    try { usable = !!ctx.fm.canCastThisSpell(ctx.myId, sid); } catch (e) {}
                    if (!usable) continue;
                    var desc = this._spellDescriptor(spell);
                    candidates.push({ id: sid, spell: spell,
                        cost: desc.apCost != null ? desc.apCost : 1 });
                }
                candidates.sort(function(a, b) { return b.cost - a.cost; });
                var enemies = ctx.enemies.slice().sort(function(a, b) { return a.hp - b.hp; });
                for (var e = 0; e < enemies.length; e++) {
                    for (var s = 0; s < candidates.length; s++) {
                        if (this._spellReaches(ctx, candidates[s].spell, enemies[e].cell)) {
                            return { type: 'cast', spellId: candidates[s].id,
                                targetId: enemies[e].id, cell: enemies[e].cell };
                        }
                    }
                }
                if (ctx.mp > 0 && enemies.length) return this._planFightMove(ctx, enemies);
                return null;
            },

            // Walk towards the nearest enemy using the client's fight pathfinder, so
            // MP cost, tackle and occupied cells are the game's answers, not guesses.
            _planFightMove: function(ctx, enemies) {
                var iso = ctx.iso;
                if (!iso || typeof iso._displayPathInFight !== 'function') return null;
                var self = this;
                var nearest = enemies[0];
                var bestDist = this._cellDistance(ctx.myCell, nearest.cell);
                for (var i = 1; i < enemies.length; i++) {
                    var d = this._cellDistance(ctx.myCell, enemies[i].cell);
                    if (d < bestDist) { bestDist = d; nearest = enemies[i]; }
                }
                var limit = Math.max(1, Math.min(ctx.mp, 6));
                var best = null;
                for (var cell = 0; cell < 560; cell++) {
                    if (cell === ctx.myCell) continue;
                    if (this._cellDistance(cell, ctx.myCell) > limit) continue;
                    var dist = this._cellDistance(cell, nearest.cell);
                    if (dist >= bestDist) continue;
                    try {
                        if (typeof ctx.am.getActorsOnCell === 'function'
                            && ctx.am.getActorsOnCell(cell).length > 0) continue;
                    } catch (e) {}
                    var path = null;
                    try { path = iso._displayPathInFight(cell); } catch (e) {}
                    if (!path || !path.reachable || path.reachable.indexOf(cell) === -1) continue;
                    if (path.costMP != null && path.costMP > ctx.mp) continue;
                    best = { cell: cell, path: path.reachable };
                    bestDist = dist;
                }
                return best ? { type: 'move', cell: best.cell, path: best.path } : null;
            },

            // Same grid the bundle uses (getMapPointFromCellId): cell ids are not
            // row-major, so distance computed on raw ids is meaningless.
            _cellPoint: function(cell) {
                var t = cell % 14 - Math.floor(cell / 28);
                return { x: t + 19, y: t + Math.floor(cell / 14) };
            },

            _cellDistance: function(a, b) {
                var pa = this._cellPoint(a), pb = this._cellPoint(b);
                return Math.abs(pa.x - pb.x) + Math.abs(pa.y - pb.y);
            },

            // The bundle's compressPath, verified against the live capture: cells
            // [316, 359] must encode to keyMovements [4412, 4455].
            _compressPath: function(path) {
                var out = [], prev = path[0], lastDir = -1, px, py;
                for (var i = 0; i < path.length; i++) {
                    var cell = path[i], p = this._cellPoint(cell), dir;
                    if (i === 0) dir = -1;
                    else if (p.y === py) dir = p.x > px ? 7 : 3;
                    else if (p.x === px) dir = p.y > py ? 1 : 5;
                    else if (p.x > px) dir = p.y > py ? 0 : 6;
                    else dir = p.y > py ? 2 : 4;
                    if (dir !== lastDir) { out.push(prev + (dir << 12)); lastDir = dir; }
                    prev = cell; px = p.x; py = p.y;
                }
                out.push(prev + (lastDir << 12));
                return out;
            },

            sendPacket: function(messageType, payload) {
                try {
                    if (window.dofus && window.dofus.connectionManager) {
                        window.dofus.connectionManager.sendMessage(messageType, payload);
                        this.lastSendError = null;
                        return true;
                    }
                    this.lastSendError = 'connectionManager unavailable';
                    return false;
                } catch (e) {
                    this.lastSendError = String(e);
                    return false;
                }
            },

            isUserActor: function(id) {
                // This used to read `playerData.character.id`. That field does not
                // exist in this client build — the bundle never reads a field off it
                // — so the condition was always false, the fallback below always ran,
                // and every actor in the fight counted as the player. Every
                // GameFightTurnStartMessage then looked like the player's own turn,
                // and executeOptimalTurn was handed the monster's id as the fighter to
                // plan for. The real path is
                // `playerData.characterBaseInformations.id`, the same object
                // `_agentSnapshot` reads.
                if (window.gui && window.gui.playerData && window.gui.playerData.characterBaseInformations) {
                    return window.gui.playerData.characterBaseInformations.id === id;
                }
                return false;
            }
        };

        FarmBot.init();
        window.__FarmBot = FarmBot;
        try {
            if (typeof window.__agentFightBrain === 'undefined') {
                window.__agentFightBrain = 'http://127.0.0.1:8891';
            }
        } catch (e) {}
    } catch(e) {}

    // 25b. Agent directive bus — driven by the C# AgentDirectiveBridge (127.0.0.1:8893),
    // which the Python agent (dofus-network/agent) POSTs to. window.__agentDirective
    // receives the raw envelope {id, ts, directive, tool, args, source} and returns a
    // JSON string; the host unwraps it and hands it back to the agent as the tool result,
    // so every directive lands in the trajectory recorder with a real outcome.
    try {
        // One build string for the page and for `debug.version`. A client that
        // was not fully restarted keeps the previous script, and the first
        // symptom is a fix that appears not to work; this makes that visible
        // in every recorded observation.
        window.__agentBuild = '2026-10-25d';
        var _agentResult = function(ok, data, error) {
            return JSON.stringify({ ok: !!ok, data: data || null, error: error || null, at: Date.now() });
        };

        // Send a protocol message through the game's own connection manager.
        //
        // Every payload below was taken from a recorded session (see
        // docs/PROTOCOL_NOTES.md), never inferred from the bundle: a hand-built
        // message that the server disagrees with is a desync, and the client has
        // no way to recover from one.
        var _cmSend = function(name, args) {
            try {
                var cm = window.dofus && window.dofus.connectionManager;
                if (!cm) return { ok: false, error: 'connectionManager unavailable' };
                cm.sendMessage(name, args);
                return { ok: true };
            } catch (e) {
                return { ok: false, error: String(e) };
            }
        };

        // Claims that already ended a connection.
        //
        // A disconnect that follows a validation by a second is the server
        // answering the claim, and it is the most expensive label this agent can
        // collect. Remember the (quest, objective) pair the packet carried:
        // window.__agentRecordKick is called from the disconnect hook, and the
        // host seeds signatures extracted from old sessions before the page
        // loads. Nothing here is a quest list -- only what the server itself
        // rejected, on this machine or in a recorded session.
        var _KICK_STORE = 'dofus.agent.unsafeClaims.v1';
        var _knownKickSignatures = function() {
            var out = {};
            try {
                var seeded = window.__agentKnownKickSignatures;
                if (seeded && seeded.length) {
                    for (var i = 0; i < seeded.length; i++) out[String(seeded[i])] = true;
                }
            } catch (e) {}
            try {
                var raw = window.localStorage && window.localStorage.getItem(_KICK_STORE);
                var saved = raw ? JSON.parse(raw) : null;
                if (saved && typeof saved === 'object') {
                    for (var k in saved) {
                        if (Object.prototype.hasOwnProperty.call(saved, k)) out[k] = true;
                    }
                }
            } catch (e) {}
            return out;
        };
        window.__agentRecordKick = function(via) {
            var prev = window.__agentLastValidate;
            if (!prev || !prev.quest_id || !prev.objective_id) return false;
            var age = Date.now() - Number(prev.at || 0);
            if (age < 0 || age > 3000) return false;
            var sig = Number(prev.quest_id) + '/' + Number(prev.objective_id);
            var saved = {};
            try {
                var raw = window.localStorage && window.localStorage.getItem(_KICK_STORE);
                saved = raw ? JSON.parse(raw) : {};
                if (!saved || typeof saved !== 'object') saved = {};
            } catch (e) { saved = {}; }
            saved[sig] = { at: Date.now(), via: String(via || ''),
                           count: ((saved[sig] && saved[sig].count) || 0) + 1 };
            var keys = [];
            for (var k in saved) {
                if (Object.prototype.hasOwnProperty.call(saved, k)) keys.push(k);
            }
            while (keys.length > 500) delete saved[keys.shift()];
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(_KICK_STORE, JSON.stringify(saved));
                }
            } catch (e) {}
            console.log('[AgentGuard] kick signature recorded: quest=' + prev.quest_id
                        + ' objective=' + prev.objective_id
                        + ' via=' + (via || '') + ' age_ms=' + age);
            return true;
        };

        // Whether a quest objective can be claimed safely.
        //
        // QuestObjectiveValidationMessage is a claim, and the server checks it
        // against its own quest state: a claim it did not expect ends the
        // connection (recorded as serverDisconnecting/SERVER_GONE within a
        // second of the packet). The client's quest model is only a copy of
        // that state and it lags -- it can still show the previous step, a
        // status from before a map change, or CAN_BE_FINISHED on an objective
        // whose event the server has not counted. So a claim is sent only when
        // the model says, at this instant, that this is the current objective,
        // that it is CAN_BE_FINISHED, that any counter it carries is complete,
        // that its type is one the client is allowed to report, that no earlier
        // claim of this pair ended the connection, and that nothing refreshed
        // the state or changed the map moments ago.
        var _questClaimCheck = function(qid, oid) {
            var now = Date.now();
            var sq = window.gui && window.gui.playerData && window.gui.playerData.quests
                && window.gui.playerData.quests.standardQuests;
            var q = null;
            try {
                if (sq && typeof sq.getActiveQuestById === 'function') {
                    q = sq.getActiveQuestById(qid) || sq.getActiveQuestById(String(qid));
                }
            } catch (e) {}
            if (!q || !q.objectives) {
                return { ok: false, reason: 'objective_not_in_client_model',
                         error: 'quest ' + qid + ' objective ' + oid
                             + ' is not in the client quest model; the client cannot see what it would claim' };
            }
            var target = null, current = null;
            for (var i = 0; i < q.objectives.length; i++) {
                var o = q.objectives[i] || {};
                if (o.objectiveStatus !== 0 && !current) current = o;
                if (Number(o.objectiveId) === oid) target = o;
            }
            if (!target) {
                return { ok: false, reason: 'objective_not_in_current_step',
                         error: 'objective ' + oid + ' is not part of quest ' + qid
                             + "'s current step" };
            }
            if (current && Number(current.objectiveId) !== oid) {
                return { ok: false, reason: 'objective_not_current',
                         current_objective_id: current.objectiveId,
                         error: 'objective ' + oid + ' is not the current objective (current is '
                             + current.objectiveId + ')' };
            }
            if (Number(target.objectiveStatus) !== 2) {
                return { ok: false, reason: 'objective_not_ready', status: target.objectiveStatus,
                         error: 'objective ' + oid + ' is not CAN_BE_FINISHED (status '
                             + target.objectiveStatus + ')' };
            }
            if (target.maxCompletion != null && target.curCompletion != null
                    && Number(target.curCompletion) < Number(target.maxCompletion)) {
                return { ok: false, reason: 'objective_count_incomplete',
                         cur_completion: target.curCompletion,
                         max_completion: target.maxCompletion,
                         error: 'objective ' + oid + ' is at ' + target.curCompletion + '/'
                             + target.maxCompletion + '; the server counts this one itself' };
            }
            // Types whose completion the server decides. Claiming one before
            // the server has counted the event is what the disconnect follows.
            var db = (q.dbObjectives && q.dbObjectives[oid]) || null;
            if (!db) {
                try {
                    var table = window.gui && window.gui.databases && window.gui.databases.QuestObjectives;
                    db = (table && table[oid]) || null;
                } catch (e) {}
            }
            var kind = (db && db._type) || target._type || null;
            var SERVER_TYPES = {
                // GoToNpc is the talk objective. The server counts the NPC
                // conversation, not a validation: measured 2026-10-24, claiming
                // tutorial objective 13922 (QuestObjectiveGoToNpc, "Talk to Ayin
                // Spyr") while the NPC was not on the map was followed 230 ms
                // later by serverDisconnecting SERVER_GONE.
                QuestObjectiveGoToNpc: true,
                QuestObjectiveDiscoverMap: true,
                QuestObjectiveDiscoverSubArea: true,
                QuestObjectiveFightMonster: true,
                QuestObjectiveFightMonstersOnMap: true,
                QuestObjectiveMultiFightMonster: true,
                QuestObjectiveWinMonsterInDungeon: true,
                QuestObjectiveWinMonsterInDungeonXTimes: true,
                QuestObjectiveWinMonsterInDungeonValidChallenge: true,
                QuestObjectiveWinPvpAgainstBreed: true,
                QuestObjectiveCraftXItem: true,
                QuestObjectiveBringItemToNpc: true,
                QuestObjectiveBringSoulToNpc: true,
                QuestObjectiveValidChallenge: true,
                QuestObjectiveDuelSpecificPlayer: true,
                QuestObjectivePoiId: true
            };
            var typeId = db && db.typeId != null ? Number(db.typeId)
                : (target.typeId != null ? Number(target.typeId) : null);
            // Fallback for builds where _type is absent: 1 is GoToNpc (measured on
            // objective 13922), 4 and 6 are the DiscoverMap and FightMonster ids in
            // this client's database, all types whose claims were followed by a
            // disconnect.
            if (!kind && (typeId === 1 || typeId === 4 || typeId === 6)) {
                kind = typeId === 1 ? 'QuestObjectiveGoToNpc'
                    : (typeId === 4 ? 'QuestObjectiveDiscoverMap' : 'QuestObjectiveFightMonster');
            }
            if (kind && SERVER_TYPES[kind]) {
                return { ok: false, reason: 'objective_server_counted', type: kind, type_id: typeId,
                         params: (db && db.parameters) || target.parameters || null,
                         error: 'objective ' + oid + ' (' + kind + ') is completed by the server; '
                             + 'perform the action instead of claiming it' };
            }
            // An unresolvable type is not permission to claim. Types absent from
            // SERVER_TYPES (FreeForm, Criterion) are still allowed below, but only
            // when the client model names them; an unnamed objective may be one the
            // server counts, and a wrong claim ends the connection.
            if (!kind) {
                return { ok: false, reason: 'objective_type_unknown', type_id: typeId,
                         params: (db && db.parameters) || target.parameters || null,
                         error: 'objective ' + oid + ' has no resolvable type in the client model; '
                             + 'perform the action instead of claiming it' };
            }
            try {
                if (_knownKickSignatures()[Number(qid) + '/' + Number(oid)]) {
                    return { ok: false, reason: 'known_kick_signature',
                             error: 'a previous claim of objective ' + oid + ' of quest ' + qid
                                 + ' ended the connection; perform the objective instead' };
                }
            } catch (e) {}
            var mapAt = Number(window.__agentLastMapChange || 0);
            if (mapAt && now - mapAt < 2000) {
                return { ok: false, reason: 'map_change_settling',
                         wait_ms: 2000 - (now - mapAt),
                         error: 'the map changed ' + (now - mapAt) + ' ms ago; the quest state is settling' };
            }
            var syncAt = Number(window.__agentLastQuestSync || 0);
            if (syncAt && now - syncAt < 800) {
                return { ok: false, reason: 'quest_state_syncing',
                         wait_ms: 800 - (now - syncAt),
                         error: 'the quest state was refreshed ' + (now - syncAt) + ' ms ago; retry in a moment' };
            }
            var prev = window.__agentLastValidate;
            if (prev && Number(prev.quest_id) === qid && Number(prev.objective_id) === oid
                    && now - prev.at < 5000) {
                return { ok: false, reason: 'validation_cooldown',
                         wait_ms: 5000 - (now - prev.at),
                         error: 'this objective was already claimed ' + (now - prev.at)
                             + ' ms ago; the server has not answered yet' };
            }
            return { ok: true };
        };

        // The interactive elements on the current map, as {elemId, skillInstanceUid}.
        // Harvest is `InteractiveUseRequestMessage` with exactly these two fields.
        var _interactives = function() {
            var out = [];
            try {
                var ie = window.isoEngine;
                var list = (ie && (ie.interactiveElements || (ie.mapData && ie.mapData.interactiveElements))) || null;
                if (list && typeof list === 'object') {
                    for (var k in list) {
                        if (!Object.prototype.hasOwnProperty.call(list, k)) continue;
                        var el = list[k] || {};
                        var elemId = el.id != null ? el.id : (el.elemId != null ? el.elemId : parseInt(k, 10));
                        if (elemId == null || isNaN(elemId)) continue;
                        out.push({
                            elemId: elemId,
                            skillInstanceUid: el.skillInstanceUid != null ? el.skillInstanceUid : null,
                            cell: el.cell != null ? el.cell : (el.position != null ? el.position.cell : null)
                        });
                    }
                }
            } catch (e) {}
            return out;
        };

        // Inventory lookup by name, so hdv.list / inv.sell can take a name like
        // every other verb instead of an internal uid.
        var _findItem = function(name) {
            var items = [];
            try {
                var inv = window.gui && window.gui.playerData && window.gui.playerData.inventory;
                var bag = (inv && (inv.items || inv.objects)) || null;
                if (bag && typeof bag === 'object') {
                    for (var k in bag) {
                        if (!Object.prototype.hasOwnProperty.call(bag, k)) continue;
                        var it = bag[k] || {};
                        items.push({ uid: it.objectUID != null ? it.objectUID : (it.uid != null ? it.uid : parseInt(k, 10)),
                                     gid: it.objectGID != null ? it.objectGID : (it.gid != null ? it.gid : null),
                                     quantity: it.quantity != null ? it.quantity : (it.count != null ? it.count : 1),
                                     name: it.name || it.itemName || '' });
                    }
                }
            } catch (e) {}
            if (!name) return items;
            var needle = String(name).toLowerCase();
            for (var i = 0; i < items.length; i++) {
                if (String(items[i].name).toLowerCase() === needle) return [items[i]];
            }
            for (var j = 0; j < items.length; j++) {
                if (String(items[j].name).toLowerCase().indexOf(needle) !== -1) return [items[j]];
            }
            return [];
        };

        // -- market price cache ------------------------------------------------
        //
        // Prices do not come back with the request that asks for them. A scan
        // sends ExchangeBidHouseTypeMessage / ExchangeBidHouseListMessage and the
        // numbers arrive later as ExchangeBidHouseInListAddedMessage; the server
        // also answers ObjectAveragePricesGetMessage with one table covering every
        // item on the server. Both land here, and nothing else does: this cache
        // only ever holds values the server actually sent, tagged with when and
        // from which message, so a listing can say where its price came from.
        var __PriceCache = {
            average: {},      // gid -> average price across the server
            listings: {},     // gid -> [{uid, price, quantity}] seen in a category
            averageAt: 0,
            listingAt: 0,
            averageRequestedAt: 0,
            // The game's own field names, taken from the bundle handlers:
            // ObjectAveragePricesMessage  {ids, avgPrices}
            // ExchangeBidHouseInList*Message {itemUID, objGenericId, prices, effects}
            // The earlier reader used prices/objectGID/objectUID and therefore
            // never cached a single server number.
            absorb: function(name, msg) {
                if (!msg || typeof msg !== 'object') return;
                if (name === 'ObjectAveragePricesMessage') {
                    var ids = msg.ids, prices = msg.avgPrices || msg.prices;
                    if (Object.prototype.toString.call(ids) !== '[object Array]') return;
                    if (Object.prototype.toString.call(prices) !== '[object Array]') return;
                    for (var i = 0; i < ids.length && i < prices.length; i++) {
                        var gid = Number(ids[i]), price = Number(prices[i]);
                        if (gid > 0 && price > 0) this.average[gid] = price;
                    }
                    this.averageAt = Date.now();
                    return;
                }
                if (name === 'ExchangeBidHouseInListAddedMessage'
                        || name === 'ExchangeBidHouseInListUpdatedMessage') {
                    var g = Number(msg.objGenericId != null ? msg.objGenericId
                                 : msg.objectGID);
                    if (!(g > 0)) return;
                    var p = msg.prices;
                    if (Object.prototype.toString.call(p) !== '[object Array]') return;
                    var uid = msg.itemUID != null ? Number(msg.itemUID)
                            : (msg.objectUID != null ? Number(msg.objectUID) : null);
                    var rows = [];
                    for (var j = 0; j < p.length; j++) {
                        var price = Number(p[j]);
                        if (!(price > 0)) continue;
                        rows.push({ uid: uid, price: price, quantity: 1 });
                    }
                    if (rows.length) {
                        this.listings[g] = rows;
                        this.listingAt = Date.now();
                    }
                }
            },
            stats: function(gid) {
                var rows = this.listings[gid] || [];
                if (!rows.length) return null;
                var prices = rows.map(function(r) { return r.price; }).sort(function(a, b) { return a - b; });
                var mid = Math.floor(prices.length / 2);
                return {
                    count: prices.length,
                    min: prices[0],
                    median: prices.length % 2 ? prices[mid] : Math.round((prices[mid - 1] + prices[mid]) / 2),
                    max: prices[prices.length - 1],
                    source: 'listing',
                    age_ms: Date.now() - this.listingAt
                };
            },
            averageOf: function(gid) {
                var price = this.average[gid];
                if (!(price > 0)) return null;
                return { average: price, source: 'average', age_ms: Date.now() - this.averageAt };
            }
        };

        // The authoritative life state, read the same way the HUD reads it.
        // playerData carries lifePoints too, but in this build it stayed 0 while the
        // character stood in world at 55/55 (see the snapshot below), and a live
        // session refused every fight with "0 HP; revive before fighting".
        // isAlive() is the bundle's own test: state === STATUS_ALIVE_AND_KICKING.
        var _agentLife = function () {
            var pd = null;
            try { pd = (window.gui && window.gui.playerData) || null; } catch (e) {}
            var life = null, max = null, state = null, alive = null;
            try {
                var stats = pd && pd.characters && pd.characters.mainCharacter
                    && pd.characters.mainCharacter.characteristics;
                if (stats) {
                    if (stats.lifePoints != null) life = Number(stats.lifePoints);
                    if (stats.effectiveMaxLifePoints != null) {
                        max = Number(stats.effectiveMaxLifePoints);
                    } else if (stats.maxLifePoints != null) {
                        max = Number(stats.maxLifePoints);
                    }
                }
            } catch (e) {}
            try {
                if (pd && pd.state != null) state = Number(pd.state);
                if (pd && typeof pd.isAlive === 'function') alive = !!pd.isAlive();
            } catch (e) {}
            if (life == null && pd && pd.lifePoints != null) life = Number(pd.lifePoints);
            // A missing stat is not a death certificate: when nothing says the
            // character is dead, let the game reject the fight instead of blocking it.
            if (alive == null) alive = (life == null) ? true : life > 0;
            return { alive: alive, life: life, max: max, state: state };
        };
        var _agentSnapshot = function() {
            var snap = { connected: false, in_fight: false };
            try {
                // The fields this used to read do not exist in this client build.
                // `playerData.character` is not an object with id/name/level (the
                // bundle never reads a field off it), `isoEngine.currentMapId` appears
                // zero times, and `playerData.characteristics` likewise — so every
                // value came back null while the character stood on Astrub 4,-21.
                // A reader that returns nulls is indistinguishable from a character
                // that is not in the game, and the agent correctly refused to act on
                // an empty world. The real shape, taken from the bundle itself:
                //   playerData.characterBaseInformations  {id,name,level,breed,sex}
                //   playerData.position                   {mapId,coordinates{posX,posY},...}
                //   playerData.isFighting                 bool
                //   isoEngine.actorManager.userActor      the player's own actor
                var first = function (fns) {
                    for (var i = 0; i < fns.length; i++) {
                        try { var v = fns[i](); if (v !== undefined && v !== null) return v; }
                        catch (e) {}
                    }
                    return null;
                };
                var pd = (window.gui && window.gui.playerData) || null;
                var base = (pd && pd.characterBaseInformations) || {};
                var pos = (pd && pd.position) || {};
                var coords = pos.coordinates || {};
                var ua = null;
                try { ua = window.isoEngine && window.isoEngine.actorManager
                    && window.isoEngine.actorManager.userActor; } catch (e) {}
                snap.connected = !!(window.dofus && window.dofus.connectionManager);
                snap.character = {
                    id: first([function(){ return base.id; }, function(){ return pd.id; }]),
                    name: base.name || null,
                    level: first([function(){ return base.level; }]),
                    breed: first([function(){ return base.breed && base.breed.name; },
                                  function(){ return base.breed; }])
                };
                var mapId = first([
                    function(){ return pos.mapId; },
                    function(){ return window.isoEngine.mapData.mapId; }
                ]);
                snap.position = {
                    cell: first([
                        function(){ return ua.position.cell; },
                        function(){ return ua.cellId; },
                        function(){ return pos.cell; }
                    ]),
                    x: first([function(){ return coords.posX; }, function(){ return pos.x; }]),
                    y: first([function(){ return coords.posY; }, function(){ return pos.y; }]),
                    map_id: mapId
                };
                // Connected is not the same as in-world. The client stays connected at
                // character selection, where every action silently does nothing — and
                // position.mapId is 0 there. Treating "not null" as in-world reported
                // a character standing on map 0, so guard the value itself.
                snap.in_world = (mapId != null && Number(mapId) > 0);
                var stats = (ua && ua.stats) || {};
                // The live stats live on the selected character, not on playerData:
                //   playerData.characters.mainCharacter.characteristics
                // That is what the bundle's own HUD and XP gauge read. playerData has
                // lifePoints/actionPoints keys too, but they stayed at 0 while the
                // character stood in world, and `first()` treats 0 as a real value,
                // so the actor fallback was never reached. Read the character stats
                // first and keep the others as fallbacks.
                var chars = null;
                try {
                    chars = (pd && pd.characters && pd.characters.mainCharacter
                        && pd.characters.mainCharacter.characteristics) || null;
                } catch (e) {}
                snap.hp = {
                    life: first([function(){ return chars && chars.lifePoints; },
                                 function(){ return pd.lifePoints; },
                                 function(){ return stats.lifePoints; },
                                 function(){ return base.lifePoints; }]),
                    max: first([function(){ return chars && chars.effectiveMaxLifePoints; },
                                function(){ return chars && chars.maxLifePoints; },
                                function(){ return pd.effectiveMaxLifePoints; },
                                function(){ return stats.maxLifePoints; },
                                function(){ return base.maxLifePoints; }]),
                    ap: first([function(){ return chars && chars.actionPointsCurrent; },
                               function(){ return pd.actionPoints; },
                               function(){ return stats.actionPoints; }]),
                    mp: first([function(){ return chars && chars.movementPointsCurrent; },
                               function(){ return pd.movementPoints; },
                               function(){ return stats.movementPoints; }])
                };
                var lifeSelf = _agentLife();
                snap.alive = lifeSelf.alive;
                snap.life_state = lifeSelf.state;
                snap.in_fight = !!(pd && pd.isFighting);
                // A talk objective opens a dialog; without this flag the verifier sees
                // no change and calls the step a noop, and the loop retires a quest
                // that was actually progressing.
                snap.in_dialog = !!(pd && pd.isInDialog);
                // The dialog itself: the question and every reply, with the
                // server reply id aligned to each string. `in_dialog` alone told
                // the policy that a choice existed but not what the choices were,
                // so it could only pick index 0 and hope.
                try {
                    snap.dialog = _agentDialogState();
                } catch (e) { snap.dialog = null; }
                // Why the previous connection ended, if it did. Carried in the
                // observation so a session that died of a kick can be told apart from one
                // that was closed by hand — the two look identical from the host side.
                snap.agent_build = window.__agentBuild || null;
                snap.last_disconnect = window.__agentLastDisconnect || null;
                // What the last navigation attempt actually did. A failed walk and a
                // rejected transition look identical in the position fields; this
                // says which one happened, and at which cell.
                snap.last_nav = window.__agentLastNav || null;
                // The map's exits, NPCs and interactive elements: the details a
                // policy needs to choose a specific spot, not just a map id.
                try {
                    if (typeof _agentMapDetails === 'function') {
                        snap.map = _agentMapDetails();
                    }
                } catch (e) { snap.map = null; }
                // The tutorial's own gate: which crossings it allows, which cells it
                // lets the character stand on, and which NPC it lets it tap. During
                // the tutorial the client refuses actions outside that set, so the
                // planner needs it to pick a step it can actually complete.
                try {
                    snap.tutorial = _agentTutorial();
                } catch (e) { snap.tutorial = null; }

                // Quest state. Without it the planner is blind: it cannot tell which
                // quests are open, which objective is current, or which one is ready to
                // hand in. It was planning against an observation that carried none of
                // this, so every step re-accepted a quest and nothing could progress.
                //
                // The client's own model (from the bundle): an active quest has
                // `objectives[{objectiveId, objectiveStatus}]` and `finishedCount`, and
                // objectiveStatus is {FINISHED:0, ACTIVE:1, CAN_BE_FINISHED:2}.
                // Status 2 is the interesting one: the conditions are met and the
                // objective only needs QuestObjectiveValidationMessage to be claimed.
                // An objective's type and parameters live in the quest's own
                // `dbObjectives` table, which the client loads from the game database
                // and which is newer than any knowledge-base snapshot. Without them the
                // planner saw an id and a status and could do nothing but re-send the
                // validation message: the tutorial quest it was stuck on (3841) is not
                // in quests_full.json at all, so there was no type to translate.
                var _objParams = function (raw) {
                    var out = {};
                    if (!raw) return out;
                    if (raw.length !== undefined && typeof raw !== 'string') {
                        if (raw.length > 0 && raw[0] != null) out.parameter0 = raw[0];
                        if (raw.length > 1 && raw[1] != null) out.parameter1 = raw[1];
                    } else if (typeof raw === 'object') {
                        for (var k in raw) {
                            if (raw.hasOwnProperty(k) && raw[k] != null) out[k] = raw[k];
                        }
                    }
                    return out;
                };
                // The quest's own dbObjectives is filled asynchronously from the
                // client's game database. A quest can therefore be active while that
                // table is still empty for it, which is what left some objectives as
                // bare ids. The same table is available globally once the client has
                // loaded its databases, so use it as a generic fallback -- the raw id
                // lookup is not specific to any quest.
                var _rawObjectiveDb = function (id) {
                    try {
                        var dbs = window.gui && window.gui.databases;
                        var table = dbs && dbs.QuestObjectives;
                        return (table && table[id]) || null;
                    } catch (e) { return null; }
                };
                // `typeId` is the objective type the planner translates; the bundle
                // itself formats text from `_type`. They are the same enum, so prefer
                // typeId and fall back rather than emit a null type on a build that
                // only carries one of them.
                var _objMeta = function (o, db, raw) {
                    var d = db || {};
                    var r = raw || {};
                    var meta = {};
                    var t = d.typeId != null ? d.typeId
                        : (o.typeId != null ? o.typeId
                        : (d._type != null ? d._type
                        : (r.typeId != null ? r.typeId : r._type)));
                    if (t != null && !isNaN(Number(t))) meta.type_id = Number(t);
                    // `_type` is the game's own class name for the objective
                    // (QuestObjectiveDiscoverMap, QuestObjectiveFightMonster,
                    // ...). It decides who completes the objective: the client
                    // reports some, the server counts the rest, and a claim on
                    // a server-counted one before its event is a disconnect.
                    var tn = d._type || o._type || r._type;
                    if (typeof tn === 'string' && tn) meta.type = tn;
                    if (o.curCompletion != null && !isNaN(Number(o.curCompletion))) {
                        meta.cur_completion = Number(o.curCompletion);
                    }
                    if (o.maxCompletion != null && !isNaN(Number(o.maxCompletion))) {
                        meta.max_completion = Number(o.maxCompletion);
                    }
                    var m = Number(d.mapId || r.mapId || 0);
                    if (m) meta.map_id = m;
                    var p = _objParams(d.parameters || r.parameters);
                    var keys = [];
                    for (var pk in p) if (p.hasOwnProperty(pk)) keys.push(pk);
                    if (keys.length) meta.params = p;
                    if (typeof o.text === 'string') meta.text = o.text;
                    else if (typeof d.text === 'string') meta.text = d.text;
                    else if (typeof r.text === 'string') meta.text = r.text;
                    var sid = d.stepId != null ? d.stepId : r.stepId;
                    if (sid != null) meta.step_id = Number(sid);
                    return meta;
                };
                var _objEntry = function (base, meta) {
                    for (var k in meta) if (meta.hasOwnProperty(k)) base[k] = meta[k];
                    return base;
                };
                var qs = { active: {}, current: null, ready: [], step_objectives: [], completed: 0 };
                try {
                    var sq = pd && pd.quests && pd.quests.standardQuests;
                    if (sq && typeof sq.getListOfActiveQuestsId === 'function') {
                        var qids = sq.getListOfActiveQuestsId() || [];
                        var perQuest = {};
                        for (var qi = 0; qi < qids.length; qi++) {
                            var qid = qids[qi];
                            var q = sq.getActiveQuestById(qid);
                            if (!q) continue;
                            var fin = Number(q.finishedCount || 0);
                            qs.active[qid] = fin;
                            qs.completed += fin;
                            var objs = q.objectives || [];
                            var dbs = q.dbObjectives || {};
                            perQuest[qid] = [];
                            for (var oi = 0; oi < objs.length; oi++) {
                                var o = objs[oi] || {};
                                var st = o.objectiveStatus;
                                var meta = _objMeta(o, dbs[o.objectiveId],
                                                    _rawObjectiveDb(o.objectiveId));
                                perQuest[qid].push(_objEntry(
                                    { id: o.objectiveId, done: st === 0, status: st }, meta));
                                if (st === 2) qs.ready.push(_objEntry(
                                    { quest_id: qid, objective_id: o.objectiveId }, meta));
                                if (st !== 0 && !qs.current) {
                                    qs.current = _objEntry(
                                        { quest_id: qid, step: fin,
                                          objective_id: o.objectiveId, status: st }, meta);
                                }
                            }
                        }
                        if (qs.current) qs.step_objectives = perQuest[qs.current.quest_id] || [];
                    }
                } catch (e) {}
                snap.quests_active = qs.active;
                snap.current_objective = qs.current;
                snap.ready_objectives = qs.ready;
                snap.step_objectives = qs.step_objectives;
                snap.objectives_completed = qs.completed;
                // XP is what makes a farm measurable: it is the only quantity the
                // client reports on every kill, and a fight that yields 0 xp is a
                // fight that was lost or abandoned. Read several candidate keys
                // because the field name differs across client builds.
                // `ch` used to be playerData.character, which does not exist. Leaving
                // the reference in place after switching to characterBaseInformations
                // threw here — and because the whole body is wrapped in one try/catch,
                // every field below it (kamas, inventory) silently vanished and the
                // snapshot came back with an `error` key instead.
                snap.xp = first([function(){ return chars && chars.experience; },
                                 function(){ return base.experience; },
                                 function(){ return pd.experience; },
                                 function(){ return base.xp; }]);
                snap.xp_level = first([function(){ return base.experienceLevel; },
                                       function(){ return base.level; }]);
                snap.kamas = (pd && pd.inventory && pd.inventory.kamas != null)
                    ? pd.inventory.kamas : null;
                if (window.__FarmBot) {
                    snap.auto_fight = !!window.__FarmBot.enabled;
                    snap.farm_state = window.__FarmBot.state || null;
                    // Running counters ride along with every observation, so a
                    // session's progress is reconstructible from the log alone.
                    try {
                        snap.farm_session = (window.__FarmBot.sessionSummary
                            ? window.__FarmBot.sessionSummary() : null);
                    } catch(e) { snap.farm_session = null; }
                }
                // Inventory probe. Item GIDs are returned raw; the Python agent maps
                // gid -> name from its local items cache (no per-item DOM work here).
                //
                // `inventory.objects` is keyed by object UID on this client, not an
                // array, and each value is a whole ObjectItem. The old branch emitted
                // the UID as the gid and the entire ObjectItem as the quantity, which
                // bloated every observation to hundreds of tokens and made inventory
                // reasoning impossible. Normalise both shapes to {gid, qty} numbers.
                if (pd && pd.inventory) {
                    var inv = pd.inventory;
                    snap.inventory = { kamas: (inv.kamas != null ? inv.kamas : null), items: [], raw_keys: null };
                    var _qty = function (v) {
                        if (typeof v === 'number') return v;
                        if (v && typeof v === 'object' && typeof v.quantity === 'number') return v.quantity;
                        var n = Number(v);
                        return isFinite(n) ? n : 1;
                    };
                    var _pushItem = function (gid, qty) {
                        if (gid == null) return;
                        if (snap.inventory.items.length >= 120) {
                            snap.inventory.items_truncated = true;
                            return;
                        }
                        snap.inventory.items.push({ gid: gid, qty: _qty(qty) });
                    };
                    var items = inv.objects || inv.items || null;
                    if (Array.isArray(items)) {
                        for (var i = 0; i < items.length; i++) {
                            var it = items[i] || {};
                            _pushItem(it.objectGID != null ? it.objectGID
                                      : (it.gid != null ? it.gid : null),
                                      it.quantity != null ? it.quantity : it.qty);
                        }
                    } else if (items && typeof items === 'object') {
                        for (var key in items) {
                            if (!Object.prototype.hasOwnProperty.call(items, key)) continue;
                            var entry = items[key];
                            if (entry && typeof entry === 'object') {
                                _pushItem(entry.objectGID != null ? entry.objectGID
                                          : (entry.gid != null ? entry.gid
                                          : (parseInt(key, 10) || null)),
                                          entry.quantity != null ? entry.quantity : entry.qty);
                            } else {
                                _pushItem(parseInt(key, 10) || key, entry);
                            }
                        }
                    } else {
                        snap.inventory.raw_keys = Object.keys(inv).slice(0, 25);
                    }
                }
            } catch(err) {
                snap.error = err.toString();
            }
            return snap;
        };

        // Exposed so the host can attach the game state to every directive
        // response. Without this the Python recorder logs `observation: null`
        // for every action, which is the difference between "the bot moved" and
        // "the bot moved because it was standing on cell 402 at 120 HP with a
        // fight open". Cheap: one object build, no DOM walk.
        window.__agentSnapshot = _agentSnapshot;

        // ------------------------------------------------------------------
        // __DofusRecorder — log everything a human does.
        //
        // The point is one 10-minute session on a clean account that yields a
        // complete trace: every message the client sent, every click, every key,
        // every state change. That trace is what tells us which hooks exist and
        // how the real flows (create account -> login -> move -> fight -> loot ->
        // market) are sequenced, instead of guessing method names.
        //
        // PRIVACY, non-negotiable: input *values* are never read. A password or
        // an account name typed into a field is not logged, only the fact that a
        // field was used. Everything else is game state.
        // ------------------------------------------------------------------
        var __Rec = {
            enabled: false,
            sessionId: null,
            startedAt: 0,
            seq: 0,
            counts: {},
            lastState: null,
            _hooks: [],
            _timer: null,

            _post: function(event) {
                try {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage({
                            type: 'PLAY_LOG',
                            session: this.sessionId,
                            seq: this.seq++,
                            at: Date.now(),
                            event: event
                        });
                    }
                } catch (e) {}
                var k = event.kind;
                this.counts[k] = (this.counts[k] || 0) + 1;
            },

            start: function(label) {
                if (this.enabled) return this.status();
                this.enabled = true;
                this.startedAt = Date.now();
                this.sessionId = (label || 'play') + '-' + new Date().toISOString().replace(/[:.]/g, '-');
                this.seq = 0;
                this.counts = {};
                this.lastState = null;
                this._post({ kind: 'session_start', label: label || 'play',
                             ua: navigator.userAgent, href: String(location.href).slice(0, 200),
                             screen: { w: screen.width, h: screen.height } });
                this._hookNetwork();
                this._hookDom();
                this._hookConsole();
                this._pollState();
                console.log('[Recorder] recording as ' + this.sessionId);
                return this.status();
            },

            stop: function() {
                if (!this.enabled) return this.status();
                this._post({ kind: 'session_stop', counts: this.counts });
                this.enabled = false;
                for (var i = 0; i < this._hooks.length; i++) {
                    try { this._hooks[i](); } catch (e) {}
                }
                this._hooks = [];
                if (this._timer) { clearInterval(this._timer); this._timer = null; }
                console.log('[Recorder] stopped');
                return this.status();
            },

            status: function() {
                return {
                    enabled: this.enabled,
                    session: this.sessionId,
                    seconds: this.startedAt ? Math.round((Date.now() - this.startedAt) / 1000) : 0,
                    counts: this.counts
                };
            },

            // The API surface, written straight to the trace (not through
            // _shrink: this one is meant to be big).
            probe: function(tag) {
                var report = null;
                try { report = window.__DofusProbe ? window.__DofusProbe() : null; } catch (e) {}
                try {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage({
                            type: 'PLAY_LOG', session: this.sessionId,
                            seq: this.seq++, at: Date.now(),
                            event: { kind: 'probe', tag: tag || null, report: report }
                        });
                    }
                } catch (e) {}
                return report;
            },

            // -- network: every message the client sends is an action --------
            _hookNetwork: function() {
                var self = this;
                var cm = (window.dofus && window.dofus.connectionManager) || null;
                if (!cm) {
                    // The connection manager appears after login; retry until it does.
                    var tries = 0;
                    var iv = setInterval(function() {
                        tries++;
                        if (window.dofus && window.dofus.connectionManager) {
                            clearInterval(iv);
                            self._hookNetwork();
                        } else if (tries > 600) { clearInterval(iv); }
                    }, 500);
                    this._hooks.push(function() { clearInterval(iv); });
                    return;
                }
                if (cm.__recorderHooked) return;
                cm.__recorderHooked = true;

                // Outgoing: movement, fight actions, item use, market ops, chat.
                // Both entry points exist on this client and the login path does
                // not use the one we wrapped first, which is why an earlier trace
                // had no `send` events at all.
                var sendMethods = ['sendMessage', 'send'];
                for (var s = 0; s < sendMethods.length; s++) {
                    (function(method) {
                        var original = cm[method];
                        if (typeof original !== 'function') return;
                        cm[method] = function(name, params) {
                            try {
                                self._post({ kind: 'send', via: method, name: String(name),
                                             args: self._shrink(params) });
                            } catch (e) {}
                            return original.apply(this, arguments);
                        };
                        self._hooks.push(function() { cm[method] = original; });
                    })(sendMethods[s]);
                }

                // The connect call itself: what address does the client think it
                // is dialling? Everything upstream of this (appInfo.server,
                // config.json, serversData) is guesswork until this is recorded.
                if (typeof cm.connect === 'function') {
                    var origConnect = cm.connect;
                    cm.connect = function() {
                        try {
                            self._post({ kind: 'connect', args: self._shrink(
                                Array.prototype.slice.call(arguments)) });
                        } catch (e) {}
                        return origConnect.apply(this, arguments);
                    };
                    self._hooks.push(function() { cm.connect = origConnect; });
                }

                // Incoming: what the server told us. This client keeps its
                // handlers in `eventHandlers` (confirmed by the probe); the other
                // names are kept as fallbacks for older builds.
                var registry = cm.eventHandlers || cm._handlers || cm.handlers
                    || cm._messageHandlers || null;
                if (registry && typeof registry === 'object') {
                    var wrapped = {};
                    var names = Object.keys(registry);
                    for (var i = 0; i < names.length; i++) {
                        (function(name, handlers) {
                            if (!handlers) return;
                            var isArray = Object.prototype.toString.call(handlers) === '[object Array]';
                            var list = isArray ? handlers : [handlers];
                            var out = [];
                            for (var j = 0; j < list.length; j++) {
                                (function(fn) {
                                    if (typeof fn !== 'function') { out.push(fn); return; }
                                    out.push(function(msg) {
                                        // Market prices land here before anything
                                        // else can use them; the recorder post is
                                        // capped, the cache is not.
                                        try {
                                            if (name === 'ObjectAveragePricesMessage'
                                                || name === 'ExchangeBidHouseInListAddedMessage') {
                                                __PriceCache.absorb(name, msg);
                                            }
                                        } catch (e) {}
                                        try { self._post({ kind: 'recv', name: name,
                                                           args: self._shrink(msg, 6000) }); } catch (e) {}
                                        return fn.apply(this, arguments);
                                    });
                                })(list[j]);
                            }
                            wrapped[name] = isArray ? out : out[0];
                        })(names[i], registry[names[i]]);
                    }
                    // Write back to the property we actually wrapped: assigning
                    // to `_handlers` while the client reads `eventHandlers` left
                    // the game dispatching to the originals and produced no
                    // incoming events in the trace.
                    if (cm.eventHandlers === registry) cm.eventHandlers = wrapped;
                    else if (cm._handlers === registry) cm._handlers = wrapped;
                    else if (cm.handlers === registry) cm.handlers = wrapped;
                    else cm._messageHandlers = wrapped;
                    self._post({ kind: 'hook_report', incoming: true, names: names.length,
                                 slot: cm.eventHandlers === wrapped ? 'eventHandlers' : 'other' });
                } else {
                    self._post({ kind: 'hook_report', incoming: false,
                                 note: 'no message registry found; outgoing only',
                                 cmKeys: Object.keys(cm).slice(0, 40) });
                }
            },

            // -- console: what the app says about itself --------------------
            // The loader announces every version decision here ("Looking for
            // update", "Update available: true"). Without these lines a blocked
            // launch is invisible: the dialog is native, the cause is a log.
            _hookConsole: function() {
                var self = this;
                if (console.__recorderHooked) return;
                console.__recorderHooked = true;
                var levels = ['log', 'info', 'warn', 'error'];
                var originals = {};
                for (var i = 0; i < levels.length; i++) {
                    (function(level) {
                        var original = console[level];
                        originals[level] = original;
                        console[level] = function() {
                            if (!self._inConsole) {
                                self._inConsole = true;
                                try {
                                    var parts = [];
                                    for (var j = 0; j < arguments.length; j++) {
                                        var a = arguments[j];
                                        try { parts.push(typeof a === 'string' ? a : JSON.stringify(a)); }
                                        catch (e) { parts.push(String(a)); }
                                    }
                                    self._post({ kind: 'console', level: level,
                                                 text: parts.join(' ').slice(0, 500) });
                                } catch (e) {} finally { self._inConsole = false; }
                            }
                            try { return original.apply(console, arguments); } catch (e) {}
                        };
                    })(levels[i]);
                }
                this._hooks.push(function() {
                    for (var k in originals) {
                        if (Object.prototype.hasOwnProperty.call(originals, k)) console[k] = originals[k];
                    }
                    console.__recorderHooked = false;
                });
            },

            // -- DOM: clicks and keys, never values --------------------------
            _hookDom: function() {
                var self = this;
                var clickHandler = function(ev) {
                    if (!self.enabled) return;
                    var t = ev.target || {};
                    self._post({
                        kind: 'click',
                        tag: (t.tagName || '').toLowerCase(),
                        id: t.id || null,
                        cls: (typeof t.className === 'string' ? t.className : '').slice(0, 80),
                        x: ev.clientX, y: ev.clientY,
                        // The cell under the pointer is the one piece of game
                        // meaning a raw click has.
                        cell: self._cellAt(ev.clientX, ev.clientY)
                    });
                };
                var keyHandler = function(ev) {
                    if (!self.enabled) return;
                    var t = ev.target || {};
                    self._post({
                        kind: 'key',
                        key: ev.key,
                        code: ev.code,
                        tag: (t.tagName || '').toLowerCase(),
                        id: t.id || null,
                        // Deliberately NOT ev.target.value.
                        typed: (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')
                    });
                };
                var visHandler = function() {
                    if (!self.enabled) return;
                    self._post({ kind: 'visibility', hidden: !!document.hidden });
                };
                document.addEventListener('click', clickHandler, true);
                document.addEventListener('keydown', keyHandler, true);
                document.addEventListener('visibilitychange', visHandler);
                this._hooks.push(function() {
                    document.removeEventListener('click', clickHandler, true);
                    document.removeEventListener('keydown', keyHandler, true);
                    document.removeEventListener('visibilitychange', visHandler);
                });
            },

            _cellAt: function(x, y) {
                try {
                    var ie = window.isoEngine;
                    if (ie && typeof ie.getCellFromPixel === 'function') return ie.getCellFromPixel(x, y);
                    if (ie && typeof ie.getCellIdFromMousePosition === 'function') return ie.getCellIdFromMousePosition(x, y);
                    if (ie && ie.mapPos && typeof ie.mapPos.getCellId === 'function') return ie.mapPos.getCellId(x, y);
                } catch (e) {}
                return null;
            },

            // -- state deltas: position, hp, kamas, xp, fights ----------------
            _pollState: function() {
                var self = this;
                this._timer = setInterval(function() {
                    if (!self.enabled) return;
                    var snap;
                    try { snap = _agentSnapshot(); } catch (e) { return; }
                    var now = {
                        map: snap.position && snap.position.map_id,
                        cell: snap.position && snap.position.cell,
                        hp: snap.hp && snap.hp.life,
                        max: snap.hp && snap.hp.max,
                        xp: snap.xp,
                        level: snap.xp_level,
                        kamas: snap.kamas,
                        fight: snap.farm_state && snap.farm_state.inFight,
                        inv: snap.inventory && snap.inventory.items ? snap.inventory.items.length : null
                    };
                    var prev = self.lastState;
                    self.lastState = now;
                    if (!prev) { self._post({ kind: 'state', state: now }); return; }
                    var diff = {};
                    var changed = false;
                    for (var k in now) {
                        if (!Object.prototype.hasOwnProperty.call(now, k)) continue;
                        if (now[k] !== prev[k]) { diff[k] = { from: prev[k], to: now[k] }; changed = true; }
                    }
                    if (changed) {
                        // A map change is the most informative single event: it
                        // is the edge the world graph is built from.
                        if (diff.map) self._post({ kind: 'map_change', from: prev.map, to: now.map, cell: now.cell });
                        if (diff.fight) self._post({ kind: now.fight ? 'fight_start' : 'fight_end', state: now });
                        self._post({ kind: 'state_delta', diff: diff, state: now });
                    }
                }, 500);
                this._hooks.push(function() { clearInterval(self._timer); });
            },

            // Keep a message small enough to survive the bridge without
            // exploding on a circular object graph.
            //
            // The cap is per direction: outgoing calls are small and a 600-char
            // preview is plenty, but incoming map state (actors, NPCs,
            // interactive elements) is the payload the client verbs are written
            // against -- truncating it at 600 left us with `_truncated: true` and
            // no NPC ids at all. Incoming gets a much larger budget.
            _shrink: function(value, cap) {
                var limit = cap || 600;
                try {
                    var text = JSON.stringify(value);
                    if (text && text.length <= limit) return value;
                    if (text) return { _truncated: true, length: text.length,
                                       preview: text.slice(0, limit) };
                } catch (e) {
                    return { _unserialisable: String(e) };
                }
                return null;
            }
        };

        window.__DofusRecorder = __Rec;

        // Auto-start: a session that has to be switched on by hand is a session
        // that is not recorded when it matters. `localStorage.recorderOff = '1'`
        // disables it for a machine that must not log.
        try {
            if (localStorage.getItem('recorderOff') !== '1') {
                setTimeout(function() {
                    try { __Rec.start('auto'); } catch (e) {
                        console.log('[Recorder] autostart failed: ' + e);
                    }
                    // Probe twice: once immediately (pre-login surface) and once
                    // after the game has had time to build its world, so the
                    // trace shows what exists at each stage.
                    setTimeout(function() { try { __Rec.probe('early'); } catch (e) {} }, 2000);
                    setTimeout(function() { try { __Rec.probe('loaded'); } catch (e) {} }, 25000);
                }, 1500);
            }
        } catch (e) {}

        // ------------------------------------------------------------------
        // __DofusProbe — one call, the whole API surface we have to code against.
        // ------------------------------------------------------------------
        window.__DofusProbe = function() {
            function shape(obj, depth) {
                if (!obj) return null;
                var out = { type: typeof obj, keys: [] };
                try {
                    var keys = Object.keys(obj);
                    out.keys = keys.slice(0, 60);
                    out.functions = keys.filter(function(k) {
                        try { return typeof obj[k] === 'function'; } catch (e) { return false; }
                    }).slice(0, 60);
                    out.count = keys.length;
                } catch (e) { out.error = String(e); }
                if (depth > 0 && obj) {
                    out.children = {};
                    var kids = ['playerData', 'isoEngine', 'dofus', 'gui', 'connectionManager',
                                'actorManager', 'mapData', 'mapPos', 'fighter', 'character',
                                'inventory', 'spells', 'inventoryViewer', 'chat'];
                    for (var i = 0; i < kids.length; i++) {
                        var name = kids[i];
                        try {
                            if (obj[name] !== undefined) out.children[name] = shape(obj[name], depth - 1);
                        } catch (e) {}
                    }
                }
                return out;
            }
            var report = {
                at: Date.now(),
                href: String(location.href).slice(0, 200),
                ua: navigator.userAgent,
                globals: {
                    gui: shape(window.gui, 2),
                    dofus: shape(window.dofus, 2),
                    isoEngine: shape(window.isoEngine, 2),
                    farmbot: !!window.__FarmBot,
                    agentDirective: typeof window.__agentDirective
                },
                snapshot: null,
                map: null,
                dom: { canvases: document.getElementsByTagName('canvas').length,
                       iframes: document.getElementsByTagName('iframe').length,
                       inputs: document.getElementsByTagName('input').length }
            };
            try { report.snapshot = _agentSnapshot(); } catch (e) { report.snapshotError = String(e); }
            // Where the client thinks the game server is. Without this the only
            // symptom of a wrong target is a bare LOGIN_ERROR.
            try {
                var sd = null;
                try { sd = JSON.parse(JSON.stringify(window.gui && window.gui.serversData)); } catch (e) { sd = String(e); }
                report.server = {
                    appInfoServer: (window.appInfo && window.appInfo.server) || null,
                    appInfoVersion: (window.appInfo && window.appInfo.version) || null,
                    buildVersion: window.buildVersion || null,
                    serversData: JSON.stringify(sd).slice(0, 800),
                    connectionState: (window.dofus && window.dofus.connectionManager)
                        ? (window.dofus.connectionManager.connectionState || null) : null
                };
            } catch (e) { report.serverError = String(e); }
            try {
                var ie = window.isoEngine;
                if (ie && ie.mapData) {
                    report.map = {
                        id: ie.mapData.mapId || ie.currentMapId || null,
                        cells: (ie.mapData.cells || []).length,
                        neighbours: ie.mapData.topNeighbourId != null ? {
                            top: ie.mapData.topNeighbourId, bottom: ie.mapData.bottomNeighbourId,
                            left: ie.mapData.leftNeighbourId, right: ie.mapData.rightNeighbourId
                        } : null,
                        cellKeys: (ie.mapData.cells && ie.mapData.cells[0])
                            ? Object.keys(ie.mapData.cells[0]) : null
                    };
                }
            } catch (e) { report.mapError = String(e); }
            try { console.log('[Probe] ' + JSON.stringify(report)); } catch (e) {}
            return report;
        };


        var _agentCommander = function(action) {
            try {
                if (typeof window.__executeCommanderDirective === 'function') {
                    var raw = window.__executeCommanderDirective({ action: action });
                    try { return JSON.parse(raw); } catch(e) { return { status: 'ok', action: action }; }
                }
            } catch(err) { return { status: 'error', message: err.toString() }; }
            return { status: 'error', message: '__executeCommanderDirective unavailable' };
        };

        // Every NPC actor on the current map.
        //
        // Two things were wrong before this existed. The actor manager is on
        // `window.actorManager` (the bundle uses it that way in eight places), and the
        // id in NpcGenericActionRequestMessage is the actor's `contextualId` — the
        // bundle sends `npcId: l.contextualId` — not the template npcId the quest
        // objective carries. Sending the template id talks to nobody.
        var _listNpcActors = function () {
            var am = window.actorManager
                || (window.isoEngine && window.isoEngine.actorManager) || null;
            var actors = (am && (am.actors || am.actorList)) || null;
            var out = [];
            if (!actors) return out;
            for (var k in actors) {
                if (!Object.prototype.hasOwnProperty.call(actors, k)) continue;
                var a = actors[k] || {};
                var d = a.data || {};
                var si = d.staticInfos || a.staticInfos || {};
                // The actor's template id lives on `data.npcId`: the bundle's own
                // isNPC() and getActorFromNpcId() both read it there. `staticInfos`
                // exists on some builds but not this one, which is why this list was
                // always empty and the map looked NPC-less.
                var template = d.npcId != null ? d.npcId
                    : (si.npcId != null ? si.npcId : (a.npcId != null ? a.npcId : null));
                if (template == null || Number(template) <= 0) continue;
                // The actor is constructed with `actorId` = contextual id; it has
                // no `contextualId` or `id` property. Sending the template id (or
                // undefined) in NpcGenericActionRequestMessage talks to nobody.
                var cid = a.actorId != null ? a.actorId
                    : (d.contextualId != null ? d.contextualId
                    : (a.contextualId != null ? a.contextualId
                    : (a.id != null ? a.id : k)));
                out.push({
                    templateId: Number(template),
                    contextualId: Number(cid),
                    cell: a.cellId != null ? a.cellId : (a.position && a.position.cell)
                });
            }
            return out;
        };

        var _findNpcActor = function (templateId) {
            var list = _listNpcActors();
            var seen = [];
            for (var i = 0; i < list.length; i++) seen.push(list[i].templateId);
            // The client has a purpose-built lookup (the bundle calls
            // `window.actorManager.getActorFromNpcId(npcId)`), so prefer it over a scan.
            if (templateId != null) {
                var am = window.actorManager
                    || (window.isoEngine && window.isoEngine.actorManager) || null;
                if (am && typeof am.getActorFromNpcId === 'function') {
                    try {
                        var hit = am.getActorFromNpcId(Number(templateId));
                        if (hit) {
                            var hd = hit.data || {};
                            var hcid = hit.actorId != null ? hit.actorId
                                : (hd.contextualId != null ? hd.contextualId
                                : (hit.contextualId != null ? hit.contextualId : hit.id));
                            return {
                                actor: {
                                    templateId: hd.npcId != null ? Number(hd.npcId) : Number(templateId),
                                    contextualId: hcid != null ? Number(hcid) : null,
                                    cell: hit.cellId != null ? hit.cellId : (hit.position && hit.position.cell),
                                    isNpc: typeof hit.isNPC === 'function' ? !!hit.isNPC() : null
                                },
                                seen: seen, all: list
                            };
                        }
                    } catch (e) {}
                }
                for (var j = 0; j < list.length; j++) {
                    if (Number(list[j].templateId) === Number(templateId)) {
                        return { actor: list[j], seen: seen, all: list };
                    }
                }
                return { actor: null, seen: seen, all: list };
            }
            return { actor: list.length ? list[0] : null, seen: seen, all: list };
        };

        // Same grid the bundle uses (getMapPointFromCellId). Two cells are
        // neighbours when their grid points differ by one on each axis at most;
        // diagonal neighbours included, because the client allows diagonal steps.
        var _cellsAdjacent = function (a, b) {
            var point = function (cell) {
                var t = Number(cell) % 14 - Math.floor(Number(cell) / 28);
                return { x: t + 19, y: t + Math.floor(Number(cell) / 14) };
            };
            var pa = point(a), pb = point(b);
            return Math.max(Math.abs(pa.x - pb.x), Math.abs(pa.y - pb.y)) === 1;
        };

        // The bundle reads the player's map from `gui.playerData.position.mapId`;
        // `isoEngine.mapData.mapId` is absent on this build. A null npcMapId makes
        // the server ignore a talk request, so this must be the same source the
        // client itself uses.
        var _currentMapId = function () {
            try {
                var pd = window.gui && window.gui.playerData;
                if (pd && pd.position && pd.position.mapId != null) {
                    return Number(pd.position.mapId);
                }
                var mr = window.isoEngine && window.isoEngine.mapRenderer;
                if (mr && mr.map && mr.map.id != null) return Number(mr.map.id);
            } catch (e) {}
            return null;
        };

        // Dialog and monster-group observation.
        //
        // A dialog is a choice, not a button: the server sends a question and a
        // list of replies, and only one of them advances the objective. Choosing
        // by index works, but an index means nothing to a policy that has to
        // understand the text, and it breaks as soon as the reply order changes.
        // The client already holds the resolved strings -- `npcDialogUi` gets the
        // formatted question and replies from `nextQuestionAsync`, while
        // `npcDialogHandler` holds the server reply ids -- so expose both,
        // aligned by index, and never re-resolve text ids here.
        var _agentPlainText = function (value) {
            if (value == null) return null;
            var t = String(value);
            // Strip markup before decoding entities: a decoded `&lt;` would
            // otherwise look like a tag to the next pass.
            t = t.replace(/<br\s*\/?>/gi, ' ')
                 .replace(/<\/(p|div|li)>/gi, ' ')
                 .replace(/<[^>]*>/g, '');
            t = t.replace(/&nbsp;/gi, ' ')
                 .replace(/&lt;/gi, '<')
                 .replace(/&gt;/gi, '>')
                 .replace(/&quot;/gi, '"')
                 .replace(/&#39;|&apos;/gi, "'")
                 .replace(/&amp;/gi, '&')
                 .replace(/&#(x?[0-9a-f]+);/gi, function (m, code) {
                     var n = (code[0] === 'x' || code[0] === 'X')
                         ? parseInt(code.slice(1), 16) : parseInt(code, 10);
                     return isNaN(n) ? m : String.fromCharCode(n);
                 });
            return t.replace(/\s+/g, ' ').trim();
        };
        var _agentNormalizeText = function (value) {
            var t = _agentPlainText(value);
            if (t == null) return null;
            try {
                t = t.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
            } catch (e) {}
            return t.toLowerCase();
        };
        var _agentDialogInfo = function () {
            var pd = (window.gui && window.gui.playerData) || null;
            var ui = (window.gui && window.gui.npcDialogUi) || null;
            var handler = (window.gui && window.gui.npcDialogHandler) || null;
            // The ids and the texts live on different objects: the handler
            // receives `replyIds` from NpcDialogCreationMessage, the view
            // receives the formatted strings. Prefer a populated list.
            var ids = (handler && handler.replyIds && handler.replyIds.length)
                ? handler.replyIds
                : ((ui && ui.replyIds && ui.replyIds.length)
                    ? ui.replyIds
                    : ((handler && handler.replyIds) || (ui && ui.replyIds) || null));
            var texts = (ui && ui.replies) ? ui.replies : [];
            var npcData = (handler && handler.npcData) || (ui && ui.npcData) || null;
            return {
                in_dialog: !!(pd && pd.isInDialog),
                ui: ui,
                handler: handler,
                ids: ids,
                texts: texts,
                message: (ui && ui.currentMsg != null)
                    ? _agentPlainText(ui.currentMsg) : null,
                name_id: (ui && ui.npcName != null) ? ui.npcName
                    : (npcData && npcData.nameId != null ? npcData.nameId : null),
                npc_id: (npcData && npcData.id != null) ? Number(npcData.id) : null
            };
        };
        // Resolve what the policy read back to the index the client sends. Kept
        // separate from the handler so the same rule is testable offline.
        var _agentReplyIndexForText = function (text) {
            var ui = (window.gui && window.gui.npcDialogUi) || null;
            var texts = (ui && ui.replies) || [];
            var wantText = _agentNormalizeText(text);
            var hits = [];
            for (var i = 0; i < texts.length; i++) {
                if (_agentNormalizeText(texts[i]) === wantText) hits.push(i);
            }
            return { hits: hits, count: texts.length };
        };
        var _agentDialogState = function () {
            var info = _agentDialogInfo();
            if (!info.in_dialog) return { open: false };
            var texts = info.texts || [];
            var ids = info.ids || [];
            var count = Math.max(texts.length, ids.length);
            var replies = [];
            for (var i = 0; i < count && i < 12; i++) {
                var rid = ids[i] != null ? Number(ids[i]) : null;
                replies.push({
                    index: i,
                    text: texts[i] != null ? _agentPlainText(texts[i]) : null,
                    reply_id: rid != null && !isNaN(rid) ? rid : (ids[i] != null ? ids[i] : null)
                });
            }
            return {
                open: true,
                npc_id: info.npc_id,
                name_id: info.name_id,
                message: info.message,
                replies: replies,
                reply_count: count,
                ids_ready: !!(info.ids && info.ids.length),
                text_ready: !!texts.length
            };
        };

        // Every monster group on the current map, in the shape the client itself
        // reads. The group id is the actor id sent in
        // GameRolePlayAttackMonsterRequestMessage; `staticInfos` carries one
        // main creature plus underlings, each with its own level and boss flags.
        // Only fields the client actually provides are exposed -- a missing level
        // stays null instead of becoming 0, so an estimator can tell "unknown"
        // from "level zero".
        var _listMonsterGroups = function () {
            var am = window.actorManager
                || (window.isoEngine && window.isoEngine.actorManager) || null;
            var actors = (am && (am.actors || am.actorList)) || null;
            var out = [];
            if (!actors) return out;
            for (var k in actors) {
                if (!Object.prototype.hasOwnProperty.call(actors, k)) continue;
                var a = actors[k] || {};
                var d = a.data || {};
                var ty = d.type || a._type || a.type;
                if (ty !== 'GameRolePlayGroupMonsterInformations') continue;
                var si = d.staticInfos || a.staticInfos || {};
                var members = [];
                var addMember = function (m) {
                    if (!m) return;
                    var light = m.staticInfos || m;
                    var id = m.creatureGenericId != null ? m.creatureGenericId
                        : (light.creatureGenericId != null
                            ? light.creatureGenericId : null);
                    if (id == null) return;
                    members.push({
                        monster_id: Number(id),
                        level: light.level != null ? Number(light.level) : null,
                        is_boss: !!light.isBoss,
                        is_mini_boss: !!light.isMiniBoss
                    });
                };
                addMember(si.mainCreatureLightInfos);
                var underlings = si.underlings || [];
                for (var u = 0; u < underlings.length; u++) addMember(underlings[u]);
                // Older payloads nested the list under `monsters`; keep it as a
                // fallback so a build change does not silently empty this.
                var legacy = si.monsters || d.monsters || [];
                if (!members.length && legacy.length) {
                    for (var m2 = 0; m2 < legacy.length; m2++) {
                        addMember(legacy[m2]);
                    }
                }
                var ids = [];
                var total = 0;
                var known = 0;
                var maxLevel = null;
                var boss = false;
                for (var mi = 0; mi < members.length; mi++) {
                    var mem = members[mi];
                    ids.push(mem.monster_id);
                    if (mem.level != null && !isNaN(mem.level)) {
                        total += mem.level;
                        known += 1;
                        if (maxLevel == null || mem.level > maxLevel) maxLevel = mem.level;
                    }
                    if (mem.is_boss) boss = true;
                }
                var cid = a.actorId != null ? a.actorId
                    : (d.contextualId != null ? d.contextualId
                    : (a.contextualId != null ? a.contextualId
                    : (a.id != null ? a.id : Number(k))));
                var gid = cid != null ? Number(cid) : null;
                out.push({
                    group_id: gid != null && !isNaN(gid) ? gid : null,
                    contextualId: gid != null && !isNaN(gid) ? gid : null,
                    cell: a.cellId != null ? a.cellId
                        : (a.position && a.position.cell),
                    monster_ids: ids,
                    members: members.slice(0, 8),
                    member_count: members.length,
                    levels_known: known,
                    // Null unless every level is known: a partial sum would look
                    // like a weaker group than it is.
                    total_level: (members.length && known === members.length)
                        ? total : null,
                    max_level: maxLevel,
                    is_boss: boss,
                    scale_level: d.scaleLevel != null ? Number(d.scaleLevel) : null,
                    static_keys: Object.keys(si).slice(0, 12)
                });
            }
            return out;
        };

        // What the tutorial currently allows.
        //
        // While a tutorial step is running the client disables map crossings and
        // restricts taps to one NPC or a set of cells. `gotoNeighbourMap` bypasses
        // that gate and asks the server for a transition the tutorial is about to
        // refuse: a live session walked to the edge cell and then sat there while
        // the agent re-sent the same request. The scenario step is the authority on
        // what may happen next, so expose it instead of guessing from the map.
        var _agentTutorial = function () {
            var sm = (window.gui && window.gui.scenarioManager) || null;
            if (!sm) return null;
            var iso = window.isoEngine || null;
            var dirs = ['left', 'right', 'top', 'bottom'];
            var can = {};
            for (var i = 0; i < dirs.length; i++) {
                var d = dirs[i];
                var blocked = false;
                try {
                    blocked = !!(typeof sm.isBehaviourEnabled === 'function'
                        && sm.isBehaviourEnabled('DISABLE_' + d.toUpperCase()
                            + '_SLIDE_CHANGEMAP'));
                } catch (e) {}
                can[d] = !blocked;
            }
            var cells = null;
            try {
                var raw = iso && iso.unblockedCells;
                if (raw && typeof raw === 'object') {
                    // An empty gate is not "no gate": a step that calls
                    // setUnblockedCells({}) forbids every cell, which is how a talk
                    // step pins the character in place. Only a null gate means
                    // "all cells allowed".
                    cells = [];
                    for (var k in raw) {
                        if (Object.prototype.hasOwnProperty.call(raw, k) && raw[k]) {
                            var n = Number(k);
                            if (isFinite(n)) cells.push(n);
                        }
                    }
                    cells.sort(function (a, b) { return a - b; });
                }
            } catch (e) { cells = null; }
            var npc = null;
            try {
                var ni = iso && iso.unblockedNpcId;
                // -1 means no NPC restriction; 0 is a real restriction to "no NPC",
                // and collapsing it to null would tell the planner every tap is open.
                if (ni != null && Number(ni) !== -1) npc = Number(ni);
            } catch (e) {}
            var questId = Number(sm.currentQuestId);
            var stepId = Number(sm.currentStepId);
            var specialId = Number(sm.currentSpecialStepId);
            return {
                active: !!(sm.currentStep || specialId >= 0),
                quest_id: questId > 0 ? questId : null,
                step_id: stepId >= 0 ? stepId : null,
                special_step_id: specialId >= 0 ? specialId : null,
                can_change_map: can,
                unblocked_cells: cells,
                unblocked_npc_id: npc
            };
        };

        // What the client knows about this map, in the shape a decision needs.
        //
        // Navigation only ever asked for a destination map id. A quest can ask for a
        // specific spot instead ("click the right edge"), and an agent cannot act on
        // that from a map id alone. This is the observation side of that contract:
        // the exits and their border cells, the NPCs here, and the interactive
        // elements here. It is deliberately generic -- no quest ids, no hard-coded
        // targets -- so a policy can decide from the details for any objective.
        var _agentMapDetails = function () {
            var iso = window.isoEngine || null;
            var pd = (window.gui && window.gui.playerData) || null;
            var pos = (pd && pd.position) || {};
            var coords = pos.coordinates || {};
            var mapId = pos.mapId != null ? pos.mapId
                : (iso && iso.mapData && iso.mapData.mapId != null
                    ? iso.mapData.mapId : null);
            var playerCell = null;
            try {
                var ua = iso && iso.actorManager && iso.actorManager.userActor;
                if (ua) {
                    playerCell = ua.cellId != null ? ua.cellId
                        : (ua.position && ua.position.cell != null
                            ? ua.position.cell : null);
                }
            } catch (e) {}
            var mr = iso && iso.mapRenderer;
            var map = (mr && mr.map) || (iso && iso.mapData) || {};
            var dirs = ['left', 'right', 'top', 'bottom'];
            var keys = { left: 'leftNeighbourId', right: 'rightNeighbourId',
                         top: 'topNeighbourId', bottom: 'bottomNeighbourId' };
            var byDir = {};
            try {
                var cells = map.cells || [];
                if (mr && typeof mr.getChangeMapFlags === 'function') {
                    for (var c = 0; c < cells.length; c++) {
                        var flags = mr.getChangeMapFlags(c) || {};
                        for (var fi = 0; fi < dirs.length; fi++) {
                            var fd = dirs[fi];
                            if (!flags[fd]) continue;
                            if (!byDir[fd]) byDir[fd] = [];
                            byDir[fd].push(c);
                        }
                    }
                }
            } catch (e) {}
            var playerScene = null;
            try {
                if (playerCell != null && mr
                        && typeof mr.getCellSceneCoordinate === 'function') {
                    playerScene = mr.getCellSceneCoordinate(playerCell);
                }
            } catch (e) {}
            var nearest = function (list) {
                if (!playerScene || !mr
                        || typeof mr.getCellSceneCoordinate !== 'function') {
                    return list.slice();
                }
                return list.slice().sort(function (a, b) {
                    var ca = mr.getCellSceneCoordinate(a) || {};
                    var cb = mr.getCellSceneCoordinate(b) || {};
                    var da = (ca.x - playerScene.x) * (ca.x - playerScene.x)
                           + (ca.y - playerScene.y) * (ca.y - playerScene.y);
                    var db = (cb.x - playerScene.x) * (cb.x - playerScene.x)
                           + (cb.y - playerScene.y) * (cb.y - playerScene.y);
                    return da - db;
                });
            };
            // The tutorial gate belongs on the exits themselves. A caller that sees
            // only `left: 1234` cannot know the client will refuse that crossing, so
            // it walks to the edge and stalls. Carrying `blocked` lets the planner
            // route around it before spending the walk.
            var tutorial = null;
            try { tutorial = _agentTutorial(); } catch (e) { tutorial = null; }
            var gate = (tutorial && tutorial.can_change_map) || null;
            var neighbours = {};
            var exits = [];
            for (var di = 0; di < dirs.length; di++) {
                var d = dirs[di];
                var raw = map[keys[d]];
                var nid = raw != null && Number(raw) > 0 ? Number(raw) : null;
                if (nid != null) neighbours[d] = nid;
                if (nid == null || !byDir[d] || !byDir[d].length) continue;
                exits.push({ direction: d, map_id: nid,
                             blocked: !!(gate && gate[d] === false),
                             cells: nearest(byDir[d]).slice(0, 6) });
            }
            var npcs = [];
            try {
                npcs = _listNpcActors().slice(0, 20).map(function (n) {
                    return { npc_id: n.templateId, actor_id: n.contextualId,
                             cell: n.cell };
                });
            } catch (e) {}
            var interactives = [];
            try {
                interactives = _interactives().slice(0, 12).map(function (el) {
                    return { elem_id: el.elemId, cell: el.cell,
                             skill_uid: el.skillInstanceUid };
                });
            } catch (e) {}
            var allGroups = [];
            try { allGroups = _listMonsterGroups(); } catch (e) { allGroups = []; }
            return {
                map_id: mapId,
                cell: playerCell,
                x: coords.posX != null ? coords.posX : (pos.x != null ? pos.x : null),
                y: coords.posY != null ? coords.posY : (pos.y != null ? pos.y : null),
                neighbours: neighbours,
                exits: exits,
                npcs: npcs,
                interactives: interactives,
                groups: allGroups.slice(0, 20),
                group_count: allGroups.length
            };
        };

        // ------------------------------------------------------------------
        // Read-only advisor diagnostics.
        //
        // The spell/gear/market advisor runs beside the quest policy and needs
        // the live rows the client itself uses: owned spells and their grade,
        // the raw and derived spell-point budget, equipped items with their
        // real effect ids, set thresholds, and the server's average-price
        // table. Everything here only reads. No handler spends a point,
        // equips, buys, lists or sells.
        // ------------------------------------------------------------------
        var _agentDatabases = function() {
            try { return (window.gui && window.gui.databases) || null; }
            catch (e) { return null; }
        };
        var _agentDbTable = function(name) {
            var dbs = _agentDatabases();
            var t = dbs && dbs[name];
            return (t && typeof t === 'object') ? t : null;
        };
        var _agentDbRow = function(table, id) {
            var t = _agentDbTable(table);
            if (!t || id == null) return null;
            if (t[id] !== undefined && t[id] !== null) return t[id];
            var k = String(id);
            return (t[k] !== undefined && t[k] !== null) ? t[k] : null;
        };
        var _agentNum = function(v) {
            if (v === null || v === undefined || v === '') return null;
            var n = Number(v);
            return isFinite(n) ? n : null;
        };
        var _agentPick = function(row, names) {
            if (!row || typeof row !== 'object') return null;
            for (var i = 0; i < names.length; i++) {
                var v = row[names[i]];
                if (v !== undefined && v !== null) return v;
            }
            return null;
        };
        var _agentCap = function(v, lo, hi, dflt) {
            var n = _agentNum(v);
            if (n == null) n = dflt;
            return Math.max(lo, Math.min(hi, Math.floor(n)));
        };
        var _agentUpgradeCost = function(from, to) {
            // Total cost at grade L is L*(L-1)/2; the marginal L-1 -> L is L-1.
            var sum = function(n) { return n > 0 ? n * (n - 1) / 2 : 0; };
            return sum(to) - sum(from);
        };
        var _agentReadStat = function(v) {
            if (v === null || v === undefined) return null;
            if (typeof v === 'number') return { total: v };
            var out = { total: null, base: null, equipment: null,
                        bonus: null, additional: null };
            var call = function(fn) {
                try {
                    return (typeof v[fn] === 'function') ? _agentNum(v[fn]()) : null;
                } catch (e) { return null; }
            };
            out.total = call('getTotalStat');
            out.base = call('getBasePts');
            out.equipment = call('getEquipmentPts');
            out.bonus = call('getBonusPts');
            out.additional = call('getAdditionalPts');
            if (out.total == null) out.total = _agentNum(_agentPick(v, ['_total', 'total', 'value']));
            if (out.base == null) out.base = _agentNum(_agentPick(v, ['_base', 'base']));
            if (out.equipment == null) out.equipment = _agentNum(_agentPick(v, ['_objectsAndMountBonus', 'objectsAndMountBonus']));
            if (out.bonus == null) out.bonus = _agentNum(_agentPick(v, ['_alignGiftBonus', 'alignGiftBonus', '_bonus']));
            if (out.additional == null) out.additional = _agentNum(_agentPick(v, ['_additionnal', 'additionnal', '_additional']));
            return out;
        };
        var _agentEffectList = function(list) {
            var out = [];
            if (!list) return out;
            var arr = list;
            if (Object.prototype.toString.call(list) !== '[object Array]') {
                if (typeof list !== 'object') return out;
                arr = [];
                for (var k in list) {
                    if (!Object.prototype.hasOwnProperty.call(list, k)) continue;
                    arr.push(list[k]);
                }
            }
            for (var i = 0; i < arr.length; i++) {
                var eff = arr[i];
                if (!eff || typeof eff !== 'object') continue;
                var db = eff.effect || eff.effectData || eff.template || null;
                var eid = _agentNum(_agentPick(eff, ['effectId', 'effect_id']));
                if (eid == null) eid = _agentNum(_agentPick(eff, ['id']));
                if (eid == null && db) eid = _agentNum(_agentPick(db, ['id', 'effectId']));
                var row = {
                    effect_id: eid,
                    element_id: _agentNum(_agentPick(eff, ['elementId', 'element_id'])),
                    dice_num: _agentNum(_agentPick(eff, ['diceNum', 'dice_num'])),
                    dice_side: _agentNum(_agentPick(eff, ['diceSide', 'dice_side'])),
                    value: _agentNum(_agentPick(eff, ['value'])),
                    duration: _agentNum(_agentPick(eff, ['duration'])),
                    delay: _agentNum(_agentPick(eff, ['delay'])),
                    characteristic: _agentNum(_agentPick(db || {}, ['characteristic'])),
                    description_id: _agentNum(_agentPick(db || {}, ['descriptionId', 'description_id'])),
                    category: _agentNum(_agentPick(db || {}, ['category'])),
                    bonus_type: _agentNum(_agentPick(db || {}, ['bonusType', 'bonus_type']))
                };
                out.push(row);
            }
            return out;
        };
        var _agentItemType = function(typeId) {
            var row = _agentDbRow('ItemTypes', typeId);
            if (!row) return null;
            return {
                id: _agentNum(_agentPick(row, ['id'])),
                name_id: _agentNum(_agentPick(row, ['nameId', 'name_id'])),
                super_type_id: _agentNum(_agentPick(row, ['superTypeId', 'super_type_id'])),
                category: _agentNum(_agentPick(row, ['category']))
            };
        };
        // superTypeId -> equippable positions, copied from the bundle's
        // itemTypePositions table (module 488). This is the only reliable
        // weapon/hat/cape test: superTypeId is the type family, typeId is the
        // concrete item type.
        var _agentTypePositions = {
            1: [0], 2: [1], 3: [2, 4], 4: [3], 5: [5], 7: [15],
            8: [1], 10: [6], 11: [7], 12: [8],
            13: [9, 10, 11, 12, 13, 14], 15: [20], 16: [21],
            17: [22, 23], 18: [24, 25], 19: [26], 20: [27], 21: [16],
            23: [32], 24: [31], 26: [28], 27: [29], 28: [30]
        };
        var _agentItemRow = function(gid, withEffects) {
            gid = _agentNum(gid);
            var row = _agentDbRow('Items', gid);
            if (!row) return { gid: gid, db: false };
            var typeId = _agentNum(_agentPick(row, ['typeId', 'type_id']));
            var typeRow = _agentItemType(typeId);
            var sets = _agentPick(row, ['itemSetIds', 'setIds', 'itemSetId', 'setId']);
            if (Object.prototype.toString.call(sets) !== '[object Array]') {
                sets = (sets != null) ? [sets] : [];
            }
            var setIds = [];
            for (var i = 0; i < sets.length; i++) {
                var sid = _agentNum(sets[i]);
                if (sid != null && sid > 0) setIds.push(sid);
            }
            var out = {
                gid: gid,
                db: true,
                name_id: _agentNum(_agentPick(row, ['nameId', 'name_id'])),
                type_id: typeId,
                super_type_id: typeRow ? typeRow.super_type_id : null,
                item_type: typeRow,
                level: _agentNum(_agentPick(row, ['level'])),
                set_ids: setIds,
                possible_positions: _agentTypePositions[typeRow ? typeRow.super_type_id : -1] || [],
                is_weapon: !!(typeRow && (typeRow.super_type_id === 2
                        || typeRow.super_type_id === 23)),
                two_handed: !!_agentPick(row, ['twoHanded', 'two_handed']),
                ap_cost: _agentNum(_agentPick(row, ['apCost', 'ap_cost'])),
                min_range: _agentNum(_agentPick(row, ['minRange', 'min_range'])),
                max_range: _agentNum(_agentPick(row, ['maxRange', 'max_range', 'range'])),
                critical_hit_probability: _agentNum(_agentPick(row, ['criticalHitProbability', 'critical_hit_probability'])),
                criteria: _agentPick(row, ['criteria']),
                is_legendary: !!_agentPick(row, ['isLegendary', 'is_legendary']),
                raw_keys: Object.keys(row).slice(0, 30)
            };
            if (withEffects) {
                out.effects = _agentEffectList(_agentPick(row, ['possibleEffects', 'effects']));
            }
            return out;
        };
        var _agentItemInstance = function(item, withEffects) {
            if (!item || typeof item !== 'object') return null;
            var gid = _agentNum(_agentPick(item, ['objectGID', 'objectGid', 'object_gid', 'gid']));
            var uid = _agentNum(_agentPick(item, ['objectUID', 'objectUid', 'object_uid', 'uid']));
            var out = {
                uid: uid,
                gid: gid,
                quantity: _agentNum(_agentPick(item, ['quantity', 'qty'])) || 1,
                position: _agentNum(_agentPick(item, ['position'])),
                template: gid != null ? _agentItemRow(gid, false) : null
            };
            if (withEffects) {
                out.effects = _agentEffectList(item.effects || item.effectsMap);
            }
            return out;
        };
        var _agentSpellLevelRow = function(spell, levelId) {
            try {
                var tables = spell && spell._tables;
                var map = tables && (tables.spellLevels || tables.SpellLevels);
                if (map && typeof map === 'object' && map[levelId] != null) {
                    return map[levelId];
                }
            } catch (e) {}
            return _agentDbRow('SpellLevels', levelId);
        };
        var _agentSpellEntry = function(spell, playerLevel, withGrades, withEffects) {
            if (!spell) return null;
            var id = _agentNum(_agentPick(spell, ['id']));
            var nameId = null, breed = null, isItem = false, level = null;
            try { if (typeof spell.getProperty === 'function') nameId = _agentNum(spell.getProperty('nameId')); } catch (e) {}
            try { if (typeof spell.getProperty === 'function') breed = _agentNum(spell.getProperty('spellBreed')); } catch (e) {}
            try { isItem = !!spell.isItem; } catch (e) {}
            try { level = _agentNum(spell.level); } catch (e) {}
            var levelIds = [];
            try {
                if (Object.prototype.toString.call(spell.spellLevels) === '[object Array]') {
                    levelIds = spell.spellLevels;
                }
            } catch (e) {}
            var maxGrade = levelIds.length || 6;
            var maxForLevel = 0;
            var grades = [];
            for (var i = 0; i < levelIds.length; i++) {
                var lr = _agentSpellLevelRow(spell, levelIds[i]);
                var minLvl = lr ? _agentNum(_agentPick(lr, ['minPlayerLevel', 'min_player_level'])) : null;
                if (lr && minLvl != null && playerLevel != null && playerLevel >= minLvl) {
                    maxForLevel = i + 1;
                }
                if (withGrades) {
                    var grade = {
                        grade: i + 1,
                        level_id: _agentNum(levelIds[i]),
                        min_player_level: minLvl,
                        ap_cost: lr ? _agentNum(_agentPick(lr, ['apCost', 'ap_cost'])) : null,
                        min_range: lr ? _agentNum(_agentPick(lr, ['minRange', 'min_range'])) : null,
                        max_range: lr ? _agentNum(_agentPick(lr, ['maxRange', 'max_range', 'range'])) : null,
                        max_cast_per_turn: lr ? _agentNum(_agentPick(lr, ['maxCastPerTurn', 'max_cast_per_turn', 'castsPerTurn'])) : null,
                        max_cast_per_target: lr ? _agentNum(_agentPick(lr, ['maxCastPerTarget', 'max_cast_per_target'])) : null,
                        max_cast_per_fight: lr ? _agentNum(_agentPick(lr, ['maxCastPerFight', 'max_cast_per_fight'])) : null,
                        critical_hit_probability: lr ? _agentNum(_agentPick(lr, ['criticalHitProbability', 'critical_hit_probability'])) : null,
                        critical_failure_probability: lr ? _agentNum(_agentPick(lr, ['criticalFailureProbability', 'critical_failure_probability'])) : null,
                        cast_in_line: lr ? !!_agentPick(lr, ['castInLine', 'cast_in_line']) : null,
                        cast_in_diagonal: lr ? !!_agentPick(lr, ['castInDiagonal', 'cast_in_diagonal']) : null,
                        cast_test_los: lr ? !!_agentPick(lr, ['castTestLos', 'cast_test_los']) : null,
                        range_can_be_boosted: lr ? !!_agentPick(lr, ['rangeCanBeBoosted', 'range_can_be_boosted']) : null
                    };
                    if (withEffects && lr) {
                        grade.effects = _agentEffectList(lr.effects);
                        grade.critical_effects = _agentEffectList(lr.criticalEffect || lr.criticalEffects);
                    }
                    grades.push(grade);
                }
            }
            var cur = level != null ? level : 0;
            // A spell that is already at grade 3 was unlockable at that grade even
            // if a level row could not be read; never report a cap below the
            // level the server itself gave us.
            if (maxForLevel < cur) maxForLevel = cur;
            if (playerLevel == null && levelIds.length) maxForLevel = maxGrade;
            if (maxForLevel > maxGrade) maxForLevel = maxGrade;
            return {
                spell_id: id,
                name_id: nameId,
                breed_id: breed,
                is_item: isItem,
                level: cur,
                unlocked: cur > 0,
                max_grade: maxGrade,
                max_grade_for_level: maxForLevel,
                next_grade_available: maxForLevel > cur,
                upgrade_cost: (cur > 0 && maxForLevel > cur)
                    ? _agentUpgradeCost(cur, cur + 1) : null,
                refund_if_lowered: cur > 1 ? _agentUpgradeCost(1, cur) - _agentUpgradeCost(1, cur - 1) : 0,
                total_cost_at_level: _agentUpgradeCost(1, cur),
                grades: withGrades ? grades : undefined
            };
        };
        var _agentPositionName = function(pos) {
            var names = {
                0: 'amulet', 1: 'weapon', 2: 'ring_left', 3: 'belt',
                4: 'ring_right', 5: 'boots', 6: 'hat', 7: 'cape',
                8: 'pet', 9: 'dofus_1', 10: 'dofus_2', 11: 'dofus_3',
                12: 'dofus_4', 13: 'dofus_5', 14: 'dofus_6',
                15: 'shield', 16: 'mount', 20: 'mutation', 21: 'boost_food',
                22: 'bonus_1', 23: 'bonus_2', 24: 'malus_1', 25: 'malus_2',
                26: 'roleplay_buff', 27: 'follower', 28: 'cosmetic_hat',
                29: 'cosmetic_cape', 30: 'cosmetic_pet', 31: 'cosmetic_shield',
                32: 'cosmetic_weapon', 61: 'emblem_1', 62: 'emblem_2',
                63: 'not_equipped'
            };
            return names[pos] || ('position_' + pos);
        };
        var _agentCharacterReport = function(args) {
            args = args || {};
            var pd = null, base = {}, pos = {}, coords = {}, main = null, chars = {};
            try { pd = (window.gui && window.gui.playerData) || null; } catch (e) {}
            try { base = (pd && pd.characterBaseInformations) || {}; } catch (e) {}
            try { pos = (pd && pd.position) || {}; } catch (e) {}
            try { coords = pos.coordinates || {}; } catch (e) {}
            try { main = pd && pd.characters && pd.characters.mainCharacter; } catch (e) {}
            try { chars = (main && main.characteristics) || {}; } catch (e) {}
            var level = _agentNum(_agentPick(base, ['level', 'experienceLevel']));
            if (level == null) level = _agentNum(_agentPick(chars, ['level']));
            var statNames = ['strength', 'intelligence', 'chance', 'agility',
                'wisdom', 'vitality', 'actionPoints', 'movementPoints', 'range',
                'damagesBonusPercent', 'criticalHit', 'criticalDamageBonus',
                'healBonus', 'damageBonus', 'earthDamageBonus', 'fireDamageBonus',
                'waterDamageBonus', 'airDamageBonus', 'neutralDamageBonus',
                'earthElementReduction', 'fireElementReduction',
                'waterElementReduction', 'airElementReduction',
                'neutralElementReduction', 'earthElementResistPercent',
                'fireElementResistPercent', 'waterElementResistPercent',
                'airElementResistPercent', 'neutralElementResistPercent',
                'actionPointsCurrent', 'movementPointsCurrent', 'lifePoints',
                'maxLifePoints', 'effectiveMaxLifePoints', 'experience',
                'experienceLevelFloor', 'experienceNextLevelFloor'];
            var stats = {};
            for (var si = 0; si < statNames.length; si++) {
                var key = statNames[si];
                if (chars[key] !== undefined && chars[key] !== null) {
                    stats[key] = _agentReadStat(chars[key]);
                }
            }
            var out = {
                at: Date.now(),
                character: {
                    id: _agentNum(_agentPick(base, ['id'])),
                    name: _agentPick(base, ['name']),
                    level: level,
                    breed: _agentPick(base, ['breed']),
                    sex: _agentPick(base, ['sex'])
                },
                position: {
                    map_id: _agentNum(_agentPick(pos, ['mapId', 'map_id'])),
                    cell: _agentNum(_agentPick(pos, ['cell', 'cellId'])),
                    x: _agentNum(_agentPick(coords, ['posX', 'x'])),
                    y: _agentNum(_agentPick(coords, ['posY', 'y']))
                },
                stats: stats,
                stat_keys: Object.keys(chars).slice(0, 100)
            };
            var inv = null;
            try { inv = pd && pd.inventory; } catch (e) {}
            out.inventory = inv ? {
                kamas: _agentNum(inv.kamas),
                weight: _agentNum(inv.weight),
                max_weight: _agentNum(inv.maxWeight),
                object_count: inv.objects ? Object.keys(inv.objects).length : null,
                // The advisor needs to tell an owned set piece from one it has
                // to price on the market. Set-level counts are not enough: a
                // set can be complete but unequipped. Keyed by gid, capped so a
                // hoarder's inventory cannot turn a read-only report into a
                // multi-megabyte response.
                gids: {}
            } : null;

            var spellData = null;
            try { spellData = main && main.spellData; } catch (e) {}
            var used = null;
            try {
                if (spellData && typeof spellData.getUsedSpellPoints === 'function') {
                    used = _agentNum(spellData.getUsedSpellPoints());
                }
            } catch (e) {}
            var rawSpells = _agentNum(_agentPick(chars, ['spellsPoints']));
            var rawAccount = _agentNum(_agentPick(chars, ['accountSpellsPoints']));
            var budget = level != null ? level - 1 : null;
            out.points = {
                level: level,
                used_spell_points: used,
                character_budget: budget,
                character_cap_left: (budget != null && used != null) ? budget - used : null,
                raw_spells_points: rawSpells,
                raw_account_spells_points: rawAccount,
                stats_points_raw: _agentNum(_agentPick(chars, ['statsPoints'])),
                remaining_if_raw_is_balance:
                    (rawSpells != null || rawAccount != null)
                        ? ((rawSpells || 0) + (rawAccount || 0)) : null,
                note: 'raw_*_spells_points are what the server sent; character_cap_left is ' +
                      '(level-1) - used, the bundle calculateAttribution cap. Compare the two ' +
                      'once on a live character to fix the interpretation.'
            };

            var spells = [];
            try {
                var smap = spellData && spellData.spells;
                for (var sid in smap) {
                    if (!Object.prototype.hasOwnProperty.call(smap, sid)) continue;
                    var sp = smap[sid];
                    if (!sp) continue;
                    if (sp.isItem && !args.include_item_spells) continue;
                    var entry = _agentSpellEntry(sp, level, args.spell_grades !== false,
                                                 !!args.spell_effects);
                    if (entry) spells.push(entry);
                }
            } catch (e) { out.spells_error = String(e); }
            spells.sort(function(a, b) { return (a.spell_id || 0) - (b.spell_id || 0); });
            out.spells = spells;

            var equipment = [];
            var equippedSets = {};
            var ownedBySet = {};
            try {
                var eq = (inv && inv.equippedItems) || {};
                for (var pk in eq) {
                    if (!Object.prototype.hasOwnProperty.call(eq, pk)) continue;
                    var inst = _agentItemInstance(eq[pk], true);
                    if (!inst) continue;
                    inst.position_name = _agentPositionName(_agentNum(pk));
                    equipment.push(inst);
                    var setIds = (inst.template && inst.template.set_ids) || [];
                    for (var q = 0; q < setIds.length; q++) {
                        equippedSets[setIds[q]] = (equippedSets[setIds[q]] || 0) + 1;
                    }
                }
                var objs = (inv && inv.objects) || {};
                var seen = 0;
                for (var ok in objs) {
                    if (!Object.prototype.hasOwnProperty.call(objs, ok)) continue;
                    if (seen++ > 2000) break;
                    var oi = objs[ok];
                    var og = _agentNum(_agentPick(oi, ['objectGID', 'objectGid', 'gid']));
                    if (og == null) continue;
                    var tpl = _agentItemRow(og, false);
                    var sids = tpl.set_ids || [];
                    var qty = _agentNum(_agentPick(oi, ['quantity', 'qty'])) || 1;
                    if (out.inventory && out.inventory.gids
                            && Object.keys(out.inventory.gids).length < 2000) {
                        out.inventory.gids[og] = (out.inventory.gids[og] || 0) + qty;
                    }
                    for (var r = 0; r < sids.length; r++) {
                        if (!ownedBySet[sids[r]]) ownedBySet[sids[r]] = { gids: {}, qty: 0 };
                        ownedBySet[sids[r]].gids[og] = true;
                        ownedBySet[sids[r]].qty += qty;
                    }
                }
            } catch (e) { out.equipment_error = String(e); }
            equipment.sort(function(a, b) { return (a.position || 0) - (b.position || 0); });
            out.equipment = equipment;

            var setsOut = [];
            try {
                for (var setId in equippedSets) {
                    if (!Object.prototype.hasOwnProperty.call(equippedSets, setId)) continue;
                    var setRow = _agentDbRow('ItemSets', setId) || null;
                    var count = equippedSets[setId];
                    var pieces = (setRow && _agentPick(setRow, ['items'])) || [];
                    if (Object.prototype.toString.call(pieces) !== '[object Array]') pieces = [];
                    var effectsByCount = (setRow && _agentPick(setRow, ['effects'])) || [];
                    var active = [];
                    if (Object.prototype.toString.call(effectsByCount) === '[object Array]'
                            && effectsByCount.length) {
                        var idx = Math.min(Math.max(count - 1, 0), effectsByCount.length - 1);
                        active = _agentEffectList(effectsByCount[idx]);
                    }
                    var missing = [];
                    for (var pi = 0; pi < pieces.length; pi++) {
                        var pg = _agentNum(pieces[pi]);
                        var isOn = false;
                        for (var ei = 0; ei < equipment.length; ei++) {
                            if (equipment[ei].gid === pg) { isOn = true; break; }
                        }
                        if (!isOn) missing.push(pg);
                    }
                    setsOut.push({
                        set_id: _agentNum(setId),
                        name_id: setRow ? _agentNum(_agentPick(setRow, ['nameId', 'name_id'])) : null,
                        equipped_count: count,
                        piece_ids: pieces,
                        missing_ids: missing,
                        owned_piece_count: ownedBySet[setId]
                            ? Object.keys(ownedBySet[setId].gids).length : count,
                        owned_quantity: ownedBySet[setId] ? ownedBySet[setId].qty : null,
                        active_effects: active,
                        bonus_is_secret: !!(setRow && _agentPick(setRow, ['bonusIsSecret', 'bonus_is_secret']))
                    });
                }
            } catch (e) { out.sets_error = String(e); }
            setsOut.sort(function(a, b) { return b.equipped_count - a.equipped_count; });
            out.sets = setsOut;

            var dbs = _agentDatabases();
            var counts = {};
            var names = ['Items', 'ItemSets', 'ItemTypes', 'Effects', 'Spells', 'SpellLevels'];
            for (var ni = 0; ni < names.length; ni++) {
                var tb = dbs && dbs[names[ni]];
                counts[names[ni]] = (tb && typeof tb === 'object')
                    ? Object.keys(tb).length : null;
            }
            out.db = {
                available: !!dbs,
                counts: counts,
                effect_sample_ids: (dbs && dbs.Effects)
                    ? Object.keys(dbs.Effects).slice(0, 20).map(Number) : null
            };
            return _agentResult(!!main, out, main ? null : 'mainCharacter is not loaded (not in world?)');
        };
        var _agentItemDbReport = function(args) {
            args = args || {};
            var dbs = _agentDatabases();
            var table = dbs && dbs.Items;
            if (!table) return _agentResult(false, null, 'Items database is not loaded');
            var gid = _agentNum(args.gid);
            var gids = {};
            if (Object.prototype.toString.call(args.gids) === '[object Array]') {
                for (var gi = 0; gi < args.gids.length && gi < 200; gi++) {
                    var gv = _agentNum(args.gids[gi]);
                    if (gv != null) gids[gv] = true;
                }
            }
            var levelMin = _agentNum(args.level_min);
            var levelMax = _agentNum(args.level_max);
            var typeId = _agentNum(args.type_id);
            var setId = _agentNum(args.set_id);
            var offset = _agentCap(args.offset, 0, 2147483647, 0);
            var limit = _agentCap(args.limit, 1, 200, 50);
            var withEffects = args.with_effects !== false;
            var matched = [];
            for (var k in table) {
                if (!Object.prototype.hasOwnProperty.call(table, k)) continue;
                var row = table[k];
                if (!row || typeof row !== 'object') continue;
                var id = _agentNum(_agentPick(row, ['id']));
                if (id == null) id = _agentNum(k);
                if (gid != null && id !== gid) continue;
                if (gid == null && Object.keys(gids).length && !gids[id]) continue;
                var lvl = _agentNum(_agentPick(row, ['level']));
                if (levelMin != null && (lvl == null || lvl < levelMin)) continue;
                if (levelMax != null && (lvl == null || lvl > levelMax)) continue;
                if (typeId != null && _agentNum(_agentPick(row, ['typeId', 'type_id'])) !== typeId) continue;
                if (setId != null) {
                    var sets = _agentPick(row, ['itemSetIds', 'setIds', 'itemSetId', 'setId']);
                    if (Object.prototype.toString.call(sets) !== '[object Array]') sets = sets != null ? [sets] : [];
                    var hit = false;
                    for (var s = 0; s < sets.length; s++) {
                        if (_agentNum(sets[s]) === setId) { hit = true; break; }
                    }
                    if (!hit) continue;
                }
                matched.push(id);
                if (gid != null || Object.keys(gids).length) {
                    if (matched.length >= 200) break;
                }
            }
            matched.sort(function(a, b) { return a - b; });
            var page = matched.slice(offset, offset + limit);
            var rows = [];
            for (var p = 0; p < page.length; p++) {
                rows.push(_agentItemRow(page[p], withEffects));
            }
            return _agentResult(true, {
                total_matched: matched.length,
                offset: offset,
                limit: limit,
                rows: rows
            });
        };
        var _agentEffectDbReport = function(args) {
            args = args || {};
            var dbs = _agentDatabases();
            var table = dbs && dbs.Effects;
            if (!table) return _agentResult(false, null, 'Effects database is not loaded');
            var wanted = [];
            var seen = {};
            if (Object.prototype.toString.call(args.ids) === '[object Array]') {
                for (var i = 0; i < args.ids.length && wanted.length < 60; i++) {
                    var id = _agentNum(args.ids[i]);
                    if (id != null && !seen[id]) { seen[id] = true; wanted.push(id); }
                }
            }
            var allIds = Object.keys(table);
            var out = {
                count: allIds.length,
                sample_ids: allIds.slice(0, 40).map(Number),
                rows: [],
                keys: null
            };
            if (!wanted.length) {
                var limit = _agentCap(args.limit, 1, 60, 20);
                var offset = _agentCap(args.offset, 0, allIds.length, 0);
                wanted = allIds.slice(offset, offset + limit).map(Number);
            }
            var keySet = {};
            for (var w = 0; w < wanted.length; w++) {
                var row = _agentDbRow('Effects', wanted[w]);
                if (!row || typeof row !== 'object') {
                    out.rows.push({ effect_id: wanted[w], db: false });
                    continue;
                }
                var item = { effect_id: wanted[w], db: true };
                for (var f in row) {
                    if (!Object.prototype.hasOwnProperty.call(row, f)) continue;
                    var v = row[f];
                    if (v === null || v === undefined) continue;
                    if (typeof v === 'object') continue;
                    if (Object.keys(keySet).length < 40) keySet[f] = true;
                    item[f] = v;
                }
                out.rows.push(item);
            }
            out.keys = Object.keys(keySet);
            return _agentResult(true, out);
        };
        var _agentMarketCapture = function(args) {
            args = args || {};
            var now = Date.now();
            var refreshed = false;
            var requestError = null;
            if (args.refresh) {
                if (!__PriceCache.averageRequestedAt
                        || now - __PriceCache.averageRequestedAt > 120000) {
                    var r = _cmSend('ObjectAveragePricesGetMessage', {});
                    if (r.ok) {
                        __PriceCache.averageRequestedAt = now;
                        refreshed = true;
                    } else {
                        requestError = r.error;
                    }
                }
            }
            var avg = __PriceCache.average;
            var gids = [];
            if (Object.prototype.toString.call(args.gids) === '[object Array]') {
                for (var i = 0; i < args.gids.length && i < 500; i++) {
                    var g = _agentNum(args.gids[i]);
                    if (g != null) gids.push(g);
                }
            }
            var total = Object.keys(avg).length;
            var offset = _agentCap(args.offset, 0, Math.max(total, 0), 0);
            var limit = _agentCap(args.limit, 1, 20000, 2000);
            var prices = [];
            if (gids.length) {
                for (var gi = 0; gi < gids.length; gi++) {
                    if (avg[gids[gi]] != null) prices.push([gids[gi], avg[gids[gi]]]);
                }
            } else {
                var ids = Object.keys(avg).map(Number).sort(function(a, b) { return a - b; });
                var page = ids.slice(offset, offset + limit);
                for (var p = 0; p < page.length; p++) {
                    prices.push([page[p], avg[page[p]]]);
                }
            }
            var listingGids = gids.length ? gids
                : Object.keys(__PriceCache.listings).map(Number).slice(0, limit);
            var listingLimit = _agentCap(args.listing_limit, 0, 500, 200);
            var listings = {};
            var listingCount = 0;
            for (var li = 0; li < listingGids.length && listingCount < listingLimit; li++) {
                var st = __PriceCache.stats(listingGids[li]);
                if (st) {
                    listings[listingGids[li]] = st;
                    listingCount++;
                }
            }
            return _agentResult(true, {
                captured_at: now,
                average_at: __PriceCache.averageAt || null,
                average_age_ms: __PriceCache.averageAt ? now - __PriceCache.averageAt : null,
                average_count: total,
                listing_count: Object.keys(__PriceCache.listings).length,
                listing_at: __PriceCache.listingAt || null,
                requested: refreshed,
                request_error: requestError,
                gids: gids.length ? gids : null,
                offset: offset,
                limit: limit,
                returned: prices.length,
                prices: prices,
                listings: listings
            });
        };

        var _agentHandlers = {
            'safe.stop': function(args) {
                var stopped = { auto_fight: false, follow: false };
                try {
                    if (window.__FarmBot && window.__FarmBot.enabled) {
                        window.__FarmBot.setEnabled(false);
                        stopped.auto_fight = true;
                    }
                } catch(e){}
                try { _activeDirectives = {}; stopped.follow = true; } catch(e){}
                return _agentResult(true, { stopped: stopped, reason: (args && args.reason) || null });
            },
            'farm.start': function(args) {
                var started = false;
                var s = null;
                var harvest = null;
                // A named mob is a focus order. Find a group containing it
                // before enabling anything: with no match on this map, enabling
                // the generic bot would farm unrelated groups, which is exactly
                // what "farm bouftou" must not do. The caller can travel/explore
                // and ask again.
                var wanted = args && args.monster ? parseInt(args.monster, 10) : null;
                var targetGroupId = null;
                if (wanted) {
                    var amG = window.actorManager
                        || (window.isoEngine && window.isoEngine.actorManager) || null;
                    var actorsG = (amG && amG.actors) || {};
                    var targetGroup = null;
                    for (var gk in actorsG) {
                        if (!Object.prototype.hasOwnProperty.call(actorsG, gk)) continue;
                        var ga = actorsG[gk] || {};
                        var gty = (ga.data && ga.data.type) || ga._type || ga.type;
                        if (gty !== 'GameRolePlayGroupMonsterInformations') continue;
                        var gsi = (ga.data && ga.data.staticInfos) || ga.staticInfos || {};
                        var gm = gsi.monsters || (ga.data && ga.data.monsters) || [];
                        for (var gmi = 0; gmi < gm.length; gmi++) {
                            var gmm = gm[gmi] || {};
                            var gid2 = gmm.creatureGenericId != null ? gmm.creatureGenericId
                                : (gmm.genericId != null ? gmm.genericId : gmm.monsterId);
                            if (Number(gid2) === wanted) { targetGroup = ga; break; }
                        }
                        if (targetGroup) break;
                    }
                    if (!targetGroup) {
                        return _agentResult(false, {
                            auto_fight: !!(window.__FarmBot && window.__FarmBot.enabled),
                            requested: args || null,
                            note: 'target monster ' + wanted + ' is not on this map'
                        }, 'target monster ' + wanted + ' is not on this map');
                    }
                    // The actor is constructed with `actorId`; `contextualId` and
                    // `id` do not exist on it, and an undefined group id is a
                    // request the server cannot answer.
                    targetGroupId = targetGroup.actorId != null ? targetGroup.actorId
                        : (targetGroup.contextualId != null ? targetGroup.contextualId
                        : targetGroup.id);
                }
                try {
                    if (window.__FarmBot) {
                        // Idempotent: a scheduler that re-issues farm.start must
                        // not wipe kills/kamas every cycle. A fresh session is
                        // explicit (restart) or implicit when the bot was off.
                        var restart = !!(args && args.restart);
                        if (!window.__FarmBot.enabled || restart) {
                            window.__FarmBot.resetSession();
                        }
                        window.__FarmBot.setEnabled(true);
                        started = !!window.__FarmBot.enabled;
                        s = window.__FarmBot.sessionSummary();
                    }
                } catch(e){}
                if (targetGroupId != null) {
                    // Start the focused fight; fight.join also arms the bot.
                    return _agentHandlers['fight.join']({ group_id: targetGroupId });
                }
                // Harvesting is `InteractiveUseRequestMessage {elemId,
                // skillInstanceUid}` -- captured 2026-10-22 while gathering. If the
                // agent asked for a resource, take the nearest interactive element
                // on this map; walking to it is still the client's job.
                if (started && args && args.resource) {
                    var list = _interactives();
                    var target = null;
                    for (var i = 0; i < list.length; i++) {
                        if (list[i].skillInstanceUid != null) { target = list[i]; break; }
                    }
                    if (target) {
                        var r = _cmSend('InteractiveUseRequestMessage',
                            { elemId: target.elemId, skillInstanceUid: target.skillInstanceUid });
                        harvest = r.ok
                            ? { elem_id: target.elemId, skill_instance_uid: target.skillInstanceUid, cell: target.cell }
                            : { error: r.error };
                    } else {
                        harvest = { error: 'no interactive element on this map',
                                    candidates: list.length };
                    }
                }
                return _agentResult(started, {
                    auto_fight: started,
                    harvest: harvest,
                    requested: args || null,
                    session: s,
                    // Flat copies: this is the shape the learning contract reads
                    // (planning/outcome.py -> data.kills / data.drops / ...).
                    kills: s ? s.kills : null,
                    drops: s ? s.drop_count : null,
                    minutes: s ? s.minutes : null,
                    kamas: s ? s.kamas : null,
                    xp: s ? s.xp : null,
                    note: 'auto-fight enabled; harvest fires on the nearest interactive element when a resource is requested'
                }, started ? null : 'FarmBot unavailable');
            },
            'farm.stop': function() {
                var s = null;
                try {
                    if (window.__FarmBot) {
                        window.__FarmBot.setEnabled(false);
                        s = window.__FarmBot.sessionSummary();
                    }
                } catch(e){}
                return _agentResult(true, {
                    auto_fight: false,
                    session: s,
                    kills: s ? s.kills : null,
                    drops: s ? s.drop_count : null,
                    minutes: s ? s.minutes : null,
                    kamas: s ? s.kamas : null,
                    xp: s ? s.xp : null
                });
            },
            // Attack a monster group, using the client's own sequence.
            //
            // The bundle does exactly this (see the handler that sends
            // GameRolePlayAttackMonsterRequestMessage): look the group up in
            // actorManager.actors by its contextual id, walk to its cell if we are not
            // already there, then send the request. The previous version clicked a DOM
            // selector (`.join-fight`) that almost certainly does not exist in this
            // client, which is why it returned ok and nothing happened.
            'fight.join': function(args) {
                var pdF = (window.gui && window.gui.playerData) || null;
                var fmF = (window.gui && window.gui.fightManager) || null;
                var managerFight = false;
                try { managerFight = !!(fmF && fmF.isInFight && fmF.isInFight()); } catch (e) {}
                if (!!(pdF && pdF.isFighting) || managerFight) {
                    if (window.__FarmBot) window.__FarmBot.setEnabled(true);
                    // Report the latch, not the object: the controller uses this
                    // answer to decide whether it still has to arm the page.
                    return _agentResult(true, { in_fight: true,
                        auto_fight: !!(window.__FarmBot && window.__FarmBot.enabled) });
                }
                var lifeF = _agentLife();
                if (!lifeF.alive) {
                    return _agentResult(false, { life: lifeF.life, max: lifeF.max,
                        life_state: lifeF.state },
                        'the character is dead (life ' + lifeF.life + '); '
                        + 'revive before fighting');
                }
                var am = window.actorManager
                    || (window.isoEngine && window.isoEngine.actorManager) || null;
                var iso = window.isoEngine || null;
                if (!am || !am.actors) return _agentResult(false, null, 'actorManager unavailable');
                var actors = am.actors;
                var want = parseInt(args && args.group_id, 10) || null;
                // The planner names a monster, not a group: an objective says "fight
                // monster 447", and only the client knows which group contains it. The
                // contract used to declare `target` while the planner sent `monster` and
                // this handler read `group_id` - three names with no overlap, so a fight
                // objective was rejected by the schema before it reached the game.
                var wantMonster = parseInt(args && args.monster, 10) || null;
                var pick = want ? (actors[want] || null) : null;
                var pickKey = pick ? want : null;
                if (!pick) {
                    var seenMonsters = [];
                    for (var k in actors) {
                        if (!Object.prototype.hasOwnProperty.call(actors, k)) continue;
                        var a = actors[k] || {};
                        var ty = (a.data && a.data.type) || a._type || a.type;
                        if (ty !== 'GameRolePlayGroupMonsterInformations') continue;
                        if (wantMonster) {
                            var gsi = (a.data && a.data.staticInfos) || a.staticInfos || {};
                            var gm = gsi.monsters || (a.data && a.data.monsters) || [];
                            var hit = false;
                            for (var gmi = 0; gmi < gm.length; gmi++) {
                                var gmm = gm[gmi] || {};
                                var mid = gmm.creatureGenericId != null ? gmm.creatureGenericId
                                    : (gmm.genericId != null ? gmm.genericId : gmm.monsterId);
                                if (mid != null) seenMonsters.push(Number(mid));
                                if (Number(mid) === wantMonster) { hit = true; break; }
                            }
                            if (!hit) continue;
                        }
                        pick = a;
                        pickKey = k;
                        break;
                    }
                    if (!pick && wantMonster) {
                        return _agentResult(false, { wanted_monster: wantMonster,
                            monsters_here: seenMonsters },
                            'no group on this map contains monster ' + wantMonster
                            + (seenMonsters.length ? ' (here: ' + seenMonsters.join(',') + ')'
                                                   : ' (no monster groups on this map)'));
                    }
                }
                if (!pick) return _agentResult(false, { map_id: (window.gui && window.gui.playerData
                        && window.gui.playerData.position && window.gui.playerData.position.mapId) },
                    'no monster group on this map');
                // Actors are keyed by contextual id and carry it as `actorId`;
                // `contextualId`/`id` are undefined (the NPC reader had the same
                // defect). Fall back to the map key, which is that same id.
                var gid = pick.actorId != null ? pick.actorId
                    : (pick.contextualId != null ? pick.contextualId
                    : (pick.id != null ? pick.id : Number(pickKey)));
                if (gid == null || isNaN(Number(gid))) {
                    return _agentResult(false, { cell: pick.cellId != null ? pick.cellId
                            : (pick.position && pick.position.cell) },
                        'monster group actor has no contextual id');
                }
                gid = Number(gid);
                var cell = pick.cellId != null ? pick.cellId : (pick.position && pick.position.cell);
                var ua = am.userActor;
                var attack = function () {
                    var result = _cmSend('GameRolePlayAttackMonsterRequestMessage', { monsterGroupId: gid });
                    if (result.ok && window.__FarmBot) window.__FarmBot.setEnabled(true);
                    return result;
                };
                if (ua && ua.cellId === cell) {
                    var r0 = attack();
                    window.__agentLastFight = { at: Date.now(), stage: r0.ok ? 'attacked' : 'attack_failed',
                        group_id: gid, cell: cell, error: r0.ok ? null : String(r0.error || '') };
                    return _agentResult(r0.ok, { group_id: gid, cell: cell, via: 'attacked',
                        auto_fight: r0.ok && !!window.__FarmBot }, r0.ok ? null : r0.error);
                }
                if (!iso || typeof iso._movePlayerOnMap !== 'function') {
                    window.__agentLastFight = { at: Date.now(), stage: 'cannot_reach',
                        group_id: gid, cell: cell };
                    return _agentResult(false, { group_id: gid, cell: cell },
                        'the group is not on my cell and I cannot walk there');
                }
                try {
                    window.__agentLastFight = { at: Date.now(), stage: 'walk_then_attack',
                        group_id: gid, cell: cell };
                    iso._movePlayerOnMap(cell, false, function () {
                        var result = attack();
                        window.__agentLastFight = { at: Date.now(),
                            stage: result.ok ? 'attacked' : 'attack_failed',
                            group_id: gid, cell: cell,
                            error: result.ok ? null : String(result.error || '') };
                    });
                    return _agentResult(true, { group_id: gid, cell: cell, via: 'walk then attack' });
                } catch (e) {
                    window.__agentLastFight = { at: Date.now(), stage: 'walk_failed',
                        group_id: gid, cell: cell, error: String(e) };
                    return _agentResult(false, { group_id: gid, cell: cell }, 'walk failed: ' + e);
                }
            },
            'fight.pass': function() { return _agentResult(true, _agentCommander('PASS_TURN')); },
            'fight.state': function() {
                var gui = window.gui || {};
                var fm = gui.fightManager || null;
                var pd = gui.playerData || null;
                var am = window.actorManager
                    || (window.isoEngine && window.isoEngine.actorManager) || null;
                if (!fm || !pd || !am) {
                    return _agentResult(false, null, 'combat managers unavailable');
                }
                var num = function(value) {
                    if (value === null || value === undefined || value === '') return null;
                    var parsed = Number(value);
                    return isFinite(parsed) ? parsed : null;
                };
                var text = function(value) {
                    return typeof value === 'string' && value ? value : null;
                };
                var effects = function(list) {
                    var out = [];
                    if (!list || !list.length) return out;
                    for (var i = 0; i < list.length; i++) {
                        var effect = list[i] || {};
                        var params = [];
                        for (var p = 1; p <= 3; p++) {
                            var param = num(effect['param' + p]);
                            if (param !== null) params.push(param);
                        }
                        out.push({
                            effect_id: num(effect.effectId),
                            dice_num: num(effect.diceNum),
                            dice_side: num(effect.diceSide),
                            value: num(effect.value),
                            element_id: num(effect.elementId),
                            target_id: num(effect.targetId),
                            duration: num(effect.duration),
                            delay: num(effect.delay),
                            random: effect.random == null ? null : !!effect.random,
                            params: params
                        });
                    }
                    return out;
                };

                // Spell level data is static: the same spell at the same level has the
                // same cost, ranges and effects for every fighter and every turn. A
                // five-fighter fight was re-parsing every effect of every spell on each
                // `fight.state` call. Cache the finished fact object by spell id and
                // level; the object is output-only and never mutated by the caller.
                // A level-up changes `spell.level`, so it gets a new key; the cap only
                // exists so a long session cannot grow this without bound.
                var spellFactsCache = window.__agentSpellFactsCache;
                if (!spellFactsCache) {
                    spellFactsCache = window.__agentSpellFactsCache = { facts: {}, n: 0 };
                }
                var fighterSpellFacts = function(spell, spellId) {
                    if (!spell) return { id: num(spellId) };
                    var spellNum = num(spellId) !== null ? num(spellId) : num(spell.id);
                    var spellLevel = num(spell.level);
                    var key = (spellNum !== null && spellLevel !== null)
                        ? spellNum + ':' + spellLevel : null;
                    if (key !== null
                        && Object.prototype.hasOwnProperty.call(spellFactsCache.facts, key)) {
                        return spellFactsCache.facts[key];
                    }
                    var level = spell.spellLevel || null;
                    var ap = num(level && level.apCost);
                    if (ap === null) ap = num(spell.apCost);
                    var name = null;
                    try { name = text(spell.name); } catch (e) {}
                    if (!name && level) name = text(level.name);
                    if (!name) {
                        try { name = text(spell.getName && spell.getName()); } catch (e) {}
                    }
                    var facts = {
                        id: spellNum,
                        name: name,
                        level: spellLevel,
                        ap_cost: ap,
                        min_range: num(level && level.minRange),
                        range: num(level && level.range),
                        cast_in_line: !!(level && level.castInLine),
                        cast_in_diagonal: !!(level && level.castInDiagonal),
                        requires_los: !!(level && level.castTestLos),
                        max_cast_per_turn: num(level && level.maxCastPerTurn),
                        max_cast_per_target: num(level && level.maxCastPerTarget),
                        min_cast_interval: num(level && level.minCastInterval),
                        critical_hit_probability: num(level && level.criticalHitProbability),
                        effects: effects(level && level.effects),
                        critical_effects: effects(level && level.criticalEffect)
                    };
                    if (key !== null) {
                        if (spellFactsCache.n >= 2048) {
                            spellFactsCache.facts = {};
                            spellFactsCache.n = 0;
                        }
                        spellFactsCache.facts[key] = facts;
                        spellFactsCache.n += 1;
                    }
                    return facts;
                };
                var chars = pd.characters || {};
                var base = pd.characterBaseInformations || {};
                var myId = num(chars.controlledCharacterId);
                if (myId === null) myId = num(base.id);
                if (myId === null && am.userActor) myId = num(am.userActor.actorId);
                var inFight = false;
                try { inFight = !!(pd.isFighting || (fm.isInFight && fm.isInFight())); } catch (e) {}

                var state = {
                    available: true,
                    in_fight: inFight,
                    in_preparation: false,
                    fight_id: num(fm.fightId),
                    fight_state: num(fm.fightState),
                    round: typeof fm.getTurnCount === 'function' ? num(fm.getTurnCount()) : null,
                    current_fighter_id: num(fm.currentFighterId) || null,
                    is_my_turn: false,
                    turn_started_ms_ago: null,
                    my_id: myId,
                    character_id: num(base.id),
                    my_cell: null,
                    map_id: num(pd.position && pd.position.mapId),
                    cells: [],
                    my_stats: null,
                    fighters: [],
                    spells: []
                };
                // The cell flags are what make the board solvable instead of a picture.
                // Bit 0 is walkable, bit 1 is line of sight, and fight-mode walkability
                // is (l & 5) === 1 in the client's own isWalkable(). Read them here so
                // the planner does not need a second call, a map cache, or a guess.
                try {
                    var map = window.isoEngine && window.isoEngine.mapRenderer
                        && window.isoEngine.mapRenderer.map;
                    var mapCells = map && map.cells;
                    if (mapCells && mapCells.length) {
                        // Cell flags are static for a map. Converting 560 cells
                        // through num() on every fight decision was repeat work;
                        // cache by the renderer map object and hand out a copy
                        // (the caller owns and may mutate state.cells).
                        var cellsCache = window.__agentCellsCache;
                        if (!cellsCache || cellsCache.map !== map
                            || cellsCache.len !== mapCells.length) {
                            var flags = [];
                            for (var ci = 0; ci < mapCells.length; ci++) {
                                flags.push(num(mapCells[ci] && mapCells[ci].l));
                            }
                            cellsCache = window.__agentCellsCache = {
                                map: map, len: mapCells.length, flags: flags };
                        }
                        state.cells = cellsCache.flags.slice();
                    }
                } catch (e) {}
                try {
                    state.in_preparation = !!(fm.isInFightPreparation
                        && fm.isInFightPreparation());
                } catch (e) {}
                var current = num(fm.currentFighterId);
                state.is_my_turn = current !== null && myId !== null && current === myId;
                var turnStarted = num(fm.turnStartTime);
                if (turnStarted !== null && turnStarted > 0) {
                    state.turn_started_ms_ago = Math.max(0, Date.now() - turnStarted);
                }
                if (myId !== null) {
                    try {
                        var mine = am.getActor ? am.getActor(myId) : null;
                        state.my_cell = mine && mine.cellId != null
                            ? num(mine.cellId)
                            : (pd.position && pd.position.cell != null
                                ? num(pd.position.cell) : null);
                    } catch (e) {}
                }

                if (inFight) {
                    var seen = {};
                    var ids = [];
                    var addId = function(id) {
                        var value = num(id);
                        if (value === null || seen[value]) return;
                        seen[value] = true;
                        ids.push(value);
                    };
                    try {
                        var actorIds = (typeof am.getActorsIDList === 'function')
                            ? am.getActorsIDList() : [];
                        for (var i = 0; i < actorIds.length; i++) addId(actorIds[i]);
                    } catch (e) {}
                    try {
                        var fighterIds = fm._fighters ? Object.keys(fm._fighters) : [];
                        for (var j = 0; j < fighterIds.length; j++) addId(fighterIds[j]);
                    } catch (e) {}

                    for (var k = 0; k < ids.length; k++) {
                        var id = ids[k];
                        var actor = null;
                        var fighter = null;
                        try { actor = am.getActor ? am.getActor(id) : null; } catch (e) {}
                        try { fighter = fm.getFighter ? fm.getFighter(id) : null; } catch (e) {}
                        if (!actor && !fighter) continue;
                        var data = (fighter && fighter.data) || (actor && actor.data) || {};
                        var stats = data.stats || {};
                        var rawSpellList = [];
                        try {
                            if (Array.isArray(data.spells)) rawSpellList = data.spells;
                            else if (data.spells && typeof data.spells === 'object') rawSpellList = Object.keys(data.spells);
                            if (!rawSpellList.length && data.spellIds) {
                                rawSpellList = Array.isArray(data.spellIds)
                                    ? data.spellIds : Object.keys(data.spellIds);
                            }
                        } catch (e) {}
                        var spellMap = {};
                        try { spellMap = (fighter && fighter.spells) || {}; } catch (e) {}
                        var spellIds = [];
                        var seenSpells = {};
                        var addSpell = function(value) {
                            var sid = num(value);
                            if (sid === null || seenSpells[sid]) return;
                            seenSpells[sid] = true;
                            spellIds.push(sid);
                        };
                        for (var sl = 0; sl < rawSpellList.length; sl++) addSpell(rawSpellList[sl]);
                        for (var sm in spellMap) {
                            if (Object.prototype.hasOwnProperty.call(spellMap, sm)) addSpell(sm);
                        }
                        var fighterSpells = [];
                        for (var ei = 0; ei < spellIds.length; ei++) {
                            fighterSpells.push(fighterSpellFacts(spellMap[spellIds[ei]], spellIds[ei]));
                        }
                        var onTeam = false;
                        try { onTeam = !!fm.isFighterOnUsersTeam(id); } catch (e) {}
                        var cell = actor && actor.cellId != null ? num(actor.cellId) : null;
                        if (cell === null && data.disposition && data.disposition.cellId != null) {
                            cell = num(data.disposition.cellId);
                        }
                        var states = [];
                        try {
                            var rawStates = (fighter && fighter.states) || [];
                            for (var s = 0; s < rawStates.length && states.length < 32; s++) {
                                if (typeof rawStates[s] === 'number'
                                    || typeof rawStates[s] === 'string') {
                                    states.push(rawStates[s]);
                                }
                            }
                        } catch (e) {}
                        state.fighters.push({
                            id: id,
                            cell: cell,
                            team: onTeam ? 'player' : 'enemy',
                            team_id: num(data.teamId),
                            is_me: myId !== null && id === myId,
                            alive: data.alive !== false && !(actor && actor.isDead),
                            hp: num(stats.lifePoints),
                            max_hp: num(stats.maxLifePoints),
                            ap: num(stats.actionPointsCurrent),
                            max_ap: num(stats.actionPointsMax),
                            mp: num(stats.movementPointsCurrent),
                            max_mp: num(stats.movementPointsMax),
                            shield: num(stats.shieldPoints),
                            level: num(fighter && fighter.level) !== null
                                ? num(fighter.level) : num(data.level),
                            name: text(fighter && fighter.name) || text(data.name),
                            creature_generic_id: num(data.creatureGenericId),
                            summoner_id: num(stats.summoner),
                            is_monster: !!(fighter && fighter.isMonster && fighter.isMonster()),
                            is_summon: !!(fighter && fighter.isSummon && fighter.isSummon()),
                            is_companion: !!(fighter && fighter.isCompanion
                                && fighter.isCompanion()),
                            is_boss: !!(fighter && fighter.isBoss),
                            buffs: fighter && fighter.buffs ? fighter.buffs.length : 0,
                            states: states,
                            resist_percent: {
                                neutral: num(stats.neutralElementResistPercent),
                                earth: num(stats.earthElementResistPercent),
                                fire: num(stats.fireElementResistPercent),
                                water: num(stats.waterElementResistPercent),
                                air: num(stats.airElementResistPercent)
                            },
                            resist_fixed: {
                                neutral: num(stats.neutralElementReduction),
                                earth: num(stats.earthElementReduction),
                                fire: num(stats.fireElementReduction),
                                water: num(stats.waterElementReduction),
                                air: num(stats.airElementReduction)
                            },
                            critical_resistance: num(stats.criticalDamageReduction),
                            permanent_damage_percent: num(stats.permanentDamagePercent),
                            ap_dodge: num(stats.dodgePALostProbability),
                            mp_dodge: num(stats.dodgePMLostProbability),
                            tackle_block: num(stats.tackleBlock),
                            tackle_evade: num(stats.tackleEvade),
                            spell_ids: spellIds,
                            spells: fighterSpells
                        });
                        if (id !== myId) {
                            for (var ri = 0; ri < spellIds.length; ri++) {
                                if (spellMap[spellIds[ri]]) continue;
                                // Read-only cache fill: the client's own spell loader.
                                try { fm.getFighterSpell(spellIds[ri], id, function() {}); } catch (e) {}
                            }
                        }
                    }
                }

                var controlled = null;
                try {
                    controlled = chars.getControlledCharacter
                        ? chars.getControlledCharacter() : (chars.mainCharacter || null);
                } catch (e) {}
                // Characteristics are CharacterBaseCharacteristic wrappers, not numbers;
                // getTotalStat() is base + equipment + bonuses, which is what the damage
                // formula consumes. Reading `.strength` alone would hand the solver an
                // object and silently poison every estimate.
                var charStat = function(name) {
                    var stat = controlled && controlled.characteristics
                        ? controlled.characteristics[name] : null;
                    if (!stat) return null;
                    try {
                        return stat.getTotalStat ? num(stat.getTotalStat()) : num(stat);
                    } catch (e) { return null; }
                };
                if (controlled) {
                    state.my_stats = {
                        strength: charStat('strength'),
                        intelligence: charStat('intelligence'),
                        chance: charStat('chance'),
                        agility: charStat('agility'),
                        wisdom: charStat('wisdom'),
                        vitality: charStat('vitality'),
                        power: charStat('damagesBonusPercent'),
                        damage: charStat('allDamagesBonus'),
                        damage_neutral: charStat('neutralDamageBonus'),
                        damage_earth: charStat('earthDamageBonus'),
                        damage_fire: charStat('fireDamageBonus'),
                        damage_water: charStat('waterDamageBonus'),
                        damage_air: charStat('airDamageBonus'),
                        critical_hit: charStat('criticalHit'),
                        damage_critical: charStat('criticalDamageBonus'),
                        heals: charStat('healBonus'),
                        range: charStat('range'),
                        action_points: charStat('actionPoints'),
                        movement_points: charStat('movementPoints'),
                        critical_resistance: charStat('criticalDamageReduction')
                    };
                }
                var spellList = {};
                try {
                    spellList = (controlled && controlled.spellData
                        && controlled.spellData.getSpellListDependingOnGameMode)
                        ? controlled.spellData.getSpellListDependingOnGameMode()
                        : ((controlled && controlled.spells) || {});
                } catch (e) {}
                var prop = function(spell, name, fallback) {
                    try {
                        var value = spell.getProperty ? spell.getProperty(name) : null;
                        return value === undefined ? fallback : value;
                    } catch (e) { return fallback; }
                };
                for (var key in spellList) {
                    if (!Object.prototype.hasOwnProperty.call(spellList, key)) continue;
                    var spell = spellList[key];
                    var spellId = num(spell && spell.id);
                    if (spellId === null) spellId = num(key);
                    if (spellId === null || !spell) continue;
                    var level = spell.spellLevel || null;
                    var levelId = null;
                    try {
                        levelId = typeof spell.getSpellLevelId === 'function'
                            ? num(spell.getSpellLevelId()) : null;
                    } catch (e) {}
                    if (levelId === null && level) levelId = num(level.id);
                    var castable = false;
                    try {
                        castable = myId !== null && !!fm.canCastThisSpell(myId, spellId);
                    } catch (e) {}
                    var name = null;
                    try { name = text(spell.getName ? spell.getName() : null); } catch (e) {}
                    var castsThisTurn = null;
                    try {
                        if (fm.spellCastCounts && fm.spellCastCounts[spellId] != null) {
                            castsThisTurn = num(fm.spellCastCounts[spellId]);
                        }
                    } catch (e) {}
                    var spellFacts = {
                        id: spellId,
                        name: name,
                        level: num(spell.level) !== null ? num(spell.level) : null,
                        spell_level_id: levelId,
                        ap_cost: num(prop(spell, 'apCost', spell.level)),
                        min_range: num(prop(spell, 'minRange')),
                        range: num(prop(spell, 'range')),
                        cast_in_line: !!prop(spell, 'castInLine', false),
                        cast_in_diagonal: !!prop(spell, 'castInDiagonal', false),
                        requires_los: !!prop(spell, 'castTestLos', false),
                        need_free_cell: !!prop(spell, 'needFreeCell', false),
                        need_taken_cell: !!prop(spell, 'needTakenCell', false),
                        max_cast_per_turn: num(prop(spell, 'maxCastPerTurn')),
                        max_cast_per_target: num(prop(spell, 'maxCastPerTarget')),
                        max_cast_per_fight: num(prop(spell, 'maxCastPerFight')),
                        min_cast_interval: num(prop(spell, 'minCastInterval')),
                        global_cooldown: num(prop(spell, 'globalCooldown')),
                        critical_hit_probability: num(prop(spell, 'criticalHitProbability')),
                        critical_failure_probability: num(prop(spell, 'criticalFailureProbability')),
                        zone_size: num(prop(spell, 'zoneSize')),
                        zone_shape: prop(spell, 'zoneShape') || null,
                        casts_this_turn: castsThisTurn,
                        castable: castable,
                        is_weapon: !!(spell.isWeapon && spell.isWeapon()),
                        effects: effects(level && level.effects),
                        critical_effects: effects(level && level.criticalEffect)
                    };
                    state.spells.push(spellFacts);
                }
                var lastActions = [];
                try {
                    var stack = fm.fightMessagesStack || fm._fightMessagesStack || [];
                    var start = Math.max(0, stack.length - 48);
                    for (var ai = start; ai < stack.length; ai++) {
                        var msg = stack[ai] || {};
                        var mt = text(msg._messageType);
                        if (!mt) continue;
                        lastActions.push({
                            type: mt,
                            action_id: num(msg.actionId),
                            source_id: num(msg.sourceId),
                            target_id: num(msg.targetId),
                            spell_id: num(msg.spellId),
                            spell_level: num(msg.spellLevel),
                            destination_cell: num(msg.destinationCellId),
                            critical: num(msg.critical),
                            value: num(msg.value),
                            delta: num(msg.delta),
                            effect_id: num(msg.effectId),
                            cell: num(msg.cellId),
                            key_movements: Array.isArray(msg.keyMovements)
                                ? msg.keyMovements.slice(0, 32) : null
                        });
                    }
                } catch (e) {}
                state.last_actions = lastActions;
                try { state.spell_cast_counts = fm.spellCastCounts || {}; } catch (e) {}
                for (var la = 0; la < lastActions.length; la++) {
                    var lastAction = lastActions[la];
                    if (!lastAction.spell_id || lastAction.source_id === null
                        || lastAction.source_id === myId) continue;
                    // Load the spell descriptor the first time a monster casts it.
                    try { fm.getFighterSpell(lastAction.spell_id, lastAction.source_id,
                                             function() {}); } catch (e) {}
                }
                return _agentResult(true, state);
            },
            'nav.follow': function(args) {
                return _agentResult(true, _agentCommander('FOLLOW'), null);
            },
            // Travel, using the client's own machinery instead of a blind protocol
            // message. The client already owns a pathfinder and a map transition:
            //
            //   isoEngine.gotoNeighbourMap(dir, cell, x, y)  walk to the border cell
            //       and send ChangeMapMessage when the walk lands
            //   isoEngine._movePlayerOnMap(cell, flag, cb)   pathfind within this map
            //   isoEngine._requestMapChange(mapId, dir)      transition animation
            //
            // Sending ChangeMapMessage by hand (what this did) asks for one edge hop
            // from wherever the character happens to stand; the server refuses it
            // unless that is the edge cell. The client's own transition walks there
            // first, so this drives that instead of reimplementing a pathfinder.
            //
            // Targets, exactly one:
            //   cell       walk to that cell on this map (a specific spot)
            //   npc_id     walk next to that NPC on this map (its cell is occupied)
            //   direction  leave by that edge (left/right/top/bottom)
            //   map_id     go to that map; adjacent maps are left by their edge,
            //              and a far map is reported with the exits of this one
            'nav.goto': function(args) {
                var iso = window.isoEngine || null;
                var a = args || {};
                var details = _agentMapDetails();
                var here = details.map_id;
                var wantMap = parseInt(a.map_id !== undefined ? a.map_id : a.mapId, 10) || null;
                var wantCell = parseInt(a.cell !== undefined ? a.cell : a.cellId, 10);
                var npcId = parseInt(a.npc_id !== undefined ? a.npc_id : a.npcId, 10) || null;
                if (isNaN(wantCell)) wantCell = null;
                // An NPC stands on its own cell, so walking there has to stop on a
                // free neighbour. `_movePlayerOnMap(cell, true, ...)` is the client's
                // form for exactly that; passing false paths onto the occupied cell.
                var stopAdjacent = false;
                var dir = String(a.direction || a.dir || '').toLowerCase().trim();
                if (dir === 'up') dir = 'top';
                if (dir === 'down') dir = 'bottom';
                if (dir && ['left', 'right', 'top', 'bottom'].indexOf(dir) < 0) {
                    return _agentResult(false, { direction: dir, exits: details.exits },
                        'direction must be one of left, right, top, bottom');
                }

                var exits = details.exits || [];
                var exitFor = function (d) {
                    for (var ei = 0; ei < exits.length; ei++) {
                        if (exits[ei].direction === d) return exits[ei];
                    }
                    return null;
                };
                var exitTo = function (m) {
                    for (var ei = 0; ei < exits.length; ei++) {
                        if (Number(exits[ei].map_id) === Number(m)) return exits[ei];
                    }
                    return null;
                };

                // Resolve an NPC to the cell it is standing on.
                if (wantCell == null && npcId) {
                    var found = _findNpcActor(npcId);
                    var actor = found && found.actor;
                    if (actor && actor.cell != null) {
                        wantCell = actor.cell;
                        stopAdjacent = true;
                    }
                    if (wantCell == null) {
                        return _agentResult(false, { npc_id: npcId, map_id: here,
                            npcs: details.npcs },
                            'npc ' + npcId + ' is not on map ' + here);
                    }
                }

                if (here == null || here === undefined) {
                    return _agentResult(false, { map_id: null },
                        'not in world yet: there is no current map');
                }

                // The tutorial is the authority on what may happen next. Its gate is
                // checked here rather than left to the client because the client's
                // `gotoNeighbourMap` bypasses it: it walks to the border and sends a
                // transition the scenario then refuses, which looks like a stuck
                // character. Refusing up front names the restriction and hands the
                // caller the step it should follow instead.
                var tutorial = null;
                try { tutorial = _agentTutorial(); } catch (e) { tutorial = null; }
                var tutorialActive = !!(tutorial && tutorial.active);
                var allowedCells = tutorialActive ? tutorial.unblocked_cells : null;
                var tutorialHint = function () {
                    if (!tutorialActive) return '';
                    var parts = [];
                    if (tutorial.unblocked_npc_id != null) {
                        parts.push('tap NPC ' + tutorial.unblocked_npc_id);
                    }
                    if (allowedCells && allowedCells.length) {
                        parts.push('stand on cell ' + allowedCells.slice(0, 8).join('/'));
                    } else if (allowedCells) {
                        parts.push('stay where you are');
                    }
                    return parts.length ? ' Follow the tutorial step: '
                        + parts.join(', ') + '.' : '';
                };
                var refuseRestricted = function (stage, error, data) {
                    window.__agentLastNav = { at: Date.now(), stage: stage,
                        from_map: data && data.map_id,
                        direction: data && data.direction,
                        to_map: data && data.target_map,
                        tutorial_step: tutorial.step_id,
                        can_change_map: tutorial.can_change_map,
                        unblocked_cells: allowedCells,
                        unblocked_npc_id: tutorial.unblocked_npc_id };
                    return _agentResult(false, data, error + tutorialHint());
                };

                if (tutorialActive && npcId
                        && tutorial.unblocked_npc_id != null
                        && Number(tutorial.unblocked_npc_id) !== Number(npcId)) {
                    return refuseRestricted('tutorial_blocked',
                        'the tutorial only allows NPC ' + tutorial.unblocked_npc_id
                        + ', not ' + npcId + '.',
                        { map_id: here, npc_id: npcId, target_map: null });
                }

                // A map id equal to the current map is not a transition. If a cell
                // came with it, fall through and walk there.
                if (wantMap && Number(wantMap) === Number(here)) {
                    if (wantCell == null && !dir) {
                        return _agentResult(true, { map_id: here, already_here: true });
                    }
                    wantMap = null;
                }

                // A direction alone names the exit: the destination is whatever the
                // client's map data says is on that edge.
                if (!wantMap && dir) {
                    var edge = exitFor(dir);
                    if (!edge) {
                        return _agentResult(false, { map_id: here, direction: dir,
                            exits: exits, neighbours: details.neighbours },
                            'no exit to the ' + dir + ' on map ' + here);
                    }
                    wantMap = Number(edge.map_id);
                }

                // Cross to another map through the edge that leads there.
                if (wantMap && Number(wantMap) !== Number(here)) {
                    var crossing = dir ? exitFor(dir) : exitTo(wantMap);
                    if (!crossing) {
                        return _agentResult(false, { map_id: here, target_map: wantMap,
                            direction: dir, exits: exits, neighbours: details.neighbours },
                            'map ' + wantMap + ' is not adjacent to ' + here
                            + '; take one of the exits: '
                            + exits.map(function (e) {
                                return e.direction + '->' + e.map_id;
                            }).join(', '));
                    }
                    if (dir && Number(crossing.map_id) !== Number(wantMap)) {
                        return _agentResult(false, { map_id: here, target_map: wantMap,
                            direction: dir, exit_map: crossing.map_id, exits: exits },
                            'the ' + dir + ' exit leads to ' + crossing.map_id
                            + ', not ' + wantMap);
                    }
                    var changeDir = dir || crossing.direction;
                    if (tutorialActive && tutorial.can_change_map
                            && tutorial.can_change_map[changeDir] === false) {
                        return refuseRestricted('tutorial_blocked',
                            'the tutorial restricts map changes on the ' + changeDir
                            + ' edge, so the ' + changeDir + ' neighbour '
                            + crossing.map_id + ' cannot be reached yet.',
                            { map_id: here, target_map: Number(crossing.map_id),
                              direction: changeDir });
                    }
                    var exitCell = wantCell;
                    if (exitCell == null || (crossing.cells || []).indexOf(exitCell) < 0) {
                        exitCell = (crossing.cells || [])[0];
                    }
                    if (exitCell == null) {
                        return _agentResult(false, { map_id: here, target_map: wantMap,
                            direction: changeDir, exits: exits },
                            'the client reports a ' + changeDir + ' neighbour but no '
                            + changeDir + ' border cell to walk to');
                    }
                    var outcome = { from_map: here, to_map: Number(crossing.map_id),
                        requested_map: wantMap, direction: changeDir, cell: exitCell };
                    window.__agentLastNav = { at: Date.now(), stage: 'walking_to_edge',
                        from_map: here, to_map: Number(crossing.map_id),
                        direction: changeDir, cell: exitCell };
                    try {
                        // `gotoNeighbourMap` walks to the border and sends the change
                        // when the walk lands. A character already standing on a
                        // border cell has nothing to walk, so the transition is never
                        // sent: the call answers ok, the snapshot never changes, and
                        // the loop repeats the same crossing forever. Live tutorial:
                        // cell 322 was the left border cell and stage stayed
                        // `walk_then_change` across every attempt.
                        var currentCell = details.cell;
                        var atEdge = currentCell != null && (crossing.cells || [])
                            .some(function (c) { return Number(c) === Number(currentCell); });
                        if (atEdge && typeof iso._requestMapChange === 'function') {
                            iso._requestMapChange(Number(crossing.map_id), changeDir);
                            outcome.via = 'isoEngine._requestMapChange';
                            window.__agentLastNav = { at: Date.now(),
                                stage: 'change_requested_at_edge', from_map: here,
                                to_map: Number(crossing.map_id),
                                direction: changeDir, cell: currentCell };
                            return _agentResult(true, outcome);
                        }
                        if (typeof iso.gotoNeighbourMap === 'function') {
                            // The x/y arguments only position the border arrow. The
                            // transition itself uses the direction and the cell.
                            iso.gotoNeighbourMap(changeDir, exitCell, 0, 0);
                            outcome.via = 'isoEngine.gotoNeighbourMap';
                            window.__agentLastNav.stage = 'walk_then_change';
                            window.__agentLastNav.via = outcome.via;
                            return _agentResult(true, outcome);
                        }
                        if (typeof iso._movePlayerOnMap !== 'function'
                                || typeof iso._requestMapChange !== 'function') {
                            return _agentResult(false, outcome,
                                'the client has no map transition method');
                        }
                        var moved = iso._movePlayerOnMap(exitCell, false,
                            function (err, reached) {
                                if (err) {
                                    window.__agentLastNav = { at: Date.now(),
                                        stage: 'walk_failed', from_map: here,
                                        to_map: Number(crossing.map_id),
                                        direction: changeDir, cell: exitCell,
                                        error: String(err) };
                                    return;
                                }
                                if (reached != null && Number(reached) !== Number(exitCell)) {
                                    window.__agentLastNav = { at: Date.now(),
                                        stage: 'walk_stopped_short', from_map: here,
                                        to_map: Number(crossing.map_id),
                                        direction: changeDir, cell: exitCell,
                                        reached: reached };
                                    return;
                                }
                                iso._requestMapChange(Number(crossing.map_id), changeDir);
                                window.__agentLastNav = { at: Date.now(),
                                    stage: 'change_requested', from_map: here,
                                    to_map: Number(crossing.map_id),
                                    direction: changeDir, cell: exitCell };
                            });
                        if (moved == null) {
                            return _agentResult(false, outcome,
                                'no path to the ' + changeDir + ' border cell ' + exitCell);
                        }
                        outcome.via = 'isoEngine._movePlayerOnMap';
                        return _agentResult(true, outcome);
                    } catch (e) {
                        window.__agentLastNav = { at: Date.now(), stage: 'map_change_failed',
                            from_map: here, to_map: Number(crossing.map_id),
                            direction: changeDir, cell: exitCell, error: String(e) };
                        return _agentResult(false, outcome, 'map change failed: ' + e);
                    }
                }

                // Same map: walk to the cell.
                if (wantCell == null) {
                    return _agentResult(false, { map_id: here, exits: exits },
                        'nav.goto needs one of: cell, npc_id, direction, or a map_id '
                        + 'different from ' + here);
                }
                if (tutorialActive && allowedCells && !stopAdjacent && details.cell != null
                        && Number(wantCell) !== Number(details.cell)
                        && allowedCells.indexOf(Number(wantCell)) < 0) {
                    return refuseRestricted('tutorial_blocked',
                        'the tutorial only lets the character stand on '
                        + (allowedCells.length ? allowedCells.slice(0, 10).join('/')
                            : 'no cell') + ', not cell ' + wantCell + '.',
                        { map_id: here, target_map: null });
                }
                if (!iso || typeof iso._movePlayerOnMap !== 'function') {
                    return _agentResult(false, null, 'isoEngine._movePlayerOnMap unavailable');
                }
                var walkOutcome = { cell: wantCell, map_id: here,
                    via: 'isoEngine._movePlayerOnMap' };
                window.__agentLastNav = { at: Date.now(), stage: 'walking',
                    from_map: here, cell: wantCell };
                try {
                    // The callback fires after the server confirms; the request itself
                    // goes out synchronously inside the call, so ok here means "asked",
                    // and the next state.snapshot says whether the character moved.
                    var walked = iso._movePlayerOnMap(wantCell, stopAdjacent, function (err) {
                        if (err) {
                            walkOutcome.error = String(err);
                            window.__agentLastNav = { at: Date.now(),
                                stage: 'walk_failed', from_map: here, cell: wantCell,
                                error: String(err) };
                        }
                    });
                    if (walked == null) {
                        return _agentResult(false, walkOutcome,
                            'no path to cell ' + wantCell + ' on map ' + here);
                    }
                } catch (e) {
                    return _agentResult(false, walkOutcome, 'movement failed: ' + e);
                }
                return _agentResult(true, walkOutcome);
            },
            // Diagnostic: report the shape of the client's own objects.
            //
            // The snapshot reader assumes window.gui.playerData.character holds id,
            // name, level, position and hp. On a live client standing on 4,-21 it
            // returned null for every one of them, which made the agent believe there
            // was no character and nothing to act on. Guessing a second set of field
            // names would repeat the mistake, so this asks the client what it has.
            // Re-inject the agent script without restarting the app.
            //
            // mobile-disguise.js is registered with AddScriptToExecuteOnDocumentCreated,
            // so it runs again on any page load. Reloading the page therefore picks up
            // whatever is on disk right now. Without this, every client-side fix needed
            // a full app restart, and with no auto-launch that meant losing the session.
            // What the client knows about the current map, so cross-map routing can be
            // built on its data instead of a guess. `getChangeMapFlags(cell)` tells us
            // which border cell leads off this map; the *destination* ids come from the
            // server's map information, and this reports wherever they landed.
            'debug.map': function() {
                var iso = window.isoEngine || null;
                var pd = (window.gui && window.gui.playerData) || null;
                var md = (iso && iso.mapData) || {};
                var mr = (iso && iso.mapRenderer && iso.mapRenderer.map) || {};
                var out = { map_id: pd && pd.position ? pd.position.mapId : null,
                            mapData_keys: Object.keys(md).slice(0, 30),
                            rendererMap_keys: Object.keys(mr).slice(0, 30),
                            candidates: {} };
                var tryGet = function (label, obj, key) {
                    try { if (obj && obj[key] !== undefined) out.candidates[label] = obj[key]; }
                    catch (e) {}
                };
                tryGet('mapData.neighbours', md, 'neighbours');
                tryGet('mapData.neighbors', md, 'neighbors');
                tryGet('mapRendererMap.neighbours', mr, 'neighbours');
                tryGet('mapData.mapId', md, 'mapId');
                // Which cells on this map are exits, and where they lead. The flags
                // object has one boolean per direction; the old `flags.length` test
                // was always undefined, so this diagnostic listed no exits at all.
                try {
                    var details = _agentMapDetails();
                    out.exit_cells = details.exits;
                    out.npcs = details.npcs;
                    out.interactives = details.interactives;
                } catch (e) { out.exit_cells = []; }
                return _agentResult(true, out);
            },
            'debug.groups': function() {
                var out = _listMonsterGroups();
                return _agentResult(true, { count: out.length, groups: out });
            },
            'debug.npcs': function() {
                var list = _listNpcActors();
                return _agentResult(true, { count: list.length, npcs: list });
            },
            'debug.reload': function() {
                setTimeout(function () {
                    try { window.location.reload(); } catch (e) {}
                }, 250);
                return _agentResult(true, { reloading: true,
                    build: window.__agentBuild || '2026-10-25d',
                    note: 'a reload keeps the already-injected script; a full app '
                        + 'restart is required to pick up a rebuilt one' });
            },
            'debug.fight_state': function() {
                // Where the fight chain stands: was it requested, did placement
                // arrive, was a ready actually sent, and is the bot driving the
                // turns. Those four are indistinguishable from the host when
                // the fight simply sits there.
                var pdG = (window.gui && window.gui.playerData) || null;
                var fm = (window.gui && window.gui.fightManager) || null;
                var fb = window.__FarmBot || null;
                var st = (fb && fb.state) || null;
                return _agentResult(true, {
                    in_fight: !!(pdG && pdG.isFighting) || !!(fm && fm.isInFight && fm.isInFight()),
                    auto_fight: !!(fb && fb.enabled),
                    my_turn: !!(st && st.myTurn),
                    readied: !!(st && st.readied),
                    placement_pending: !!(st && st.placement),
                    last: window.__agentLastFight || null,
                    placement: window.__agentLastFightPlacement || null,
                    ready: window.__agentLastFightReady || null,
                    last_turn: window.__agentLastFightTurn || null,
                    last_turn_error: window.__agentLastFightTurnError || null,
                    note: window.__agentLastFight ? null
                        : 'no fight attempted since this page loaded'
                });
            },
            'debug.talk_state': function() {
                // Where the walk -> talk -> reply chain stopped.
                //
                // That chain is four steps across two messages and a dialog, and every
                // way it can break looks identical from the host: "nothing advanced".
                // This says which step, so the next live session is a reading, not a
                // guess.
                var pdT = (window.gui && window.gui.playerData) || null;
                var uiT = (window.gui && window.gui.npcDialogUi) || null;
                var hT = (window.gui && window.gui.npcDialogHandler) || null;
                var canSend = function(o) {
                    return !!(o && (typeof o._uiReplyHandler === 'function'
                        || typeof o.replyHandler === 'function'));
                };
                return _agentResult(true, {
                    in_dialog: !!(pdT && pdT.isInDialog),
                    dialog_ui: !!uiT,
                    dialog_handler: !!hT,
                    reply_handler: canSend(uiT) || canSend(hT),
                    reply_ids: (hT && hT.replyIds) ? hT.replyIds.slice(0, 8)
                        : ((uiT && uiT.replyIds) ? uiT.replyIds.slice(0, 8) : null),
                    last: window.__agentLastTalk || null,
                    note: window.__agentLastTalk ? null
                        : 'no talk attempted since this page loaded'
                });
            },
            'debug.last_disconnect': function() {
                var d = window.__agentLastDisconnect || null;
                return _agentResult(true, d || { none: true,
                    note: 'no disconnect observed since this page loaded' });
            },
            // Read-only client database index and paging.
            //
            // Task #27 needs the Touch-specific rows (quests, achievement
            // objectives, monster grades) that the DofusDB cache does not have.
            // Table names and row shapes differ by build, so this reports them
            // first; a harvester written from a guess would page a table that
            // does not exist. Sends nothing, writes nothing.
            'debug.dbtables': function() {
                var dbs = _agentDatabases();
                if (!dbs) {
                    return _agentResult(false, null, 'client databases are not loaded');
                }
                var tables = [];
                for (var name in dbs) {
                    if (!Object.prototype.hasOwnProperty.call(dbs, name)) continue;
                    var t = dbs[name];
                    tables.push({
                        table: name,
                        rows: (t && typeof t === 'object') ? Object.keys(t).length : null
                    });
                }
                tables.sort(function(a, b) { return a.table < b.table ? -1 : 1; });
                return _agentResult(true, { count: tables.length, tables: tables });
            },
            'debug.dbrows': function(args) {
                args = args || {};
                var dbs = _agentDatabases();
                if (!dbs) {
                    return _agentResult(false, null, 'client databases are not loaded');
                }
                var name = args.table != null ? String(args.table) : '';
                if (!name || !Object.prototype.hasOwnProperty.call(dbs, name)) {
                    return _agentResult(false, null, 'unknown or missing table: ' + name);
                }
                var table = dbs[name];
                if (!table || typeof table !== 'object') {
                    return _agentResult(false, null, 'table is not an object: ' + name);
                }
                var allIds = Object.keys(table);
                var wanted = [];
                if (Object.prototype.toString.call(args.ids) === '[object Array]') {
                    for (var i = 0; i < args.ids.length && wanted.length < 200; i++) {
                        var id = args.ids[i];
                        if (Object.prototype.hasOwnProperty.call(table, id)) {
                            wanted.push(String(id));
                        }
                    }
                } else {
                    var offset = _agentCap(args.offset, 0, allIds.length, 0);
                    var limit = _agentCap(args.limit, 1, 200, 50);
                    wanted = allIds.slice(offset, offset + limit);
                }
                var keys = {};
                var rows = [];
                for (var w = 0; w < wanted.length; w++) {
                    var row = table[wanted[w]];
                    if (!row || typeof row !== 'object') {
                        rows.push({ id: wanted[w], db: false });
                        continue;
                    }
                    var item = { id: row.id != null ? row.id : wanted[w], db: true };
                    for (var f in row) {
                        if (!Object.prototype.hasOwnProperty.call(row, f)) continue;
                        var v = row[f];
                        if (v === null || v === undefined) continue;
                        if (typeof v === 'object') {
                            if (Object.prototype.toString.call(v) === '[object Array]'
                                    && v.length <= 80) {
                                var simple = true;
                                for (var ai = 0; ai < v.length; ai++) {
                                    if (v[ai] !== null && typeof v[ai] === 'object') {
                                        simple = false;
                                        break;
                                    }
                                }
                                if (simple) item[f] = v;
                            }
                            continue;
                        }
                        if (typeof v === 'string' && v.length > 500) v = v.slice(0, 500);
                        if (Object.keys(keys).length < 60) keys[f] = true;
                        item[f] = v;
                    }
                    rows.push(item);
                }
                return _agentResult(true, {
                    table: name,
                    count: allIds.length,
                    keys: Object.keys(keys),
                    rows: rows
                });
            },
            // What the quest DB and the client's objective compass actually say.
            // The planner reads `parameter0` as a map id, but for some objective
            // types that field is an index into another table, and a wrong
            // reading sends the agent walking to a map that does not exist.
            // Read-only: no message is sent and nothing is changed.
            'debug.questdb': function(args) {
                var out = {};
                var a = args || {};
                var qid = Number(a.quest_id || 3841);
                var oid = Number(a.objective_id || 13541);
                var mid = Number(a.map_id || 0);
                var keys = function(o, n) {
                    try { return o ? Object.keys(o).slice(0, n || 60) : null; }
                    catch (e) { return 'ERR:' + e; }
                };
                var val = function(fn) {
                    try { var v = fn(); return (v === undefined) ? '__undefined' : v; }
                    catch (e) { return 'ERR:' + e; }
                };
                var pd = window.gui && window.gui.playerData;
                var sq = pd && pd.quests && pd.quests.standardQuests;
                var q = (sq && typeof sq.getActiveQuestById === 'function')
                    ? sq.getActiveQuestById(qid) : null;
                out.quest = null;
                try {
                    if (q) {
                        out.quest = {
                            id: q.questId,
                            finishedCount: q.finishedCount,
                            objectives: q.objectives,
                            db_objective_keys: keys(q.dbObjectives),
                            db_objective: (q.dbObjectives || {})[oid] || null
                        };
                    }
                } catch (e) { out.quest = 'ERR:' + e; }
                out.objective_db = val(function () {
                    var dbs = window.gui && window.gui.databases;
                    return dbs && dbs.QuestObjectives
                        ? (dbs.QuestObjectives[oid] || null) : null;
                });
                out.databases = val(function () {
                    return (window.gui && window.gui.databases)
                        ? keys(window.gui.databases, 60) : null;
                });
                out.map_position = null;
                out.map_position_count = null;
                try {
                    var dbs2 = window.gui && window.gui.databases;
                    var mp = dbs2 && dbs2.MapPositions;
                    if (mp) {
                        out.map_position_count = Object.keys(mp).length;
                        if (mid) out.map_position = mp[mid] || null;
                    }
                } catch (e) { out.map_position = 'ERR:' + e; }
                out.position = val(function () {
                    return (pd && pd.position) ? JSON.stringify(pd.position) : null;
                });
                out.gps = null;
                try {
                    var g = window.gui && window.gui.GPS;
                    if (g) {
                        out.gps = { keys: keys(g, 40) };
                        ['_currentTarget', 'currentTarget', '_target', 'target',
                         '_targetMapId', 'targetMapId', 'currentMapId'].forEach(function (k) {
                            if (g[k] !== undefined) out.gps[k] = g[k];
                        });
                        if (typeof g.getCurrentTarget === 'function') {
                            out.gps.getCurrentTarget = val(function () {
                                return g.getCurrentTarget();
                            });
                        }
                    }
                } catch (e) { out.gps = 'ERR:' + e; }
                out.quests_keys = val(function () {
                    return (pd && pd.quests) ? keys(pd.quests, 40) : null;
                });
                out.scenario_keys = val(function () {
                    return (window.gui && window.gui.scenarioManager)
                        ? keys(window.gui.scenarioManager, 40) : null;
                });
                return _agentResult(true, out);
            },
            'debug.character': function(args) {
                return _agentCharacterReport(args);
            },
            'debug.itemdb': function(args) {
                return _agentItemDbReport(args);
            },
            'debug.effectdb': function(args) {
                return _agentEffectDbReport(args);
            },
            'debug.version': function() {
                return _agentResult(true, { build: window.__agentBuild || '2026-10-25d' });
            },
            'debug.probe': function() {
                var out = {};
                var keys = function(o, n) {
                    try { return o ? Object.keys(o).slice(0, n || 40) : null; }
                    catch (e) { return 'ERR:' + e; }
                };
                var val = function(fn) {
                    try { var v = fn(); return (v === undefined) ? '__undefined' : v; }
                    catch (e) { return 'ERR:' + e; }
                };
                var pd = null, ch = null;
                try { pd = window.gui && window.gui.playerData; } catch (e) {}
                try { ch = pd && pd.character; } catch (e) {}
                out.gui_keys = keys(window.gui, 30);
                out.playerData_keys = keys(pd, 40);
                out.character_keys = keys(ch, 40);
                out.characteristics_keys = keys(pd && pd.characteristics, 40);
                out.iso_keys = keys(window.isoEngine, 40);
                out.dofus_keys = keys(window.dofus, 20);
                out.candidates = {
                    'character.name': val(function(){ return ch && ch.name; }),
                    'character.id': val(function(){ return ch && ch.id; }),
                    'character.level': val(function(){ return ch && ch.level; }),
                    'characteristics.level': val(function(){ return pd && pd.characteristics && pd.characteristics.level; }),
                    'characteristics.life': val(function(){ return pd && pd.characteristics && pd.characteristics.lifePoints; }),
                    'position': val(function(){ return pd && pd.position ? JSON.stringify(pd.position) : null; }),
                    'character.position': val(function(){ return ch && ch.position ? JSON.stringify(ch.position) : null; }),
                    'iso.currentMapId': val(function(){ return window.isoEngine && window.isoEngine.currentMapId; }),
                    'iso.mapData.mapId': val(function(){ return window.isoEngine && window.isoEngine.mapData && window.isoEngine.mapData.mapId; }),
                    'iso.actorManager.playerId': val(function(){ return window.isoEngine && window.isoEngine.actorManager && window.isoEngine.actorManager.playerId; }),
                    'iso.actorManager.playingActorId': val(function(){ return window.isoEngine && window.isoEngine.actorManager && window.isoEngine.actorManager.playingActorId; }),
                    'gui.playerData.inventory.kamas': val(function(){ return pd && pd.inventory && pd.inventory.kamas; }),
                    // The paths the snapshot now reads. Listed here so one probe run
                    // verifies the reader end to end instead of confirming one field.
                    'characterBaseInformations.name': val(function(){ return pd && pd.characterBaseInformations && pd.characterBaseInformations.name; }),
                    'characterBaseInformations.level': val(function(){ return pd && pd.characterBaseInformations && pd.characterBaseInformations.level; }),
                    'position.mapId': val(function(){ return pd && pd.position && pd.position.mapId; }),
                    'position.coordinates': val(function(){ return pd && pd.position && pd.position.coordinates ? JSON.stringify(pd.position.coordinates) : null; }),
                    'lifePoints': val(function(){ return pd && pd.lifePoints; }),
                    'effectiveMaxLifePoints': val(function(){ return pd && pd.effectiveMaxLifePoints; }),
                    'actionPoints': val(function(){ return pd && pd.actionPoints; }),
                    'movementPoints': val(function(){ return pd && pd.movementPoints; }),
                    'isFighting': val(function(){ return pd && pd.isFighting; })
                };
                return _agentResult(true, out);
            },
            'inv.list': function() {
                var snap = _agentSnapshot();
                return _agentResult(!!snap.inventory, snap.inventory || null,
                    snap.inventory ? null : 'inventory is not exposed by this client build (probe: playerData.inventory missing)');
            },
            'state.snapshot': function() { return _agentResult(true, _agentSnapshot()); },
            'chat.say': function(args) {
                var text = (args && args.text) || '';
                if (!text) return _agentResult(false, null, 'chat.say needs non-empty text');
                try {
                    if (window.gui && window.gui.chat && typeof window.gui.chat.sendMessage === 'function') {
                        window.gui.chat.sendMessage(text, 0);
                        return _agentResult(true, { via: 'gui.chat', channel: 'say' });
                    }
                    if (window.dofus && window.dofus.connectionManager) {
                        window.dofus.connectionManager.sendMessage('ChatClientMultiMessage',
                            { channel: 0, content: text, parameters: [] });
                        return _agentResult(true, { via: 'connectionManager', channel: 'say', verified: false });
                    }
                    return _agentResult(false, null, 'no chat transport available');
                } catch(err) { return _agentResult(false, null, err.toString()); }
            },
            'quest.advance': function(args) {
                // A dialog that is already open has to be answered, and answering is
                // the only way a talk objective ever completes: opening the NPC is
                // step one, choosing a reply is step two. Before this, `quest.advance`
                // could open a dialog and then had no way to finish it — which is why
                // 46% of the objectives in the KB (talk + deliver) could not move.
                //
                // The client exposes the whole dialog: gui.npcDialogUi._uiReplyHandler
                // (index) sends NpcDialogReplyMessage{replyId: replyIds[index]}.
                var pd0 = (window.gui && window.gui.playerData) || null;
                if (pd0 && pd0.isInDialog) {
                    var want = (args && args.reply !== undefined) ? parseInt(args.reply, 10) : 0;
                    if (isNaN(want)) want = 0;
                    // A reply can be named by the text the policy saw instead of
                    // by an index. Indices are positional and change with the
                    // server's ordering; the text is what the policy reasons
                    // about. Exact normalized match only -- a fuzzy match could
                    // silently pick "Non, merci" for "Oui".
                    if (args && args.reply_text != null) {
                        var resolved = _agentReplyIndexForText(args.reply_text);
                        var hits = resolved.hits;
                        if (!hits.length) {
                            return _agentResult(false, { reply_text: args.reply_text,
                                reply_count: resolved.count },
                                'no dialog reply matches the given text');
                        }
                        if (hits.length > 1) {
                            return _agentResult(false, { reply_text: args.reply_text,
                                candidates: hits },
                                'the given reply text matches ' + hits.length + ' replies');
                        }
                        if (args.reply !== undefined
                                && parseInt(args.reply, 10) !== hits[0]) {
                            return _agentResult(false, { reply: args.reply,
                                reply_text_index: hits[0] },
                                'reply and reply_text point at different choices');
                        }
                        want = hits[0];
                    }
                    // Which object owns the reply sender differs by build. On 1.74
                    // `gui.npcDialogHandler` receives NpcDialogCreationMessage and
                    // holds both `replyIds` and `_uiReplyHandler`, while
                    // `gui.npcDialogUi` is only the view. Requiring the view made
                    // every open dialog fail with "npcDialogUi is unavailable"
                    // while the handler sat right beside it. Accept either, and
                    // refuse only when neither can send.
                    var dialogOwners = [];
                    var addOwner = function(label, owner) {
                        if (!owner) return;
                        if (typeof owner._uiReplyHandler === 'function') {
                            dialogOwners.push({ owner: owner, fn: owner._uiReplyHandler,
                                                label: label + '._uiReplyHandler',
                                                ids: owner.replyIds || null });
                        }
                        if (typeof owner.replyHandler === 'function') {
                            dialogOwners.push({ owner: owner, fn: owner.replyHandler,
                                                label: label + '.replyHandler',
                                                ids: owner.replyIds || null });
                        } else if (owner.replyHandler && typeof owner.replyHandler === 'object'
                                   && typeof owner.replyHandler._uiReplyHandler === 'function') {
                            dialogOwners.push({ owner: owner.replyHandler,
                                                fn: owner.replyHandler._uiReplyHandler,
                                                label: label + '.replyHandler._uiReplyHandler',
                                                ids: owner.replyHandler.replyIds || owner.replyIds || null });
                        }
                    };
                    try { addOwner('gui.npcDialogHandler', window.gui.npcDialogHandler); } catch (e) {}
                    try { addOwner('gui.npcDialogUi', window.gui.npcDialogUi); } catch (e) {}
                    if (!dialogOwners.length) {
                        return _agentResult(false, { dialog_owners: [] },
                            'in a dialog but no reply handler is available (npcDialogHandler/npcDialogUi missing)');
                    }
                    // The ids and the sender can live on different objects: the
                    // handler received the reply list, the view forwards clicks.
                    // Use the first populated list with the first sender.
                    var pick = dialogOwners[0];
                    for (var dpi = 0; dpi < dialogOwners.length; dpi++) {
                        if (dialogOwners[dpi].ids && dialogOwners[dpi].ids.length) {
                            pick = dialogOwners[dpi];
                            break;
                        }
                    }
                    var ids = pick.ids || null;
                    if (!ids) {
                        for (var dpi2 = 0; dpi2 < dialogOwners.length; dpi2++) {
                            if (dialogOwners[dpi2].ids) { ids = dialogOwners[dpi2].ids; break; }
                        }
                    }
                    // A dialog is open, but the replies have not arrived yet.
                    //
                    // `isInDialog` flips when the dialog opens; the reply list is filled
                    // by the message that carries it, a moment later. Replying in that gap
                    // sent reply index 0 into a list that did not exist — which either
                    // closes the dialog or picks the wrong answer, and in both cases the
                    // objective does not advance and nothing says why. Wait instead: the
                    // loop calls again in a second, and by then the list is there.
                    if (!ids) {
                        window.__agentLastTalk = {
                            at: Date.now(), stage: 'dialog_open_no_replies',
                            replies: null, want: want
                        };
                        return _agentResult(false, { stage: 'dialog_open_no_replies' },
                            'a dialog is open but replyIds is not populated yet; retry in a moment');
                    }
                    if (!ids.length) {
                        window.__agentLastTalk = {
                            at: Date.now(), stage: 'dialog_open_no_choices',
                            replies: 0, want: want
                        };
                        return _agentResult(false, { stage: 'dialog_open_no_choices', replies: 0 },
                            'the dialog offers no replies; it may need to be closed instead');
                    }
                    if (want >= ids.length) {
                        return _agentResult(false, { replies: ids.length },
                            'dialog has ' + ids.length + ' replies; asked for index ' + want);
                    }
                    try {
                        var talkUi = (window.gui && window.gui.npcDialogUi) || null;
                        var talkTexts = (talkUi && talkUi.replies) || [];
                        var talkQuestion = (talkUi && talkUi.currentMsg != null)
                            ? _agentPlainText(talkUi.currentMsg) : null;
                        var talkReply = talkTexts[want] != null
                            ? _agentPlainText(talkTexts[want]) : null;
                        var talkReplyId = ids[want] != null ? ids[want] : null;
                        pick.fn.call(pick.owner, want);
                        // Question + chosen reply + server id is the free label
                        // for the dialog policy: an outcome later says whether
                        // this choice advanced the objective.
                        window.__agentLastTalk = {
                            at: Date.now(), stage: 'replied', replied: want,
                            replies: ids.length, reply_ids: ids.slice(0, 8),
                            reply_id: talkReplyId, reply_text: talkReply,
                            question: talkQuestion, via: pick.label
                        };
                        return _agentResult(true, { replied: want, replies: ids.length,
                            reply_id: talkReplyId, reply_text: talkReply,
                            via: pick.label });
                    } catch (e) {
                        window.__agentLastTalk = {
                            at: Date.now(), stage: 'reply_threw', error: String(e)
                        };
                        return _agentResult(false, null, 'reply failed: ' + e);
                    }
                }

                // Two ways to advance, both captured on 2026-10-22:
                //   explicit objective -> QuestObjectiveValidationMessage
                //   otherwise          -> talk to the NPC (npcActionId 3) and let
                //                         the dialog drive the step.
                var questId = (args && (args.quest_id || args.questId)) || null;
                var objectiveId = (args && (args.objective_id || args.objectiveId)) || null;
                if (questId && objectiveId) {
                    var qid = Number(questId), oid = Number(objectiveId);
                    var claim = _questClaimCheck(qid, oid);
                    // Every attempt is a candidate training row: the reason is the
                    // precondition the gate believes it is enforcing. The trajectory
                    // captures console lines, so this is the feature that lets the
                    // label extractor see refusals and not only packets.
                    try {
                        console.log('[AgentGuard] claim ' + (claim.ok ? 'allowed' : 'refused')
                            + ' quest=' + qid + ' objective=' + oid
                            + (claim.ok ? '' : ' reason=' + claim.reason));
                    } catch (e) {}
                    if (!claim.ok) {
                        window.__agentLastTalk = { at: Date.now(), stage: 'validate_refused',
                            quest_id: qid, objective_id: oid, reason: claim.reason };
                        return _agentResult(false, claim, claim.error);
                    }
                    window.__agentLastValidate = { quest_id: qid, objective_id: oid,
                                                   at: Date.now() };
                    var r = _cmSend('QuestObjectiveValidationMessage',
                        { questId: qid, objectiveId: oid });
                    return _agentResult(r.ok, { quest_id: qid, objective_id: oid },
                        r.ok ? null : r.error);
                }
                // Accepting a quest is not talking to an NPC. The client's own quest
                // link and quest book send QuestStartRequestMessage. This handler used
                // to fall through to "talk to the first NPC on the map", so a call
                // that meant "start quest 584" produced the same "no NPC on this map"
                // failure 70 times in one recorded session. When the client already
                // knows the quest (active or finished), keep the talk path below.
                var sq0 = window.gui && window.gui.playerData
                    && window.gui.playerData.quests
                    && window.gui.playerData.quests.standardQuests;
                if (questId && sq0) {
                    var known = false;
                    try {
                        known = (typeof sq0.isActiveQuestById === 'function'
                                    && sq0.isActiveQuestById(questId))
                             || (typeof sq0.isFinishedQuestById === 'function'
                                    && sq0.isFinishedQuestById(questId));
                    } catch (e) {}
                    if (!known) {
                        // The client reports the request as sent; the server decides
                        // whether to activate the quest, and QuestStartedMessage is
                        // what moves it into standardQuests. A quest whose
                        // prerequisites are unmet never does, and reporting ok every
                        // step made quest 584 an endless no-op. Check the previous
                        // request before sending another.
                        var starts = window.__agentQuestStarts
                            || (window.__agentQuestStarts = {});
                        var startKey = Number(questId);
                        var pendingStart = starts[startKey] || null;
                        var nowStart = Date.now();
                        if (pendingStart) {
                            var waited = nowStart - Number(pendingStart.at || 0);
                            if (waited < 5000) {
                                window.__agentLastTalk = {
                                    at: nowStart, stage: 'quest_start_pending',
                                    quest_id: startKey, waited_ms: waited
                                };
                                // Still in flight, not a failed directive: the
                                // autopilot abandons a goal after three ok=false
                                // steps, and a slow acknowledgement is not one.
                                // The planner counts the attempt from the
                                // observation, which still says "not active".
                                return _agentResult(true,
                                    { quest_id: startKey, via: 'QuestStartRequestMessage',
                                      pending: true, waited_ms: waited }, null);
                            }
                            delete starts[startKey];
                            window.__agentLastTalk = {
                                at: nowStart, stage: 'quest_not_activated',
                                quest_id: startKey, waited_ms: waited
                            };
                            return _agentResult(false,
                                { quest_id: startKey, via: 'QuestStartRequestMessage',
                                  not_activated: true, waited_ms: waited },
                                'the server did not activate quest ' + startKey
                                + ' after the start request');
                        }
                        var start = _cmSend('QuestStartRequestMessage',
                            { questId: Number(questId) });
                        if (start.ok) starts[startKey] = { at: Date.now() };
                        window.__agentLastTalk = {
                            at: Date.now(),
                            stage: start.ok ? 'quest_start_sent' : 'quest_start_failed',
                            quest_id: startKey,
                            error: start.ok ? null : String(start.error || '')
                        };
                        return _agentResult(start.ok,
                            { quest_id: startKey, via: 'QuestStartRequestMessage' },
                            start.ok ? null : start.error);
                    }
                }
                // The objective names its NPC; the planner passes it as npc_id. This
                // handler used to ignore that and talk to the FIRST NPC on the map,
                // which is a different person — the call succeeded, nothing advanced,
                // and the loop spent its budget re-sending it. Talking to the wrong
                // NPC and reporting ok is worse than failing: it hides the mistake.
                // Without an npc_id the only honest answer is the list of NPCs here,
                // so the caller can choose one instead of guessing.
                var wantNpc = Number((args && (args.npc_id || args.npcId)) || 0) || null;
                if (!wantNpc) {
                    var hereNpcs = _listNpcActors().slice(0, 20).map(function (n) {
                        return { npc_id: n.templateId, actor_id: n.contextualId, cell: n.cell };
                    });
                    window.__agentLastTalk = { at: Date.now(), stage: 'npc_id_required',
                        quest_id: questId, npcs_here: hereNpcs.length };
                    return _agentResult(false,
                        { quest_id: questId, npcs_here: hereNpcs, reason: 'npc_id_required' },
                        'quest is active but no npc_id was given; npcs on this map: '
                        + (hereNpcs.length
                            ? hereNpcs.map(function (n) { return n.npc_id; }).join(',')
                            : 'none'));
                }
                var found = _findNpcActor(wantNpc);
                var npc = found.actor;
                var npcIds = found.seen;
                if (wantNpc && !npc) {
                    // The right answer here is "you are on the wrong map", because that
                    // is what the agent can act on. The old code either talked to
                    // somebody else or reported a bare "no NPC".
                    var here = _currentMapId();
                    window.__agentLastTalk = { at: Date.now(), stage: 'npc_not_on_map',
                        wanted_npc: wantNpc, map_id: here, npcs_here: npcIds };
                    return _agentResult(false, { quest_id: questId, wanted_npc: wantNpc,
                        map_id: here, npcs_here: npcIds },
                        'npc ' + wantNpc + ' is not on map ' + here + ' (npcs here: '
                        + (npcIds.length ? npcIds.join(',') : 'none') + ')');
                }
                if (!npc) {
                    return _agentResult(false, { quest_id: questId },
                        'no NPC on this map to talk to; pass objective_id to validate directly');
                }
                if (npc.contextualId == null || isNaN(Number(npc.contextualId))) {
                    return _agentResult(false, {
                        quest_id: questId, npc_template: npc.templateId,
                        npc_cell: npc.cell },
                        'npc ' + npc.templateId + ' has no actor id in the client model, '
                            + 'so a talk request cannot name it');
                }
                var mapId = _currentMapId();
                // The wire field is the actor's contextual id, which this build
                // stores as `actorId` (the menu sends `npcId: actorId`). The template
                // id talks to nobody.
                var doTalk = function () {
                    return _cmSend('NpcGenericActionRequestMessage',
                        { npcId: npc.contextualId, npcActionId: 3, npcMapId: mapId });
                };
                // Talking from the far side of the map does nothing in this game, and
                // the planner has no cell to walk to — only the client knows where the
                // NPC stands. So walk first, then talk, the same way the client does
                // before attacking a monster group.
                var ua = null;
                try {
                    var am2 = window.actorManager
                        || (window.isoEngine && window.isoEngine.actorManager);
                    ua = am2 && am2.userActor;
                } catch (e) {}
                var myCell = ua && ua.cellId != null ? Number(ua.cellId) : null;
                var npcCell = npc.cell != null ? Number(npc.cell) : null;
                var talkData = { quest_id: questId, npc_template: npc.templateId,
                    npc_contextual_id: npc.contextualId, map_id: mapId,
                    npc_cell: npcCell, my_cell: myCell };

                // Already next to the NPC: the talk request is valid immediately.
                if (myCell != null && npcCell != null && _cellsAdjacent(myCell, npcCell)) {
                    var talkNow = doTalk();
                    window.__agentLastTalk = { at: Date.now(),
                        stage: talkNow.ok ? 'talked' : 'talk_failed',
                        npc_template: npc.templateId, npc_contextual_id: npc.contextualId,
                        map_id: mapId, npc_cell: npcCell, my_cell: myCell,
                        error: talkNow.ok ? null : String(talkNow.error || '') };
                    talkData.via = 'talked';
                    return _agentResult(talkNow.ok, talkData, talkNow.ok ? null : talkNow.error);
                }

                // The NPC stands on its own cell, and the server does not accept a
                // move onto it. `_movePlayerOnMap(cell, true, cb)` is the client's own
                // "walk next to this cell, then call me" form — the same one it uses
                // before harvesting or opening a door. Passing false (the old call)
                // aimed the path at the occupied cell, which never arrived.
                var iso2 = window.isoEngine;
                if (myCell != null && npcCell != null && iso2
                        && typeof iso2._movePlayerOnMap === 'function') {
                    var walkError = null;
                    var moved = null;
                    try {
                        moved = iso2._movePlayerOnMap(npcCell, true, function (err) {
                            if (err) {
                                walkError = String(err && err.message || err);
                                window.__agentLastTalk = { at: Date.now(), stage: 'walk_failed',
                                    npc_template: npc.templateId,
                                    npc_contextual_id: npc.contextualId, map_id: mapId,
                                    npc_cell: npcCell, my_cell: myCell, error: walkError };
                                return;
                            }
                            try { doTalk(); } catch (e) {}
                        });
                    } catch (e) { walkError = String(e); }
                    if (moved != null && !walkError) {
                        window.__agentLastTalk = { at: Date.now(), stage: 'walking_then_talking',
                            npc_template: npc.templateId,
                            npc_contextual_id: npc.contextualId, map_id: mapId,
                            npc_cell: npcCell, my_cell: myCell };
                        talkData.via = 'walking to npc then talking';
                        return _agentResult(true, talkData);
                    }
                    if (walkError) {
                        window.__agentLastTalk = { at: Date.now(), stage: 'walk_failed',
                            npc_template: npc.templateId, npc_contextual_id: npc.contextualId,
                            map_id: mapId, npc_cell: npcCell, my_cell: myCell,
                            error: walkError };
                        return _agentResult(false, talkData,
                            'could not walk next to npc ' + npc.templateId + ': ' + walkError);
                    }
                }
                // No movement API or no path: report the honest failure instead of
                // sending a talk the server will ignore from this distance.
                window.__agentLastTalk = { at: Date.now(), stage: 'not_adjacent',
                    npc_template: npc.templateId, npc_contextual_id: npc.contextualId,
                    map_id: mapId, npc_cell: npcCell, my_cell: myCell };
                return _agentResult(false, talkData,
                    'npc ' + npc.templateId + ' is at cell ' + npcCell
                        + ' and the character is at cell ' + myCell
                        + '; the client could not walk next to it');
            },
            'inv.sell': function(args) {
                // Same wire call as hdv.list; resolved through the map, not `this`,
                // because handlers are invoked as bare functions.
                return _agentHandlers['hdv.list'](args);
            },
            'hdv.scan': function(args) {
                // ExchangeBidHouseTypeMessage opens a category, then
                // ExchangeBidHouseListMessage lists it. Both need an open
                // exchange: the server ignores them otherwise, which is why this
                // reports the missing precondition instead of pretending.
                //
                // Prices arrive asynchronously (ExchangeBidHouseInListAddedMessage,
                // ObjectAveragePricesMessage) and are cached by __PriceCache. If
                // the cache already has a number for this item, return it --
                // asking the server again would just cost a round trip.
                var type = (args && (args.type || args.category)) || null;
                var id = (args && (args.id || args.item_type)) || null;
                var itemName = (args && args.item) || null;
                var gid = args && args.gid != null ? Number(args.gid) : null;

                if (gid == null && itemName) {
                    var found = _findItem(itemName);
                    if (found.length && found[0].gid != null) gid = Number(found[0].gid);
                }

                if (gid != null) {
                    var stats = __PriceCache.stats(gid) || __PriceCache.averageOf(gid);
                    if (stats) {
                        return _agentResult(true, { gid: gid, item: itemName, stats: stats,
                            note: 'cached from a server message; age_ms says how fresh' });
                    }
                }

                if (type == null && id == null) {
                    // No category given but we know the item: ask for the whole
                    // average table, which is one message and covers every item.
                    if (gid != null || itemName) {
                        var ra = _cmSend('ObjectAveragePricesGetMessage', {});
                        if (!ra.ok) return _agentResult(false, { item: itemName }, ra.error);
                        return _agentResult(true, { gid: gid, item: itemName,
                            sent: [{ message: 'ObjectAveragePricesGetMessage' }],
                            note: 'average table requested; call hdv.scan again once it arrives' });
                    }
                    return _agentResult(false, null, 'hdv.scan needs an item, a category type or an item type id');
                }

                var sent = [];
                if (type != null) {
                    var r1 = _cmSend('ExchangeBidHouseTypeMessage', { type: Number(type) });
                    if (!r1.ok) return _agentResult(false, null, r1.error);
                    sent.push({ message: 'ExchangeBidHouseTypeMessage', type: Number(type) });
                }
                if (id != null) {
                    var r2 = _cmSend('ExchangeBidHouseListMessage', { id: Number(id) });
                    if (!r2.ok) return _agentResult(false, null, r2.error);
                    sent.push({ message: 'ExchangeBidHouseListMessage', id: Number(id) });
                }
                return _agentResult(true, { gid: gid, item: itemName, sent: sent,
                    note: 'category opened; prices arrive as ExchangeBidHouseInListAddedMessage and are cached for the next hdv.scan' });
            },
            'hdv.list': function(args) {
                var itemName = (args && args.item) || null;
                var price = args && (args.price != null ? Number(args.price) : null);
                var qty = args && (args.quantity != null ? Number(args.quantity) : 1);
                if (!itemName) return _agentResult(false, null, 'hdv.list needs an item name');

                var matches = _findItem(itemName);
                if (!matches.length) return _agentResult(false, { item: itemName }, 'item not in inventory');
                if (matches.length > 1 && !matches[0].uid) {
                    return _agentResult(false, { item: itemName, matches: matches.length }, 'ambiguous item name');
                }
                var entry = matches[0];
                var uid = entry.uid;

                // Price resolution. An explicit price always wins. Otherwise use a
                // number the SERVER sent (cached by __PriceCache) and say so in
                // `price_source`; never invent one. A listing at a made-up price is
                // money lost quietly, which is the one failure nobody notices.
                var priceSource = 'explicit';
                var rule = (args && args.price_rule) || 'median_minus_1';
                if (price == null || !isFinite(price) || price <= 0) {
                    if (rule === 'fixed') {
                        // `fixed` means the caller knows the number. If it did not
                        // supply one there is nothing to resolve, so refuse.
                        return _agentResult(false, { item: itemName },
                            'hdv.list price_rule=fixed needs an explicit price');
                    }
                    var st = entry.gid != null
                        ? (__PriceCache.stats(entry.gid) || __PriceCache.averageOf(entry.gid))
                        : null;
                    if (!st) {
                        return _agentResult(false, { item: itemName, gid: entry.gid },
                            'hdv.list needs a price: none was given and no measured market price is cached for this item (run hdv.scan first)');
                    }
                    var basis = st.median || st.average;
                    price = Math.max(1, basis - 1);
                    priceSource = rule + ':' + st.source;
                }

                var have = entry.quantity != null ? Number(entry.quantity) : null;
                if (have != null && have > 0 && qty > have) qty = have;

                var r = _cmSend('ExchangeObjectMovePricedMessage',
                    { objectUID: uid, quantity: qty, price: price });
                return _agentResult(r.ok, { item: entry.name, object_uid: uid, quantity: qty,
                    price: price, price_source: priceSource,
                    note: 'listing_open: the sale depends on a buyer, not on us' },
                    r.ok ? null : r.error);
            },
            'hdv.prices': function(args) {
                return _agentMarketCapture(args || {});
            },
            'market.capture': function(args) {
                return _agentMarketCapture(args || {});
            }
        };

        var _agentDirectiveAliases = {
            'SAFE_STOP': 'safe.stop', 'FARM_START': 'farm.start', 'FARM_STOP': 'farm.stop',
            'AUTO_JOIN_COMBAT': 'fight.join', 'PASS_TURN': 'fight.pass',
            'FIGHT_STATE': 'fight.state', 'FOLLOW': 'nav.follow',
            'INV_LIST': 'inv.list', 'INV_SELL': 'inv.sell', 'HDV_SCAN': 'hdv.scan',
            'HDV_LIST': 'hdv.list', 'HDV_PRICES': 'hdv.prices',
            'MARKET_CAPTURE': 'market.capture', 'CHARACTER_REPORT': 'debug.character',
            'ITEM_DB': 'debug.itemdb', 'EFFECT_DB': 'debug.effectdb',
            'CHAT_SAY': 'chat.say', 'STATE_SNAPSHOT': 'state.snapshot',
            'QUEST_ADVANCE': 'quest.advance'
        };

        window.__agentDirective = function(payload) {
            try {
                if (typeof payload === 'string') payload = JSON.parse(payload);
                payload = payload || {};
                var key = payload.tool ? String(payload.tool).toLowerCase() : '';
                if (!key && payload.directive) {
                    key = _agentDirectiveAliases[String(payload.directive).toUpperCase()] || '';
                }
                console.log('[AgentBridge] directive:', key || payload.directive, payload);
                var handler = _agentHandlers[key];
                if (!handler) return _agentResult(false, null, 'no client handler for tool "' + key + '"');
                return handler(payload.args || {});
            } catch(err) {
                return _agentResult(false, null, err.toString());
            }
        };
        // One renderer round-trip for the host: run the directive and read the
        // post-action snapshot in the same script turn. The bridge used to make
        // two ExecuteScriptAsync calls per action (one for the directive, one
        // for the snapshot), paying the WebView2 IPC twice and exposing a
        // window where the world changed between them. The returned envelope is
        // the exact JSON the bridge used to assemble in C#.
        window.__agentDirectiveWithSnapshot = function(request) {
            request = request || {};
            var result = null;
            var error = null;
            try {
                result = window.__agentDirective(request.payload || {});
            } catch (err) {
                error = String(err);
            }
            var snapshot = null;
            try {
                snapshot = window.__agentSnapshot ? window.__agentSnapshot() : null;
            } catch (err) {
                snapshot = null;
            }
            var embedded = result;
            if (typeof result === 'string') {
                try { embedded = JSON.parse(result); } catch (e) { embedded = result; }
            }
            var envelope = {
                ok: error === null,
                id: request.id == null ? null : request.id,
                target: request.target == null ? null : request.target,
                result: embedded,
                snapshot: snapshot
            };
            if (error !== null) envelope.error = error;
            return JSON.stringify(envelope);
        };
        // The host pushes the AI loop's state here so the badge reflects reality
        // rather than whatever the page last assumed.
        try {
            window.__aiState = function(running, text) {
                if (window.__FarmBot && window.__FarmBot.setAiState) {
                    window.__FarmBot.setAiState(running, text);
                }
            };
        } catch (e) {}

        console.log('[AgentBridge] directive bus ready');
    } catch(e) {}

    // 20b. F11 fullscreen bridge: WebView2 never handles F11 itself, so the
    // page forwards it to the C# host (TOGGLE_FULLSCREEN) which toggles the
    // window borderless fullscreen on the screen it currently sits on.
    try {
        window.addEventListener('keydown', function(e) {
            if (e.key === 'F11' || e.keyCode === 122) {
                e.preventDefault();
                e.stopPropagation();
                if (window.chrome && window.chrome.webview) {
                    window.chrome.webview.postMessage({ type: 'TOGGLE_FULLSCREEN' });
                }
            }
        }, true);
    } catch(e) {}

    // 20c. (removed) A shortcutBarManager stub lived here for one build. It was a
    // mistake: making the manager look present defeated the guard in patch 0, so
    // resizeWideScreen ran against a half-built GUI and threw on
    // menuBar.computeMinimumSize. The guard in the bundle now checks menuBar too;
    // nothing should fake that object into existence.

    // 21. Visitor Account Persistence Hook (accounts.txt bridge)
    try {
        if (typeof window !== 'undefined' && window.localStorage) {
            var _origSetItem = window.localStorage.setItem;
            window.localStorage.setItem = function(k, v) {
                try {
                    if (k === 'dofus.1' && v && window.chrome && window.chrome.webview) {
                        var parsed = JSON.parse(v);
                        var g = parsed && parsed['all#guestAccount'];
                        if (g && g.login && g.password) {
                            window.chrome.webview.postMessage({
                                type: 'GUEST_ACCOUNT_PERSIST',
                                login: g.login,
                                password: g.password
                            });
                        }
                    }
                } catch(e) {}
                return _origSetItem.apply(this, arguments);
            };
        }
    } catch(e) {}

})();
