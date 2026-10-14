(function() {
    'use strict';
    if (typeof window === 'undefined') return;

    var nativeToString = Function.prototype.toString;
    try {
        if (Function.prototype.toString.toString() === 'function toString() { [native code] }' &&
            Navigator.prototype.vibrate && Navigator.prototype.vibrate.toString() === 'function vibrate() { [native code] }' &&
            window.matchMedia && window.matchMedia('(pointer: coarse)').matches === true &&
            typeof window.matchMedia('(pointer: coarse)').onchange === 'object') {
            return;
        }
    } catch (e) {}

    var hookedFunctions = new WeakMap();
    var disguisedFrames = new WeakSet();

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

    var MOBILE_UA = (function() {
        var ua = '__DEVICE_UA__';
        return (ua === '__' + 'DEVICE_UA__')
            ? 'Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36'
            : ua;
    })();
    var MOBILE_APP_VERSION = MOBILE_UA.replace(/^Mozilla\//, '');
    var MOBILE_PLATFORM = 'Linux armv8l';

    var _deviceModel = (function() {
        var m = '__DEVICE_MODEL__';
        return (m === '__' + 'DEVICE_MODEL__') ? 'SM-A515F' : m;
    })();
    var _deviceManufacturer = (function() {
        var m = '__DEVICE_MANUFACTURER__';
        return (m === '__' + 'DEVICE_MANUFACTURER__') ? 'samsung' : m;
    })();
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

    var _osVerMatch = MOBILE_UA.match(/Android\s+([0-9.]+)/);
    var _chromeVerMatch = MOBILE_UA.match(/Chrome\/([0-9.]+)/);
    var _majorVer = _chromeVerMatch ? _chromeVerMatch[1].split('.')[0] : '83';
    var _fullVer = _chromeVerMatch ? _chromeVerMatch[1] : '83.0.4103.106';
    var _osVer = _osVerMatch ? _osVerMatch[1] : '10';

    var _deviceUuid = (function() {
        var configured = '__DEVICE_UUID__';
        if (configured && configured.indexOf('__') !== 0) return configured;
        if (window.device && window.device.uuid) return window.device.uuid;
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
        if (configured && configured.indexOf('__') !== 0) return configured;
        if (window.device && window.device.serial) return window.device.serial;
        try {
            var hostPort = (typeof location !== 'undefined' && location.port) ? parseInt(location.port, 10) : 0;
            if (hostPort > 0) {
                var hex = ((hostPort * 1103515245) >>> 0).toString(16).toUpperCase();
                return 'R58M' + hex.padStart(8, '0');
            }
        } catch (e) {}
        return 'R58M45F41EFD';
    })();

    // Deterministic per-instance hash seeded from unique Serial + AndroidId
    var _instanceHash = (function() {
        var seed = (_deviceSerial || 'R58M45F41EFD') + ':' + (_deviceUuid || 'ca8ea76b0fe32f85') + ':' + _deviceModel;
        var hash = 2166136261;
        for (var i = 0; i < seed.length; i++) {
            hash ^= seed.charCodeAt(i);
            hash = Math.imul(hash, 16777619);
        }
        return (hash >>> 0);
    })();

    var _fakeLocalIp = (function() {
        var o2 = 64 + (_instanceHash % 64);         // 100.64.0.0/10 RFC 6598 CGNAT
        var o3 = ((_instanceHash >>> 6) % 254) + 1;
        var o4 = ((_instanceHash >>> 14) % 253) + 2;
        return '100.' + o2 + '.' + o3 + '.' + o4;
    })();

    var _hwSpecs = (function() {
        var m = (_deviceModel || '').toUpperCase();
        if (m.indexOf('SM-G998') !== -1 || m.indexOf('PIXEL 6') !== -1 || m.indexOf('LE2123') !== -1 || m.indexOf('XQ-BC52') !== -1 || m.indexOf('I005D') !== -1) {
            return { cores: 8, ram: 8 };
        }
        if (m.indexOf('SM-T510') !== -1) {
            return { cores: 8, ram: 3 };
        }
        if (m.indexOf('PIXEL 4A') !== -1 || m.indexOf('SM-T860') !== -1 || m.indexOf('M2007') !== -1 || m.indexOf('JOYEUSE') !== -1 || m.indexOf('NOTE 9') !== -1) {
            return { cores: 8, ram: 6 };
        }
        return { cores: 8, ram: 4 };
    })();

    var _VIEWPORT_DIMS = new Int32Array([MAX_TEX_SIZE, MAX_TEX_SIZE]);
    var _LINE_WIDTH_RANGE = new Float32Array([1, 7]);
    var _POINT_SIZE_RANGE = new Float32Array([1, 1024]);

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
            } else if (obj === window && typeof Window !== 'undefined' && Window.prototype) {
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

    // 1. Timezone coherence (Europe/Paris)
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

    // 2. Suppress transient proxy/network alert popups
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
            if (originalAlert) return originalAlert.apply(this, arguments);
        }, 'alert');
        if (typeof Window !== 'undefined' && Window.prototype && Window.prototype.alert) {
            Window.prototype.alert = wrappedAlert;
        } else {
            window.alert = wrappedAlert;
        }
        document.addEventListener('deviceready', function() {
            if (navigator.notification && navigator.notification.alert) {
                var origCdvAlert = navigator.notification.alert;
                navigator.notification.alert = makeNative(function(msg, callback) {
                    if (msg && typeof msg === 'string') {
                        var lower = msg.toLowerCase();
                        if (lower.indexOf('server cannot be reached') !== -1 || lower.indexOf('check your internet') !== -1) {
                            if (typeof callback === 'function') callback();
                            return;
                        }
                    }
                    return origCdvAlert.apply(this, arguments);
                }, 'alert');
            }
        }, false);
    } catch (e) {}

    // 3. Navigator & PluginArray / NetworkInformation spoofing
    defineProp(navigator, 'platform', MOBILE_PLATFORM);
    defineProp(navigator, 'userAgent', MOBILE_UA);
    defineProp(navigator, 'appVersion', MOBILE_APP_VERSION);
    defineProp(navigator, 'maxTouchPoints', 5);
    defineProp(navigator, 'hardwareConcurrency', _hwSpecs.cores);
    defineProp(navigator, 'deviceMemory', _hwSpecs.ram);
    defineProp(navigator, 'vendor', 'Google Inc.');
    defineProp(navigator, 'webdriver', false);
    defineProp(navigator, 'language', 'fr-FR');
    defineProp(navigator, 'languages', ['fr-FR', 'fr', 'en-US', 'en']);
    defineProp(navigator, 'onLine', true);

    try {
        if (typeof Navigator !== 'undefined' && Navigator.prototype) {
            Navigator.prototype.vibrate = makeNative(function vibrate() { return true; }, 'vibrate');
            if (navigator !== Navigator.prototype) {
                try { navigator.vibrate = Navigator.prototype.vibrate; } catch (e) {}
            }
        }
    } catch (e) {}

    var emptyPluginArray, emptyMimeTypeArray;
    try {
        emptyPluginArray = (typeof PluginArray !== 'undefined' && PluginArray.prototype)
            ? Object.create(PluginArray.prototype) : Object.create(Object.prototype);
        Object.defineProperty(emptyPluginArray, 'length', { value: 0, writable: false, enumerable: false, configurable: true });
        emptyPluginArray.item = makeNative(function item() { return null; }, 'item');
        emptyPluginArray.namedItem = makeNative(function namedItem() { return null; }, 'namedItem');
        emptyPluginArray.refresh = makeNative(function refresh() {}, 'refresh');
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
            try { Object.defineProperty(emptyPluginArray, Symbol.toStringTag, { value: 'PluginArray', configurable: true }); } catch (e) {}
        }

        emptyMimeTypeArray = (typeof MimeTypeArray !== 'undefined' && MimeTypeArray.prototype)
            ? Object.create(MimeTypeArray.prototype) : Object.create(Object.prototype);
        Object.defineProperty(emptyMimeTypeArray, 'length', { value: 0, writable: false, enumerable: false, configurable: true });
        emptyMimeTypeArray.item = makeNative(function item() { return null; }, 'item');
        emptyMimeTypeArray.namedItem = makeNative(function namedItem() { return null; }, 'namedItem');
        if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
            try { Object.defineProperty(emptyMimeTypeArray, Symbol.toStringTag, { value: 'MimeTypeArray', configurable: true }); } catch (e) {}
        }

        defineProp(navigator, 'plugins', emptyPluginArray);
        defineProp(navigator, 'mimeTypes', emptyMimeTypeArray);
    } catch (e) {}

    var connectionObj;
    try {
        connectionObj = (typeof NetworkInformation !== 'undefined' && NetworkInformation.prototype)
            ? Object.create(NetworkInformation.prototype)
            : ((typeof EventTarget !== 'undefined' && EventTarget.prototype) ? Object.create(EventTarget.prototype) : Object.create(Object.prototype));
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
            try { Object.defineProperty(connectionObj, Symbol.toStringTag, { value: 'NetworkInformation', configurable: true }); } catch (e) {}
        }
        var baseRtt = 40 + (_instanceHash % 25);
        var baseDownlink = 12.0 + ((_instanceHash >>> 4) % 80) * 0.1;
        Object.defineProperty(connectionObj, 'rtt', {
            get: makeNative(function() { return baseRtt + Math.floor((Math.random() - 0.5) * 6); }, 'get rtt'),
            enumerable: true
        });
        Object.defineProperty(connectionObj, 'downlink', {
            get: makeNative(function() { return +(baseDownlink + (Math.random() - 0.5) * 1.2).toFixed(1); }, 'get downlink'),
            enumerable: true
        });
        defineProp(navigator, 'connection', connectionObj);
        defineProp(navigator, 'mozConnection', connectionObj);
        defineProp(navigator, 'webkitConnection', connectionObj);
    } catch (e) {}

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

    if (navigator.userAgentData || window.NavigatorUAData) {
        var uaData = {
            brands: [
                { brand: 'Android WebView', version: _majorVer },
                { brand: 'Chromium', version: _majorVer },
                { brand: 'Not(A:Brand', version: '99' }
            ],
            mobile: true,
            platform: 'Android',
            getHighEntropyValues: makeNative(function() {
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
                return { brands: uaData.brands, mobile: true, platform: 'Android' };
            }, 'toJSON')
        };
        defineProp(navigator, 'userAgentData', uaData);
    }

    // 4. WebGL, WebGL2 & OffscreenCanvas Vendor/Renderer/ASTC/Precision Disguise
    var UNMASKED_VENDOR_WEBGL = 0x9245;
    var UNMASKED_RENDERER_WEBGL = 0x9246;

    function wrapContext(ctxProto) {
        if (!ctxProto) return;
        var origGetParameter = ctxProto.getParameter;
        var origGetExtension = ctxProto.getExtension;
        var origGetSupportedExtensions = ctxProto.getSupportedExtensions;
        var origGetShaderPrecisionFormat = ctxProto.getShaderPrecisionFormat;

        if (origGetParameter) {
            ctxProto.getParameter = makeNative(function(param) {
                if (param === UNMASKED_VENDOR_WEBGL || param === 0x1F00) return GL_VENDOR;
                if (param === UNMASKED_RENDERER_WEBGL || param === 0x1F01) return GL_RENDERER;
                if (param === 0x1F02) {
                    var isGL2 = (typeof WebGL2RenderingContext !== 'undefined' && this instanceof WebGL2RenderingContext);
                    return isGL2 ? 'WebGL 2.0 (OpenGL ES 3.0 Chromium)' : 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
                }
                if (param === 0x8B8C) {
                    var isGL2Sh = (typeof WebGL2RenderingContext !== 'undefined' && this instanceof WebGL2RenderingContext);
                    return isGL2Sh ? 'WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)' : 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
                }
                if (param === 0x0D33 || param === 0x851C || param === 0x84E8) return MAX_TEX_SIZE;
                if (param === 0x0D3A) return _VIEWPORT_DIMS;
                if (param === 0x846E) return _LINE_WIDTH_RANGE;
                if (param === 0x846D) return _POINT_SIZE_RANGE;
                if (param === 0x8869 || param === 0x8B4C || param === 0x8872) return 16;
                if (param === 0x8DFB || param === 0x8DFD) return 1024;
                if (param === 0x8DFC) return 30;
                if (param === 0x8B4D) return 96;
                return origGetParameter.apply(this, arguments);
            }, 'getParameter');
        }

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
                        COMPRESSED_RGBA_ASTC_4x4_KHR: 0x93B0, COMPRESSED_RGBA_ASTC_5x4_KHR: 0x93B1,
                        COMPRESSED_RGBA_ASTC_5x5_KHR: 0x93B2, COMPRESSED_RGBA_ASTC_6x5_KHR: 0x93B3,
                        COMPRESSED_RGBA_ASTC_6x6_KHR: 0x93B4, COMPRESSED_RGBA_ASTC_8x5_KHR: 0x93B5,
                        COMPRESSED_RGBA_ASTC_8x6_KHR: 0x93B6, COMPRESSED_RGBA_ASTC_8x8_KHR: 0x93B7,
                        COMPRESSED_RGBA_ASTC_10x5_KHR: 0x93B8, COMPRESSED_RGBA_ASTC_10x6_KHR: 0x93B9,
                        COMPRESSED_RGBA_ASTC_10x8_KHR: 0x93BA, COMPRESSED_RGBA_ASTC_10x10_KHR: 0x93BB,
                        COMPRESSED_RGBA_ASTC_12x10_KHR: 0x93BC, COMPRESSED_RGBA_ASTC_12x12_KHR: 0x93BD,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_4x4_KHR: 0x93D0, COMPRESSED_SRGB8_ALPHA8_ASTC_5x4_KHR: 0x93D1,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_5x5_KHR: 0x93D2, COMPRESSED_SRGB8_ALPHA8_ASTC_6x5_KHR: 0x93D3,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_6x6_KHR: 0x93D4, COMPRESSED_SRGB8_ALPHA8_ASTC_8x5_KHR: 0x93D5,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_8x6_KHR: 0x93D6, COMPRESSED_SRGB8_ALPHA8_ASTC_8x8_KHR: 0x93D7,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_10x5_KHR: 0x93D8, COMPRESSED_SRGB8_ALPHA8_ASTC_10x6_KHR: 0x93D9,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_10x8_KHR: 0x93DA, COMPRESSED_SRGB8_ALPHA8_ASTC_10x10_KHR: 0x93DB,
                        COMPRESSED_SRGB8_ALPHA8_ASTC_12x10_KHR: 0x93DC, COMPRESSED_SRGB8_ALPHA8_ASTC_12x12_KHR: 0x93DD,
                        getSupportedProfiles: makeNative(function() { return ['ldr']; }, 'getSupportedProfiles')
                    };
                }
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
                if (precisionType < 0x8DF0 || precisionType > 0x8DF5) return null;
                if (shaderType !== 0x8B30 && shaderType !== 0x8B31) return null;
                var rMin = 127, rMax = 127, prec = 23;
                if (precisionType === 0x8DF1 || precisionType === 0x8DF0) { rMin = 15; rMax = 15; prec = 10; }
                else if (precisionType === 0x8DF5) { rMin = 31; rMax = 30; prec = 0; }
                else if (precisionType === 0x8DF4) { rMin = 15; rMax = 14; prec = 0; }
                else if (precisionType === 0x8DF3) { rMin = 7; rMax = 6; prec = 0; }
                var target = null;
                try { target = origGetShaderPrecisionFormat.call(this, shaderType, precisionType); } catch (e) {}
                if (!target) {
                    var proto = (typeof WebGLShaderPrecisionFormat !== 'undefined' && WebGLShaderPrecisionFormat.prototype)
                        ? WebGLShaderPrecisionFormat.prototype : Object.prototype;
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

    if (window.WebGLRenderingContext) wrapContext(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) wrapContext(WebGL2RenderingContext.prototype);
    if (typeof OffscreenCanvas !== 'undefined' && OffscreenCanvas.prototype && OffscreenCanvas.prototype.getContext) {
        var origOffscreenGetContext = OffscreenCanvas.prototype.getContext;
        OffscreenCanvas.prototype.getContext = makeNative(function(type) {
            var ctx = origOffscreenGetContext.apply(this, arguments);
            if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {
                wrapContext(Object.getPrototypeOf(ctx));
            }
            return ctx;
        }, 'getContext');
    }

    // 5. Lazy, Per-Instance Seeded BatteryManager
    var battStartTime = Date.now();
    var initialBattPct = 72 + (_instanceHash % 23);
    var battProto = (typeof BatteryManager !== 'undefined' && BatteryManager.prototype)
        ? BatteryManager.prototype
        : Object.create((typeof EventTarget !== 'undefined' && EventTarget.prototype) ? EventTarget.prototype : Object.prototype);
    var fakeBattery = Object.create(battProto);

    function getLazyBatteryLevel() {
        var elapsedMin = (Date.now() - battStartTime) / 60000;
        var dropSteps = Math.floor(elapsedMin / 6);
        return Math.max(20, initialBattPct - dropSteps) / 100;
    }

    try {
        Object.defineProperties(battProto, {
            charging: { get: makeNative(function() { return false; }, 'get charging'), enumerable: true, configurable: true },
            chargingTime: { get: makeNative(function() { return Infinity; }, 'get chargingTime'), enumerable: true, configurable: true },
            dischargingTime: { get: makeNative(function() { return Math.round(getLazyBatteryLevel() * 4 * 3600); }, 'get dischargingTime'), enumerable: true, configurable: true },
            level: { get: makeNative(function() { return getLazyBatteryLevel(); }, 'get level'), enumerable: true, configurable: true }
        });
    } catch (e) {}

    var getBatteryMethod = makeNative(function getBattery() {
        return Promise.resolve(fakeBattery);
    }, 'getBattery');

    try {
        if (typeof Navigator !== 'undefined' && Navigator.prototype) {
            Object.defineProperty(Navigator.prototype, 'getBattery', {
                value: getBatteryMethod, writable: true, enumerable: true, configurable: true
            });
        }
        defineProp(navigator, 'battery', fakeBattery);
    } catch (e) {}

    // 6. Coherent Touch & PointerEvent Disguise
    try {
        if (!('ontouchstart' in window)) {
            window.ontouchstart = null;
            window.ontouchend = null;
            window.ontouchmove = null;
            window.ontouchcancel = null;
        }
        var eventContactCache = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
        var baseTouchRadius = 14 + (_instanceHash % 4);
        function getEventContact(ev) {
            if (eventContactCache && ev && typeof ev === 'object') {
                var cached = eventContactCache.get(ev);
                if (cached) return cached;
            }
            var r = baseTouchRadius + Math.round((Math.random() - 0.5) * 4);
            var p = +(0.58 + (Math.random() - 0.5) * 0.14).toFixed(2);
            var result = { radius: Math.max(11, Math.min(23, r)), pressure: p };
            if (eventContactCache && ev && typeof ev === 'object') {
                eventContactCache.set(ev, result);
            }
            return result;
        }

        if (window.PointerEvent && PointerEvent.prototype) {
            Object.defineProperty(PointerEvent.prototype, 'pointerType', {
                get: makeNative(function() { return 'touch'; }, 'get pointerType'),
                configurable: true, enumerable: true
            });
            Object.defineProperty(PointerEvent.prototype, 'width', {
                get: makeNative(function() {
                    if (this.buttons === 0 && this.type !== 'pointerdown') return 1;
                    return getEventContact(this).radius * 2;
                }, 'get width'),
                configurable: true, enumerable: true
            });
            Object.defineProperty(PointerEvent.prototype, 'height', {
                get: makeNative(function() {
                    if (this.buttons === 0 && this.type !== 'pointerdown') return 1;
                    return getEventContact(this).radius * 2;
                }, 'get height'),
                configurable: true, enumerable: true
            });
            Object.defineProperty(PointerEvent.prototype, 'pressure', {
                get: makeNative(function() {
                    if (this.type === 'pointerup' || this.type === 'pointercancel') return 0;
                    if (this.buttons === 0 && this.type !== 'pointerdown') return 0;
                    return getEventContact(this).pressure;
                }, 'get pressure'),
                configurable: true, enumerable: true
            });
        }

        if (typeof Touch !== 'undefined' && Touch.prototype) {
            Object.defineProperty(Touch.prototype, 'radiusX', {
                get: makeNative(function() { return getEventContact(this).radius; }, 'get radiusX'),
                configurable: true, enumerable: true
            });
            Object.defineProperty(Touch.prototype, 'radiusY', {
                get: makeNative(function() { return getEventContact(this).radius + 1; }, 'get radiusY'),
                configurable: true, enumerable: true
            });
            Object.defineProperty(Touch.prototype, 'force', {
                get: makeNative(function() { return getEventContact(this).pressure; }, 'get force'),
                configurable: true, enumerable: true
            });
        }

        if (window.matchMedia) {
            var origMatchMedia = window.matchMedia;
            window.matchMedia = makeNative(function(query) {
                var q = (query || '').toLowerCase();
                if (q.indexOf('hover: none') !== -1 || q.indexOf('any-hover: none') !== -1 ||
                    q.indexOf('pointer: coarse') !== -1 || q.indexOf('any-pointer: coarse') !== -1) {
                    return {
                        matches: true, media: query, onchange: null,
                        addListener: function() {}, removeListener: function() {},
                        addEventListener: function() {}, removeEventListener: function() {},
                        dispatchEvent: function() { return false; }
                    };
                }
                if (q.indexOf('hover: hover') !== -1 || q.indexOf('any-hover: hover') !== -1 ||
                    q.indexOf('pointer: fine') !== -1 || q.indexOf('any-pointer: fine') !== -1) {
                    return {
                        matches: false, media: query, onchange: null,
                        addListener: function() {}, removeListener: function() {},
                        addEventListener: function() {}, removeEventListener: function() {},
                        dispatchEvent: function() { return false; }
                    };
                }
                return origMatchMedia.apply(this, arguments);
            }, 'matchMedia');
        }
    } catch (e) {}

    // 7. On-Demand Accelerometer & Gyroscope Sensor Spoofing
    try {
        var motionListeners = [];
        var orientationListeners = [];
        var onDeviceMotionHandler = null;
        var onDeviceOrientationHandler = null;
        var _sensorTimerId = null;

        function createDeviceMotionEvent(acc, accG, rot, interval) {
            var evt = null;
            if (typeof DeviceMotionEvent === 'function') {
                try {
                    evt = new DeviceMotionEvent('devicemotion', {
                        acceleration: acc, accelerationIncludingGravity: accG, rotationRate: rot, interval: interval
                    });
                } catch (e) {}
            }
            if (!evt) evt = new Event('devicemotion');
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
                        alpha: alpha, beta: beta, gamma: gamma, absolute: absolute
                    });
                } catch (e) {}
            }
            if (!evt) evt = new Event('deviceorientation');
            try {
                Object.defineProperty(evt, 'alpha', { value: alpha, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'beta', { value: beta, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'gamma', { value: gamma, configurable: true, enumerable: true });
                Object.defineProperty(evt, 'absolute', { value: absolute, configurable: true, enumerable: true });
            } catch (e) {}
            return evt;
        }

        function tickSensors() {
            var hasMotion = motionListeners.length > 0 || typeof onDeviceMotionHandler === 'function';
            var hasOrient = orientationListeners.length > 0 || typeof onDeviceOrientationHandler === 'function';
            if (!hasMotion && !hasOrient) {
                if (_sensorTimerId !== null) { clearInterval(_sensorTimerId); _sensorTimerId = null; }
                return;
            }
            if (hasMotion) {
                var acc = { x: (Math.random() - 0.5) * 0.02, y: (Math.random() - 0.5) * 0.02, z: (Math.random() - 0.5) * 0.02 };
                var accG = { x: 0.08 + (Math.random() - 0.5) * 0.04, y: 9.80 + (Math.random() - 0.5) * 0.06, z: 0.42 + (Math.random() - 0.5) * 0.04 };
                var rot = { alpha: (Math.random() - 0.5) * 0.1, beta: (Math.random() - 0.5) * 0.1, gamma: (Math.random() - 0.5) * 0.1 };
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
        }

        function ensureSensorTimer() {
            if (_sensorTimerId === null) _sensorTimerId = setInterval(tickSensors, 50);
        }

        var targetProto = (typeof EventTarget !== 'undefined' && EventTarget.prototype) ? EventTarget.prototype : window;
        var origAddEL = targetProto.addEventListener || window.addEventListener;
        var origRemEL = targetProto.removeEventListener || window.removeEventListener;

        targetProto.addEventListener = makeNative(function(type, listener, options) {
            if (this === window || (typeof Window !== 'undefined' && this instanceof Window)) {
                if (type === 'devicemotion' && listener && motionListeners.indexOf(listener) === -1) {
                    motionListeners.push(listener); ensureSensorTimer();
                } else if (type === 'deviceorientation' && listener && orientationListeners.indexOf(listener) === -1) {
                    orientationListeners.push(listener); ensureSensorTimer();
                }
            }
            if (origAddEL) return origAddEL.apply(this, arguments);
        }, 'addEventListener');

        targetProto.removeEventListener = makeNative(function(type, listener, options) {
            if (this === window || (typeof Window !== 'undefined' && this instanceof Window)) {
                if (type === 'devicemotion') {
                    var idx = motionListeners.indexOf(listener);
                    if (idx !== -1) motionListeners.splice(idx, 1);
                } else if (type === 'deviceorientation') {
                    var idxO = orientationListeners.indexOf(listener);
                    if (idxO !== -1) orientationListeners.splice(idxO, 1);
                }
            }
            if (origRemEL) return origRemEL.apply(this, arguments);
        }, 'removeEventListener');

        var winProto = (typeof Window !== 'undefined' && Window.prototype) ? Window.prototype : window;
        Object.defineProperty(winProto, 'ondevicemotion', {
            get: makeNative(function() { return onDeviceMotionHandler; }, 'get ondevicemotion'),
            set: makeNative(function(fn) { onDeviceMotionHandler = fn; if (typeof fn === 'function') ensureSensorTimer(); }, 'set ondevicemotion'),
            configurable: true, enumerable: true
        });
        Object.defineProperty(winProto, 'ondeviceorientation', {
            get: makeNative(function() { return onDeviceOrientationHandler; }, 'get ondeviceorientation'),
            set: makeNative(function(fn) { onDeviceOrientationHandler = fn; if (typeof fn === 'function') ensureSensorTimer(); }, 'set ondeviceorientation'),
            configurable: true, enumerable: true
        });
    } catch (e) {}

    // 8. Coherent Screen & Orientation
    try {
        if (window.screen) {
            defineProp(window.screen, 'colorDepth', 24);
            defineProp(window.screen, 'pixelDepth', 24);
            defineProp(window.screen, 'width', function() { return window.innerWidth || 1280; });
            defineProp(window.screen, 'height', function() { return window.innerHeight || 720; });
            defineProp(window.screen, 'availWidth', function() { return window.innerWidth || 1280; });
            defineProp(window.screen, 'availHeight', function() { return window.innerHeight || 720; });
            if (window.screen.orientation) {
                Object.defineProperty(window.screen.orientation, 'type', {
                    get: makeNative(function() { return 'landscape-primary'; }, 'get type'),
                    enumerable: true, configurable: true
                });
                Object.defineProperty(window.screen.orientation, 'angle', {
                    get: makeNative(function() { return 90; }, 'get angle'),
                    enumerable: true, configurable: true
                });
            }
        }
    } catch (e) {}

    // 9. Cordova Device Plugin Identity Synchronization
    function applyDevProps(dev) {
        if (!dev) return;
        dev.model = _deviceModel;
        dev.manufacturer = _deviceManufacturer;
        dev.isVirtual = false;
        dev.platform = 'Android';
        dev.version = _osVer;
        dev.uuid = _deviceUuid;
        dev.serial = _deviceSerial;
    }

    function patchCordovaDevice() {
        try { if (window.device) applyDevProps(window.device); } catch (e) {}
    }
    patchCordovaDevice();
    if (typeof document !== 'undefined' && document.addEventListener) {
        document.addEventListener('deviceready', patchCordovaDevice, false);
    }

    try {
        var _curDev = window.device;
        Object.defineProperty(window, 'device', {
            get: makeNative(function() {
                if (_curDev) applyDevProps(_curDev);
                return _curDev;
            }, 'get device'),
            set: makeNative(function(val) {
                _curDev = val;
                if (_curDev) applyDevProps(_curDev);
            }, 'set device'),
            configurable: true, enumerable: true
        });
    } catch (e) {}

    if (window.cordova && window.cordova.define) {
        var origDefine = window.cordova.define;
        window.cordova.define = makeNative(function(id, factory) {
            if (id === 'cordova-plugin-device.device') {
                var wrappedFactory = function(require, exports, module) {
                    factory(require, exports, module);
                    var dev = module.exports;
                    if (dev) {
                        applyDevProps(dev);
                        if (dev.getInfo) {
                            var origGetInfo = dev.getInfo;
                            dev.getInfo = makeNative(function(success, error) {
                                origGetInfo.call(dev, function(info) {
                                    applyDevProps(info);
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

    // 10. Full WebRTC SDP & ICE Candidate QEMU 10.0.2.15 Scrubbing
    try {
        var PeerConn = window.RTCPeerConnection || window.webkitRTCPeerConnection;
        if (PeerConn && PeerConn.prototype) {
            var origCreateOffer = PeerConn.prototype.createOffer;
            if (origCreateOffer) {
                PeerConn.prototype.createOffer = makeNative(function() {
                    return origCreateOffer.apply(this, arguments).then(function(offer) {
                        if (offer && offer.sdp) offer.sdp = offer.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp);
                        return offer;
                    });
                }, 'createOffer');
            }
            var origCreateAnswer = PeerConn.prototype.createAnswer;
            if (origCreateAnswer) {
                PeerConn.prototype.createAnswer = makeNative(function() {
                    return origCreateAnswer.apply(this, arguments).then(function(answer) {
                        if (answer && answer.sdp) answer.sdp = answer.sdp.replace(/10\.0\.2\.15/g, _fakeLocalIp);
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
                    try { Object.defineProperty(candidate, 'address', { value: _fakeLocalIp, configurable: true, enumerable: true }); } catch (e) {}
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
            var origOnIceDesc = Object.getOwnPropertyDescriptor(PeerConn.prototype, 'onicecandidate');
            if (origOnIceDesc && origOnIceDesc.set) {
                var iceHandlerMap = new WeakMap();
                Object.defineProperty(PeerConn.prototype, 'onicecandidate', {
                    get: makeNative(function() { return iceHandlerMap.get(this) || null; }, 'get onicecandidate'),
                    set: makeNative(function(fn) {
                        if (typeof fn === 'function') {
                            iceHandlerMap.set(this, fn);
                            var self = this;
                            origOnIceDesc.set.call(this, function(evt) {
                                if (evt && evt.candidate) scrubCandidate(evt.candidate);
                                return fn.call(self, evt);
                            });
                        } else {
                            iceHandlerMap.delete(this);
                            origOnIceDesc.set.call(this, null);
                        }
                    }, 'set onicecandidate'),
                    configurable: true, enumerable: true
                });
            }
        }
    } catch (e) {}

    // 11. Per-Instance Deterministic Audio & Font Coherence
    try {
        if (window.AudioContext || window.webkitAudioContext) {
            var Actx = window.AudioContext || window.webkitAudioContext;
            Object.defineProperty(Actx.prototype, 'sampleRate', {
                get: makeNative(function() { return 48000; }, 'get sampleRate'),
                enumerable: true, configurable: true
            });
        }
        if (window.OfflineAudioContext && OfflineAudioContext.prototype.startRendering) {
            var origStartRendering = OfflineAudioContext.prototype.startRendering;
            var audioSeedJitter = ((_instanceHash % 97) + 1) * 1e-8;
            OfflineAudioContext.prototype.startRendering = makeNative(function() {
                return origStartRendering.apply(this, arguments).then(function(buffer) {
                    if (buffer && buffer.getChannelData) {
                        var chan = buffer.getChannelData(0);
                        if (chan && chan.length > 10) {
                            for (var j = (_instanceHash % 7); j < chan.length; j += 128) {
                                chan[j] += audioSeedJitter;
                            }
                        }
                    }
                    return buffer;
                });
            }, 'startRendering');
        }
        if (document.fonts && document.fonts.check) {
            var origFontsCheck = document.fonts.check;
            var desktopFonts = ['segoe ui', 'calibri', 'consolas', 'cambria', 'ms gothic', 'arial black', 'tahoma', 'lucida console', 'comic sans ms'];
            document.fonts.check = makeNative(function(font) {
                var fLower = (font || '').toLowerCase();
                for (var df = 0; df < desktopFonts.length; df++) {
                    if (fLower.indexOf(desktopFonts[df]) !== -1) return false;
                }
                return origFontsCheck.apply(this, arguments);
            }, 'check');
        }
    } catch (e) {}

    // 12. Outbound HTTPS XHR & Fetch Payload/Header Sanitizer (scrubs x86/QEMU/10.0.2.15 before TLS encryption)
    try {
        function scrubNetworkString(str) {
            if (typeof str !== 'string' || str.length === 0) return str;
            if (str.indexOf('10.0.2.15') === -1 && str.indexOf('x86') === -1 && str.indexOf('goldfish') === -1 && str.indexOf('ranchu') === -1 && str.indexOf('sdk_gphone') === -1) {
                return str;
            }
            return str
                .replace(/10\.0\.2\.15/g, _fakeLocalIp)
                .replace(/x86_64/gi, 'arm64-v8a')
                .replace(/sdk_gphone_x86_64|Android SDK built for x86_64|sdk_gphone_x86/gi, _deviceModel)
                .replace(/goldfish|ranchu/gi, 'qcom');
        }

        if (window.XMLHttpRequest && XMLHttpRequest.prototype) {
            var origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
            if (origSetHeader) {
                XMLHttpRequest.prototype.setRequestHeader = makeNative(function(header, value) {
                    var hLower = (header || '').toLowerCase();
                    if (hLower === 'user-agent') value = MOBILE_UA;
                    else if (hLower === 'sec-ch-ua-arch') value = '"arm"';
                    else if (hLower === 'sec-ch-ua-platform') value = '"Android"';
                    else if (hLower === 'sec-ch-ua-model') value = '"' + _deviceModel + '"';
                    else if (typeof value === 'string') value = scrubNetworkString(value);
                    return origSetHeader.call(this, header, value);
                }, 'setRequestHeader');
            }
            var origXhrSend = XMLHttpRequest.prototype.send;
            if (origXhrSend) {
                XMLHttpRequest.prototype.send = makeNative(function(body) {
                    if (typeof body === 'string') {
                        body = scrubNetworkString(body);
                    }
                    return origXhrSend.call(this, body);
                }, 'send');
            }
        }
        if (typeof window.fetch === 'function') {
            var origFetch = window.fetch;
            window.fetch = makeNative(function(input, init) {
                if (init && typeof init === 'object' && typeof init.body === 'string') {
                    init.body = scrubNetworkString(init.body);
                }
                return origFetch.apply(this, arguments);
            }, 'fetch');
        }
    } catch (e) {}

    // 13. Child Iframe Disguise
    function disguiseFrameWindow(targetWin) {
        if (!targetWin || targetWin === window || disguisedFrames.has(targetWin)) return;
        try {
            disguisedFrames.add(targetWin);
            if (targetWin.Function && targetWin.Function.prototype) {
                targetWin.Function.prototype.toString = customToString;
            }
            var tNavProto = (targetWin.Navigator && targetWin.Navigator.prototype) ? targetWin.Navigator.prototype : targetWin.navigator;
            if (tNavProto) {
                var props = {
                    platform: MOBILE_PLATFORM, userAgent: MOBILE_UA, appVersion: MOBILE_APP_VERSION,
                    maxTouchPoints: 5, hardwareConcurrency: _hwSpecs.cores, deviceMemory: _hwSpecs.ram,
                    vendor: 'Google Inc.', webdriver: false, onLine: true
                };
                for (var k in props) {
                    (function(p, val) {
                        try {
                            Object.defineProperty(tNavProto, p, {
                                get: makeNative(function() { return val; }, 'get ' + p),
                                configurable: true, enumerable: true
                            });
                        } catch (e) {}
                    })(k, props[k]);
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
                    get: makeNative(function() {
                        var cw = origCWDesc.get.apply(this, arguments);
                        if (cw) disguiseFrameWindow(cw);
                        return cw;
                    }, 'get contentWindow'),
                    set: origCWDesc.set, configurable: true, enumerable: true
                });
            }
        }
    } catch (e) {}
})();
