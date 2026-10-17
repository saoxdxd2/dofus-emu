using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Media;
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
        private readonly ClientLaunchOptions _options;
        private readonly string _instanceId;
        private readonly string _displayName;
        private readonly string _userDataDir;
        private readonly string _entryUrl;
        private readonly Action<string> _logCallback;

        private CoreWebView2Environment _env;
        private OAuthLoginForm _activeOAuthDialog;

        private WebView2 _webView;
        private Panel _toolbarPanel;
        private Label _lblStatus;
        private Label _lblMemory;
        private Button _btnDevTools;
        private Button _btnReload;
        private System.Windows.Forms.Timer _telemetryTimer;

        public string InstanceId { get { return _instanceId; } }
        public bool IsReady { get; private set; }
        public string LastGpuVendor { get; private set; }
        public string LastGpuRenderer { get; private set; }
        public int BrowserProcessId { get; private set; }
        public WebView2 BrowserControl { get { return _webView; } }

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
            MinimumSize = new Size(640, 400);
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

            _btnDevTools = new Button
            {
                Text = "🛠️ DevTools",
                Size = new Size(88, 24),
                Location = new Point(Width - 190, 4),
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
                Location = new Point(Width - 94, 4),
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

                // Proxy integration
                if (_options.ProxyPort > 0)
                {
                    args.AppendFormat("--proxy-server=\"http://127.0.0.1:{0}\" ", _options.ProxyPort);
                }
                else if (!string.IsNullOrEmpty(_options.Proxy) && !_options.Proxy.Contains("DIRECT"))
                {
                    string rawProxy = _options.Proxy.Trim();
                    if (rawProxy.StartsWith("[🌐]")) rawProxy = rawProxy.Substring(4).Trim();
                    if (rawProxy.StartsWith("[⚡]") || rawProxy.Contains("Accelerator") || rawProxy.Contains("127.0.0.1:8880"))
                    {
                        bool isAccLive = false;
                        try
                        {
                            using (var tcp = new System.Net.Sockets.TcpClient())
                            {
                                var ar = tcp.BeginConnect("127.0.0.1", 8880, null, null);
                                if (ar.AsyncWaitHandle.WaitOne(100))
                                {
                                    tcp.EndConnect(ar);
                                    isAccLive = true;
                                }
                            }
                        }
                        catch { }
                        if (isAccLive) args.Append("--proxy-server=\"http://127.0.0.1:8880\" ");
                    }
                    else
                    {
                        var m = Regex.Match(rawProxy, @"(?:https?://)?([0-9a-zA-Z.-]+:[0-9]+)");
                        if (m.Success)
                        {
                            args.AppendFormat("--proxy-server=\"http://{0}\" ", m.Groups[1].Value);
                        }
                    }
                }

                // Performance & GPU rendering flags
                args.Append("--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist ");

                // Native mobile touch and automation suppression switches
                args.Append("--touch-events=enabled --disable-blink-features=AutomationControlled ");

                // Allow cross-origin requests from virtual host https://dofus.local to Ankama Auth & CDN
                args.Append("--disable-web-security --allow-running-insecure-content ");

                // Background CPU optimization: throttle background window timers to preserve host CPU
                if (_options.EnableBackgroundThrottling)
                {
                    args.Append("--disable-background-timer-throttling=false --disable-renderer-backgrounding=false ");
                }

                envOptions.AdditionalBrowserArguments = args.ToString().Trim();

                var env = await CoreWebView2Environment.CreateAsync(null, _userDataDir, envOptions);
                await _webView.EnsureCoreWebView2Async(env);
                _env = env;

                // Enable native desktop mouse-to-touch emulation so drag/slide/drawers and map panning work seamlessly
                try
                {
                    await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setTouchEmulationEnabled", "{\"enabled\":true,\"maxTouchPoints\":5}");
                    await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setEmitTouchEventsForMouse", "{\"enabled\":true,\"configuration\":\"desktop\"}");
                    _logCallback?.Invoke(string.Format("[{0}] Enabled native desktop touch emulation for mouse drag/swipe gestures.", _instanceId));
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

                // 4. Map virtual origin https://dofus.local/ to standalone game bundle
                string clientDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop", "client", "www");
                if (!Directory.Exists(clientDir)) clientDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "client", "www");
                if (!Directory.Exists(clientDir)) clientDir = Path.Combine(Directory.GetCurrentDirectory(), "desktop", "client", "www");
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

                // Wire WebResourceRequested filters for Ankama CDN / API and tactical map mocks
                _webView.CoreWebView2.AddWebResourceRequestedFilter("*ankama*", CoreWebView2WebResourceContext.All);
                _webView.CoreWebView2.AddWebResourceRequestedFilter("*4624.json*", CoreWebView2WebResourceContext.All);
                _webView.CoreWebView2.WebResourceRequested += OnWebResourceRequested;

                IsReady = true;
                _lblStatus.Text = string.Format("⚡ WebView2 Engine Active ({0})", _options.Profile != null ? _options.Profile.DisplayName : "Direct");
                _lblStatus.ForeColor = Color.FromArgb(74, 222, 128);

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
            if (IsReady && _webView != null && _webView.CoreWebView2 != null)
            {
                try
                {
                    _webView.CoreWebView2.ExecuteScriptAsync(
                        "if(window.gui&&typeof window.gui._resizeUi==='function'){window.gui._resizeUi();}else{window.dispatchEvent(new Event('resize'));}if(window.isoEngine&&window.isoEngine.mapScene&&typeof window.isoEngine.mapScene.requireCompleteRefresh==='function'){window.isoEngine.mapScene.requireCompleteRefresh();}"
                    );
                    BeginInvoke(new Action(async () =>
                    {
                        await Task.Delay(150);
                        if (IsReady && _webView != null && _webView.CoreWebView2 != null)
                        {
                            try
                            {
                                await _webView.CoreWebView2.ExecuteScriptAsync(
                                    "if(window.gui&&typeof window.gui._resizeUi==='function'){window.gui._resizeUi();}if(window.isoEngine&&window.isoEngine.mapScene&&typeof window.isoEngine.mapScene.requireCompleteRefresh==='function'){window.isoEngine.mapScene.requireCompleteRefresh();}"
                                );
                            }
                            catch { }
                        }
                    }));
                }
                catch { }
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

                // 1. Intercept tactical theme 4624.json fallback to avoid 403 stall
                if (uri.IndexOf("4624.json", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    byte[] mockBytes = Encoding.UTF8.GetBytes(
                        "{\"id\":4624,\"cells\":[],\"midgroundLayer\":{},\"atlasLayout\":{\"useUiPath\":true,\"width\":1024,\"height\":1024,\"graphicsPositions\":{}}}"
                    );
                    var ms = new MemoryStream(mockBytes);
                    e.Response = _env.CreateWebResourceResponse(
                        ms, 200, "OK",
                        "Content-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, OPTIONS\r\n"
                    );
                    return;
                }

                // 2. Outbound User-Agent enforcement for Ankama API / CDN endpoints
                if (uri.IndexOf("ankama", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    string ua = (_options.Profile != null && !string.IsNullOrEmpty(_options.Profile.UserAgent))
                        ? _options.Profile.UserAgent
                        : "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36";
                    ua = Regex.Replace(ua, @";\s*wv\b", "", RegexOptions.IgnoreCase);
                    ua = Regex.Replace(ua, @"Version/[0-9.]+\s*", "", RegexOptions.IgnoreCase).Trim();
                    e.Request.Headers.SetHeader("User-Agent", ua);
                }

                // 3. Intercept static.ankama.com image assets to bypass missing CORS headers
                if (uri.IndexOf("static.ankama.com", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    uri.IndexOf("s.ankama.com", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    var deferral = e.GetDeferral();
                    Task.Run(() =>
                    {
                        try
                        {
                            var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(uri);
                            req.Method = "GET";
                            req.UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36";
                            req.Timeout = 8000;
                            using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                            {
                                var mem = new MemoryStream();
                                using (var s = resp.GetResponseStream())
                                {
                                    s.CopyTo(mem);
                                }
                                mem.Position = 0;
                                string ct = resp.ContentType ?? "image/png";
                                string headers = string.Format(
                                    "Content-Type: {0}\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, OPTIONS\r\n",
                                    ct);
                                e.Response = _env.CreateWebResourceResponse(mem, (int)resp.StatusCode, resp.StatusDescription, headers);
                            }
                        }
                        catch
                        {
                            // Fall back to natural browser request
                        }
                        finally
                        {
                            deferral.Complete();
                        }
                    });
                    return;
                }
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] WebResourceRequested error: {1}", _instanceId, ex.Message));
            }
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

        private void UpdateMemoryUsage()
        {
            if (IsDisposed || !IsHandleCreated) return;
            try
            {
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
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("*ankama*", CoreWebView2WebResourceContext.All); } catch { }
                try { _webView.CoreWebView2.RemoveWebResourceRequestedFilter("*4624.json*", CoreWebView2WebResourceContext.All); } catch { }
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
    /// Specialized Mobile OAuth dialog for Ankama Connect / Google sign-in.
    /// Runs with mobile Chrome identity (no embedded webview markers) and intercepts deep link redirects.
    /// </summary>
    public class OAuthLoginForm : Form
    {
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
                string cleanUa = (_options != null && _options.Profile != null && !string.IsNullOrEmpty(_options.Profile.UserAgent))
                    ? _options.Profile.UserAgent
                    : null;
                if (!string.IsNullOrEmpty(cleanUa))
                {
                    cleanUa = Regex.Replace(cleanUa, @";\s*wv\b", "", RegexOptions.IgnoreCase);
                    cleanUa = Regex.Replace(cleanUa, @"Version/[0-9.]+\s*", "", RegexOptions.IgnoreCase).Trim();
                }

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

                    args.Append("--enable-gpu-rasterization --touch-events=enabled --disable-blink-features=AutomationControlled --disable-web-security --allow-running-insecure-content ");
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

                if (_options != null && !string.IsNullOrEmpty(_options.DisguiseScript))
                {
                    await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(_options.DisguiseScript);
                }

                _webView.CoreWebView2.NavigationStarting += (sdr, ev) =>
                {
                    string u = ev.Uri;
                    _logCallback?.Invoke("[OAUTH-POPUP] Navigating: " + u);

                    if (u.StartsWith("dofustouch://", StringComparison.OrdinalIgnoreCase) ||
                        (u.Contains("login-authorized") && (u.Contains("code=") || u.Contains("error="))))
                    {
                        ev.Cancel = true;
                        _logCallback?.Invoke("[OAUTH-POPUP] Intercepted OAuth completion code redirect: " + u);
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
