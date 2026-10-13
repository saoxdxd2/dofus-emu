using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;
using DofusFarm.Engine;
using DofusFarm.Hardware;
using DofusFarm.Native;

namespace DofusFarm.UI
{
    public class MainForm : Form
    {
        private FarmEngine _engine;
        private FarmWatchdog _watchdog;
        private PriorityGovernor _governor;
        private HardwareSpecs _specs;

        private Panel _headerPanel;
        private Label _titleLabel;
        private Label _subtitleBadge;
        private Label _specsLabel;
        private Label _lblActiveBadge;
        private Button _btnAutoTune;

        private Panel _toolbarPanel;
        private Button _btnLaunchFarm;
        private Button _btnStopAll;
        private Button _btnRandomizeProfiles;
        private Button _btnRelayout;
        private Button _btnCascade;
        private Button _btnTrimRam;
        private Button _btnAddSlot;
        private Button _btnRefresh;

        private Button _btnFocus1;
        private Button _btnFocus2;
        private Button _btnFocus3;
        private Button _btnFocus4;

        private Label _lblRam;
        private ComboBox _cmbRam;
        private Label _lblCores;
        private ComboBox _cmbCores;
        private CheckBox _chkWatchdog;
        private CheckBox _chkGovernor;

        private DataGridView _grid;
        private Panel _logPanel;
        private Panel _logHeaderPanel;
        private Label _lblLogHeader;
        private Button _btnToggleLogs;
        private Button _btnClearLogs;
        private Button _btnCopyLogs;
        private TextBox _txtLogs;

        private volatile bool _isClosing = false;
        private volatile bool _isFarmLaunching = false;
        private readonly Queue<string> _earlyLogBuffer = new Queue<string>();
        private readonly object _earlyLogLock = new object();
        private readonly HashSet<string> _inFlightOps = new HashSet<string>();
        private readonly object _opsLock = new object();

        private bool TryBeginOp(string key)
        {
            lock (_opsLock)
            {
                if (_inFlightOps.Contains(key)) return false;
                _inFlightOps.Add(key);
                return true;
            }
        }

        private void EndOp(string key)
        {
            lock (_opsLock)
            {
                _inFlightOps.Remove(key);
            }
        }

        private void SafeInvoke(Action action)
        {
            if (this.IsDisposed) return;
            try
            {
                if (this.IsHandleCreated && !this.IsDisposed)
                {
                    this.Invoke(action);
                }
            }
            catch (ObjectDisposedException) { }
            catch (InvalidOperationException) { }
        }

        private void SafeBeginInvoke(Action action)
        {
            if (this.IsDisposed) return;
            try
            {
                if (this.IsHandleCreated && !this.IsDisposed)
                {
                    this.BeginInvoke(action);
                }
            }
            catch (ObjectDisposedException) { }
            catch (InvalidOperationException) { }
        }

        public MainForm()
        {
            InitializeComponent();
            InitializeEngine();
        }

        private void InitializeComponent()
        {
            this.Text = "Dofus Touch Virtualization Farm Manager (Native C#)";
            this.Size = new Size(1140, 720);
            this.MinimumSize = new Size(980, 580);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(18, 19, 24);
            this.ForeColor = Color.FromArgb(235, 235, 240);
            this.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);
            this.DoubleBuffered = true;

            // Load app icon if available
            string iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");
            if (File.Exists(iconPath))
            {
                try { this.Icon = new Icon(iconPath); } catch { }
            }

            // Top Header Panel
            _headerPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 72,
                BackColor = Color.FromArgb(25, 26, 34),
                Padding = new Padding(15, 8, 15, 8)
            };

            _titleLabel = new Label
            {
                Text = "⚡ DOFUS TOUCH FARM MANAGER",
                Font = new Font("Segoe UI", 12f, FontStyle.Bold),
                ForeColor = Color.FromArgb(96, 165, 250),
                AutoSize = true,
                Location = new Point(15, 10)
            };

            _subtitleBadge = new Label
            {
                Text = "● ANKAMA ANTI-CHEAT EVASION ACTIVE • 16-DEVICE POOL • ENFORCING VFS",
                Font = new Font("Segoe UI", 8.2f, FontStyle.Bold),
                ForeColor = Color.FromArgb(52, 211, 153),
                AutoSize = true,
                Location = new Point(325, 13)
            };

            _specsLabel = new Label
            {
                Text = "Detecting Host Hardware Specs & Resource Budget...",
                Font = new Font("Segoe UI", 8.8f),
                ForeColor = Color.FromArgb(156, 163, 175),
                AutoSize = true,
                Location = new Point(15, 38)
            };

            _lblActiveBadge = new Label
            {
                Text = "● 0 / 4 Running",
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(156, 163, 175),
                AutoSize = true,
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                Location = new Point(_headerPanel.Width - 325, 22)
            };

            _btnAutoTune = CreateButton("⚡ Auto-Tune (Optimal)", Color.FromArgb(39, 41, 52), Color.White, 165, 34);
            _btnAutoTune.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnAutoTune.Location = new Point(_headerPanel.Width - 185, 16);
            _btnAutoTune.Click += OnAutoTuneClick;

            _headerPanel.Controls.Add(_titleLabel);
            _headerPanel.Controls.Add(_subtitleBadge);
            _headerPanel.Controls.Add(_specsLabel);
            _headerPanel.Controls.Add(_lblActiveBadge);
            _headerPanel.Controls.Add(_btnAutoTune);

