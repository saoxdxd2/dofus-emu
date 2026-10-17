using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Dofus Touch Farm Uninstaller")]
[assembly: AssemblyDescription("Dofus Touch Virtualization Farm Native Uninstaller")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Touch Farm")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.2.0.0")]
[assembly: AssemblyVersion("2.2.0.0")]

namespace DofusUninstall
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
                string exePath = Assembly.GetExecutingAssembly().Location;
                string tempDir = Path.GetTempPath();

                bool noRespawn = false;
                int waitForPid = 0;
                string targetDir = baseDir;
                bool isQuiet = false;
                bool isForce = false;

                if (args != null)
                {
                    for (int i = 0; i < args.Length; i++)
                    {
                        if (args[i].Equals("-NoRespawn", StringComparison.OrdinalIgnoreCase) || args[i].Equals("/NoRespawn", StringComparison.OrdinalIgnoreCase))
                        {
                            noRespawn = true;
                        }
                        else if (args[i].Equals("-WaitForPid", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                        {
                            int.TryParse(args[i + 1], out waitForPid);
                        }
                        else if (args[i].Equals("-InstallDir", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                        {
                            targetDir = args[i + 1].Trim('"');
                        }
                        else if (args[i].Equals("/quiet", StringComparison.OrdinalIgnoreCase) || args[i].Equals("-quiet", StringComparison.OrdinalIgnoreCase))
                        {
                            isQuiet = true;
                        }
                        else if (args[i].Equals("/force", StringComparison.OrdinalIgnoreCase) || args[i].Equals("-force", StringComparison.OrdinalIgnoreCase))
                        {
                            isForce = true;
                        }
                    }
                }

                if (!noRespawn && !exePath.StartsWith(tempDir, StringComparison.OrdinalIgnoreCase))
                {
                    int currentPid = Process.GetCurrentProcess().Id;
                    string tempExe = Path.Combine(tempDir, "dofus_uninstall_" + currentPid + ".exe");
                    try
                    {
                        File.Copy(exePath, tempExe, true);
                        string forwardArgs = string.Format("-WaitForPid {0} -InstallDir \"{1}\" {2} -NoRespawn",
                            currentPid,
                            baseDir,
                            args != null ? string.Join(" ", args) : "");
                        ProcessStartInfo psiRestart = new ProcessStartInfo
                        {
                            FileName = tempExe,
                            Arguments = forwardArgs,
                            UseShellExecute = false
                        };
                        Process.Start(psiRestart);
                        return;
                    }
                    catch { }
                }

                if (waitForPid > 0)
                {
                    try
                    {
                        using (Process parent = Process.GetProcessById(waitForPid))
                        {
                            parent.WaitForExit(10000);
                        }
                    }
                    catch { }
                    Thread.Sleep(400);
                }

                if (string.IsNullOrEmpty(targetDir) || targetDir.StartsWith(tempDir, StringComparison.OrdinalIgnoreCase))
                {
                    try
                    {
                        using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\DofusFarm"))
                        {
                            if (k != null)
                            {
                                object val = k.GetValue("InstallPath");
                                if (val != null) targetDir = val.ToString().TrimEnd('\\');
                            }
                        }
                    }
                    catch { }
                }

                if (!string.IsNullOrEmpty(targetDir) && (File.Exists(Path.Combine(targetDir, @"desktop\MainForm.cs")) || File.Exists(Path.Combine(targetDir, @"builder\Build.cs"))))
                {
                    targetDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "DofusFarm");
                }

                if (isQuiet || isForce)
                {
                    RunUninstallSteps(targetDir, exePath, tempDir, noRespawn, isForce, null);
                }
                else
                {
                    Application.Run(new UninstallForm(targetDir, exePath, tempDir, noRespawn, isForce));
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show("Uninstallation Error: " + ex.Message, "Dofus Touch Farm Uninstall", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        public static void RunUninstallSteps(string targetDir, string exePath, string tempDir, bool noRespawn, bool isForce, Action<int, string> onProgress)
        {
            if (onProgress != null) onProgress(10, "Stopping active game instances and network proxies...");
            KillProcesses(new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy", "DofusFarm", "adb", "msedgewebview2" }, targetDir);
            Thread.Sleep(1200);

            if (onProgress != null) onProgress(35, "Removing Android virtual disks, snapshots, and caches...");
            string userProfile = Environment.GetEnvironmentVariable("USERPROFILE");
            if (string.IsNullOrEmpty(userProfile)) userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string avdHome = Path.Combine(userProfile, @".android\avd");
            if (Directory.Exists(avdHome))
            {
                foreach (string d in Directory.GetDirectories(avdHome))
                {
                    string dirName = Path.GetFileName(d);
                    if (dirName.EndsWith(".avd", StringComparison.OrdinalIgnoreCase))
                    {
                        string baseName = dirName.Substring(0, dirName.Length - 4);
                        if (baseName.StartsWith("dofus-", StringComparison.OrdinalIgnoreCase))
                        {
                            SafeDeleteDirectory(d);
                        }
                    }
                }
                foreach (string f in Directory.GetFiles(avdHome))
                {
                    string fileName = Path.GetFileName(f);
                    if (fileName.EndsWith(".ini", StringComparison.OrdinalIgnoreCase))
                    {
                        string baseName = fileName.Substring(0, fileName.Length - 4);
                        if (baseName.StartsWith("dofus-", StringComparison.OrdinalIgnoreCase))
                        {
                            try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); } catch { }
                        }
                    }
                }
            }

            string androidCache = Path.Combine(userProfile, @".android\cache");
            if (Directory.Exists(androidCache))
            {
                try { Directory.Delete(androidCache, true); } catch { }
            }

            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string wvDataDir = Path.Combine(localAppData, "DofusFarm");
            if (Directory.Exists(wvDataDir))
            {
                SafeDeleteDirectory(wvDataDir);
            }

            try
            {
                foreach (string d in Directory.GetDirectories(tempDir, "DofusSetup_*"))
                {
                    try { Directory.Delete(d, true); } catch { }
                }
                foreach (string d in Directory.GetDirectories(tempDir, "dofus_build_stage_*"))
                {
                    try { Directory.Delete(d, true); } catch { }
                }
            }
            catch { }

            if (onProgress != null) onProgress(65, "Removing desktop shortcuts and registry entries...");
            string[] shortcutDirs = new[]
            {
                Environment.GetFolderPath(Environment.SpecialFolder.Desktop),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
                Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms)
            };

            foreach (string sDir in shortcutDirs)
            {
                if (!string.IsNullOrEmpty(sDir) && Directory.Exists(sDir))
                {
                    foreach (string sc in new[] { "Dofus Farm Manager.lnk", "Dofus Touch Farm.lnk", "DofusFarm.lnk" })
                    {
                        string p = Path.Combine(sDir, sc);
                        if (File.Exists(p)) try { File.Delete(p); } catch { }
                    }
                }
            }

            try { Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\DofusTouchFarm", false); } catch { }
            try { Registry.CurrentUser.DeleteSubKeyTree(@"Software\DofusFarm", false); } catch { }

            try
            {
                Environment.SetEnvironmentVariable("DOFUS_FARM_HOME", null, EnvironmentVariableTarget.User);
                string sdkRoot = Environment.GetEnvironmentVariable("ANDROID_SDK_ROOT", EnvironmentVariableTarget.User);
                if (!string.IsNullOrEmpty(sdkRoot) && (sdkRoot.IndexOf("DofusFarm", StringComparison.OrdinalIgnoreCase) >= 0 || (!string.IsNullOrEmpty(targetDir) && sdkRoot.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))))
                {
                    Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", null, EnvironmentVariableTarget.User);
                }
                string andHome = Environment.GetEnvironmentVariable("ANDROID_HOME", EnvironmentVariableTarget.User);
                if (!string.IsNullOrEmpty(andHome) && (andHome.IndexOf("DofusFarm", StringComparison.OrdinalIgnoreCase) >= 0 || (!string.IsNullOrEmpty(targetDir) && andHome.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))))
                {
                    Environment.SetEnvironmentVariable("ANDROID_HOME", null, EnvironmentVariableTarget.User);
                }
                string avdHomeEnv = Environment.GetEnvironmentVariable("ANDROID_AVD_HOME", EnvironmentVariableTarget.User);
                if (!string.IsNullOrEmpty(avdHomeEnv) && (avdHomeEnv.IndexOf("DofusFarm", StringComparison.OrdinalIgnoreCase) >= 0 || (!string.IsNullOrEmpty(targetDir) && avdHomeEnv.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))))
                {
                    Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", null, EnvironmentVariableTarget.User);
                }
            }
            catch { }

            if (onProgress != null) onProgress(85, "Removing application files and SDK directory...");
            if (!string.IsNullOrEmpty(targetDir) && Directory.Exists(targetDir))
            {
                DirectoryInfo di = new DirectoryInfo(targetDir);
                foreach (FileInfo fi in di.GetFiles())
                {
                    if (isForce || !fi.Name.Equals("setup.exe", StringComparison.OrdinalIgnoreCase))
                    {
                        try { fi.Attributes = FileAttributes.Normal; fi.Delete(); } catch { }
                    }
                }
                foreach (DirectoryInfo sub in di.GetDirectories())
                {
                    SafeDeleteDirectory(sub.FullName);
                }
                if (isForce || di.GetFiles().Length == 0)
                {
                    try { di.Delete(true); } catch { }
                }
            }

            if (noRespawn && exePath.StartsWith(tempDir, StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    ProcessStartInfo psiDel = new ProcessStartInfo
                    {
                        FileName = "cmd.exe",
                        Arguments = string.Format("/c ping 127.0.0.1 -n 2 > nul & del /f /q \"{0}\"", exePath),
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    Process.Start(psiDel);
                }
                catch { }
            }

            if (onProgress != null) onProgress(100, "Uninstallation complete!");
        }

        private static void KillProcesses(string[] names, string targetDir = null)
        {
            foreach (string name in names)
            {
                if (!string.IsNullOrEmpty(targetDir) && name.Equals("qemu-system-x86_64", StringComparison.OrdinalIgnoreCase))
                {
                    foreach (Process p in Process.GetProcessesByName(name))
                    {
                        try
                        {
                            using (p)
                            {
                                bool isOurs = true;
                                try
                                {
                                    if (p.MainModule != null && !p.MainModule.FileName.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase))
                                        isOurs = false;
                                }
                                catch { }
                                if (isOurs)
                                {
                                    p.Kill();
                                    p.WaitForExit(1000);
                                }
                            }
                        }
                        catch { }
                    }
                    continue;
                }

                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo
                    {
                        FileName = "taskkill.exe",
                        Arguments = "/F /T /IM " + name + ".exe",
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    using (Process p = Process.Start(psi)) { p.WaitForExit(1000); }
                }
                catch { }

                foreach (Process p in Process.GetProcessesByName(name))
                {
                    try
                    {
                        using (p)
                        {
                            p.Kill();
                            p.WaitForExit(1000);
                        }
                    }
                    catch { }
                }
            }
        }

        private static void SafeDeleteDirectory(string path)
        {
            if (!Directory.Exists(path)) return;
            string cleanPath = path.TrimEnd('\\');
            try
            {
                DirectoryInfo di = new DirectoryInfo(cleanPath);
                foreach (FileInfo fi in di.GetFiles("*", SearchOption.AllDirectories))
                {
                    try { fi.Attributes = FileAttributes.Normal; fi.Delete(); } catch { }
                }
                foreach (DirectoryInfo sub in di.GetDirectories("*", SearchOption.AllDirectories))
                {
                    try { sub.Attributes = FileAttributes.Normal; } catch { }
                }
                Directory.Delete(cleanPath, true);
            }
            catch { }

            if (Directory.Exists(cleanPath))
            {
                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo
                    {
                        FileName = "cmd.exe",
                        Arguments = "/c attrib -r -s -h /s /d \"" + cleanPath + "\\*\" & rd /s /q \"" + cleanPath + "\"",
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    using (Process p = Process.Start(psi)) { p.WaitForExit(10000); }
                }
                catch { }
            }

            if (Directory.Exists(cleanPath))
            {
                try { Directory.Delete(cleanPath, true); } catch { }
            }
        }
    }

    public class UninstallProgressBar : Control
    {
        private int _value = 0;
        public int Value
        {
            get { return _value; }
            set
            {
                int clamped = Math.Min(100, Math.Max(0, value));
                if (_value != clamped)
                {
                    _value = clamped;
                    this.Invalidate();
                }
            }
        }

        public UninstallProgressBar()
        {
            this.SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            this.Height = 16;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            if (this.Width <= 4 || this.Height <= 4) return;
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            Rectangle rect = new Rectangle(0, 0, this.Width - 1, this.Height - 1);
            using (SolidBrush bg = new SolidBrush(Color.FromArgb(26, 28, 36)))
            using (Pen border = new Pen(Color.FromArgb(45, 48, 62), 1f))
            {
                g.FillRectangle(bg, rect);
                g.DrawRectangle(border, rect);
            }

            if (_value > 0)
            {
                int fillW = Math.Min(this.Width - 2, Math.Max(4, (int)Math.Round(((this.Width - 2) * _value) / 100.0)));
                Rectangle fillRect = new Rectangle(1, 1, fillW, Math.Max(2, this.Height - 2));
                using (LinearGradientBrush grad = new LinearGradientBrush(
                    fillRect,
                    Color.FromArgb(220, 38, 38),
                    Color.FromArgb(239, 68, 68),
                    LinearGradientMode.Horizontal))
                {
                    g.FillRectangle(grad, fillRect);
                }
            }
        }
    }

    public class UninstallForm : Form
    {
        private readonly string _targetDir;
        private readonly string _exePath;
        private readonly string _tempDir;
        private readonly bool _noRespawn;
        private readonly bool _isForce;

        private Label _lblStatus;
        private UninstallProgressBar _progBar;
        private Button _btnUninstall;
        private Button _btnCancel;
        private bool _isRunning = false;

        public UninstallForm(string targetDir, string exePath, string tempDir, bool noRespawn, bool isForce)
        {
            _targetDir = targetDir;
            _exePath = exePath;
            _tempDir = tempDir;
            _noRespawn = noRespawn;
            _isForce = isForce;

            this.Text = "Dofus Touch Farm — Uninstall";
            this.Size = new Size(500, 250);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.BackColor = Color.FromArgb(18, 19, 24);
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f);

            Panel header = new Panel
            {
                Dock = DockStyle.Top,
                Height = 60,
                BackColor = Color.FromArgb(24, 26, 34)
            };
            Label title = new Label
            {
                Text = "UNINSTALL DOFUS TOUCH FARM",
                Font = new Font("Segoe UI", 11.5f, FontStyle.Bold),
                ForeColor = Color.White,
                AutoSize = true,
                Location = new Point(22, 18)
            };
            header.Controls.Add(title);

            _lblStatus = new Label
            {
                Text = "Remove Dofus Touch Farm, all virtual disks, snapshots, and shortcuts?",
                Location = new Point(22, 80),
                Size = new Size(440, 38),
                ForeColor = Color.FromArgb(203, 213, 225)
            };

            _progBar = new UninstallProgressBar
            {
                Location = new Point(22, 122),
                Size = new Size(440, 16),
                Value = 0
            };

            _btnUninstall = new Button
            {
                Text = "Uninstall",
                Location = new Point(242, 158),
                Size = new Size(120, 36),
                BackColor = Color.FromArgb(185, 28, 28),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _btnUninstall.FlatAppearance.BorderSize = 0;
            _btnUninstall.Click += OnUninstallClick;

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(372, 158),
                Size = new Size(90, 36),
                BackColor = Color.FromArgb(40, 43, 56),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand
            };
            _btnCancel.FlatAppearance.BorderSize = 0;
            _btnCancel.Click += delegate { if (!_isRunning) this.Close(); };

            this.Controls.Add(header);
            this.Controls.Add(_lblStatus);
            this.Controls.Add(_progBar);
            this.Controls.Add(_btnUninstall);
            this.Controls.Add(_btnCancel);
        }

        private void OnUninstallClick(object sender, EventArgs e)
        {
            if (_isRunning) return;
            _isRunning = true;
            _btnUninstall.Enabled = false;
            _btnCancel.Enabled = false;
            _btnUninstall.Text = "Removing...";

            ThreadPool.QueueUserWorkItem(delegate
            {
                Program.RunUninstallSteps(_targetDir, _exePath, _tempDir, _noRespawn, _isForce, delegate (int pct, string msg)
                {
                    if (this.IsDisposed || !this.IsHandleCreated) return;
                    try
                    {
                        this.BeginInvoke(new Action(delegate
                        {
                            _progBar.Value = pct;
                            _lblStatus.Text = msg;
                        }));
                    }
                    catch { }
                });

                Thread.Sleep(500);
                if (!this.IsDisposed && this.IsHandleCreated)
                {
                    try
                    {
                        this.Invoke(new Action(delegate
                        {
                            MessageBox.Show("Dofus Touch Farm has been completely uninstalled.", "Uninstall Complete", MessageBoxButtons.OK, MessageBoxIcon.Information);
                            this.Close();
                        }));
                    }
                    catch { }
                }
            });
        }
    }
}
