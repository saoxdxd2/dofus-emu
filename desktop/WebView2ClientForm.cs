using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Media;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace DofusFarm.Engine
{
    public class WebView2ClientForm : Form
    {
        static WebView2ClientForm()
        {
            try
            {
                // This executable has no app.config, so the CLR keeps its
                // legacy SecurityProtocol default (Ssl3 | Tls) and every
                // .NET fetch to the CDN dies with SecureChannelFailure
                // ("Could not create SSL/TLS secure channel"), which the
                // intercept surfaced as 502 Bad Gateway -> black maps.
                // CloudFront requires TLS 1.2, so opt into modern TLS once
                // for the whole process.
                ServicePointManager.SecurityProtocol =
                    (SecurityProtocolType)3072 | (SecurityProtocolType)768;
            }
            catch { }
        }

        private readonly ClientLaunchOptions _options;
        private readonly string _instanceId;
        private readonly string _displayName;
        private readonly string _userDataDir;
        private readonly string _entryUrl;
        private readonly Action<string> _logCallback;

        private CoreWebView2Environment _env;
        private OAuthLoginForm _activeOAuthDialog;

        // ---- CDN asset repair (WebResourceRequested) ----
        // Ankama's CDN hotlink-checks asset requests: XHRs from the virtual
        // origin https://dofus.local/ get 403 because the browser's CDN
        // cookies are cross-site and the Referer is not on the allowlist.
        // Fetched CDN assets are re-issued here via .NET with the full
        // WebView2 cookie jar (including HttpOnly/SameSite cookies) and the
        // official web-client Referer/Origin.
        private const string CdnReferer = "https://www.dofus-touch.com/";
        private const int CdnCookieTtlSeconds = 45;
        private static readonly Regex CdnAssetUriRe = new Regex(
            @"^https?://(?:[a-z0-9-]+\.)*cdn\.ankama(?:-games)?\.com/(?:assets|ui|export-web)/",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly string[] CdnCookieOrigins = new[]
        {
            "https://dofustouch.cdn.ankama.com/",
            "https://dofus-touch.com/",
            "https://www.dofus-touch.com/",
            "https://account.ankama.com/",
            "https://www.ankama.com/",
            "https://dt-proxy-production-login.ankama-games.com/"
        };
        private readonly object _cdnCookieLock = new object();
        private string _cdnCookieHeader = "";
        private DateTime _cdnCookieRefreshedUtc = DateTime.MinValue;
        private string _cleanUa;

        // Intercept telemetry: summed in the log to keep it flood-free but
        // still observable (a silent black screen produced zero diagnostics
        // the first time this shipped).
        private int _cdnInterceptTotal;
        private int _cdnInterceptOk;
        private int _cdnInterceptFail;
        private int _cdnInterceptLoggedErrors;
        private bool _cdnInterceptFirstOkLogged;

        private sealed class CdnFetchResult
        {
            public int Status;
            public string Reason;
            public MemoryStream Body;
            public string Headers;
        }

        private WebView2 _webView;
        private Panel _toolbarPanel;
        private Label _lblStatus;
        private Label _lblMemory;
        private Button _btnZoom;
        private Button _btnDevTools;
        private Button _btnReload;
        private System.Windows.Forms.Timer _telemetryTimer;

        private bool _autoZoomEnabled = true;
        private bool _isUpdatingZoom = false;
        private double _currentZoomFactor = 1.0;

        public string InstanceId { get { return _instanceId; } }
        public bool IsReady { get; private set; }
        public string LastGpuVendor { get; private set; }
        public string LastGpuRenderer { get; private set; }
        public int BrowserProcessId { get; private set; }
        public WebView2 BrowserControl { get { return _webView; } }
        internal CoreWebView2CookieManager MainCookieManager
        {
            get { return (_webView != null && _webView.CoreWebView2 != null) ? _webView.CoreWebView2.CookieManager : null; }
        }

        public async Task<string> ExecuteScriptAsync(string script)
        {
            if (_webView != null && _webView.CoreWebView2 != null)
            {
                return await _webView.CoreWebView2.ExecuteScriptAsync(script);
            }
            return null;
        }

        public async Task<bool> CaptureScreenshotAsync(string outputPath)
        {
            try
            {
                if (_webView != null && _webView.CoreWebView2 != null)
                {
                    using (FileStream fs = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.None))
                    {
                        await _webView.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
                        return true;
                    }
                }
            }
            catch { }
            return false;
        }

        public event Action<string, string> OnDiagnosticReport;
        public event Action<string> OnFormClosedByUser;

        // Embedded Cordova compatibility shim: emulates Cordova runtime so Dofus Touch / hybrid web assets run standalone
        private const string CordovaShimScript = @"(function() {
    'use strict';
    if (typeof window === 'undefined') return;

    if (!window.appInfo) {
        window.appInfo = {
            version: '3.14.2',
            identifier: 'com.ankama.dofustouch',
            build: '3.14.2',
            versionCode: '3014002'
        };
    }
    if (!window.buildVersion) {
        window.buildVersion = '1.74.8';
    }

    if (!window.IonicDeeplink) {
        window.IonicDeeplink = {
            _callbacks: [],
            route: function(paths, callback) {
                if (typeof callback === 'function') this._callbacks.push(callback);
            },
            onDeepLink: function(callback) {
                if (typeof callback === 'function') this._callbacks.push(callback);
            },
            fire: function(data) {
                for (var i = 0; i < this._callbacks.length; i++) {
                    try { this._callbacks[i](data); } catch (e) { console.error('[IonicDeeplink] Error:', e); }
                }
            }
        };
    }

    if (!window.__onDeepLinkCallback) {
        window.__onDeepLinkCallback = function(urlStr) {
            try {
                console.log('[Auth] Processing DeepLink callback URL:', urlStr);
                var safeUrl = urlStr || '';
                if (safeUrl.indexOf('login-authorized') !== -1 || (safeUrl.indexOf('code=') !== -1 && safeUrl.indexOf('dofustouch://') === -1)) {
                    var qIdx = safeUrl.indexOf('?');
                    var qs = qIdx !== -1 ? safeUrl.substring(qIdx) : '';
                    safeUrl = 'dofustouch://authorized' + qs;
                }
                var normalizedStr = safeUrl;
                safeUrl = safeUrl.replace('dofustouch://', 'https://dofus.local/');
                var parsedUrl = new URL(safeUrl);
                var host = 'authorized';
                var path = parsedUrl.pathname || '';
                var queryString = parsedUrl.search ? parsedUrl.search.substring(1) : '';

                var params = {};
                if (queryString) {
                    queryString.split('&').forEach(function(part) {
                        var kv = part.split('=');
                        if (kv.length >= 2) params[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1]);
                    });
                }

                var deepLinkData = { host: 'authorized', path: path, queryString: queryString, url: normalizedStr };
                if (window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function') {
                    window.IonicDeeplink.fire(deepLinkData);
                }
                if (window.dofus && window.dofus.webAuth && typeof window.dofus.webAuth.connectThroughIonicDeepLink === 'function') {
                    window.dofus.webAuth.connectThroughIonicDeepLink(params);
                }
                if (window.__webAuth && typeof window.__webAuth.connectThroughIonicDeepLink === 'function') {
                    window.__webAuth.connectThroughIonicDeepLink(params);
                }
            } catch (e) {
                console.error('[Auth] Failed to parse callback URL:', e);
            }
        };
    }

    try {
        var _cLog = console.log, _cWarn = console.warn, _cErr = console.error;
        console.log = function() {
            _cLog.apply(console, arguments);
            if (window.chrome && window.chrome.webview) {
                try { window.chrome.webview.postMessage({ type: 'CONSOLE_LOG', level: 'info', text: Array.prototype.slice.call(arguments).join(' ') }); } catch(e){}
            }
        };
        console.warn = function() {
            _cWarn.apply(console, arguments);
            if (window.chrome && window.chrome.webview) {
                try { window.chrome.webview.postMessage({ type: 'CONSOLE_LOG', level: 'warn', text: Array.prototype.slice.call(arguments).join(' ') }); } catch(e){}
            }
        };
        console.error = function() {
            _cErr.apply(console, arguments);
            if (window.chrome && window.chrome.webview) {
                try { window.chrome.webview.postMessage({ type: 'CONSOLE_LOG', level: 'error', text: Array.prototype.slice.call(arguments).join(' ') }); } catch(e){}
            }
        };

        window.addEventListener('error', function(evt) {
            if (window.chrome && window.chrome.webview) {
                var msg = evt.message || (evt.error && evt.error.message) || 'Unknown JS Error';
                var src = evt.filename || '';
                var line = evt.lineno || 0;
                var col = evt.colno || 0;
                var stack = (evt.error && evt.error.stack) ? ('\n' + evt.error.stack) : '';
                try {
                    window.chrome.webview.postMessage({
                        type: 'CONSOLE_LOG',
                        level: 'error',
                        text: '[UNCAUGHT-JS-ERROR] ' + msg + ' (' + src + ':' + line + ':' + col + ')' + stack
                    });
                } catch(e){}
            }
        });

        window.addEventListener('unhandledrejection', function(evt) {
            if (window.chrome && window.chrome.webview) {
                var reason = evt.reason;
                var msg = reason ? (reason.stack || reason.message || reason) : 'Unhandled rejection';
                try {
                    window.chrome.webview.postMessage({
                        type: 'CONSOLE_LOG',
                        level: 'error',
                        text: '[UNHANDLED-PROMISE-REJECTION] ' + msg
                    });
                } catch(e){}
            }
        });
    } catch(e) {}

    if (!window.__executeCommanderDirective) {
        window.__executeCommanderDirective = function(directiveData) {
            try {
                console.log('[AI Commander] Received directive:', directiveData);
                var evt = new CustomEvent('dofus_commander_action', { detail: directiveData });
                window.dispatchEvent(evt);
                if (window.dofus && window.dofus.commander && typeof window.dofus.commander.handleDirective === 'function') {
                    window.dofus.commander.handleDirective(directiveData);
                }
            } catch (e) {
                console.error('[AI Commander] Directive error:', e);
            }
        };
    }

    if (!window.__onCommanderReply) {
        window.__onCommanderReply = function(replyData) {
            try {
                console.log('[AI Commander] Received reply:', replyData);
                var evt = new CustomEvent('dofus_commander_reply', { detail: replyData });
                window.dispatchEvent(evt);
            } catch (e) {
                console.error('[AI Commander] Reply error:', e);
            }
        };
    }

    if (!window.__queryNLP) {
        window.__queryNLP = function(queryText) {
            if (window.chrome && window.chrome.webview) {
                window.chrome.webview.postMessage({ type: 'NLP_QUERY', text: queryText });
            }
        };
    }

    if (!window.__sendChatMessageToSentinel) {
        window.__sendChatMessageToSentinel = function(sender, text, isMod) {
            if (window.chrome && window.chrome.webview) {
                window.chrome.webview.postMessage({ type: 'CHAT_MESSAGE', sender: sender, text: text, isMod: !!isMod });
            }
        };
    }

    if (window.cordova) return;

    window.cordova = {
        platformId: 'android',
        version: '10.0.0',
        plugins: {
            browsertab: {
                isAvailable: function(cb) { if (cb) cb(true); },
                openUrl: function(url, success, error) {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage({ type: 'open_oauth', url: url });
                    }
                    if (typeof success === 'function') success();
                },
                close: function() {
                    if (window.chrome && window.chrome.webview) {
                        window.chrome.webview.postMessage({ type: 'close_oauth' });
                    }
                }
            }
        },
        InAppBrowser: {
            open: function(url, target, options) {
                if (window.chrome && window.chrome.webview) {
                    window.chrome.webview.postMessage({ type: 'open_oauth', url: url });
                }
                return {
                    addEventListener: function() {},
                    removeEventListener: function() {},
                    close: function() {
                        if (window.chrome && window.chrome.webview) {
                            window.chrome.webview.postMessage({ type: 'close_oauth' });
                        }
                    }
                };
            }
        },
        exec: function(success, fail, service, action, args) {
            if (service === 'Device' && action === 'getDeviceInfo') {
                if (typeof success === 'function') success(window.device || {});
                return;
            }
            if (service === 'Notification') {
                if (action === 'alert' || action === 'confirm') {
                    if (typeof success === 'function') success(1);
                    return;
                }
            }
            if (typeof success === 'function') {
                setTimeout(function() { success(); }, 0);
            }
        },
        require: function(id) {
            if (id === 'cordova/channel') return { onDeviceReady: { subscribe: function(cb) { setTimeout(cb, 10); } } };
            return null;
        },
        define: function() {}
    };

    function fireDeviceReady() {
        try {
            var evt = document.createEvent('Event');
            evt.initEvent('deviceready', false, false);
            document.dispatchEvent(evt);
        } catch(e) {}
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        setTimeout(fireDeviceReady, 20);
    } else {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(fireDeviceReady, 20); }, false);
    }
})();";

        public WebView2ClientForm(ClientLaunchOptions options, Action<string> logCallback)
        {
            _options = options ?? new ClientLaunchOptions();
            _instanceId = _options.InstanceId ?? "webview-01";
            _displayName = string.IsNullOrEmpty(_options.DisplayName) ? _instanceId : _options.DisplayName;
            _userDataDir = string.IsNullOrEmpty(_options.UserDataDirectory)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm", "webview2_profiles", _instanceId)
                : _options.UserDataDirectory;
            _entryUrl = _options.EntryUrl;
            _logCallback = logCallback;

            InitializeForm();
        }

        public WebView2ClientForm(string instanceId, string displayName, string userDataDir, string entryUrl, Action<string> logCallback)
            : this(new ClientLaunchOptions
            {
                InstanceId = instanceId,
                DisplayName = displayName,
                UserDataDirectory = userDataDir,
                EntryUrl = entryUrl
            }, logCallback)
        {
        }

        private void InitializeForm()
        {
            Text = string.Format("[WebView2] {0} - Dofus Farm", _displayName);
            Size = new Size(1024, 640);
            MinimumSize = new Size(360, 240);
            BackColor = Color.FromArgb(15, 23, 42);
            StartPosition = FormStartPosition.WindowsDefaultLocation;

            try
            {
                string icoPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");
                if (File.Exists(icoPath)) Icon = new Icon(icoPath);
            }
            catch { }

            // Top control bar
            _toolbarPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 32,
                BackColor = Color.FromArgb(30, 41, 59),
                Padding = new Padding(8, 4, 8, 4)
            };

            _lblStatus = new Label
            {
                Text = "⚡ Initializing WebView2 runtime...",
                ForeColor = Color.FromArgb(56, 189, 248),
                Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                AutoSize = true,
                Location = new Point(8, 7)
            };
            _toolbarPanel.Controls.Add(_lblStatus);

            _lblMemory = new Label
            {
                Text = "RAM: -- MB",
                ForeColor = Color.FromArgb(148, 163, 184),
                Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                AutoSize = true,
                Location = new Point(320, 7)
            };
            _toolbarPanel.Controls.Add(_lblMemory);

            _btnZoom = new Button
            {
                Text = "🔍 Auto",
                Size = new Size(95, 24),
                Location = new Point(Width - 275, 4),
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnZoom.FlatAppearance.BorderSize = 0;
            _btnZoom.Click += (s, e) => CycleZoomMode();
            _toolbarPanel.Controls.Add(_btnZoom);

            _btnDevTools = new Button
            {
                Text = "🛠️ DevTools",
                Size = new Size(84, 24),
                Location = new Point(Width - 175, 4),
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(51, 65, 85),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 8.25f),
                Cursor = Cursors.Hand
            };
            _btnDevTools.FlatAppearance.BorderSize = 0;
            _btnDevTools.Click += (s, e) => { if (IsReady && _webView != null && _webView.CoreWebView2 != null) _webView.CoreWebView2.OpenDevToolsWindow(); };
            _toolbarPanel.Controls.Add(_btnDevTools);

            _btnReload = new Button
            {
                Text = "🔄 Reload",
                Size = new Size(76, 24),
                Location = new Point(Width - 86, 4),
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnReload.FlatAppearance.BorderSize = 0;
            _btnReload.Click += async (s, e) =>
            {
                if (IsReady && _webView != null && _webView.CoreWebView2 != null)
                {
                    try { await _webView.CoreWebView2.Profile.ClearBrowsingDataAsync(CoreWebView2BrowsingDataKinds.DiskCache | CoreWebView2BrowsingDataKinds.CacheStorage); } catch { }
                    _webView.Reload();
                }
            };
            _toolbarPanel.Controls.Add(_btnReload);

            Controls.Add(_toolbarPanel);

            // WebView2 browser control
            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(15, 23, 42)
            };
            Controls.Add(_webView);
            _webView.BringToFront();

            // Telemetry timer (measures memory and CPU)
            _telemetryTimer = new System.Windows.Forms.Timer { Interval = 2000 };
            _telemetryTimer.Tick += (s, e) => UpdateMemoryUsage();
            _telemetryTimer.Start();
        }

        public async Task StartAsync()
        {
            try
            {
                _logCallback?.Invoke(string.Format("[{0}] Preparing WebView2 profile at: {1}", _instanceId, _userDataDir));
                Directory.CreateDirectory(_userDataDir);

                // Resolve the standalone bundle root once; used for the virtual
                // host mapping and for the CDN relay shim injection below.
                string clientDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop", "client", "www");
                if (!Directory.Exists(clientDir)) clientDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "client", "www");
                if (!Directory.Exists(clientDir)) clientDir = Path.Combine(Directory.GetCurrentDirectory(), "desktop", "client", "www");

                // Configure Chromium switches
                var envOptions = new CoreWebView2EnvironmentOptions();
                var args = new StringBuilder();

                string ua = (_options.Profile != null && !string.IsNullOrEmpty(_options.Profile.UserAgent))
                    ? _options.Profile.UserAgent
                    : null;
                if (!string.IsNullOrEmpty(ua))
                {
                    // Clean UserAgent to remove embedded WebView flags (; wv and Version/X.X) that trigger Google disallowed_useragent
                    ua = Regex.Replace(ua, @";\s*wv\b", "", RegexOptions.IgnoreCase);
                    ua = Regex.Replace(ua, @"Version/[0-9.]+\s*", "", RegexOptions.IgnoreCase).Trim();
                    args.AppendFormat("--user-agent=\"{0}\" ", ua);
                }
                _cleanUa = ua;

                // Local Go proxy (relay + shared asset cache + hotlink-safe
                // headers). When mainform wired a ProxyPort, all WebView2
                // traffic goes through it; Chromium implicitly bypasses
                // loopback, so the relay endpoint on 127.0.0.1 stays direct.
                if (_options.ProxyPort > 0)
                {
                    args.AppendFormat("--proxy-server=\"http://127.0.0.1:{0}\" ", _options.ProxyPort);
                }
                // Proxy integration: default to pure DIRECT network connection
                // Never hijack default traffic through local accelerators or tunnels
                else if (!string.IsNullOrEmpty(_options.Proxy) &&
                    !_options.Proxy.Contains("DIRECT") &&
                    !_options.Proxy.Contains("[⚡]") &&
                    !_options.Proxy.Contains("Accelerator") &&
                    !_options.Proxy.Contains("127.0.0.1:8880"))
                {
                    string rawProxy = _options.Proxy.Trim();
                    if (rawProxy.StartsWith("[🌐]")) rawProxy = rawProxy.Substring(4).Trim();
                    var m = Regex.Match(rawProxy, @"(?:https?://)?([0-9a-zA-Z.-]+:[0-9]+)");
                    if (m.Success)
                    {
                        args.AppendFormat("--proxy-server=\"http://{0}\" ", m.Groups[1].Value);
                    }
                }

                // Performance & GPU rendering flags
                args.Append("--enable-gpu-rasterization --ignore-gpu-blocklist --use-angle=d3d11 --enable-webgl --enable-accelerated-2d-canvas ");

                // Native mobile touch and automation suppression switches
                args.Append("--touch-events=enabled --disable-blink-features=AutomationControlled ");

                // Allow cross-origin requests from virtual host https://dofus.local to Ankama Auth & CDN
                args.Append("--disable-web-security --allow-running-insecure-content ");

                // Background CPU optimization: Chromium throttles background-window timers by
                // default; only opt out when the user disabled background throttling. The old
                // code appended the disable switches with "=false" (Chromium switches are
                // presence-only), so the option never actually changed anything.
                if (!_options.EnableBackgroundThrottling)
                {
                    args.Append("--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-features=CalculateNativeWinOcclusion ");
                }

                envOptions.AdditionalBrowserArguments = args.ToString().Trim();

                var env = await CoreWebView2Environment.CreateAsync(null, _userDataDir, envOptions);
                await _webView.EnsureCoreWebView2Async(env);
                _env = env;

                // Enable native desktop mouse-to-touch emulation so drag/slide/drawers and map panning work seamlessly
                try
                {
                    await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setTouchEmulationEnabled", "{\"enabled\":true,\"maxTouchPoints\":5}");
                    await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setEmitTouchEventsForMouse", "{\"enabled\":true,\"configuration\":\"mobile\"}");
                    await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Network.setCacheDisabled", "{\"cacheDisabled\":true}");
                    _logCallback?.Invoke(string.Format("[{0}] Enabled native mobile touch emulation and disabled HTTP cache for fresh assets.", _instanceId));
                }
                catch (Exception ex)
                {
                    _logCallback?.Invoke(string.Format("[{0}] Touch emulation setup warning: {1}", _instanceId, ex.Message));
                }

                BrowserProcessId = (int)_webView.CoreWebView2.BrowserProcessId;

                // Security & runtime features
                var settings = _webView.CoreWebView2.Settings;
                settings.IsWebMessageEnabled = true;
                settings.AreDevToolsEnabled = true;
                settings.IsStatusBarEnabled = false;
                settings.AreDefaultContextMenusEnabled = true;
                settings.IsScriptEnabled = true;
                settings.IsZoomControlEnabled = true;
                settings.IsPinchZoomEnabled = true;

                if (!string.IsNullOrEmpty(ua))
                {
                    settings.UserAgent = ua;
                }

                // 0. Ensure dynamic version is resolved
                if (string.IsNullOrEmpty(_options.AppVersion))
                {
                    var vi = DynamicVersionResolver.ResolveVersion(null, false, msg => _logCallback?.Invoke(msg));
                    _options.AppVersion = vi.AppVersion;
                    _options.BuildVersion = vi.BuildVersion;
                    _options.VersionCode = vi.VersionCode;
                }

                // 1. Synchronously inject early dynamic version state
                string dynamicVerScript = string.Format(
                    "(function() {{ window.appInfo = window.appInfo || {{}}; window.appInfo.version = '{0}'; window.appInfo.build = '{0}'; window.appInfo.versionCode = '{1}'; window.appInfo.identifier = 'com.ankama.dofustouch'; window.buildVersion = '{2}'; }})();",
                    _options.AppVersion, _options.VersionCode, _options.BuildVersion);
                await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(dynamicVerScript);

                // 2. Synchronously inject Mobile Disguise script (closes the blackbox completely)
                if (!string.IsNullOrEmpty(_options.DisguiseScript))
                {
                    await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(_options.DisguiseScript);
                    _logCallback?.Invoke(string.Format("[{0}] Registered mobile spoofing disguise script in CoreWebView2.", _instanceId));
                }

                // 3. Synchronously inject Cordova compatibility polyfill
                await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(CordovaShimScript);

                // 3b. CDN relay: expose the local relay config, then inject the
                // shim that rewrites CDN asset URLs through it. Inert when no
                // ProxyPort is wired or when the page ships its own copy
                // (double-load is guarded inside the shim).
                string relayCfgScript = string.Format(
                    "(function() {{ window.__DOFUS_CDN_RELAY = {{ enabled: {0}, port: {1}, referer: \"{2}\" }}; }})();",
                    (_options.ProxyPort > 0) ? "true" : "false", _options.ProxyPort, CdnReferer);
                await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(relayCfgScript);
                try
                {
                    string cdnShimPath = Path.Combine(clientDir, "js", "cdn-relay-shim.js");
                    if (File.Exists(cdnShimPath))
                    {
                        await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(File.ReadAllText(cdnShimPath));
                        _logCallback?.Invoke(string.Format("[{0}] Registered CDN relay shim (relay port: {1}).", _instanceId, _options.ProxyPort));
                    }
                }
                catch (Exception shimEx)
                {
                    _logCallback?.Invoke(string.Format("[{0}] CDN relay shim injection warning: {1}", _instanceId, shimEx.Message));
                }

                // 4. Map virtual origin https://dofus.local/ to standalone game bundle
                if (Directory.Exists(clientDir))
                {
                    _webView.CoreWebView2.SetVirtualHostNameToFolderMapping("dofus.local", clientDir, CoreWebView2HostResourceAccessKind.Allow);
                    _logCallback?.Invoke(string.Format("[{0}] Mapped virtual origin https://dofus.local/ -> {1}", _instanceId, clientDir));
                }

                // Wire event handlers
                _webView.CoreWebView2.NavigationStarting += OnNavigationStarting;
                _webView.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
                _webView.CoreWebView2.ProcessFailed += OnProcessFailed;
                _webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
                _webView.CoreWebView2.NewWindowRequested += OnNewWindowRequested;

                // Wire WebResourceRequested filter for the Ankama CDN asset
                // repair path (cookie + referer fix).
                try
                {
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://*.cdn.ankama.com/*", CoreWebView2WebResourceContext.All);
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://*.cdn.ankama-games.com/*", CoreWebView2WebResourceContext.All);
                }
                catch (Exception filtEx)
                {
                    _logCallback?.Invoke(string.Format("[{0}] CDN filter registration warning: {1}", _instanceId, filtEx.Message));
                }
                _webView.CoreWebView2.WebResourceRequested += OnWebResourceRequested;



                _webView.ZoomFactorChanged += (s, e) =>
                {
                    if (_isUpdatingZoom) return;
                    if (Math.Abs(_webView.ZoomFactor - _currentZoomFactor) > 0.02)
                    {
                        _currentZoomFactor = _webView.ZoomFactor;
                        _autoZoomEnabled = false;
                    }
                    UpdateZoomButtonText();
                };

                IsReady = true;
                _lblStatus.Text = string.Format("⚡ WebView2 Engine Active ({0})", _options.Profile != null ? _options.Profile.DisplayName : "Direct");
                _lblStatus.ForeColor = Color.FromArgb(74, 222, 128);

                if (_autoZoomEnabled)
                {
                    ApplyAutoZoom();
                }

                _logCallback?.Invoke(string.Format("[{0}] WebView2 Core initialized successfully (Version: {1}, Browser PID: {2})",
                    _instanceId, env.BrowserVersionString, BrowserProcessId));

                // Navigate to target
                string targetUrl = _entryUrl;
                if (string.IsNullOrEmpty(targetUrl))
                {
                    if (Directory.Exists(clientDir) && File.Exists(Path.Combine(clientDir, "standalone.html")))
                    {
                        targetUrl = "https://dofus.local/standalone.html";
                    }
                }

                if (!string.IsNullOrEmpty(targetUrl))
                {
                    if (targetUrl.Contains("standalone.html") && !targetUrl.Contains("appVersion="))
                    {
                        string sep = targetUrl.Contains("?") ? "&" : "?";
                        targetUrl += string.Format("{0}appVersion={1}&buildVersion={2}&versionCode={3}",
                            sep,
                            Uri.EscapeDataString(_options.AppVersion),
                            Uri.EscapeDataString(_options.BuildVersion),
                            Uri.EscapeDataString(_options.VersionCode));
                    }

                    _logCallback?.Invoke(string.Format("[{0}] Navigating to: {1}", _instanceId, targetUrl));
                    _webView.CoreWebView2.Navigate(targetUrl);
                }
            }
            catch (Exception ex)
            {
                IsReady = false;
                _lblStatus.Text = "❌ Initialization Failed";
                _lblStatus.ForeColor = Color.FromArgb(248, 113, 113);
                _logCallback?.Invoke(string.Format("[{0}] WebView2 Initialization Error: {1}", _instanceId, ex.Message));
                MessageBox.Show(
                    "WebView2 runtime failed to initialize:\n\n" + ex.Message,
                    "WebView2 Initialization Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }

        protected override void OnActivated(EventArgs e)
        {
            base.OnActivated(e);
            if (IsReady && _webView != null && _webView.CoreWebView2 != null)
            {
                try { _webView.CoreWebView2.PostWebMessageAsJson("{\"type\":\"window_focus\",\"active\":true}"); } catch { }
            }
        }

        protected override void OnDeactivate(EventArgs e)
        {
            base.OnDeactivate(e);
            if (IsReady && _webView != null && _webView.CoreWebView2 != null)
            {
                try { _webView.CoreWebView2.PostWebMessageAsJson("{\"type\":\"window_focus\",\"active\":false}"); } catch { }
            }
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            if (_lblMemory != null)
            {
                _lblMemory.Visible = (Width >= 620);
            }

            if (IsReady && _webView != null && _webView.CoreWebView2 != null)
            {
                if (_autoZoomEnabled)
                {
                    ApplyAutoZoom();
                }
                TriggerGameResize();

                BeginInvoke(new Action(async () =>
                {
                    await Task.Delay(150);
                    TriggerGameResize();
                }));
            }
        }

        private void OnNavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            if (e.Uri.StartsWith("dofustouch://", StringComparison.OrdinalIgnoreCase) ||
                (e.Uri.Contains("login-authorized") && (e.Uri.Contains("code=") || e.Uri.Contains("error="))))
            {
                e.Cancel = true;
                _logCallback?.Invoke(string.Format("[{0}] Intercepted Ankama Connect DeepLink in client: {1}", _instanceId, e.Uri));
                HandleDeepLinkUri(e.Uri);
                return;
            }

            if (!e.Uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
                !e.Uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase) &&
                !e.Uri.StartsWith("file://", StringComparison.OrdinalIgnoreCase))
            {
                _logCallback?.Invoke(string.Format("[{0}] Blocked disallowed navigation scheme: {1}", _instanceId, e.Uri));
                e.Cancel = true;
            }
        }

        private void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (e.IsSuccess)
            {
                _logCallback?.Invoke(string.Format("[{0}] Navigation completed successfully (Status: {1})", _instanceId, e.HttpStatusCode));
                if (_autoZoomEnabled)
                {
                    ApplyAutoZoom();
                }
                try
                {
                    _webView.CoreWebView2.ExecuteScriptAsync(
                        "setTimeout(function(){ if(window.gui&&typeof window.gui._resizeUi==='function'){window.gui._resizeUi();}window.dispatchEvent(new Event('resize')); }, 1500);"
                    );
                }
                catch { }
            }
            else
            {
                _logCallback?.Invoke(string.Format("[{0}] Navigation failed: {1} (Status: {2})", _instanceId, e.WebErrorStatus, e.HttpStatusCode));
            }
        }

        private void OnProcessFailed(object sender, CoreWebView2ProcessFailedEventArgs e)
        {
            _logCallback?.Invoke(string.Format("[{0}] ⚠️ WebView2 Process Failed! Kind: {1}, Reason: {2}",
                _instanceId, e.ProcessFailedKind, e.Reason));

            if (e.ProcessFailedKind == CoreWebView2ProcessFailedKind.BrowserProcessExited)
            {
                _lblStatus.Text = "❌ Browser Crashed";
                _lblStatus.ForeColor = Color.FromArgb(248, 113, 113);
            }
            else if (e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessExited ||
                     e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessUnresponsive)
            {
                _lblStatus.Text = "⚠️ Renderer Restarting...";
                _lblStatus.ForeColor = Color.FromArgb(250, 204, 21);
                _webView.Reload();
            }
        }

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                string json = e.WebMessageAsJson;
                if (!json.Contains("\"type\":\"CONSOLE_LOG\""))
                {
                    _logCallback?.Invoke(string.Format("[{0}] [JS->C# Message]: {1}", _instanceId, json));
                }

                if (json.Contains("\"type\":\"CONSOLE_LOG\""))
                {
                    Match mText = Regex.Match(json, "\"text\"\\s*:\\s*\"((?:\\\\\"|[^\"])*)\"");
                    Match mLvl = Regex.Match(json, "\"level\"\\s*:\\s*\"([^\"]+)\"");
                    string text = mText.Success ? Regex.Unescape(mText.Groups[1].Value) : json;
                    string lvl = mLvl.Success ? mLvl.Groups[1].Value.ToUpper() : "JS";
                    _logCallback?.Invoke(string.Format("[{0}] [WEB-{1}] {2}", _instanceId, lvl, text));
                }
                else if (json.Contains("\"type\":\"ping\""))
                {
                    _webView.CoreWebView2.PostWebMessageAsJson("{\"type\":\"pong\",\"reply\":\"Hello from C# WinForms Host!\"}");
                }
                else if (json.Contains("\"type\":\"diagnostics_report\""))
                {
                    OnDiagnosticReport?.Invoke(_instanceId, json);
                }
                else if (json.Contains("\"type\":\"open_oauth\""))
                {
                    Match m = Regex.Match(json, "\"url\"\\s*:\\s*\"([^\"]+)\"");
                    if (m.Success)
                    {
                        string oauthUrl = Regex.Unescape(m.Groups[1].Value);
                        OpenOAuthDialog(oauthUrl);
                    }
                }
                else if (json.Contains("\"type\":\"close_oauth\""))
                {
                    CloseOAuthDialog();
                }
                else if (json.Contains("\"type\":\"MOD_WHISPER_ALERT\""))
                {
                    HandleModWhisperAlert(json);
                }
                else if (json.Contains("\"type\":\"NLP_QUERY\"") || json.Contains("\"type\":\"CHAT_DIRECTIVE\""))
                {
                    HandleNlpQueryMessage(json);
                }
                else if (json.Contains("\"type\":\"CHAT_MESSAGE\""))
                {
                    HandleInGameChatMessage(json);
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] WebMessage handling error: {1}", _instanceId, ex.Message));
            }
        }

        [DllImport("user32.dll")]
        private static extern bool FlashWindow(IntPtr hwnd, bool bInvert);

        private void HandleModWhisperAlert(string json)
        {
            try
            {
                Match mSender = Regex.Match(json, "\"sender\"\\s*:\\s*\"([^\"]+)\"");
                Match mText = Regex.Match(json, "\"text\"\\s*:\\s*\"([^\"]+)\"");
                bool isMod = json.Contains("\"isMod\":true");
                string sender = mSender.Success ? Regex.Unescape(mSender.Groups[1].Value) : "Unknown";
                string text = mText.Success ? Regex.Unescape(mText.Groups[1].Value) : "";

                _logCallback?.Invoke(string.Format("🚨 [{0}] {1} from '{2}': \"{3}\"",
                    _instanceId, isMod ? "CRITICAL MODERATOR PM" : "Private Message", sender, text));

                if (InvokeRequired)
                {
                    BeginInvoke(new Action(() => TriggerVisualAlert(sender, text, isMod)));
                }
                else
                {
                    TriggerVisualAlert(sender, text, isMod);
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] Error parsing MOD_WHISPER_ALERT: {1}", _instanceId, ex.Message));
            }
        }

        private void TriggerVisualAlert(string sender, string text, bool isMod)
        {
            try
            {
                FlashWindow(Handle, true);
                SystemSounds.Exclamation.Play();

                if (isMod)
                {
                    TopMost = true;
                    TopMost = false;
                    BringToFront();
                }

                if (_lblStatus != null)
                {
                    _lblStatus.Text = string.Format("⚠️ [{0}] {1}: {2}", isMod ? "MOD ALERT" : "WHISPER", sender, text.Length > 40 ? text.Substring(0, 40) + "..." : text);
                    _lblStatus.ForeColor = isMod ? Color.FromArgb(239, 68, 68) : Color.FromArgb(56, 189, 248);
                }

                // Dispatch to Discord Webhook if configured
                string webhookUrl = Environment.GetEnvironmentVariable("DOFUS_MOD_WEBHOOK");
                if (!string.IsNullOrEmpty(webhookUrl))
                {
                    Task.Run(() => DispatchDiscordAlert(webhookUrl, _instanceId, _displayName, sender, text, isMod));
                }

                // Query central AI Sentinel (singleton THX-01) for threat triage
                Task.Run(() => QueryCentralSentinel(sender, text, isMod));
            }
            catch { }
        }

        private void QueryCentralSentinel(string sender, string text, bool isMod)
        {
            try
            {
                using (var client = new System.Net.WebClient())
                {
                    client.Headers[System.Net.HttpRequestHeader.ContentType] = "application/json";
                    string escapedSender = sender.Replace("\"", "\\\"");
                    string escapedText = text.Replace("\"", "\\\"");
                    string payload = string.Format("{{\"instance_id\":\"{0}\",\"sender\":\"{1}\",\"text\":\"{2}\",\"is_mod\":{3}}}",
                        _instanceId, escapedSender, escapedText, isMod ? "true" : "false");
                    string response = client.UploadString("http://127.0.0.1:8890/sentinel/eval_chat", "POST", payload);
                    if (!string.IsNullOrEmpty(response) && response.Contains("\"is_threat\":true"))
                    {
                        Match mUrgency = Regex.Match(response, "\"urgency\"\\s*:\\s*\"([^\"]+)\"");
                        string urgency = mUrgency.Success ? mUrgency.Groups[1].Value : "HIGH";
                        _logCallback?.Invoke(string.Format("🧠 [{0}] Central THX-01 Sentinel flagged threat ({1}): \"{2}\"", _instanceId, urgency, text));
                    }
                }
            }
            catch { }
        }

        private static void DispatchDiscordAlert(string webhookUrl, string instanceId, string displayName, string sender, string text, bool isMod)
        {
            try
            {
                using (var client = new System.Net.WebClient())
                {
                    client.Headers[System.Net.HttpRequestHeader.ContentType] = "application/json";
                    string escapedSender = sender.Replace("\"", "\\\"");
                    string escapedText = text.Replace("\"", "\\\"");
                    string color = isMod ? "15158332" : "3447003"; // Red or Blue
                    string title = isMod ? "🚨 MODERATOR IN-GAME ALERT" : "💬 In-Game Private Message";
                    string payload = string.Format(
                        "{{\"embeds\":[{{\"title\":\"{0}\",\"description\":\"**Account**: {1} ({2})\\n**Sender**: {3}\\n**Message**: {4}\",\"color\":{5}}}]}}",
                        title, instanceId, displayName, escapedSender, escapedText, color);
                    client.UploadString(webhookUrl, "POST", payload);
                }
            }
            catch { }
        }

        private void SafeUiInvoke(Action action)
        {
            if (IsDisposed) return;
            try
            {
                if (InvokeRequired) BeginInvoke(action);
                else action();
            }
            catch { }
        }

        private void HandleNlpQueryMessage(string json)
        {
            try
            {
                Match mText = Regex.Match(json, "\"text\"\\s*:\\s*\"([^\"]+)\"");
                string queryText = mText.Success ? Regex.Unescape(mText.Groups[1].Value) : "";
                if (string.IsNullOrWhiteSpace(queryText)) return;

                _logCallback?.Invoke(string.Format("🤖 [{0}] In-Game NLP Query: \"{1}\"", _instanceId, queryText));

                System.Threading.ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        using (var client = new System.Net.WebClient())
                        {
                            client.Headers[System.Net.HttpRequestHeader.ContentType] = "application/json";
                            client.Encoding = Encoding.UTF8;
                            string escapedText = queryText.Replace("\"", "\\\"");
                            string payload = string.Format("{{\"command\":\"{0}\",\"target_slot\":\"{1}\"}}", escapedText, _instanceId);
                            string response = client.UploadString("http://127.0.0.1:8890/sentinel/parse_command", "POST", payload);

                            if (!string.IsNullOrEmpty(response))
                            {
                                SafeUiInvoke(() =>
                                {
                                    Match mDir = Regex.Match(response, "\"directive\"\\s*:\\s*\"([^\"]+)\"");
                                    string directive = mDir.Success ? mDir.Groups[1].Value : "ACTION";
                                    Match mExp = Regex.Match(response, "\"explanation\"\\s*:\\s*\"([^\"]+)\"");
                                    string exp = mExp.Success ? Regex.Unescape(mExp.Groups[1].Value) : queryText;

                                    if (_lblStatus != null && !IsDisposed)
                                    {
                                        _lblStatus.Text = string.Format("🤖 [{0}] {1}", directive, exp.Length > 40 ? exp.Substring(0, 40) + "..." : exp);
                                        _lblStatus.ForeColor = Color.FromArgb(168, 85, 247);
                                    }

                                    if (_webView != null && _webView.CoreWebView2 != null)
                                    {
                                        _webView.CoreWebView2.PostWebMessageAsJson(string.Format(
                                            "{{\"type\":\"NLP_RESPONSE\",\"instance_id\":\"{0}\",\"response\":{1}}}",
                                            _instanceId, response));

                                        _webView.CoreWebView2.ExecuteScriptAsync(string.Format(
                                            "window.__onCommanderReply && window.__onCommanderReply({0});", response));
                                    }

                                    _logCallback?.Invoke(string.Format("🤖 [{0}] AI Commander reply: [{1}] {2}", _instanceId, directive, exp));
                                });
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        _logCallback?.Invoke(string.Format("[{0}] NLP query failed: {1}", _instanceId, ex.Message));
                    }
                });
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] Error in HandleNlpQueryMessage: {1}", _instanceId, ex.Message));
            }
        }

        private void HandleInGameChatMessage(string json)
        {
            try
            {
                Match mSender = Regex.Match(json, "\"sender\"\\s*:\\s*\"([^\"]+)\"");
                Match mText = Regex.Match(json, "\"text\"\\s*:\\s*\"([^\"]+)\"");
                bool isMod = json.Contains("\"isMod\":true") || json.Contains("\"is_mod\":true");
                string sender = mSender.Success ? Regex.Unescape(mSender.Groups[1].Value) : "Unknown";
                string text = mText.Success ? Regex.Unescape(mText.Groups[1].Value) : "";

                if (isMod)
                {
                    HandleModWhisperAlert(json);
                }
                else
                {
                    _logCallback?.Invoke(string.Format("💬 [{0}] In-game message from '{1}': \"{2}\"", _instanceId, sender, text));
                    string low = text.ToLowerInvariant();
                    if (low.StartsWith("!") || low.StartsWith("/") || low.Contains("va en") || low.Contains("suis") || low.Contains("passe") || low.Contains("farm") || low.Contains("join") || low.Contains("stop"))
                    {
                        HandleNlpQueryMessage(string.Format("{{\"text\":\"{0}\"}}", text.Replace("\"", "\\\"")));
                    }
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] Error in HandleInGameChatMessage: {1}", _instanceId, ex.Message));
            }
        }

        private void OnNewWindowRequested(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            e.Handled = true;
            string targetUrl = e.Uri;
            _logCallback?.Invoke(string.Format("[{0}] Intercepted new window request: {1}", _instanceId, targetUrl));
            OpenOAuthDialog(targetUrl);
        }

        private void OnWebResourceRequested(object sender, CoreWebView2WebResourceRequestedEventArgs e)
        {
            try
            {
                string uri = e.Request.Uri;

                // 1. Ankama CDN assets: re-issue the request from .NET with the
                // full WebView2 cookie jar (HttpOnly/SameSite included) and the
                // official web-client Referer/Origin. This repairs the CDN 403s
                // that black-screen the client on map/asset loads.
                if (!CdnAssetUriRe.IsMatch(uri)) return;
                string method = e.Request.Method ?? "GET";
                bool isGet = method.Equals("GET", StringComparison.OrdinalIgnoreCase);
                bool isHead = method.Equals("HEAD", StringComparison.OrdinalIgnoreCase);
                bool isOptions = method.Equals("OPTIONS", StringComparison.OrdinalIgnoreCase);
                if (!isGet && !isHead && !isOptions) return;

                if (isOptions)
                {
                    e.Response = _env.CreateWebResourceResponse(
                        new MemoryStream(new byte[0]), 204, "No Content",
                        "Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, HEAD, OPTIONS\r\nAccess-Control-Allow-Headers: *\r\n"
                    );
                    return;
                }

                // NOTE: CoreWebView2HttpRequestHeaders.GetHeader THROWS
                // 0x80070490 ("Element not found") when the header is absent
                // instead of returning an empty string - verified against
                // runtime 155.0.4283.62. Always go through SafeGetHeader.
                string range = SafeGetHeader(e.Request, "Range");
                string inboundCookie = SafeGetHeader(e.Request, "Cookie");
                string requestUri = uri;
                var deferral = e.GetDeferral();
                _cdnInterceptTotal++;

                Task.Run(async () =>
                {
                    int status = 502;
                    string reason = "Bad Gateway";
                    MemoryStream body = null;
                    string headerBlock = "Access-Control-Allow-Origin: *\r\nContent-Length: 0\r\n";
                    try
                    {
                        string cookies = inboundCookie;
                        if (string.IsNullOrEmpty(cookies))
                        {
                            try { cookies = await RunOnUiThreadAsync(GetCdnCookieHeaderAsync); }
                            catch { }
                        }
                        CdnFetchResult result = FetchCdnAsset(requestUri, method, range, cookies);
                        status = result.Status;
                        reason = result.Reason;
                        body = result.Body;
                        headerBlock = result.Headers;
                        if (status >= 200 && status < 400)
                        {
                            _cdnInterceptOk++;
                            if (!_cdnInterceptFirstOkLogged)
                            {
                                _cdnInterceptFirstOkLogged = true;
                                _logCallback?.Invoke(string.Format(
                                    "[{0}] CDN intercept live: first asset {1} ({2}B, cookies={3})",
                                    _instanceId, status, body != null ? body.Length : 0,
                                    string.IsNullOrEmpty(cookies) ? "none" : cookies.Length + "c"));
                            }
                        }
                        else
                        {
                            _cdnInterceptFail++;
                            if (_cdnInterceptFail <= 5)
                                _logCallback?.Invoke(string.Format("[{0}] CDN intercept {1} ({4}) for {2} (cookies={3})",
                                    _instanceId, status, requestUri, string.IsNullOrEmpty(cookies) ? "none" : cookies.Length + "c", reason));
                        }
                    }
                    catch (Exception ex)
                    {
                        _cdnInterceptFail++;
                        if (_cdnInterceptFail <= 5)
                            _logCallback?.Invoke(string.Format("[{0}] CDN fetch failed ({1}): {2}", _instanceId, requestUri, ex.Message));
                    }
                    try
                    {
                        MemoryStream payload = body ?? new MemoryStream(new byte[0]);
                        int st = status;
                        string rs = reason;
                        string hb = headerBlock;
                        await RunOnUiThreadAsync(delegate
                        {
                            try { e.Response = _env.CreateWebResourceResponse(payload, st, rs, hb); } catch { }
                        });
                    }
                    catch { }
                    finally
                    {
                        try { deferral.Complete(); } catch { }
                    }
                });
            }
            catch (Exception ex)
            {
                _cdnInterceptLoggedErrors++;
                if (_cdnInterceptLoggedErrors <= 3 || _cdnInterceptLoggedErrors % 100 == 0)
                    _logCallback?.Invoke(string.Format("[{0}] WebResourceRequested error #{1}: {2}",
                        _instanceId, _cdnInterceptLoggedErrors, ex.Message));
            }
        }

        private static string SafeGetHeader(CoreWebView2WebResourceRequest request, string name)
        {
            try { return request.Headers.GetHeader(name); }
            catch { return null; }
        }

        private Task RunOnUiThreadAsync(Action action)
        {
            var tcs = new TaskCompletionSource<bool>();
            try
            {
                BeginInvoke((Action)delegate
                {
                    try { action(); tcs.TrySetResult(true); }
                    catch (Exception ex) { tcs.TrySetException(ex); }
                });
            }
            catch (Exception ex) { tcs.TrySetException(ex); }
            return tcs.Task;
        }

        private Task<T> RunOnUiThreadAsync<T>(Func<Task<T>> func)
        {
            var tcs = new TaskCompletionSource<T>();
            try
            {
                BeginInvoke((Action)(async delegate
                {
                    try { tcs.TrySetResult(await func()); }
                    catch (Exception ex) { tcs.TrySetException(ex); }
                }));
            }
            catch (Exception ex) { tcs.TrySetException(ex); }
            return tcs.Task;
        }

        // Collects cookies for every origin that participates in the CDN
        // handshake (the CDN itself plus Ankama auth domains), deduped by
        // name@domain and skipping expired ones. Cached for a few seconds so
        // an asset burst costs a single cookie-jar round trip. Must run on the
        // UI thread (CookieManager is a CoreWebView2 member).
        private async Task<string> GetCdnCookieHeaderAsync()
        {
            if ((DateTime.UtcNow - _cdnCookieRefreshedUtc).TotalSeconds < CdnCookieTtlSeconds)
                return _cdnCookieHeader;

            var sb = new StringBuilder(256);
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (string origin in CdnCookieOrigins)
            {
                List<CoreWebView2Cookie> cookies;
                try { cookies = await _webView.CoreWebView2.CookieManager.GetCookiesAsync(origin); }
                catch { continue; }
                if (cookies == null) continue;
                foreach (CoreWebView2Cookie c in cookies)
                {
                    if (!c.IsSession && c.Expires <= DateTime.Now) continue;
                    if (!seen.Add(c.Name + "@" + c.Domain)) continue;
                    sb.Append(c.Name).Append('=').Append(c.Value).Append("; ");
                }
            }
            string header = sb.Length >= 2 ? sb.ToString(0, sb.Length - 2) : "";
            lock (_cdnCookieLock)
            {
                _cdnCookieHeader = header;
                _cdnCookieRefreshedUtc = DateTime.UtcNow;
            }
            return header;
        }

        private HttpWebRequest BuildCdnRequest(string uri, string method, string range, string cookieHeader, WebProxy proxy)
        {
            var req = (HttpWebRequest)WebRequest.Create(uri);
            req.Method = method;
            req.Timeout = 30000;
            req.ReadWriteTimeout = 30000;
            req.MaximumAutomaticRedirections = 5;
            req.AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate;
            req.KeepAlive = true;
            req.Accept = "*/*";
            if (!string.IsNullOrEmpty(_cleanUa)) req.UserAgent = _cleanUa;
            req.Referer = CdnReferer;
            try { req.Headers["Origin"] = CdnReferer.TrimEnd('/'); } catch { }
            if (!string.IsNullOrEmpty(cookieHeader)) req.Headers["Cookie"] = cookieHeader;
            if (!string.IsNullOrEmpty(range))
            {
                try { req.Headers["Range"] = range; } catch { }
            }
            req.Proxy = proxy;
            try
            {
                req.ServicePoint.Expect100Continue = false;
                req.ServicePoint.ConnectionLimit = 24;
            }
            catch { }
            return req;
        }

        private CdnFetchResult FetchCdnAsset(string uri, string method, string range, string cookieHeader)
        {
            bool useProxy = _options != null && _options.ProxyPort > 0;
            WebProxy proxy = useProxy ? new WebProxy("http://127.0.0.1:" + _options.ProxyPort) : null;

            HttpWebResponse resp = null;
            string lastError = null;
            try
            {
                resp = (HttpWebResponse)BuildCdnRequest(uri, method, range, cookieHeader, proxy).GetResponse();
            }
            catch (WebException wex)
            {
                resp = wex.Response as HttpWebResponse;
                lastError = wex.Status + ": " + wex.Message;
                if (resp == null && useProxy)
                {
                    // Local relay unreachable: retry straight once.
                    try { resp = (HttpWebResponse)BuildCdnRequest(uri, method, range, cookieHeader, null).GetResponse(); }
                    catch (WebException wex2) { resp = wex2.Response as HttpWebResponse; lastError = wex2.Status + ": " + wex2.Message; }
                    catch (Exception ex2) { resp = null; lastError = ex2.GetType().Name + ": " + ex2.Message; }
                }
            }
            if (resp == null) return SimpleCdnResult(502, string.IsNullOrEmpty(lastError) ? "Bad Gateway" : lastError);

            using (resp)
            {
                var sb = new StringBuilder(320);
                sb.Append("Access-Control-Allow-Origin: *\r\n");
                sb.Append("Access-Control-Allow-Methods: GET, HEAD, OPTIONS\r\n");
                sb.Append("Access-Control-Allow-Headers: *\r\n");
                sb.Append("Access-Control-Expose-Headers: Content-Length, Content-Range, Content-Type, ETag\r\n");
                string ct = resp.ContentType;
                if (!string.IsNullOrEmpty(ct)) sb.Append("Content-Type: ").Append(ct).Append("\r\n");
                string cr = resp.Headers["Content-Range"];
                if (!string.IsNullOrEmpty(cr)) sb.Append("Content-Range: ").Append(cr).Append("\r\n");
                string ar = resp.Headers["Accept-Ranges"];
                if (!string.IsNullOrEmpty(ar)) sb.Append("Accept-Ranges: ").Append(ar).Append("\r\n");
                string etag = resp.Headers["ETag"];
                if (!string.IsNullOrEmpty(etag)) sb.Append("ETag: ").Append(etag).Append("\r\n");
                string lm = resp.Headers["Last-Modified"];
                if (!string.IsNullOrEmpty(lm)) sb.Append("Last-Modified: ").Append(lm).Append("\r\n");

                var body = new MemoryStream();
                bool wantBody = method.Equals("GET", StringComparison.OrdinalIgnoreCase) &&
                                resp.StatusCode != HttpStatusCode.NoContent &&
                                resp.StatusCode != HttpStatusCode.NotModified;
                if (wantBody)
                {
                    try
                    {
                        using (Stream rs = resp.GetResponseStream())
                        {
                            if (rs != null)
                            {
                                byte[] buf = new byte[81920];
                                int n;
                                while ((n = rs.Read(buf, 0, buf.Length)) > 0) body.Write(buf, 0, n);
                            }
                        }
                    }
                    catch { }
                }
                sb.Append("Content-Length: ").Append(body.Length).Append("\r\n");

                return new CdnFetchResult
                {
                    Status = (int)resp.StatusCode,
                    Reason = string.IsNullOrEmpty(resp.StatusDescription) ? "OK" : resp.StatusDescription,
                    Body = body,
                    Headers = sb.ToString()
                };
            }
        }

        private static CdnFetchResult SimpleCdnResult(int status, string reason)
        {
            return new CdnFetchResult
            {
                Status = status,
                Reason = reason,
                Body = new MemoryStream(new byte[0]),
                Headers = "Access-Control-Allow-Origin: *\r\nContent-Length: 0\r\n"
            };
        }



        public void HandleDeepLinkUri(string uri)
        {
            if (string.IsNullOrEmpty(uri)) return;

            if (InvokeRequired)
            {
                BeginInvoke(new Action<string>(HandleDeepLinkUri), uri);
                return;
            }

            _logCallback?.Invoke(string.Format("[{0}] 🔑 Dispatching OAuth DeepLink to game engine: {1}", _instanceId, uri.Length > 80 ? uri.Substring(0, 80) + "..." : uri));

            CloseOAuthDialog();

            if (_webView != null && _webView.CoreWebView2 != null)
            {
                string normalizedUri = uri;
                if (normalizedUri.Contains("login-authorized") || (normalizedUri.Contains("code=") && !normalizedUri.StartsWith("dofustouch://", StringComparison.OrdinalIgnoreCase)))
                {
                    int qIdx = normalizedUri.IndexOf('?');
                    string qs = qIdx >= 0 ? normalizedUri.Substring(qIdx) : "";
                    normalizedUri = "dofustouch://authorized" + qs;
                }

                string escapedUri = normalizedUri.Replace("\\", "\\\\").Replace("'", "\\'").Replace("\r", "").Replace("\n", "");
                string dispatchJs = string.Format(
                    "(function() {{" +
                    "  var target = '{0}';" +
                    "  if (typeof window.__onDeepLinkCallback === 'function') {{" +
                    "    window.__onDeepLinkCallback(target);" +
                    "  }} else if (window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function') {{" +
                    "    window.IonicDeeplink.fire({{ url: target }});" +
                    "  }} else {{" +
                    "    window.location.href = target;" +
                    "  }}" +
                    "}})();", escapedUri);

                _webView.CoreWebView2.ExecuteScriptAsync(dispatchJs);
            }
        }

        public void OpenOAuthDialog(string url)
        {
            if (string.IsNullOrEmpty(url)) return;

            if (InvokeRequired)
            {
                BeginInvoke(new Action<string>(OpenOAuthDialog), url);
                return;
            }

            try
            {
                CloseOAuthDialog();

                _activeOAuthDialog = new OAuthLoginForm(url, _options, this, _logCallback, _env);
                _activeOAuthDialog.FormClosed += (s, ev) => { _activeOAuthDialog = null; };
                _activeOAuthDialog.Show(this);
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] Failed to open OAuth dialog: {1}", _instanceId, ex.Message));
            }
        }

        public void CloseOAuthDialog()
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(CloseOAuthDialog));
                return;
            }

            if (_activeOAuthDialog != null && !_activeOAuthDialog.IsDisposed)
            {
                try
                {
                    _activeOAuthDialog.Close();
                    _activeOAuthDialog.Dispose();
                }
                catch { }
                finally
                {
                    _activeOAuthDialog = null;
                }
            }
        }

        private void CycleZoomMode()
        {
            if (!IsReady || _webView == null || _webView.CoreWebView2 == null) return;

            // Cycle: Auto -> 100% -> 80% -> 65% -> 50% -> Auto
            if (_autoZoomEnabled)
            {
                _autoZoomEnabled = false;
                SetZoom(1.0);
            }
            else
            {
                double z = _webView.ZoomFactor;
                if (Math.Abs(z - 1.0) < 0.05)
                {
                    SetZoom(0.80);
                }
                else if (Math.Abs(z - 0.80) < 0.05)
                {
                    SetZoom(0.65);
                }
                else if (Math.Abs(z - 0.65) < 0.05)
                {
                    SetZoom(0.50);
                }
                else
                {
                    _autoZoomEnabled = true;
                    ApplyAutoZoom();
                }
            }
        }

        private void SetZoom(double factor)
        {
            if (!IsReady || _webView == null || _webView.CoreWebView2 == null) return;
            try
            {
                _isUpdatingZoom = true;
                double clamped = Math.Max(0.25, Math.Min(3.0, factor));
                _webView.ZoomFactor = clamped;
                _currentZoomFactor = clamped;
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] SetZoom error: {1}", _instanceId, ex.Message));
            }
            finally
            {
                _isUpdatingZoom = false;
            }
            UpdateZoomButtonText();
            TriggerGameResize();
        }

        private void ApplyAutoZoom()
        {
            if (!IsReady || _webView == null || _webView.CoreWebView2 == null) return;
            try
            {
                int w = _webView.ClientSize.Width;
                int h = _webView.ClientSize.Height;
                if (w <= 0 || h <= 0) return;

                // Base design target for Dofus Touch mobile layout
                const double targetWidth = 1280.0;
                const double targetHeight = 720.0;

                double scaleX = (double)w / targetWidth;
                double scaleY = (double)h / targetHeight;
                double autoZoom = Math.Min(scaleX, scaleY);

                // Cap auto zoom at 1.0 so large/maximized windows retain crisp 1:1 pixel rendering
                // Floor at 0.30 to avoid extreme shrinking
                double clampedZoom = Math.Max(0.30, Math.Min(1.0, autoZoom));

                if (Math.Abs(_webView.ZoomFactor - clampedZoom) > 0.015)
                {
                    _isUpdatingZoom = true;
                    _currentZoomFactor = clampedZoom;
                    _webView.ZoomFactor = clampedZoom;
                    BeginInvoke(new Action(() => { _isUpdatingZoom = false; }));
                    UpdateZoomButtonText();
                    TriggerGameResize();
                }
                else
                {
                    UpdateZoomButtonText();
                }
            }
            catch (Exception ex)
            {
                _isUpdatingZoom = false;
                _logCallback?.Invoke(string.Format("[{0}] AutoZoom error: {1}", _instanceId, ex.Message));
            }
        }

        private void UpdateZoomButtonText()
        {
            if (_btnZoom == null || _btnZoom.IsDisposed) return;
            if (_webView == null) return;

            int pct = (int)Math.Round(_webView.ZoomFactor * 100.0);
            if (_autoZoomEnabled)
            {
                _btnZoom.Text = string.Format("🔍 Auto ({0}%)", pct);
                _btnZoom.BackColor = Color.FromArgb(37, 99, 235);
            }
            else
            {
                _btnZoom.Text = string.Format("🔍 {0}%", pct);
                _btnZoom.BackColor = Color.FromArgb(51, 65, 85);
            }
        }

        private void TriggerGameResize()
        {
            if (!IsReady || _webView == null || _webView.CoreWebView2 == null) return;
            try
            {
                _webView.CoreWebView2.ExecuteScriptAsync(
                    "try{if(window.gui&&typeof window.gui._resizeUi==='function'){window.gui._resizeUi();}}catch(e){}try{window.dispatchEvent(new Event('resize'));}catch(e){}try{if(window.isoEngine&&window.isoEngine.mapScene&&typeof window.isoEngine.mapScene.requireCompleteRefresh==='function'){window.isoEngine.mapScene.requireCompleteRefresh();}}catch(e){}"
                );
            }
            catch { }
        }

        private void UpdateMemoryUsage()
        {
            if (IsDisposed || !IsHandleCreated) return;
            try
            {
                if (_lblMemory != null)
                {
                    _lblMemory.Visible = (Width >= 620);
                }
                double wsMb = GetWorkingSetMb();
                double pmMb = GetPrivateCommitMb();
                _lblMemory.Text = string.Format("RAM: {0:F1} MB (Commit: {1:F1} MB)", wsMb, pmMb);
            }
            catch { }
        }

        public double GetWorkingSetMb()
        {
            try
            {
                using (var proc = Process.GetCurrentProcess())
                {
                    return proc.WorkingSet64 / (1024.0 * 1024.0);
                }
            }
            catch { return 0; }
        }

        public double GetPrivateCommitMb()
        {
            try
            {
                using (var proc = Process.GetCurrentProcess())
                {
                    return proc.PrivateMemorySize64 / (1024.0 * 1024.0);
                }
            }
            catch { return 0; }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            CloseOAuthDialog();

            if (_cdnInterceptTotal > 0)
                _logCallback?.Invoke(string.Format(
                    "[{0}] CDN intercept summary: {1} requests, {2} ok, {3} failed, {4} handler errors",
                    _instanceId, _cdnInterceptTotal, _cdnInterceptOk, _cdnInterceptFail, _cdnInterceptLoggedErrors));

            if (_telemetryTimer != null)
            {
                _telemetryTimer.Stop();
                _telemetryTimer.Dispose();
                _telemetryTimer = null;
            }

            if (_webView != null && _webView.CoreWebView2 != null)
            {
                _webView.CoreWebView2.NavigationStarting -= OnNavigationStarting;
                _webView.CoreWebView2.NavigationCompleted -= OnNavigationCompleted;
                _webView.CoreWebView2.ProcessFailed -= OnProcessFailed;
                _webView.CoreWebView2.WebMessageReceived -= OnWebMessageReceived;
                _webView.CoreWebView2.NewWindowRequested -= OnNewWindowRequested;
                _webView.CoreWebView2.WebResourceRequested -= OnWebResourceRequested;
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://*.cdn.ankama.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://*.cdn.ankama-games.com/*", CoreWebView2WebResourceContext.All); } catch { }
            }

            base.OnFormClosing(e);
            OnFormClosedByUser?.Invoke(_instanceId);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                CloseOAuthDialog();

                if (_telemetryTimer != null)
                {
                    _telemetryTimer.Stop();
                    _telemetryTimer.Dispose();
                    _telemetryTimer = null;
                }
                if (_webView != null)
                {
                    _webView.Dispose();
                    _webView = null;
                }
            }
            base.Dispose(disposing);
        }
    }

    /// <summary>
    /// Specialized OAuth dialog for Ankama Connect / Google sign-in.
    /// Runs with a stock desktop Chrome identity (no mobile/webview markers)
    /// and intercepts deep link redirects, copying auth cookies into the game
    /// session's cookie jar before closing.
    /// </summary>
    public class OAuthLoginForm : Form
    {
        private static readonly string[] AuthCookieOrigins = new string[]
        {
            "https://account.ankama.com/",
            "https://www.ankama.com/",
            "https://ankama.com/",
            "https://auth.ankama.com/",
            "https://www.dofus-touch.com/",
            "https://dofus-touch.com/",
            "https://cdn.ankama.com/",
            "https://www.cdn.ankama.com/",
            "https://cdn.ankama-games.com/",
            "https://dt-proxy-production-login.ankama-games.com/"
        };

        private readonly string _initialUrl;
        private readonly ClientLaunchOptions _options;
        private readonly WebView2ClientForm _parentForm;
        private readonly Action<string> _logCallback;
        private readonly CoreWebView2Environment _sharedEnv;

        private WebView2 _webView;
        private Panel _topBar;
        private Label _lblTitle;
        private Button _btnClose;

        public OAuthLoginForm(string initialUrl, ClientLaunchOptions options, WebView2ClientForm parentForm, Action<string> logCallback, CoreWebView2Environment sharedEnv)
        {
            _initialUrl = initialUrl;
            _options = options;
            _parentForm = parentForm;
            _logCallback = logCallback;
            _sharedEnv = sharedEnv;

            InitializeComponent();
        }

        private void InitializeComponent()
        {
            Text = "Connexion Ankama Connect";
            Size = new Size(520, 720);
            MinimumSize = new Size(400, 500);
            StartPosition = FormStartPosition.CenterParent;
            BackColor = Color.FromArgb(15, 23, 42);
            ShowIcon = false;
            ShowInTaskbar = false;
            TopMost = true;

            _topBar = new Panel
            {
                Dock = DockStyle.Top,
                Height = 36,
                BackColor = Color.FromArgb(30, 41, 59),
                Padding = new Padding(10, 4, 10, 4)
            };

            _lblTitle = new Label
            {
                Text = "🔒 Authentification Sécurisée Ankama",
                ForeColor = Color.FromArgb(226, 232, 240),
                Font = new Font("Segoe UI", 9.0f, FontStyle.Bold),
                AutoSize = false,
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleLeft
            };
            _topBar.Controls.Add(_lblTitle);

            _btnClose = new Button
            {
                Text = "✕ Fermer",
                Dock = DockStyle.Right,
                Width = 76,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(239, 68, 68),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnClose.FlatAppearance.BorderSize = 0;
            _btnClose.Click += (s, e) => Close();
            _topBar.Controls.Add(_btnClose);

            Controls.Add(_topBar);

            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(15, 23, 42)
            };
            Controls.Add(_webView);
            _webView.BringToFront();
        }

        protected override async void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            try
            {
                // Google blocks sign-in from spoofed/embedded browser identities
                // ("This browser or app may not be secure"), so the OAuth popup
                // presents a stock desktop Chrome identity: no mobile UA, no wv
                // marker, no navigator disguise. The game window itself keeps the
                // mobile disguise, but Google's pages must never see it.
                string engineMajor = "155";
                try
                {
                    string browserVersion = CoreWebView2Environment.GetAvailableBrowserVersionString();
                    if (!string.IsNullOrEmpty(browserVersion))
                    {
                        int dot = browserVersion.IndexOf('.');
                        engineMajor = dot > 0 ? browserVersion.Substring(0, dot) : browserVersion;
                    }
                }
                catch { }
                string cleanUa = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + engineMajor + ".0.0.0 Safari/537.36";

                if (_sharedEnv != null)
                {
                    await _webView.EnsureCoreWebView2Async(_sharedEnv);
                }
                else
                {
                    var envOptions = new CoreWebView2EnvironmentOptions();
                    var args = new StringBuilder();

                    if (!string.IsNullOrEmpty(cleanUa))
                    {
                        args.AppendFormat("--user-agent=\"{0}\" ", cleanUa);
                    }

                    if (_options != null && _options.ProxyPort > 0)
                    {
                        args.AppendFormat("--proxy-server=\"http://127.0.0.1:{0}\" ", _options.ProxyPort);
                    }
                    else if (_options != null && !string.IsNullOrEmpty(_options.Proxy) && !_options.Proxy.Contains("DIRECT"))
                    {
                        args.AppendFormat("--proxy-server=\"{0}\" ", _options.Proxy);
                    }

                    args.Append("--enable-gpu-rasterization --touch-events=enabled --disable-blink-features=AutomationControlled ");
                    envOptions.AdditionalBrowserArguments = args.ToString().Trim();

                    string oauthDataDir = Path.Combine(Path.GetTempPath(), "DofusFarm_OAuth_" + Guid.NewGuid().ToString("N"));
                    var env = await CoreWebView2Environment.CreateAsync(null, oauthDataDir, envOptions);
                    await _webView.EnsureCoreWebView2Async(env);
                }

                var s = _webView.CoreWebView2.Settings;
                s.IsWebMessageEnabled = true;
                s.AreDevToolsEnabled = true;
                s.IsStatusBarEnabled = false;
                s.AreDefaultContextMenusEnabled = true;

                if (!string.IsNullOrEmpty(cleanUa))
                {
                    s.UserAgent = cleanUa;
                }

                _webView.CoreWebView2.NavigationStarting += async (sdr, ev) =>
                {
                    string u = ev.Uri;
                    _logCallback?.Invoke("[OAUTH-POPUP] Navigating: " + u);

                    if (u.StartsWith("dofustouch://", StringComparison.OrdinalIgnoreCase) ||
                        (u.Contains("login-authorized") && (u.Contains("code=") || u.Contains("error="))))
                    {
                        ev.Cancel = true;
                        _logCallback?.Invoke("[OAUTH-POPUP] Intercepted OAuth completion code redirect: " + u);
                        await CopyAuthCookiesToParentAsync();
                        _parentForm.HandleDeepLinkUri(u);
                        this.Close();
                        return;
                    }
                };

                _webView.CoreWebView2.NavigationCompleted += (sdr, ev) =>
                {
                    if (ev.IsSuccess && _webView.CoreWebView2 != null)
                    {
                        string docTitle = _webView.CoreWebView2.DocumentTitle;
                        if (!string.IsNullOrEmpty(docTitle))
                        {
                            _lblTitle.Text = "🔒 " + docTitle;
                        }
                    }
                };

                _webView.CoreWebView2.NewWindowRequested += (sdr, ev) =>
                {
                    ev.Handled = true;
                    _webView.CoreWebView2.Navigate(ev.Uri);
                };

                _webView.CoreWebView2.Navigate(_initialUrl);
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke("[OAUTH-POPUP] Error initializing dialog: " + ex.Message);
            }
        }

        private async Task CopyAuthCookiesToParentAsync()
        {
            try
            {
                CoreWebView2CookieManager src = (_webView != null && _webView.CoreWebView2 != null)
                    ? _webView.CoreWebView2.CookieManager : null;
                CoreWebView2CookieManager dst = _parentForm != null ? _parentForm.MainCookieManager : null;
                if (src == null || dst == null) return;

                int copied = 0;
                foreach (string origin in AuthCookieOrigins)
                {
                    List<CoreWebView2Cookie> cookies = null;
                    try { cookies = await src.GetCookiesAsync(origin); } catch { }
                    if (cookies == null) continue;
                    foreach (CoreWebView2Cookie c in cookies)
                    {
                        try
                        {
                            if (!c.IsSession && c.Expires < DateTime.Now) continue;
                            dst.AddOrUpdateCookie(dst.CopyCookie(c));
                            copied++;
                        }
                        catch { }
                    }
                }
                _logCallback?.Invoke("[OAUTH-POPUP] Copied " + copied + " auth cookies into the game session");
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke("[OAUTH-POPUP] Auth cookie copy failed: " + ex.Message);
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && _webView != null)
            {
                _webView.Dispose();
                _webView = null;
            }
            base.Dispose(disposing);
        }
    }
}