            // Toolbar Panel
            _toolbarPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 82,
                BackColor = Color.FromArgb(22, 23, 29),
                Padding = new Padding(15, 8, 15, 8)
            };

            int btnY = 8;
            int btnH = 32;

            _btnLaunchFarm = CreateButton("🚀 Launch (4x)", Color.FromArgb(37, 99, 235), Color.White, 120, btnH);
            _btnLaunchFarm.Location = new Point(15, btnY);
            _btnLaunchFarm.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _btnLaunchFarm.Click += OnLaunchFarmClick;

            _btnStopAll = CreateButton("🛑 Stop All", Color.FromArgb(220, 38, 38), Color.White, 85, btnH);
            _btnStopAll.Location = new Point(140, btnY);
            _btnStopAll.Click += OnStopAllClick;

            _btnRandomizeProfiles = CreateButton("🎲 Randomize Profiles", Color.FromArgb(124, 58, 237), Color.White, 150, btnH);
            _btnRandomizeProfiles.Location = new Point(230, btnY);
            _btnRandomizeProfiles.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _btnRandomizeProfiles.Click += OnRandomizeProfilesClick;

            _btnRelayout = CreateButton("📐 Tile 2x2", Color.FromArgb(39, 41, 52), Color.White, 80, btnH);
            _btnRelayout.Location = new Point(385, btnY);
            _btnRelayout.Click += OnRelayoutClick;

            _btnCascade = CreateButton("📐 Cascade", Color.FromArgb(39, 41, 52), Color.White, 80, btnH);
            _btnCascade.Location = new Point(470, btnY);
            _btnCascade.Click += OnCascadeClick;

            _btnTrimRam = CreateButton("🧹 Clear RAM", Color.FromArgb(39, 41, 52), Color.White, 95, btnH);
            _btnTrimRam.Location = new Point(555, btnY);
            _btnTrimRam.Click += delegate
            {
                ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        _engine.TrimAllMemory();
                    }
                    catch (Exception ex)
                    {
                        Log("Error trimming RAM: " + ex.Message);
                    }
                });
            };

            _btnAddSlot = CreateButton("➕ Add Slot", Color.FromArgb(39, 41, 52), Color.White, 85, btnH);
            _btnAddSlot.Location = new Point(655, btnY);
            _btnAddSlot.Click += OnAddSlotClick;

            _btnRefresh = CreateButton("🔄 Refresh", Color.FromArgb(39, 41, 52), Color.White, 75, btnH);
            _btnRefresh.Location = new Point(745, btnY);
            _btnRefresh.Click += delegate { RefreshGrid(); };

            // Hardware Sizing dropdowns (Row 1 right)
            _lblRam = new Label { Text = "RAM:", ForeColor = Color.FromArgb(170, 175, 185), AutoSize = true, Location = new Point(830, 14) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 115,
                Location = new Point(870, 11)
            };
            _cmbRam.Items.AddRange(new object[] {
                "512 MB", "768 MB", "1024 MB (Default)", "1536 MB", "2048 MB",
                "3072 MB", "4096 MB", "6144 MB", "8192 MB", "12288 MB", "16384 MB (16GB)"
            });
            _cmbRam.SelectedIndex = 2; // 1024 MB

            _lblCores = new Label { Text = "vCPU:", ForeColor = Color.FromArgb(170, 175, 185), AutoSize = true, Location = new Point(995, 14) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 80,
                Location = new Point(1035, 11)
            };
            _cmbCores.Items.AddRange(new object[] { "1 Core", "2 Cores", "3 Cores", "4 Cores", "6 Cores", "8 Cores" });
            _cmbCores.SelectedIndex = 0; // 1 Core

            // Quick focus buttons and Checkboxes (Row 2)
            Label lblFocus = new Label { Text = "Quick Focus:", ForeColor = Color.FromArgb(130, 135, 145), AutoSize = true, Location = new Point(15, 49) };
            _btnFocus1 = CreateButton("#1", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 36, 24);
            _btnFocus1.Location = new Point(100, 46);
            _btnFocus1.Click += delegate { _engine.FocusInstance(5554); };

            _btnFocus2 = CreateButton("#2", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 36, 24);
            _btnFocus2.Location = new Point(140, 46);
            _btnFocus2.Click += delegate { _engine.FocusInstance(5556); };

            _btnFocus3 = CreateButton("#3", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 36, 24);
            _btnFocus3.Location = new Point(180, 46);
            _btnFocus3.Click += delegate { _engine.FocusInstance(5558); };

            _btnFocus4 = CreateButton("#4", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 36, 24);
            _btnFocus4.Location = new Point(220, 46);
            _btnFocus4.Click += delegate { _engine.FocusInstance(5560); };

            _chkWatchdog = new CheckBox
            {
                Text = "Self-Heal (120s Grace)",
                ForeColor = Color.FromArgb(52, 211, 153),
                Checked = true,
                AutoSize = true,
                Location = new Point(280, 48)
            };
            _chkWatchdog.CheckedChanged += delegate { if (_watchdog != null) _watchdog.Enabled = _chkWatchdog.Checked; };

            _chkGovernor = new CheckBox
            {
                Text = "Dynamic CPU & FPS Governor",
                ForeColor = Color.FromArgb(96, 165, 250),
                Checked = true,
                AutoSize = true,
                Location = new Point(450, 48)
            };
            _chkGovernor.CheckedChanged += delegate { if (_governor != null) _governor.Enabled = _chkGovernor.Checked; };

            _toolbarPanel.Controls.Add(_btnLaunchFarm);
            _toolbarPanel.Controls.Add(_btnStopAll);
            _toolbarPanel.Controls.Add(_btnRandomizeProfiles);
            _toolbarPanel.Controls.Add(_btnRelayout);
            _toolbarPanel.Controls.Add(_btnCascade);
            _toolbarPanel.Controls.Add(_btnTrimRam);
            _toolbarPanel.Controls.Add(_btnAddSlot);
            _toolbarPanel.Controls.Add(_btnRefresh);
            _toolbarPanel.Controls.Add(_lblRam);
            _toolbarPanel.Controls.Add(_cmbRam);
            _toolbarPanel.Controls.Add(_lblCores);
            _toolbarPanel.Controls.Add(_cmbCores);

            _toolbarPanel.Controls.Add(lblFocus);
            _toolbarPanel.Controls.Add(_btnFocus1);
            _toolbarPanel.Controls.Add(_btnFocus2);
            _toolbarPanel.Controls.Add(_btnFocus3);
            _toolbarPanel.Controls.Add(_btnFocus4);
            _toolbarPanel.Controls.Add(_chkWatchdog);
            _toolbarPanel.Controls.Add(_chkGovernor);

            // DataGridView for Instances
            _grid = new DataGridView
            {
                Dock = DockStyle.Fill,
                BackgroundColor = Color.FromArgb(16, 17, 22),
                ForeColor = Color.FromArgb(240, 240, 245),
                GridColor = Color.FromArgb(36, 38, 48),
                BorderStyle = BorderStyle.None,
                CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal,
                ColumnHeadersBorderStyle = DataGridViewHeaderBorderStyle.None,
                EnableHeadersVisualStyles = false,
                RowHeadersVisible = false,
                AllowUserToAddRows = false,
                AllowUserToDeleteRows = false,
                SelectionMode = DataGridViewSelectionMode.FullRowSelect,
                MultiSelect = false,
                RowTemplate = { Height = 36 },
                ShowCellToolTips = true
            };

            _grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(26, 28, 36);
            _grid.ColumnHeadersDefaultCellStyle.ForeColor = Color.FromArgb(156, 163, 175);
            _grid.ColumnHeadersDefaultCellStyle.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            _grid.ColumnHeadersDefaultCellStyle.Padding = new Padding(6);
            _grid.DefaultCellStyle.BackColor = Color.FromArgb(18, 19, 25);
            _grid.DefaultCellStyle.ForeColor = Color.FromArgb(240, 240, 245);
            _grid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(37, 44, 68);
            _grid.DefaultCellStyle.SelectionForeColor = Color.White;
            _grid.DefaultCellStyle.Padding = new Padding(6);
            _grid.AlternatingRowsDefaultCellStyle.BackColor = Color.FromArgb(23, 24, 32);

            _grid.Columns.Add("Status", "Status");
            _grid.Columns.Add("Name", "Instance");
            _grid.Columns.Add("Port", "Console Port");
            _grid.Columns.Add("Ram", "RAM");
            _grid.Columns.Add("Cores", "vCPU");
            _grid.Columns.Add("Device", "Phone Model Identity");
            _grid.Columns.Add("Proxy", "Network Engine");

            DataGridViewButtonColumn btnAction = new DataGridViewButtonColumn
            {
                Name = "Action",
                HeaderText = "State",
                Text = "Launch",
                UseColumnTextForButtonValue = false,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnAction);

            DataGridViewButtonColumn btnClone = new DataGridViewButtonColumn
            {
                Name = "Clone",
                HeaderText = "Clone",
                Text = "📋 Clone",
                UseColumnTextForButtonValue = true,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnClone);

            DataGridViewButtonColumn btnDelete = new DataGridViewButtonColumn
            {
                Name = "Delete",
                HeaderText = "Delete",
                Text = "🗑️ Delete",
                UseColumnTextForButtonValue = true,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnDelete);

            _grid.Columns["Status"].Width = 105;
            _grid.Columns["Name"].Width = 110;
            _grid.Columns["Port"].Width = 95;
            _grid.Columns["Ram"].Width = 85;
            _grid.Columns["Cores"].Width = 70;
            _grid.Columns["Device"].Width = 210;
            _grid.Columns["Proxy"].Width = 180;
            _grid.Columns["Action"].Width = 85;
            _grid.Columns["Clone"].Width = 80;
            _grid.Columns["Delete"].Width = 80;

            _grid.CellContentClick += OnGridCellContentClick;
            _grid.CellDoubleClick += OnGridCellDoubleClick;

            // Collapsible Activity Log Panel at bottom
            _logPanel = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 145,
                BackColor = Color.FromArgb(20, 21, 28)
            };

            _logHeaderPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 26,
                BackColor = Color.FromArgb(28, 30, 40),
                Padding = new Padding(8, 2, 8, 2)
            };

            _lblLogHeader = new Label
            {
                Text = "📋 Real-Time Activity & Anti-Cheat Telemetry",
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(156, 163, 175),
                AutoSize = true,
                Location = new Point(10, 5)
            };

            _btnClearLogs = CreateButton("🧹 Clear", Color.FromArgb(42, 44, 56), Color.FromArgb(200, 205, 215), 65, 20);
            _btnClearLogs.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnClearLogs.Location = new Point(_logHeaderPanel.Width - 235, 3);
            _btnClearLogs.Font = new Font("Segoe UI", 7.8f);
            _btnClearLogs.Click += delegate { _txtLogs.Clear(); };

            _btnCopyLogs = CreateButton("📋 Copy", Color.FromArgb(42, 44, 56), Color.FromArgb(200, 205, 215), 65, 20);
            _btnCopyLogs.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnCopyLogs.Location = new Point(_logHeaderPanel.Width - 165, 3);
            _btnCopyLogs.Font = new Font("Segoe UI", 7.8f);
            _btnCopyLogs.Click += delegate
            {
                if (!string.IsNullOrEmpty(_txtLogs.Text))
                {
                    try { Clipboard.SetText(_txtLogs.Text); } catch { }
                }
            };

            _btnToggleLogs = CreateButton("▼ Hide Logs", Color.FromArgb(42, 44, 56), Color.FromArgb(200, 205, 215), 85, 20);
            _btnToggleLogs.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnToggleLogs.Location = new Point(_logHeaderPanel.Width - 95, 3);
            _btnToggleLogs.Font = new Font("Segoe UI", 7.8f);
            _btnToggleLogs.Click += OnLogToggleClick;

            _logHeaderPanel.Controls.Add(_lblLogHeader);
            _logHeaderPanel.Controls.Add(_btnClearLogs);
            _logHeaderPanel.Controls.Add(_btnCopyLogs);
            _logHeaderPanel.Controls.Add(_btnToggleLogs);

            _txtLogs = new TextBox
            {
                Dock = DockStyle.Fill,
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                BackColor = Color.FromArgb(14, 15, 20),
                ForeColor = Color.FromArgb(175, 180, 195),
                Font = new Font("Consolas", 9f),
                BorderStyle = BorderStyle.None,
                MaxLength = 0
            };

            _logPanel.Controls.Add(_txtLogs);
            _logPanel.Controls.Add(_logHeaderPanel);

            this.Controls.Add(_grid);
            this.Controls.Add(_logPanel);
            this.Controls.Add(_toolbarPanel);
            this.Controls.Add(_headerPanel);
        }

        private Button CreateButton(string text, Color backColor, Color foreColor, int width, int height)
        {
            Button btn = new Button
            {
                Text = text,
                BackColor = backColor,
                ForeColor = foreColor,
                FlatStyle = FlatStyle.Flat,
                Width = width,
                Height = height,
                Cursor = Cursors.Hand
            };
            btn.FlatAppearance.BorderSize = 0;
            return btn;
        }

        private void InitializeEngine()
        {
            _engine = new FarmEngine();
            _engine.OnLog += Log;

            _watchdog = new FarmWatchdog(_engine);
            _watchdog.OnLog += Log;
            _watchdog.Start();

            _governor = new PriorityGovernor();
            _governor.OnLog += Log;
            _governor.Start();

            // Run hardware diagnostics in background
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    _specs = HardwareSpecs.Detect();
                    SafeInvoke(delegate
                    {
                        if (_specs != null)
                        {
                            _specsLabel.Text = string.Format("Host: {0} | {1} | {2} GB RAM ({3} GB Free) | {4} Threads",
                                _specs.CpuName, _specs.GpuName, _specs.HostRamGB, _specs.FreeRamGB, _specs.LogicalThreads);
                            Log("Hardware profile loaded: " + _specs.Recommendation);
                        }
                        RefreshGrid();
                    });
                }
                catch (Exception ex)
                {
                    Log("Diagnostics error: " + ex.Message);
                }
            });
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            lock (_earlyLogLock)
            {
                while (_earlyLogBuffer.Count > 0)
                {
                    AppendLogLine(_earlyLogBuffer.Dequeue());
                }
            }
            RefreshGrid();
        }

        private void AppendLogLine(string line)
        {
            if (_txtLogs.TextLength > 300000)
            {
                try
                {
                    _txtLogs.Select(0, 100000);
                    _txtLogs.SelectedText = "";
                }
                catch { }
            }
            try
            {
                _txtLogs.AppendText(line + "\r\n");
            }
            catch { }
        }

        private void Log(string msg)
        {
            if (this.IsDisposed || _isClosing) return;
            if (!this.IsHandleCreated)
            {
                lock (_earlyLogLock)
                {
                    _earlyLogBuffer.Enqueue(msg);
                }
                return;
            }
            if (this.InvokeRequired)
            {
                SafeBeginInvoke(delegate { Log(msg); });
                return;
            }
            lock (_earlyLogLock)
            {
                while (_earlyLogBuffer.Count > 0)
                {
                    AppendLogLine(_earlyLogBuffer.Dequeue());
                }
            }
            AppendLogLine(msg);
        }

        private int GetSelectedRam()
        {
            string s = _cmbRam.SelectedItem != null ? _cmbRam.SelectedItem.ToString() : "1024 MB";
            System.Text.RegularExpressions.Match m = System.Text.RegularExpressions.Regex.Match(s, @"(\d+)");
            return m.Success ? int.Parse(m.Groups[1].Value) : 1024;
        }

        private int GetSelectedCores()
        {
            string s = _cmbCores.SelectedItem != null ? _cmbCores.SelectedItem.ToString() : "1 Core";
            System.Text.RegularExpressions.Match m = System.Text.RegularExpressions.Regex.Match(s, @"(\d+)");
            return m.Success ? int.Parse(m.Groups[1].Value) : 1;
        }

        private void RefreshGrid()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    if (_isClosing) return;
                    List<InstanceModel> list = _engine.GetInstances();
                    SafeInvoke(delegate
                    {
                        _grid.Rows.Clear();
                        int runningCount = 0;
                        foreach (InstanceModel inst in list)
                        {
                            if (inst.IsRunning) runningCount++;
                            string proxyDisplay = inst.Proxy ?? "127.0.0.1:8880";
                            if (proxyDisplay.Contains("127.0.0.1"))
                                proxyDisplay = "⚡ Accelerator (" + proxyDisplay + ")";
                            else
                                proxyDisplay = "🌐 " + proxyDisplay;

                            int rowIdx = _grid.Rows.Add(
                                inst.IsRunning ? "● RUNNING" : "○ STOPPED",
                                inst.Name,
                                inst.Port,
                                inst.RamMb + " MB",
                                inst.Cores + " Core",
                                "📱 " + (inst.PhoneModelName ?? "Galaxy A51"),
                                proxyDisplay,
                                inst.IsRunning ? "Stop" : "Launch",
                                "📋 Clone",
                                "🗑️ Delete"
                            );

                            DataGridViewRow row = _grid.Rows[rowIdx];
                            row.Tag = inst;
                            row.Cells["Device"].Style.ForeColor = Color.FromArgb(235, 235, 240);
                            row.Cells["Proxy"].Style.ForeColor = Color.FromArgb(180, 185, 200);
                            if (inst.IsRunning)
                            {
                                row.Cells["Status"].Style.ForeColor = Color.FromArgb(52, 211, 153);
                                row.Cells["Action"].Style.ForeColor = Color.FromArgb(239, 68, 68);
                            }
                            else
                            {
                                row.Cells["Status"].Style.ForeColor = Color.FromArgb(140, 145, 155);
                                row.Cells["Action"].Style.ForeColor = Color.FromArgb(59, 130, 246);
                            }
                            row.Cells["Clone"].Style.ForeColor = Color.FromArgb(245, 158, 11);
                            row.Cells["Delete"].Style.ForeColor = Color.FromArgb(244, 63, 94);
                        }

                        // Update Active Badge
                        if (runningCount > 0)
                        {
                            _lblActiveBadge.Text = string.Format("● {0} / {1} Running", runningCount, list.Count);
                            _lblActiveBadge.ForeColor = Color.FromArgb(52, 211, 153);
                        }
                        else
                        {
                            _lblActiveBadge.Text = string.Format("○ 0 / {0} Running", list.Count);
                            _lblActiveBadge.ForeColor = Color.FromArgb(156, 163, 175);
                        }
                    });
                }
                catch (Exception ex)
                {
                    Log("Error refreshing grid: " + ex.Message);
                }
            });
        }

        private void OnRandomizeProfilesClick(object sender, EventArgs e)
        {
            _btnRandomizeProfiles.Enabled = false;
            Log("Reshuffling and applying distinct hardware identities across all farm slots...");
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    if (_isClosing) return;
                    _engine.RandomizeAllInstanceProfiles();
                    Thread.Sleep(500);
                    SafeInvoke(delegate
                    {
                        RefreshGrid();
                    });
                }
                catch (Exception ex)
                {
                    Log("Error randomizing profiles: " + ex.Message);
                }
                finally
                {
                    SafeBeginInvoke(delegate
                    {
                        _btnRandomizeProfiles.Enabled = true;
                    });
                }
            });
        }

        private void OnCascadeClick(object sender, EventArgs e)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    List<int> pids = new List<int>();
                    foreach (InstanceModel inst in _engine.GetInstances())
                    {
                        if (inst.IsRunning && inst.Pid > 0)
                        {
                            pids.Add(inst.Pid);
                        }
                    }

                    if (pids.Count == 0)
                    {
                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                        try
                        {
                            foreach (Process p in procs)
                            {
                                pids.Add(p.Id);
                            }
                        }
                        finally
                        {
                            foreach (Process p in procs)
                            {
                                p.Dispose();
                            }
                        }
                    }

                    if (pids.Count > 0)
                    {
                        Win32.ApplyCascadeLayout(pids);
                        Log(string.Format("Arranged {0} windows in cascade view.", pids.Count));
                    }
                    else
                    {
                        Log("No active instances found to cascade.");
                    }
                }
                catch (Exception ex)
                {
                    Log("Error cascading windows: " + ex.Message);
                }
            });
        }

        private void OnLogToggleClick(object sender, EventArgs e)
        {
            if (_logPanel.Height > 30)
            {
                _logPanel.Height = 26;
                _btnToggleLogs.Text = "▲ Show Logs";
            }
            else
            {
                _logPanel.Height = 145;
                _btnToggleLogs.Text = "▼ Hide Logs";
            }
        }

        private void OnLaunchFarmClick(object sender, EventArgs e)
        {
            if (_isFarmLaunching) return;
            _isFarmLaunching = true;
            _btnLaunchFarm.Enabled = false;

            int ram = GetSelectedRam();
            int cores = GetSelectedCores();

            Log(string.Format("Launching 4x Farm with {0} MB RAM & {1} Core(s) per instance...", ram, cores));

            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    // Ensure 4 instances are provisioned
                    for (int i = 1; i <= 4; i++)
                    {
                        if (_isClosing || !_isFarmLaunching) return;
                        string name = "dofus-0" + i;
                        _engine.ProvisionInstance(name, ram, cores);
                    }

                    // Query current instances to retrieve configured proxies per slot (NET-HIGH-01)
                    List<InstanceModel> existingInsts = _engine.GetInstances();
                    Dictionary<string, string> proxyMap = new Dictionary<string, string>();
                    foreach (InstanceModel im in existingInsts)
                    {
                        proxyMap[im.Name] = im.Proxy;
                    }

                    // Launch them staggered by 500ms
                    for (int i = 1; i <= 4; i++)
                    {
                        if (_isClosing || !_isFarmLaunching) return;
                        string name = "dofus-0" + i;
                        int port = 5554 + 2 * (i - 1);
                        string proxy = proxyMap.ContainsKey(name) ? proxyMap[name] : "127.0.0.1:8880";
                        _engine.LaunchInstance(name, port, ram, cores, proxy);
                        Thread.Sleep(500);
                    }

                    Thread.Sleep(3000);
                    if (_isClosing || !_isFarmLaunching) return;
                    SafeInvoke(delegate
                    {
                        RefreshGrid();
                        OnRelayoutClick(null, null);
                    });

                    // Follow-up layout snap once Qt windows finish initializing
                    Thread.Sleep(5000);
                    if (_isClosing || !_isFarmLaunching) return;
                    SafeInvoke(delegate
                    {
                        OnRelayoutClick(null, null);
                    });
                }
                catch (Exception ex)
                {
                    Log("Farm launch error: " + ex.Message);
                }
                finally
                {
                    _isFarmLaunching = false;
                    SafeBeginInvoke(delegate
                    {
                        _btnLaunchFarm.Enabled = true;
                    });
                }
            });
        }

        private void OnStopAllClick(object sender, EventArgs e)
        {
            _isFarmLaunching = false; // Aborts any launch in-flight
            _btnStopAll.Enabled = false;
            Log("Stopping all running instances in background...");
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    _engine.StopAll();
                    Thread.Sleep(500);
                    SafeInvoke(delegate
                    {
                        RefreshGrid();
                    });
                }
                catch (Exception ex)
                {
                    Log("Error stopping all: " + ex.Message);
                }
                finally
                {
                    SafeBeginInvoke(delegate
                    {
                        _btnStopAll.Enabled = true;
                    });
                }
            });
        }

        private void OnRelayoutClick(object sender, EventArgs e)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    List<int> pids = new List<int>();
                    foreach (InstanceModel inst in _engine.GetInstances())
                    {
                        if (inst.IsRunning && inst.Pid > 0)
                        {
                            pids.Add(inst.Pid);
                        }
                    }

                    if (pids.Count == 0)
                    {
                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                        try
                        {
                            foreach (Process p in procs)
                            {
                                pids.Add(p.Id);
                            }
                        }
                        finally
                        {
                            foreach (Process p in procs)
                            {
                                p.Dispose();
                            }
                        }
                    }

                    if (pids.Count > 0)
                    {
                        Win32.ApplyFarmLayout(pids, true);
                        Log(string.Format("Arranged {0} windows into clean edge-to-edge layout.", pids.Count));
                    }
                    else
                    {
                        Log("No active instances found to tile.");
                    }
                }
                catch (Exception ex)
                {
                    Log("Error tiling windows: " + ex.Message);
                }
            });
        }

        private void OnAddSlotClick(object sender, EventArgs e)
        {
            string newSlot = _engine.GetNextAvailableSlot();
            int ram = GetSelectedRam();
            int cores = GetSelectedCores();

            DialogResult res = MessageBox.Show(
                string.Format("Provision new instance slot '{0}' with {1} MB RAM and {2} vCPU?", newSlot, ram, cores),
                "Add Instance Slot",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question);

            if (res == DialogResult.Yes)
            {
                ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        _engine.ProvisionInstance(newSlot, ram, cores);
                        RefreshGrid();
                    }
                    catch (Exception ex)
                    {
                        Log("Error provisioning " + newSlot + ": " + ex.Message);
                    }
                });
            }
        }

        private void OnAutoTuneClick(object sender, EventArgs e)
        {
            if (_specs == null) return;
            DialogResult res = MessageBox.Show(
                "Apply Auto-Tune profile (1024 MB RAM, 1 Core, 30 FPS vsync, Host GLES passthrough) to all non-running instances?\n\nReason: " + _specs.Recommendation,
                "Auto-Tune Confirmation",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Information);

            if (res == DialogResult.Yes)
            {
                ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        List<InstanceModel> instances = _engine.GetInstances();
                        int tunedCount = 0;
                        foreach (InstanceModel inst in instances)
                        {
                            if (inst.IsRunning)
                            {
                                Log("Auto-tune: Skipping active instance " + inst.Name + " (must be stopped to reconfigure)");
                                continue;
                            }
                            _engine.ProvisionInstance(inst.Name, 1024, 1);
                            tunedCount++;
                        }
                        Log(string.Format("Auto-tune profile applied across {0} instance(s).", tunedCount));
                        RefreshGrid();
                    }
                    catch (Exception ex)
                    {
                        Log("Auto-tune error: " + ex.Message);
                    }
                });
            }
        }

        private void OnGridCellContentClick(object sender, DataGridViewCellEventArgs e)
        {
            if (e.RowIndex < 0 || e.RowIndex >= _grid.Rows.Count) return;
            DataGridViewRow row = _grid.Rows[e.RowIndex];
            if (row == null) return;
            InstanceModel inst = row.Tag as InstanceModel;
            if (inst == null) return;

            // 1. Action: Launch / Stop
            if (e.ColumnIndex == _grid.Columns["Action"].Index)
            {
                if (!TryBeginOp(inst.Name)) return;
                ThreadPool.QueueUserWorkItem(delegate
                {
                    try
                    {
                        if (_isClosing) return;
                        if (inst.IsRunning)
                        {
                            _engine.StopInstance(inst.Serial, inst.Name);
                        }
                        else
                        {
                            _engine.LaunchInstance(inst.Name, inst.Port, inst.RamMb, inst.Cores, inst.Proxy);
                        }
                        Thread.Sleep(800);
                        RefreshGrid();
                    }
                    catch (Exception ex)
                    {
                        Log(string.Format("Error toggling {0}: {1}", inst.Name, ex.Message));
                    }
                    finally
                    {
                        EndOp(inst.Name);
                    }
                });
            }
            // 2. Clone Instance
            else if (e.ColumnIndex == _grid.Columns["Clone"].Index)
            {
                string nextSlot = _engine.GetNextAvailableSlot();
                using (CloneDialog dlg = new CloneDialog(inst.Name, nextSlot))
                {
                    if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.TargetName))
                    {
                        string srcName = inst.Name;
                        string targetName = dlg.TargetName;
                        bool cloneState = dlg.CloneState;

                        if (!TryBeginOp(targetName)) return;
                        ThreadPool.QueueUserWorkItem(delegate
                        {
                            try
                            {
                                if (_isClosing) return;
                                _engine.CloneInstance(srcName, targetName, cloneState);
                                Thread.Sleep(800);
                                RefreshGrid();
                            }
                            catch (Exception ex)
                            {
                                Log(string.Format("Error cloning {0} to {1}: {2}", srcName, targetName, ex.Message));
                            }
                            finally
                            {
                                EndOp(targetName);
                            }
                        });
                    }
                }
            }
            // 3. Delete Instance
            else if (e.ColumnIndex == _grid.Columns["Delete"].Index)
            {
                DialogResult confirm = MessageBox.Show(
                    string.Format("Are you sure you want to completely DELETE instance '{0}'?\n\nThis will terminate the instance and permanently delete its virtual disk files.", inst.Name),
                    "Confirm Instance Deletion",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);

                if (confirm == DialogResult.Yes)
                {
                    if (!TryBeginOp(inst.Name)) return;
                    string nameToDelete = inst.Name;
                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        try
                        {
                            if (_isClosing) return;
                            _engine.DeleteInstance(nameToDelete);
                            Thread.Sleep(600);
                            RefreshGrid();
                        }
                        catch (Exception ex)
                        {
                            Log(string.Format("Error deleting {0}: {1}", nameToDelete, ex.Message));
                        }
                        finally
                        {
                            EndOp(nameToDelete);
                        }
                    });
                }
            }
        }

        private void OnGridCellDoubleClick(object sender, DataGridViewCellEventArgs e)
        {
            if (e.RowIndex < 0 || e.RowIndex >= _grid.Rows.Count) return;
            DataGridViewRow row = _grid.Rows[e.RowIndex];
            if (row == null) return;
            InstanceModel inst = row.Tag as InstanceModel;
            if (inst == null) return;

            if (e.ColumnIndex == _grid.Columns["Device"].Index)
            {
                using (DeviceProfileDialog dlg = new DeviceProfileDialog(inst.Name, inst.PhoneModelId))
                {
                    if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.SelectedProfileId))
                    {
                        string instName = inst.Name;
                        string selectedProfileId = dlg.SelectedProfileId;
                        ThreadPool.QueueUserWorkItem(delegate
                        {
                            try
                            {
                                _engine.SetInstancePhoneModel(instName, selectedProfileId);
                                RefreshGrid();
                            }
                            catch (Exception ex)
                            {
                                Log(string.Format("Error setting profile for {0}: {1}", instName, ex.Message));
                            }
                        });
                    }
                }
            }
            else if (e.ColumnIndex == _grid.Columns["Proxy"].Index)
            {
                using (ProxyDialog dlg = new ProxyDialog(inst.Name, inst.Proxy))
                {
                    if (dlg.ShowDialog(this) == DialogResult.OK)
                    {
                        string instName = inst.Name;
                        string proxyVal = dlg.ProxyValue;
                        ThreadPool.QueueUserWorkItem(delegate
                        {
                            try
                            {
                                _engine.SetInstanceProxy(instName, proxyVal);
                                RefreshGrid();
                            }
                            catch (Exception ex)
                            {
                                Log(string.Format("Error setting proxy for {0}: {1}", instName, ex.Message));
                            }
                        });
                    }
                }
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            _isClosing = true;
            _isFarmLaunching = false;

            if (_engine != null) _engine.OnLog -= Log;
            if (_watchdog != null)
            {
                _watchdog.OnLog -= Log;
                _watchdog.Stop();
            }
            if (_governor != null)
            {
                _governor.OnLog -= Log;
                _governor.Stop();
            }
            if (_engine != null)
            {
                _engine.StopAll();
            }
            base.OnFormClosing(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _isClosing = true;
                if (this.Icon != null)
                {
                    try { this.Icon.Dispose(); } catch { }
                }
            }
            base.Dispose(disposing);
        }
    }

    public class CloneDialog : Form
    {
        public string TargetName { get; private set; }
        public bool CloneState { get; private set; }

        private TextBox _txtName;
        private CheckBox _chkState;
        private Button _btnOk;
        private Button _btnCancel;

        public CloneDialog(string sourceName, string defaultTarget)
        {
            this.Text = "Clone Instance: " + sourceName;
            this.Size = new Size(440, 240);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(30, 30, 30);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            Label lbl = new Label
            {
                Text = string.Format("Clone '{0}' into a new isolated instance:\n(Assigns unique Samsung hardware identity, serial & MAC address)", sourceName),
                Location = new Point(20, 15),
                Size = new Size(390, 45)
            };

            Label lblName = new Label { Text = "Target Slot Name:", Location = new Point(20, 70), AutoSize = true };
            _txtName = new TextBox
            {
                Text = defaultTarget,
                Location = new Point(150, 68),
                Width = 240,
                BackColor = Color.FromArgb(24, 24, 24),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };

            _chkState = new CheckBox
            {
                Text = "Preserve 3GB map files, game state & cache (Fast delta copy)",
                Checked = true,
                Location = new Point(20, 105),
                Size = new Size(390, 35),
                ForeColor = Color.FromArgb(78, 201, 176)
            };

            _btnOk = new Button
            {
                Text = "Clone Now",
                DialogResult = DialogResult.OK,
                Location = new Point(200, 150),
                Size = new Size(110, 34),
                BackColor = Color.FromArgb(14, 99, 156),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnOk.FlatAppearance.BorderSize = 0;
            _btnOk.Click += delegate
            {
                TargetName = _txtName.Text.Trim();
                CloneState = _chkState.Checked;
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(320, 150),
                Size = new Size(80, 34),
                BackColor = Color.FromArgb(45, 45, 48),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;

            this.Controls.Add(lbl);
            this.Controls.Add(lblName);
            this.Controls.Add(_txtName);
            this.Controls.Add(_chkState);
            this.Controls.Add(_btnOk);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnOk;
            this.CancelButton = _btnCancel;
        }
    }

    public class ProxyDialog : Form
    {
        public string ProxyValue { get; private set; }

        private TextBox _txtProxy;
        private Button _btnOk;
        private Button _btnCancel;
        private Button _btnResetDefault;

        public ProxyDialog(string instanceName, string currentProxy)
        {
            this.Text = "Network & Proxy Configuration - " + instanceName;
            this.Size = new Size(480, 260);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(30, 30, 30);
            this.ForeColor = Color.White;

            Label lblInfo = new Label
            {
                Text = string.Format("Configure Upstream Network / Proxy for '{0}':\n\n" +
                                     "Format: IP:Port (e.g. 192.168.1.50:8080)\n" +
                                     "Leave blank or reset to use the built-in High-Speed Accelerator (127.0.0.1:8880).", instanceName),
                Location = new Point(20, 15),
                Size = new Size(430, 65),
                ForeColor = Color.FromArgb(200, 200, 200),
                Font = new Font("Segoe UI", 9f)
            };

            string initial = (currentProxy ?? "").Trim();
            if (initial.StartsWith("[🌐] ")) initial = initial.Substring(5).Trim();
            if (initial.StartsWith("[⚡] ")) initial = "";

            _txtProxy = new TextBox
            {
                Text = initial,
                Location = new Point(20, 90),
                Size = new Size(425, 26),
                BackColor = Color.FromArgb(45, 45, 48),
                ForeColor = Color.White,
                Font = new Font("Consolas", 10.5f),
                BorderStyle = BorderStyle.FixedSingle
            };

            _btnResetDefault = new Button
            {
                Text = "⚡ Reset to Default Accelerator (127.0.0.1:8880)",
                Location = new Point(20, 125),
                Size = new Size(425, 30),
                BackColor = Color.FromArgb(45, 45, 55),
                ForeColor = Color.FromArgb(78, 201, 176),
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnResetDefault.FlatAppearance.BorderSize = 0;
            _btnResetDefault.Click += delegate
            {
                _txtProxy.Text = "";
            };

            _btnOk = new Button
            {
                Text = "Save & Apply",
                DialogResult = DialogResult.OK,
                Location = new Point(225, 170),
                Size = new Size(110, 32),
                BackColor = Color.FromArgb(0, 122, 204),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnOk.FlatAppearance.BorderSize = 0;
            _btnOk.Click += delegate
            {
                ProxyValue = _txtProxy.Text.Trim();
                this.Close();
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(345, 170),
                Size = new Size(100, 32),
                BackColor = Color.FromArgb(60, 60, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9f),
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;
            _btnCancel.Click += delegate { this.Close(); };

            this.Controls.Add(lblInfo);
            this.Controls.Add(_txtProxy);
            this.Controls.Add(_btnResetDefault);
            this.Controls.Add(_btnOk);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnOk;
            this.CancelButton = _btnCancel;
        }
    }

    public class DeviceProfileDialog : Form
    {
        public string SelectedProfileId { get; private set; }

        private ComboBox _cmbBrandFilter;
        private TextBox _txtSearch;
        private Button _btnPickRandom;
        private ComboBox _cmbProfiles;

        private Label _lblManufacturerVal;
        private Label _lblChipsetVal;
        private Label _lblGpuVal;
        private Label _lblDisplayVal;
        private Label _lblFingerprintVal;
        private Label _lblSerialVal;

        private Button _btnSave;
        private Button _btnCancel;

        private readonly List<DeviceProfile> _allProfiles;
        private readonly List<DeviceProfile> _filteredProfiles;

        public DeviceProfileDialog(string instanceName, string currentProfileId)
        {
            this.Text = "📱 Device Spoofing & Hardware Identity - " + instanceName;
            this.Size = new Size(600, 480);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(20, 21, 28);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            _allProfiles = DeviceCatalog.GetAllProfiles();
            _filteredProfiles = new List<DeviceProfile>();

            Label lblInfo = new Label
            {
                Text = string.Format("Select the physical Android device profile to spoof for '{0}':\n" +
                                     "All system properties, build fingerprints, WebGL shaders, and screen metrics\n" +
                                     "will dynamically adapt to mirror the selected real hardware model.", instanceName),
                Location = new Point(20, 12),
                Size = new Size(545, 55),
                ForeColor = Color.FromArgb(180, 185, 195),
                Font = new Font("Segoe UI", 9f)
            };

            // Filter row
            Label lblBrand = new Label
            {
                Text = "OEM Brand:",
                Location = new Point(20, 72),
                AutoSize = true,
                ForeColor = Color.FromArgb(156, 163, 175),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold)
            };

            _cmbBrandFilter = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(20, 92),
                Size = new Size(160, 26),
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            _cmbBrandFilter.Items.AddRange(new object[] {
                "All Brands (16)",
                "Samsung (4)",
                "Google (3)",
                "Xiaomi / POCO (3)",
                "OnePlus (2)",
                "Sony (1)",
                "Motorola (1)",
                "Realme (1)",
                "ASUS ROG (1)"
            });
            _cmbBrandFilter.SelectedIndex = 0;

            Label lblSearch = new Label
            {
                Text = "Search Model / SoC:",
                Location = new Point(190, 72),
                AutoSize = true,
                ForeColor = Color.FromArgb(156, 163, 175),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold)
            };

            _txtSearch = new TextBox
            {
                Location = new Point(190, 92),
                Size = new Size(265, 26),
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 9.5f)
            };

            _btnPickRandom = new Button
            {
                Text = "🎲 Random",
                Location = new Point(465, 91),
                Size = new Size(95, 28),
                BackColor = Color.FromArgb(124, 58, 237),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnPickRandom.FlatAppearance.BorderSize = 0;
            _btnPickRandom.Click += delegate
            {
                if (_filteredProfiles.Count > 0)
                {
                    Random rng = new Random();
                    int randIdx = rng.Next(_filteredProfiles.Count);
                    _cmbProfiles.SelectedIndex = randIdx;
                }
            };

            // Profile Selection
            Label lblSelect = new Label
            {
                Text = "Hardware Profile:",
                Location = new Point(20, 126),
                AutoSize = true,
                Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                ForeColor = Color.FromArgb(96, 165, 250)
            };

            _cmbProfiles = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(20, 146),
                Size = new Size(540, 28),
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };

            // Specs Preview Group Box
            GroupBox grpSpecs = new GroupBox
            {
                Text = " Hardware Identity Profile Preview ",
                Location = new Point(20, 185),
                Size = new Size(540, 195),
                ForeColor = Color.FromArgb(156, 163, 175),
                Font = new Font("Segoe UI", 9f)
            };

            int y = 22;
            int step = 24;

            Label lblMfr = new Label { Text = "Manufacturer / Model:", Location = new Point(15, y), Size = new Size(150, 20), ForeColor = Color.FromArgb(140, 145, 155) };
            _lblManufacturerVal = new Label { Location = new Point(165, y), Size = new Size(365, 20), ForeColor = Color.White, Font = new Font("Segoe UI", 9f, FontStyle.Bold) };
            grpSpecs.Controls.Add(lblMfr);
            grpSpecs.Controls.Add(_lblManufacturerVal);

            y += step;
            Label lblChip = new Label { Text = "SoC / Chipset:", Location = new Point(15, y), Size = new Size(150, 20), ForeColor = Color.FromArgb(140, 145, 155) };
            _lblChipsetVal = new Label { Location = new Point(165, y), Size = new Size(365, 20), ForeColor = Color.White };
            grpSpecs.Controls.Add(lblChip);
            grpSpecs.Controls.Add(_lblChipsetVal);

            y += step;
            Label lblGpu = new Label { Text = "GPU / WebGL Renderer:", Location = new Point(15, y), Size = new Size(150, 20), ForeColor = Color.FromArgb(140, 145, 155) };
            _lblGpuVal = new Label { Location = new Point(165, y), Size = new Size(365, 20), ForeColor = Color.FromArgb(52, 211, 153) };
            grpSpecs.Controls.Add(lblGpu);
            grpSpecs.Controls.Add(_lblGpuVal);

            y += step;
            Label lblDisp = new Label { Text = "Resolution / Density:", Location = new Point(15, y), Size = new Size(150, 20), ForeColor = Color.FromArgb(140, 145, 155) };
            _lblDisplayVal = new Label { Location = new Point(165, y), Size = new Size(365, 20), ForeColor = Color.White };
            grpSpecs.Controls.Add(lblDisp);
            grpSpecs.Controls.Add(_lblDisplayVal);

            y += step;
            Label lblSer = new Label { Text = "Serial Prefix / Format:", Location = new Point(15, y), Size = new Size(150, 20), ForeColor = Color.FromArgb(140, 145, 155) };
            _lblSerialVal = new Label { Location = new Point(165, y), Size = new Size(365, 20), ForeColor = Color.FromArgb(245, 158, 11) };
            grpSpecs.Controls.Add(lblSer);
            grpSpecs.Controls.Add(_lblSerialVal);

            y += step;
            Label lblFp = new Label { Text = "Build Fingerprint:", Location = new Point(15, y), Size = new Size(150, 20), ForeColor = Color.FromArgb(140, 145, 155) };
            _lblFingerprintVal = new Label { Location = new Point(165, y), Size = new Size(365, 36), ForeColor = Color.FromArgb(180, 185, 195), Font = new Font("Consolas", 7.8f) };
            grpSpecs.Controls.Add(lblFp);
            grpSpecs.Controls.Add(_lblFingerprintVal);

            _cmbProfiles.SelectedIndexChanged += delegate
            {
                int idx = _cmbProfiles.SelectedIndex;
                if (idx >= 0 && idx < _filteredProfiles.Count)
                {
                    UpdatePreview(_filteredProfiles[idx]);
                }
            };

            _cmbBrandFilter.SelectedIndexChanged += delegate { ApplyFilter(null); };
            _txtSearch.TextChanged += delegate { ApplyFilter(null); };

            ApplyFilter(currentProfileId);

            _btnSave = new Button
            {
                Text = "Save & Apply",
                DialogResult = DialogResult.OK,
                Location = new Point(335, 395),
                Size = new Size(125, 34),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnSave.FlatAppearance.BorderSize = 0;
            _btnSave.Click += delegate
            {
                int idx = _cmbProfiles.SelectedIndex;
                if (idx >= 0 && idx < _filteredProfiles.Count)
                {
                    SelectedProfileId = _filteredProfiles[idx].Id;
                }
                this.Close();
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(470, 395),
                Size = new Size(90, 34),
                BackColor = Color.FromArgb(55, 65, 81),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;
            _btnCancel.Click += delegate { this.Close(); };

            this.Controls.Add(lblInfo);
            this.Controls.Add(lblBrand);
            this.Controls.Add(_cmbBrandFilter);
            this.Controls.Add(lblSearch);
            this.Controls.Add(_txtSearch);
            this.Controls.Add(_btnPickRandom);
            this.Controls.Add(lblSelect);
            this.Controls.Add(_cmbProfiles);
            this.Controls.Add(grpSpecs);
            this.Controls.Add(_btnSave);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnSave;
            this.CancelButton = _btnCancel;
        }

        private void ApplyFilter(string preferredId)
        {
            string brandSel = _cmbBrandFilter.SelectedItem != null ? _cmbBrandFilter.SelectedItem.ToString() : "All";
            string search = (_txtSearch.Text ?? "").Trim().ToLower();

            _filteredProfiles.Clear();
            _cmbProfiles.Items.Clear();

            int targetIdx = 0;
            for (int i = 0; i < _allProfiles.Count; i++)
            {
                var p = _allProfiles[i];
                bool matchBrand = true;
                if (brandSel.StartsWith("Samsung", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "samsung", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("Google", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "google", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("Xiaomi", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "Xiaomi", StringComparison.OrdinalIgnoreCase) ||
                                 string.Equals(p.Brand, "Redmi", StringComparison.OrdinalIgnoreCase) ||
                                 string.Equals(p.Brand, "POCO", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("OnePlus", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "OnePlus", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("Sony", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "Sony", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("Motorola", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "motorola", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("Realme", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "realme", StringComparison.OrdinalIgnoreCase);
                else if (brandSel.StartsWith("ASUS", StringComparison.OrdinalIgnoreCase))
                    matchBrand = string.Equals(p.Brand, "asus", StringComparison.OrdinalIgnoreCase);

                if (!matchBrand) continue;

                if (!string.IsNullOrEmpty(search))
                {
                    bool matchSearch = (p.DisplayName != null && p.DisplayName.ToLower().Contains(search)) ||
                                       (p.Model != null && p.Model.ToLower().Contains(search)) ||
                                       (p.SocModel != null && p.SocModel.ToLower().Contains(search)) ||
                                       (p.GlRenderer != null && p.GlRenderer.ToLower().Contains(search));
                    if (!matchSearch) continue;
                }

                _filteredProfiles.Add(p);
                string itemText = string.Format("📱 {0}  [{1} / {2}]", p.DisplayName, p.SocModel, p.GlRenderer);
                _cmbProfiles.Items.Add(itemText);

                if (!string.IsNullOrEmpty(preferredId) && string.Equals(p.Id, preferredId, StringComparison.OrdinalIgnoreCase))
                {
                    targetIdx = _filteredProfiles.Count - 1;
                }
            }

            if (_filteredProfiles.Count > 0)
            {
                _cmbProfiles.SelectedIndex = targetIdx;
                UpdatePreview(_filteredProfiles[targetIdx]);
            }
            else
            {
                _lblManufacturerVal.Text = "No devices match filter";
                _lblChipsetVal.Text = "-";
                _lblGpuVal.Text = "-";
                _lblDisplayVal.Text = "-";
                _lblSerialVal.Text = "-";
                _lblFingerprintVal.Text = "-";
            }
        }

        private void UpdatePreview(DeviceProfile p)
        {
            if (p == null) return;
            _lblManufacturerVal.Text = string.Format("{0} ({1})", p.DisplayName, p.Model);
            _lblChipsetVal.Text = string.Format("{0} {1} ({2})", p.SocManufacturer, p.SocModel, p.Platform);
            _lblGpuVal.Text = string.Format("{0} ({1})", p.GlRenderer, p.GlVendor);
            _lblDisplayVal.Text = string.Format("{0} x {1} @ {2} DPI (Max Texture: {3}px)", p.LcdWidth, p.LcdHeight, p.LcdDensity, p.MaxTextureSize);
            _lblSerialVal.Text = string.Format("Prefix: {0} • Board: {1} • Hardware: {2}", p.SerialPrefix ?? "R58M", p.Board, p.Hardware);
            _lblFingerprintVal.Text = p.BuildFingerprint;
        }
    }
}
