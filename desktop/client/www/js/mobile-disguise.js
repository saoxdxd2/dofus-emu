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

    // 2. Suppress transient proxy/network alert popups
    function isTransientNetMsg(msg) { return typeof msg === 'string' && /server cannot be reached|check your internet/i.test(msg); }
    try {
        var winAlertProto = (typeof Window !== 'undefined' && Window.prototype) ? Window.prototype : window;
        var originalAlert = winAlertProto.alert || window.alert;
        var wrappedAlert = makeNative(function alert(msg) { if (!isTransientNetMsg(msg) && originalAlert) return originalAlert.apply(this, arguments); }, 'alert');
        if (typeof Window !== 'undefined' && Window.prototype && Window.prototype.alert) Window.prototype.alert = wrappedAlert;
        else window.alert = wrappedAlert;
        document.addEventListener('deviceready', function() {
            if (navigator.notification && navigator.notification.alert) {
                var origCdvAlert = navigator.notification.alert;
                navigator.notification.alert = makeNative(function alert(msg, cb) {
                    if (isTransientNetMsg(msg)) { if (typeof cb === 'function') cb(); return; }
                    return origCdvAlert.apply(this, arguments);
                }, 'alert');
            }
        }, false);
    } catch (e) {}

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

    var _appVersion = (window.appInfo && window.appInfo.version) || cfg('__DEVICE_APP_VERSION__', '3.14.2');
    var _versionCode = (window.appInfo && window.appInfo.versionCode) || cfg('__DEVICE_VERSION_CODE__', '3014002');
    var _buildVersion = window.buildVersion || cfg('__DEVICE_BUILD_VERSION__', '1.74.8');

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
        window.appInfo = window.appInfo || { build: _appVersion, version: _appVersion, identifier: 'com.ankama.dofustouch', versionCode: _versionCode };
        if (!window.appInfo.version) window.appInfo.version = _appVersion;
        if (!window.appInfo.build) window.appInfo.build = _appVersion;
        if (!window.appInfo.versionCode) window.appInfo.versionCode = _versionCode;
        if (!window.buildVersion) window.buildVersion = _buildVersion;
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

        // Universal Mouse-to-Touch Bridge: translates desktop mouse drag and slide into native TouchEvents
        try {
            var isMouseDown = false, touchIdSeq = 1, currentTarget = null;
            function makeFakeTouch(e, target) {
                var radius = 14;
                var touchProps = {
                    identifier: touchIdSeq,
                    target: target || e.target,
                    screenX: e.screenX,
                    screenY: e.screenY,
                    clientX: e.clientX,
                    clientY: e.clientY,
                    pageX: e.pageX,
                    pageY: e.pageY,
                    radiusX: radius,
                    radiusY: radius,
                    rotationAngle: 0,
                    force: 1
                };
                if (typeof Touch === 'function') {
                    try { return new Touch(touchProps); } catch (err) {}
                }
                return touchProps;
            }

            function dispatchTouchEvent(type, mouseEvt, touches, targetTouches, changedTouches, target) {
                var touchEvt;
                var eventInit = {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    touches: touches,
                    targetTouches: targetTouches,
                    changedTouches: changedTouches,
                    shiftKey: mouseEvt.shiftKey,
                    altKey: mouseEvt.altKey,
                    ctrlKey: mouseEvt.ctrlKey,
                    metaKey: mouseEvt.metaKey
                };
                if (typeof TouchEvent === 'function') {
                    try {
                        touchEvt = new TouchEvent(type, eventInit);
                    } catch (err) {}
                }
                if (!touchEvt) {
                    touchEvt = document.createEvent('UIEvent');
                    touchEvt.initUIEvent(type, true, true, window, 1);
                    for (var k in eventInit) {
                        try { Object.defineProperty(touchEvt, k, { value: eventInit[k] }); } catch (ex) {}
                    }
                }
                (target || mouseEvt.target).dispatchEvent(touchEvt);
            }

            window.addEventListener('mousedown', function(e) {
                if (e.which !== 1) return;
                isMouseDown = true;
                currentTarget = e.target;
                touchIdSeq++;
                var t = makeFakeTouch(e, currentTarget);
                dispatchTouchEvent('touchstart', e, [t], [t], [t], currentTarget);
            }, true);

            window.addEventListener('mousemove', function(e) {
                if (!isMouseDown || !currentTarget) return;
                var t = makeFakeTouch(e, currentTarget);
                dispatchTouchEvent('touchmove', e, [t], [t], [t], currentTarget);
            }, true);

            window.addEventListener('mouseup', function(e) {
                if (!isMouseDown || !currentTarget) return;
                var t = makeFakeTouch(e, currentTarget);
                dispatchTouchEvent('touchend', e, [], [], [t], currentTarget);
                isMouseDown = false;
                currentTarget = null;
            }, true);
        } catch (e) {}
    } catch (e) {}

    // 7 & 10. Sensors + Full WebRTC SDP & ICE Candidate QEMU 10.0.2.15 Scrubbing
    var PeerConn = window.RTCPeerConnection || window.webkitRTCPeerConnection;
    var iceListenerMap = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
    function scrubCandidate(c) {
        if (!c) return c;
        if (c.candidate && c.candidate.indexOf('10.0.2.15') !== -1) c.candidate = c.candidate.replace(/10\.0\.2\.15/g, _fakeLocalIp);
        if (c.address === '10.0.2.15') { try { Object.defineProperty(c, 'address', { value: _fakeLocalIp, configurable: true, enumerable: true }); } catch (e) {} }
        return c;
    }

    try {
        var motionListeners = [], orientationListeners = [], onDeviceMotionHandler = null, onDeviceOrientationHandler = null, _sensorTimerId = null;
        function makeSensorEvent(Ctor, type, props) {
            var evt = null;
            if (typeof Ctor === 'function') { try { evt = new Ctor(type, props); } catch (e) {} }
            if (!evt) evt = new Event(type);
            try { for (var k in props) Object.defineProperty(evt, k, { value: props[k], configurable: true, enumerable: true }); } catch (e) {}
            return evt;
        }
        function dispatchSensor(list, handler, evt) {
            for (var i = 0; i < list.length; i++) { try { list[i].call(window, evt); } catch (e) {} }
            if (typeof handler === 'function') { try { handler.call(window, evt); } catch (e) {} }
        }
        function tickSensors() {
            var hasM = motionListeners.length > 0 || typeof onDeviceMotionHandler === 'function';
            var hasO = orientationListeners.length > 0 || typeof onDeviceOrientationHandler === 'function';
            if (!hasM && !hasO) { if (_sensorTimerId !== null) { clearInterval(_sensorTimerId); _sensorTimerId = null; } return; }
            if (hasM) {
                dispatchSensor(motionListeners, onDeviceMotionHandler, makeSensorEvent(typeof DeviceMotionEvent !== 'undefined' ? DeviceMotionEvent : undefined, 'devicemotion', {
                    acceleration: { x: (Math.random() - 0.5) * 0.02, y: (Math.random() - 0.5) * 0.02, z: (Math.random() - 0.5) * 0.02 },
                    accelerationIncludingGravity: { x: 0.08 + (Math.random() - 0.5) * 0.04, y: 9.80 + (Math.random() - 0.5) * 0.06, z: 0.42 + (Math.random() - 0.5) * 0.04 },
                    rotationRate: { alpha: (Math.random() - 0.5) * 0.1, beta: (Math.random() - 0.5) * 0.1, gamma: (Math.random() - 0.5) * 0.1 }, interval: 50
                }));
            }
            if (hasO) {
                dispatchSensor(orientationListeners, onDeviceOrientationHandler, makeSensorEvent(typeof DeviceOrientationEvent !== 'undefined' ? DeviceOrientationEvent : undefined, 'deviceorientation', {
                    alpha: +(180.2 + (Math.random() - 0.5) * 0.2).toFixed(2), beta: +(45.1 + (Math.random() - 0.5) * 0.2).toFixed(2), gamma: +((Math.random() - 0.5) * 0.2).toFixed(2), absolute: true
                }));
            }
        }
        function ensureSensorTimer() { if (_sensorTimerId === null) _sensorTimerId = setInterval(tickSensors, 50); }

        var targetProto = (typeof EventTarget !== 'undefined' && EventTarget.prototype) ? EventTarget.prototype : window;
        var origAddEL = targetProto.addEventListener || window.addEventListener, origRemEL = targetProto.removeEventListener || window.removeEventListener;
        targetProto.addEventListener = makeNative(function addEventListener(type, listener, options) {
            if (typeof listener === 'function') {
                if (this === window || (typeof Window !== 'undefined' && this instanceof Window)) {
                    var arr = type === 'devicemotion' ? motionListeners : (type === 'deviceorientation' ? orientationListeners : null);
                    if (arr && arr.indexOf(listener) === -1) { arr.push(listener); ensureSensorTimer(); }
                } else if (PeerConn && this instanceof PeerConn && type === 'icecandidate' && iceListenerMap) {
                    var wrapped = iceListenerMap.get(listener);
                    if (!wrapped) { wrapped = function(evt) { if (evt && evt.candidate) scrubCandidate(evt.candidate); return listener.apply(this, arguments); }; iceListenerMap.set(listener, wrapped); }
                    return origAddEL.call(this, type, wrapped, options);
                }
            }
            if (origAddEL) return origAddEL.apply(this, arguments);
        }, 'addEventListener');
        targetProto.removeEventListener = makeNative(function removeEventListener(type, listener, options) {
            if (listener) {
                if (this === window || (typeof Window !== 'undefined' && this instanceof Window)) {
                    var arr = type === 'devicemotion' ? motionListeners : (type === 'deviceorientation' ? orientationListeners : null);
                    if (arr) { var idx = arr.indexOf(listener); if (idx !== -1) arr.splice(idx, 1); }
                } else if (PeerConn && this instanceof PeerConn && type === 'icecandidate' && iceListenerMap && iceListenerMap.has(listener)) {
                    return origRemEL.call(this, type, iceListenerMap.get(listener), options);
                }
            }
            if (origRemEL) return origRemEL.apply(this, arguments);
        }, 'removeEventListener');

        var winProto = (typeof Window !== 'undefined' && Window.prototype) ? Window.prototype : window;
        Object.defineProperty(winProto, 'ondevicemotion', { get: makeNative(function() { return onDeviceMotionHandler; }, 'get ondevicemotion'), set: makeNative(function(fn) { onDeviceMotionHandler = fn; if (typeof fn === 'function') ensureSensorTimer(); }, 'set ondevicemotion'), configurable: true, enumerable: true });
        Object.defineProperty(winProto, 'ondeviceorientation', { get: makeNative(function() { return onDeviceOrientationHandler; }, 'get ondeviceorientation'), set: makeNative(function(fn) { onDeviceOrientationHandler = fn; if (typeof fn === 'function') ensureSensorTimer(); }, 'set ondeviceorientation'), configurable: true, enumerable: true });
    } catch (e) {}

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

    // 12. Outbound HTTPS XHR & Fetch Header Sanitizer
    try {
        if (window.XMLHttpRequest && XMLHttpRequest.prototype) {
            var origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
            if (origSetHeader) {
                var hdrMap = { 'user-agent': MOBILE_UA, 'sec-ch-ua-arch': '"arm"', 'sec-ch-ua-platform': '"Android"', 'sec-ch-ua-model': '"' + _deviceModel + '"' };
                XMLHttpRequest.prototype.setRequestHeader = makeNative(function setRequestHeader(h, v) {
                    var hL = (h || '').toLowerCase();
                    if (Object.prototype.hasOwnProperty.call(hdrMap, hL)) v = hdrMap[hL];
                    return origSetHeader.call(this, h, v);
                }, 'setRequestHeader');
            }
        }
    } catch (e) {}

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
    try {
        var baseInputJitter = ((_instanceHash % 11) + 1); // 1..11ms deterministic instance base
        if (typeof Event !== 'undefined' && Event.prototype) {
            var origTimeStampDesc = Object.getOwnPropertyDescriptor(Event.prototype, 'timeStamp');
            if (origTimeStampDesc && origTimeStampDesc.get) {
                var origGetTs = origTimeStampDesc.get;
                var jitteredEvents = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
                Object.defineProperty(Event.prototype, 'timeStamp', {
                    get: makeNative(function() {
                        var raw = origGetTs.call(this);
                        if (jitteredEvents && (this.type && /touch|pointer|mouse|click/i.test(this.type))) {
                            var cached = jitteredEvents.get(this);
                            if (cached !== undefined) return cached;
                            // Add 15..25ms (mean ~20ms) subtle desynchronization
                            var j = raw + baseInputJitter + (Math.random() * 10 - 5) + 10;
                            jitteredEvents.set(this, j);
                            return j;
                        }
                        return raw;
                    }, 'get timeStamp'),
                    configurable: true,
                    enumerable: true
                });
            }
        }
    } catch (e) {}

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

        // A. WebSocket packet hook for chat streams
        if (typeof window.WebSocket !== 'undefined') {
            var origWs = window.WebSocket;
            window.WebSocket = function(url, protocols) {
                var ws = protocols ? new origWs(url, protocols) : new origWs(url);
                try {
                    ws.addEventListener('message', function(ev) {
                        try {
                            var strData = '';
                            if (typeof ev.data === 'string') {
                                strData = ev.data;
                            } else if (ev.data instanceof ArrayBuffer && typeof TextDecoder !== 'undefined') {
                                strData = new TextDecoder().decode(ev.data);
                            }
                            if (strData) {
                                var isMod = /\[(?:Mod|Admin|GM|Ankama)\]/i.test(strData) || /(?:^|\W)(?:modérateur|moderator)(?:\W|$)/i.test(strData);
                                var isWhisper = /channel["']?\s*:\s*9/i.test(strData) || /ChatServerMessage/i.test(strData) || /whisper|chuchote/i.test(strData);
                                if (isMod || isWhisper) {
                                    var senderMatch = strData.match(/["'](?:senderName|sender|name)["']\s*:\s*["']([^"']+)["']/i) ||
                                                      strData.match(/\[([a-zA-Z0-9_\-\s]{3,24})\]/);
                                    var textMatch = strData.match(/["'](?:content|text|message)["']\s*:\s*["']([^"']+)["']/i);
                                    var sender = senderMatch ? senderMatch[1] : (isMod ? 'Moderator' : 'Player');
                                    var text = textMatch ? textMatch[1] : strData.substring(0, 100);
                                    triggerWhisperAlert(sender, text, isMod, isWhisper ? 'whisper' : 'chat');
                                }
                            }
                        } catch (err) {}
                    });
                } catch (err) {}
                return ws;
            };
            window.WebSocket.prototype = origWs.prototype;
            window.WebSocket.CONNECTING = origWs.CONNECTING;
            window.WebSocket.OPEN = origWs.OPEN;
            window.WebSocket.CLOSING = origWs.CLOSING;
            window.WebSocket.CLOSED = origWs.CLOSED;
        }

        // B. DOM MutationObserver hook for chat UI rendering
        if (typeof MutationObserver !== 'undefined') {
            var chatObserver = new MutationObserver(function(mutations) {
                for (var i = 0; i < mutations.length; i++) {
                    var added = mutations[i].addedNodes;
                    if (!added) continue;
                    for (var j = 0; j < added.length; j++) {
                        var node = added[j];
                        if (node.nodeType === 1) { // Element node
                            var txt = node.textContent || '';
                            if (txt.length > 2) {
                                var isMod = /\[(?:Mod|Admin|GM|Ankama)\]/i.test(txt) || /(?:^|\W)(?:modérateur|moderator)(?:\W|$)/i.test(txt);
                                var cls = (node.className || '') + ' ' + (node.getAttribute && node.getAttribute('data-channel') || '');
                                var isWhisper = /whisper|chuchot/i.test(cls) || /\[(?:de|from)\s+([a-zA-Z0-9_\-]+)\]/i.test(txt);
                                if (isMod || isWhisper) {
                                    var sender = isMod ? 'Moderator' : 'Player';
                                    var m = txt.match(/\[(?:de|from)\s+([a-zA-Z0-9_\-]+)\]/i) || txt.match(/\[([a-zA-Z0-9_\-\s]{3,24})\]/);
                                    if (m) sender = m[1];
                                    triggerWhisperAlert(sender, txt.trim().substring(0, 120), isMod, isWhisper ? 'whisper' : 'chat');
                                }
                            }
                        }
                    }
                }
            });

            var attachObserver = function() {
                var target = document.body || document.documentElement;
                if (target) {
                    chatObserver.observe(target, { childList: true, subtree: true });
                }
            };

            if (document.readyState === 'complete' || document.readyState === 'interactive') {
                attachObserver();
            } else {
                document.addEventListener('DOMContentLoaded', attachObserver, { once: true });
            }
        }
    } catch (e) {}

    // 18. Spatial & Temporal Input Desynchronizer (Anti-Conga Line & Natural Jitter)
    try {
        var dx = ((_instanceHash % 7) - 3);
        var dy = (((_instanceHash >>> 4) % 7) - 3);

        if (typeof MouseEvent !== 'undefined' && MouseEvent.prototype) {
            var jitterCoordProp = function(propName, deltaBase) {
                var desc = Object.getOwnPropertyDescriptor(MouseEvent.prototype, propName);
                if (desc && desc.get) {
                    var origGet = desc.get;
                    var jitterMap = new WeakMap();
                    Object.defineProperty(MouseEvent.prototype, propName, {
                        get: makeNative(function() {
                            var val = origGet.call(this);
                            if (typeof val === 'number') {
                                var cached = jitterMap.get(this);
                                if (cached !== undefined) return cached;
                                // Subtle Gaussian spatial jitter: +- 2..4 px
                                var offset = deltaBase + (Math.random() * 2 - 1);
                                var newVal = Math.round(val + offset);
                                jitterMap.set(this, newVal);
                                return newVal;
                            }
                            return val;
                        }, 'get ' + propName),
                        configurable: true,
                        enumerable: true
                    });
                }
            };

            jitterCoordProp('clientX', dx);
            jitterCoordProp('pageX', dx);
            jitterCoordProp('screenX', dx);
            jitterCoordProp('clientY', dy);
            jitterCoordProp('pageY', dy);
            jitterCoordProp('screenY', dy);
        }
    } catch (e) {}

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

    // 17b. Comprehensive Runtime Diagnostics Probe
    try {
        var _diagStartTime = Date.now();
        var _diagImageFails = [];
        var _diagFetchFails = [];
        var _diagXhrFails = [];
        var _diagImageLoads = 0;
        var _diagImageErrors = 0;
        var _diagProbeCount = 0;

        // Patch Image constructor to track load failures
        var _OrigImage = window.Image;
        window.Image = function(w, h) {
            var img = (w !== undefined && h !== undefined) ? new _OrigImage(w, h) : (w !== undefined ? new _OrigImage(w) : new _OrigImage());
            img.addEventListener('load', function() { _diagImageLoads++; });
            img.addEventListener('error', function() {
                _diagImageErrors++;
                var failSrc = (img.src || '').substring(0, 200);
                if (_diagImageFails.length < 20) _diagImageFails.push(failSrc);
                console.error('[DIAG-IMG-FAIL] ' + failSrc + ' crossOrigin=' + img.crossOrigin);
            });
            return img;
        };
        window.Image.prototype = _OrigImage.prototype;
        try { Object.defineProperty(window.Image, 'name', { value: 'Image' }); } catch(e){}

        // Patch fetch to track failures
        if (window.fetch) {
            var _origFetch = window.fetch;
            window.fetch = function() {
                var url = (arguments[0] && typeof arguments[0] === 'string') ? arguments[0] : (arguments[0] && arguments[0].url ? arguments[0].url : '');
                return _origFetch.apply(this, arguments).then(function(resp) {
                    if (!resp.ok && _diagFetchFails.length < 20) {
                        _diagFetchFails.push(resp.status + ' ' + url.substring(0, 150));
                        console.error('[DIAG-FETCH-FAIL] ' + resp.status + ' ' + url.substring(0, 150));
                    }
                    return resp;
                }).catch(function(err) {
                    if (_diagFetchFails.length < 20) {
                        _diagFetchFails.push('ERR ' + url.substring(0, 150) + ': ' + err);
                        console.error('[DIAG-FETCH-ERR] ' + url.substring(0, 150) + ': ' + err);
                    }
                    throw err;
                });
            };
        }

        // Patch XHR open/send to track failures
        if (window.XMLHttpRequest) {
            var _origXhrOpen = XMLHttpRequest.prototype.open;
            var _origXhrSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__diagUrl = url;
                return _origXhrOpen.apply(this, arguments);
            };
            var _origXhrSendPatched = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function() {
                var xhr = this;
                var url = xhr.__diagUrl || '';
                xhr.addEventListener('error', function() {
                    if (_diagXhrFails.length < 20) {
                        _diagXhrFails.push('NETERR ' + url.substring(0, 150));
                        console.error('[DIAG-XHR-ERR] Network error: ' + url.substring(0, 150));
                    }
                });
                xhr.addEventListener('load', function() {
                    if (xhr.status >= 400 && _diagXhrFails.length < 20) {
                        _diagXhrFails.push(xhr.status + ' ' + url.substring(0, 150));
                        console.error('[DIAG-XHR-FAIL] ' + xhr.status + ' ' + url.substring(0, 150));
                    }
                });
                return _origXhrSendPatched.apply(this, arguments);
            };
        }

        // Periodic state probe - runs every 3s for first 90s
        var _diagInterval = setInterval(function() {
            _diagProbeCount++;
            var elapsed = ((Date.now() - _diagStartTime) / 1000).toFixed(1);
            var state = {
                t: elapsed + 's',
                hasConfig: !!window.Config,
                hasGui: !!window.gui,
                hasIsoEngine: !!window.isoEngine,
                hasDofus: !!window.dofus,
                imgOk: _diagImageLoads,
                imgFail: _diagImageErrors
            };

            if (window.Config) {
                state.assetsUrl = (window.Config.assetsUrl || '').substring(0, 80);
                state.uiUrl = (window.Config.uiUrl || '').substring(0, 80);
                state.imgCrossOrigin = window.Config.imgCrossOrigin;
                state.language = window.Config.language;
            }

            if (window.isoEngine) {
                var ie = window.isoEngine;
                state.mapLoading = ie._isMapLoading;
                state.fadeIn = ie._loadingFadeInTransitionRunning;
                state.waitMsg = ie._waitingForMessage;
                if (ie.mapRenderer) {
                    state.mapId = ie.mapRenderer.mapId;
                    state.mapReady = ie.mapRenderer.isReady;
                    state.hasMap = !!ie.mapRenderer.map;
                }
                if (ie._loadingProgress && ie._loadingProgress.progressEgg) {
                    state.eggAlpha = ie._loadingProgress.progressEgg.alpha;
                }
                if (ie.mapScene) {
                    state.sceneW = ie.mapScene.w;
                    state.sceneH = ie.mapScene.h;
                }
            }

            if (window.gui) {
                state.guiOpen = !!window.gui.isConnected;
                state.guiNbChars = (window.gui.playerData && window.gui.playerData.characters) ?
                    Object.keys(window.gui.playerData.characters).length : 0;
            }

            // Check WebGL context
            try {
                var canvases = document.querySelectorAll('canvas');
                state.canvasCount = canvases.length;
                if (canvases.length > 0) {
                    var mainCanvas = canvases[0];
                    state.canvasSize = mainCanvas.width + 'x' + mainCanvas.height;
                    var glCtx = mainCanvas.getContext('webgl') || mainCanvas.getContext('webgl2') || mainCanvas.getContext('experimental-webgl');
                    state.glContextLost = glCtx ? glCtx.isContextLost() : 'no-ctx';
                }
            } catch(e) { state.canvasErr = e.message; }

            console.warn('[DIAG-PROBE #' + _diagProbeCount + '] ' + JSON.stringify(state));

            if (_diagImageFails.length > 0 && _diagProbeCount <= 5) {
                console.warn('[DIAG-IMG-FAILS] ' + JSON.stringify(_diagImageFails.slice(0, 10)));
            }
            if (_diagFetchFails.length > 0 && _diagProbeCount <= 5) {
                console.warn('[DIAG-FETCH-FAILS] ' + JSON.stringify(_diagFetchFails.slice(0, 10)));
            }
            if (_diagXhrFails.length > 0 && _diagProbeCount <= 5) {
                console.warn('[DIAG-XHR-FAILS] ' + JSON.stringify(_diagXhrFails.slice(0, 10)));
            }

            // Stop probing after 90 seconds
            if (Date.now() - _diagStartTime > 90000) {
                console.warn('[DIAG] Probe period ended. Final: imgOk=' + _diagImageLoads + ' imgFail=' + _diagImageErrors + ' fetchFail=' + _diagFetchFails.length + ' xhrFail=' + _diagXhrFails.length);
                clearInterval(_diagInterval);
            }
        }, 3000);

        console.warn('[DIAG] Runtime diagnostics probe initialized. Will report every 3s for 90s.');
    } catch (e) {
        try { console.error('[DIAG] Failed to initialize diagnostics: ' + e); } catch(e2) {}
    }

    // 18. Map Loading Transition & Black Screen Safeguard Watchdog
    try {
        var _mapStallStart = 0;
        setInterval(function() {
            try {
                if (window.isoEngine) {
                    var ie = window.isoEngine;
                    var eggAlpha = (ie._loadingProgress && ie._loadingProgress.progressEgg) ? ie._loadingProgress.progressEgg.alpha : 0;
                    var isTransitionActive = ie._isMapLoading || ie._loadingFadeInTransitionRunning || (eggAlpha > 0.1);
                    if (isTransitionActive) {
                        if (!_mapStallStart) {
                            _mapStallStart = Date.now();
                        } else if (Date.now() - _mapStallStart > 4000) {
                            console.warn('[MapWatchdog] Map transition exceeded 4s safeguard. Forcing display fade-out and scene refresh...');
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
                            ie._isMapLoading = false;
                            ie._loadingFadeInTransitionRunning = false;
                        }
                    } else {
                        _mapStallStart = 0;
                    }
                }
            } catch(e) {}
        }, 800);
    } catch (e) {}
})();
