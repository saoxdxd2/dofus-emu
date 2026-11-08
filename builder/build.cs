using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;

namespace DofusBuild
{
    class Program
    {
        // Compiler resolution - no hardcoded tool paths:
        //   1. Preferred: modern Roslyn csc.dll from any installed .NET SDK
        //      (dotnet.exe found on PATH or %ProgramFiles%\dotnet; the "sdk"
        //      folder is scanned by wildcard and the newest version wins).
        //      Required because the desktop sources use C# 6+ syntax.
        //   2. Fallback: legacy .NET Framework 4.x csc.exe (C# 5 only).
        static string DotNetHost;   // dotnet.exe when Roslyn is used, else null
        static string CscDll;       // Roslyn csc.dll when used, else null
        static string Csc;           // legacy csc.exe (FrameworkDir\csc.exe)
        static string FrameworkDir;  // folder holding mscorlib.dll, System.dll, ...
        static string RepoRoot;

        static int Main(string[] args)
        {
            Console.Title = "Dofus Farm Native Builder";
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("==========================================================");
            Console.WriteLine("   DOFUS TOUCH FARM NATIVE C# COMPILER & PACKAGER        ");
            Console.WriteLine("==========================================================");
            Console.ResetColor();

            ResolveCompiler();
            if (DotNetHost == null && (Csc == null || !File.Exists(Csc)))
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("[ERROR] No C# compiler found (no .NET SDK Roslyn csc.dll, no .NET Framework csc.exe).");
                Console.ResetColor();
                return 1;
            }
            Console.ForegroundColor = ConsoleColor.DarkGray;
            if (DotNetHost != null) Console.WriteLine("Compiler: Roslyn csc.dll ({0})", CscDll);
            else Console.WriteLine("Compiler: legacy Framework csc.exe (C# 5 only - install a .NET SDK for modern syntax)");
            Console.ResetColor();

            RepoRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            if (!File.Exists(Path.Combine(RepoRoot, "builder", "Build.cs")) &&
                Directory.GetParent(RepoRoot) != null &&
                File.Exists(Path.Combine(Directory.GetParent(RepoRoot).FullName, "builder", "Build.cs")))
            {
                RepoRoot = Directory.GetParent(RepoRoot).FullName;
            }

            string desktopDir = Path.Combine(RepoRoot, "desktop");
            string ico = Path.Combine(RepoRoot, "app_icon.ico");
            string setupIco = Path.Combine(RepoRoot, "installer_icon.ico");
            string outFarm = Path.Combine(RepoRoot, "DofusFarm.exe");
            string outUninstall = Path.Combine(RepoRoot, "uninstall.exe");
            string outSetup = Path.Combine(RepoRoot, "setup.exe");

            PreBuildClean(RepoRoot);

            // 0. Compile Go Network Proxy if go.exe is available
            TryBuildGoProxy();

            // 0b. Apply client bundle patches (anti-detection, telemetry silencing, and engine stability)
            TryApplyClientPatches();

            // 0c. Synchronize client runtime shims across desktop and root distributions
            TrySyncClientShims();

            // 1. Compile DofusFarm.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[1/3] Compiling native C# DofusFarm.exe...");
            Console.ResetColor();
            var farmSources = new List<string>(12)
            {
                Path.Combine(desktopDir, "Win32.cs"),
                Path.Combine(desktopDir, "Hardware.cs"),
                Path.Combine(desktopDir, "DynamicVersionResolver.cs"),
                Path.Combine(desktopDir, "IClientBackend.cs"),
                Path.Combine(desktopDir, "LocalSecret.cs"),
                Path.Combine(desktopDir, "EmulatorBackend.cs"),
                Path.Combine(desktopDir, "WebView2ClientForm.cs"),
                Path.Combine(desktopDir, "ClientBundle.cs"),
                Path.Combine(desktopDir, "UiThread.cs"),
                Path.Combine(desktopDir, "WebView2Backend.cs"),
                Path.Combine(desktopDir, "AgentBridge.cs"),
                Path.Combine(desktopDir, "FarmEngine.cs"),
                Path.Combine(desktopDir, "ProxySupervisor.cs"),
                Path.Combine(desktopDir, "Watchdog.cs"),
                Path.Combine(desktopDir, "PriorityGovernor.cs"),
                Path.Combine(desktopDir, "AiPanel.cs"),
                Path.Combine(desktopDir, "NetSettings.cs"),
                Path.Combine(desktopDir, "NetPill.cs"),
                Path.Combine(desktopDir, "BootState.cs"),
                Path.Combine(desktopDir, "RuntimeSettings.cs"),
                Path.Combine(desktopDir, "Ini.cs"),
                Path.Combine(desktopDir, "PythonLocator.cs"),
                Path.Combine(desktopDir, "Pages.cs"),
                Path.Combine(desktopDir, "MainForm.cs"),
                Path.Combine(desktopDir, "Program.cs")
            };
            // NOTE: kept C#5-compatible (no string interpolation) because this builder is
            // compiled with the .NET Framework 4.x csc.exe, which supports at most C# 5.
            string iconArg = File.Exists(ico) ? string.Format("\"/win32icon:{0}\" ", ico) : "";
            string farmArgs = string.Format(
                "/nologo /target:winexe /optimize+ {0}\"/out:{1}\" {2}\"{3}\"",
                iconArg, outFarm, RefArgs(), string.Join("\" \"", farmSources));

            if (RunCsc(farmArgs) != 0)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("Failed to compile DofusFarm.exe");
                Console.ResetColor();
                return 1;
            }
            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("  [OK] DofusFarm.exe compiled ({0:N0} bytes)", new FileInfo(outFarm).Length);
            Console.ResetColor();

            // 2. Compile uninstall.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[2/3] Compiling native C# uninstall.exe...");
            Console.ResetColor();
            string uninstallerCs = Path.Combine(desktopDir, "Uninstaller.cs");
            string progressCs = Path.Combine(desktopDir, "ProgressBar.cs");
            string setupIconArg = File.Exists(setupIco) ? string.Format("\"/win32icon:{0}\" ", setupIco) : "";
            string uninstArgs = string.Format("/nologo /target:winexe /optimize+ {0}\"/out:{1}\" {2}\"{3}\" \"{4}\"",
                setupIconArg, outUninstall, RefArgs(), uninstallerCs, progressCs);

