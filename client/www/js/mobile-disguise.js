(function() {
    'use strict';
    if (typeof window === 'undefined') return;

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
            var applyCanvasMicroNoise = function(canvas) {
                try {
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
                aoeSpellId: 164,       // Flèche Explosive (Cra) or primary AOE
                autoReadyDelayMs: 20,   // Delay before GameFightTurnReadyMessage
                autoFinishDelayMs: 15   // Delay before GameFightTurnFinishMessage
            },
            state: {
                inFight: false,
                myTurn: false
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
                console.log('[FarmBot] Auto-fight state: ' + (this.enabled ? 'ACTIVE (Sub-20ms turns)' : 'INACTIVE (Manual play)'));
            },

            toggle: function() {
                this.setEnabled(!this.enabled);
            },

            syncToHost: function() {
                try {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage(JSON.stringify({
                            type: 'FARMBOT_STATE',
                            enabled: this.enabled
                        }));
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
                            window.chrome.webview.postMessage(JSON.stringify({
                                type: 'AI_REQUEST', action: 'start_auto'
                            }));
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
                            self.toggle();
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
                var hud = document.getElementById('__farmbot_hud');
                var label = document.getElementById('__farmbot_hud_label');
                if (!hud || !label) return;
                if (this.enabled) {
                    label.textContent = 'DofusFarm AI: ON';
                    hud.style.backgroundColor = '#10b981';
                    hud.style.color = '#ffffff';
                    hud.style.border = '1px solid #34d399';
                    hud.style.boxShadow = '0 0 12px rgba(16,185,129,0.7)';
                } else {
                    label.textContent = 'DofusFarm AI: OFF';
                    hud.style.backgroundColor = 'rgba(30, 41, 59, 0.85)';
                    hud.style.color = '#94a3b8';
                    hud.style.border = '1px solid rgba(100, 116, 139, 0.4)';
                    hud.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
                }
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

                cm.on('GameFightStartingMessage', function(msg) {
                    self.state.inFight = true;
                    self.openFight();
                });

                cm.on('GameFightPlacementPossiblePositionsMessage', function() {
                    if (!self.enabled) return;
                    setTimeout(function() {
                        self.sendPacket('GameFightOptionToggleMessage', { option: 2 });
                        self.sendPacket('GameFightTurnReadyMessage', { isReady: true });
                    }, self.config.autoReadyDelayMs);
                });

                cm.on('GameFightTurnStartMessage', function(msg) {
                    if (!self.enabled) return;
                    if (self.isUserActor(msg.id)) {
                        self.state.myTurn = true;
                        self.executeOptimalTurn(cm, msg.id);
                    }
                });

                cm.on('GameFightEndMessage', function(msg) {
                    self.state.inFight = false;
                    self.state.myTurn = false;
                    try { self.closeFight(); } catch(e) {
                        console.log('[FarmBot] closeFight failed: ' + e);
                    }
                });
            },

            executeOptimalTurn: function(cm, fighterId) {
                var self = this;
                var charData = (window.gui && window.gui.playerData && window.gui.playerData.character) || {};
                var charCell = (charData.position && charData.position.cell) || 250;
                var charAp = charData.ap || 6;
                var charMp = charData.mp || 3;
                var charHp = charData.lifePoints || 400;
                var charMaxHp = charData.maxLifePoints || 400;
                var breed = (charData.breed && charData.breed.name) || 'Cra';

                var monsters = [];
                if (window.isoEngine && window.isoEngine.actorManager) {
                    var am = window.isoEngine.actorManager;
                    var ids = (typeof am.getActorsIDList === 'function') ? am.getActorsIDList() : [];
                    for (var i = 0; i < ids.length; i++) {
                        var a = am.getActor(ids[i]);
                        if (!a || a.cellId == null) continue;
                        var fighter = null;
                        try { fighter = (typeof a.getFighter === 'function') ? a.getFighter() : null; } catch(e) {}
                        var isMonster = false;
                        try { isMonster = !!(fighter && typeof fighter.isMonster === 'function' && fighter.isMonster()); } catch(e) {}
                        if (!isMonster) continue;
                        var fData = (fighter && fighter.data) || {};
                        var fStats = fData.stats || {};
                        monsters.push({
                            id: a.actorId != null ? a.actorId : (ids[i] || -1),
                            name: fData.monsterName || fData.name || 'Monster',
                            hp: fStats.lifePoints || 500,
                            maxHp: fStats.maxLifePoints || fStats.lifePoints || 500,
                            cellId: a.cellId
                        });
                    }
                }

                var aiPayload = {
                    turn: 1,
                    player: {
                        id: fighterId,
                        breed: breed,
                        hp: charHp,
                        maxHp: charMaxHp,
                        ap: charAp,
                        mp: charMp,
                        cellId: charCell
                    },
                    monsters: monsters
                };

                // Query DofusFarm combat decision server with 750ms abort timeout
                var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
                var timeoutId = controller ? setTimeout(function() { try { controller.abort(); } catch(e){} }, 750) : null;

                fetch('http://127.0.0.1:8892/v1/combat/decide', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(aiPayload),
                    signal: controller ? controller.signal : undefined
                }).then(function(resp) {
                    if (timeoutId) clearTimeout(timeoutId);
                    if (!resp.ok) throw new Error('AI Server ' + resp.status);
                    return resp.json();
                }).then(function(data) {
                    if (data && data.status === 'ok' && Array.isArray(data.actions) && data.actions.length > 0) {
                        console.log('[FarmBot AI] Decision: Stance=' + data.stance + ' (Conf: ' + (data.confidence * 100).toFixed(1) + '%) -> ' + data.actions.length + ' actions');
                        self.dispatchActionSequence(data.actions, 0);
                    } else {
                        self.executeFallbackBurst();
                    }
                }).catch(function(err) {
                    if (timeoutId) clearTimeout(timeoutId);
                    self.executeFallbackBurst();
                });
            },

            dispatchActionSequence: function(actions, index) {
                var self = this;
                if (!this.state.myTurn) return;
                if (index >= actions.length) {
                    setTimeout(function() {
                        self.sendPacket('GameFightTurnFinishMessage', { isAfk: false });
                        self.state.myTurn = false;
                    }, this.config.autoFinishDelayMs);
                    return;
                }

                var act = actions[index];
                if (act.type === 'CAST') {
                    this.sendPacket('GameActionFightCastRequestMessage', {
                        spellId: act.spellId,
                        cellId: act.cellId
                    });
                } else if (act.type === 'MOVE') {
                    this.sendPacket('GameActionFightMovementRequestMessage', {
                        keyMovements: [act.cellId]
                    });
                }

                setTimeout(function() {
                    self.dispatchActionSequence(actions, index + 1);
                }, 20);
            },

            executeFallbackBurst: function() {
                var self = this;
                var targetCell = this.resolveOptimalTargetCell();
                this.sendPacket('GameActionFightCastRequestMessage', {
                    spellId: this.config.aoeSpellId,
                    cellId: targetCell
                });
                setTimeout(function() {
                    self.sendPacket('GameFightTurnFinishMessage', { isAfk: false });
                    self.state.myTurn = false;
                }, this.config.autoFinishDelayMs);
            },

            resolveOptimalTargetCell: function() {
                if (window.isoEngine && window.isoEngine.actorManager) {
                    var am = window.isoEngine.actorManager;
                    var ids = (typeof am.getActorsIDList === 'function') ? am.getActorsIDList() : [];
                    for (var i = 0; i < ids.length; i++) {
                        var actor = am.getActor(ids[i]);
                        if (!actor || actor.cellId == null) continue;
                        var fighter = null;
                        try { fighter = (typeof actor.getFighter === 'function') ? actor.getFighter() : null; } catch(e) {}
                        var isMonster = false;
                        try { isMonster = !!(fighter && typeof fighter.isMonster === 'function' && fighter.isMonster()); } catch(e) {}
                        if (isMonster) return actor.cellId;
                    }
                }
                return 250;
            },

            sendPacket: function(messageType, payload) {
                if (window.dofus && window.dofus.connectionManager) {
                    window.dofus.connectionManager.sendMessage(messageType, payload);
                }
            },

            isUserActor: function(id) {
                if (window.gui && window.gui.playerData && window.gui.playerData.character) {
                    return window.gui.playerData.character.id === id;
                }
                return true;
            }
        };

        FarmBot.init();
        window.__FarmBot = FarmBot;
    } catch(e) {}

    // 25b. Agent directive bus — driven by the C# AgentDirectiveBridge (127.0.0.1:8893),
    // which the Python agent (dofus-network/agent) POSTs to. window.__agentDirective
    // receives the raw envelope {id, ts, directive, tool, args, source} and returns a
    // JSON string; the host unwraps it and hands it back to the agent as the tool result,
    // so every directive lands in the trajectory recorder with a real outcome.
    try {
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
            absorb: function(name, msg) {
                if (!msg || typeof msg !== 'object') return;
                if (name === 'ObjectAveragePricesMessage') {
                    var ids = msg.ids, prices = msg.prices;
                    if (Object.prototype.toString.call(ids) !== '[object Array]') return;
                    if (Object.prototype.toString.call(prices) !== '[object Array]') return;
                    for (var i = 0; i < ids.length && i < prices.length; i++) {
                        var gid = Number(ids[i]), price = Number(prices[i]);
                        if (gid > 0 && price > 0) this.average[gid] = price;
                    }
                    this.averageAt = Date.now();
                    return;
                }
                if (name === 'ExchangeBidHouseInListAddedMessage') {
                    var g = Number(msg.objectGID);
                    if (!(g > 0)) return;
                    var p = msg.prices, q = msg.quantities;
                    if (Object.prototype.toString.call(p) !== '[object Array]') return;
                    var rows = [];
                    for (var j = 0; j < p.length; j++) {
                        var price = Number(p[j]);
                        if (!(price > 0)) continue;
                        rows.push({ uid: msg.objectUID != null ? Number(msg.objectUID) : null,
                                    price: price,
                                    quantity: q && q[j] != null ? Number(q[j]) : 1 });
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

        var _agentSnapshot = function() {
            var snap = { connected: false, in_fight: false };
            try {
                var pd = (window.gui && window.gui.playerData) || null;
                var ch = (pd && pd.character) || {};
                snap.connected = !!(window.dofus && window.dofus.connectionManager);
                snap.character = {
                    id: ch.id != null ? ch.id : null,
                    name: ch.name || null,
                    level: ch.level != null ? ch.level : null,
                    breed: (ch.breed && ch.breed.name) || null
                };
                var pos = ch.position || {};
                var mapId = pos.mapId;
                try {
                    if (mapId == null && window.isoEngine) {
                        mapId = window.isoEngine.currentMapId || (window.isoEngine.mapData && window.isoEngine.mapData.mapId);
                    }
                } catch(e){}
                snap.position = {
                    cell: pos.cell != null ? pos.cell : null,
                    x: pos.x != null ? pos.x : null,
                    y: pos.y != null ? pos.y : null,
                    map_id: mapId != null ? mapId : null
                };
                snap.hp = {
                    life: ch.lifePoints != null ? ch.lifePoints : null,
                    max: ch.maxLifePoints != null ? ch.maxLifePoints : null,
                    ap: ch.ap != null ? ch.ap : null,
                    mp: ch.mp != null ? ch.mp : null
                };
                // XP is what makes a farm measurable: it is the only quantity the
                // client reports on every kill, and a fight that yields 0 xp is a
                // fight that was lost or abandoned. Read several candidate keys
                // because the field name differs across client builds.
                snap.xp = (ch.experience != null ? ch.experience
                          : (ch.xp != null ? ch.xp
                          : (ch.experiencePoints != null ? ch.experiencePoints : null)));
                snap.xp_level = (ch.experienceLevel != null ? ch.experienceLevel : (ch.level != null ? ch.level : null));
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
                if (pd && pd.inventory) {
                    var inv = pd.inventory;
                    snap.inventory = { kamas: (inv.kamas != null ? inv.kamas : null), items: [], raw_keys: null };
                    var items = inv.objects || inv.items || null;
                    if (Array.isArray(items)) {
                        for (var i = 0; i < items.length; i++) {
                            var it = items[i] || {};
                            snap.inventory.items.push({
                                gid: it.objectGID != null ? it.objectGID : (it.gid != null ? it.gid : null),
                                qty: it.quantity != null ? it.quantity : (it.qty != null ? it.qty : 1)
                            });
                        }
                    } else if (items && typeof items === 'object') {
                        for (var gid in items) {
                            if (Object.prototype.hasOwnProperty.call(items, gid)) {
                                snap.inventory.items.push({ gid: parseInt(gid, 10) || gid, qty: items[gid] });
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
                try {
                    if (window.__FarmBot) {
                        window.__FarmBot.resetSession();
                        window.__FarmBot.setEnabled(true);
                        started = !!window.__FarmBot.enabled;
                        s = window.__FarmBot.sessionSummary();
                    }
                } catch(e){}
                // Harvesting is `InteractiveUseRequestMessage {elemId,
                // skillInstanceUid}` -- captured 2026-10-22 while gathering. If the
                // agent asked for a resource, take the nearest interactive element
                // on this map; walking to it is still the client's job.
                if (started && args && (args.resource || args.monster)) {
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
            'fight.join': function() { return _agentResult(true, _agentCommander('AUTO_JOIN_COMBAT')); },
            'fight.pass': function() { return _agentResult(true, _agentCommander('PASS_TURN')); },
            'nav.follow': function(args) {
                return _agentResult(true, _agentCommander('FOLLOW'), null);
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
                // Two ways to advance, both captured on 2026-10-22:
                //   explicit objective -> QuestObjectiveValidationMessage
                //   otherwise          -> talk to the NPC (npcActionId 3) and let
                //                         the dialog drive the step.
                var questId = (args && (args.quest_id || args.questId)) || null;
                var objectiveId = (args && (args.objective_id || args.objectiveId)) || null;
                if (questId && objectiveId) {
                    var r = _cmSend('QuestObjectiveValidationMessage',
                        { questId: Number(questId), objectiveId: Number(objectiveId) });
                    return _agentResult(r.ok, { quest_id: Number(questId), objective_id: Number(objectiveId) },
                        r.ok ? null : r.error);
                }
                var npc = null;
                try {
                    var am = window.isoEngine && window.isoEngine.actorManager;
                    var actors = (am && (am.actors || am.actorList)) || null;
                    if (actors) {
                        for (var k in actors) {
                            if (!Object.prototype.hasOwnProperty.call(actors, k)) continue;
                            var a = actors[k] || {};
                            var nid = a.npcId != null ? a.npcId : (a.staticInfos && a.staticInfos.npcId);
                            if (nid != null && nid > 0) { npc = { npcId: nid, cell: a.cellId || (a.position && a.position.cell) }; break; }
                        }
                    }
                } catch (e) {}
                if (!npc) {
                    return _agentResult(false, { quest_id: questId },
                        'no NPC on this map to talk to; pass objective_id to validate directly');
                }
                var mapId = null;
                try { mapId = window.isoEngine.currentMapId || (window.isoEngine.mapData && window.isoEngine.mapData.mapId) || null; } catch (e) {}
                var talk = _cmSend('NpcGenericActionRequestMessage',
                    { npcId: npc.npcId, npcActionId: 3, npcMapId: mapId });
                return _agentResult(talk.ok, { quest_id: questId, npc_id: npc.npcId, map_id: mapId },
                    talk.ok ? null : talk.error);
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
            }
        };

        var _agentDirectiveAliases = {
            'SAFE_STOP': 'safe.stop', 'FARM_START': 'farm.start', 'FARM_STOP': 'farm.stop',
            'AUTO_JOIN_COMBAT': 'fight.join', 'PASS_TURN': 'fight.pass', 'FOLLOW': 'nav.follow',
            'INV_LIST': 'inv.list', 'INV_SELL': 'inv.sell', 'HDV_SCAN': 'hdv.scan',
            'HDV_LIST': 'hdv.list', 'CHAT_SAY': 'chat.say', 'STATE_SNAPSHOT': 'state.snapshot',
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

    // 20c. shortcutBarManager boot guard.
    //     Some client builds read gui.shortcutBarManager.shortcutBars during boot
    //     and on resize, before the manager exists -- 18 uncaught TypeErrors in one
    //     session ("Cannot read properties of undefined (reading 'shortcutBars')").
    //     A stub absorbs those reads; the real manager replaces it on assignment.
    try {
        var _sbmDone = false;
        var _installSbmGuard = function() {
            try {
                var g = window.gui;
                if (!g || _sbmDone) return;
                var existing = Object.getOwnPropertyDescriptor(g, 'shortcutBarManager');
                if (existing && (existing.get || existing.value)) { _sbmDone = true; return; }

                var _bar = new Proxy(function() {}, {
                    get: function(t, k) { return k === 'then' ? undefined : _bar; },
                    apply: function() { return undefined; }
                });
                var _mgr = new Proxy(function() {}, {
                    get: function(t, k) {
                        if (k === 'then') return undefined;
                        if (k === 'shortcutBars') return { playerBar: _bar, heroBar: _bar };
                        return _mgr;
                    },
                    apply: function() { return undefined; }
                });

                Object.defineProperty(g, 'shortcutBarManager', {
                    configurable: true,
                    get: function() { return _mgr; },
                    set: function(v) {
                        try {
                            Object.defineProperty(g, 'shortcutBarManager',
                                { value: v, writable: true, configurable: true });
                        } catch (e) {}
                    }
                });
                _sbmDone = true;
            } catch (e) {}
        };
        var _sbmTimer = setInterval(function() {
            _installSbmGuard();
            if (_sbmDone) { try { clearInterval(_sbmTimer); } catch (e) {} }
        }, 40);
        setTimeout(function() { try { clearInterval(_sbmTimer); } catch (e) {} }, 30000);
    } catch (e) {}

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
