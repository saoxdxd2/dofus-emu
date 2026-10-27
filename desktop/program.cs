using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using DofusFarm.Native;
using DofusFarm.UI;

[assembly: AssemblyTitle("Dofus Farm Manager")]
[assembly: AssemblyDescription("Dofus Touch High-Performance Native Virtualization Manager")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Manager")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("2.4.0.0")]
[assembly: AssemblyVersion("2.4.0.0")]

namespace DofusFarm
{
    static class Program
    {
        private static Mutex _singleInstanceMutex = null;

        private static void SafeLogFatal(string prefix, string msg)
        {
            string line = string.Format("[{0:yyyy-MM-dd HH:mm:ss}] {1}: {2}\r\n", DateTime.Now, prefix, msg);
            try
            {
                string baseLog = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "DofusFarm.log");
                File.AppendAllText(baseLog, line);
                return;
            }
            catch { }

            try
            {
                string fallbackDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
                if (!Directory.Exists(fallbackDir)) Directory.CreateDirectory(fallbackDir);
                File.AppendAllText(Path.Combine(fallbackDir, "DofusFarm.log"), line);
            }
            catch { }
        }

        private static void FocusExistingInstance()
        {
            try
            {
                int currentId = Process.GetCurrentProcess().Id;
                Process[] procs = Process.GetProcessesByName("DofusFarm");
                for (int i = 0; i < procs.Length; i++)
                {
                    using (Process p = procs[i])
                    {
                        if (p.Id != currentId && p.MainWindowHandle != IntPtr.Zero)
                        {
                            Win32.ShowWindow(p.MainWindowHandle, Win32.SW_RESTORE);
                            Win32.SetForegroundWindow(p.MainWindowHandle);
                            break;
                        }
                    }
                }
            }
            catch { }
        }

        /// <summary>
        /// Headless check of the runtime bundle patcher: apply tools\patch-rules.txt
        /// to the unpatched script.js.bak and compare with the live script.js that
        /// the build produced. If they differ, the rules are not what the build
        /// applied, and an update would ship a bundle nobody verified.
        ///
        ///   DofusFarm.exe --bundle-selftest   (exit 0 = identical)
        ///
        /// Writes its result to bundle-selftest.txt because this is a winexe.
        /// </summary>
        private static int BundleSelfTest()
        {
            var sb = new System.Text.StringBuilder();
            try
            {
                string dir = Engine.ClientBundle.BundleDir();
                if (dir == null) { sb.AppendLine("FAIL: client/www not found"); return Finish(sb, 2); }

                string error;
                var rules = Engine.ClientBundle.LoadRules(out error);
                sb.AppendLine("rules: " + rules.Count + (error != null ? " (" + error + ")" : ""));
                if (rules.Count == 0) return Finish(sb, 2);

                string live = Path.Combine(dir, @"build\script.js");
                string bak = Path.Combine(dir, @"build\script.js.bak");
                if (!File.Exists(live) || !File.Exists(bak))
                {
                    sb.AppendLine("SKIP: need both script.js and script.js.bak");
                    return Finish(sb, 2);
                }

                string js = File.ReadAllText(bak, System.Text.Encoding.UTF8);
                System.Collections.Generic.List<string> matched, skipped;
                js = Engine.ClientBundle.ApplyPatches(js, rules, out matched, out skipped);
                sb.AppendLine("matched: " + string.Join(", ", matched.ToArray()));
                foreach (string s2 in skipped) sb.AppendLine("skipped: " + s2);

                string patched = File.ReadAllText(live, System.Text.Encoding.UTF8);
                bool same = string.Equals(js, patched, StringComparison.Ordinal);
                sb.AppendLine("patched " + js.Length + " chars, live " + patched.Length + " chars");
                sb.AppendLine(same ? "RESULT: IDENTICAL" : "RESULT: DIFFERENT");
                return Finish(sb, same ? 0 : 1);
            }
            catch (Exception ex)
            {
                sb.AppendLine("ERROR: " + ex);
                return Finish(sb, 3);
            }
        }

        private static int Finish(System.Text.StringBuilder sb, int code)
        {
            return Finish(sb, code, "bundle-selftest.txt");
        }

        private static int Finish(System.Text.StringBuilder sb, int code, string fileName)
        {
            try
            {
                File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, fileName), sb.ToString());
            }
            catch { }
            return code;
        }

        /// <summary>
        /// Headless check of the live AI pill: read the last step the loop wrote and
        /// format it exactly as the nav strip does. Writes ai-status-selftest.txt.
        ///
        ///   DofusFarm.exe --ai-status-selftest [--running]
        /// </summary>
        private static int AiStatusSelfTest(bool running, string instance, bool askedForInstance)
        {
            var sb = new System.Text.StringBuilder();
            try
            {
                string log = UI.AiPanel.StepLogFor(instance);
                sb.AppendLine("instance: " + (instance == null ? "(farm-wide)" : instance));
                sb.AppendLine("log     : " + log + (File.Exists(log) ? "" : " (absent)"));
                string line = UI.AiPanel.ReadLastStepLine(log);
                sb.AppendLine("last step line: " + (line == null ? "(none)" : line.Substring(0, Math.Min(160, line.Length))));
                sb.AppendLine("idle form  : " + UI.AiPanel.FormatStatus(false, line));
                sb.AppendLine("running form: " + UI.AiPanel.FormatStatus(true, line));
                sb.AppendLine("no log     : " + UI.AiPanel.FormatStatus(false, null));
                sb.AppendLine("first step : " + UI.AiPanel.FormatStatus(true, null));
                // A step row was found but the formatter could not name it: that is
                // the bug the pill showed as "waiting for the first step".
                bool parsed = line != null && line.IndexOf("\"index\"") >= 0;
                bool wrongShape = parsed && !UI.AiPanel.FormatStatus(true, line).Contains("step");
                // Asking about an instance and silently reading the farm-wide log is
                // the failure this selftest exists to catch, so it must not pass.
                bool fellBack = askedForInstance && !log.EndsWith(instance + ".jsonl");
                sb.AppendLine((wrongShape || fellBack)
                    ? "RESULT: WRONG (" + (wrongShape ? "a step row formatted as no step" : "")
                      + (wrongShape && fellBack ? ", " : "")
                      + (fellBack ? "asked for " + instance + " but read the farm-wide log" : "") + ")"
                    : "RESULT: ok");
                // Exit 0 even with no log: "no loop has run yet" is a valid state,
                // not a failure of the formatter.
                return Finish(sb, 0, "ai-status-selftest.txt");
            }
            catch (Exception ex)
            {
                sb.AppendLine("ERROR: " + ex);
                return Finish(sb, 3, "ai-status-selftest.txt");
            }
        }

        // This attribute must sit directly on the entry point. It was left attached
        // to a helper method inserted above Main, so the process started in MTA and
        // every WebView2 init failed with RPC_E_CHANGED_MODE (0x80010106).
        [STAThread]
        static void Main(string[] args)
        {
            if (args != null && args.Length > 0 &&
                string.Equals(args[0], "--bundle-selftest", StringComparison.OrdinalIgnoreCase))
            {
                Environment.Exit(BundleSelfTest());
                return;
            }
            if (args != null && args.Length > 0 &&
                string.Equals(args[0], "--ai-status-selftest", StringComparison.OrdinalIgnoreCase))
            {
                bool running = Array.IndexOf(args, "--running") >= 0;
                // A free argument names an instance, and with several loops the
                // farm-wide log is no longer the interesting one: each client now
                // writes autopilot.<instance>.jsonl. Scan for it -- "--running"
                // can sit in front of it, and an index here once read args[1] and
                // reported the farm-wide log for every instance asked about.
                string instance = null;
                if (args != null)
                    for (int i = 1; i < args.Length; i++)
                        if (!args[i].StartsWith("--")) { instance = args[i]; break; }
                bool askedForInstance = instance != null;
                Environment.Exit(AiStatusSelfTest(running, instance, askedForInstance));
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);

            bool createdNew;
            _singleInstanceMutex = new Mutex(true, @"Global\DofusFarmManager_SingleInstance_Mutex_2026", out createdNew);
            if (!createdNew)
            {
                FocusExistingInstance();
                return;
            }

            // Rotate an oversized log once at startup (single writer is
            // guaranteed here by the instance mutex). Keeps DofusFarm.log from
            // growing unbounded across sessions; a locked .1 file just skips
            // rotation for this launch.
            try
            {
                string logPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "DofusFarm.log");
                var logInfo = new FileInfo(logPath);
                if (logInfo.Exists && logInfo.Length > 10 * 1024 * 1024)
                {
                    string prevPath = logPath + ".1";
                    if (File.Exists(prevPath)) File.Delete(prevPath);
                    File.Move(logPath, prevPath);
                }
            }
            catch { }

            try
            {
                AppDomain.CurrentDomain.UnhandledException += delegate (object sender, UnhandledExceptionEventArgs e)
                {
                    Exception ex = e.ExceptionObject as Exception;
                    string msg = ex != null ? ex.ToString() : "Unknown fatal error";
                    SafeLogFatal("FATAL", msg);
                    MessageBox.Show("Fatal Error: " + (ex != null ? ex.Message : "Unknown"), "Dofus Farm Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                };

                Application.ThreadException += delegate (object sender, ThreadExceptionEventArgs e)
                {
                    string msg = e.Exception != null ? e.Exception.ToString() : "Unknown UI error";
                    SafeLogFatal("UI_ERROR", msg);
                    string exMsg = e.Exception != null ? e.Exception.Message : "Unknown error";
                    MessageBox.Show("UI Error: " + exMsg, "Dofus Farm Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                };

                // The UI thread is the only one that may host WebView2. Capture its
                // SynchronizationContext before the message loop starts, so every
                // later dispatch (client windows, dialogs) lands there by construction
                // instead of by inspecting Application.OpenForms.
                UI.UiThread.Capture();
                SafeLogFatal("START", "UI thread apartment: " + UI.UiThread.Apartment()
                    + " (WebView2 requires STA)");
                var mainForm = new MainForm();
                UI.UiThread.SetMainForm(mainForm);
                Application.Run(mainForm);
            }
            finally
            {
                if (_singleInstanceMutex != null)
                {
                    try { _singleInstanceMutex.ReleaseMutex(); } catch { }
                    try { _singleInstanceMutex.Dispose(); } catch { }
                    _singleInstanceMutex = null;
                }
            }
        }
    }
}
