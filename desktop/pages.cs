// pages.cs - the secondary pages: Settings, Shortcuts, Logs.
//
// The main window used to be one long column: header, toolbar, grid, a 184 px AI
// strip and a status bar. Everything competed for the same vertical space, so the
// grid -- the thing you actually watch -- kept losing rows to panels you touch
// once a session.
//
// These pages give each of those jobs its own screen. The main page keeps the
// instance list and nothing else.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using DofusFarm.Engine;

namespace DofusFarm.UI
{
    internal static class UiKit
    {
        public static readonly Color Page = Color.FromArgb(20, 22, 28);
        public static readonly Color Card = Color.FromArgb(26, 28, 36);
        public static readonly Color Muted = Color.FromArgb(148, 163, 184);
        public static readonly Color Faint = Color.FromArgb(100, 116, 139);
        public static readonly Color Text = Color.FromArgb(226, 232, 240);
        public static readonly Color Accent = Color.FromArgb(129, 140, 248);
        public static readonly Color Input = Color.FromArgb(28, 30, 40);

        public static Label Heading(string text, int x, int y)
        {
            var l = new Label();
            l.Text = text;
            l.Font = new Font("Segoe UI", 11f, FontStyle.Bold);
            l.ForeColor = Text;
            l.AutoSize = true;
            l.Location = new Point(x, y);
            return l;
        }

        public static Label Sub(string text, int x, int y)
        {
            var l = new Label();
            l.Text = text;
            l.ForeColor = Muted;
            l.AutoSize = true;
            l.Location = new Point(x, y);
            return l;
        }

        public static Label Note(string text, int x, int y)
        {
            var l = new Label();
            l.Text = text;
            l.ForeColor = Faint;
            l.AutoSize = true;
            l.Location = new Point(x, y);
            return l;
        }

        public static Button Button(string text, int x, int y, int w, Color back, EventHandler onClick)
        {
            var b = new Button();
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(w, 30);
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.BackColor = back;
            b.ForeColor = Color.White;
            b.Font = new Font("Segoe UI", 8.5f, FontStyle.Bold);
            b.Cursor = Cursors.Hand;
            Color normal = back;
            b.MouseEnter += delegate { b.BackColor = Lighten(back); };
            b.MouseLeave += delegate { b.BackColor = normal; };
            if (onClick != null) b.Click += onClick;
            return b;
        }

        private static Color Lighten(Color c)
        {
            int r = Math.Min(255, c.R + 18), g = Math.Min(255, c.G + 18), b = Math.Min(255, c.B + 18);
            return Color.FromArgb(r, g, b);
        }

        public static CheckBox Check(string text, int x, int y, bool value)
        {
            var c = new CheckBox();
            c.Text = text;
            c.ForeColor = Text;
            c.AutoSize = true;
            c.Location = new Point(x, y);
            c.Checked = value;
            return c;
        }

        public static Label Field(string text, int x, int y)
        {
            var l = new Label();
            l.Text = text;
            l.ForeColor = Muted;
            l.AutoSize = true;
            l.Location = new Point(x, y);
            return l;
        }

        public static TextBox Entry(string text, int x, int y, int w)
        {
            // Fully qualified: inside UiKit, the bare name "TextBox" also matches
            // the RichTextBox factory below.
            var t = new System.Windows.Forms.TextBox();
            t.Text = text ?? "";
            t.Location = new Point(x, y);
            t.Size = new Size(w, 24);
            t.BackColor = Input;
            t.ForeColor = Text;
            t.BorderStyle = BorderStyle.FixedSingle;
            t.Font = new Font("Consolas", 9f);
            return t;
        }

        public static ComboBox Combo(string[] items, int selected, int x, int y, int w)
        {
            var c = new ComboBox();
            c.DropDownStyle = ComboBoxStyle.DropDownList;
            c.FlatStyle = FlatStyle.Flat;
            c.BackColor = Input;
            c.ForeColor = Text;
            c.Font = new Font("Segoe UI", 9f);
            c.Location = new Point(x, y);
            c.Size = new Size(w, 24);
            foreach (string it in items) c.Items.Add(it);
            if (selected >= 0 && selected < items.Length) c.SelectedIndex = selected;
            return c;
        }

