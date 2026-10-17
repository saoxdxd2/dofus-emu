using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
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

        public event Action<string, string> OnDiagnosticReport;
        public event Action<string> OnFormClosedByUser;

        // Embedded Cordova compatibility shim: emulates Cordova runtime so Dofus Touch / hybrid web assets run standalone
        private const string CordovaShimScript = @"(function() {
    'use strict';
    if (typeof window === 'undefined') return;
    if (window.cordova) return;

    window.cordova = {
        platformId: 'android',
        version: '10.0.0',
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
            }
            catch (Exception ex)
            {
                _logCallback?.Invoke(string.Format("[{0}] WebMessage handling error: {1}", _instanceId, ex.Message));
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
            }

            base.OnFormClosing(e);
            OnFormClosedByUser?.Invoke(_instanceId);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
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
}