            if (RunCsc(uninstArgs) != 0)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("Failed to compile uninstall.exe");
                Console.ResetColor();
                return 1;
            }
            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("  [OK] uninstall.exe compiled ({0:N0} bytes)", new FileInfo(outUninstall).Length);
            Console.ResetColor();

            // 3. Compile standalone setup.exe (by default standalone WebView2; legacy emulator excluded)
            bool includeLegacyEmulator = false;
            if (args != null)
            {
                foreach (string a in args)
                {
                    if (a.Equals("--include-legacy-emulator", StringComparison.OrdinalIgnoreCase) ||
                        a.Equals("-LegacyEmulator", StringComparison.OrdinalIgnoreCase))
                    {
                        includeLegacyEmulator = true;
                    }
                }
            }
            if (Environment.GetEnvironmentVariable("INCLUDE_LEGACY_EMULATOR") == "1")
            {
                includeLegacyEmulator = true;
            }

            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[3/3] Packaging release payload and compiling standalone setup.exe...");
            Console.ResetColor();
            if (!BuildStandaloneSetup(outSetup, setupIco, desktopDir, includeLegacyEmulator))
            {
                return 1;
            }

            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("\n==========================================================");
            Console.WriteLine("   BUILD COMPLETE: STANDALONE RELEASE SUITE READY!        ");
            Console.WriteLine("==========================================================");
            Console.ResetColor();
            return 0;
        }

        static void TryBuildGoProxy()
        {
            string proxySrc = Path.Combine(RepoRoot, @"scripts\dofus-net-proxy.go");
            if (!File.Exists(proxySrc)) return;

            // Locate go.exe without hardcoded install paths:
            //   1. PATH (via where-like scan)
            //   2. GOROOT\bin (set by the Go installer)
            //   3. %ProgramFiles%\Go\bin and GOPATH\bin (standard install locations)
            string goExe = FindOnPath("go.exe");
            if (goExe == null)
            {
                List<string> goEnvCandidates = new List<string>();
                string goroot = Environment.GetEnvironmentVariable("GOROOT");
                if (!string.IsNullOrEmpty(goroot)) goEnvCandidates.Add(Path.Combine(goroot, "bin"));
                string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
                if (!string.IsNullOrEmpty(pf)) goEnvCandidates.Add(Path.Combine(pf, "Go", "bin"));
                string gopath = Environment.GetEnvironmentVariable("GOPATH");
                if (!string.IsNullOrEmpty(gopath)) goEnvCandidates.Add(Path.Combine(gopath, "bin"));
                foreach (string dir in goEnvCandidates)
                {
                    try
                    {
                        string p = Path.Combine(dir, "go.exe");
                        if (File.Exists(p)) { goExe = p; break; }
                    }
                    catch { }
                }
            }

            if (!string.IsNullOrEmpty(goExe))
            {
                string proxyOut = Path.Combine(RepoRoot, "dofus-net-proxy.exe");
                Run(goExe, string.Format("build -ldflags=\"-s -w\" -o \"{0}\" \"{1}\"", proxyOut, proxySrc), RepoRoot);
            }
        }

        static void TryApplyClientPatches()
        {
            string patchScript = Path.Combine(RepoRoot, @"tools\patch-engine.js");
            if (!File.Exists(patchScript)) return;

            string nodeExe = FindOnPath("node.exe");
            if (nodeExe == null)
            {
                string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
                if (!string.IsNullOrEmpty(pf))
                {
                    string p = Path.Combine(pf, "nodejs", "node.exe");
                    if (File.Exists(p)) nodeExe = p;
                }
            }

            if (!string.IsNullOrEmpty(nodeExe))
            {
                Console.ForegroundColor = ConsoleColor.Cyan;
                Console.WriteLine("\n[0b/3] Applying client bundle anti-detection & engine patches (patch-engine.js)...");
                Console.ResetColor();
                int code = Run(nodeExe, string.Format("\"{0}\"", patchScript), RepoRoot);
                if (code != 0)
                {
                    Console.ForegroundColor = ConsoleColor.Yellow;
                    Console.WriteLine("  [WARN] Client patch-engine returned exit code {0}.", code);
                    Console.ResetColor();
                }
                ReportPatchVerdict();
            }
            else
            {
                Console.ForegroundColor = ConsoleColor.DarkGray;
                Console.WriteLine("  [INFO] node.exe not found on PATH; skipping bundle repatch (using existing script.js).");
                Console.ResetColor();
                ReportPatchVerdict();
            }
        }

        // This step used to end with a hard-coded "Client bundle patched successfully (11 patches
        // active)" whenever patch-engine exited 0 -- and Run() swallows that tool's own stdout on
        // success, so its "[SKIP]" lines never reached the log either. The line stayed true-looking
        // through the whole 3.3.11 incident: the rules matched, the patched bundle double-fired a
        // series callback, the client black-screened, and the build reported success. A build
        // message that cannot be wrong cannot be right either.
        //
        // So the verdict is measured from the artifact the app will actually serve, against the
        // state the tree says it is in. `client-patches.quarantine` present + patched bundle means
        // something wrote past the flag, and packaging it ships the black screen: that is fatal.
        static void ReportPatchVerdict()
        {
            string scriptPath = Path.Combine(RepoRoot, @"desktop\client\www\build\script.js");
            bool quarantined = File.Exists(Path.Combine(RepoRoot, "client-patches.quarantine"));
            // Emitted only by the resilience rules -- in patch-engine.js and in
            // apply-map-resilience.js, which keep the same strings on purpose. Present means the
            // served bundle is ours; absent means it is the vendor's.
            const string Witness = "var _seriesDone=!1";
            string js;
            long size;
            try
            {
                js = File.ReadAllText(scriptPath);
                // The file's bytes, not the string's UTF-16 characters: the bundle is UTF-8 with
                // multi-byte sequences, so js.Length reads 35,195 smaller than the file is and a
                // size in a build log that is not the size on disk is worse than no size at all.
                size = new FileInfo(scriptPath).Length;
            }
            catch (Exception ex)
            {
                Console.WriteLine("  [WARN] cannot read the served bundle: " + ex.Message);
                return;
            }
            bool patched = js.IndexOf(Witness, StringComparison.Ordinal) >= 0;

            if (quarantined && patched)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("  [FAIL] the served bundle carries client patches while");
                Console.WriteLine("         client-patches.quarantine says the set is OFF. Something");
                Console.WriteLine("         wrote it past the flag -- shipping this is the black screen.");
                Console.ResetColor();
                Environment.Exit(1);
            }
            if (!quarantined && !patched)
            {
                // The other silent state: node missing, or every needle moved with the update.
                Console.ForegroundColor = ConsoleColor.Yellow;
                Console.WriteLine("  [WARN] the served bundle is the vendor's (" + size
                    + " bytes) and no quarantine flag explains it.");
                Console.WriteLine("         An unpatched bundle is what breaks login after an update;");
                Console.WriteLine("         if shipping it is intended, write client-patches.quarantine");
                Console.WriteLine("         with the reason and this line becomes a verdict.");
                Console.ResetColor();
                return;
            }
            Console.WriteLine(quarantined
                ? "  [OK] patches quarantined: the served bundle is the vendor's (" + size + " bytes, 0 applied)."
                : "  [OK] the served bundle carries the client patches (" + size + " bytes).");
        }

        static void TrySyncClientShims()
        {
            try
            {
                string desktopJsDir = Path.Combine(RepoRoot, @"desktop\client\www\js");
                string rootJsDir = Path.Combine(RepoRoot, @"client\www\js");

                if (!Directory.Exists(desktopJsDir) || !Directory.Exists(rootJsDir)) return;

                // mobile-disguise.js used to be copied from scripts/ into both www/js
                // folders here. That copy is why three identical 3135-line files sat in
                // the tree: the build recreated them after every edit, so a change made
                // in one place silently diverged from the other two.
                //
                // It is no longer copied because it is no longer loaded from there.
                // WebView2 injects scripts/mobile-disguise.js with
                // AddScriptToExecuteOnDocumentCreated, and the Go proxy serves it as
                // /__disguise.js for the Android path. The <script src> tags that pulled
                // the copies in were removed from both standalone.html files, and
                // scripts/mobile-disguise.js is now the single source of truth.
                // Do not add this copy back: it recreates the drift, and a second copy
                // means a second FarmBot bound to the same connection manager.

                // Sync autoupdate.js (master in client\www\js -> live copy in
                // desktop\client\www\js). The runtime serves desktop\client\www,
                // so an edit that only touches client\www never reaches the game:
                // that is exactly how the "new version, restart?" fix shipped to
                // a folder nobody loads.
                string masterAutoUpdate = Path.Combine(rootJsDir, "autoupdate.js");
                if (File.Exists(masterAutoUpdate))
                {
                    File.Copy(masterAutoUpdate, Path.Combine(desktopJsDir, "autoupdate.js"), true);
                }

                // Sync cordova-standalone-shim.js & cdn-relay-shim.js (master in desktop\client\www\js)
                string masterCordova = Path.Combine(desktopJsDir, "cordova-standalone-shim.js");
                if (File.Exists(masterCordova))
                {
                    File.Copy(masterCordova, Path.Combine(rootJsDir, "cordova-standalone-shim.js"), true);
                }
                string masterRelay = Path.Combine(desktopJsDir, "cdn-relay-shim.js");
                if (File.Exists(masterRelay))
                {
                    File.Copy(masterRelay, Path.Combine(rootJsDir, "cdn-relay-shim.js"), true);
                }
            }
            catch { }
        }

        // Training corpora are inputs to offline tools, not to inference. They
        // stay in the repo (and in the cleanup archive) but shipping every SFT
        // shard in setup.exe added ~160 MB of payload the installed app never
        // opens. The runtime still ships data/cache, data/knowledge, data/maps,
        // all models used by the agent, and every agent/ai serving module.
        static bool SkipTrainingPayload(string path)
        {
            string p = path.Replace('\\', '/');
            return p.IndexOf("/data/core_sft", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/tool_sft", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/tool_candidate", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/kaggle/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/models/_tune", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/models/archive", StringComparison.OrdinalIgnoreCase) >= 0
                // Recorded sessions are training inputs, not runtime state. The
                // recorder creates dofus-network/trajectories on first write
                // (agent/recorder.py mkdir parents), so a fresh install starts
                // empty instead of shipping other sessions' logs.
                || p.IndexOf("/trajectories/", StringComparison.OrdinalIgnoreCase) >= 0
                // Live encoder inference uses encoder_int8.onnx + tokenizer +
                // policy.json (autopilot's --policy encoder path). The FP32 graph
                // and the PyTorch checkpoint are training/export artifacts and
                // together are ~192 MB; shipping them doubled the payload for a
                // path the app never opens. The repo keeps both.
                || p.EndsWith("/models/encoder30_v15/model.pt", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/encoder30_v15/encoder.onnx", StringComparison.OrdinalIgnoreCase)
                // The command shadow ships one INT8 ONNX graph. Its FP32 graph,
                // torch heads, torch router and training reports are export
                // artifacts; the INT8 graph, config and tokenizer are the runtime.
                || p.EndsWith("/models/command_encoder30/command_encoder.onnx", StringComparison.OrdinalIgnoreCase)
                || (p.IndexOf("/models/command_encoder30/", StringComparison.OrdinalIgnoreCase) >= 0
                    && (p.EndsWith(".pt", StringComparison.OrdinalIgnoreCase)
                        || p.EndsWith("/router.json", StringComparison.OrdinalIgnoreCase)
                        || p.EndsWith("/adapter_report.json", StringComparison.OrdinalIgnoreCase)
                        || p.EndsWith("/router_report.json", StringComparison.OrdinalIgnoreCase)
                        || p.EndsWith("/metrics.json", StringComparison.OrdinalIgnoreCase)
                        || p.EndsWith("/metrics_eval.json", StringComparison.OrdinalIgnoreCase)
                        || p.EndsWith("/metrics_plz_rejected.json", StringComparison.OrdinalIgnoreCase)))
                || p.IndexOf("/data/commands/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/command_adapters/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.EndsWith("/data/command_replay.jsonl", StringComparison.OrdinalIgnoreCase)
                // The 0.25M word-level BiGRU is a negative baseline, not runtime.
                || p.IndexOf("/models/command_encoder/", StringComparison.OrdinalIgnoreCase) >= 0
                // Per-map cell/layer dumps are inputs to the offline duel and
                // benchmarks. The live agent reads only _index.json for routing
                // and the client supplies the live board; keep the default duel
                // map so installed offline commands still resolve.
                || (p.IndexOf("/data/maps/", StringComparison.OrdinalIgnoreCase) >= 0
                    && p.EndsWith(".json", StringComparison.OrdinalIgnoreCase)
                    && !p.EndsWith("/data/maps/_index.json", StringComparison.OrdinalIgnoreCase)
                    && !p.EndsWith("/data/maps/100139008.json", StringComparison.OrdinalIgnoreCase))
                // Design docs, offline benchmark corpora and training-only
                // checkpoints are never opened by the shipped runtime. They stay
                // in the repo but do not need to cross the installer wire.
                || p.IndexOf("/docs/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/benchmarks/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/colab/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/decision_sft", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/verifier_sft", StringComparison.OrdinalIgnoreCase) >= 0
                || p.EndsWith("/models/encounter_pbt.pkl", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/verifier_head.pt", StringComparison.OrdinalIgnoreCase);
        }

        // The installer is the artifact that leaves this machine. Measured 2026-11-03 with
        // tools/payload_audit.py --gate against the built setup.exe: the payload carried 21
        // `login:password` lines from accounts.txt and a 44-character WireGuard PrivateKey in three
        // files (wgcf-profile.conf, wireproxy.conf, wireproxy.runtime.conf), plus a WARP account
        // token, license key and private key in wgcf-account.toml. Anything that crosses the wire
        // there is handed to every installed copy -- and every installed copy then runs one shared
        // tunnel identity, which is a defect even if the key were throwaway. So ship the format and
        // never the values, and put wgcf.exe back in the payload: the shipped start-network.bat
        // tells the operator to run bin/warp/wgcf.exe to enroll, and the builder was excluding
        // that very tool, so the documented recovery path could not exist on an installed copy.
        static void WriteAccountsTemplate(string source, string target)
        {
            List<string> kept = new List<string>();
            foreach (string line in File.ReadAllLines(source))
            {
                string trimmed = line.Trim();
                if (trimmed.Length == 0 || trimmed.StartsWith("#")) kept.Add(line);
            }
            kept.Add("# one account per line as name:password - the installer ships no credentials,");
            kept.Add("# so an installed copy starts empty and the operator adds their own.");
            File.WriteAllLines(target, kept.ToArray());
        }

        static void WriteTunnelTemplate(string source, string target)
        {
            string[] secretKeys = new string[] { "privatekey", "address", "publickey", "endpoint", "presets" };
            List<string> lines = new List<string>();
            foreach (string raw in File.ReadAllLines(source))
            {
                int eq = raw.IndexOf('=');
                string head = eq < 0 ? "" : raw.Substring(0, eq).Trim().ToLowerInvariant().Replace("_", string.Empty).Replace(" ", string.Empty);
                if (eq >= 0 && Array.IndexOf(secretKeys, head) >= 0)
                    lines.Add(raw.Substring(0, eq).TrimEnd() + " = PLACEHOLDER_RUN_BIN_WARP_WGCF_EXE_REGISTER_AND_GENERATE");
                else
                    lines.Add(raw);
            }
            File.WriteAllLines(target, lines.ToArray());
        }

        // The combat grid ranker is a JAX graph, and the interpreter that ships inside
        // setup.exe is the embedded one -- it carries no jax wheel, so an installed copy can
        // never load models/combat_grid/model.pkl. That was 118 MB of weights the product
        // cannot read. The rule is conditional on the shipped runtime rather than on a guess:
        // if jax ever lands in runtimes/python the artifact ships again and the opt-in ranker
        // works there. combat/grid_infer.py loads jax lazily for the same reason -- before that
        // fix an installed copy raised ImportError on the opt-in path instead of refusing.
        static bool _jaxChecked;
        static bool _jaxPresent;

        static bool RuntimeHasJax()
        {
            if (!_jaxChecked)
            {
                _jaxChecked = true;
                _jaxPresent = Directory.Exists(Path.Combine(RepoRoot, "runtimes", "python",
                    "Lib", "site-packages", "jax"));
                Console.WriteLine(_jaxPresent
                    ? "  embedded runtime has jax: shipping the grid model"
                    : "  embedded runtime has NO jax: skipping models/combat_grid (118 MB of unloadable weights)");
            }
            return _jaxPresent;
        }

        // Recorded session dumps, training shards and dev transcripts. The installed app never
        // opens any of these; they are inputs to offline tools in the same class as the SFT
        // shards SkipTrainingPayload already drops, and together they were another ~82 MB of
        // payload plus 1.9 MB of gate logs.
        static bool SkipDevPayload(string path)
        {
            string p = path.Replace('\\', '/');
            return p.IndexOf("/data/transitions", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/entity_pointer", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/combat_grid", StringComparison.OrdinalIgnoreCase) >= 0
                || (p.IndexOf("/models/combat_grid", StringComparison.OrdinalIgnoreCase) >= 0 && !RuntimeHasJax())
                || p.EndsWith(".log", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase)
                || p.IndexOf(".tmp-", StringComparison.OrdinalIgnoreCase) >= 0
                || p.EndsWith("_selftest.txt", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith(".bak", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith(".orig", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("gate_run.json", StringComparison.OrdinalIgnoreCase)
                || p.IndexOf("/models/encoder30_v15/encoder.report.json", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/models/encoder30_v15/metrics.json", StringComparison.OrdinalIgnoreCase) >= 0
                // Dev-tool droppings: this box's linter and test caches, its editor settings and
                // the bundler's timestamped staging copies. 96 such files were in the 2026-11-03
                // artifact; none has a reader on an installed machine, which is not a git tree.
                || p.IndexOf("/.ruff_cache/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/.pytest_cache/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/.vscode/", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/staging-", StringComparison.OrdinalIgnoreCase) >= 0
                || p.EndsWith("/.gitignore", StringComparison.OrdinalIgnoreCase)
                // One-off run logs and their stderr, written by a janitor/close probe on this box.
                || p.IndexOf("/data/close_run", StringComparison.OrdinalIgnoreCase) >= 0
                || p.IndexOf("/data/janitor_run", StringComparison.OrdinalIgnoreCase) >= 0
                || (p.EndsWith(".err", StringComparison.OrdinalIgnoreCase)
                    && p.IndexOf("/data/", StringComparison.OrdinalIgnoreCase) >= 0)
                // Model graphs with no reader anywhere in the shipped tree. Measured 2026-11-03:
                // `grep -rl <stem> --include=*.py` over dofus-network returns only the training
                // script that writes each one. The cause head's runtime form is models/
                // cause_head.json plus its sibling prototypes -- both keep shipping -- and the
                // verifier's int8 graph has no loader at all, while veto_shadow is asserted
                // unwired by agent/regression.py ("veto_shadow not in autopilot").
                || p.EndsWith("/models/cause_head.onnx", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/cause_head_int8.onnx", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/verifier_head_int8.onnx", StringComparison.OrdinalIgnoreCase)
                || p.IndexOf("/models/veto_shadow/", StringComparison.OrdinalIgnoreCase) >= 0
                // The documented-facts history is a dev instrument, and shipping it made the
                // payload's own unpacked size unmeasurable: every `tools/doc_facts.py --render`
                // or `--check` appends a record to it, the docs quote the unpacked total, and
                // the build then ships the file the quote is inside. Each pass moved the number
                // it was trying to pin (+14 B observed per render/build round), so the tagged
                // value could never settle. Nothing on an installed machine reads it.
                || p.EndsWith("/data/doc_facts_history.json", StringComparison.OrdinalIgnoreCase)
                // Learned weights, moved to the opt-in tier on 2026-11-03 (owner's call). Measured
                // in-zip: command_encoder_int8.onnx 22.0 MB, encoder30_v15/encoder_int8.onnx
                // 21.9 MB, lexical_policy/policy.joblib 3.7 MB, tool_policy.onnx 0.18 MB,
                // verifier_head.onnx 0.05 MB -- 47.8 MB, 45.5% of a 100.1 MiB setup.exe, for three
                // paths that are off by default (`--policy rule`, command_shadow="off", and the
                // lexical policy behind DOFUS_LEARNED_COMMAND_MODEL). Their wheels move with them:
                // onnxruntime 43 MB, scikit-learn 29 MB, tokenizers 7.9 MB, joblib 1.3 MB as
                // measured in this repo's runtime. An installed copy keeps the code and can load
                // a weight the user copies in, but no longer carries graphs nobody asked to run;
                // the repo keeps all five. The small configs beside them (command_onnx.json,
                // policy.json, tokenizer.json) still ship, so a copied-in graph resolves.
                || p.EndsWith("/models/command_encoder30/command_encoder_int8.onnx",
                              StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/encoder30_v15/encoder_int8.onnx",
                              StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/lexical_policy/policy.joblib",
                              StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/tool_policy.onnx", StringComparison.OrdinalIgnoreCase)
                || p.EndsWith("/models/verifier_head.onnx", StringComparison.OrdinalIgnoreCase)
                // Two contradictory requirements.txt files shipped: the root one is what
                // setup.exe and install-python-ai.bat install, `dofus-network/requirements.txt`
                // listed only numpy+requests under a stale "this machine runs Python 3.15, use
                // Colab" header. Anyone who pip-installed from the second got an env where the
                // ONNX and lexical paths cannot load. One file, one tier list.
                || p.EndsWith("/dofus-network/requirements.txt", StringComparison.OrdinalIgnoreCase);
        }

        static bool BuildStandaloneSetup(string outSetup, string setupIco, string desktopDir, bool includeLegacyEmulator = false)
        {
            string tempDir = Path.GetTempPath();
            string stageDir = Path.Combine(tempDir, "dofus_build_stage_" + Guid.NewGuid().ToString("N"));
            string payloadZip = Path.Combine(tempDir, "DofusPayload_" + Guid.NewGuid().ToString("N") + ".zip");

            try
            {
                if (Directory.Exists(stageDir)) Directory.Delete(stageDir, true);
                Directory.CreateDirectory(stageDir);

                string stageScripts = Path.Combine(stageDir, "scripts");
                Directory.CreateDirectory(stageScripts);
                // proxy_pool.js belongs here: it extracts, tests and rewrites proxies.txt,
                // and it was the one piece of the egress pipeline that never shipped. The
                // app reads proxies.txt and the supervisor probes it, but nothing in an
                // installed copy could refresh it, so the pool rotted to a single dead
                // entry and every launch failed closed with "no healthy egress".
                foreach (string rs in new[] { "mobile-disguise.js", "apply-zram.sh", "proxy_pool.js", "harvest_proxies.js", "proxy_tunnel.js" })
                {
                    string src = Path.Combine(RepoRoot, "scripts", rs);
                    if (File.Exists(src))
                        File.Copy(src, Path.Combine(stageScripts, rs), true);
                }

                // The two entry points an installed copy needs: one to bring the egress up,
                // one to look after it afterwards. Both live at the payload root so they sit
                // next to the launcher, not buried in scripts\.
                foreach (string bat in new[] { "start-network.bat", "manage-proxy.bat" })
                {
                    string src = Path.Combine(RepoRoot, bat);
                    if (File.Exists(src))
                        File.Copy(src, Path.Combine(stageDir, bat), true);
                }

                string repoBin = Path.Combine(RepoRoot, @"scripts\bin");
                if (Directory.Exists(repoBin) && includeLegacyEmulator)
                {
                    string stageBin = Path.Combine(stageScripts, "bin");
                    Directory.CreateDirectory(stageBin);
                    foreach (string f in Directory.GetFiles(repoBin))
                        File.Copy(f, Path.Combine(stageBin, Path.GetFileName(f)), true);
                }

                foreach (string b in new[] { "DofusFarm.exe", "uninstall.exe", "dofus-net-proxy.exe", "proxies.txt", "accounts.txt", "network.ini", @"tools\patch-rules.txt", "app_icon.ico", "installer_icon.ico", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll" })
                {
                    string p = Path.Combine(RepoRoot, b);
                    if (!File.Exists(p)) p = Path.Combine(RepoRoot, "scripts", b);
                    if (!File.Exists(p)) p = Path.Combine(RepoRoot, "desktop", "lib", "webview2", b);
                    if (File.Exists(p))
                    {
                        string dest = Path.Combine(stageDir, b);
                        string destDir = Path.GetDirectoryName(dest);
                        if (!string.IsNullOrEmpty(destDir) && !Directory.Exists(destDir))
                            Directory.CreateDirectory(destDir);
                        if (string.Equals(b, "accounts.txt", StringComparison.OrdinalIgnoreCase))
                        {
                            WriteAccountsTemplate(p, dest);
                            Console.WriteLine("  [OK] accounts.txt staged as a comment-only template (no credentials)");
                            continue;
                        }
                        File.Copy(p, dest, true);
                    }
                }

                string repoWarp = Path.Combine(RepoRoot, @"bin\warp");
                if (Directory.Exists(repoWarp))
                {
                    string stageWarp = Path.Combine(stageDir, @"bin\warp");
                    Directory.CreateDirectory(stageWarp);
                    foreach (string wf in Directory.GetFiles(repoWarp))
                    {
                        // The host uses wireproxy for the local tunnel and keeps
                        // wgcf for regenerating its config. warp-plus is a separate
                        // VPN client that no Windows code path starts; it was 20 MB
                        // of dead payload. It remains in the repo.
                        string leafName = Path.GetFileName(wf);
                        if (string.Equals(leafName, "warp-plus.exe", StringComparison.OrdinalIgnoreCase))
                            continue;
                        if (string.Equals(leafName, "wgcf-account.toml", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(leafName, "wgcf-profile.conf", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(leafName, "wireproxy.conf", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(leafName, "wireproxy.runtime.conf", StringComparison.OrdinalIgnoreCase))
                        {
                            Console.WriteLine("  [OK] kept " + leafName + " out of the payload: it holds this machine's tunnel keys");
                            continue;
                        }
                        if (string.Equals(leafName, "wgcf.exe", StringComparison.OrdinalIgnoreCase))
                            Console.WriteLine("  [OK] shipping wgcf.exe: start-network.bat tells the operator to run it to enroll");
                        File.Copy(wf, Path.Combine(stageWarp, leafName), true);
                    }
                    string tunnelSource = Path.Combine(repoWarp, "wireproxy.conf");
                    if (File.Exists(tunnelSource))
                    {
                        WriteTunnelTemplate(tunnelSource, Path.Combine(stageWarp, "wireproxy.conf.template"));
                        Console.WriteLine("  [OK] wrote bin/warp/wireproxy.conf.template (placeholders, no keys)");
                    }
                }

                string repoWebView2Boot = Path.Combine(RepoRoot, @"bin\webview2");
                if (Directory.Exists(repoWebView2Boot))
                {
                    string stageWebView2Boot = Path.Combine(stageDir, @"bin\webview2");
                    Directory.CreateDirectory(stageWebView2Boot);
                    foreach (string wf in Directory.GetFiles(repoWebView2Boot))
                    {
                        File.Copy(wf, Path.Combine(stageWebView2Boot, Path.GetFileName(wf)), true);
                    }
                }

                // Stage desktop assets (including webview_test.html)
                string stageAssets = Path.Combine(stageDir, "assets");
                Directory.CreateDirectory(stageAssets);
                string repoAssets = Path.Combine(RepoRoot, "desktop", "assets");
                if (Directory.Exists(repoAssets))
                {
                    foreach (string af in Directory.GetFiles(repoAssets))
                        File.Copy(af, Path.Combine(stageAssets, Path.GetFileName(af)), true);
                }

                // Stage native loader runtimes & clean python embed
                string repoRuntimes = Path.Combine(RepoRoot, "runtimes");
                if (Directory.Exists(repoRuntimes))
                {
                    string stageRuntimes = Path.Combine(stageDir, "runtimes");
                    Directory.CreateDirectory(stageRuntimes);
                    foreach (string dir in Directory.GetDirectories(repoRuntimes, "*", SearchOption.AllDirectories))
                    {
                        string leaf = Path.GetFileName(dir);
                        if (dir.IndexOf("site-packages", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            dir.IndexOf("__pycache__", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            dir.IndexOf(".dist-info", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            string.Equals(leaf, "Scripts", StringComparison.OrdinalIgnoreCase) ||
                            string.Equals(leaf, "share", StringComparison.OrdinalIgnoreCase))
                            continue;
                        Directory.CreateDirectory(dir.Replace(repoRuntimes, stageRuntimes));
                    }
                    foreach (string file in Directory.GetFiles(repoRuntimes, "*", SearchOption.AllDirectories))
                    {
                        if (file.IndexOf("site-packages", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            file.IndexOf("__pycache__", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            file.IndexOf(".dist-info", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            file.IndexOf("\\Scripts\\", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            file.IndexOf("\\share\\", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            file.EndsWith(".pyc", StringComparison.OrdinalIgnoreCase) ||
                            file.EndsWith("python.cat", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(Path.GetFileName(file), "python_embed.zip",
                                             StringComparison.OrdinalIgnoreCase))
                        {
                            // runtimes/python_embed.zip is the same interpreter this loop copies
                            // unpacked: 10.7 MB of already-compressed archive stored inside the
                            // payload archive -- 9.4% of setup.exe for a repair branch that could only
                            // fire if the unpacked copy failed. desktop/installer.cs now verifies the
                            // unpacked runtime and falls back to a zip placed beside setup.exe, so the
                            // repair capability survives without shipping it to every machine.
                            if (string.Equals(Path.GetFileName(file), "python_embed.zip",
                                              StringComparison.OrdinalIgnoreCase))
                                Console.WriteLine("  [OK] kept runtimes/python_embed.zip out of the payload (10.7 MB duplicate of the unpacked runtime)");
                            continue;
                        }
                        string destFile = file.Replace(repoRuntimes, stageRuntimes);
                        string parent = Path.GetDirectoryName(destFile);
                        if (!Directory.Exists(parent)) Directory.CreateDirectory(parent);
                        File.Copy(file, destFile, true);
                    }
                }

                // Stage standalone Dofus Touch client bundle
                string repoClient = Path.Combine(RepoRoot, "desktop", "client");
                if (Directory.Exists(repoClient))
                {
                    string stageClient = Path.Combine(stageDir, "client");
                    Directory.CreateDirectory(stageClient);
                    foreach (string dir in Directory.GetDirectories(repoClient, "*", SearchOption.AllDirectories))
                    {
                        if (SkipDevPayload(dir)) continue;
                        Directory.CreateDirectory(dir.Replace(repoClient, stageClient));
                    }
                    foreach (string file in Directory.GetFiles(repoClient, "*", SearchOption.AllDirectories))
                    {
                        // script.js.bak is patch-engine's pristine in-repo backup
                        // (5.4 MB). The installed client only needs the patched
                        // script.js, and patch-engine is run by the builder.
                        if (file.EndsWith(".bak", StringComparison.OrdinalIgnoreCase))
                            continue;
                        // www/staging-<timestamp>/ copies are the packager's scratch output. They
                        // were the one dev-residue class the gate still flagged after the
                        // dofus-network prune, because this loop did not consult SkipDevPayload.
                        if (SkipDevPayload(file)) continue;
                        File.Copy(file, file.Replace(repoClient, stageClient), true);
                    }
                }

                // Stage dofus-network AI engine & trained TRM-MoE checkpoint
                string repoNetwork = Path.Combine(RepoRoot, "dofus-network");
                if (Directory.Exists(repoNetwork))
                {
                    string stageNetwork = Path.Combine(stageDir, "dofus-network");
                    Directory.CreateDirectory(stageNetwork);
                    foreach (string dir in Directory.GetDirectories(repoNetwork, "*", SearchOption.AllDirectories))
                    {
                        if (dir.IndexOf("__pycache__", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            dir.IndexOf(".git", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            dir.IndexOf(".pytest_cache", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            dir.IndexOf("networkhealth_artifacts", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            SkipTrainingPayload(dir) ||
                            SkipDevPayload(dir))
                            continue;
                        Directory.CreateDirectory(dir.Replace(repoNetwork, stageNetwork));
                    }
                    foreach (string file in Directory.GetFiles(repoNetwork, "*", SearchOption.AllDirectories))
                    {
                        if (file.EndsWith(".pyc", StringComparison.OrdinalIgnoreCase) ||
                            file.IndexOf("__pycache__", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            file.IndexOf("networkhealth_artifacts", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            SkipTrainingPayload(file) ||
                            SkipDevPayload(file))
                            continue;
                        string destFile = file.Replace(repoNetwork, stageNetwork);
                        string parent = Path.GetDirectoryName(destFile);
                        if (!Directory.Exists(parent)) Directory.CreateDirectory(parent);
                        File.Copy(file, destFile, true);
                    }

                    // The artifacts folder is excluded above (100 MB of corpora), but the
                    // checkpoints are ~220 KB each and are the only files inference needs.
                    // Both ship: model.pt is the RIPE one (60 s cadence, the fallback) and
                    // local_model/model.pt is trained on this machine at 0.5 s, which is what
                    // every tool here actually samples at. Shipping only the first is why an
                    // installed copy would quote a forecast from the wrong distribution.
                    string artifacts = Path.Combine(repoNetwork, "networkhealth_artifacts");
                    string modelDst = Path.Combine(stageNetwork, "networkhealth_artifacts");
                    string globalModel = Path.Combine(artifacts, "model.pt");
                    if (File.Exists(globalModel))
                    {
                        Directory.CreateDirectory(modelDst);
                        File.Copy(globalModel, Path.Combine(modelDst, "model.pt"), true);
                    }
                    string localModel = Path.Combine(artifacts, "local_model", "model.pt");
                    if (File.Exists(localModel))
                    {
                        string localDst = Path.Combine(modelDst, "local_model");
                        Directory.CreateDirectory(localDst);
                        File.Copy(localModel, Path.Combine(localDst, "model.pt"), true);
                    }
                }

                // requirements.txt is the installed app's only source of Python packages: site-packages
                // never crosses the installer, so whatever pip cannot fetch from this list is a feature
                // that does not work after install. The builder used to write a hardcoded four-line file
                // (torch/transformers/safetensors) while the shipped code also imports numpy,
                // onnxruntime, tokenizers, scikit-learn, joblib and requests -- measured with
                // tools/shipped_deps.py over the import closure of the real entry points, which is now
                // the single source of truth. Ship the repo's file; only invent the old list if it is gone.
                string reqPath = Path.Combine(stageDir, "requirements.txt");
                string repoReq = Path.Combine(RepoRoot, "requirements.txt");
                if (File.Exists(repoReq))
                {
                    File.Copy(repoReq, reqPath, true);
                    int reqCount = 0;
                    foreach (string line in File.ReadAllLines(repoReq))
                    {
                        string trimmed = line.Trim();
                        if (trimmed.Length > 0 && !trimmed.StartsWith("#")) reqCount++;
                    }
                    Console.WriteLine("  [OK] requirements.txt from the repo (" + reqCount +
                        " distributions, derived from the import closure; the header comments are" +
                        " documentation, not packages)");
                }
                else
                {
                    File.WriteAllText(reqPath, "torch>=2.0.0\r\ntransformers>=4.48.0\r\nsafetensors>=0.4.0\r\n");
                    Console.WriteLine("  [WARN] repo requirements.txt missing; wrote the fallback list");
                }

                // The optional AI tier (torch, transformers, safetensors: 617.4 MB installed,
                // of which torch alone is 508.8 MB) ships as its own list so that `setup.exe` can
                // install the ~128 MB the product actually drives with. It is the same file for the
                // same reason: the builder writing its own copy of a script the repo already has is
                // how start-sentinel.bat ended up with two sources of truth.
                string repoAiReq = Path.Combine(RepoRoot, "requirements-ai.txt");
                if (File.Exists(repoAiReq))
                {
                    File.Copy(repoAiReq, Path.Combine(stageDir, "requirements-ai.txt"), true);
                    Console.WriteLine("  [OK] requirements-ai.txt staged (opt-in tier: run install-python-ai.bat)");
                }
                else
                {
                    Console.WriteLine("  [ERROR] repo requirements-ai.txt is missing; an installed copy"
                        + " cannot be given the optional AI tier at all");
                }

                // Copied from the repo, not retyped here. start-sentinel.bat preflights for torch and
                // tells the operator to run install-python-ai.bat, because the sentinel builds
                // nn.Module classes in its own class bodies and has no degraded mode to fall back to.
                foreach (string bat in new[] { "start-sentinel.bat", "install-python-ai.bat" })
                {
                    string repoBat = Path.Combine(RepoRoot, bat);
                    if (File.Exists(repoBat))
                    {
                        File.Copy(repoBat, Path.Combine(stageDir, bat), true);
                        Console.WriteLine("  [OK] " + bat + " staged from the repo");
                    }
                    else
                    {
                        // Shipping no launcher would be silent breakage: start-sentinel.bat is the
                        // only thing that tells an operator how to get the AI tier, and
                        // install-python-ai.bat is the only way to get it.
                        Console.WriteLine("  [ERROR] " + bat + " is not in the repo tree; the builder"
                            + " no longer writes these scripts itself, so nothing would be staged");
                        Environment.Exit(1);
                    }
                }
                if (File.Exists(payloadZip)) File.Delete(payloadZip);
                // Empty folders left behind in the stage ride along as zero-byte zip entries:
                // 16 of them in the 2026-11-03 audit (dofus-network/docs, kaggle, trajectories,
                // benchmarks, colab, data/commands -- the excluded trees, whose contents the skip
                // predicates above correctly refuse to copy while a parent chain still got created).
                // Every one of those paths is created on first use by the code that owns it, so an
                // installed copy does not need the placeholder; drop them so the payload contains
                // only bytes the app asked for.
                bool pruned = true;
                int prunePasses = 0;
                while (pruned && prunePasses < 12)
                {
                    pruned = false;
                    prunePasses++;
                    foreach (string leaf in Directory.GetDirectories(stageDir, "*", SearchOption.AllDirectories))
                    {
                        if (!Directory.Exists(leaf)) continue;
                        if (Directory.GetFiles(leaf).Length == 0 && Directory.GetDirectories(leaf).Length == 0)
                        {
                            try { Directory.Delete(leaf); pruned = true; }
                            catch (Exception) { }
                        }
                    }
                }
                ZipFile.CreateFromDirectory(stageDir, payloadZip, CompressionLevel.Optimal, false);

                // Embed DofusPayload.zip
                StringBuilder resourceArgs = new StringBuilder();
                resourceArgs.AppendFormat("\"/resource:{0},DofusPayload.zip\" ", payloadZip);

                long totalBytes = new FileInfo(payloadZip).Length;

                if (includeLegacyEmulator)
                {
                    string tplZip = Path.Combine(RepoRoot, "template.zip");
                    if (File.Exists(tplZip))
                    {
                        resourceArgs.AppendFormat("\"/resource:{0},template.zip\" ", tplZip);
                        totalBytes += new FileInfo(tplZip).Length;
                    }

                    string sdk7z = Path.Combine(RepoRoot, "sdk.7z");
                    if (File.Exists(sdk7z))
                    {
                        resourceArgs.AppendFormat("\"/resource:{0},sdk.7z\" ", sdk7z);
                        totalBytes += new FileInfo(sdk7z).Length;
                    }
                }
                else
                {
                    Console.ForegroundColor = ConsoleColor.DarkGray;
                    Console.WriteLine("  [INFO] Standalone WebView2 package mode (stripped 950 MB legacy Android emulator).");
                    Console.ResetColor();
                }

                Console.WriteLine("  [OK] Payload streams ready: {0:F1} MB (zero temp archive duplication)", totalBytes / (1024.0 * 1024.0));

                string manifestFile = Path.Combine(tempDir, "StandaloneSetup_" + Guid.NewGuid().ToString("N") + ".manifest");
                string manifestSource =
                    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" +
                    "<assembly manifestVersion=\"1.0\" xmlns=\"urn:schemas-microsoft-com:asm.v1\">\n" +
                    "  <assemblyIdentity version=\"2.4.0.0\" name=\"DofusFarmSetup\"/>\n" +
                    "  <trustInfo xmlns=\"urn:schemas-microsoft-com:asm.v3\">\n" +
                    "    <security>\n" +
                    "      <requestedPrivileges xmlns=\"urn:schemas-microsoft-com:asm.v3\">\n" +
                    "        <requestedExecutionLevel level=\"requireAdministrator\" uiAccess=\"false\" />\n" +
                    "      </requestedPrivileges>\n" +
                    "    </security>\n" +
                    "  </trustInfo>\n" +
                    "  <compatibility xmlns=\"urn:schemas-microsoft-com:compatibility.v1\">\n" +
                    "    <application>\n" +
                    "      <supportedOS Id=\"{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}\" />\n" +
                    "    </application>\n" +
                    "  </compatibility>\n" +
                    "</assembly>";
                File.WriteAllText(manifestFile, manifestSource, new UTF8Encoding(false));

                string[] setupSources = new[]
                {
                    Path.Combine(desktopDir, "Win32.cs"),
                    Path.Combine(desktopDir, "Hardware.cs"),
                    Path.Combine(desktopDir, "DynamicVersionResolver.cs"),
                    Path.Combine(desktopDir, "IClientBackend.cs"),
                    // Shared with the farm set: the governor now reads its throttle
                    // preference from RuntimeSettings, and the engine has used
                    // LocalSecret since the loopback token was introduced. Omitting
                    // them here failed setup.exe with CS0103, not the app build.
                    Path.Combine(desktopDir, "LocalSecret.cs"),
                    Path.Combine(desktopDir, "RuntimeSettings.cs"),
                    Path.Combine(desktopDir, "PriorityGovernor.cs"),
                    Path.Combine(desktopDir, "FarmEngine.cs"),
                    Path.Combine(desktopDir, "ProxySupervisor.cs"),
                    Path.Combine(desktopDir, "NetSettings.cs"),
                    Path.Combine(desktopDir, "Ini.cs"),
                    Path.Combine(desktopDir, "PythonLocator.cs"),
                    Path.Combine(desktopDir, "ProgressBar.cs"),
                    Path.Combine(desktopDir, "Installer.cs")
                };
                string setupIconArg = File.Exists(setupIco) ? string.Format("\"/win32icon:{0}\" ", setupIco) : "";
                string setupArgs = string.Format(
                    "/nologo /target:winexe /platform:anycpu /optimize+ {0}\"/win32manifest:{1}\" {2}" +
                    "{3}\"/out:{4}\" \"{5}\"",
                    setupIconArg, manifestFile, resourceArgs.ToString(), RefArgs(true), outSetup, string.Join("\" \"", setupSources));

                int sec = RunCsc(setupArgs);
                try { File.Delete(manifestFile); } catch { }

                if (sec == 0)
                {
                    Console.ForegroundColor = ConsoleColor.Green;
                    Console.WriteLine("  [OK] setup.exe compiled ({0:F1} MB)", new FileInfo(outSetup).Length / (1024.0 * 1024.0));
                    Console.ResetColor();
                    return true;
                }
                else
                {
                    Console.ForegroundColor = ConsoleColor.Red;
                    Console.WriteLine("  [FAIL] Failed to compile setup.exe");
                    Console.ResetColor();
                    return false;
                }
            }
            finally
            {
                try { if (Directory.Exists(stageDir)) Directory.Delete(stageDir, true); } catch { }
                try { if (File.Exists(payloadZip)) File.Delete(payloadZip); } catch { }
            }
        }

        // Locate dotnet.exe / csc.exe without hardcoded paths.
        static void ResolveCompiler()
        {
            // --- Preferred: Roslyn csc.dll from any installed .NET SDK (newest wins) ---
            string dotnet = FindOnPath("dotnet.exe");
            if (dotnet == null)
            {
                string candidate = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                    "dotnet", "dotnet.exe");
                if (File.Exists(candidate)) dotnet = candidate;
            }
            if (dotnet != null)
            {
                string sdkRoot = Path.Combine(Path.GetDirectoryName(dotnet), "sdk");
                if (Directory.Exists(sdkRoot))
                {
                    string bestVer = null, bestCsc = null;
                    foreach (string verDir in Directory.GetDirectories(sdkRoot))
                    {
                        string cscdll = Path.Combine(verDir, "Roslyn", "bincore", "csc.dll");
                        if (!File.Exists(cscdll)) continue;
                        if (bestVer == null || string.CompareOrdinal(Path.GetFileName(verDir), bestVer) > 0)
                        { bestVer = Path.GetFileName(verDir); bestCsc = cscdll; }
                    }
                    if (bestCsc != null) { DotNetHost = dotnet; CscDll = bestCsc; }
                }
            }

            // --- Fallback: legacy .NET Framework csc.exe (highest version found) ---
            if (DotNetHost == null)
            {
                string fwRoot = FrameworkCandidatesRoot();
                if (fwRoot != null)
                {
                    string best = null;
                    foreach (string v in Directory.GetDirectories(fwRoot))
                    {
                        if (!File.Exists(Path.Combine(v, "csc.exe"))) continue;
                        if (best == null || string.CompareOrdinal(v, best) > 0) best = v;
                    }
                    if (best != null) Csc = Path.Combine(best, "csc.exe");
                }
            }
            FrameworkDir = FindFrameworkDir();
        }

        // Candidate roots for the .NET Framework install (64-bit preferred).
        static string FrameworkCandidatesRoot()
        {
            string windir = Environment.GetEnvironmentVariable("windir");
            if (string.IsNullOrEmpty(windir)) windir = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string f64 = Path.Combine(windir, "Microsoft.NET", "Framework64");
            if (Directory.Exists(f64)) return f64;
            string f32 = Path.Combine(windir, "Microsoft.NET", "Framework");
            if (Directory.Exists(f32)) return f32;
            return null;
        }

        static string FindOnPath(string exe)
        {
            string pathVar = Environment.GetEnvironmentVariable("PATH");
            if (string.IsNullOrEmpty(pathVar)) return null;
            foreach (string dir in pathVar.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                try
                {
                    string candidate = Path.Combine(dir.Trim(), exe);
                    if (File.Exists(candidate)) return candidate;
                }
                catch { }
            }
            return null;
        }

        // Highest-version .NET Framework 4.x directory (holds mscorlib.dll, System.dll, ...).
        static string FindFrameworkDir()
        {
            string root = FrameworkCandidatesRoot();
            if (root == null) return null;
            string best = null;
            foreach (string v in Directory.GetDirectories(root))
            {
                if (!File.Exists(Path.Combine(v, "mscorlib.dll"))) continue;
                if (best == null || string.CompareOrdinal(v, best) > 0) best = v;
            }
            return best;
        }

        // Reference-assembly argument list shared by every compile step.
        // Roslyn needs explicit full paths (it does not search the legacy GAC),
        // so each name is resolved from the Framework dir or the GAC at runtime.
        static string RefArgs(bool needMicrosoftCSharp = false)
        {
            List<string> names = new List<string>
            {
                "mscorlib.dll", "System.dll", "System.Core.dll", "System.Drawing.dll",
                "System.Windows.Forms.dll", "System.Management.dll",
                "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll"
            };
            if (needMicrosoftCSharp) names.Add("Microsoft.CSharp.dll");

            List<string> parts = new List<string>();
            if (DotNetHost != null)
            {
                // Roslyn: reference the .NET Framework runtime assemblies explicitly
                // so the produced binaries still target .NET Framework 4.x.
                parts.Add("/nostdlib+");
                foreach (string n in names)
                {
                    string p = ResolveFrameworkAssembly(n);
                    if (p != null) parts.Add(string.Format("\"/r:{0}\"", p));
                }
            }
            else
            {
                // Legacy csc: framework dir is already on the default search path.
                foreach (string n in names)
                    parts.Add(string.Format("/r:{0}", n));
            }

            // Reference WebView2 assemblies
            string wvCore = Path.Combine(RepoRoot, "desktop", "lib", "webview2", "Microsoft.Web.WebView2.Core.dll");
            if (!File.Exists(wvCore)) wvCore = Path.Combine(RepoRoot, "Microsoft.Web.WebView2.Core.dll");
            if (File.Exists(wvCore)) parts.Add(string.Format("\"/r:{0}\"", wvCore));

            string wvWinForms = Path.Combine(RepoRoot, "desktop", "lib", "webview2", "Microsoft.Web.WebView2.WinForms.dll");
            if (!File.Exists(wvWinForms)) wvWinForms = Path.Combine(RepoRoot, "Microsoft.Web.WebView2.WinForms.dll");
            if (File.Exists(wvWinForms)) parts.Add(string.Format("\"/r:{0}\"", wvWinForms));

            return string.Join(" ", parts.ToArray()) + " ";
        }

        static string ResolveFrameworkAssembly(string name)
        {
            // 1. Framework dir (...\v4.0.30319\<name>)
            if (FrameworkDir != null)
            {
                string p = Path.Combine(FrameworkDir, name);
                if (File.Exists(p)) return p;
            }
            // 2. GAC_MSIL (...\assembly\GAC_MSIL\<Name>\<ver>\<Name>.dll)
            string windir = Environment.GetEnvironmentVariable("windir");
            if (string.IsNullOrEmpty(windir)) windir = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string gac = Path.Combine(windir, "Microsoft.NET", "assembly", "GAC_MSIL",
                Path.GetFileNameWithoutExtension(name));
            if (Directory.Exists(gac))
            {
                foreach (string v in Directory.GetDirectories(gac))
                {
                    string p = Path.Combine(v, name);
                    if (File.Exists(p)) return p;
                }
            }
            return null;
        }

        // Route a compile through whichever compiler was resolved.
        static int RunCsc(string args)
        {
            if (DotNetHost != null)
                return Run(DotNetHost, string.Format("\"{0}\" {1}", CscDll, args));
            return Run(Csc, args);
        }

        static int Run(string exe, string args, string workingDir = null)
        {
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = args,
                WorkingDirectory = string.IsNullOrEmpty(workingDir) ? RepoRoot : workingDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardError = true,
                RedirectStandardOutput = true
            };
            using (Process p = Process.Start(psi))
            {
                // Async reads prevent pipe-buffer deadlock on large compiler output.
                Task<string> outTask = Task<string>.Factory.StartNew(
                    delegate (object o) { return ((Process)o).StandardOutput.ReadToEnd(); }, p);
                Task<string> errTask = Task<string>.Factory.StartNew(
                    delegate (object o) { return ((Process)o).StandardError.ReadToEnd(); }, p);
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    string outText = outTask.Result;
                    string errText = errTask.Result;
                    if (!string.IsNullOrEmpty(outText)) Console.WriteLine(outText);
                    if (!string.IsNullOrEmpty(errText)) Console.WriteLine(errText);
                }
                return p.ExitCode;
            }
        }

        static void PreBuildClean(string repoRoot)
        {
            // 1. Terminate any running instances so Roslyn compiler can write to binaries without locks
            foreach (string name in new[] { "DofusFarm", "dofus-net-proxy", "setup", "uninstall" })
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(name))
                    {
                        using (p)
                        {
                            try { p.Kill(); p.WaitForExit(3000); } catch { }
                        }
                    }
                }
                catch { }
            }

            // 2. Clean up any stale hidden Roslyn compiler temporary files (*_DofusFarm.exe, etc.)
            try
            {
                foreach (string file in Directory.GetFiles(repoRoot, "*_DofusFarm.exe"))
                {
                    try { File.SetAttributes(file, FileAttributes.Normal); File.Delete(file); } catch { }
                }
                foreach (string file in Directory.GetFiles(repoRoot, "*_setup.exe"))
                {
                    try { File.SetAttributes(file, FileAttributes.Normal); File.Delete(file); } catch { }
                }
                foreach (string file in Directory.GetFiles(repoRoot, "*_uninstall.exe"))
                {
                    try { File.SetAttributes(file, FileAttributes.Normal); File.Delete(file); } catch { }
                }
            }
            catch { }
        }
    }
}
