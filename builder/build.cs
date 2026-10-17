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

            // 0. Compile Go Network Proxy if go.exe is available
            TryBuildGoProxy();

            // 1. Compile DofusFarm.exe
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("\n[1/3] Compiling native C# DofusFarm.exe...");
            Console.ResetColor();
            var farmSources = new List<string>(11)
            {
                Path.Combine(desktopDir, "Win32.cs"),
                Path.Combine(desktopDir, "Hardware.cs"),
                Path.Combine(desktopDir, "IClientBackend.cs"),
                Path.Combine(desktopDir, "EmulatorBackend.cs"),
                Path.Combine(desktopDir, "WebView2ClientForm.cs"),
                Path.Combine(desktopDir, "WebView2Backend.cs"),
                Path.Combine(desktopDir, "FarmEngine.cs"),
                Path.Combine(desktopDir, "Watchdog.cs"),
                Path.Combine(desktopDir, "PriorityGovernor.cs"),
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
            string setupIconArg = File.Exists(setupIco) ? string.Format("\"/win32icon:{0}\" ", setupIco) : "";
            string uninstArgs = string.Format("/nologo /target:winexe /optimize+ {0}\"/out:{1}\" {2}\"{3}\"",
                setupIconArg, outUninstall, RefArgs(), uninstallerCs);

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
                foreach (string rs in new[] { "mobile-disguise.js", "apply-zram.sh" })
                {
                    string src = Path.Combine(RepoRoot, "scripts", rs);
                    if (File.Exists(src))
                        File.Copy(src, Path.Combine(stageScripts, rs), true);
                }

                string repoBin = Path.Combine(RepoRoot, @"scripts\bin");
                if (Directory.Exists(repoBin) && includeLegacyEmulator)
                {
                    string stageBin = Path.Combine(stageScripts, "bin");
                    Directory.CreateDirectory(stageBin);
                    foreach (string f in Directory.GetFiles(repoBin))
                        File.Copy(f, Path.Combine(stageBin, Path.GetFileName(f)), true);
                }

                foreach (string b in new[] { "DofusFarm.exe", "uninstall.exe", "dofus-net-proxy.exe", "app_icon.ico", "installer_icon.ico", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll" })
                {
                    string p = Path.Combine(RepoRoot, b);
                    if (!File.Exists(p)) p = Path.Combine(RepoRoot, "scripts", b);
                    if (!File.Exists(p)) p = Path.Combine(RepoRoot, "desktop", "lib", "webview2", b);
                    if (File.Exists(p)) File.Copy(p, Path.Combine(stageDir, b), true);
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

                // Stage native loader runtimes
                string repoRuntimes = Path.Combine(RepoRoot, "runtimes");
                if (Directory.Exists(repoRuntimes))
                {
                    string stageRuntimes = Path.Combine(stageDir, "runtimes");
                    Directory.CreateDirectory(stageRuntimes);
                    foreach (string dir in Directory.GetDirectories(repoRuntimes, "*", SearchOption.AllDirectories))
                        Directory.CreateDirectory(dir.Replace(repoRuntimes, stageRuntimes));
                    foreach (string file in Directory.GetFiles(repoRuntimes, "*", SearchOption.AllDirectories))
                        File.Copy(file, file.Replace(repoRuntimes, stageRuntimes), true);
                }

                // Stage standalone Dofus Touch client bundle
                string repoClient = Path.Combine(RepoRoot, "desktop", "client");
                if (Directory.Exists(repoClient))
                {
                    string stageClient = Path.Combine(stageDir, "client");
                    Directory.CreateDirectory(stageClient);
                    foreach (string dir in Directory.GetDirectories(repoClient, "*", SearchOption.AllDirectories))
                        Directory.CreateDirectory(dir.Replace(repoClient, stageClient));
                    foreach (string file in Directory.GetFiles(repoClient, "*", SearchOption.AllDirectories))
                        File.Copy(file, file.Replace(repoClient, stageClient), true);
                }

                // Stage dofus-network AI engine & trained TRM-MoE checkpoint
                string repoNetwork = Path.Combine(RepoRoot, "dofus-network");
                if (Directory.Exists(repoNetwork))
                {
                    string stageNetwork = Path.Combine(stageDir, "dofus-network");
                    Directory.CreateDirectory(stageNetwork);
                    foreach (string dir in Directory.GetDirectories(repoNetwork, "*", SearchOption.AllDirectories))
                    {
                        if (dir.Contains("__pycache__") || dir.Contains(".git") || dir.Contains(".pytest_cache")) continue;
                        Directory.CreateDirectory(dir.Replace(repoNetwork, stageNetwork));
                    }
                    foreach (string file in Directory.GetFiles(repoNetwork, "*", SearchOption.AllDirectories))
                    {
                        if (file.EndsWith(".pyc") || file.Contains("__pycache__")) continue;
                        string destFile = file.Replace(repoNetwork, stageNetwork);
                        string parent = Path.GetDirectoryName(destFile);
                        if (!Directory.Exists(parent)) Directory.CreateDirectory(parent);
                        File.Copy(file, destFile, true);
                    }
                }

                // Write requirements.txt and helper scripts
                string reqPath = Path.Combine(stageDir, "requirements.txt");
                File.WriteAllText(reqPath, "torch>=2.0.0\r\ntransformers>=4.48.0\r\nthx01>=1.0.0\r\nlaya>=0.4.0\r\nsafetensors>=0.4.0\r\n");

                string sentinelBat = Path.Combine(stageDir, "start-sentinel.bat");
                File.WriteAllText(sentinelBat, "@echo off\r\ncd /d \"%~dp0\"\r\ntitle Dofus Farm AI Sentinel Daemon\r\nset \"PY_EXE=\"\r\nif exist runtimes\\python\\python.exe set \"PY_EXE=runtimes\\python\\python.exe\"\r\nif not defined PY_EXE if exist python\\python.exe set \"PY_EXE=python\\python.exe\"\r\nif not defined PY_EXE set \"PY_EXE=python\"\r\necho Starting Singleton AI Sentinel on http://127.0.0.1:8890 using %PY_EXE%...\r\n\"%PY_EXE%\" dofus-network\\networkhealth\\sentinel.py\r\npause\r\n");

                string installPyBat = Path.Combine(stageDir, "install-python-ai.bat");
                File.WriteAllText(installPyBat, "@echo off\r\ncd /d \"%~dp0\"\r\necho ====================================================\r\necho   DOFUS FARM AI SENTINEL PYTHON DEPENDENCY SETUP   \r\necho ====================================================\r\nset \"PY_EXE=\"\r\nif exist runtimes\\python\\python.exe set \"PY_EXE=runtimes\\python\\python.exe\"\r\nif not defined PY_EXE if exist python\\python.exe set \"PY_EXE=python\\python.exe\"\r\nif not defined PY_EXE where python >nul 2>nul && set \"PY_EXE=python\"\r\nif not defined PY_EXE (\r\n  echo Python not found! Please download and install Python 3.11+:\r\n  echo https://www.python.org/downloads/\r\n  pause\r\n  exit /b 1\r\n)\r\necho Using Python runtime: %PY_EXE%\r\necho Installing required AI packages (torch, transformers, laya)...\r\n\"%PY_EXE%\" -m pip install -r requirements.txt --no-warn-script-location\r\necho.\r\necho [SUCCESS] All Python AI libraries installed!\r\npause\r\n");

                if (File.Exists(payloadZip)) File.Delete(payloadZip);
                ZipFile.CreateFromDirectory(stageDir, payloadZip, CompressionLevel.Fastest, false);

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
                    "  <assemblyIdentity version=\"2.3.0.0\" name=\"DofusFarmSetup\"/>\n" +
                    "  <trustInfo xmlns=\"urn:schemas-microsoft-com:asm.v3\">\n" +
                    "    <security>\n" +
                    "      <requestedPrivileges xmlns=\"urn:schemas-microsoft-com:asm.v3\">\n" +
                    "        <requestedExecutionLevel level=\"asInvoker\" uiAccess=\"false\" />\n" +
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
                    Path.Combine(desktopDir, "IClientBackend.cs"),
                    Path.Combine(desktopDir, "PriorityGovernor.cs"),
                    Path.Combine(desktopDir, "FarmEngine.cs"),
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
    }
}