        public static RichTextBox TextBox(string text, int x, int y, int w, int h)
        {
            var t = new RichTextBox();
            t.Location = new Point(x, y);
            t.Size = new Size(w, h);
            t.BackColor = Color.FromArgb(16, 18, 24);
            t.ForeColor = Color.FromArgb(203, 213, 225);
            t.BorderStyle = BorderStyle.None;
            t.Font = new Font("Consolas", 9f);
            t.ReadOnly = true;
            t.DetectUrls = false;
            t.Text = text;
            return t;
        }
    }

    /// <summary>Settings that actually change behaviour, not decoration.</summary>
    public sealed class SettingsPage : Panel
    {
        private readonly string _baseDir;
        private readonly Action<string> _log;
        private readonly Action _onApplyNetwork;

        private readonly Label _flagsState;
        private readonly CheckBox _chkManual;
        private readonly CheckBox _chkNoUpdate;

        private readonly ComboBox _mode;
        private readonly Label _modeNote;
        private readonly TextBox _upstream;
        private readonly TextBox _socksPort;
        private readonly TextBox _basePort;
        private readonly TextBox _extraArgs;
        private readonly TextBox _logPath;
        private readonly CheckBox _failClosed;
        private readonly Label _netState;
        private NetworkSettings _net;

        public SettingsPage(string baseDir, Action<string> log, Action onApplyNetwork)
        {
            _baseDir = baseDir;
            _log = log;
            _onApplyNetwork = onApplyNetwork;
            _net = NetworkSettings.For(baseDir);

            Dock = DockStyle.Fill;
            BackColor = UiKit.Page;
            Padding = new Padding(24, 20, 24, 20);
            AutoScroll = true;

            int y = 8;

            // ---- Login ----------------------------------------------------
            Controls.Add(UiKit.Heading("Login", 0, y));
            Controls.Add(UiKit.Note("Guest accounts are wiped by client updates; a real account is the durable option.", 0, y + 26));
            y += 54;

            _chkManual = UiKit.Check("Manual login - no auto-login, no guest seeding, no stored guest replay", 0, y, false);
            _chkManual.CheckedChanged += delegate { ApplyFlags(); };
            Controls.Add(_chkManual);

            _chkNoUpdate = UiKit.Check("Do not update the client bundle at launch (the server's build wins when on)", 0, y + 26, false);
            _chkNoUpdate.CheckedChanged += delegate { ApplyFlags(); };
            Controls.Add(_chkNoUpdate);

            _flagsState = UiKit.Note("", 0, y + 54);
            Controls.Add(_flagsState);
            y += 86;

            // ---- Network --------------------------------------------------
            Controls.Add(UiKit.Heading("Network", 0, y));
            Controls.Add(UiKit.Note("How every instance leaves this machine. Saved to network.ini and used by the relay, the launcher and the AI.", 0, y + 26));
            y += 52;

            Controls.Add(UiKit.Field("Mode", 0, y + 4));
            _mode = UiKit.Combo(new string[] {
                NetworkSettings.ModeLabel(NetworkSettings.ModeAuto),
                NetworkSettings.ModeLabel(NetworkSettings.ModeWireproxy),
                NetworkSettings.ModeLabel(NetworkSettings.ModeWarp),
                NetworkSettings.ModeLabel(NetworkSettings.ModeCustom),
                NetworkSettings.ModeLabel(NetworkSettings.ModeDirect)
            }, ModeIndex(_net.Mode), 74, y, 430);
            _mode.SelectedIndexChanged += delegate { UpdateModeUi(); };
            Controls.Add(_mode);
            y += 30;

            _modeNote = new Label();
            _modeNote.AutoSize = false;
            _modeNote.Location = new Point(74, y);
            _modeNote.Size = new Size(600, 34);
            _modeNote.ForeColor = UiKit.Faint;
            Controls.Add(_modeNote);
            y += 40;

            Controls.Add(UiKit.Field("Upstream", 0, y + 4));
            _upstream = UiKit.Entry(_net.Upstream, 74, y, 430);
            Controls.Add(_upstream);
            y += 30;

            Controls.Add(UiKit.Field("SOCKS port", 0, y + 4));
            _socksPort = UiKit.Entry(_net.SocksPort.ToString(), 74, y, 70);
            Controls.Add(_socksPort);

            Controls.Add(UiKit.Field("Relay base port", 164, y + 4));
            _basePort = UiKit.Entry(_net.BasePort.ToString(), 268, y, 70);
            Controls.Add(_basePort);

            _failClosed = UiKit.Check("Fail closed - never dial direct", 360, y, _net.FailClosed);
            Controls.Add(_failClosed);
            y += 32;

            Controls.Add(UiKit.Field("Extra args", 0, y + 4));
            _extraArgs = UiKit.Entry(_net.ExtraArgs, 74, y, 430);
            Controls.Add(_extraArgs);
            y += 30;

            Controls.Add(UiKit.Field("Log file", 0, y + 4));
            _logPath = UiKit.Entry(_net.LogPath, 74, y, 430);
            Controls.Add(_logPath);
            y += 34;

            Controls.Add(UiKit.Button("Save & apply", 0, y, 110, Color.FromArgb(37, 99, 235), delegate { SaveNetwork(); }));
            Controls.Add(UiKit.Button("Start tunnel", 118, y, 100, Color.FromArgb(22, 101, 52), delegate { StartTunnel(); }));
            Controls.Add(UiKit.Button("Stop tunnel", 226, y, 100, Color.FromArgb(51, 65, 85), delegate { StopTunnel(); }));
            Controls.Add(UiKit.Button("Test", 334, y, 70, Color.FromArgb(51, 65, 85), delegate { TestUpstream(); }));
            Controls.Add(UiKit.Button("Repair my network", 412, y, 140, Color.FromArgb(127, 29, 29), delegate { RepairNetwork(); }));
            Controls.Add(UiKit.Button("proxy.log", 560, y, 90, Color.FromArgb(51, 65, 85), delegate { OpenProxyLog(); }));
            y += 36;

            // A free proxy pool is mostly dead at any moment, and on an ISP that
            // blocks WARP's UDP it is the only egress that works at all. These two
            // buttons are the whole recovery path: find what is alive, then use it.
            Controls.Add(UiKit.Button("Use proxies.txt", 0, y, 130, Color.FromArgb(22, 101, 52), delegate { UseProxyPool(); }));
            Controls.Add(UiKit.Button("Scan proxies.txt", 138, y, 130, Color.FromArgb(51, 65, 85), delegate { ScanProxies(); }));
            Controls.Add(UiKit.Button("Edit proxies.txt", 276, y, 130, Color.FromArgb(51, 65, 85), delegate
            {
                OpenPath(Path.Combine(_baseDir, "proxies.txt"));
            }));
            y += 38;

            _netState = new Label();
            _netState.AutoSize = false;
            _netState.Location = new Point(0, y);
            _netState.Size = new Size(700, 60);
            _netState.ForeColor = UiKit.Muted;
            Controls.Add(_netState);
            y += 68;

            // ---- Folders --------------------------------------------------
            Controls.Add(UiKit.Heading("Folders", 0, y));
            y += 32;
            Controls.Add(UiKit.Button("Install folder", 0, y, 130, Color.FromArgb(51, 65, 85), delegate { OpenPath(_baseDir); }));
            Controls.Add(UiKit.Button("Trajectories", 140, y, 130, Color.FromArgb(51, 65, 85), delegate
            {
                OpenPath(Path.Combine(_baseDir, "dofus-network", "trajectories"));
            }));
            Controls.Add(UiKit.Button("Models", 280, y, 110, Color.FromArgb(51, 65, 85), delegate
            {
                OpenPath(Path.Combine(_baseDir, "dofus-network", "models"));
            }));
            Controls.Add(UiKit.Button("App data", 400, y, 110, Color.FromArgb(51, 65, 85), delegate
            {
                OpenPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm"));
            }));
            y += 48;

            // ---- AI -------------------------------------------------------
            Controls.Add(UiKit.Heading("AI", 0, y));
            y += 32;
            Controls.Add(UiKit.Button("Reset AI settings", 0, y, 150, Color.FromArgb(127, 29, 29), delegate { ResetAiSettings(); }));
            Controls.Add(UiKit.Note(AiSettings.PathFor(_baseDir), 162, y + 7));
            y += 48;

            LoadFlags();
            UpdateModeUi();
            RefreshNetState();
        }

