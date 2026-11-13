using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Reflection;
using System.Text;
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
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }

            try
            {
                string fallbackDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
                if (!Directory.Exists(fallbackDir)) Directory.CreateDirectory(fallbackDir);
                File.AppendAllText(Path.Combine(fallbackDir, "DofusFarm.log"), line);
            }
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
        }

        /// <summary>The instance named by `--launch <name>`, or null.</summary>
        [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
        private static extern bool MoveFileEx(string existing, string replacement, int flags);

        private static string LaunchNameFromArgs(string[] args)
        {
            if (args == null) return null;
            int at = Array.IndexOf(args, "--launch");
            if (at >= 0 && at + 1 < args.Length && !args[at + 1].StartsWith("--"))
                return args[at + 1];
            return null;
        }

        private static string ResetNameFromArgs(string[] args)
        {
            if (args == null) return null;
            int at = Array.IndexOf(args, "--reset");
            if (at >= 0 && at + 1 < args.Length && !args[at + 1].StartsWith("--"))
                return args[at + 1];
            return null;
        }

        // A second `DofusFarm.exe --launch dofus-03` used to focus the running window
        // and drop the request: the flag is only read on the path that owns the
        // mutex. A drop file hands the name over instead, so the CLI means the same
        // thing whether or not the manager is already open.
        private static void WriteLaunchRequest(string name)
        {
            try
            {
                string dir = Path.Combine(Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
                Directory.CreateDirectory(dir);
                string path = Path.Combine(dir, "launch_request.txt");
                string temp = path + ".tmp";
                File.WriteAllText(temp, name);
                MoveFileEx(temp, path, 0x1);   // MOVEFILE_REPLACE_EXISTING
            }
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
        }

        /// <summary>Hand an OAuth callback to the running app, through its own channel.
        ///
        /// The `dofustouch://` protocol handler starts THIS exe with the URL as argv, and
        /// the single-instance mutex would then drop it on the floor -- the browser's
        /// redirect would arrive nowhere. A second file, not `launch_request.txt`: that
        /// one carries a launch name and is parsed as one, and a URL is not a name. The
        /// running app polls both (MainForm.StartLaunchRequestPoller).
        /// </summary>
        private static void WriteDeepLinkRequest(string url)
        {
            try
            {
                string dir = Path.Combine(Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
                Directory.CreateDirectory(dir);
                string path = Path.Combine(dir, "oauth_deeplink.txt");
                string temp = path + ".tmp";
                File.WriteAllText(temp, url);
                MoveFileEx(temp, path, 0x1);   // MOVEFILE_REPLACE_EXISTING
            }
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
        }

        private static string DeepLinkFromArgs(string[] args)
        {
            if (args == null) return null;
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a != null && a.StartsWith("dofustouch://", StringComparison.OrdinalIgnoreCase))
                    return a;
            }
            return null;
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
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
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
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
            return code;
        }

        /// <summary>
        /// Prove the OS half of the system-browser sign-in, without a Google account.
        ///
        ///   DofusFarm.exe --oauth-selftest      (exit 0 = every piece in place)
        ///
        /// Three things can break here and none of them needs a browser to notice: the
        /// protocol key Windows would refuse to start, the command it points at (a stale
        /// path after the app moves), and the file the second process writes for the
        /// running app to poll. The Google round trip itself is NOT tested here -- that
        /// needs a real account and is named as unproven in G119. Writes
        /// oauth-selftest.txt.
        /// </summary>
        private static int OAuthSelfTest()
        {
            var sb = new System.Text.StringBuilder();
            int failures = 0;
            Action<bool, string> check = delegate(bool ok, string what)
            {
                if (!ok) failures++;
                sb.AppendLine((ok ? "OK   " : "FAIL ") + what);
            };
            try
            {
                string exe = Application.ExecutablePath;
                sb.AppendLine("exe: " + exe);

                bool registered = Engine.OAuthBrowserHandoff.EnsureProtocolHandler(
                    delegate(string line) { sb.AppendLine("     " + line); });
                check(registered, "the dofustouch:// handler is registered");

                string value = null, urlProtocol = null, command = null;
                try
                {
                    using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                               @"Software\Classes\dofustouch"))
                    {
                        if (key != null)
                        {
                            value = key.GetValue("") as string;
                            urlProtocol = key.GetValue("URL Protocol") as string;
                            using (var cmd = key.OpenSubKey(@"shell\open\command"))
                                if (cmd != null) command = cmd.GetValue("") as string;
                        }
                    }
                }
                catch (Exception ex) { sb.AppendLine("     read-back failed: " + ex.Message); }

                sb.AppendLine("     default     : " + (value ?? "(absent)"));
                sb.AppendLine("     URL Protocol: " + (urlProtocol == null ? "(absent)" : "(set)"));
                sb.AppendLine("     command     : " + (command ?? "(absent)"));
                check(urlProtocol != null, "URL Protocol is present (Windows refuses the scheme without it)");
                check(!string.IsNullOrEmpty(command)
                      && command.IndexOf(exe, StringComparison.OrdinalIgnoreCase) >= 0,
                      "the handler points at THIS binary");

                // The path inside the command has to exist: a moved or deleted app leaves
                // a handler that opens nothing, and Windows shows no error at all.
                string quoted = command ?? "";
                int first = quoted.IndexOf('"');
                int second = first >= 0 ? quoted.IndexOf('"', first + 1) : -1;
                string target = (first >= 0 && second > first)
                    ? quoted.Substring(first + 1, second - first - 1) : null;
                check(target != null && File.Exists(target),
                      "the binary the handler names exists" + (target == null ? "" : " (" + target + ")"));

                check(DeepLinkFromArgs(new string[] { "--launch", "dofus-02",
                          "dofustouch://authorized?code=ABC" }) == "dofustouch://authorized?code=ABC",
                      "a dofustouch:// URL is found in argv");
                check(DeepLinkFromArgs(new string[] { "--launch", "dofus-02" }) == null,
                      "a launch name is not mistaken for a callback");

                // The hand-off file, both directions. The running app deletes it when it
                // reads it, so this leaves nothing behind.
                string dir = Path.Combine(Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData), "DofusFarm");
                string file = Path.Combine(dir, "oauth_deeplink.txt");
                string probe = "dofustouch://authorized?code=SELFTEST-" + Guid.NewGuid().ToString("N");
                WriteDeepLinkRequest(probe);
                string read = File.Exists(file) ? File.ReadAllText(file).Trim() : null;
                check(read == probe, "the callback file round-trips unchanged");
                try { if (File.Exists(file)) File.Delete(file); } catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
            }
            catch (Exception ex)
            {
                sb.AppendLine("ERROR: " + ex);
                failures++;
            }
            sb.AppendLine(failures == 0 ? "RESULT: OK" : "RESULT: " + failures + " FAILED");
            return Finish(sb, failures == 0 ? 0 : 1, "oauth-selftest.txt");
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

        private static int RunSoakSlot(string[] args)
        {
            string slot = args.Length > 1 ? args[1] : "dofus-01";
            int port = 8081;
            if (args.Length > 2) int.TryParse(args[2], out port);
            int durationSec = 30;
            if (args.Length > 3) int.TryParse(args[3], out durationSec);
            string outJson = args.Length > 4 ? args[4] : ("soak_" + slot + ".json");

            var samples = new System.Collections.Generic.List<string>();
            var proc = Process.GetCurrentProcess();
            IntPtr hProcess = proc.Handle;

            UiDispatcher.Instance.Start();

            var cts = new CancellationTokenSource();
            var sw = Stopwatch.StartNew();

            var timer = new System.Windows.Forms.Timer();
            timer.Interval = 1000;
            timer.Tick += delegate
            {
                uint handleCount = 0;
                Win32.GetProcessHandleCount(hProcess, out handleCount);
                int gdi = Win32.GetGuiResources(hProcess, 0);
                int user = Win32.GetGuiResources(hProcess, 1);
                proc.Refresh();
                double pwsMb = proc.PrivateMemorySize64 / (1024.0 * 1024.0);
                double wsMb = proc.WorkingSet64 / (1024.0 * 1024.0);
                long dropped = UiDispatcher.Instance.Dropped;
                long pending = UiDispatcher.Instance.Pending;
                double elapsed = sw.Elapsed.TotalSeconds;

                string sample = string.Format(
                    System.Globalization.CultureInfo.InvariantCulture,
                    "{{\"elapsed\":{0:F2},\"private_mb\":{1:F2},\"working_set_mb\":{2:F2},\"gdi\":{3},\"user\":{4},\"handles\":{5},\"dropped\":{6},\"pending\":{7}}}",
                    elapsed, pwsMb, wsMb, gdi, user, handleCount, dropped, pending);
                samples.Add(sample);

                UiDispatcher.Instance.Post("slot-" + slot, delegate { });
                UiDispatcher.Instance.Log(msg => { }, string.Format("[{0}] soak step at {1:F1}s", slot, elapsed));

                if (sw.Elapsed.TotalSeconds >= durationSec)
                {
                    timer.Stop();
                    Application.ExitThread();
                }
            };

            ThreadPool.QueueUserWorkItem(delegate
            {
                while (!cts.IsCancellationRequested && sw.Elapsed.TotalSeconds < durationSec)
                {
                    try
                    {
                        var req = (HttpWebRequest)WebRequest.Create(string.Format("http://127.0.0.1:{0}/__health", port));
                        req.Timeout = 1000;
                        using (var resp = req.GetResponse()) { }
                    }
                    catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
                    Thread.Sleep(250);
                }
            });

            timer.Start();
            Application.Run();
            cts.Cancel();

            string json = "{\"slot\":\"" + slot + "\",\"duration\":" + durationSec + ",\"samples\":[" + string.Join(",", samples.ToArray()) + "]}";
            try
            {
                if (!string.IsNullOrEmpty(outJson) && outJson != "-")
                {
                    string target = Path.GetFullPath(outJson);
                    string dir = Path.GetDirectoryName(target);
                    if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                        Directory.CreateDirectory(dir);
                    File.WriteAllText(target, json, new UTF8Encoding(false));
                }
                else
                    Console.WriteLine(json);
            }
            catch (Exception ex)
            {
                Debug.WriteLine("[WARN] RunSoakSlot save: " + ex.Message);
            }
            return 0;
        }

        // This attribute must sit directly on the entry point. It was left attached
        // to a helper method inserted above Main, so the process started in MTA and
        // every WebView2 init failed with RPC_E_CHANGED_MODE (0x80010106).
        [STAThread]
        static void Main(string[] args)
        {
            if (args != null && args.Length > 0 &&
                string.Equals(args[0], "--soak-slot", StringComparison.OrdinalIgnoreCase))
            {
                Environment.Exit(RunSoakSlot(args));
                return;
            }
            if (args != null && args.Length > 0 &&
                string.Equals(args[0], "--bundle-selftest", StringComparison.OrdinalIgnoreCase))
            {
                Environment.Exit(BundleSelfTest());
                return;
            }
            if (args != null && args.Length > 0 &&
                string.Equals(args[0], "--oauth-selftest", StringComparison.OrdinalIgnoreCase))
            {
                Environment.Exit(OAuthSelfTest());
                return;
            }
            if (args != null && args.Length > 0 &&
                string.Equals(args[0], "--ai-status-selftest", StringComparison.OrdinalIgnoreCase))
            {                bool running = Array.IndexOf(args, "--running") >= 0;
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

            // A `dofustouch://` URL in argv is the OAuth callback coming back from the
            // system browser (the protocol handler launches this exe). It is forwarded
            // before the mutex decides anything, so it works whether or not the manager
            // is already running: the running app polls the file, and a first instance
            // picks it up from the same poller a second later.
            string deepLink = DeepLinkFromArgs(args);
            if (!string.IsNullOrEmpty(deepLink)) WriteDeepLinkRequest(deepLink);

            bool createdNew;
            _singleInstanceMutex = new Mutex(true, @"Global\DofusFarmManager_SingleInstance_Mutex_2026", out createdNew);
            if (!createdNew)
            {
                string forwarded = null;
                try { forwarded = LaunchNameFromArgs(args); } catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
                string resetName = null;
                try { resetName = ResetNameFromArgs(args); } catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
                if (!string.IsNullOrEmpty(resetName)) forwarded = "reset:" + resetName;
                if (!string.IsNullOrEmpty(forwarded)) WriteLaunchRequest(forwarded);
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
            catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }

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
                // Headless fleet hook: `--launch <instance>` opens one client through
                // the same path the Launch button uses (including the AI autostart).
                // Unattended runs and the tooling need this; before it, starting a
                // client required a human click on an owner-drawn grid cell.
                string launchName = LaunchNameFromArgs(args);
                if (launchName != null)
                {
                    string wanted = launchName;
                    mainForm.Shown += delegate
                    {
                        try
                        {
                            mainForm.BeginInvoke((Action)(delegate
                            {
                                mainForm.LaunchInstanceFromCommandLine(wanted);
                            }));
                        }
                        catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
                    };
                }
                Application.Run(mainForm);
                // Hand the last frame to the controls before the handles die: a status
                // line queued in the final second is otherwise lost with the process.
                UI.UiDispatcher.Instance.Stop();
            }
            finally
            {
                if (_singleInstanceMutex != null)
                {
                    try { _singleInstanceMutex.ReleaseMutex(); } catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
                    try { _singleInstanceMutex.Dispose(); } catch (Exception ex) { Debug.WriteLine("[WARN] program: " + ex.Message); }
                    _singleInstanceMutex = null;
                }
            }
        }
    }
}


