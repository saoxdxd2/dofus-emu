using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

namespace DofusFarm.Engine
{
    public class WebView2Backend : IClientBackend
    {
        private readonly ConcurrentDictionary<string, WebView2ClientForm> _activeForms = new ConcurrentDictionary<string, WebView2ClientForm>(StringComparer.OrdinalIgnoreCase);
        private readonly ConcurrentDictionary<string, ClientInstanceState> _states = new ConcurrentDictionary<string, ClientInstanceState>(StringComparer.OrdinalIgnoreCase);
        private readonly ConcurrentDictionary<string, string> _latestDiagnostics = new ConcurrentDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        private string _installedVersion = null;

        public string Name { get { return "Standalone Microsoft Edge WebView2"; } }
        public BackendType Type { get { return BackendType.WebView2Standalone; } }

        public event Action<string> OnLog;
        public event Action<string, ClientInstanceState> OnStateChanged;

        /// <summary>Forwards in-page AI requests (the HUD's Auto button) to the host.</summary>
        public event Action<string, string> OnAiRequest;

        public bool IsSupported(out string failureReason)
        {
            failureReason = null;
            try
            {
                _installedVersion = CoreWebView2Environment.GetAvailableBrowserVersionString();
                if (string.IsNullOrEmpty(_installedVersion))
                {
                    failureReason = "Microsoft Edge WebView2 runtime is not installed on this system.";
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                failureReason = "WebView2 runtime query failed: " + ex.Message;
                return false;
            }
        }

        public void Initialize()
        {
            string reason;
            if (IsSupported(out reason))
            {
                OnLog?.Invoke(string.Format("[WEBVIEW2-BACKEND] Detected Edge WebView2 Runtime: {0}", _installedVersion));
            }
            else
            {
                OnLog?.Invoke(string.Format("[WEBVIEW2-BACKEND] ⚠️ {0}", reason));
            }
        }

        public void Start(ClientLaunchOptions options)
        {
            if (options == null || string.IsNullOrEmpty(options.InstanceId)) return;
            string id = options.InstanceId;

            // Check if already running
            WebView2ClientForm existing;
            if (_activeForms.TryGetValue(id, out existing) && !existing.IsDisposed)
            {
                SafeUiInvoke(existing, () =>
                {
                    existing.WindowState = FormWindowState.Normal;
                    existing.BringToFront();
                    existing.Activate();
                    existing.Focus();
                });
                return;
            }

            SetState(id, ClientInstanceState.Starting);

            // Calculate user data directory
            if (string.IsNullOrEmpty(options.UserDataDirectory))
            {
                options.UserDataDirectory = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DofusFarm", "webview2_profiles", id);
            }

            // Assign coherent mobile blackbox device profile if not explicitly chosen
            if (options.Profile == null)
            {
                int slotNum = 1;
                Match m = Regex.Match(id, @"(\d+)$");
                if (m.Success) int.TryParse(m.Groups[1].Value, out slotNum);
                options.Profile = DeviceCatalog.GetDefaultProfileForIndex(slotNum);
            }

            // Dynamically resolve live client & server protocol version
            DofusVersionInfo verInfo = DynamicVersionResolver.ResolveVersion(null, false, msg => OnLog?.Invoke(msg));
            if (string.IsNullOrEmpty(options.AppVersion)) options.AppVersion = verInfo.AppVersion;
            if (string.IsNullOrEmpty(options.BuildVersion)) options.BuildVersion = verInfo.BuildVersion;
            if (string.IsNullOrEmpty(options.VersionCode)) options.VersionCode = verInfo.VersionCode;

            // Prepare mobile disguise script to close the blackbox completely
            if (string.IsNullOrEmpty(options.DisguiseScript))
            {
                string asmDir = Path.GetDirectoryName(typeof(WebView2Backend).Assembly.Location);
                string[] scriptCandidates = new[]
                {
                    Path.Combine(asmDir ?? "", "scripts", "mobile-disguise.js"),
                    Path.Combine(asmDir ?? "", "mobile-disguise.js"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts", "mobile-disguise.js"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "mobile-disguise.js"),
                    Path.Combine(Directory.GetCurrentDirectory(), "scripts", "mobile-disguise.js")
                };

                string scriptPath = null;
                foreach (string c in scriptCandidates)
                {
                    if (!string.IsNullOrEmpty(c) && File.Exists(c)) { scriptPath = c; break; }
                }

                if (scriptPath != null)
                {
                    try
                    {
                        string raw = File.ReadAllText(scriptPath, Encoding.UTF8);
                        string serial = options.DeviceSerial ?? ((options.Profile != null ? options.Profile.SerialPrefix : "R58M") + "45F41EFD");
                        string uuid = options.DeviceUuid ?? "ca8ea76b0fe32f85";
                        options.DisguiseScript = FarmEngine.InterpolateDisguise(
                            raw, uuid, serial, options.Profile,
                            options.Profile != null ? options.Profile.UserAgent : "",
                            options.AppVersion, options.VersionCode, options.BuildVersion);
                    }
                    catch { }
                }
            }

            // Default entry URL points to our standalone Dofus Touch client if present, else test probe
            if (string.IsNullOrEmpty(options.EntryUrl))
            {
                string asmDir = Path.GetDirectoryName(typeof(WebView2Backend).Assembly.Location);
                string[] clientCandidates = new[]
                {
                    Path.Combine(asmDir ?? "", "desktop", "client", "www", "standalone.html"),
                    Path.Combine(asmDir ?? "", "client", "www", "standalone.html"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop", "client", "www", "standalone.html"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "client", "www", "standalone.html"),
                    Path.Combine(Directory.GetCurrentDirectory(), "desktop", "client", "www", "standalone.html"),
                    Path.Combine(Directory.GetCurrentDirectory(), "client", "www", "standalone.html")
                };

                bool foundClient = false;
                foreach (string c in clientCandidates)
                {
                    if (!string.IsNullOrEmpty(c) && File.Exists(c)) { foundClient = true; break; }
                }

                if (foundClient)
                {
                    options.EntryUrl = string.Format(
                        "https://dofus.local/standalone.html?buildVersion={1}&versionCode={2}&appVersion={0}",
                        Uri.EscapeDataString(options.AppVersion),
                        Uri.EscapeDataString(options.BuildVersion),
                        Uri.EscapeDataString(options.VersionCode));
                }
                else
                {
                    string[] probeCandidates = new[]
                    {
                        Path.Combine(asmDir ?? "", "desktop", "assets", "webview_test.html"),
                        Path.Combine(asmDir ?? "", "assets", "webview_test.html"),
                        Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop", "assets", "webview_test.html"),
                        Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "assets", "webview_test.html"),
                        Path.Combine(Directory.GetCurrentDirectory(), "desktop", "assets", "webview_test.html"),
                        Path.Combine(Directory.GetCurrentDirectory(), "assets", "webview_test.html")
                    };

                    string localProbe = null;
                    foreach (string c in probeCandidates)
                    {
                        if (!string.IsNullOrEmpty(c) && File.Exists(c)) { localProbe = c; break; }
                    }

                    if (localProbe != null) options.EntryUrl = new Uri(localProbe).AbsoluteUri;
                    else options.EntryUrl = "https://www.google.com";
                }
            }

            // Spawn Form on UI thread
            Action launchAction = () =>
            {
                try
                {
                    var form = new WebView2ClientForm(options, msg => OnLog?.Invoke(msg));
                    form.OnDiagnosticReport += (instId, json) =>
                    {
                        _latestDiagnostics[instId] = json;
                        OnLog?.Invoke(string.Format("[WEBVIEW2-TELEMETRY] [{0}]: Received capability probe report.", instId));
                    };
                    form.OnAiRequest += (instId, action) =>
                    {
                        var handler = OnAiRequest;
                        if (handler != null) handler(instId, action);
                    };
                    form.OnFormClosedByUser += instId =>
                    {
                        // The form can be closed long after a newer form replaced it in
                        // _activeForms (relaunch); only the currently registered form may clear
                        // the slot and flip the state to Stopped.
                        WebView2ClientForm current;
                        if (_activeForms.TryGetValue(instId, out current) && !ReferenceEquals(current, form)) return;
                        WebView2ClientForm removed;
                        _activeForms.TryRemove(instId, out removed);
                        SetState(instId, ClientInstanceState.Stopped);
                        OnLog?.Invoke(string.Format("[{0}] WebView2 client closed by user.", instId));
                        ScheduleAutoTile(200);
                    };

                    _activeForms[id] = form;
                    form.Show();
                    form.StartAsync().ContinueWith(t =>
                    {
                        if (t.IsFaulted)
                        {
                            SetState(id, ClientInstanceState.Faulted);
                            OnLog?.Invoke(string.Format("[{0}] Startup error: {1}", id, t.Exception != null ? t.Exception.GetBaseException().Message : "Unknown"));
                        }
                        else
                        {
                            SetState(id, ClientInstanceState.Running);
                            ScheduleAutoTile(350);
                        }
                    });
                }
                catch (Exception ex)
                {
                    SetState(id, ClientInstanceState.Faulted);
                    OnLog?.Invoke(string.Format("[WEBVIEW2-BACKEND] Failed to spawn Form for {0}: {1}", id, ex.Message));
                }
            };

            if (Application.OpenForms.Count > 0)
            {
                Control ui = Application.OpenForms[0];
                if (ui.InvokeRequired) ui.BeginInvoke(launchAction);
                else launchAction();
            }
            else
            {
                SetState(id, ClientInstanceState.Faulted);
                OnLog?.Invoke("[WEBVIEW2-BACKEND] No UI thread available to host the client window.");
            }
        }

        public void Stop(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return;
            SetState(instanceId, ClientInstanceState.Stopping);

            WebView2ClientForm form;
            if (_activeForms.TryRemove(instanceId, out form))
            {
                SafeUiInvoke(form, () =>
                {
                    try { form.Close(); } catch { }
                    try { form.Dispose(); } catch { }
                });
            }

            SetState(instanceId, ClientInstanceState.Stopped);
            ScheduleAutoTile(200);
        }

        public void Focus(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return;
            WebView2ClientForm form;
            if (_activeForms.TryGetValue(instanceId, out form) && !form.IsDisposed)
            {
                SafeUiInvoke(form, () =>
                {
                    form.WindowState = FormWindowState.Normal;
                    form.BringToFront();
                    form.Activate();
                    form.Focus();
                });
            }
        }

        private System.Threading.Timer _autoTileDebounceTimer;
        private readonly object _autoTileLock = new object();

        public void ScheduleAutoTile(int delayMs = 350)
        {
            lock (_autoTileLock)
            {
                try
                {
                    if (_autoTileDebounceTimer != null)
                    {
                        _autoTileDebounceTimer.Dispose();
                        _autoTileDebounceTimer = null;
                    }
                    _autoTileDebounceTimer = new System.Threading.Timer(_ =>
                    {
                        if (Application.OpenForms.Count > 0)
                        {
                            var main = Application.OpenForms[0];
                            if (main != null && !main.IsDisposed)
                            {
                                SafeUiInvoke(main, () => TileWindows(false));
                            }
                        }
                    }, null, delayMs, Timeout.Infinite);
                }
                catch { }
            }
        }

        public void TileWindows(bool edgeToEdge)
        {
            var forms = _activeForms.Values
                .Where(f => !f.IsDisposed)
                .OrderBy(f => f.InstanceId, StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (forms.Count == 0) return;

            Rectangle[] rects = DofusFarm.Native.Win32.ComputeLayout(forms.Count, edgeToEdge);
            for (int i = 0; i < forms.Count && i < rects.Length; i++)
            {
                var f = forms[i];
                var r = rects[i];
                SafeUiInvoke(f, () =>
                {
                    f.WindowState = FormWindowState.Normal;
                    f.Bounds = r;
                    f.BringToFront();
                });
            }
        }

        public void CascadeWindows()
        {
            var forms = _activeForms.Values.Where(f => !f.IsDisposed).ToList();
            if (forms.Count == 0) return;

            Rectangle wa = Screen.PrimaryScreen.WorkingArea;
            int width = Math.Min(640, (int)(wa.Width * 0.5));
            int height = Math.Min(800, (int)(wa.Height * 0.75));
            int stepX = 45, stepY = 45;

            for (int i = 0; i < forms.Count; i++)
            {
                var f = forms[i];
                int x = wa.X + (i * stepX);
                int y = wa.Y + (i * stepY);
                SafeUiInvoke(f, () =>
                {
                    f.WindowState = FormWindowState.Normal;
                    f.SetBounds(x, y, width, height);
                    f.BringToFront();
                });
            }
        }

        public List<int> GetAllBrowserProcessIds()
        {
            var pids = new List<int>();
            foreach (var f in _activeForms.Values)
            {
                if (!f.IsDisposed && f.BrowserProcessId > 0)
                {
                    pids.Add(f.BrowserProcessId);
                }
            }
            return pids;
        }

        public ICollection<string> GetActiveInstanceIds()
        {
            return _activeForms.Keys;
        }

        public ClientInstanceState GetState(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return ClientInstanceState.Stopped;
            ClientInstanceState state;
            if (_states.TryGetValue(instanceId, out state)) return state;
            return ClientInstanceState.Stopped;
        }

        public ClientDiagnostics GetDiagnostics(string instanceId)
        {
            var diag = new ClientDiagnostics
            {
                InstanceId = instanceId,
                Backend = BackendType.WebView2Standalone,
                State = GetState(instanceId)
            };

            WebView2ClientForm form;
            if (_activeForms.TryGetValue(instanceId, out form) && !form.IsDisposed)
            {
                diag.WorkingSetMb = form.GetWorkingSetMb();
                diag.PrivateCommitMb = form.GetPrivateCommitMb();
                string json;
                if (_latestDiagnostics.TryGetValue(instanceId, out json))
                {
                    diag.Details = json;
                }
            }

            return diag;
        }

        public WebView2ClientForm GetForm(string instanceId)
        {
            WebView2ClientForm form;
            _activeForms.TryGetValue(instanceId, out form);
            return form;
        }

        public void ExecuteScriptOnInstance(string instanceId, string script)
        {
            if (string.IsNullOrEmpty(script)) return;

            if (string.IsNullOrEmpty(instanceId) || string.Equals(instanceId, "all", StringComparison.OrdinalIgnoreCase))
            {
                foreach (var kvp in _activeForms)
                {
                    WebView2ClientForm f = kvp.Value;
                    if (f != null && !f.IsDisposed)
                        SafeUiInvoke(f, () => { _ = f.ExecuteScriptAsync(script); });
                }
                return;
            }

            WebView2ClientForm form;
            if (_activeForms.TryGetValue(instanceId, out form) && form != null && !form.IsDisposed)
            {
                SafeUiInvoke(form, () => { _ = form.ExecuteScriptAsync(script); });
            }
        }

        private void SetState(string instanceId, ClientInstanceState newState)
        {
            _states[instanceId] = newState;
            try { OnStateChanged?.Invoke(instanceId, newState); } catch { }
        }

        private static void SafeUiInvoke(Control c, Action action)
        {
            if (c == null || c.IsDisposed) return;
            try
            {
                if (c.InvokeRequired && c.IsHandleCreated) c.BeginInvoke(action);
                else action();
            }
            catch { }
        }

        public void Dispose()
        {
            lock (_autoTileLock)
            {
                if (_autoTileDebounceTimer != null) { try { _autoTileDebounceTimer.Dispose(); } catch { } _autoTileDebounceTimer = null; }
            }
            foreach (var kvp in _activeForms)
            {
                try
                {
                    SafeUiInvoke(kvp.Value, () =>
                    {
                        try { kvp.Value.Close(); } catch { }
                        try { kvp.Value.Dispose(); } catch { }
                    });
                }
                catch { }
            }
            _activeForms.Clear();
            _states.Clear();
        }
    }
}