        private static int ModeIndex(string mode)
        {
            string m = NetworkSettings.NormalizeMode(mode);
            if (m == NetworkSettings.ModeWireproxy) return 1;
            if (m == NetworkSettings.ModeWarp) return 2;
            if (m == NetworkSettings.ModeCustom) return 3;
            if (m == NetworkSettings.ModeDirect) return 4;
            return 0;                                  // auto
        }

        private string SelectedMode
        {
            get
            {
                switch (_mode.SelectedIndex)
                {
                    case 1: return NetworkSettings.ModeWireproxy;
                    case 2: return NetworkSettings.ModeWarp;
                    case 3: return NetworkSettings.ModeCustom;
                    case 4: return NetworkSettings.ModeDirect;
                    default: return NetworkSettings.ModeAuto;
                }
            }
        }

        private void UpdateModeUi()
        {
            string mode = SelectedMode;
            _modeNote.Text = NetworkSettings.ModeNote(mode);
            bool custom = mode == NetworkSettings.ModeCustom;
            _upstream.Enabled = custom;
            _upstream.BackColor = custom ? UiKit.Input : Color.FromArgb(22, 24, 30);
            bool tunnel = mode == NetworkSettings.ModeWireproxy || mode == NetworkSettings.ModeWarp;
            _socksPort.Enabled = tunnel;
            _failClosed.Enabled = mode != NetworkSettings.ModeDirect;
        }

