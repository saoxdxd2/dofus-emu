using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
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
                var safeUrl = (urlStr || '').replace('dofustouch://', 'https://dofus.local/');
                var parsedUrl = new URL(safeUrl);
                var host = parsedUrl.host || 'authorized';
                var path = parsedUrl.pathname || '';
                var queryString = parsedUrl.search ? parsedUrl.search.substring(1) : '';

                var params = {};
                if (queryString) {
                    queryString.split('&').forEach(function(part) {
                        var kv = part.split('=');
                        if (kv.length >= 2) params[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1]);
                    });
                }

                var deepLinkData = { host: host, path: path, queryString: queryString, url: urlStr };
                if (window.IonicDeeplink && typeof window.IonicDeeplink.fire === 'function') {
                    window.IonicDeeplink.fire(deepLinkData);
                }
                if (window.dofus && window.dofus.webAuth && typeof window.dofus.webAuth.connectThroughIonicDeepLink === 'function') {
                    window.dofus.webAuth.connectThroughIonicDeepLink(params);
                }
            } catch (e) {
                console.error('[Auth] Failed to parse callback URL:', e);
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
            _btnReload.Click += (s, e) => { if (IsReady && _webView != null && _webView.CoreWebView2 != null) _webView.Reload(); };
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
                    args.AppendFormat("--proxy-server=\"{0}\" ", _options.Proxy);
                }

                // Performance & GPU rendering flags
                args.Append("--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist ");

                // Native mobile touch and automation suppression switches
                args.Append("--touch-events=enabled --disable-blink-features=AutomationControlled ");

                // Background CPU optimization: throttle background window timers to preserve host CPU
                if (_options.EnableBackgroundThrottling)
                {
                    args.Append("--disable-background-timer-throttling=false --disable-renderer-backgrounding=false ");
                }

                envOptions.AdditionalBrowserArguments = args.ToString().Trim();

                var env = await CoreWebView2Environment.CreateAsync(null, _userDataDir, envOptions);
                await _webView.EnsureCoreWebView2Async(env);
                _env = env;

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

                // 1. Synchronously inject Mobile Disguise script (closes the blackbox completely)
                if (!string.IsNullOrEmpty(_options.DisguiseScript))
                {
                    await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(_options.DisguiseScript);
                    _logCallback?.Invoke(string.Format("[{0}] Registered mobile spoofing disguise script in CoreWebView2.", _instanceId));
                }

                // 2. Synchronously inject Cordova compatibility polyfill
                await _webView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(CordovaShimScript);

                // 3. Map virtual origin https://dofus.local/ to standalone game bundle
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
                _logCallback?.Invoke(string.Format("[{0}] [JS->C# Message]: {1}", _instanceId, json));

                if (json.Contains("\"type\":\"ping\""))
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
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] WebMessage handling error: {1}", _instanceId, ex.Message));
            }
        }

        private void OnNewWindowRequested(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            e.Handled = true;
            string targetUrl = e.Uri;
            _logCallback?.Invoke(string.Format("[{0}] Intercepted new window request: {1}", _instanceId, targetUrl));
            OpenOAuthDialog(targetUrl);
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
                string escapedUri = uri.Replace("\\", "\\\\").Replace("'", "\\'").Replace("\r", "").Replace("\n", "");
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
