/**
 * Stealth Mobile Environment & WebGL Disguise Shim - Dynamic Edition
 * Normalizes WebView DOM, WebGL rendering context, Navigator, Battery/Thermal dynamics,
 * Sensor micro-tremor (IMU), Touch/Cursor interactions (zero-hover), Network Information,
 * AudioContext, and Cordova APIs to appear as an authentic physical Samsung Galaxy A51
 * (ARM Mali-G76 MP12) mobile device with zero desktop cursor leaks.
 */
(function() {
    'use strict';

    if (window.__MOBILE_DISGUISE_ACTIVE__) return;
    window.__MOBILE_DISGUISE_ACTIVE__ = true;

    // --- 1. Function toString Cloaking ---
    var nativeToString = Function.prototype.toString;
    var hookedFunctions = new WeakMap();

    function makeNative(fn, name) {
        hookedFunctions.set(fn, 'function ' + (name || fn.name || '') + '() { [native code] }');
        return fn;
    }

    Function.prototype.toString = function() {
        if (hookedFunctions.has(this)) {
            return hookedFunctions.get(this);
        }
        return nativeToString.apply(this, arguments);
    };
    makeNative(Function.prototype.toString, 'toString');

    // Helper to safely redefine properties with native getter masking
    function defineProp(obj, prop, valueGetter) {
        try {
            Object.defineProperty(obj, prop, {
                get: makeNative(function() {
                    return typeof valueGetter === 'function' ? valueGetter() : valueGetter;
                }, 'get ' + prop),
                set: makeNative(function(val) {}, 'set ' + prop),
                configurable: true,
                enumerable: true
            });
        } catch (e) {}
    }

    // --- 2. Navigator Properties ---
    var MOBILE_UA = 'Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36';
    var MOBILE_APP_VERSION = '5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36';
    var MOBILE_PLATFORM = 'Linux armv8l';

    defineProp(navigator, 'platform', MOBILE_PLATFORM);
    defineProp(navigator, 'userAgent', MOBILE_UA);
    defineProp(navigator, 'appVersion', MOBILE_APP_VERSION);
    defineProp(navigator, 'maxTouchPoints', 5);
    defineProp(navigator, 'hardwareConcurrency', 8);
    defineProp(navigator, 'deviceMemory', 4);
    defineProp(navigator, 'vendor', 'Google Inc.');
    defineProp(navigator, 'webdriver', false);
    defineProp(navigator, 'vibrate', makeNative(function() { return true; }, 'vibrate'));

    // Plugins & MimeTypes: empty on real mobile webviews
    try {
        var emptyPluginArray = [];
        emptyPluginArray.item = makeNative(function() { return null; }, 'item');
        emptyPluginArray.namedItem = makeNative(function() { return null; }, 'namedItem');
        emptyPluginArray.refresh = makeNative(function() {}, 'refresh');
        defineProp(navigator, 'plugins', emptyPluginArray);
        defineProp(navigator, 'mimeTypes', []);
    } catch (e) {}

    // Network Information API (Dynamic 4G LTE cellular connection)
    var connectionObj = {
        effectiveType: '4g',
        type: 'cellular',
        saveData: false,
        onchange: null,
        addEventListener: makeNative(function() {}, 'addEventListener'),
        removeEventListener: makeNative(function() {}, 'removeEventListener'),
        dispatchEvent: makeNative(function() { return false; }, 'dispatchEvent')
    };
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

    // Client Hints (User-Agent Data)
    if (navigator.userAgentData || window.NavigatorUAData) {
        var uaData = {
            brands: [
                { brand: 'Android WebView', version: '83' },
                { brand: 'Chromium', version: '83' },
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
                    model: 'SM-A515F',
                    platform: 'Android',
                    platformVersion: '10',
                    uaFullVersion: '83.0.4103.106'
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
            Object.defineProperty(navigator, 'userAgentData', {
                get: makeNative(function() { return uaData; }, 'get userAgentData'),
                configurable: true,
                enumerable: true
            });
        } catch (e) {}
    }

    // --- 3. WebGL & WebGL2 Interception ---
    var GL_VENDOR = 'ARM';
    var GL_RENDERER = 'Mali-G76 MP12';
    var GL_VERSION = 'OpenGL ES 3.2 v1.r26p0-01rel0.640f0962b489da1b17b0185e783637e7';
    var GL_SHADING_LANG_VERSION = 'OpenGL ES GLSL ES 3.20';

    var UNMASKED_VENDOR_WEBGL = 0x9245;
    var UNMASKED_RENDERER_WEBGL = 0x9246;

    // Mobile Mali extensions
    var astcExtMock = {
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
        COMPRESSED_SRGB8_ALPHA8_ASTC_4x4_KHR: 0x93D0
    };
    var etcExtMock = {
        COMPRESSED_R11_EAC: 0x9270,
        COMPRESSED_SIGNED_R11_EAC: 0x9271,
        COMPRESSED_RG11_EAC: 0x9272,
        COMPRESSED_SIGNED_RG11_EAC: 0x9273,
        COMPRESSED_RGB8_ETC2: 0x9274,
        COMPRESSED_SRGB8_ETC2: 0x9275,
        COMPRESSED_RGB8_PUNCHTHROUGH_ALPHA1_ETC2: 0x9276,
        COMPRESSED_SRGB8_PUNCHTHROUGH_ALPHA1_ETC2: 0x9277,
        COMPRESSED_RGBA8_ETC2_EAC: 0x9278,
        COMPRESSED_SRGB8_ALPHA8_ETC2_EAC: 0x9279
    };
    var etc1ExtMock = {
        COMPRESSED_RGB_ETC1_WEBGL: 0x8D64
    };

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
            // 0x1F02 (VERSION)
            if (param === 0x1F02) return GL_VERSION;
            // 0x8B8C (SHADING_LANGUAGE_VERSION)
            if (param === 0x8B8C) return GL_SHADING_LANG_VERSION;
            // MAX_TEXTURE_SIZE (0x0D33) - 8192 on Mali-G76 (Desktop is 16384)
            if (param === 0x0D33) return 8192;
            // MAX_CUBE_MAP_TEXTURE_SIZE (0x851C)
            if (param === 0x851C) return 8192;
            // MAX_RENDERBUFFER_SIZE (0x84E8)
            if (param === 0x84E8) return 8192;
            // MAX_VIEWPORT_DIMS (0x0D3A)
            if (param === 0x0D3A) return new Int32Array([8192, 8192]);
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
                    var real = origGetExtension.apply(this, arguments);
                    return real || astcExtMock;
                }
                if (name === 'WEBGL_compressed_texture_etc') {
                    var real = origGetExtension.apply(this, arguments);
                    return real || etcExtMock;
                }
                if (name === 'WEBGL_compressed_texture_etc1') {
                    var real = origGetExtension.apply(this, arguments);
                    return real || etc1ExtMock;
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
                if (arr.indexOf('WEBGL_compressed_texture_astc') === -1) arr.push('WEBGL_compressed_texture_astc');
                if (arr.indexOf('WEBGL_compressed_texture_etc') === -1) arr.push('WEBGL_compressed_texture_etc');
                if (arr.indexOf('WEBGL_compressed_texture_etc1') === -1) arr.push('WEBGL_compressed_texture_etc1');
                if (arr.indexOf('KHR_parallel_shader_compile') === -1) arr.push('KHR_parallel_shader_compile');
                return arr;
            }, 'getSupportedExtensions');
        }

        if (origGetShaderPrecisionFormat) {
            ctxProto.getShaderPrecisionFormat = makeNative(function(shaderType, precisionType) {
                // Canonical ARM Mali-G76 precision format lookup
                // LOW_FLOAT (0x8DF0), MEDIUM_FLOAT (0x8DF1), HIGH_FLOAT (0x8DF2)
                // LOW_INT (0x8DF3), MEDIUM_INT (0x8DF4), HIGH_INT (0x8DF5)
                if (precisionType === 0x8DF2) { // HIGH_FLOAT
                    return { rangeMin: 127, rangeMax: 127, precision: 23 };
                }
                if (precisionType === 0x8DF1 || precisionType === 0x8DF0) { // MEDIUM_FLOAT or LOW_FLOAT
                    return { rangeMin: 15, rangeMax: 15, precision: 10 };
                }
                if (precisionType === 0x8DF5) { // HIGH_INT
                    return { rangeMin: 31, rangeMax: 30, precision: 0 };
                }
                if (precisionType === 0x8DF4) { // MEDIUM_INT
                    return { rangeMin: 15, rangeMax: 14, precision: 0 };
                }
                if (precisionType === 0x8DF3) { // LOW_INT
                    return { rangeMin: 7, rangeMax: 6, precision: 0 };
                }
                return { rangeMin: 127, rangeMax: 127, precision: 23 };
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
    var currentBattLevel = 0.85;
    var currentBattTemp = 28.5;
    var battStartTime = Date.now();

    var fakeBattery = {
        charging: false,
        chargingTime: Infinity,
        onchargingchange: null,
        onchargingtimechange: null,
        ondischargingtimechange: null,
        onlevelchange: null,
        addEventListener: makeNative(function(type, fn) {
            if (batteryListeners[type]) batteryListeners[type].push(fn);
        }, 'addEventListener'),
        removeEventListener: makeNative(function(type, fn) {
            if (batteryListeners[type]) {
                var idx = batteryListeners[type].indexOf(fn);
                if (idx !== -1) batteryListeners[type].splice(idx, 1);
            }
        }, 'removeEventListener'),
        dispatchEvent: makeNative(function(evt) {
            var type = evt.type;
            if (fakeBattery['on' + type]) fakeBattery['on' + type].call(fakeBattery, evt);
            if (batteryListeners[type]) {
                for (var i = 0; i < batteryListeners[type].length; i++) {
                    batteryListeners[type][i].call(fakeBattery, evt);
                }
            }
            return true;
        }, 'dispatchEvent')
    };

    Object.defineProperty(fakeBattery, 'level', {
        get: makeNative(function() { return currentBattLevel; }, 'get level'),
        enumerable: true
    });
    Object.defineProperty(fakeBattery, 'dischargingTime', {
        get: makeNative(function() { return Math.round(currentBattLevel * 4 * 3600); }, 'get dischargingTime'),
        enumerable: true
    });
    Object.defineProperty(fakeBattery, 'temperature', {
        get: makeNative(function() { return +currentBattTemp.toFixed(1); }, 'get temperature'),
        enumerable: true
    });

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
            window.dispatchEvent(battEvent);
            if (document) document.dispatchEvent(battEvent);
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

    defineProp(navigator, 'getBattery', makeNative(function() {
        return Promise.resolve(fakeBattery);
    }, 'getBattery'));
    defineProp(navigator, 'battery', fakeBattery);

    // --- 5. Touch Events, Zero-Hover Cursor Suppression & Capacitive Dynamics ---
    try {
        // Enforce transparent/hidden cursor CSS across entire DOM
        var ensureCursorNone = function() {
            if (!document.getElementById('__disguise_cursor_style__')) {
                var styleEl = document.createElement('style');
                styleEl.id = '__disguise_cursor_style__';
                styleEl.textContent = '* { cursor: none !important; }';
                (document.head || document.documentElement).appendChild(styleEl);
            }
        };
        ensureCursorNone();
        if (window.MutationObserver) {
            new MutationObserver(ensureCursorNone).observe(document.documentElement, { childList: true, subtree: true });
        }
    } catch (e) {}

    try {
        if (!('ontouchstart' in window)) {
            window.ontouchstart = null;
            window.ontouchend = null;
            window.ontouchmove = null;
            window.ontouchcancel = null;
        }

        // Dynamic finger contact patch generator
        function getDynamicFingerContact() {
            var r = 16 + Math.sin(Date.now() / 60) * 3 + (Math.random() - 0.5) * 2;
            var p = 0.62 + (Math.random() - 0.5) * 0.12;
            return { radius: Math.round(r), pressure: +p.toFixed(2) };
        }

        if (window.PointerEvent) {
            try {
                Object.defineProperty(PointerEvent.prototype, 'pointerType', {
                    get: makeNative(function() { return 'touch'; }, 'get pointerType'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'width', {
                    get: makeNative(function() { return getDynamicFingerContact().radius * 2; }, 'get width'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'height', {
                    get: makeNative(function() { return getDynamicFingerContact().radius * 2; }, 'get height'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'pressure', {
                    get: makeNative(function() { return this.buttons > 0 ? getDynamicFingerContact().pressure : 0; }, 'get pressure'),
                    configurable: true,
                    enumerable: true
                });
            } catch (err) {}
        }

        // Global cursor suppression handler: drops all passive hover when no button is pressed
        function suppressPassiveHover(e) {
            if (e.buttons === 0 && !e.__isSyntheticTouch__) {
                e.stopImmediatePropagation();
                e.stopPropagation();
                e.preventDefault();
                return false;
            }
            // Active click/drag: mask pointer parameters to mobile capacitive touch contact
            if (e.buttons > 0) {
                var contact = getDynamicFingerContact();
                try {
                    Object.defineProperty(e, 'pointerType', { get: function() { return 'touch'; }, configurable: true });
                    Object.defineProperty(e, 'pointerId', { get: function() { return 1; }, configurable: true });
                    Object.defineProperty(e, 'width', { get: function() { return contact.radius * 2; }, configurable: true });
                    Object.defineProperty(e, 'height', { get: function() { return contact.radius * 2; }, configurable: true });
                    Object.defineProperty(e, 'pressure', { get: function() { return contact.pressure; }, configurable: true });
                    Object.defineProperty(e, 'tangentialPressure', { get: function() { return 0; }, configurable: true });
                    Object.defineProperty(e, 'tiltX', { get: function() { return 0; }, configurable: true });
                    Object.defineProperty(e, 'tiltY', { get: function() { return 0; }, configurable: true });
                    Object.defineProperty(e, 'twist', { get: function() { return 0; }, configurable: true });
                } catch (err) {}
            }
        }

        var hoverEvents = ['mousemove', 'pointermove', 'mouseover', 'mouseenter', 'mouseout', 'mouseleave'];
        for (var h = 0; h < hoverEvents.length; h++) {
            window.addEventListener(hoverEvents[h], suppressPassiveHover, true);
            if (document) {
                document.addEventListener(hoverEvents[h], suppressPassiveHover, true);
                if (document.documentElement) {
                    document.documentElement.addEventListener(hoverEvents[h], suppressPassiveHover, true);
                }
            }
        }

        // Intercept EventTarget.addEventListener to guarantee no element callback catches hover
        var origAddEventListener = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = makeNative(function(type, listener, options) {
            if (typeof listener === 'function' && hoverEvents.indexOf(type) !== -1) {
                var wrappedListener = function(event) {
                    if (event.buttons === 0 && !event.__isSyntheticTouch__) {
                        return; // Non-existent ghost hover dropped
                    }
                    return listener.apply(this, arguments);
                };
                return origAddEventListener.call(this, type, wrappedListener, options);
            }
            return origAddEventListener.apply(this, arguments);
        }, 'addEventListener');

        // Wrap inline event handler properties on window, document and HTMLElement
        function wrapInlineHoverProps(target) {
            if (!target) return;
            hoverEvents.forEach(function(evt) {
                var prop = 'on' + evt;
                try {
                    var curVal = null;
                    Object.defineProperty(target, prop, {
                        get: function() { return curVal; },
                        set: function(fn) {
                            if (typeof fn === 'function') {
                                curVal = function(e) {
                                    if (e && e.buttons === 0 && !e.__isSyntheticTouch__) return;
                                    return fn.apply(this, arguments);
                                };
                            } else {
                                curVal = fn;
                            }
                        },
                        configurable: true,
                        enumerable: true
                    });
                } catch (e) {}
            });
        }
        wrapInlineHoverProps(window);
        if (typeof document !== 'undefined') wrapInlineHoverProps(document);
        if (typeof HTMLElement !== 'undefined') wrapInlineHoverProps(HTMLElement.prototype);

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

        var origWinAddEventListener = window.addEventListener;
        window.addEventListener = makeNative(function(type, listener, options) {
            if (type === 'devicemotion') motionListeners.push(listener);
            if (type === 'deviceorientation') orientationListeners.push(listener);
            return origWinAddEventListener.apply(this, arguments);
        }, 'addEventListener');

        // Support inline window.ondevicemotion and window.ondeviceorientation
        Object.defineProperty(window, 'ondevicemotion', {
            get: makeNative(function() { return onDeviceMotionHandler; }, 'get ondevicemotion'),
            set: makeNative(function(fn) { onDeviceMotionHandler = fn; }, 'set ondevicemotion'),
            configurable: true,
            enumerable: true
        });
        Object.defineProperty(window, 'ondeviceorientation', {
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

        // 20Hz Handheld Sensor Loop: dispatches authentic IMU noise
        setInterval(function() {
            var hasMotion = motionListeners.length > 0 || typeof onDeviceMotionHandler === 'function';
            var hasOrient = orientationListeners.length > 0 || typeof onDeviceOrientationHandler === 'function';
            if (!hasMotion && !hasOrient) return;

            // In landscape: gravity is primarily on Y/X vector
            var gx = 0.08 + (Math.random() - 0.5) * 0.04;
            var gy = 9.80 + (Math.random() - 0.5) * 0.06;
            var gz = 0.42 + (Math.random() - 0.5) * 0.04;

            if (hasMotion) {
                var motionEvt = new Event('devicemotion');
                motionEvt.acceleration = {
                    x: (Math.random() - 0.5) * 0.02,
                    y: (Math.random() - 0.5) * 0.02,
                    z: (Math.random() - 0.5) * 0.02
                };
                motionEvt.accelerationIncludingGravity = { x: gx, y: gy, z: gz };
                motionEvt.rotationRate = {
                    alpha: (Math.random() - 0.5) * 0.1,
                    beta: (Math.random() - 0.5) * 0.1,
                    gamma: (Math.random() - 0.5) * 0.1
                };
                motionEvt.interval = 50;
                for (var m = 0; m < motionListeners.length; m++) {
                    try { motionListeners[m].call(window, motionEvt); } catch (e) {}
                }
                if (typeof onDeviceMotionHandler === 'function') {
                    try { onDeviceMotionHandler.call(window, motionEvt); } catch (e) {}
                }
            }

            if (hasOrient) {
                var orientEvt = new Event('deviceorientation');
                orientEvt.alpha = +(180.2 + (Math.random() - 0.5) * 0.2).toFixed(2);
                orientEvt.beta = +(45.1 + (Math.random() - 0.5) * 0.2).toFixed(2);
                orientEvt.gamma = +((Math.random() - 0.5) * 0.2).toFixed(2);
                orientEvt.absolute = true;
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

    // --- 8. Cordova Device Plugin Interception ---
    function patchCordovaDevice() {
        if (window.device) {
            window.device.model = 'SM-A515F';
            window.device.manufacturer = 'samsung';
            window.device.isVirtual = false;
            window.device.platform = 'Android';
            window.device.version = '10';
        }
    }

    document.addEventListener('deviceready', patchCordovaDevice, false);
    if (window.cordova) {
        var origDefine = window.cordova.define;
        if (origDefine) {
            window.cordova.define = function(id, factory) {
                if (id === 'cordova-plugin-device.device') {
                    var wrappedFactory = function(require, exports, module) {
                        factory(require, exports, module);
                        var dev = module.exports;
                        if (dev && dev.getInfo) {
                            var origGetInfo = dev.getInfo;
                            dev.getInfo = function(success, error) {
                                origGetInfo.call(dev, function(info) {
                                    info.model = 'SM-A515F';
                                    info.manufacturer = 'samsung';
                                    info.isVirtual = false;
                                    info.platform = 'Android';
                                    info.version = '10';
                                    success(info);
                                }, error);
                            };
                        }
                    };
                    return origDefine.call(window.cordova, id, wrappedFactory);
                }
                return origDefine.apply(this, arguments);
            };
        }
    }

    // --- 9. Screen Depth, Pixel Ratio & WebRTC Protection ---
    try {
        if (window.screen) {
            defineProp(window.screen, 'colorDepth', 24);
            defineProp(window.screen, 'pixelDepth', 24);
            defineProp(window.screen, 'width', 1280);
            defineProp(window.screen, 'height', 720);
            defineProp(window.screen, 'availWidth', 1280);
            defineProp(window.screen, 'availHeight', 720);
        }
        defineProp(window, 'devicePixelRatio', 1.33125);
    } catch (e) {}

    // WebRTC IP leak protection: prevents host private IP & QEMU 10.0.2.15 leakage
    try {
        var PeerConn = window.RTCPeerConnection || window.webkitRTCPeerConnection;
        if (PeerConn) {
            var origCreateOffer = PeerConn.prototype.createOffer;
            if (origCreateOffer) {
                PeerConn.prototype.createOffer = makeNative(function(options) {
                    return origCreateOffer.apply(this, arguments).then(function(offer) {
                        if (offer && offer.sdp) {
                            offer.sdp = offer.sdp.replace(/10\.0\.2\.15/g, '192.168.1.105');
                        }
                        return offer;
                    });
                }, 'createOffer');
            }
            var origAddIceCandidate = PeerConn.prototype.addIceCandidate;
            if (origAddIceCandidate) {
                PeerConn.prototype.addIceCandidate = makeNative(function(candidate) {
                    if (candidate && candidate.candidate) {
                        if (/10\.0\.2\.15/.test(candidate.candidate)) {
                            candidate.candidate = candidate.candidate.replace(/10\.0\.2\.15/g, '192.168.1.105');
                        }
                    }
                    return origAddIceCandidate.apply(this, arguments);
                }, 'addIceCandidate');
            }
        }
    } catch (e) {}

    // --- 10. Canvas 2D, Font Probing & Offline Audio Defenses ---
    try {
        // A. Canvas 2D subtle micro-noise: masks desktop font hinting & GPU rasterization hashes
        if (window.CanvasRenderingContext2D) {
            var origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
            CanvasRenderingContext2D.prototype.getImageData = makeNative(function(sx, sy, sw, sh) {
                var imgData = origGetImageData.apply(this, arguments);
                if (imgData && imgData.data && imgData.data.length > 4) {
                    var d = imgData.data;
                    // Apply subtle 1-bit Mali noise on non-transparent pixels
                    for (var i = 0; i < d.length; i += 64) {
                        if (d[i + 3] > 0) {
                            d[i] = d[i] ^ 1;
                        }
                    }
                }
                return imgData;
            }, 'getImageData');
        }

        // B. Font Probing Normalization: ensure desktop Windows fonts return false/fallback
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

    console.log('[disguise] Mobile phone environment disguise activated: Samsung SM-A515F (Mali-G76 MP12 / ARM, Dynamic Sensors/Battery/Touch, Hover Suppressed, WebGL Leak-Proof, Canvas/Font/Audio Hardened)');
})();