        private void SaveNetwork()
        {
            int socks, basePort;
            if (!int.TryParse(_socksPort.Text.Trim(), out socks) || socks < 1 || socks > 65535)
            {
                SetState("SOCKS port must be 1-65535.", Color.FromArgb(248, 113, 113));
                return;
            }
            if (!int.TryParse(_basePort.Text.Trim(), out basePort) || basePort < 1024 || basePort > 65000)
            {
                SetState("Relay base port must be 1024-65000.", Color.FromArgb(248, 113, 113));
                return;
            }

            _net.Mode = SelectedMode;
            _net.Upstream = _upstream.Text.Trim();
            _net.SocksPort = socks;
            _net.BasePort = basePort;
            _net.FailClosed = _failClosed.Checked;
            _net.ExtraArgs = _extraArgs.Text.Trim();
            _net.LogPath = _logPath.Text.Trim();
            _net.Save(_baseDir);
            _net = NetworkSettings.For(_baseDir);

            if (_log != null)
            {
                _log("[SETTINGS] network saved: " + _net.Summary +
                     " (fail_closed=" + (_net.FailClosed ? "1" : "0") + ", relay " + _net.BasePort + "+slot)");
            }
            // Relays already running still point at the old upstream, so tear them
            // down: the next launch re-wires with the new settings.
            if (_onApplyNetwork != null)
            {
                try { _onApplyNetwork(); } catch { }
            }
            SetState("Saved. Running relays were stopped; the next launch uses the new egress.", Color.FromArgb(74, 222, 128));
            RefreshNetState();
        }

        private void StartTunnel()
        {
            SetState("starting tunnel...", UiKit.Muted);
            ThreadPool.QueueUserWorkItem(delegate
            {
                NetworkSettings.Invalidate();
                NetworkSettings s = NetworkSettings.For(_baseDir);
                if (!s.IsTunnelMode)
                {
                    SetState("Mode is " + NetworkSettings.ModeLabel(s.Mode) + " - there is no tunnel to start.", UiKit.Muted);
                    return;
                }
                bool ok = FarmEngine.EnsureTunnel(msg => { if (_log != null) _log(msg); });
                if (!ok)
                {
                    SetState("Tunnel did not start. See the log for the reason.", Color.FromArgb(248, 113, 113));
                    return;
                }
                // A bound port is not a working tunnel: prove it carries traffic.
                bool egress = FarmEngine.ProbeSocksEgress(s.SocksPort, "1.1.1.1", 443, 8000);
                SetState(egress
                    ? "Tunnel is up on 127.0.0.1:" + s.SocksPort + " and carries traffic."
                    : "Port " + s.SocksPort + " is listening but nothing gets through: the WireGuard handshake\r\n" +
                      "is not completing (some ISPs drop WARP's UDP). Use Custom mode with proxies.txt, or the\r\n" +
                      "system WARP mode, which tunnels over 443 instead.",
                    egress ? Color.FromArgb(74, 222, 128) : Color.FromArgb(251, 191, 36));
            });
        }

