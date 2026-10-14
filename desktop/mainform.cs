using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text.RegularExpressions;
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
        private Label _specsLabel;
        private Label _lblActiveBadge;

        private Panel _toolbarPanel;
        private Button _btnLaunchFarm;
        private Button _btnStopAll;
        private Button _btnAddSlot;
        private Button _btnEditSelected;
        private Button _btnRelayout;
        private Button _btnCascade;

        private Label _lblRam;
        private ComboBox _cmbRam;
        private Label _lblCores;
        private ComboBox _cmbCores;
        private Button _btnApplyAllHw;

        private DataGridView _grid;

        private Panel _statusPanel;
        private Label _lblStatusText;
        private Button _btnFocus1;
        private Button _btnFocus2;
        private Button _btnFocus3;
        private Button _btnFocus4;

        private volatile bool _isClosing = false;
        private volatile bool _isFarmLaunching = false;
        private readonly HashSet<string> _inFlightOps = new HashSet<string>();
        private readonly object _opsLock = new object();
        private readonly object _logFileLock = new object();

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
            this.Text = "Dofus Touch Farm Manager";
            this.Size = new Size(1160, 680);
            this.MinimumSize = new Size(1020, 520);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(18, 19, 24);
            this.ForeColor = Color.FromArgb(235, 235, 240);
            this.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);
            this.DoubleBuffered = true;

            string iconPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_icon.ico");
            if (File.Exists(iconPath))
            {
                try { this.Icon = new Icon(iconPath); } catch { }
            }

            // Top Header Panel
            _headerPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 68,
                BackColor = Color.FromArgb(25, 26, 34),
                Padding = new Padding(15, 8, 15, 8)
            };

            _titleLabel = new Label
            {
                Text = "⚡ DOFUS TOUCH FARM MANAGER",
                Font = new Font("Segoe UI", 12.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(96, 165, 250),
                AutoSize = true,
                Location = new Point(16, 10)
            };

            _specsLabel = new Label
            {
                Text = "Detecting PC Hardware...",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(156, 163, 175),
                AutoSize = true,
                Location = new Point(16, 38)
            };

            _lblActiveBadge = new Label
            {
                Text = "○ 0 / 4 Running",
                Font = new Font("Segoe UI", 10.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(156, 163, 175),
                AutoSize = true,
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                Location = new Point(_headerPanel.Width - 175, 22)
            };

            _headerPanel.Controls.Add(_titleLabel);
            _headerPanel.Controls.Add(_specsLabel);
            _headerPanel.Controls.Add(_lblActiveBadge);

            // Clean Single-Row Toolbar Panel
            _toolbarPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 52,
                BackColor = Color.FromArgb(22, 23, 29),
                Padding = new Padding(15, 8, 15, 8)
            };

            int btnY = 9;
            int btnH = 34;

            _btnLaunchFarm = CreateButton("🚀 Launch All", Color.FromArgb(37, 99, 235), Color.White, 125, btnH);
            _btnLaunchFarm.Location = new Point(15, btnY);
            _btnLaunchFarm.Font = new Font("Segoe UI", 9.2f, FontStyle.Bold);
            _btnLaunchFarm.Click += OnLaunchFarmClick;

            _btnStopAll = CreateButton("🛑 Stop All", Color.FromArgb(220, 38, 38), Color.White, 95, btnH);
            _btnStopAll.Location = new Point(146, btnY);
            _btnStopAll.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _btnStopAll.Click += OnStopAllClick;

            _btnAddSlot = CreateButton("➕ Add Instance", Color.FromArgb(16, 185, 129), Color.White, 125, btnH);
            _btnAddSlot.Location = new Point(250, btnY);
            _btnAddSlot.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _btnAddSlot.Click += OnAddSlotClick;

            _btnEditSelected = CreateButton("✏️ Edit Selected", Color.FromArgb(59, 130, 246), Color.White, 120, btnH);
            _btnEditSelected.Location = new Point(381, btnY);
            _btnEditSelected.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _btnEditSelected.Click += OnEditSelectedClick;

            _btnRelayout = CreateButton("📐 Tile 2x2", Color.FromArgb(39, 41, 52), Color.White, 88, btnH);
            _btnRelayout.Location = new Point(510, btnY);
            _btnRelayout.Click += OnRelayoutClick;

            _btnCascade = CreateButton("📐 Cascade", Color.FromArgb(39, 41, 52), Color.White, 88, btnH);
            _btnCascade.Location = new Point(604, btnY);
            _btnCascade.Click += OnCascadeClick;

            // Global RAM & CPU Allocation Controls on Right
            _lblRam = new Label { Text = "RAM:", ForeColor = Color.FromArgb(180, 185, 195), AutoSize = true, Location = new Point(712, 16) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 105,
                Location = new Point(752, 13)
            };
            _cmbRam.Items.AddRange(new object[] {
                "512 MB", "768 MB", "1024 MB", "1536 MB", "2048 MB",
                "3072 MB", "4096 MB", "6144 MB", "8192 MB", "12288 MB", "16384 MB"
            });
            _cmbRam.SelectedIndex = 2; // 1024 MB

            _lblCores = new Label { Text = "CPU:", ForeColor = Color.FromArgb(180, 185, 195), AutoSize = true, Location = new Point(866, 16) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(28, 30, 38),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Width = 82,
                Location = new Point(904, 13)
            };
            _cmbCores.Items.AddRange(new object[] { "1 Core", "2 Cores", "3 Cores", "4 Cores", "6 Cores", "8 Cores" });
            _cmbCores.SelectedIndex = 0; // 1 Core

            _btnApplyAllHw = CreateButton("⚡ Apply to All", Color.FromArgb(124, 58, 237), Color.White, 115, btnH);
            _btnApplyAllHw.Location = new Point(996, btnY);
            _btnApplyAllHw.Font = new Font("Segoe UI", 8.8f, FontStyle.Bold);
            _btnApplyAllHw.Click += OnApplyAllHardwareClick;

            _toolbarPanel.Controls.Add(_btnLaunchFarm);
            _toolbarPanel.Controls.Add(_btnStopAll);
            _toolbarPanel.Controls.Add(_btnAddSlot);
            _toolbarPanel.Controls.Add(_btnEditSelected);
            _toolbarPanel.Controls.Add(_btnRelayout);
            _toolbarPanel.Controls.Add(_btnCascade);
            _toolbarPanel.Controls.Add(_lblRam);
            _toolbarPanel.Controls.Add(_cmbRam);
            _toolbarPanel.Controls.Add(_lblCores);
            _toolbarPanel.Controls.Add(_cmbCores);
            _toolbarPanel.Controls.Add(_btnApplyAllHw);

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
                RowTemplate = { Height = 40 },
                ShowCellToolTips = true,
                ReadOnly = true
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
            _grid.Columns.Add("CustomName", "Instance Name");
            _grid.Columns.Add("Name", "Slot");
            _grid.Columns.Add("Ram", "RAM");
            _grid.Columns.Add("Cores", "CPU Cores");
            _grid.Columns.Add("Device", "Phone Identity");
            _grid.Columns.Add("Proxy", "Network");

            DataGridViewButtonColumn btnAction = new DataGridViewButtonColumn
            {
                Name = "Action",
                HeaderText = "Run",
                Text = "Launch",
                UseColumnTextForButtonValue = false,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnAction);

            DataGridViewButtonColumn btnEdit = new DataGridViewButtonColumn
            {
                Name = "Edit",
                HeaderText = "Edit",
                Text = "✏️ Edit",
                UseColumnTextForButtonValue = true,
                FlatStyle = FlatStyle.Flat
            };
            _grid.Columns.Add(btnEdit);

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
            _grid.Columns["CustomName"].Width = 165;
            _grid.Columns["Name"].Width = 85;
            _grid.Columns["Ram"].Width = 85;
            _grid.Columns["Cores"].Width = 85;
            _grid.Columns["Device"].Width = 205;
            _grid.Columns["Proxy"].Width = 155;
            _grid.Columns["Action"].Width = 80;
            _grid.Columns["Edit"].Width = 75;
            _grid.Columns["Clone"].Width = 75;
            _grid.Columns["Delete"].Width = 75;

            _grid.CellContentClick += OnGridCellContentClick;
            _grid.CellDoubleClick += OnGridCellDoubleClick;

            // Clean Bottom Status Bar (no developer log clutter!)
            _statusPanel = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 36,
                BackColor = Color.FromArgb(24, 26, 34),
                Padding = new Padding(15, 6, 15, 6)
            };

            _lblStatusText = new Label
            {
                Text = "● Ready — Double-click any instance or click '✏️ Edit' to change its Name, RAM, or CPU Cores.",
                Font = new Font("Segoe UI", 9f),
                ForeColor = Color.FromArgb(180, 185, 200),
                AutoSize = true,
                Location = new Point(15, 9)
            };

            Label lblFocus = new Label
            {
                Text = "Quick Focus:",
                ForeColor = Color.FromArgb(140, 145, 160),
                AutoSize = true,
                Anchor = AnchorStyles.Top | AnchorStyles.Right,
                Location = new Point(_statusPanel.Width - 260, 9)
            };

            _btnFocus1 = CreateButton("#1", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 38, 24);
            _btnFocus1.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus1.Location = new Point(_statusPanel.Width - 175, 6);
            _btnFocus1.Click += delegate { _engine.FocusInstance(5554); };

            _btnFocus2 = CreateButton("#2", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 38, 24);
            _btnFocus2.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus2.Location = new Point(_statusPanel.Width - 133, 6);
            _btnFocus2.Click += delegate { _engine.FocusInstance(5556); };

            _btnFocus3 = CreateButton("#3", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 38, 24);
            _btnFocus3.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus3.Location = new Point(_statusPanel.Width - 91, 6);
            _btnFocus3.Click += delegate { _engine.FocusInstance(5558); };

            _btnFocus4 = CreateButton("#4", Color.FromArgb(34, 36, 46), Color.FromArgb(147, 197, 253), 38, 24);
            _btnFocus4.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnFocus4.Location = new Point(_statusPanel.Width - 49, 6);
            _btnFocus4.Click += delegate { _engine.FocusInstance(5560); };

            _statusPanel.Controls.Add(_lblStatusText);
            _statusPanel.Controls.Add(lblFocus);
            _statusPanel.Controls.Add(_btnFocus1);
            _statusPanel.Controls.Add(_btnFocus2);
            _statusPanel.Controls.Add(_btnFocus3);
            _statusPanel.Controls.Add(_btnFocus4);

            this.Controls.Add(_grid);
            this.Controls.Add(_statusPanel);
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

            // Self-Heal Watchdog and Priority Governor are always active automatically
            _watchdog = new FarmWatchdog(_engine);
            _watchdog.OnLog += Log;
            _watchdog.Start();

            _governor = new PriorityGovernor();
            _governor.OnLog += Log;
            _governor.Start();

            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    _specs = HardwareSpecs.Detect();
                    SafeInvoke(delegate
                    {
                        if (_specs != null)
                        {
                            _specsLabel.Text = string.Format("PC: {0} • {1} • {2} GB RAM ({3} GB Free) • {4} Threads",
                                _specs.CpuName, _specs.GpuName, _specs.HostRamGB, _specs.FreeRamGB, _specs.LogicalThreads);
                            if (_specs.HostRamGB >= 15.5)
                            {
                                _cmbRam.SelectedIndex = 3; // 1536 MB
                                _cmbCores.SelectedIndex = 1; // 2 Cores
                            }
                        }
                        RefreshGrid();
                    });
                }
                catch { }
            });
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            RefreshGrid();
        }

        private void Log(string msg)
        {
            if (string.IsNullOrEmpty(msg)) return;
            try
            {
                lock (_logFileLock)
                {
                    string logFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "DofusFarm.log");
                    File.AppendAllText(logFile, msg + "\r\n");
                }
            }
            catch { }

            if (this.IsDisposed || _isClosing || !this.IsHandleCreated) return;
            // Show only clean user-relevant status updates on the status bar
            if (!msg.Contains("[GOVERNOR]") && !msg.Contains("[SECURITY]"))
            {
                SafeBeginInvoke(delegate
                {
                    if (_lblStatusText != null && !this.IsDisposed)
                    {
                        string clean = Regex.Replace(msg, @"^\[\d{2}:\d{2}:\d{2}\]\s*", "");
                        _lblStatusText.Text = "● " + clean;
                    }
                });
            }
        }

        private int GetSelectedRam()
        {
            string s = _cmbRam.SelectedItem != null ? _cmbRam.SelectedItem.ToString() : "1024 MB";
            Match m = Regex.Match(s, @"(\d+)");
            return m.Success ? int.Parse(m.Groups[1].Value) : 1024;
        }

        private int GetSelectedCores()
        {
            string s = _cmbCores.SelectedItem != null ? _cmbCores.SelectedItem.ToString() : "1 Core";
            Match m = Regex.Match(s, @"(\d+)");
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
                        string selectedName = null;
                        if (_grid.SelectedRows.Count > 0 && _grid.SelectedRows[0].Tag is InstanceModel)
                        {
                            selectedName = ((InstanceModel)_grid.SelectedRows[0].Tag).Name;
                        }

                        _grid.Rows.Clear();
                        int runningCount = 0;
                        foreach (InstanceModel inst in list)
                        {
                            if (inst.IsRunning) runningCount++;
                            string proxyDisplay = inst.Proxy ?? "127.0.0.1:8880";
                            if (proxyDisplay.Contains("127.0.0.1") || proxyDisplay.Contains("Accelerator"))
                                proxyDisplay = "⚡ Built-in Accelerator";
                            else
                                proxyDisplay = "🌐 " + proxyDisplay.Replace("[🌐] ", "");

                            int rowIdx = _grid.Rows.Add(
                                inst.IsRunning ? "● RUNNING" : "○ STOPPED",
                                inst.CustomName ?? inst.DisplayName,
                                inst.Name,
                                inst.RamMb + " MB",
                                inst.Cores + (inst.Cores == 1 ? " Core" : " Cores"),
                                "📱 " + (inst.PhoneModelName ?? "Galaxy Tab S6"),
                                proxyDisplay,
                                inst.IsRunning ? "Stop" : "Launch",
                                "✏️ Edit",
                                "📋 Clone",
                                "🗑️ Delete"
                            );

                            DataGridViewRow row = _grid.Rows[rowIdx];
                            row.Tag = inst;
                            row.Cells["CustomName"].Style.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
                            row.Cells["CustomName"].Style.ForeColor = Color.White;
                            row.Cells["Name"].Style.ForeColor = Color.FromArgb(140, 145, 160);
                            row.Cells["Device"].Style.ForeColor = Color.FromArgb(220, 225, 235);
                            row.Cells["Proxy"].Style.ForeColor = Color.FromArgb(175, 180, 195);

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
                            row.Cells["Edit"].Style.ForeColor = Color.FromArgb(96, 165, 250);
                            row.Cells["Clone"].Style.ForeColor = Color.FromArgb(245, 158, 11);
                            row.Cells["Delete"].Style.ForeColor = Color.FromArgb(244, 63, 94);

                            if (!string.IsNullOrEmpty(selectedName) && string.Equals(inst.Name, selectedName, StringComparison.OrdinalIgnoreCase))
                            {
                                row.Selected = true;
                            }
                        }

                        int count = list.Count > 0 ? list.Count : 4;
                        _btnLaunchFarm.Text = string.Format("🚀 Launch All ({0}x)", count);

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
                catch { }
            });
        }

        private void OnApplyAllHardwareClick(object sender, EventArgs e)
        {
            int ram = GetSelectedRam();
            int cores = GetSelectedCores();

            _btnApplyAllHw.Enabled = false;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    int updated = _engine.ApplyHardwareToAllInstances(ram, cores);
                    Log(string.Format("Applied {0} MB RAM and {1} CPU Core(s) across {2} instance(s).", ram, cores, updated));
                    RefreshGrid();
                }
                catch (Exception ex)
                {
                    Log("Error applying hardware settings: " + ex.Message);
                }
                finally
                {
                    SafeBeginInvoke(delegate { _btnApplyAllHw.Enabled = true; });
                }
            });
        }

        private void OnLaunchFarmClick(object sender, EventArgs e)
        {
            if (_isFarmLaunching) return;
            _isFarmLaunching = true;
            _btnLaunchFarm.Enabled = false;

            int defaultRam = GetSelectedRam();
            int defaultCores = GetSelectedCores();

            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    List<InstanceModel> existingInsts = _engine.GetInstances();
                    if (existingInsts.Count == 0)
                    {
                        for (int i = 1; i <= 4; i++)
                        {
                            if (_isClosing || !_isFarmLaunching) return;
                            string slotName = string.Format("dofus-{0:D2}", i);
                            _engine.ProvisionInstance(slotName, defaultRam, defaultCores, null, "Instance #" + i, "");
                        }
                        existingInsts = _engine.GetInstances();
                    }

                    Log(string.Format("Launching {0} instance(s)...", existingInsts.Count));

                    foreach (InstanceModel im in existingInsts)
                    {
                        if (_isClosing || !_isFarmLaunching) return;
                        if (im.IsRunning) continue;
                        _engine.LaunchInstance(
                            im.Name,
                            im.Port,
                            im.RamMb > 0 ? im.RamMb : defaultRam,
                            im.Cores > 0 ? im.Cores : defaultCores,
                            im.Proxy);
                        Thread.Sleep(450);
                    }

                    Thread.Sleep(2500);
                    if (_isClosing || !_isFarmLaunching) return;
                    SafeInvoke(delegate
                    {
                        RefreshGrid();
                        OnRelayoutClick(null, null);
                    });

                    Thread.Sleep(4000);
                    if (_isClosing || !_isFarmLaunching) return;
                    SafeInvoke(delegate
                    {
                        RefreshGrid();
                        OnRelayoutClick(null, null);
                    });
                }
                catch (Exception ex)
                {
                    Log("Launch error: " + ex.Message);
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
            _isFarmLaunching = false;
            _btnStopAll.Enabled = false;
            Log("Stopping all running instances...");
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
                    Log("Error stopping instances: " + ex.Message);
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
                        if (inst.IsRunning && inst.Pid > 0) pids.Add(inst.Pid);
                    }

                    if (pids.Count == 0)
                    {
                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                        try
                        {
                            foreach (Process p in procs) pids.Add(p.Id);
                        }
                        finally
                        {
                            foreach (Process p in procs) p.Dispose();
                        }
                    }

                    if (pids.Count > 0)
                    {
                        Win32.ApplyFarmLayout(pids, true);
                        Log(string.Format("Tiled {0} active window(s).", pids.Count));
                    }
                }
                catch { }
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
                        if (inst.IsRunning && inst.Pid > 0) pids.Add(inst.Pid);
                    }

                    if (pids.Count == 0)
                    {
                        Process[] procs = Process.GetProcessesByName("qemu-system-x86_64");
                        try
                        {
                            foreach (Process p in procs) pids.Add(p.Id);
                        }
                        finally
                        {
                            foreach (Process p in procs) p.Dispose();
                        }
                    }

                    if (pids.Count > 0)
                    {
                        Win32.ApplyCascadeLayout(pids);
                        Log(string.Format("Cascaded {0} active window(s).", pids.Count));
                    }
                }
                catch { }
            });
        }

        private void OnAddSlotClick(object sender, EventArgs e)
        {
            string nextSlot = _engine.GetNextAvailableSlot();
            int slotNum = 1;
            Match m = Regex.Match(nextSlot, @"(\d+)$");
            if (m.Success) int.TryParse(m.Groups[1].Value, out slotNum);

            string defaultCustomName = "Instance #" + slotNum;
            int defaultRam = GetSelectedRam();
            int defaultCores = GetSelectedCores();
            DeviceProfile defProfile = DeviceCatalog.GetDefaultProfileForIndex(slotNum);

            using (InstanceEditorDialog dlg = new InstanceEditorDialog(true, nextSlot, defaultCustomName, defaultRam, defaultCores, defProfile.Id, ""))
            {
                if (dlg.ShowDialog(this) == DialogResult.OK)
                {
                    string customName = dlg.InstanceCustomName;
                    int ram = dlg.SelectedRamMb;
                    int cores = dlg.SelectedCores;
                    string profileId = dlg.SelectedProfileId;
                    string proxy = dlg.SelectedProxy;

                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        try
                        {
                            _engine.ProvisionInstance(nextSlot, ram, cores, profileId, customName, proxy);
                            RefreshGrid();
                        }
                        catch (Exception ex)
                        {
                            Log("Error adding instance: " + ex.Message);
                        }
                    });
                }
            }
        }

        private void OnEditSelectedClick(object sender, EventArgs e)
        {
            if (_grid.SelectedRows.Count == 0)
            {
                MessageBox.Show("Select an instance in the table first to edit it.", "Edit Instance", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            InstanceModel inst = _grid.SelectedRows[0].Tag as InstanceModel;
            if (inst != null)
            {
                OpenEditDialogForInstance(inst);
            }
        }

        private void OpenEditDialogForInstance(InstanceModel inst)
        {
            if (inst == null) return;
            using (InstanceEditorDialog dlg = new InstanceEditorDialog(
                false,
                inst.Name,
                inst.CustomName ?? inst.DisplayName,
                inst.RamMb,
                inst.Cores,
                inst.PhoneModelId,
                inst.Proxy))
            {
                if (dlg.ShowDialog(this) == DialogResult.OK)
                {
                    string slotName = inst.Name;
                    string customName = dlg.InstanceCustomName;
                    int ram = dlg.SelectedRamMb;
                    int cores = dlg.SelectedCores;
                    string profileId = dlg.SelectedProfileId;
                    string proxy = dlg.SelectedProxy;

                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        try
                        {
                            _engine.UpdateInstanceConfig(slotName, customName, ram, cores, profileId, proxy);
                            RefreshGrid();
                        }
                        catch (Exception ex)
                        {
                            Log("Error updating instance: " + ex.Message);
                        }
                    });
                }
            }
        }

        private void OnGridCellContentClick(object sender, DataGridViewCellEventArgs e)
        {
            if (e.RowIndex < 0 || e.RowIndex >= _grid.Rows.Count) return;
            DataGridViewRow row = _grid.Rows[e.RowIndex];
            if (row == null) return;
            InstanceModel inst = row.Tag as InstanceModel;
            if (inst == null) return;

            // 1. Launch / Stop
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
            // 2. Edit Instance (Name, RAM, CPU Cores, Phone Model, Proxy)
            else if (e.ColumnIndex == _grid.Columns["Edit"].Index)
            {
                OpenEditDialogForInstance(inst);
            }
            // 3. Clone Instance
            else if (e.ColumnIndex == _grid.Columns["Clone"].Index)
            {
                string nextSlot = _engine.GetNextAvailableSlot();
                string suggestedCustom = (inst.CustomName ?? inst.Name) + " (Copy)";
                using (CloneDialog dlg = new CloneDialog(inst.CustomName ?? inst.Name, nextSlot, suggestedCustom))
                {
                    if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.TargetSlotName))
                    {
                        string srcName = inst.Name;
                        string targetSlot = dlg.TargetSlotName;
                        string targetCustom = dlg.TargetCustomName;
                        bool cloneState = dlg.CloneState;

                        if (!TryBeginOp(targetSlot)) return;
                        ThreadPool.QueueUserWorkItem(delegate
                        {
                            try
                            {
                                if (_isClosing) return;
                                _engine.CloneInstance(srcName, targetSlot, cloneState, targetCustom);
                                Thread.Sleep(800);
                                RefreshGrid();
                            }
                            catch (Exception ex)
                            {
                                Log(string.Format("Error cloning {0}: {1}", srcName, ex.Message));
                            }
                            finally
                            {
                                EndOp(targetSlot);
                            }
                        });
                    }
                }
            }
            // 4. Delete Instance
            else if (e.ColumnIndex == _grid.Columns["Delete"].Index)
            {
                DialogResult confirm = MessageBox.Show(
                    string.Format("Delete instance '{0}' ({1}) and remove its virtual disk?", inst.CustomName ?? inst.Name, inst.Name),
                    "Confirm Delete",
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

            OpenEditDialogForInstance(inst);
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

    public class InstanceEditorDialog : Form
    {
        public string InstanceCustomName { get; private set; }
        public int SelectedRamMb { get; private set; }
        public int SelectedCores { get; private set; }
        public string SelectedProfileId { get; private set; }
        public string SelectedProxy { get; private set; }

        private TextBox _txtCustomName;
        private ComboBox _cmbRam;
        private ComboBox _cmbCores;
        private ComboBox _cmbProfiles;
        private Button _btnRandomProfile;
        private TextBox _txtProxy;
        private Button _btnSave;
        private Button _btnCancel;

        private readonly List<DeviceProfile> _profiles;

        public InstanceEditorDialog(bool isNew, string slotName, string currentCustomName, int currentRamMb, int currentCores, string currentProfileId, string currentProxy)
        {
            this.Text = isNew ? ("Add New Instance (" + slotName + ")") : ("Edit Instance — " + currentCustomName + " (" + slotName + ")");
            this.Size = new Size(520, 385);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(22, 23, 30);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            _profiles = DeviceCatalog.GetAllProfiles();

            int y = 20;

            // 1. Instance Name
            Label lblName = new Label { Text = "Instance Name:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(200, 205, 215) };
            _txtCustomName = new TextBox
            {
                Text = currentCustomName ?? slotName,
                Location = new Point(165, y),
                Width = 315,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold)
            };
            y += 45;

            // 2. RAM & CPU Cores Allocation
            Label lblRam = new Label { Text = "RAM Allocation:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(200, 205, 215) };
            _cmbRam = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(165, y),
                Width = 140,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            int[] ramOptions = new[] { 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384 };
            int ramIdx = 2;
            for (int i = 0; i < ramOptions.Length; i++)
            {
                _cmbRam.Items.Add(ramOptions[i] + " MB");
                if (ramOptions[i] == currentRamMb) ramIdx = i;
            }
            _cmbRam.SelectedIndex = ramIdx;

            Label lblCores = new Label { Text = "CPU Cores:", Location = new Point(320, y + 3), AutoSize = true, ForeColor = Color.FromArgb(200, 205, 215) };
            _cmbCores = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(395, y),
                Width = 85,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            int[] coreOptions = new[] { 1, 2, 3, 4, 6, 8 };
            int coreIdx = 0;
            for (int i = 0; i < coreOptions.Length; i++)
            {
                _cmbCores.Items.Add(coreOptions[i] + (coreOptions[i] == 1 ? " Core" : " Cores"));
                if (coreOptions[i] == currentCores) coreIdx = i;
            }
            _cmbCores.SelectedIndex = coreIdx;
            y += 45;

            // 3. Phone Identity Spoofing
            Label lblPhone = new Label { Text = "Phone Identity:", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(200, 205, 215) };
            _cmbProfiles = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Location = new Point(165, y),
                Width = 235,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            int profIdx = 0;
            for (int i = 0; i < _profiles.Count; i++)
            {
                _cmbProfiles.Items.Add(_profiles[i].DisplayName);
                if (!string.IsNullOrEmpty(currentProfileId) && string.Equals(_profiles[i].Id, currentProfileId, StringComparison.OrdinalIgnoreCase))
                    profIdx = i;
            }
            if (_cmbProfiles.Items.Count > 0) _cmbProfiles.SelectedIndex = profIdx;

            _btnRandomProfile = new Button
            {
                Text = "🎲 Random",
                Location = new Point(406, y - 1),
                Size = new Size(74, 26),
                BackColor = Color.FromArgb(124, 58, 237),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnRandomProfile.FlatAppearance.BorderSize = 0;
            _btnRandomProfile.Click += delegate
            {
                if (_profiles.Count > 0)
                {
                    Random rng = new Random();
                    _cmbProfiles.SelectedIndex = rng.Next(_profiles.Count);
                }
            };
            y += 45;

            // 4. Proxy / Network
            Label lblProxy = new Label { Text = "Proxy (Optional):", Location = new Point(22, y + 3), AutoSize = true, ForeColor = Color.FromArgb(200, 205, 215) };
            string cleanProxy = (currentProxy ?? "").Trim();
            if (cleanProxy.StartsWith("[🌐] ")) cleanProxy = cleanProxy.Substring(5).Trim();
            if (cleanProxy.StartsWith("[⚡]") || cleanProxy.Contains("127.0.0.1:8880") || cleanProxy.Contains("Accelerator")) cleanProxy = "";

            _txtProxy = new TextBox
            {
                Text = cleanProxy,
                Location = new Point(165, y),
                Width = 315,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Consolas", 9.5f)
            };

            Label lblProxyHint = new Label
            {
                Text = "Format: IP:Port (leave blank to use the built-in high-speed network accelerator).",
                Location = new Point(165, y + 28),
                Size = new Size(320, 32),
                ForeColor = Color.FromArgb(140, 145, 160),
                Font = new Font("Segoe UI", 8.2f)
            };
            y += 75;

            _btnSave = new Button
            {
                Text = isNew ? "➕ Create Instance" : "💾 Save Changes",
                DialogResult = DialogResult.OK,
                Location = new Point(235, y),
                Size = new Size(145, 36),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnSave.FlatAppearance.BorderSize = 0;
            _btnSave.Click += delegate
            {
                InstanceCustomName = _txtCustomName.Text.Trim();
                if (string.IsNullOrEmpty(InstanceCustomName)) InstanceCustomName = slotName;

                Match mR = Regex.Match(_cmbRam.SelectedItem != null ? _cmbRam.SelectedItem.ToString() : "1024", @"(\d+)");
                SelectedRamMb = mR.Success ? int.Parse(mR.Groups[1].Value) : 1024;

                Match mC = Regex.Match(_cmbCores.SelectedItem != null ? _cmbCores.SelectedItem.ToString() : "1", @"(\d+)");
                SelectedCores = mC.Success ? int.Parse(mC.Groups[1].Value) : 1;

                int pIdx = _cmbProfiles.SelectedIndex;
                SelectedProfileId = (pIdx >= 0 && pIdx < _profiles.Count) ? _profiles[pIdx].Id : "galaxy-tab-s6";
                SelectedProxy = _txtProxy.Text.Trim();
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(390, y),
                Size = new Size(90, 36),
                BackColor = Color.FromArgb(45, 48, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;

            this.Controls.Add(lblName);
            this.Controls.Add(_txtCustomName);
            this.Controls.Add(lblRam);
            this.Controls.Add(_cmbRam);
            this.Controls.Add(lblCores);
            this.Controls.Add(_cmbCores);
            this.Controls.Add(lblPhone);
            this.Controls.Add(_cmbProfiles);
            this.Controls.Add(_btnRandomProfile);
            this.Controls.Add(lblProxy);
            this.Controls.Add(_txtProxy);
            this.Controls.Add(lblProxyHint);
            this.Controls.Add(_btnSave);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnSave;
            this.CancelButton = _btnCancel;
        }
    }

    public class CloneDialog : Form
    {
        public string TargetSlotName { get; private set; }
        public string TargetCustomName { get; private set; }
        public bool CloneState { get; private set; }

        private TextBox _txtCustomName;
        private CheckBox _chkState;
        private Button _btnOk;
        private Button _btnCancel;

        public CloneDialog(string sourceDisplayName, string targetSlotName, string suggestedCustomName)
        {
            TargetSlotName = targetSlotName;
            this.Text = "Clone Instance — " + sourceDisplayName;
            this.Size = new Size(440, 235);
            this.StartPosition = FormStartPosition.CenterParent;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(22, 23, 30);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            Label lbl = new Label
            {
                Text = string.Format("Clone '{0}' into slot {1} with a unique hardware identity:", sourceDisplayName, targetSlotName),
                Location = new Point(20, 16),
                Size = new Size(390, 28),
                ForeColor = Color.FromArgb(200, 205, 215)
            };

            Label lblName = new Label { Text = "New Instance Name:", Location = new Point(20, 56), AutoSize = true };
            _txtCustomName = new TextBox
            {
                Text = suggestedCustomName,
                Location = new Point(160, 53),
                Width = 240,
                BackColor = Color.FromArgb(32, 34, 44),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };

            _chkState = new CheckBox
            {
                Text = "Keep downloaded game files & map cache (Instant copy)",
                Checked = true,
                Location = new Point(20, 95),
                Size = new Size(390, 30),
                ForeColor = Color.FromArgb(52, 211, 153)
            };

            _btnOk = new Button
            {
                Text = "📋 Clone Now",
                DialogResult = DialogResult.OK,
                Location = new Point(195, 142),
                Size = new Size(115, 34),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnOk.FlatAppearance.BorderSize = 0;
            _btnOk.Click += delegate
            {
                TargetCustomName = _txtCustomName.Text.Trim();
                if (string.IsNullOrEmpty(TargetCustomName)) TargetCustomName = targetSlotName;
                CloneState = _chkState.Checked;
            };

            _btnCancel = new Button
            {
                Text = "Cancel",
                DialogResult = DialogResult.Cancel,
                Location = new Point(320, 142),
                Size = new Size(80, 34),
                BackColor = Color.FromArgb(45, 48, 60),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;

            this.Controls.Add(lbl);
            this.Controls.Add(lblName);
            this.Controls.Add(_txtCustomName);
            this.Controls.Add(_chkState);
            this.Controls.Add(_btnOk);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnOk;
            this.CancelButton = _btnCancel;
        }
    }
}
