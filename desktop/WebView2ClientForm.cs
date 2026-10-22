using System;
using System.Collections.Concurrent;
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
        public ClientLaunchOptions Options { get { return _options; } }
        private readonly string _instanceId;
        private string _lastGuestCode;
        private string _lastGuestTokensRaw;
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

        // Ankama web-shop artwork (bonus packs, store articles) and store
        // banners/news (ankama/cms) live on static.ankama.com and answer 403
        // when the request carries the virtual-origin Referer
        // (https://dofus.local/...). Routed through the same repair path,
        // which re-fetches it with the official web-client Referer/Origin
        // (verified: 200). Only GET/HEAD/OPTIONS are repaired; everything
        // else passes through untouched.
        private static readonly Regex StaticAssetUriRe = new Regex(
            @"^https?://static\.ankama\.com/",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);

        // Compiled once: OnWebMessageReceived runs for every bridged message.
        private static readonly Regex WmConsoleTextRe = new Regex("\"text\"\\s*:\\s*\"((?:\\\\\"|[^\"])*)\"", RegexOptions.Compiled);
        private static readonly Regex WmConsoleLevelRe = new Regex("\"level\"\\s*:\\s*\"([^\"]+)\"", RegexOptions.Compiled);
        private static readonly Regex WmEnabledRe = new Regex("\"enabled\"\\s*:\\s*(true|false)", RegexOptions.Compiled);
        private static readonly Regex WmUrlRe = new Regex("\"url\"\\s*:\\s*\"([^\"]+)\"", RegexOptions.Compiled);
        private static readonly Regex WmCodeRe = new Regex("\"code\"\\s*:\\s*\"([^\"]+)\"", RegexOptions.Compiled);
        private static readonly Regex WmLoginRe = new Regex("\"login\"\\s*:\\s*\"([^\"]+)\"", RegexOptions.Compiled);
        private static readonly Regex WmPasswordRe = new Regex("\"password\"\\s*:\\s*\"([^\"]+)\"", RegexOptions.Compiled);
        private static readonly Regex WmActionRe = new Regex("\"action\"\\s*:\\s*\"([^\"]+)\"", RegexOptions.Compiled);
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
        private int _cdnInterceptReqLog;
        private bool _cdnInterceptFirstOkLogged;

        private sealed class CdnFetchResult
        {
            public int Status;
            public string Reason;
            public MemoryStream Body;
            public string Headers;
        }

        // Small pool for the CDN copy buffer: asset bursts otherwise allocate
        // a fresh 80 KB byte[] per response (gen2 churn during map loads).
        private static readonly ConcurrentQueue<byte[]> CdnCopyBufferPool = new ConcurrentQueue<byte[]>();

        private WebView2 _webView;
        private Panel _toolbarPanel;
        private Label _lblStatus;
        private Label _lblMemory;
        private Button _btnZoom;
        private Button _btnAutoFight;
        private Button _btnDevTools;
        private Button _btnReload;
        private System.Windows.Forms.Timer _telemetryTimer;

        private bool _autoZoomEnabled = true;
        private bool _autoFightEnabled = false;
        private bool _isUpdatingZoom = false;
        private double _currentZoomFactor = 1.0;

        // F11 fullscreen toggle (driven by the page: WebView2 no longer
        // implements F11 itself, so the key reaches JS and is bridged here).
        private bool _isFullscreen = false;
        private Rectangle _preFullscreenBounds;
        private FormBorderStyle _preFullscreenStyle = FormBorderStyle.Sizable;
        private FormWindowState _preFullscreenWindowState = FormWindowState.Normal;

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

        // Host-performed guest token exchange result. The game's own cross-site
        // fetch cannot carry the popup's SameSite=Lax aws-waf-token cookie, so
        // when the host pre-exchanges the code it stores the raw token JSON
        // here; the shim then asks for it via the webauth_exchange message.
        public void SetGuestExchangeResult(string code, string rawTokensJson)
        {
            _lastGuestCode = code;
            _lastGuestTokensRaw = rawTokensJson;
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

        /// <summary>(instanceId, action) requests raised from inside the page, e.g.
        /// the in-game badge's Auto button. The page cannot start processes; the
        /// host does.</summary>
        public event Action<string, string> OnAiRequest;
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
        window.buildVersion = '1.74.9';
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
        // Throttled host bridge: the game's console traffic is heavy (loader
        // chatter, per-tile diagnostics). Keep console semantics untouched,
        // but meter what crosses into the host: info lines get a small
        // per-second budget, repeats collapse into an [xN] summary, and
        // warn/error always pass (bounded). Nothing is silently lost.
        var __bridgeBudget = 30, __bridgeWindow = 0, __bridgeSeenCount = 0;
        var __bridgeSeen = {};
        function __bridgeSend(level, text) {
            try {
                if (window.chrome && window.chrome.webview) {
                    window.chrome.webview.postMessage({ type: 'CONSOLE_LOG', level: level, text: text });
                }
            } catch(e){}
        }
        function __bridgeFlush(key, rec) {
            if (rec.n > 1) __bridgeSend(rec.level, '[x' + rec.n + '] ' + rec.text);
            delete __bridgeSeen[key];
            __bridgeSeenCount--;
        }
        function __bridge(level, args) {
            var now = Date.now();
            if (now - __bridgeWindow > 1000) { __bridgeWindow = now; __bridgeBudget = 30; }
            var text;
            try { text = Array.prototype.slice.call(args).join(' '); } catch (e) { text = '[unserializable]'; }
            if (text.length > 350) text = text.slice(0, 350) + '...';
            var key = level + '|' + text;
            var rec = __bridgeSeen[key];
            if (rec) {
                rec.n++;
                if (!rec.timer) rec.timer = setTimeout(function () { rec.timer = null; __bridgeFlush(key, rec); }, 6000);
                return;
            }
            var budgeted = (level === 'info');
            if (budgeted && __bridgeBudget <= 0) return;
            if (budgeted) __bridgeBudget--;
            if (__bridgeSeenCount > 400) { __bridgeSeen = {}; __bridgeSeenCount = 0; }
            rec = { n: 1, level: level, text: text, timer: null };
            __bridgeSeen[key] = rec;
            __bridgeSeenCount++;
            __bridgeSend(level, text);
        }
        console.log = function() { _cLog.apply(console, arguments); __bridge('info', arguments); };
        console.warn = function() { _cWarn.apply(console, arguments); __bridge('warn', arguments); };
        console.error = function() { _cErr.apply(console, arguments); __bridge('error', arguments); };

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

        private bool _skipPoolSeed;
        private bool _explicitAccount;
        private string _claimedLogin;

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

            ApplyPendingIdentityReset();
            InitializeForm();
        }

        private void ApplyPendingIdentityReset()
        {
            try
            {
                string flag = Path.Combine(_userDataDir, "force_fresh_identity.flag");
                if (!File.Exists(flag)) return;
                _skipPoolSeed = true;
                string cleared = Path.Combine(_userDataDir, "fresh_identity.cleared");
                if (!File.Exists(cleared))
                {
                    string def = Path.Combine(_userDataDir, @"EBWebView\Default");
                    foreach (string sub in new[] { "Local Storage", "IndexedDB", "Network", "Service Worker", "Cookies", "Session Storage" })
                    {
                        string src = Path.Combine(def, sub);
                        try
                        {
                            if (Directory.Exists(src)) FarmEngine.ForceDeleteDirectory(src);
                            else if (File.Exists(src)) File.Delete(src);
                        }
                        catch { }
                    }
                    try { File.Delete(Path.Combine(_userDataDir, "account.identity")); } catch { }
                    try { File.WriteAllText(cleared, DateTime.UtcNow.ToString("o")); } catch { }
                    _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] Cleared shared session storage once; a fresh guest account will be registered", _instanceId));
                }
                else
                {
                    _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] Fresh-identity mode active; pool seeding stays disabled for this instance", _instanceId));
                }
            }
            catch { }
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

            _btnAutoFight = new Button
            {
                Text = "⚡ THX-01: OFF",
                Size = new Size(100, 24),
                Location = new Point(Width - 380, 4),
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(51, 65, 85),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnAutoFight.FlatAppearance.BorderSize = 0;
            _btnAutoFight.Click += (s, e) => ToggleAutoFight();
            _toolbarPanel.Controls.Add(_btnAutoFight);

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
            // WebView2 needs an STA thread with a message pump. If this was reached
            // from anywhere else (a pool thread, a ContinueWith), hand the whole
            // startup back to the UI thread instead of letting CreateAsync fail with
            // RPC_E_CHANGED_MODE (0x80010106) -- which surfaces as "WebView2
            // Initialization Error" with no hint about the apartment.
            if (!DofusFarm.UI.UiThread.OnUiThread)
            {
                _logCallback?.Invoke(string.Format(
                    "[{0}] WebView2 init reached a {1} thread; moving it to the UI thread.",
                    _instanceId, DofusFarm.UI.UiThread.Apartment()));
                DofusFarm.UI.UiThread.Post(delegate { var ignored = StartAsync(); });
                return;
            }

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

                // Native mobile touch, automation suppression switches, and WebRTC IP leak shield
                args.Append("--touch-events=enabled --disable-blink-features=AutomationControlled --force-webrtc-ip-handling-policy=disable_non_proxied_udp --disable-features=WebRtcHideLocalIpsWithMdns ");

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
                    await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Network.setCacheDisabled", "{\"cacheDisabled\":false}");
                    _logCallback?.Invoke(string.Format("[{0}] Enabled native mobile touch emulation; HTTP cache active (versioned asset URLs keep it safe).", _instanceId));
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
                    "(function() {{ window.appInfo = window.appInfo || {{}}; window.appInfo.version = '{0}'; window.appInfo.build = '{0}'; window.appInfo.versionCode = '{1}'; window.appInfo.identifier = 'com.ankama.dofustouch'; window.buildVersion = '{2}'; window.appInfo.server = 'https://dt-proxy-production-login.ankama-games.com'; }})();",
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

                // 3a. Account credentials injection from pool (accounts.txt)
                bool hasConfigAccount = !string.IsNullOrEmpty(_options.AccountLogin);
                _explicitAccount = hasConfigAccount;
                if (!hasConfigAccount && !_skipPoolSeed)
                {
                    int slot = 0;
                    Match mSlot = Regex.Match(_instanceId ?? "", @"(\d+)$");
                    if (mSlot.Success) int.TryParse(mSlot.Groups[1].Value, out slot);
                    var acc = FarmEngine.GetAccountForSlot(slot);
                    if (acc.HasValue && !FarmEngine.IsIdentityOwnedByOtherProfile(acc.Value.Key, _instanceId))
                    {
                        _options.AccountLogin = acc.Value.Key;
                        _options.AccountPassword = acc.Value.Value;
                    }
                    else if (acc.HasValue)
                    {
                        _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] Pool account {1} already belongs to another instance profile; keeping this profile's own identity", _instanceId, acc.Value.Key));
                    }
                }

                bool hasSeedAccount = !string.IsNullOrEmpty(_options.AccountLogin) && !string.IsNullOrEmpty(_options.AccountPassword);
                string safeLogin = hasSeedAccount ? _options.AccountLogin.Replace("\\", "\\\\").Replace("'", "\\'") : "";
                string safePass = hasSeedAccount ? _options.AccountPassword.Replace("\\", "\\\\").Replace("'", "\\'") : "";
                string accScript = string.Format(
                    "(function() {{\n" +
                    "    try {{\n" +
                    "        var seedLogin = '{0}';\n" +
                    "        var seedPass = '{1}';\n" +
                    "        // After a rejected guest the pool entry is dead; re-seeding it on the\n" +
                    "        // reload would undo the self-heal and replay the same failure.\n" +
                    "        try {{ if (sessionStorage.getItem('dfSkipSeed') === '1') {{ seedLogin = ''; seedPass = ''; }} }} catch(e) {{}}\n" +
                    "        var raw = localStorage.getItem('dofus.1');\n" +
                    "        var prefs = {{}};\n" +
                    "        if (raw) {{ try {{ prefs = JSON.parse(raw); }} catch(e) {{ prefs = {{}}; }} }}\n" +
                    "        var existing = prefs['all#guestAccount'];\n" +
                    "        if (seedLogin && !(existing && existing.login)) {{\n" +
                    "            prefs['all#guestAccount'] = {{ login: seedLogin, password: seedPass }};\n" +
                    "            localStorage.setItem('dofus.1', JSON.stringify(prefs));\n" +
                    "            console.log('[Auth] Pre-seeded visitor credentials for ' + seedLogin);\n" +
                    "        }} else if (existing && existing.login) {{\n" +
                    "            console.log('[Auth] Keeping profile identity for ' + existing.login);\n" +
                    "        }} else {{\n" +
                    "            console.log('[Auth] No stored guest identity; client will register a fresh one');\n" +
                    "        }}\n" +
                    "        var _postIdentity = function() {{\n" +
                    "            try {{\n" +
                    "                if (window.chrome && window.chrome.webview) {{\n" +
                    "                    var idAcc = (existing && existing.login) ? existing : (seedLogin ? {{ login: seedLogin, password: seedPass }} : null);\n" +
                    "                    window.chrome.webview.postMessage({{ type: 'ACCOUNT_IDENTITY', login: idAcc ? idAcc.login : '', password: idAcc ? idAcc.password : '' }});\n" +
                    "                    return true;\n" +
                    "                }}\n" +
                    "            }} catch(e) {{}}\n" +
                    "            return false;\n" +
                    "        }};\n" +
                    "        if (!_postIdentity()) {{\n" +
                    "            var _idTries = 0;\n" +
                    "            var _idTimer = setInterval(function() {{\n" +
                    "                if (_postIdentity() || ++_idTries > 25) {{ clearInterval(_idTimer); }}\n" +
                    "            }}, 200);\n" +
                    "        }}\n" +
                    "        var attempts = 0, lastAt = 0;\n" +
                    "        try {{ attempts = parseInt(sessionStorage.getItem('dfGuestTries') || '0', 10) || 0; }} catch(e) {{}}\n" +
                    "        try {{ lastAt = parseFloat(sessionStorage.getItem('dfGuestLastAt') || '0') || 0; }} catch(e) {{}}\n" +
                    "        if (attempts >= 5) {{ console.log('[Auth] Guest login retry budget exhausted (5 attempts)'); return; }}\n" +
                    "        if (lastAt && Date.now() - lastAt < 20000) {{ console.log('[Auth] Guest login attempt ' + attempts + ' was ' + Math.round((Date.now()-lastAt)/1000) + 's ago; not retrying yet'); return; }}\n" +
                    "        var fired = false;\n" +
                    "        var deadline = Date.now() + 300000;\n" +
                    "        var tId = setInterval(function() {{\n" +
                    "            if (fired || Date.now() > deadline) {{ clearInterval(tId); return; }}\n" +
                    "            if (window.gui && window.gui.loginScreen && typeof window.gui.loginScreen.playWithGuest === 'function') {{\n" +
                    "                fired = true;\n" +
                    "                clearInterval(tId);\n" +
                    "                try {{\n" +
                    "                    sessionStorage.setItem('dfGuestTries', String(attempts + 1));\n" +
                    "                    sessionStorage.setItem('dfGuestLastAt', String(Date.now()));\n" +
                    "                }} catch(e) {{}}\n" +
                    "                console.log('[Auth] Automatically initiating direct guest socket login (attempt ' + (attempts + 1) + '/5)');\n" +
                    "                setTimeout(function() {{ try {{ window.gui.loginScreen.playWithGuest(); }} catch(e) {{}} }}, 800);\n" +
                    "                var _watch = setInterval(function() {{\n" +
                    "                    try {{\n" +
                    "                        var ch = window.gui && window.gui.playerData && window.gui.playerData.character;\n" +
                    "                        if (ch && ch.id != null) {{\n" +
                    "                            clearInterval(_watch);\n" +
                    "                            console.log('[Auth] Guest login succeeded as ' + ch.name + ' (id ' + ch.id + ')');\n" +
                    "                        }}\n" +
                    "                    }} catch(e) {{}}\n" +
                    "                }}, 1000);\n" +
                    "                setTimeout(function() {{ clearInterval(_watch); }}, 120000);\n" +
                    "            }}\n" +
                    "        }}, 400);\n" +
                    "        // Self-healing identity. A guest account is temporary by design: when\n" +
                    "        // the server rejects it the client only says LOGIN_ERROR, and the\n" +
                    "        // seeded identity in localStorage keeps being replayed forever, so the\n" +
                    "        // instance can never log in again. Clear it once per tab session and\n" +
                    "        // reload, which makes the client register a brand-new guest.\n" +
                    "        try {{\n" +
                    "            if (!window.__dfSelfHeal) {{\n" +
                    "                window.__dfSelfHeal = true;\n" +
                    "                var _origInfo = console.info;\n" +
                    "                console.info = function() {{\n" +
                    "                    try {{\n" +
                    "                        var msg = Array.prototype.slice.call(arguments).join(' ');\n" +
                    "                        if (msg.indexOf('LOGIN_ERROR') >= 0 && !sessionStorage.getItem('dfGuestReset')) {{\n" +
                    "                            sessionStorage.setItem('dfGuestReset', '1');\n" +
                    "                            try {{ sessionStorage.setItem('dfSkipSeed', '1'); }} catch(e) {{}}\n" +
                    "                            try {{ sessionStorage.removeItem('dfGuestTries'); sessionStorage.removeItem('dfGuestLastAt'); }} catch(e) {{}}\n" +
                    "                            var raw2 = localStorage.getItem('dofus.1');\n" +
                    "                            var cleared = false;\n" +
                    "                            if (raw2) {{\n" +
                    "                                try {{\n" +
                    "                                    var p2 = JSON.parse(raw2);\n" +
                    "                                    if (p2['all#guestAccount']) {{ delete p2['all#guestAccount']; cleared = true; }}\n" +
                    "                                    localStorage.setItem('dofus.1', JSON.stringify(p2));\n" +
                    "                                }} catch(e) {{}}\n" +
                    "                            }}\n" +
                    "                            console.log('[Auth] Guest identity rejected by the server; ' +\n" +
                    "                                (cleared ? 'cleared it' : 'nothing stored to clear') +\n" +
                    "                                ', reloading to register a fresh guest');\n" +
                    "                            setTimeout(function() {{ try {{ location.reload(); }} catch(e) {{}} }}, 600);\n" +
                    "                        }}\n" +
                    "                    }} catch(e) {{}}\n" +
                    "                    return _origInfo.apply(console, arguments);\n" +
                    "                }};\n" +
                    "            }}\n" +
                    "        }} catch(e) {{}}\n" +
                    "    }} catch(e) {{ console.error('[Auth] Failed to seed visitor account:', e); }}\n" +
                    "}})();",
                    safeLogin, safePass);
                // Manual-login escape hatch. Drop `manual-login.flag` next to the
                // exe and the host injects nothing at all: no credential seeding,
                // no automatic guest login, no self-heal. The client shows its own
                // login screen and the player signs in by hand, which is the only
                // way to tell "our automation is broken" apart from "the account is
                // dead". The recorder still runs, so the manual login is captured.
                bool manualLogin = false;
                try
                {
                    manualLogin = File.Exists(Path.Combine(
                        AppDomain.CurrentDomain.BaseDirectory, "manual-login.flag"));
                }
                catch { }
                if (manualLogin)
                {
                    _logCallback?.Invoke(string.Format(
                        "[{0}] manual-login.flag present: skipping credential seeding and auto-login",
                        _instanceId));
                    // Clear any guest identity the profile still carries. A stale
                    // `[GUEST]...` entry is replayed on every reload and overrides
                    // a real OAuth login, so the session never survives.
                    await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                        "(function(){try{var raw=localStorage.getItem('dofus.1');if(!raw)return;" +
                        "var p=JSON.parse(raw);if(p&&p['all#guestAccount']){" +
                        "delete p['all#guestAccount'];localStorage.setItem('dofus.1',JSON.stringify(p));" +
                        "console.log('[Auth] manual-login: cleared stored guest identity');}}catch(e){}})();");
                }
                else
                {
                    await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(accScript);
                }
                if (hasSeedAccount)
                    _logCallback?.Invoke(string.Format("[{0}] Registered visitor credentials injection for {1}", _instanceId, _options.AccountLogin));
                else
                    _logCallback?.Invoke(string.Format("[{0}] Registered guest auto-login (profile keeps its own identity)", _instanceId));

                // 3b. CDN relay: expose the local relay config, then inject the
                // shim that rewrites CDN asset URLs through it. Inert when no
                // ProxyPort is wired or when the page ships its own copy
                // (double-load is guarded inside the shim).
                string cacheRoot = !string.IsNullOrEmpty(_options.CacheDir)
                    ? _options.CacheDir
                    : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "asset_cache");
                string mirrorDir = Path.Combine(cacheRoot, "files");
                string cacheBase = "";
                try
                {
                    Directory.CreateDirectory(mirrorDir);
                    _webView.CoreWebView2.SetVirtualHostNameToFolderMapping("assets.local", mirrorDir, CoreWebView2HostResourceAccessKind.Allow);
                    cacheBase = "https://assets.local/";
                    _logCallback?.Invoke(string.Format("[{0}] Mapped asset mirror https://assets.local/ -> {1}", _instanceId, mirrorDir));
                }
                catch (Exception mirrorEx)
                {
                    _logCallback?.Invoke(string.Format("[{0}] Asset mirror mapping warning: {1}", _instanceId, mirrorEx.Message));
                }
                string relayCfgScript = string.Format(
                    "(function() {{ window.__DOFUS_CDN_RELAY = {{ enabled: {0}, port: {1}, referer: \"{2}\", cacheBase: \"{3}\" }}; }})();",
                    (_options.ProxyPort > 0) ? "true" : "false", _options.ProxyPort, CdnReferer, cacheBase);
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

                // 3c. wizAssets local-file layer: the official mobile client
                // persists every asset to device storage and loads it back from
                // files; this shim replicates that on top of the relay cache
                // mirror so repeat loads never touch the network.
                try
                {
                    string wizShimPath = Path.Combine(clientDir, "js", "wizassets-shim.js");
                    if (File.Exists(wizShimPath))
                    {
                        await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(File.ReadAllText(wizShimPath));
                        _logCallback?.Invoke(string.Format("[{0}] Registered wizAssets local-file shim.", _instanceId));
                    }
                }
                catch (Exception wizEx)
                {
                    _logCallback?.Invoke(string.Format("[{0}] wizAssets shim injection warning: {1}", _instanceId, wizEx.Message));
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
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://static.ankama.com/*", CoreWebView2WebResourceContext.All);
                }
                catch (Exception filtEx)
                {
                    _logCallback?.Invoke(string.Format("[{0}] CDN filter registration warning: {1}", _instanceId, filtEx.Message));
                }
                _webView.CoreWebView2.WebResourceRequested += OnWebResourceRequested;

                // Wire AUTH endpoint observation (login/token/guest flows) so
                // auth rejections carry their real HTTP status + error body
                // into the log instead of only showing as in-game popups.
                try
                {
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://auth.ankama.com/*", CoreWebView2WebResourceContext.All);
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://account.ankama.com/*", CoreWebView2WebResourceContext.All);
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://dt-proxy-production-login.ankama-games.com/*", CoreWebView2WebResourceContext.All);
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://dt-proxy-production-early.ankama-games.com/*", CoreWebView2WebResourceContext.All);
                }
                catch (Exception authFiltEx)
                {
                    _logCallback?.Invoke(string.Format("[{0}] AUTH filter registration warning: {1}", _instanceId, authFiltEx.Message));
                }
                _webView.CoreWebView2.WebResourceResponseReceived += OnAuthNetResponse;



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
                        targetUrl += string.Format("{0}buildVersion={2}&versionCode={3}&appVersion={1}",
                            sep,
                            Uri.EscapeDataString(_options.AppVersion),
                            Uri.EscapeDataString(_options.BuildVersion),
                            Uri.EscapeDataString(_options.VersionCode));
                    }

                    _logCallback?.Invoke(string.Format("[{0}] Navigating to: {1}", _instanceId, targetUrl));
                    // Background: a manifest check must never delay the first paint,
                    // and a 403 from the CDN must not stall navigation either.
                    var updaterLog = _logCallback;
                    int updaterPort = _options.ProxyPort;
                    System.Threading.ThreadPool.QueueUserWorkItem(delegate
                    {
                        ClientBundle.EnsureUpToDate(updaterPort, updaterLog);
                    });
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

        private async void OnAuthNetResponse(object sender, CoreWebView2WebResourceResponseReceivedEventArgs e)
        {
            try
            {
                string uri = e.Request.Uri;
                int status = e.Response.StatusCode;
                bool isTokenCall = uri.Contains("/token") || uri.Contains("create-guest") || uri.Contains("validate-guest");
                bool isFailure = status >= 400;
                if (uri.Contains("access_token") || uri.Contains("refresh_token")) isTokenCall = false;
                if (!isTokenCall && !isFailure) return;

                // Body capture is expensive (full GetContentAsync round trip);
                // pay it only for auth flows. CDN 4xx storms (doomed map ids)
                // are already fully logged by the relay's requests.log, so
                // those stay header-only here.
                string body = "";
                bool readBody = isTokenCall || (isFailure && !CdnAssetUriRe.IsMatch(uri));
                if (readBody)
                {
                    try
                    {
                        using (var content = await e.Response.GetContentAsync())
                        {
                            if (content != null)
                            {
                                using (var reader = new StreamReader(content))
                                {
                                    body = await reader.ReadToEndAsync();
                                    if (body.Length > 300) body = body.Substring(0, 300);
                                }
                            }
                        }
                    }
                    catch { }
                }

                string shortUri = uri.Length > 110 ? uri.Substring(0, 110) + "..." : uri;
                _logCallback?.Invoke(string.Format("[{0}] [AUTH-NET] {1} {2} -> {3}{4}",
                    _instanceId, e.Request.Method, shortUri, status,
                    body.Length > 0 ? " body=" + body : ""));
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] [AUTH-NET] handler error: {1}", _instanceId, ex.Message));
            }
        }

        /// <summary>
        /// Keep the game bundle in step with what the server serves.
        ///
        /// The client is not shipped in the APK: `build/script.js` is downloaded
        /// at runtime, and a cached copy silently goes stale when Ankama ships a
        /// new build. That is exactly what broke login: the cached bundle reported
        /// buildVersion 1.74.8 while the server had moved to 1.74.9, so the login
        /// socket was dropped and the client showed "a new version is available".
        ///
        /// The server publishes `manifest.json` with a version per file, so the
        /// check is one small request: compare, download only what changed. All
        /// traffic goes through the instance proxy, so the update never bypasses
        /// WARP. Any failure is logged and ignored -- a stale client must not stop
        /// the instance from launching.
        /// </summary>
        // The updater lives in ClientBundle now: it downloads to a staging tree,
        // re-applies the build-time patches, validates the result and only then
        // swaps files. See clientbundle.cs for why that matters.

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                string json = e.WebMessageAsJson;
                if (!json.Contains("\"type\":\"CONSOLE_LOG\"") && !json.Contains("\"type\":\"PLAY_LOG\""))
                {
                    _logCallback?.Invoke(string.Format("[{0}] [JS->C# Message]: {1}", _instanceId, json));
                }

                if (json.Contains("\"type\":\"PLAY_LOG\""))
                {
                    // Everything a human did, one JSON object per line. Written
                    // verbatim: parsing here would be a second schema to keep in
                    // sync with the page, and the whole point is that nothing is
                    // dropped on the way to disk.
                    PlayLog.Append(_instanceId, json);
                }
                else if (json.Contains("\"type\":\"CONSOLE_LOG\""))
                {
                    Match mText = WmConsoleTextRe.Match(json);
                    Match mLvl = WmConsoleLevelRe.Match(json);
                    string text = mText.Success ? Regex.Unescape(mText.Groups[1].Value) : json;
                    string lvl = mLvl.Success ? mLvl.Groups[1].Value.ToUpper() : "JS";
                    _logCallback?.Invoke(string.Format("[{0}] [WEB-{1}] {2}", _instanceId, lvl, text));
                }
                else if (json.Contains("\"type\":\"ping\""))
                {
                    _webView.CoreWebView2.PostWebMessageAsJson("{\"type\":\"pong\",\"reply\":\"Hello from C# WinForms Host!\"}");
                }
                else if (json.Contains("\"type\":\"FARMBOT_STATE\""))
                {
                    Match m = WmEnabledRe.Match(json);
                    if (m.Success)
                    {
                        _autoFightEnabled = bool.Parse(m.Groups[1].Value);
                        UpdateAutoFightButtonState();
                    }
                }
                else if (json.Contains("\"type\":\"AI_REQUEST\""))
                {
                    Match mAction = WmActionRe.Match(json);
                    if (mAction.Success && OnAiRequest != null)
                    {
                        OnAiRequest(_instanceId, mAction.Groups[1].Value);
                    }
                }
                else if (json.Contains("\"type\":\"TOGGLE_FULLSCREEN\""))
                {
                    ToggleFullscreen();
                }
                else if (json.Contains("\"type\":\"diagnostics_report\""))
                {
                    OnDiagnosticReport?.Invoke(_instanceId, json);
                }
                else if (json.Contains("\"type\":\"open_oauth\""))
                {
                    Match m = WmUrlRe.Match(json);
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
                else if (json.Contains("\"type\":\"webauth_exchange\""))
                {
                    Match mCode = WmCodeRe.Match(json);
                    string code = mCode.Success ? mCode.Groups[1].Value : null;
                    string shortCode = (code != null && code.Length > 8) ? code.Substring(0, 8) + "..." : code;
                    if (code != null && code == _lastGuestCode && !string.IsNullOrEmpty(_lastGuestTokensRaw))
                    {
                        string raw = _lastGuestTokensRaw;
                        _lastGuestTokensRaw = null;
                        _webView.CoreWebView2.PostWebMessageAsJson(
                            "{\"type\":\"webauth_tokens\",\"ok\":true,\"code\":\"" + code + "\",\"raw\":" + raw + "}");
                        _logCallback?.Invoke(string.Format("[{0}] [WEBAUTH] Delivered pre-exchanged guest tokens to game (code {1})", _instanceId, shortCode));
                    }
                    else
                    {
                        _webView.CoreWebView2.PostWebMessageAsJson(
                            "{\"type\":\"webauth_tokens\",\"ok\":false,\"code\":\"" + (code ?? "") + "\"}");
                        _logCallback?.Invoke(string.Format("[{0}] [WEBAUTH] No pre-exchanged tokens for code {1}; game falls back to its own fetch", _instanceId, shortCode));
                    }
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
                else if (json.Contains("\"type\":\"ACCOUNT_IDENTITY\""))
                {
                    HandleAccountIdentity(json);
                }
                else if (json.Contains("\"type\":\"GUEST_ACCOUNT_PERSIST\""))
                {
                    Match mLog = WmLoginRe.Match(json);
                    Match mPass = WmPasswordRe.Match(json);
                    if (mLog.Success && mPass.Success)
                    {
                        SavePermanentAccount(mLog.Groups[1].Value, mPass.Groups[1].Value);
                        RegisterAccountIdentity(mLog.Groups[1].Value);
                    }
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] WebMessage handling error: {1}", _instanceId, ex.Message));
            }
        }

        // Toggle this host window between normal chrome and borderless
        // fullscreen on the screen the form currently sits on. Triggered from
        // the page (F11 keydown -> TOGGLE_FULLSCREEN postMessage).
        private void ToggleFullscreen()
        {
            try
            {
                if (!_isFullscreen)
                {
                    _preFullscreenBounds = RestoreBounds;
                    _preFullscreenStyle = FormBorderStyle;
                    _preFullscreenWindowState = WindowState;

                    if (_toolbarPanel != null) _toolbarPanel.Visible = false;
                    WindowState = FormWindowState.Normal;
                    FormBorderStyle = FormBorderStyle.None;
                    Bounds = Screen.FromControl(this).Bounds;
                    _isFullscreen = true;
                    _logCallback?.Invoke(string.Format("[{0}] [FULLSCREEN] Entered fullscreen (F11 to exit)", _instanceId));
                }
                else
                {
                    WindowState = FormWindowState.Normal;
                    FormBorderStyle = _preFullscreenStyle;
                    if (_preFullscreenBounds.Width > 100 && _preFullscreenBounds.Height > 100)
                    {
                        Bounds = _preFullscreenBounds;
                    }
                    WindowState = _preFullscreenWindowState;
                    if (_toolbarPanel != null) _toolbarPanel.Visible = true;
                    _isFullscreen = false;
                    _logCallback?.Invoke(string.Format("[{0}] [FULLSCREEN] Exited fullscreen", _instanceId));
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] [FULLSCREEN] Toggle error: {1}", _instanceId, ex.Message));
            }
        }

        private void HandleAccountIdentity(string json)
        {
            try
            {
                Match mLog = WmLoginRe.Match(json);
                if (mLog.Success) RegisterAccountIdentity(mLog.Groups[1].Value);
            }
            catch { }
        }

        private void RegisterAccountIdentity(string login)
        {
            try
            {
                if (string.IsNullOrEmpty(login)) return;
                string conflict = FarmEngine.ClaimAccount(_instanceId, login);
                if (conflict == null)
                {
                    if (!string.Equals(_claimedLogin, login, StringComparison.OrdinalIgnoreCase))
                    {
                        _claimedLogin = login;
                        _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] Account {1} reserved for this instance", _instanceId, login));
                    }
                    try { File.WriteAllText(Path.Combine(_userDataDir, "account.identity"), login); } catch { }
                    return;
                }

                _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] BLOCKED: account {1} is already live on '{2}' - closing before a simultaneous same-account login (ban risk).", _instanceId, login, conflict));
                if (!_explicitAccount)
                {
                    try { File.WriteAllText(Path.Combine(_userDataDir, "force_fresh_identity.flag"), "1"); } catch { }
                    _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] Next launch will reset this shared identity and register a fresh guest account", _instanceId));
                }
                else
                {
                    _logCallback?.Invoke(string.Format("[{0}] [ACCOUNT GUARD] This account was set manually - change it in Edit so every instance uses a unique login", _instanceId));
                }
                if (InvokeRequired) BeginInvoke(new Action(Close)); else Close();
            }
            catch { }
        }

        private void SavePermanentAccount(string login, string password)
        {
            try
            {
                string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "accounts.txt");
                if (!File.Exists(path))
                {
                    string parent = Directory.GetParent(AppDomain.CurrentDomain.BaseDirectory)?.FullName;
                    if (parent != null && File.Exists(Path.Combine(parent, "accounts.txt")))
                        path = Path.Combine(parent, "accounts.txt");
                }
                string line = login + ":" + password;
                bool alreadyExists = false;
                if (File.Exists(path))
                {
                    var existing = File.ReadAllLines(path);
                    foreach (var l in existing)
                    {
                        if (l.StartsWith(login + ":", StringComparison.OrdinalIgnoreCase))
                        {
                            alreadyExists = true;
                            break;
                        }
                    }
                }
                if (!alreadyExists)
                {
                    File.AppendAllText(path, line + "\r\n");
                    _logCallback?.Invoke(string.Format("[{0}] Auto-harvested visitor credentials into accounts.txt: {1}", _instanceId, login));
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] Failed to persist visitor credentials: {1}", _instanceId, ex.Message));
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
                if (!CdnAssetUriRe.IsMatch(uri) && !StaticAssetUriRe.IsMatch(uri)) return;
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
                string ifNoneMatch = SafeGetHeader(e.Request, "If-None-Match");
                string ifModifiedSince = SafeGetHeader(e.Request, "If-Modified-Since");
                string inboundCookie = SafeGetHeader(e.Request, "Cookie");
                string requestUri = uri;
                var deferral = e.GetDeferral();
                _cdnInterceptTotal++;
                if (_cdnInterceptReqLog < 60)
                {
                    _cdnInterceptReqLog++;
                    _logCallback?.Invoke(string.Format("[{0}] CDN intercept req #{1} {2}",
                        _instanceId, _cdnInterceptReqLog,
                        requestUri.Length > 170 ? requestUri.Substring(0, 170) : requestUri));
                }

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
                            if ((DateTime.UtcNow - _cdnCookieRefreshedUtc).TotalSeconds < CdnCookieTtlSeconds)
                            {
                                cookies = _cdnCookieHeader;
                            }
                            else
                            {
                                try { cookies = await RunOnUiThreadAsync(GetCdnCookieHeaderAsync); }
                                catch { }
                            }
                        }
                        CdnFetchResult result = FetchCdnAsset(requestUri, method, range, cookies, ifNoneMatch, ifModifiedSince);
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

        private HttpWebRequest BuildCdnRequest(string uri, string method, string range, string cookieHeader, WebProxy proxy, string ifNoneMatch, string ifModifiedSince)
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
            if (!string.IsNullOrEmpty(ifNoneMatch))
            {
                try { req.Headers["If-None-Match"] = ifNoneMatch; } catch { }
            }
            if (!string.IsNullOrEmpty(ifModifiedSince))
            {
                try { req.Headers["If-Modified-Since"] = ifModifiedSince; } catch { }
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

        private CdnFetchResult FetchCdnAsset(string uri, string method, string range, string cookieHeader, string ifNoneMatch, string ifModifiedSince)
        {
            bool useProxy = _options != null && _options.ProxyPort > 0;

            HttpWebResponse resp = null;
            string lastError = null;

            // Serve intercepted assets through the local relay first: it
            // shares the on-disk cache with the JS shim path, applies the
            // hotlink headers server-side and keeps TLS handling in one
            // place. Falls through to a direct fetch when the relay is
            // unreachable or has no usable answer.
            Uri parsed = null;
            if (useProxy && Uri.TryCreate(uri, UriKind.Absolute, out parsed))
            {
                string relayUri = string.Format("http://127.0.0.1:{0}/__cdn/{1}{2}{3}",
                    _options.ProxyPort, parsed.Host, parsed.AbsolutePath,
                    string.IsNullOrEmpty(parsed.Query) ? "" : parsed.Query);
                try
                {
                    resp = (HttpWebResponse)BuildCdnRequest(relayUri, method, range, cookieHeader, null, ifNoneMatch, ifModifiedSince).GetResponse();
                }
                catch (WebException wex)
                {
                    resp = wex.Response as HttpWebResponse;
                    lastError = wex.Status + ": " + wex.Message;
                }
                catch (Exception ex)
                {
                    resp = null;
                    lastError = ex.GetType().Name + ": " + ex.Message;
                }
            }

            if (resp == null)
            {
                WebProxy proxy = useProxy ? new WebProxy("http://127.0.0.1:" + _options.ProxyPort) : null;
                try
                {
                    resp = (HttpWebResponse)BuildCdnRequest(uri, method, range, cookieHeader, proxy, ifNoneMatch, ifModifiedSince).GetResponse();
                }
                catch (WebException wex)
                {
                    resp = wex.Response as HttpWebResponse;
                    lastError = wex.Status + ": " + wex.Message;
                    if (resp == null)
                    {
                        // Local relay unreachable: retry straight once.
                        try { resp = (HttpWebResponse)BuildCdnRequest(uri, method, range, cookieHeader, null, ifNoneMatch, ifModifiedSince).GetResponse(); }
                        catch (WebException wex2) { resp = wex2.Response as HttpWebResponse; lastError = wex2.Status + ": " + wex2.Message; }
                        catch (Exception ex2) { resp = null; lastError = ex2.GetType().Name + ": " + ex2.Message; }
                    }
                }
                catch (Exception ex)
                {
                    resp = null;
                    lastError = ex.GetType().Name + ": " + ex.Message;
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
                string cc = resp.Headers["Cache-Control"];
                if (!string.IsNullOrEmpty(cc))
                {
                    sb.Append("Cache-Control: ").Append(cc).Append("\r\n");
                }
                else if (resp.StatusCode == HttpStatusCode.OK && method.Equals("GET", StringComparison.OrdinalIgnoreCase))
                {
                    // CDN asset paths are version-hashed: cache hard so map
                    // navigation stops re-fetching every sprite every time.
                    sb.Append("Cache-Control: public, max-age=604800, immutable\r\n");
                }

                var body = new MemoryStream(
                    resp.ContentLength > 0 && resp.ContentLength < (64L << 20) ? (int)resp.ContentLength : 0);
                bool wantBody = method.Equals("GET", StringComparison.OrdinalIgnoreCase) &&
                                resp.StatusCode != HttpStatusCode.NoContent &&
                                resp.StatusCode != HttpStatusCode.NotModified;
                if (wantBody)
                {
                    byte[] buf;
                    if (!CdnCopyBufferPool.TryDequeue(out buf)) buf = new byte[65536];
                    try
                    {
                        using (Stream rs = resp.GetResponseStream())
                        {
                            if (rs != null)
                            {
                                int n;
                                while ((n = rs.Read(buf, 0, buf.Length)) > 0) body.Write(buf, 0, n);
                            }
                        }
                    }
                    catch { }
                    finally
                    {
                        if (CdnCopyBufferPool.Count < 32) CdnCopyBufferPool.Enqueue(buf);
                    }
                }
                if (resp.StatusCode != HttpStatusCode.NotModified)
                {
                    sb.Append("Content-Length: ").Append(body.Length).Append("\r\n");
                }

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
                    "  function diag(t) {{ try {{ if (window.chrome && window.chrome.webview) window.chrome.webview.postMessage({{ type: 'CDN_RELAY_DIAG', text: '[DEEPLINK-DIAG] ' + t }}); }} catch (e) {{}} }}" +
                    "  diag('enter url=' + String(target).slice(0, 140));" +
                    "  try {{" +
                    "    if (typeof window.__onDeepLinkCallback === 'function') {{" +
                    "      diag('branch=www-callback');" +
                    "      window.__onDeepLinkCallback(target);" +
                    "      diag('branch=www-callback-returned');" +
                    "      return 'www-callback';" +
                    "    }} else if (window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function') {{" +
                    "      diag('branch=ionic-fire');" +
                    "      window.IonicDeeplink.fire({{ url: target }});" +
                    "      diag('branch=ionic-fire-returned');" +
                    "      return 'ionic-fire';" +
                    "    }} else {{" +
                    "      diag('branch=location-href');" +
                    "      window.location.href = target;" +
                    "      return 'location-href';" +
                    "    }}" +
                    "  }} catch (e) {{" +
                    "    diag('dispatch-error ' + (e && e.message ? e.message : String(e)));" +
                    "    return 'error:' + (e && e.message ? e.message : String(e));" +
                    "  }}" +
                    "}})();", escapedUri);

                DispatchDeepLinkDiagnosticsAsync(normalizedUri, dispatchJs);
            }
        }

        private async void DispatchDeepLinkDiagnosticsAsync(string normalizedUri, string dispatchJs)
        {
            string shortUri = normalizedUri.Length > 70 ? normalizedUri.Substring(0, 70) + "..." : normalizedUri;
            try
            {
                string probeJs =
                    "(function () { try { return JSON.stringify({" +
                    " callbackType: typeof window.__onDeepLinkCallback," +
                    " callbackIsCSharp: typeof window.__onDeepLinkCallback === 'function' && String(window.__onDeepLinkCallback).indexOf('window.dofus') !== -1," +
                    " hasIonicDeeplink: !!(window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function')," +
                    " hasWebAuth: !!(window.__webAuth && typeof window.__webAuth.connectThroughIonicDeepLink === 'function')," +
                    " hasDofusWebAuth: !!(window.dofus && window.dofus.webAuth && typeof window.dofus.webAuth.connectThroughIonicDeepLink === 'function')" +
                    " }); } catch (e) { return 'probe-error:' + e.message; } })();";
                string probeResult = await _webView.CoreWebView2.ExecuteScriptAsync(probeJs);
                _logCallback?.Invoke(string.Format("[{0}] [DEEPLINK-DIAG] probe {1} -> {2}", _instanceId, shortUri, probeResult));

                string dispatchResult = await _webView.CoreWebView2.ExecuteScriptAsync(dispatchJs);
                _logCallback?.Invoke(string.Format("[{0}] [DEEPLINK-DIAG] dispatch result: {1}", _instanceId, dispatchResult));
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] [DEEPLINK-DIAG] failed: {1}", _instanceId, ex.Message));
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

        private void ToggleAutoFight()
        {
            _autoFightEnabled = !_autoFightEnabled;
            UpdateAutoFightButtonState();
            if (IsReady && _webView != null && _webView.CoreWebView2 != null)
            {
                string js = string.Format("window.__FarmBot && window.__FarmBot.setEnabled({0});", _autoFightEnabled ? "true" : "false");
                _webView.CoreWebView2.ExecuteScriptAsync(js);
            }
        }

        private void UpdateAutoFightButtonState()
        {
            if (_btnAutoFight == null) return;
            if (InvokeRequired)
            {
                Invoke(new Action(UpdateAutoFightButtonState));
                return;
            }
            if (_autoFightEnabled)
            {
                _btnAutoFight.Text = "⚡ THX-01: ON";
                _btnAutoFight.BackColor = Color.FromArgb(16, 185, 129);
            }
            else
            {
                _btnAutoFight.Text = "⚡ THX-01: OFF";
                _btnAutoFight.BackColor = Color.FromArgb(51, 65, 85);
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
                using (var proc = Process.GetCurrentProcess())
                {
                    _lblMemory.Text = string.Format("RAM: {0:F1} MB (Commit: {1:F1} MB)",
                        proc.WorkingSet64 / (1024.0 * 1024.0),
                        proc.PrivateMemorySize64 / (1024.0 * 1024.0));
                }
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
                try { _webView.CoreWebView2.WebResourceResponseReceived -= OnAuthNetResponse; } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://*.cdn.ankama.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://*.cdn.ankama-games.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://static.ankama.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://auth.ankama.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://account.ankama.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://dt-proxy-production-login.ankama-games.com/*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("https://dt-proxy-production-early.ankama-games.com/*", CoreWebView2WebResourceContext.All); } catch { }
            }

            FarmEngine.ReleaseAccount(_instanceId);

            base.OnFormClosing(e);
            OnFormClosedByUser?.Invoke(_instanceId);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                FarmEngine.ReleaseAccount(_instanceId);
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
        private bool _deepLinkDispatched;
        private bool _continueMimicStarted;

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

                    int oauthProxyPort = (_options != null && _options.ProxyPort > 0)
                        ? _options.ProxyPort
                        : (_parentForm != null && _parentForm.Options != null ? _parentForm.Options.ProxyPort : 0);

                    if (oauthProxyPort > 0)
                    {
                        args.AppendFormat("--proxy-server=\"http://127.0.0.1:{0}\" ", oauthProxyPort);
                    }
                    else if (_options != null && !string.IsNullOrEmpty(_options.Proxy) && !_options.Proxy.Contains("DIRECT"))
                    {
                        args.AppendFormat("--proxy-server=\"{0}\" ", _options.Proxy);
                    }

                    args.Append("--enable-gpu-rasterization --touch-events=enabled --disable-blink-features=AutomationControlled --force-webrtc-ip-handling-policy=disable_non_proxied_udp --disable-features=WebRtcHideLocalIpsWithMdns ");
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

                string mobileUa = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36";
                if (!string.IsNullOrEmpty(_initialUrl) && _initialUrl.Contains("ankama"))
                {
                    s.UserAgent = mobileUa;
                }
                else if (!string.IsNullOrEmpty(cleanUa))
                {
                    s.UserAgent = cleanUa;
                }

                _webView.CoreWebView2.NavigationStarting += async (sdr, ev) =>
                {
                    string u = ev.Uri;
                    _logCallback?.Invoke("[OAUTH-POPUP] Navigating: " + u);

                    if (u.Contains("accounts.google.com"))
                    {
                        s.UserAgent = cleanUa;
                    }
                    else if (u.Contains("ankama"))
                    {
                        s.UserAgent = mobileUa;
                    }

                    if (u.StartsWith("dofustouch://", StringComparison.OrdinalIgnoreCase))
                    {
                        ev.Cancel = true;
                        _deepLinkDispatched = true;
                        _logCallback?.Invoke("[OAUTH-POPUP] Intercepted OAuth completion code redirect: " + u);
                        await CopyAuthCookiesToParentAsync();
                        _parentForm.HandleDeepLinkUri(u);
                        this.Close();
                        return;
                    }
                };

                _webView.CoreWebView2.NavigationCompleted += async (sdrN, evN) =>
                {
                    if (evN.IsSuccess && _webView.CoreWebView2 != null)
                    {
                        string docTitle = _webView.CoreWebView2.DocumentTitle;
                        if (!string.IsNullOrEmpty(docTitle))
                        {
                            _lblTitle.Text = "🔒 " + docTitle;
                        }
                        try
                        {
                            string curUrl = _webView.CoreWebView2.Source;
                            if (!string.IsNullOrEmpty(curUrl) && curUrl.Contains("login-authorized") && curUrl.Contains("code="))
                            {
                                if (_continueMimicStarted)
                                {
                                    return;
                                }
                                _continueMimicStarted = true;
                                _logCallback?.Invoke("[OAUTH-POPUP] login-authorized loaded; mimicking user CONTINUE click: " + curUrl);

                                await Task.Delay(2500);

                                try
                                {
                                    string clickJs =
                                        "(function(){try{" +
                                        " var all=document.querySelectorAll('a,button,input,[role=button],div,span');" +
                                        " var best=null,bestLen=100000;" +
                                        " for(var i=0;i<all.length;i++){" +
                                        "  var e=all[i];var t=String(e.textContent||e.value||'').trim();" +
                                        "  if(!t||t.length>120)continue;" +
                                        "  if(!/continue/i.test(t))continue;" +
                                        "  if(t.length<bestLen){best=e;bestLen=t.length;}" +
                                        " }" +
                                        " if(!best)return 'no-continue-control';" +
                                        " var info=best.tagName+' cls='+String(best.className).slice(0,40)+' href='+(best.getAttribute?String(best.getAttribute('href')||''):'')+' text='+String(best.textContent||'').trim().slice(0,40);" +
                                        " try{best.click();}catch(e){return 'click-threw: '+String(e);}" +
                                        " return 'clicked: '+info.slice(0,240);" +
                                        "}catch(e){return 'click-script-error: '+String(e);}})()";
                                    // The popup can be torn down during the 2.5 s delay
                                    // above; ExecuteScriptAsync on a dead CoreWebView2 throws
                                    // a NullReferenceException, which used to fill the log
                                    // with "CONTINUE click failed" and no explanation.
                                    if (_webView == null || _webView.CoreWebView2 == null)
                                    {
                                        _logCallback?.Invoke("[OAUTH-POPUP] CONTINUE skipped: the popup closed first");
                                    }
                                    else
                                    {
                                        string clickRes = await _webView.CoreWebView2.ExecuteScriptAsync(clickJs);
                                        _logCallback?.Invoke("[OAUTH-POPUP] CONTINUE click result: " + clickRes);
                                    }
                                }
                                catch (Exception cex)
                                {
                                    _logCallback?.Invoke("[OAUTH-POPUP] CONTINUE click failed: " + cex.Message);
                                }

                                await Task.Delay(4000);

                                if (!_deepLinkDispatched)
                                {
                                    _logCallback?.Invoke("[OAUTH-POPUP] CONTINUE produced no deep link; dispatching directly: " + curUrl);
                                    await CopyAuthCookiesToParentAsync();
                                    _parentForm.HandleDeepLinkUri(curUrl);
                                    this.Close();
                                }
                                return;
                            }

                            if (!string.IsNullOrEmpty(curUrl) && curUrl.Contains("ankama"))
                            {
                                string txt = await _webView.CoreWebView2.ExecuteScriptAsync("(document.body && document.body.innerText ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 220)");
                                if (!string.IsNullOrEmpty(txt) && txt != "\"\"")
                                {
                                    _logCallback?.Invoke("[OAUTH-POPUP] Page text: " + txt);
                                }
                            }
                        }
                        catch { }
                    }
                };

                try
                {
                    _webView.CoreWebView2.AddWebResourceRequestedFilter("https://auth.ankama.com/*", CoreWebView2WebResourceContext.All);
                    _webView.CoreWebView2.WebResourceResponseReceived += (sdrR, evR) =>
                    {
                        try
                        {
                            int st = evR.Response.StatusCode;
                            if (st >= 400)
                            {
                                string u = evR.Request.Uri;
                                if (u.Length > 110) u = u.Substring(0, 110) + "...";
                                _logCallback?.Invoke("[OAUTH-POPUP] [AUTH-NET] " + evR.Request.Method + " " + u + " -> " + st);
                            }
                        }
                        catch { }
                    };
                }
                catch { }

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
                            _logCallback?.Invoke("[OAUTH-POPUP] Cookie copy: " + c.Name + " @" + c.Domain + c.Path + " sameSite=" + c.SameSite);
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

        private async Task DiagnosticExchangeProbeAsync(string interceptedUri)
        {
            try
            {
                Match codeMatch = Regex.Match(interceptedUri, "[?&]code=([A-Za-z0-9_\\-]+)");
                if (!codeMatch.Success)
                {
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe: no code in intercepted uri");
                    return;
                }
                string code = codeMatch.Groups[1].Value;

                string webAuthType = "";
                try
                {
                    string typeJson = await _parentForm.ExecuteScriptAsync("(function(){try{return localStorage.getItem('WEBAUTH_TYPE')||'';}catch(e){return '';}})()");
                    if (!string.IsNullOrEmpty(typeJson) && typeJson.Length > 2) webAuthType = Regex.Unescape(typeJson.Trim('"'));
                }
                catch { }
                if (webAuthType != "guest")
                {
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe: skipped (WEBAUTH_TYPE=" + webAuthType + ")");
                    return;
                }

                string verifier = null;
                try
                {
                    string verJson = await _parentForm.ExecuteScriptAsync("(function(){try{return localStorage.getItem('CODE_VERIFIER')||'';}catch(e){return '';}})()");
                    if (!string.IsNullOrEmpty(verJson) && verJson.Length > 2)
                    {
                        verifier = Regex.Unescape(verJson.Trim('"'));
                    }
                }
                catch (Exception ex)
                {
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe: verifier read failed: " + ex.Message);
                }

                if (string.IsNullOrEmpty(verifier))
                {
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe: verifier empty; aborting probe (code len " + code.Length + ")");
                    return;
                }

                _logCallback?.Invoke("[OAUTH-POPUP] exchange probe: guest exchange starting (code len=" + code.Length + " verifier len=" + verifier.Length + ")");

                string body = "grant_type=authorization_code&client_id=18&code=" + code + "&code_verifier=" + verifier + "&redirect_uri=dofustouch://authorized";

                // Test A: repeat the exchange inside the auth popup itself, where
                // the SameSite=Lax aws-waf-token cookie is first-party.
                string rawTokens = null;
                try
                {
                    string js =
                        "(function(){ window.__popupExchangeStatus=''; window.__popupExchangeBody='';" +
                        " fetch('https://auth.ankama.com/token',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:'" + body + "'})" +
                        " .then(function(r){return r.text().then(function(t){window.__popupExchangeStatus=String(r.status); window.__popupExchangeBody=t;});}," +
                        " function(e){window.__popupExchangeStatus='ERR'; window.__popupExchangeBody=String(e);});" +
                        " return 'started'; })()";
                    await _webView.CoreWebView2.ExecuteScriptAsync(js);
                    string popupStatus = "";
                    for (int i = 0; i < 20; i++)
                    {
                        await Task.Delay(300);
                        string sJson = await _webView.CoreWebView2.ExecuteScriptAsync("window.__popupExchangeStatus||''");
                        if (!string.IsNullOrEmpty(sJson) && sJson != "\"\"")
                        {
                            popupStatus = Regex.Unescape(sJson.Trim('"'));
                            break;
                        }
                    }
                    string bJson = await _webView.CoreWebView2.ExecuteScriptAsync("window.__popupExchangeBody||''");
                    string popupBody = string.IsNullOrEmpty(bJson) ? "" : Regex.Unescape(bJson.Trim('"'));
                    bool popupOk = popupStatus == "200" && popupBody.Length > 0;
                    string popupSnippet = popupBody.Length > 300 ? popupBody.Substring(0, 300) : popupBody;
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe (popup ctx): " + popupStatus + " " + (popupOk ? TokenSummary(popupBody) : popupSnippet));
                    if (popupOk) rawTokens = popupBody;
                }
                catch (Exception ex)
                {
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe (popup ctx) failed: " + ex.Message);
                }

                // Test B variants: host-side exchange. Native requests can send the
                // popup's WAF cookie regardless of SameSite.
                if (rawTokens == null)
                {
                    string cookieHeader = "";
                    try
                    {
                        var cookies = await _webView.CoreWebView2.CookieManager.GetCookiesAsync("https://auth.ankama.com/");
                        var sb = new StringBuilder();
                        foreach (var c in cookies)
                        {
                            if (sb.Length > 0) sb.Append("; ");
                            sb.Append(c.Name).Append('=').Append(c.Value);
                        }
                        cookieHeader = sb.ToString();
                    }
                    catch { }

                    string desktopUa = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36";
                    string mobileUa = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36";

                    rawTokens = TryNativeExchange(body, cookieHeader, desktopUa, true, "native desktop+cookies");
                    if (rawTokens == null) rawTokens = TryNativeExchange(body, cookieHeader, mobileUa, false, "native mobile+cookies");
                    if (rawTokens == null) rawTokens = TryNativeExchange(body, "", mobileUa, true, "native mobile no-cookie");
                }

                if (rawTokens != null)
                {
                    _parentForm.SetGuestExchangeResult(code, rawTokens);
                    _logCallback?.Invoke("[OAUTH-POPUP] exchange probe: SUCCESS - guest tokens pre-exchanged and stored for host delivery");
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke("[OAUTH-POPUP] exchange probe error: " + ex.Message);
            }
        }

        private string TryNativeExchange(string body, string cookieHeader, string userAgent, bool sendOrigin, string label)
        {
            try
            {
                var webReq = (System.Net.HttpWebRequest)System.Net.WebRequest.Create("https://auth.ankama.com/token");
                webReq.Method = "POST";
                webReq.ContentType = "application/x-www-form-urlencoded";
                webReq.Timeout = 15000;
                int proxyPort = (_options != null && _options.ProxyPort > 0)
                    ? _options.ProxyPort
                    : (_parentForm != null && _parentForm.Options != null ? _parentForm.Options.ProxyPort : 0);
                if (proxyPort > 0)
                {
                    // Keep the host-side exchange on the same per-instance egress
                    // IP as the popup (the local Go relay with its upstream).
                    webReq.Proxy = new System.Net.WebProxy("http://127.0.0.1:" + proxyPort);
                }
                if (!string.IsNullOrEmpty(userAgent)) webReq.UserAgent = userAgent;
                if (sendOrigin) webReq.Headers["Origin"] = "https://dofus.local";
                if (!string.IsNullOrEmpty(cookieHeader)) webReq.Headers["Cookie"] = cookieHeader;
                byte[] bodyBytes = System.Text.Encoding.UTF8.GetBytes(body);
                webReq.ContentLength = bodyBytes.Length;
                using (var reqStream = webReq.GetRequestStream())
                {
                    reqStream.Write(bodyBytes, 0, bodyBytes.Length);
                }
                int status = 0;
                string respBody = "";
                try
                {
                    using (var resp = (System.Net.HttpWebResponse)webReq.GetResponse())
                    {
                        status = (int)resp.StatusCode;
                        using (var rs = resp.GetResponseStream())
                        using (var reader = new System.IO.StreamReader(rs))
                        {
                            respBody = reader.ReadToEnd();
                        }
                    }
                }
                catch (System.Net.WebException wex)
                {
                    var er = wex.Response as System.Net.HttpWebResponse;
                    if (er == null) throw;
                    status = (int)er.StatusCode;
                    using (var rs = er.GetResponseStream())
                    using (var reader = new System.IO.StreamReader(rs))
                    {
                        respBody = reader.ReadToEnd();
                    }
                    er.Close();
                }
                bool ok = status == 200 && !string.IsNullOrEmpty(respBody);
                string snippet = respBody ?? "";
                if (snippet.Length > 300) snippet = snippet.Substring(0, 300);
                _logCallback?.Invoke("[OAUTH-POPUP] exchange probe (" + label + "): " + status + " " + (ok ? TokenSummary(respBody) : snippet));
                return ok ? respBody : null;
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke("[OAUTH-POPUP] exchange probe (" + label + ") failed: " + ex.Message);
                return null;
            }
        }

        private static string TokenSummary(string json)
        {
            try
            {
                int accessLen = TokenFieldLength(json, "access_token");
                int refreshLen = TokenFieldLength(json, "refresh_token");
                bool hasGuestCreds = json.Contains("guest_credentials") && !json.Contains("\"guest_credentials\":null");
                return "tokens access_len=" + accessLen + " refresh_len=" + refreshLen + " guest_credentials=" + hasGuestCreds;
            }
            catch { return "(token summary failed)"; }
        }

        private static int TokenFieldLength(string json, string key)
        {
            Match m = Regex.Match(json, "\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
            return m.Success ? m.Groups[1].Value.Length : -1;
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

    /// <summary>
    /// Append-only writer for the play recorder.
    ///
    /// One JSON object per line, written verbatim from the page: the point of a
    /// trace is that nothing is lost on the way to disk, and a parser here would
    /// be a second schema to keep in sync with the recorder in the page.
    ///
    /// Never logs typed text -- the page does not send input values at all, so a
    /// password cannot reach this file even by accident.
    /// </summary>
    internal static class PlayLog
    {
        private static readonly object _gate = new object();
        private static string _dir;
        private const long MaxBytes = 64L * 1024 * 1024;

        private static string Directory_
        {
            get
            {
                if (_dir != null) return _dir;
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string candidate = Path.Combine(baseDir, "dofus-network", "trajectories");
                try
                {
                    Directory.CreateDirectory(candidate);
                    _dir = candidate;
                }
                catch
                {
                    string fallback = Path.Combine(baseDir, "trajectories");
                    try { Directory.CreateDirectory(fallback); } catch { }
                    _dir = fallback;
                }
                return _dir;
            }
        }

        public static void Append(string instanceId, string json)
        {
            try
            {
                string safe = Regex.Replace(string.IsNullOrEmpty(instanceId) ? "default" : instanceId,
                                            "[^A-Za-z0-9_.-]", "_");
                string path = Path.Combine(Directory_, "play_" + safe + ".jsonl");
                lock (_gate)
                {
                    try
                    {
                        FileInfo info = new FileInfo(path);
                        if (info.Exists && info.Length >= MaxBytes)
                        {
                            string rotated = Path.Combine(
                                Directory_,
                                "play_" + safe + "." + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss") + ".jsonl");
                            File.Move(path, rotated);
                        }
                    }
                    catch { }
                    // UTF8 without BOM: a BOM at the head of a .jsonl file breaks
                    // every naive line parser, and this file exists to be parsed.
                    File.AppendAllText(path, json + Environment.NewLine,
                                       new UTF8Encoding(false));
                }
            }
            catch
            {
                // Recording must never take the game down.
            }
        }
    }
}