        private void StopTunnel()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                NetworkSettings s = NetworkSettings.For(_baseDir);
                bool alsoWarp = NetworkSettings.NormalizeMode(s.Mode) == NetworkSettings.ModeWarp;
                FarmEngine.StopTunnel(alsoWarp);
                SetState(alsoWarp
                    ? "Stopped, and system WARP was disconnected so Windows routing/DNS are yours again."
                    : "WireGuard tunnel stopped. Windows networking was never touched.", UiKit.Muted);
            });
        }

        /// <summary>Switch to the pool and forget any single upstream: one click
        /// from "tunnel is dead" to "traffic leaves through a proxy".</summary>
        private void UseProxyPool()
        {
            int live = FarmEngine.GetProxyPoolCount();
            if (live == 0)
            {
                SetState("proxies.txt has no usable entries - run Scan proxies.txt first.", Color.FromArgb(248, 113, 113));
                return;
            }
            _mode.SelectedIndex = 3;                 // custom
            _upstream.Text = "";
            _failClosed.Checked = true;
            SaveNetwork();
            SetState("Mode is Custom: " + live + " entries from proxies.txt are used round-robin. " +
                     "Run Scan proxies.txt if traffic stalls.", Color.FromArgb(74, 222, 128));
        }

        private void ScanProxies()
        {
            SetState("testing every proxy in proxies.txt (this takes a few seconds)...", UiKit.Muted);
            ThreadPool.QueueUserWorkItem(delegate
            {
                int live, total;
                string report = FarmEngine.ScanProxyPool(_baseDir, 8000, out live, out total,
                    msg => { if (_log != null) _log(msg); });
                SetState(report + "\r\nproxies.txt rewritten; the old file is proxies.txt.bak. " +
                          (live > 0 ? "Press \"Use proxies.txt\" to route through them."
                                    : "None answered - the pool needs new entries."),
                          live > 0 ? Color.FromArgb(74, 222, 128) : Color.FromArgb(248, 113, 113));
            });
        }

        private void TestUpstream()
        {
            SetState("testing...", UiKit.Muted);
            ThreadPool.QueueUserWorkItem(delegate
            {
                NetworkSettings s = NetworkSettings.For(_baseDir);
                var sb = new StringBuilder();

                // Auto: the supervisor is the thing being tested, so ask it to
                // re-probe everything and print the ranking.
                if (NetworkSettings.NormalizeMode(s.Mode) == NetworkSettings.ModeAuto)
                {
                    ProxySupervisor sup = ProxySupervisor.Instance;
                    sup.EnsureActive(true);
                    sb.AppendLine(sup.Describe());
                    foreach (ProxyCandidate c in sup.Candidates)
                    {
                        sb.AppendLine((c.Healthy ? "  OK   " : "  dead ") + c.Url +
                                      (string.IsNullOrEmpty(c.Note) ? "" : "  (" + c.Note + ")"));
                    }
                    SetState(sb.ToString().Trim(), Color.FromArgb(203, 213, 225));
                    return;
                }

                if (!s.IsTunnelMode)
                {
                    sb.AppendLine("Mode: " + NetworkSettings.ModeLabel(s.Mode));
                    sb.AppendLine(s.Mode == NetworkSettings.ModeDirect
                        ? "No tunnel by design: traffic goes out on this machine's real IP."
                        : "Upstream: " + (string.IsNullOrEmpty(s.Upstream) ? "proxies.txt" : s.Upstream));
                }
                else
                {
                    bool up = PortOpen("127.0.0.1", s.SocksPort, 500);
                    sb.AppendLine("SOCKS 127.0.0.1:" + s.SocksPort + " -> " + (up ? "listening" : "CLOSED"));
                    bool egress = up && FarmEngine.ProbeSocksEgress(s.SocksPort, "1.1.1.1", 443, 8000);
                    sb.AppendLine("Egress test (CONNECT 1.1.1.1:443) -> " +
                                  (egress ? "carries traffic" : (up ? "NO TRAFFIC - handshake blocked" : "n/a")));
                    sb.AppendLine("Relay ports: " + s.BasePort + " + slot");
                    sb.AppendLine(!up ? "Instances would fail closed until this port is open."
                                : (egress ? "Instances can start." : "Instances would start and then stall: the tunnel is dead."));
                }
                SetState(sb.ToString().Trim(), Color.FromArgb(203, 213, 225));
            });
        }

        private void RepairNetwork()
        {
            if (MessageBox.Show(
                    "This disconnects the system Cloudflare WARP client, stops its service (it keeps leak-protection " +
                    "filters active even when disconnected) and flushes DNS, so Windows gets its own network back.\r\n\r\n" +
                    "It does not touch the WireGuard userspace tunnel.\r\n\r\nContinue?",
                    "Repair my network", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;

            SetState("repairing...", UiKit.Muted);
            ThreadPool.QueueUserWorkItem(delegate
            {
                string report = FarmEngine.RepairNetwork(msg => { if (_log != null) _log(msg); });
                if (_log != null) _log("[SETTINGS] network repair:/r/n" + report);
                SetState(report.Trim(), Color.FromArgb(203, 213, 225));
            });
        }

        private void OpenProxyLog()
        {
            NetworkSettings s = NetworkSettings.For(_baseDir);
            string path = s.LogPath;
            if (string.IsNullOrEmpty(path)) path = Path.Combine(_baseDir, "asset_cache", "proxy.log");
            OpenPath(path);
        }

        private void RefreshNetState()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                // Never let a status refresh kill the process.
                //
                // This runs on a pool thread, where an unhandled exception is not a log line but
                // a fatal error dialog and a dead app. It happened: Describe() threw
                // NullReferenceException on a null candidate slot and the whole farm went down
                // from a read-only status update. The slot bug is fixed, but a status refresh is
                // not worth that risk in the first place.
                try
                {
                NetworkSettings s = NetworkSettings.For(_baseDir);
                string text = "Mode: " + NetworkSettings.ModeLabel(s.Mode) + "\r\n" + s.Summary;
                if (NetworkSettings.NormalizeMode(s.Mode) == NetworkSettings.ModeAuto)
                {
                    ProxySupervisor sup = ProxySupervisor.Instance;
                    sup.EnsureActive(false);
                    text = "Mode: " + NetworkSettings.ModeLabel(s.Mode) + "\r\n" + sup.Describe();
                }
                if (s.IsTunnelMode)
                {
                    bool up = PortOpen("127.0.0.1", s.SocksPort, 400);
                    text += "\r\nSOCKS " + s.SocksPort + ": " + (up ? "listening" : "CLOSED") +
                            "   |   relay " + s.BasePort + "+slot   |   " +
                            (s.FailClosed ? "fail closed" : "WILL FALL BACK TO DIRECT");
                    if (up)
                    {
                        bool egress = FarmEngine.ProbeSocksEgress(s.SocksPort, "1.1.1.1", 443, 6000);
                        text += egress ? "\r\ntunnel carries traffic" : "\r\ntunnel is listening but NOT passing traffic";
                    }
                }
                SetState(text, s.FailClosed ? UiKit.Muted : Color.FromArgb(251, 191, 36));
                }
                catch (Exception ex)
                {
                    SetState("network state unavailable: " + ex.Message, Color.FromArgb(248, 113, 113));
                }
            });
        }

        private void SetState(string text, Color colour)
        {
            try
            {
                if (IsDisposed || !IsHandleCreated) return;
                BeginInvoke((MethodInvoker)delegate
                {
                    _netState.Text = text;
                    _netState.ForeColor = colour;
                });
            }
            catch { }
        }

        private string FlagPath(string name)
        {
            return Path.Combine(_baseDir, name);
        }

        private void LoadFlags()
        {
            try
            {
                _chkManual.Checked = File.Exists(FlagPath("manual-login.flag"));
                _chkNoUpdate.Checked = File.Exists(FlagPath("no-client-update.flag"));
            }
            catch { }
            UpdateFlagNote();
        }

        private void ApplyFlags()
        {
            try
            {
                SetFlag("manual-login.flag", _chkManual.Checked,
                    "Manual login mode: the host injects no credentials and no auto-login.\r\nDelete this file to restore automatic guest login.\r\n");
                SetFlag("no-client-update.flag", _chkNoUpdate.Checked,
                    "Client auto-update disabled: the host keeps the local bundle.\r\nDelete this file to let the server's build win.\r\n");
                if (_log != null) _log("[SETTINGS] manual-login=" + (_chkManual.Checked ? "on" : "off") +
                                       ", client-update=" + (_chkNoUpdate.Checked ? "off" : "on"));
            }
            catch (Exception ex)
            {
                if (_log != null) _log("[SETTINGS] could not write flag: " + ex.Message);
            }
            UpdateFlagNote();
        }

        private void SetFlag(string name, bool on, string contents)
        {
            // A flag file is the contract: the running instance and the Python
            // side both look for its existence, so there is no second source of
            // truth to keep in sync.
            string path = Path.Combine(_baseDir ?? ".", name);
            if (on)
            {
                if (!File.Exists(path)) File.WriteAllText(path, contents);
            }
            else if (File.Exists(path)) File.Delete(path);
        }

        private void UpdateFlagNote()
        {
            string state = (_chkManual.Checked ? "manual login" : "auto login") + "  |  client updates " +
                           (_chkNoUpdate.Checked ? "off" : "on");
            _flagsState.Text = state + "   (takes effect on the next instance launch)";
        }

        private static bool PortOpen(string host, int port, int timeoutMs)
        {
            try
            {
                using (var c = new TcpClient())
                {
                    IAsyncResult ar = c.BeginConnect(host, port, null, null);
                    WaitHandle wait = ar.AsyncWaitHandle;
                    bool ok;
                    try { ok = wait.WaitOne(timeoutMs); }
                    finally { try { wait.Close(); } catch { } }
                    if (!ok) return false;
                    c.EndConnect(ar);
                    return true;
                }
            }
            catch { return false; }
        }

        private void ResetAiSettings()
        {
            if (MessageBox.Show("Reset the AI panel to its defaults (mode, budgets, goals, shortcuts)?",
                    "Reset AI settings", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
            try
            {
                string path = AiSettings.PathFor(_baseDir);
                if (File.Exists(path)) File.Delete(path);
                if (_log != null) _log("[SETTINGS] AI settings reset; restart the app to apply");
            }
            catch (Exception ex)
            {
                if (_log != null) _log("[SETTINGS] reset failed: " + ex.Message);
            }
        }

        private void OpenPath(string path)
        {
            try
            {
                if (Directory.Exists(path) || File.Exists(path))
                    Process.Start("explorer.exe", "\"" + path + "\"");
                else if (_log != null) _log("[SETTINGS] not found: " + path);
            }
            catch (Exception ex)
            {
                if (_log != null) _log("[SETTINGS] open failed: " + ex.Message);
            }
        }

        public void ReloadState()
        {
            LoadFlags();
            _net = NetworkSettings.For(_baseDir);
            RefreshNetState();
        }
    }

    /// <summary>Rebind the panel actions as a page rather than a modal.</summary>
    public sealed class ShortcutsPage : Panel
    {
        private readonly AiSettings _settings;
        private readonly string _baseDir;
        private readonly Action _onSaved;
        private readonly ListView _list;
        private readonly Label _status;

        public ShortcutsPage(string baseDir, AiSettings settings, Action onSaved)
        {
            _baseDir = baseDir;
            _settings = settings;
            _onSaved = onSaved;
            Dock = DockStyle.Fill;
            BackColor = UiKit.Page;
            Padding = new Padding(24, 20, 24, 20);

            Controls.Add(UiKit.Heading("Shortcuts", 0, 8));
            Controls.Add(UiKit.Note("Select a row, then press the key you want. Keys work anywhere in the window.", 0, 34));

            _list = new ListView();
            _list.View = View.Details;
            _list.FullRowSelect = true;
            _list.HideSelection = false;
            _list.Location = new Point(0, 62);
            _list.Size = new Size(560, 200);
            _list.BackColor = UiKit.Card;
            _list.ForeColor = UiKit.Text;
            _list.BorderStyle = BorderStyle.FixedSingle;
            _list.Columns.Add("Action", 380);
            _list.Columns.Add("Key", 150);
            _list.KeyDown += OnKeyDown;
            _list.SelectedIndexChanged += delegate { UpdateStatus(); };
            Controls.Add(_list);

            Controls.Add(UiKit.Button("Save", 0, 274, 100, Color.FromArgb(37, 99, 235), delegate { Save(); }));
            Controls.Add(UiKit.Button("Restore defaults", 110, 274, 140, Color.FromArgb(51, 65, 85), delegate { Restore(); }));

            _status = UiKit.Note("", 262, 282);
            Controls.Add(_status);

            Fill();
        }

        private void Fill()
        {
            _list.Items.Clear();
            foreach (string a in AiSettings.Actions())
            {
                int code;
                string key = _settings.Shortcuts.TryGetValue(a, out code) && code != 0
                    ? ((Keys)code).ToString() : "(unset)";
                var item = new ListViewItem(AiSettings.ActionLabel(a));
                item.SubItems.Add(key);
                item.Tag = a;
                _list.Items.Add(item);
            }
            UpdateStatus();
        }

        private void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (_list.SelectedItems.Count == 0) return;
            if (e.KeyCode == Keys.Tab || e.KeyCode == Keys.Enter || e.KeyCode == Keys.Escape) return;
            e.SuppressKeyPress = true;
            string action = (string)_list.SelectedItems[0].Tag;

            // A key can only mean one thing. Refuse a duplicate rather than
            // silently making the older binding unreachable.
            foreach (KeyValuePair<string, int> kv in _settings.Shortcuts)
            {
                if (kv.Value == (int)e.KeyData && kv.Key != action)
                {
                    _status.Text = ((Keys)e.KeyData).ToString() + " is already bound to " + kv.Key.Replace("_", " ");
                    return;
                }
            }
            _settings.Shortcuts[action] = (int)e.KeyData;
            Fill();
            _status.Text = "unsaved change - press Save";
        }

        private void Save()
        {
            _settings.Save(_baseDir);
            _status.Text = "saved";
            if (_onSaved != null) _onSaved();
        }

        private void Restore()
        {
            _settings.Shortcuts = AiSettings.DefaultShortcuts();
            Fill();
            _status.Text = "defaults restored - press Save";
        }

        private void UpdateStatus()
        {
            if (_status == null) return;
            _status.Text = _list.SelectedItems.Count == 0
                ? "no row selected"
                : "press a key to rebind";
        }
    }

    /// <summary>Live tails of the two logs that matter when something goes wrong.</summary>
    public sealed class LogsPage : Panel
    {
        private readonly string _baseDir;
        private readonly RichTextBox _app;
        private readonly RichTextBox _ai;
        private readonly System.Windows.Forms.Timer _timer;

        public LogsPage(string baseDir)
        {
            _baseDir = baseDir;
            Dock = DockStyle.Fill;
            BackColor = UiKit.Page;
            Padding = new Padding(24, 20, 24, 20);

            Controls.Add(UiKit.Heading("Logs", 0, 8));

            var lblApp = UiKit.Sub("DofusFarm.log", 0, 38);
            Controls.Add(lblApp);
            _app = UiKit.TextBox("", 0, 58, 640, 300);
            _app.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(_app);

            var lblAi = UiKit.Sub("autopilot.jsonl (one labelled step per line)", 0, 368);
            Controls.Add(lblAi);
            _ai = UiKit.TextBox("", 0, 388, 640, 160);
            _ai.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom;
            Controls.Add(_ai);

            var refresh = UiKit.Button("Refresh", 0, 340, 100, Color.FromArgb(51, 65, 85), delegate { Reload(); });
            refresh.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            refresh.Location = new Point(660, 340);
            Controls.Add(refresh);

            var open = UiKit.Button("Open folder", 110, 340, 120, Color.FromArgb(51, 65, 85), delegate
            {
                try
                {
                    string dir = Path.Combine(_baseDir, "dofus-network", "trajectories");
                    if (Directory.Exists(dir)) Process.Start("explorer.exe", "\"" + dir + "\"");
                }
                catch { }
            });
            open.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            open.Location = new Point(770, 340);
            Controls.Add(open);

            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 4000;
            _timer.Tick += delegate { if (Visible) Reload(); };
            _timer.Start();

            Reload();
        }

        private string Tail(string path, int lines)
        {
            try
            {
                if (!File.Exists(path)) return "(not created yet) " + path;
                var queue = new Queue<string>();
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var reader = new StreamReader(fs, Encoding.UTF8))
                {
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        queue.Enqueue(line);
                        if (queue.Count > lines) queue.Dequeue();
                    }
                }
                return string.Join(Environment.NewLine, queue.ToArray());
            }
            catch (Exception ex) { return "(cannot read " + path + ": " + ex.Message + ")"; }
        }

        public void Reload()
        {
            string appLog = Tail(Path.Combine(_baseDir, "DofusFarm.log"), 260);
            string aiLog = Tail(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm", "autopilot.jsonl"), 80);
            if (_app.Text != appLog)
            {
                _app.Text = appLog;
                _app.SelectionStart = _app.TextLength;
                _app.ScrollToCaret();
            }
            if (_ai.Text != aiLog)
            {
                _ai.Text = aiLog;
                _ai.SelectionStart = _ai.TextLength;
                _ai.ScrollToCaret();
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && _timer != null) _timer.Stop();
            base.Dispose(disposing);
        }
    }
}
