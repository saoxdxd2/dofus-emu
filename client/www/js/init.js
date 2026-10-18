/**
 * Stealth Mobile Environment & WebGL Disguise Shim
 * Normalizes WebView DOM, WebGL rendering context, Navigator, Battery/Thermal,
 * Touch/Cursor interactions, and Cordova APIs to appear as a physical Samsung
 * Galaxy A51 (ARM Mali-G76 MP12) mobile device with zero desktop cursor leaks.
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

    function wrapContext(ctxProto) {
        if (!ctxProto) return;
        var origGetParameter = ctxProto.getParameter;
        var origGetExtension = ctxProto.getExtension;
        var origGetSupportedExtensions = ctxProto.getSupportedExtensions;

        ctxProto.getParameter = makeNative(function(param) {
            // UNMASKED_VENDOR_WEBGL
            if (param === UNMASKED_VENDOR_WEBGL) return GL_VENDOR;
            // UNMASKED_RENDERER_WEBGL
            if (param === UNMASKED_RENDERER_WEBGL) return GL_RENDERER;
            // 0x1F00 (VENDOR)
            if (param === 0x1F00) return GL_VENDOR;
            // 0x1F01 (RENDERER)
            if (param === 0x1F01) return GL_RENDERER;
            // 0x1F02 (VERSION)
            if (param === 0x1F02) return GL_VERSION;
            // 0x8B8C (SHADING_LANGUAGE_VERSION)
            if (param === 0x8B8C) return GL_SHADING_LANG_VERSION;

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
                return origGetExtension.apply(this, arguments);
            }, 'getExtension');
        }

        if (origGetSupportedExtensions) {
            ctxProto.getSupportedExtensions = makeNative(function() {
                var list = origGetSupportedExtensions.apply(this, arguments) || [];
                var arr = Array.from(list);
                if (arr.indexOf('WEBGL_debug_renderer_info') === -1) {
                    arr.push('WEBGL_debug_renderer_info');
                }
                return arr;
            }, 'getSupportedExtensions');
        }
    }

    if (window.WebGLRenderingContext) {
        wrapContext(WebGLRenderingContext.prototype);
    }
    if (window.WebGL2RenderingContext) {
        wrapContext(WebGL2RenderingContext.prototype);
    }

    // --- 4. Battery & Thermal API Normalization ---
    var fakeBattery = {
        charging: false,
        chargingTime: Infinity,
        dischargingTime: 14400, // 4 hours at 85%
        level: 0.85,
        temperature: 28.5, // 28.5 C nominal mobile device operating temperature
        addEventListener: makeNative(function() {}, 'addEventListener'),
        removeEventListener: makeNative(function() {}, 'removeEventListener'),
        dispatchEvent: makeNative(function() { return false; }, 'dispatchEvent'),
        onchargingchange: null,
        onchargingtimechange: null,
        ondischargingtimechange: null,
        onlevelchange: null
    };

    if (navigator.getBattery) {
        navigator.getBattery = makeNative(function() {
            return Promise.resolve(fakeBattery);
        }, 'getBattery');
    } else {
        navigator.getBattery = makeNative(function() {
            return Promise.resolve(fakeBattery);
        }, 'getBattery');
    }

    // --- 5. Touch Events, Pointer Normalization & Hover Suppression ---
    // Physical mobile phones do NOT possess hovering cursors.
    // Passive mouse movement without an active touch contact is completely dropped.
    // Active mouse clicks/drags are masked to report as genuine capacitive touch events.
    try {
        if (!('ontouchstart' in window)) {
            window.ontouchstart = null;
            window.ontouchend = null;
            window.ontouchmove = null;
            window.ontouchcancel = null;
        }

        if (window.PointerEvent) {
            try {
                Object.defineProperty(PointerEvent.prototype, 'pointerType', {
                    get: makeNative(function() { return 'touch'; }, 'get pointerType'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'width', {
                    get: makeNative(function() { return 25; }, 'get width'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'height', {
                    get: makeNative(function() { return 25; }, 'get height'),
                    configurable: true,
                    enumerable: true
                });
                Object.defineProperty(PointerEvent.prototype, 'pressure', {
                    get: makeNative(function() { return this.buttons > 0 ? 0.5 : 0; }, 'get pressure'),
                    configurable: true,
                    enumerable: true
                });
            } catch (err) {}
        }

        // Global cursor suppression handler: drops hover when no button is pressed
        function suppressPassiveHover(e) {
            if (e.buttons === 0 && !e.__isSyntheticTouch__) {
                e.stopImmediatePropagation();
                e.stopPropagation();
                e.preventDefault();
                return false;
            }
            // Active click/touch: mask pointer type and dimensions to mobile touch standard
            if (e.buttons > 0) {
                try {
                    Object.defineProperty(e, 'pointerType', { get: function() { return 'touch'; }, configurable: true });
                    Object.defineProperty(e, 'pointerId', { get: function() { return 1; }, configurable: true });
                    Object.defineProperty(e, 'width', { get: function() { return 25; }, configurable: true });
                    Object.defineProperty(e, 'height', { get: function() { return 25; }, configurable: true });
                    Object.defineProperty(e, 'pressure', { get: function() { return 0.5; }, configurable: true });
                    Object.defineProperty(e, 'tangentialPressure', { get: function() { return 0; }, configurable: true });
                    Object.defineProperty(e, 'tiltX', { get: function() { return 0; }, configurable: true });
                    Object.defineProperty(e, 'tiltY', { get: function() { return 0; }, configurable: true });
                    Object.defineProperty(e, 'twist', { get: function() { return 0; }, configurable: true });
                } catch (err) {}
            }
        }

        var hoverEvents = ['mousemove', 'pointermove', 'mouseover', 'mouseenter', 'mouseout', 'mouseleave'];
        for (var h = 0; h < hoverEvents.length; h++) {
            window.addEventListener(hoverEvents[h], suppressPassiveHover, true); // Capture phase: intercepted before page scripts
            if (document) {
                document.addEventListener(hoverEvents[h], suppressPassiveHover, true);
            }
        }

        // Wrap EventTarget.prototype.addEventListener to guarantee no element callback catches hover
        var origAddEventListener = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = makeNative(function(type, listener, options) {
            if (typeof listener === 'function' && hoverEvents.indexOf(type) !== -1) {
                var wrappedListener = function(event) {
                    if (event.buttons === 0 && !event.__isSyntheticTouch__) {
                        return; // Ghost cursor dropped
                    }
                    return listener.apply(this, arguments);
                };
                return origAddEventListener.call(this, type, wrappedListener, options);
            }
            return origAddEventListener.apply(this, arguments);
        }, 'addEventListener');

        // Media queries for touch/hover
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

    // --- 6. Cordova Device Plugin Interception ---
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

    console.log('[disguise] Mobile phone environment disguise activated: Samsung SM-A515F (Mali-G76 MP12 / ARM, Touch Normalized, Hover Suppressed)');
})();

'use strict';

function init() {
	window.APPLICATION_NAME_DISPLAY = 'Dofus Touch';

	// to replace build's alert title "index.html"
	window.alert = function (msg) {
		window.navigator.notification.alert(msg, null, window.APPLICATION_NAME_DISPLAY, 'Ok');
	};
}
init();
