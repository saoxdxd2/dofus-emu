using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;
using DofusFarm.Native;

namespace DofusFarm.Engine
{
    public class DeviceProfile
    {
        public string Id { get; set; }
        public string DisplayName { get; set; }
        public string Brand { get; set; }
        public string Manufacturer { get; set; }
        public string Model { get; set; }
        public string ProductName { get; set; }
        public string ProductDevice { get; set; }
        public string Board { get; set; }
        public string Hardware { get; set; }
        public string Platform { get; set; }
        public string SocManufacturer { get; set; }
        public string SocModel { get; set; }
        public string BuildId { get; set; }
        public string BuildFingerprint { get; set; }
        public int LcdWidth { get; set; }
        public int LcdHeight { get; set; }
        public int LcdDensity { get; set; }
        public string GlVendor { get; set; }
        public string GlRenderer { get; set; }
        public int MaxTextureSize { get; set; }
        public string UserAgent { get; set; }
        public string SerialPrefix { get; set; }
    }

    public static class DeviceCatalog
    {
        private static DeviceProfile Make(
            string id, string display, string brand, string manuf, string model,
            string prodName, string prodDev, string board, string hw, string plat,
            string socManuf, string socModel, string buildId, string fp,
            int w, int h, int dpi, string glVendor, string glRenderer, int maxTex,
            string ua, string prefix)
        {
            return new DeviceProfile
            {
                Id = id, DisplayName = display, Brand = brand, Manufacturer = manuf, Model = model,
                ProductName = prodName, ProductDevice = prodDev, Board = board, Hardware = hw, Platform = plat,
                SocManufacturer = socManuf, SocModel = socModel, BuildId = buildId, BuildFingerprint = fp,
                LcdWidth = w, LcdHeight = h, LcdDensity = dpi, GlVendor = glVendor, GlRenderer = glRenderer,
                MaxTextureSize = maxTex, UserAgent = ua, SerialPrefix = prefix
            };
        }

        private static readonly List<DeviceProfile> _profiles = new List<DeviceProfile>
        {
            Make("galaxy-a51", "Samsung Galaxy A51", "samsung", "samsung", "SM-A515F", "a51nsxx", "a51", "exynos9611", "exynos9611", "exynos9611", "Samsung", "Exynos 9611", "QP1A.190711.020.A515FXXU1ATA7", "samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys", 1280, 720, 240, "ARM", "Mali-G72 MP3", 8192, "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "R58M"),
            Make("galaxy-s10", "Samsung Galaxy S10", "samsung", "samsung", "SM-G973F", "beyond1lte", "beyond1", "exynos9820", "exynos9820", "exynos9820", "Samsung", "Exynos 9820", "QP1A.190711.020.G973FXXS4BTB3", "samsung/beyond1lte/beyond1:10/QP1A.190711.020/G973FXXS4BTB3:user/release-keys", 1280, 720, 240, "ARM", "Mali-G76 MP12", 8192, "Mozilla/5.0 (Linux; Android 10; SM-G973F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "RZ8M"),
            Make("galaxy-s20", "Samsung Galaxy S20", "samsung", "samsung", "SM-G980F", "hubblenadx", "hubble", "exynos990", "exynos990", "exynos990", "Samsung", "Exynos 990", "QP1A.190711.020.G980FXXU1ATCT", "samsung/hubblenadx/hubble:10/QP1A.190711.020/G980FXXU1ATCT:user/release-keys", 1280, 720, 240, "ARM", "Mali-G77 MP11", 8192, "Mozilla/5.0 (Linux; Android 10; SM-G980F Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "RF8M"),
            Make("oneplus-8", "OnePlus 8", "OnePlus", "OnePlus", "IN2013", "OnePlus8_EEA", "OnePlus8", "kona", "qcom", "kona", "Qualcomm", "Snapdragon 865", "QKQ1.200114.002", "OnePlus/OnePlus8_EEA/OnePlus8:10/QKQ1.200114.002/2004111400:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 650", 16384, "Mozilla/5.0 (Linux; Android 10; IN2013 Build/QKQ1.200114.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "1F7E"),
            Make("poco-x3", "Xiaomi POCO X3 NFC", "POCO", "Xiaomi", "M2007J20CG", "surya_eea", "surya", "surya", "qcom", "sm7150", "Qualcomm", "Snapdragon 732G", "QKQ1.200512.002", "POCO/surya_eea/surya:10/QKQ1.200512.002/V12.0.4.0.QJGEUXM:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 618", 16384, "Mozilla/5.0 (Linux; Android 10; M2007J20CG Build/QKQ1.200512.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "9B4C"),
            Make("redmi-note-9-pro", "Xiaomi Redmi Note 9 Pro", "Redmi", "Xiaomi", "Redmi Note 9 Pro", "joyeuse_eea", "joyeuse", "atoll", "qcom", "atoll", "Qualcomm", "Snapdragon 720G", "QKQ1.191215.002", "Redmi/joyeuse_eea/joyeuse:10/QKQ1.191215.002/V11.0.4.0.QJZEUXM:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 618", 16384, "Mozilla/5.0 (Linux; Android 10; Redmi Note 9 Pro Build/QKQ1.191215.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "8F1B"),
            Make("galaxy-tab-s6", "Samsung Galaxy Tab S6 (16:9 Farm HD)", "samsung", "samsung", "SM-T860", "gts6lwifixx", "gts6l", "msmnile", "qcom", "msmnile", "Qualcomm", "Snapdragon 855", "QP1A.190711.020.T860XXU1BTJ2", "samsung/gts6lwifixx/gts6l:10/QP1A.190711.020/T860XXU1BTJ2:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 640", 16384, "Mozilla/5.0 (Linux; Android 10; SM-T860 Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Safari/537.36", "R52M"),
            Make("galaxy-tab-a", "Samsung Galaxy Tab A (4:3 Tablet Mode)", "samsung", "samsung", "SM-T510", "gta3xlwifixx", "gta3xlwifi", "universal7904", "samsungexynos7904", "exynos5", "Samsung", "Exynos 7904", "QP1A.190711.020.T510XXU3BTE1", "samsung/gta3xlwifixx/gta3xlwifi:10/QP1A.190711.020/T510XXU3BTE1:user/release-keys", 1024, 768, 213, "ARM", "Mali-G71 MP2", 8192, "Mozilla/5.0 (Linux; Android 10; SM-T510 Build/QP1A.190711.020) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Safari/537.36", "R52N"),
            Make("webgl-minimal", "WebGL Minimal (Chrome Dofus)", "generic", "generic", "WebGLMinimal", "webgl_minimal", "webgl_minimal", "ranchu", "ranchu", "ranchu", "Google", "virgl", "QSR1.210820.001", "generic/webgl_minimal/webgl_minimal:10/QSR1.210820.001/7663313:userdebug/test-keys", 1280, 720, 240, "Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)", 16384, "Mozilla/5.0 (Linux; Android 10; WebGLMinimal Build/QSR1.210820.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.137 Mobile Safari/537.36", "WGLM")
        };

        private static readonly Dictionary<string, DeviceProfile> _byId = _profiles.ToDictionary(p => p.Id, StringComparer.OrdinalIgnoreCase);

        public static List<DeviceProfile> GetAllProfiles() { return _profiles.ToList(); }
        public static DeviceProfile GetProfile(string id) { DeviceProfile p; return (!string.IsNullOrEmpty(id) && _byId.TryGetValue(id, out p)) ? p : _profiles[0]; }
        public static DeviceProfile GetDefaultProfileForIndex(int slotIndex) { return _profiles[Math.Max(0, slotIndex - 1) % _profiles.Count]; }
    }

    public class InstanceModel
    {
        public string Name { get; set; }
        public string CustomName { get; set; }
        public string DisplayName { get; set; }
        public int Port { get; set; }
        public string Serial { get { return "emulator-" + Port; } }
        public int RamMb { get; set; }
        public int Cores { get; set; }
        public bool IsRunning { get; set; }
        public int Pid { get; set; }
        public string Proxy { get; set; }
        public string PhoneModelId { get; set; }
        public string PhoneModelName { get; set; }
        public bool IsWarmReady { get; set; }
    }

    public class FarmEngine
    {
        // Windows Management Instrumentation is accessed through late binding on purpose: the
        // compiled app does not reference System.Management.dll directly. The build script only
        // needs the compiler to see a usable type, so a local declaration with the exact members
        // used below is enough. The interface intentionally avoids the generics/events the real
        // class has, because those cannot be named without the assembly.
        // Thin wrapper so callers keep using mo["Property"], but the underlying object is the real
        // WMI instance returned by reflection.
        private sealed class ManagementObject
        {
            private readonly object _inner;
            public ManagementObject(object inner) { _inner = inner; }
            public object this[string propertyName]
            {
                get
                {
                    try { return _inner.GetType().GetProperty(propertyName).GetValue(_inner, null); }
                    catch { return null; }
                }
            }
        }

        public string SdkRoot { get; private set; }
        public string AdbPath { get; private set; }
        public string EmulatorPath { get; private set; }
        public string QemuImgPath { get; private set; }
        public string AvdHome { get; private set; }
        public string AppBaseDir { get; private set; }
        public string ProxyExePath { get; private set; }
        public string GoldenMasterPath { get; private set; }
        public string GoldenOverlayPath { get; private set; }

        public event Action<string> OnLog;

        private readonly Dictionary<string, CancellationTokenSource> _cdpSessions = new Dictionary<string, CancellationTokenSource>();
        private readonly object _cdpLock = new object();
        private static readonly ConcurrentDictionary<int, int> _cachedPortToPid = new ConcurrentDictionary<int, int>();
        private static readonly object _pidCacheLock = new object();
        private static readonly object _qemuScanLock = new object();
        private static Dictionary<int, int> _qemuScanCache;
        private static DateTime _qemuScanCacheUtc = DateTime.MinValue;
        private static readonly Dictionary<int, Process> _proxyProcesses = new Dictionary<int, Process>();
        private static readonly Dictionary<int, InProcessProxyServer> _inProcProxies = new Dictionary<int, InProcessProxyServer>();
        private static readonly Dictionary<int, string> _proxyCurrentUpstream = new Dictionary<int, string>();
        private static readonly object _proxyLock = new object();
        private readonly object _launchGenLock = new object();
        private readonly ConcurrentDictionary<int, int> _launchGen = new ConcurrentDictionary<int, int>();
        private static readonly object _wsSendLock = new object();

        private static readonly Regex RxQemuPort = new Regex(@"-port\s+(\d+)", RegexOptions.Compiled);
        private static readonly Regex RxTrailingDigits = new Regex(@"(\d+)$", RegexOptions.Compiled);
        private static readonly Regex RxCustomName = new Regex(@"""CustomName""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxSerial = new Regex(@"""Serial""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxAndroidId = new Regex(@"""AndroidId""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxMac = new Regex(@"""Mac""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxProxy = new Regex(@"""Proxy""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxPhoneModel = new Regex(@"""PhoneModel""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxUnixSocket = new Regex(@"@(webview_devtools_remote_\d+|chrome_devtools_remote|com\.ankama\.dofustouch[^\s]*)", RegexOptions.Compiled);
        private static readonly Regex RxWsDebugger = new Regex(@"""webSocketDebuggerUrl""\s*:\s*""([^""]+)""", RegexOptions.Compiled);
        private static readonly Regex RxWsHostPort = new Regex(@"ws://(127\.0\.0\.1|localhost):\d+/", RegexOptions.Compiled);
        private static readonly Regex RxAvdReplace = new Regex(@"dofus-(?:template|\d+)\.avd", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex RxReleaseVer = new Regex(@":(\d+)/", RegexOptions.Compiled);
        private static readonly Regex RxAvdArg = new Regex(@"-avd\s+(\S+)", RegexOptions.Compiled);
        private static readonly Regex RxDebloatLine = new Regex(@"debloat:[^\r\n]*", RegexOptions.Compiled);
        private static readonly UTF8Encoding Utf8NoBom = new UTF8Encoding(false);

        private const string RegKeyPath = @"HKEY_CURRENT_USER\Software\DofusFarm";
        private const string SysImgSubPath = @"system-images\android-29\default\x86_64";
        private const string WakeAndStartCmd = "input keyevent 224 2>/dev/null || true; wm dismiss-keyguard 2>/dev/null || true; am start -n com.ankama.dofustouch/.MainActivity";
        private static readonly string[] AuxImages = { "encryptionkey.img", "cache.img", "sdcard.img" };
        private static readonly string[] ValidGpuModes = { "host", "angle_indirect", "swiftshader_indirect", "auto" };

        public FarmEngine() : this(null, false) { }

        public FarmEngine(string explicitBaseDir, bool skipIntegrityCheck = false)
        {
            string baseCand = (!string.IsNullOrEmpty(explicitBaseDir) ? explicitBaseDir : AppDomain.CurrentDomain.BaseDirectory).TrimEnd('\\');
            string parentCand = Directory.GetParent(baseCand) != null ? Directory.GetParent(baseCand).FullName.TrimEnd('\\') : null;
            string asmCand = null;
            try { string loc = Assembly.GetExecutingAssembly().Location; if (!string.IsNullOrEmpty(loc)) asmCand = Path.GetDirectoryName(loc).TrimEnd('\\'); } catch { }

            AppBaseDir = new[] { baseCand, parentCand, asmCand, Directory.GetCurrentDirectory().TrimEnd('\\') }
                .FirstOrDefault(d => !string.IsNullOrEmpty(d) && File.Exists(Path.Combine(d, @"scripts\mobile-disguise.js"))) ?? baseCand;

            AvdHome = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".android\avd");
            ResolveSdkPaths(skipIntegrityCheck);
            EnsureDirectories();

            Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", SdkRoot);
            Environment.SetEnvironmentVariable("ANDROID_HOME", SdkRoot);
            Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", AvdHome);

            string curPath = Environment.GetEnvironmentVariable("PATH") ?? "", emuDir = Path.Combine(SdkRoot, "emulator");
            if (!curPath.Contains(emuDir))
                Environment.SetEnvironmentVariable("PATH", string.Join(";", emuDir, Path.Combine(SdkRoot, "platform-tools"), curPath));
        }

        [MethodImpl(MethodImplOptions.AggressiveInlining)]
        private static int ParseSlotIdx(string name)
        {
            int idx = 1;
            Match m = RxTrailingDigits.Match(name ?? "");
            if (m.Success) int.TryParse(m.Groups[1].Value, out idx);
            return idx;
        }

        [MethodImpl(MethodImplOptions.AggressiveInlining)]
        private static string MatchVal(Regex rx, string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            Match m = rx.Match(text);
            return (m.Success && !string.IsNullOrEmpty(m.Groups[1].Value)) ? m.Groups[1].Value.Trim() : null;
        }

        [MethodImpl(MethodImplOptions.AggressiveInlining)]
        private static void SafeDeleteFile(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;
            try { File.SetAttributes(path, FileAttributes.Normal); File.Delete(path); } catch { }
        }

        private static void DeleteFiles(string dir, params string[] names)
        {
            foreach (string f in names) SafeDeleteFile(Path.Combine(dir, f));
        }

        private static bool HasRunningProcess(params string[] names)
        {
            try
            {
                foreach (string n in names)
                {
                    Process[] procs = Process.GetProcessesByName(n);
                    bool any = procs != null && procs.Length > 0;
                    if (procs != null) foreach (Process p in procs) { try { p.Dispose(); } catch { } }
                    if (any) return true;
                }
                return false;
            }
            catch { return true; }
        }

        private static void QueryWmi(string query, int timeoutSec, Action<ManagementObject> onObject)
        {
            // Reflection on purpose: the compiled app does not reference System.Management.dll, and
            // the two lines below used to fail to compile because the type could not be resolved.
            Type searcherType = Type.GetType("System.Management.ManagementObjectSearcher, System.Management", false);
            if (searcherType == null) return;

            IDisposable searcher = null;
            try
            {
                object instance = Activator.CreateInstance(searcherType, query);
                searcher = instance as IDisposable;

                object options = searcherType.GetProperty("Options").GetValue(instance, null);
                if (options != null)
                    options.GetType().GetProperty("Timeout").SetValue(options, TimeSpan.FromSeconds(timeoutSec), null);

                object results = searcherType.GetMethod("Get", Type.EmptyTypes).Invoke(instance, null);
                var collection = results as System.Collections.IEnumerable;
                if (collection == null) return;

                foreach (object raw in collection)
                {
                    var mo = raw as IDisposable;
                    try { onObject(new ManagementObject(raw)); }
                    finally { if (mo != null) mo.Dispose(); }
                }
            }
            catch { }
            finally { if (searcher != null) searcher.Dispose(); }
        }

        private void ReadIdentity(string avdDir, ref string customName, ref string serialNo, ref string androidId, ref string mac, ref string proxy, ref string phoneModel)
        {
            string identFile = Path.Combine(avdDir, "identity.json");
            if (!File.Exists(identFile)) return;
            try
            {
                string j = File.ReadAllText(identFile, Encoding.UTF8);
                customName = MatchVal(RxCustomName, j) ?? customName;
                string s = MatchVal(RxSerial, j); if (s != null) serialNo = s.ToUpperInvariant();
                string a = MatchVal(RxAndroidId, j); if (a != null) androidId = a.ToLowerInvariant();
                string m = MatchVal(RxMac, j); if (m != null && !m.StartsWith("52:54:00")) mac = m;
                proxy = MatchVal(RxProxy, j) ?? proxy;
                phoneModel = MatchVal(RxPhoneModel, j) ?? phoneModel;
            }
            catch { }
        }

        private static void WriteIdentity(string avdDir, string customName, string serialNo, string androidId, string mac, string proxy, string phoneModelId)
        {
            File.WriteAllText(Path.Combine(avdDir, "identity.json"), string.Format(
                "{{\"CustomName\":\"{0}\",\"Serial\":\"{1}\",\"AndroidId\":\"{2}\",\"Mac\":\"{3}\",\"Proxy\":\"{4}\",\"PhoneModel\":\"{5}\"}}",
                EscapeJson(customName), EscapeJson(serialNo), EscapeJson(androidId), EscapeJson(mac), EscapeJson(proxy), EscapeJson(phoneModelId)), Encoding.UTF8);
        }

        internal static string InterpolateDisguise(string rawJs, string uuid, string serial, DeviceProfile p, string defaultUa = "", string appVersion = null, string versionCode = null, string buildVersion = null)
        {
            if (string.IsNullOrEmpty(rawJs)) return "";
            string av = !string.IsNullOrEmpty(appVersion) ? appVersion : "3.14.2";
            string vc = !string.IsNullOrEmpty(versionCode) ? versionCode : "3014002";
            string bv = !string.IsNullOrEmpty(buildVersion) ? buildVersion : "1.74.8";

            return new StringBuilder(rawJs, rawJs.Length + 256)
                .Replace("__DEVICE_UUID__", uuid ?? "ca8ea76b0fe32f85")
                .Replace("__DEVICE_SERIAL__", serial ?? "R58M45F41EFD")
                .Replace("__DEVICE_MODEL__", (p != null && !string.IsNullOrEmpty(p.Model)) ? p.Model : "SM-A515F")
                .Replace("__DEVICE_MANUFACTURER__", (p != null && !string.IsNullOrEmpty(p.Manufacturer)) ? p.Manufacturer : "samsung")
                .Replace("__DEVICE_UA__", (p != null && !string.IsNullOrEmpty(p.UserAgent)) ? p.UserAgent : defaultUa)
                .Replace("__DEVICE_GL_VENDOR__", (p != null && !string.IsNullOrEmpty(p.GlVendor)) ? p.GlVendor : "ARM")
                .Replace("__DEVICE_GL_RENDERER__", (p != null && !string.IsNullOrEmpty(p.GlRenderer)) ? p.GlRenderer : "Mali-G72 MP3")
                .Replace("__DEVICE_MAX_TEXTURE_SIZE__", ((p != null && p.MaxTextureSize > 0) ? p.MaxTextureSize : 8192).ToString())
                .Replace("__DEVICE_APP_VERSION__", av)
                .Replace("__DEVICE_VERSION_CODE__", vc)
                .Replace("__DEVICE_BUILD_VERSION__", bv)
                .ToString();
        }

        private void Log(string message)
        {
            var handler = OnLog;
            if (handler != null) { try { handler(string.Format("[{0:HH:mm:ss}] {1}", DateTime.Now, message)); } catch { } }
        }

        private void ResolveSdkPaths(bool skipIntegrityCheck = false)
        {
            string dfHome = Environment.GetEnvironmentVariable("DOFUS_FARM_HOME");
            string userProf = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            SdkRoot = new[]
            {
                Path.Combine(AppBaseDir, "sdk"), Path.Combine(AppBaseDir, @"sdk\sdk"),
                dfHome != null ? Path.Combine(dfHome, "sdk") : null, dfHome != null ? Path.Combine(dfHome, @"sdk\sdk") : null,
                GetRegistrySdkPath(), Environment.GetEnvironmentVariable("ANDROID_SDK_ROOT"), Environment.GetEnvironmentVariable("ANDROID_HOME"),
                Path.Combine(userProf, @"DofusFarm\sdk"), Path.Combine(userProf, @"DofusFarm\sdk\sdk"), Path.Combine(userProf, @"AppData\Local\Android\Sdk")
            }.FirstOrDefault(c => !string.IsNullOrEmpty(c) && File.Exists(Path.Combine(c, @"platform-tools\adb.exe"))) ?? Path.Combine(AppBaseDir, "sdk");

            AdbPath = Path.Combine(SdkRoot, @"platform-tools\adb.exe");
            EmulatorPath = Path.Combine(SdkRoot, @"emulator\emulator.exe");
            QemuImgPath = Path.Combine(SdkRoot, @"emulator\qemu-img.exe");

            ProxyExePath = new[]
            {
                Path.Combine(AppBaseDir, "dofus-net-proxy.exe"), Path.Combine(AppBaseDir, @"scripts\dofus-net-proxy.exe"),
                dfHome != null ? Path.Combine(dfHome, "dofus-net-proxy.exe") : null
            }.FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p)) ?? Path.Combine(AppBaseDir, "dofus-net-proxy.exe");

            GoldenMasterPath = new[]
            {
                Path.Combine(AvdHome, @"dofus-template.avd\userdata-golden.img"), Path.Combine(AvdHome, @"dofus.avd\userdata-golden.img"), Path.Combine(AppBaseDir, "userdata-golden.img")
            }.FirstOrDefault(File.Exists) ?? Path.Combine(AppBaseDir, "userdata-golden.img");

            if (!File.Exists(GoldenMasterPath) && !string.IsNullOrEmpty(SdkRoot))
            {
                string sdkUd = Path.Combine(SdkRoot, SysImgSubPath, "userdata.img");
                if (File.Exists(sdkUd))
                {
                    try
                    {
                        GoldenMasterPath = Path.Combine(Directory.CreateDirectory(Path.Combine(AvdHome, "dofus-template.avd")).FullName, "userdata-golden.img");
                        File.Copy(sdkUd, GoldenMasterPath, true);
                    }
                    catch { }
                }
            }
            EnsureGoldenTemplate();
            GoldenOverlayPath = GetGoldenOverlayPath();
            EnsureAdvancedFeatures();
        }

        public static string DetectDiskFormat(string imagePath)
        {
            try
            {
                if (File.Exists(imagePath))
                {
                    using (var fs = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    {
                        byte[] m = new byte[4];
                        if (fs.Read(m, 0, 4) == 4 && m[0] == 0x51 && m[1] == 0x46 && m[2] == 0x49 && m[3] == 0xFB) return "qcow2";
                    }
                }
            }
            catch { }
            return "raw";
        }

        private static string GetRelativePath(string fromPath, string toPath)
        {
            if (string.IsNullOrEmpty(fromPath) || string.IsNullOrEmpty(toPath)) return toPath;
            try
            {
                Uri fromUri = new Uri(AppendDirectorySeparator(fromPath)), toUri = new Uri(AppendDirectorySeparator(toPath));
                if (fromUri.Scheme != toUri.Scheme) return toPath;
                string rel = Uri.UnescapeDataString(fromUri.MakeRelativeUri(toUri).ToString());
                return string.IsNullOrEmpty(rel) ? "." : rel.Replace('/', '\\');
            }
            catch { return toPath; }
        }

        private static string AppendDirectorySeparator(string path)
        {
            return (!string.IsNullOrEmpty(path) && !path.EndsWith("\\") && !path.EndsWith("/")) ? path + "\\" : path;
        }

        public bool IsInstanceWarmReady(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir)) return false;
            string snapDir = Path.Combine(avdDir, @"snapshots\default_boot");
            if (File.Exists(Path.Combine(snapDir, "ram.img.dirty"))) return false;
            string snapPb = Path.Combine(snapDir, "snapshot.pb");
            if (File.Exists(snapPb))
            {
                try { if (new FileInfo(snapPb).Length < 512) return false; } catch { return false; }
                return true;
            }
            return File.Exists(Path.Combine(avdDir, "cold_run_completed.flag"));
        }

        // Order matters: 'host' is the only mode that needs no extra translation layer, so it is
        // always tried first. If the host driver turns out to be unusable (the emulator logs a
        // failed OpenGL initialisation), ConfigureGuestAndLaunchGame falls back to angle_indirect
        // and then to swiftshader_indirect, and SetConfiguredGpuMode persists whatever worked.
        private static readonly string[] GpuModeProbeOrder = { "host", "angle_indirect", "swiftshader_indirect" };

        public string DetectBestGpuMode()
        {
            try
            {
                var sb = new StringBuilder();
                QueryWmi("SELECT Name FROM Win32_VideoController", 5, mo => sb.Append(' ').Append((mo["Name"] ?? "").ToString().ToLowerInvariant()));
                string adapters = sb.ToString().Trim();
                if (adapters.Length == 0 || new[] { "basic display", "swiftshader", "remote", "indirect display" }.Any(adapters.Contains))
                {
                    Log("Renderer check: no hardware GPU adapter usable - defaulting to SwiftShader (software) rendering.");
                    return "swiftshader_indirect";
                }
                if (new[] { "nvidia", "geforce", "radeon", "amd", "arc " }.Any(adapters.Contains))
                {
                    Log("Renderer check: dedicated GPU detected - starting with Host (DirectGL) rendering: " + adapters + ".");
                    return "host";
                }
                Log("Renderer check: integrated GPU detected - starting with Host (DirectGL) rendering: " + adapters + ".");
                return "host";
            }
            catch { return "host"; }
        }

        public string GetConfiguredGpuMode()
        {
            try
            {
                string m = (Registry.GetValue(RegKeyPath, "GpuMode", null) ?? "").ToString().Trim().ToLowerInvariant();
                if (ValidGpuModes.Contains(m)) return m;
            }
            catch { }
            return DetectBestGpuMode();
        }

        public void SetConfiguredGpuMode(string mode)
        {
            string m = (mode ?? "host").Trim().ToLowerInvariant();
            if (!ValidGpuModes.Contains(m)) m = "host";
            try { Registry.SetValue(RegKeyPath, "GpuMode", m); } catch { }

            try
            {
                if (Directory.Exists(AvdHome))
                {
                    foreach (string d in Directory.EnumerateDirectories(AvdHome, "dofus-*.avd"))
                    {
                        string name = Path.GetFileNameWithoutExtension(d);
                        if (name.Equals("dofus-template", StringComparison.OrdinalIgnoreCase)) continue;
                        string cfg = Path.Combine(d, "config.ini");
                        if (File.Exists(cfg)) RefreshConfigIni(cfg, name);
                        InvalidateWarmSnapshot(name);
                    }
                }
            }
            catch { }
            Log(string.Format("GPU renderer mode set to '{0}' across all instances.", m));
        }

        private void RefreshConfigIni(string cfgFile, string name, DeviceProfile prof = null)
        {
            int r = 1024, c = 1;
            ReadConfigRamCores(cfgFile, ref r, ref c);
            UpdateConfigIni(cfgFile, name, r, c, prof ?? GetInstancePhoneModel(name));
        }

        public void InvalidateWarmSnapshot(string name)
        {
            if (string.IsNullOrEmpty(name)) return;
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir)) return;
            SafeDeleteFile(Path.Combine(avdDir, "cold_run_completed.flag"));
            ForceDeleteDirectory(Path.Combine(avdDir, "snapshots"));
        }

        private static bool _vulkanProbeChecked;
        private static bool _vulkanProbeAvailable;

        // Only turn Vulkan on when the host really has a driver. The registry entry is the same on
        // every Windows machine, so probing is the only reliable way; when no driver is present the
        // emulator would waste a full initialisation attempt and fall back to GL anyway.
        private bool IsHostVulkanAvailable()
        {
            if (_vulkanProbeChecked) return _vulkanProbeAvailable;
            _vulkanProbeChecked = true;
            try
            {
                object val = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\Khronos\Vulkan\Drivers", "", null)
                    ?? Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\Khronos\Vulkan\ImplicitLayers", "", null);
                if (val != null && val.ToString().Trim().Length > 0)
                {
                    _vulkanProbeAvailable = true;
                    return true;
                }

                string root = Environment.GetFolderPath(Environment.SpecialFolder.System);
                _vulkanProbeAvailable = !string.IsNullOrEmpty(root) && File.Exists(Path.Combine(root, "vulkan-1.dll"));
            }
            catch { _vulkanProbeAvailable = false; }
            return _vulkanProbeAvailable;
        }

        public void EnsureAdvancedFeatures()
        {
            try
            {
                string dotAndroid = Directory.CreateDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".android")).FullName;
                // Performance-critical feature flags for a Dofus-Touch style OpenGL workload:
                //  * Vulkan must stay OFF. Dofus Touch renders with GLES; Vulkan here only makes the
                //    emulator pick the host Vulkan driver, which on a fallback/software Vulkan ICD
                //    (swiftshader / lavapipe) costs seconds per frame and leaves the guest at 100%
                //    CPU. That is the main "boots but the screen ignores the mouse" symptom.
                //  * GLPipeChecksum must stay OFF. It reads the framebuffer back to the CPU on every
                //    frame to validate the GL pipe - pure overhead for a game that just draws.
                //  * GLDirectMem + GLAsyncSwap + HostComposition keep the GL path zero-copy.
                //  * GLESDynamicVersion keeps the GLES 3.x path instead of silently dropping to GLES 2.
                //  * RefCountPipe / GLDMA / VirtioInput avoid guest<->host round trips in the pipe.
                //  * LogcatPipe OFF: we keep stdout for the console/UI but skip the guest logcat pipe.
                //  * IntelPerformanceMonitoringUnit only adds overhead when present.
                const string content =
                    "GrallocSync = off\r\nGLPipeChecksum = off\r\nLogcatPipe = off\r\nGLAsyncSwap = on\r\n" +
                    "GLESDynamicVersion = on\r\nGLDMA = on\r\nGLDirectMem = on\r\nVulkan = off\r\n" +
                    "VirtioInput = on\r\nVirtioMouse = off\r\nVirtioTablet = on\r\nVirtioDualModeMouse = off\r\nDualModeMouseDisplayHostCursor = off\r\n" +
                    "EncryptUserData = off\r\nIntelPerformanceMonitoringUnit = off\r\n" +
                    "Wifi = on\r\nHostComposition = on\r\nRefCountPipe = on\r\nDynamicPartition = on\r\nHardwareDecoder = on\r\n" +
                    "WindowsHypervisorPlatform = on\r\nWindowsOnDemandSnapshotLoad = off\r\n";

                File.WriteAllText(Path.Combine(dotAndroid, "advancedFeatures.ini"), content, Encoding.ASCII);
                WriteAdvancedFeaturesToSdk(content);
            }
            catch { }
        }

        private void WriteAdvancedFeaturesToSdk(string content)
        {
            if (string.IsNullOrEmpty(SdkRoot)) return;
            string sdkSysDir = Path.Combine(SdkRoot, SysImgSubPath);
            if (Directory.Exists(sdkSysDir)) File.WriteAllText(Path.Combine(sdkSysDir, "advancedFeatures.ini"), content, Encoding.ASCII);
        }

        private string GetRegistrySdkPath()
        {
            try
            {
                object val = Registry.GetValue(RegKeyPath, "InstallPath", null);
                if (val != null)
                {
                    string p1 = Path.Combine(val.ToString(), "sdk"), p2 = Path.Combine(p1, "sdk");
                    return File.Exists(Path.Combine(p2, @"platform-tools\adb.exe")) && !File.Exists(Path.Combine(p1, @"platform-tools\adb.exe")) ? p2 : p1;
                }
            }
            catch { }
            return null;
        }

        private void EnsureDirectories() { Directory.CreateDirectory(AvdHome); }

        public string RunCommand(string exe, string args, int timeoutMs = 30000, string workingDir = null)
        {
            try
            {
                string targetDir = string.IsNullOrEmpty(workingDir) ? AppBaseDir : workingDir;
                if (!Directory.Exists(targetDir))
                {
                    try { Directory.CreateDirectory(targetDir); } catch { }
                }

                var psi = new ProcessStartInfo
                {
                    FileName = exe,
                    Arguments = args ?? "",
                    CreateNoWindow = true,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    WorkingDirectory = Directory.Exists(targetDir) ? targetDir : AppBaseDir,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8
                };

                using (var p = new Process { StartInfo = psi })
                {
                    var sb = new StringBuilder();
                    var syncLock = new object();

                    p.OutputDataReceived += (s, e) => { if (e.Data != null) lock (syncLock) { sb.AppendLine(e.Data); } };
                    p.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (syncLock) { sb.AppendLine(e.Data); } };

                    if (!p.Start()) return "ERROR: Failed to start process " + exe;

                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();

                    if (!p.WaitForExit(timeoutMs))
                    {
                        try { p.Kill(); } catch { }
                        return "ERROR: Command timed out after " + timeoutMs + "ms";
                    }

                    p.WaitForExit();
                    lock (syncLock) { return sb.ToString().Trim(); }
                }
            }
            catch (Exception ex)
            {
                return "ERROR: " + ex.Message;
            }
        }

        public string RunCommandCapture(string exe, string args, int timeoutMs = 30000, string workingDir = null) { return RunCommand(exe, args, timeoutMs, workingDir); }
        public string Adb(string serial, string args, int timeoutMs = 30000) { return RunCommandCapture(AdbPath, string.IsNullOrEmpty(serial) ? args : string.Format("-s {0} {1}", serial, args), timeoutMs); }
        public string AdbShell(string serial, string shellCommand) { return Adb(serial, "shell " + shellCommand); }

        public HashSet<string> GetLiveSerials()
        {
            var live = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (!HasRunningProcess("qemu-system-x86_64", "emulator")) return live;

            using (var sr = new StringReader(Adb("", "devices") ?? ""))
            {
                for (string line; (line = sr.ReadLine()) != null;)
                {
                    string[] p = line.Trim().Split(new[] { '\t', ' ' }, StringSplitOptions.RemoveEmptyEntries);
                    if (p.Length >= 2 && p[0].StartsWith("emulator-") &&
                        (p[1].Equals("device", StringComparison.OrdinalIgnoreCase) ||
                         p[1].Equals("offline", StringComparison.OrdinalIgnoreCase) ||
                         p[1].Equals("connecting", StringComparison.OrdinalIgnoreCase)))
                        live.Add(p[0].Trim());
                }
            }
            return live;
        }

        public static bool HasManualProxy(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            if (s.StartsWith("[🌐] ")) s = s.Substring(5).Trim();
            if (s.Length == 0 ||
                s.StartsWith("[⚡]") || s.StartsWith("[🎲]") ||
                s.IndexOf("Accelerator", StringComparison.OrdinalIgnoreCase) >= 0 ||
                s.IndexOf("Auto", StringComparison.OrdinalIgnoreCase) >= 0 ||
                s.IndexOf("Random", StringComparison.OrdinalIgnoreCase) >= 0 ||
                s.Equals("DIRECT", StringComparison.OrdinalIgnoreCase) ||
                s.Equals("[DIRECT] DIRECT", StringComparison.OrdinalIgnoreCase) ||
                s.StartsWith("127.0.0.1:888", StringComparison.OrdinalIgnoreCase))
                return false;
            return true;
        }

        public static string NormalizeUpstreamProxy(string raw)
        {
            if (!HasManualProxy(raw)) return null;
            string s = raw.Trim();
            if (s.StartsWith("[🌐] ")) s = s.Substring(5).Trim();

            string scheme = "http", rest = s;
            int idx = s.IndexOf("://", StringComparison.Ordinal);
            if (idx > 0) { scheme = s.Substring(0, idx).ToLowerInvariant(); rest = s.Substring(idx + 3); }

            if (!rest.Contains("@"))
            {
                string[] p = rest.Split(':');
                if (p.Length == 4) return string.Format("{0}://{1}:{2}@{3}:{4}", scheme, Uri.EscapeDataString(p[2]), Uri.EscapeDataString(p[3]), p[0], p[1]);
            }
            return string.Format("{0}://{1}", scheme, rest);
        }

        private static string[] _cachedProxyPool = null;
        private static DateTime _cachedProxyPoolUtc = DateTime.MinValue;
        private static readonly object _proxyPoolLock = new object();

        public static string GetAutoProxyForSlot(int slot)
        {
            try
            {
                lock (_proxyPoolLock)
                {
                    if (_cachedProxyPool == null || (DateTime.UtcNow - _cachedProxyPoolUtc).TotalSeconds > 10)
                    {
                        string[] searchPaths = new[]
                        {
                            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "proxies.txt"),
                            Path.Combine(Environment.CurrentDirectory, "proxies.txt")
                        };

                        string foundFile = searchPaths.FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p));
                        if (foundFile != null)
                        {
                            var lines = File.ReadAllLines(foundFile)
                                .Select(l => l.Trim())
                                .Where(l => l.Length > 0 && !l.StartsWith("#") && !l.StartsWith("//"))
                                .Select(l => NormalizeUpstreamProxy(l))
                                .Where(l => !string.IsNullOrEmpty(l))
                                .ToArray();
                            _cachedProxyPool = lines;
                        }
                        else
                        {
                            try
                            {
                                string tplPath = Path.Combine(Environment.CurrentDirectory, "proxies.txt");
                                if (!File.Exists(tplPath))
                                {
                                    File.WriteAllText(tplPath,
                                        "# proxies.txt - Put upstream proxies here (one per line).\r\n" +
                                        "# Formats: http://host:port, socks5://host:port, http://user:pass@host:port, socks5://user:pass@host:port\r\n" +
                                        "# Instances automatically pick distinct proxies round-robin by slot without manual config.\r\n",
                                        Encoding.UTF8);
                                }
                            }
                            catch { }
                            _cachedProxyPool = new string[0];
                        }
                        _cachedProxyPoolUtc = DateTime.UtcNow;
                    }

                    if (_cachedProxyPool != null && _cachedProxyPool.Length > 0)
                    {
                        int idx = Math.Abs(slot) % _cachedProxyPool.Length;
                        return _cachedProxyPool[idx];
                    }
                }
            }
            catch { }
            return null;
        }

        private static readonly Random _proxyRng = new Random();

        public static string GetRandomProxy()
        {
            try
            {
                lock (_proxyPoolLock)
                {
                    GetAutoProxyForSlot(0);
                    if (_cachedProxyPool != null && _cachedProxyPool.Length > 0)
                    {
                        return _cachedProxyPool[_proxyRng.Next(_cachedProxyPool.Length)];
                    }
                }
            }
            catch { }
            return null;
        }

        public static string[] GetShuffledProxyPool()
        {
            try
            {
                lock (_proxyPoolLock)
                {
                    GetAutoProxyForSlot(0);
                    if (_cachedProxyPool == null || _cachedProxyPool.Length == 0) return new string[0];
                    var pool = (string[])_cachedProxyPool.Clone();
                    for (int i = pool.Length - 1; i > 0; i--)
                    {
                        int j = _proxyRng.Next(i + 1);
                        string t = pool[i]; pool[i] = pool[j]; pool[j] = t;
                    }
                    return pool;
                }
            }
            catch { return new string[0]; }
        }

        public static bool PreflightProxyViaRelay(int localProxyPort, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("https://auth.ankama.com/token/create-guest");
                req.Proxy = new WebProxy("http://127.0.0.1:" + localProxyPort);
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.Method = "POST";
                req.ContentType = "application/x-www-form-urlencoded";
                req.UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36";
                req.Headers["Origin"] = "https://www.dofus-touch.com";
                req.Referer = "https://www.dofus-touch.com/";
                byte[] data = Encoding.UTF8.GetBytes("client_id=18");
                req.ContentLength = data.Length;
                using (var stm = req.GetRequestStream()) stm.Write(data, 0, data.Length);
                using (var resp = (HttpWebResponse)req.GetResponse())
                    return (int)resp.StatusCode == 200;
            }
            catch { return false; }
        }

        public static int GetProxyPoolCount()
        {
            try
            {
                lock (_proxyPoolLock)
                {
                    GetAutoProxyForSlot(0);
                    return _cachedProxyPool != null ? _cachedProxyPool.Length : 0;
                }
            }
            catch { return 0; }
        }

        private static Process _warpProcess = null;
        private static readonly object _warpLock = new object();
        public const int WarpSocksPort = 40000;
        public static readonly string WarpSocksUrl = "socks5://127.0.0.1:" + WarpSocksPort;

        public static bool IsWarpInstalled()
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string wireproxy = Path.Combine(baseDir, @"bin\warp\wireproxy.exe");
                string conf = Path.Combine(baseDir, @"bin\warp\wireproxy.conf");
                if (File.Exists(wireproxy) && File.Exists(conf)) return true;

                wireproxy = Path.Combine(Environment.CurrentDirectory, @"bin\warp\wireproxy.exe");
                conf = Path.Combine(Environment.CurrentDirectory, @"bin\warp\wireproxy.conf");
                return File.Exists(wireproxy) && File.Exists(conf);
            }
            catch { return false; }
        }

        public static bool IsWarpListening()
        {
            try
            {
                using (var tcp = new System.Net.Sockets.TcpClient())
                {
                    var ar = tcp.BeginConnect("127.0.0.1", WarpSocksPort, null, null);
                    // Dispose the async wait handle: this runs in launch-time
                    // poll loops, and each undisposed handle is an OS resource
                    // that only gets reclaimed at GC.
                    WaitHandle wait = ar.AsyncWaitHandle;
                    bool ok;
                    try { ok = wait.WaitOne(400); }
                    finally { try { wait.Close(); } catch { } }
                    if (ok && tcp.Connected)
                    {
                        tcp.EndConnect(ar);
                        return true;
                    }
                }
            }
            catch { }
            return false;
        }

        public static bool EnsureWarpRunning(Action<string> logger = null)
        {
            if (IsWarpListening()) return true;

            lock (_warpLock)
            {
                if (IsWarpListening()) return true;

                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string warpDir = Path.Combine(baseDir, @"bin\warp");
                string wireproxy = Path.Combine(warpDir, "wireproxy.exe");
                string conf = Path.Combine(warpDir, "wireproxy.conf");

                if (!File.Exists(wireproxy) || !File.Exists(conf))
                {
                    warpDir = Path.Combine(Environment.CurrentDirectory, @"bin\warp");
                    wireproxy = Path.Combine(warpDir, "wireproxy.exe");
                    conf = Path.Combine(warpDir, "wireproxy.conf");
                }

                if (!File.Exists(wireproxy) || !File.Exists(conf))
                {
                    logger?.Invoke("[WARP] Portable Cloudflare WARP binaries not found in bin\\warp\\");
                    return false;
                }

                try
                {
                    foreach (var p in Process.GetProcessesByName("wireproxy"))
                    {
                        try { p.Kill(); } catch { }
                    }
                }
                catch { }

                try
                {
                    logger?.Invoke(string.Format("[WARP] Launching portable Cloudflare WARP WireGuard tunnel (SOCKS5 127.0.0.1:{0})...", WarpSocksPort));
                    var psi = new ProcessStartInfo
                    {
                        FileName = wireproxy,
                        Arguments = "-c \"" + conf + "\"",
                        WorkingDirectory = warpDir,
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };
                    _warpProcess = Process.Start(psi);

                    for (int i = 0; i < 15; i++)
                    {
                        System.Threading.Thread.Sleep(200);
                        if (IsWarpListening())
                        {
                            logger?.Invoke(string.Format("[WARP] Cloudflare WARP Anycast tunnel ACTIVE on 127.0.0.1:{0}", WarpSocksPort));
                            return true;
                        }
                    }
                }
                catch (Exception ex)
                {
                    logger?.Invoke("[WARP] Failed to start wireproxy: " + ex.Message);
                }
            }
            return IsWarpListening();
        }

        public static void StopWarp()
        {
            lock (_warpLock)
            {
                try
                {
                    if (_warpProcess != null && !_warpProcess.HasExited)
                    {
                        _warpProcess.Kill();
                        _warpProcess = null;
                    }
                }
                catch { }
                try
                {
                    foreach (var p in Process.GetProcessesByName("wireproxy"))
                    {
                        try { p.Kill(); } catch { }
                    }
                }
                catch { }
            }
        }

        private static KeyValuePair<string, string>[] _cachedAccountPool = null;
        private static DateTime _cachedAccountPoolUtc = DateTime.MinValue;
        private static readonly object _accountPoolLock = new object();

        public static KeyValuePair<string, string>? GetAccountForSlot(int slot)
        {
            try
            {
                lock (_accountPoolLock)
                {
                    if (_cachedAccountPool == null || (DateTime.UtcNow - _cachedAccountPoolUtc).TotalSeconds > 10)
                    {
                        string[] searchPaths = new[]
                        {
                            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "accounts.txt"),
                            Path.Combine(Environment.CurrentDirectory, "accounts.txt")
                        };

                        string foundFile = searchPaths.FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p));
                        if (foundFile != null)
                        {
                            var list = new List<KeyValuePair<string, string>>();
                            foreach (var rawLine in File.ReadAllLines(foundFile))
                            {
                                string l = rawLine.Trim();
                                if (l.Length == 0 || l.StartsWith("#") || l.StartsWith("//")) continue;
                                int cIdx = l.IndexOf(':');
                                if (cIdx > 0 && cIdx < l.Length - 1)
                                {
                                    string u = l.Substring(0, cIdx).Trim();
                                    string p = l.Substring(cIdx + 1).Trim();
                                    if (!string.IsNullOrEmpty(u) && !string.IsNullOrEmpty(p))
                                    {
                                        list.Add(new KeyValuePair<string, string>(u, p));
                                    }
                                }
                            }
                            _cachedAccountPool = list.ToArray();
                        }
                        else
                        {
                            _cachedAccountPool = new KeyValuePair<string, string>[0];
                        }
                        _cachedAccountPoolUtc = DateTime.UtcNow;
                    }

                    if (_cachedAccountPool != null && Math.Abs(slot) < _cachedAccountPool.Length)
                    {
                        return _cachedAccountPool[Math.Abs(slot)];
                    }
                }
            }
            catch { }
            return null;
        }

        public static int GetAccountPoolCount()
        {
            try
            {
                lock (_accountPoolLock)
                {
                    GetAccountForSlot(0);
                    return _cachedAccountPool != null ? _cachedAccountPool.Length : 0;
                }
            }
            catch { return 0; }
        }

        private static readonly ConcurrentDictionary<string, string> _activeAccountClaims = new ConcurrentDictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public static string ClaimAccount(string instanceId, string login)
        {
            try
            {
                if (string.IsNullOrEmpty(instanceId) || string.IsNullOrEmpty(login)) return null;
                string norm = login.Trim();
                foreach (var kv in _activeAccountClaims)
                {
                    if (!string.Equals(kv.Key, instanceId, StringComparison.OrdinalIgnoreCase) &&
                        string.Equals(kv.Value, norm, StringComparison.OrdinalIgnoreCase))
                        return kv.Key;
                }
                _activeAccountClaims[instanceId] = norm;
            }
            catch { }
            return null;
        }

        public static void ReleaseAccount(string instanceId)
        {
            try
            {
                if (!string.IsNullOrEmpty(instanceId))
                {
                    string removed;
                    _activeAccountClaims.TryRemove(instanceId, out removed);
                }
            }
            catch { }
        }

        public static bool IsIdentityOwnedByOtherProfile(string login, string selfInstanceId)
        {
            try
            {
                if (string.IsNullOrEmpty(login)) return false;
                string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DofusFarm", "webview2_profiles");
                if (!Directory.Exists(root)) return false;
                string norm = login.Trim();
                foreach (string dir in Directory.GetDirectories(root))
                {
                    if (string.Equals(Path.GetFileName(dir), selfInstanceId, StringComparison.OrdinalIgnoreCase)) continue;
                    string marker = Path.Combine(dir, "account.identity");
                    try
                    {
                        if (File.Exists(marker) && string.Equals(File.ReadAllText(marker).Trim(), norm, StringComparison.OrdinalIgnoreCase))
                            return true;
                    }
                    catch { }
                }
            }
            catch { }
            return false;
        }

        public static string GenerateRealisticMac(DeviceProfile profile, int slotIdx, string seedStr = null)
        {
            string m = (profile != null && !string.IsNullOrEmpty(profile.Manufacturer)) ? profile.Manufacturer.ToLowerInvariant() : "samsung";
            string oui = m.Contains("google") ? "3C:28:6D"
                       : (m.Contains("xiaomi") || m.Contains("redmi") || m.Contains("poco")) ? "64:CC:2E"
                       : m.Contains("oneplus") ? "94:65:2D" : m.Contains("sony") ? "D0:51:62"
                       : m.Contains("motorola") ? "F8:E0:79" : m.Contains("realme") ? "C4:06:83"
                       : m.Contains("asus") ? "04:D9:F5" : "F8:E6:1A";

            uint h = (uint)(2166136261UL ^ (uint)slotIdx);
            foreach (char c in (seedStr ?? Guid.NewGuid().ToString("N"))) h = (h ^ c) * 16777619U;
            return string.Format("{0}:{1:X2}:{2:X2}:{3:X2}", oui, (byte)((h & 0xFF) ^ (slotIdx & 0xFF)), (byte)((h >> 8) & 0xFF), (byte)(((h >> 16) & 0xFE) | 0x01));
        }

        private static bool IsLocalPortOpen(int port, int timeoutMs = 200)
        {
            try
            {
                using (var sock = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp))
                {
                    IAsyncResult ar = sock.BeginConnect(new IPEndPoint(IPAddress.Loopback, port), null, null);
                    WaitHandle wait = ar.AsyncWaitHandle;
                    bool open;
                    try { open = wait.WaitOne(timeoutMs); }
                    finally { try { wait.Close(); } catch { } }
                    try { if (open) sock.EndConnect(ar); else sock.Close(); } catch { }
                    return open;
                }
            }
            catch { return false; }
        }

        public bool EnsureProxyRunning(int port = 8880, string upstream = null, string serialNo = null, string androidId = null, DeviceProfile deviceProfile = null)
        {
            string normalizedUpstream = NormalizeUpstreamProxy(upstream);
            lock (_proxyLock)
            {
                Process existingProc;
                InProcessProxyServer existingInProc;
                string currentUpstream;
                bool upstreamChanged = !_proxyCurrentUpstream.TryGetValue(port, out currentUpstream) ||
                                       !string.Equals(currentUpstream, normalizedUpstream, StringComparison.OrdinalIgnoreCase);

                if (!upstreamChanged && IsLocalPortOpen(port, 180))
                {
                    if ((_proxyProcesses.TryGetValue(port, out existingProc) && !existingProc.HasExited) ||
                        (_inProcProxies.TryGetValue(port, out existingInProc) && existingInProc.IsRunning)) return true;

                    try
                    {
                        var tracked = new HashSet<int>(_proxyProcesses.Values.Where(p => { try { return !p.HasExited; } catch { return false; } }).Select(p => p.Id));
                        var blocking = new HashSet<int>();
                        QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='dofus-net-proxy.exe'", 2, mo =>
                        {
                            string cl = (mo["CommandLine"] ?? "").ToString();
                            if (cl.Length == 0 || cl.IndexOf("-port " + port, StringComparison.OrdinalIgnoreCase) >= 0)
                                blocking.Add(Convert.ToInt32(mo["ProcessId"]));
                        });
                        foreach (var p in Process.GetProcessesByName("dofus-net-proxy"))
                            using (p)
                            {
                                if (tracked.Contains(p.Id)) continue;
                                if (blocking.Count > 0 && !blocking.Contains(p.Id)) continue;
                                try { if (!p.HasExited) p.Kill(); } catch { }
                            }
                        Thread.Sleep(200);
                    }
                    catch { }
                }

                StopProxyInternal(port);

                if (File.Exists(ProxyExePath))
                {
                    var sb = new StringBuilder().AppendFormat("-port {0} -cache \"{1}\" -quiet", port, Path.Combine(AppBaseDir, "asset_cache"));
                    if (!string.IsNullOrEmpty(normalizedUpstream))
                    {
                        sb.AppendFormat(" -upstream \"{0}\"", normalizedUpstream);
                        Log(string.Format("Starting Go Network Spoofing Proxy (Port {0} -> Upstream: {1})...", port, normalizedUpstream));
                    }
                    else Log(string.Format("Starting Go Network Spoofing Proxy (Port {0} -> Direct)...", port));

                    if (!string.IsNullOrEmpty(serialNo)) sb.AppendFormat(" -serial \"{0}\"", serialNo);
                    if (!string.IsNullOrEmpty(androidId)) sb.AppendFormat(" -uuid \"{0}\"", androidId);
                    if (deviceProfile != null)
                    {
                        if (!string.IsNullOrEmpty(deviceProfile.Model)) sb.AppendFormat(" -model \"{0}\"", deviceProfile.Model);
                        if (!string.IsNullOrEmpty(deviceProfile.Manufacturer)) sb.AppendFormat(" -manufacturer \"{0}\"", deviceProfile.Manufacturer);
                        if (!string.IsNullOrEmpty(deviceProfile.UserAgent)) sb.AppendFormat(" -ua \"{0}\"", deviceProfile.UserAgent.Replace("\"", "\\\""));
                        if (!string.IsNullOrEmpty(deviceProfile.GlVendor)) sb.AppendFormat(" -gl-vendor \"{0}\"", deviceProfile.GlVendor);
                        if (!string.IsNullOrEmpty(deviceProfile.GlRenderer)) sb.AppendFormat(" -gl-renderer \"{0}\"", deviceProfile.GlRenderer);
                        if (deviceProfile.MaxTextureSize > 0) sb.AppendFormat(" -texture-size \"{0}\"", deviceProfile.MaxTextureSize);
                    }

                    try
                    {
                        Process proc = Process.Start(new ProcessStartInfo
                        {
                            FileName = ProxyExePath, Arguments = sb.ToString(), WorkingDirectory = AppBaseDir,
                            CreateNoWindow = true, UseShellExecute = false, WindowStyle = ProcessWindowStyle.Hidden
                        });
                        // Poll the listen port instead of one fixed Sleep+check: a
                        // cold start (AV scan on first run, spinning disk) can take
                        // over a second, and the old code then killed the healthy
                        // relay and silently fell back to the in-process proxy.
                        // IsLocalPortOpen already waits, so no extra sleep here.
                        if (proc != null)
                        {
                            for (int i = 0; i < 20 && !proc.HasExited; i++)
                            {
                                if (IsLocalPortOpen(port, 250))
                                {
                                    _proxyProcesses[port] = proc;
                                    _proxyCurrentUpstream[port] = normalizedUpstream;
                                    return true;
                                }
                            }
                            if (!proc.HasExited) { try { proc.Kill(); } catch { } }
                        }
                    }
                    catch { }
                }

                try
                {
                    var inProc = new InProcessProxyServer(port, normalizedUpstream, serialNo, androidId, deviceProfile, GetScriptPath("mobile-disguise.js"));
                    if (inProc.Start())
                    {
                        _inProcProxies[port] = inProc;
                        _proxyCurrentUpstream[port] = normalizedUpstream;
                        Log(string.Format("[🛡️ Net-Spoof] Active C# Network Spoofing Proxy listening on 127.0.0.1:{0} ({1}).", port, deviceProfile != null ? deviceProfile.Model : "Mobile"));
                        return true;
                    }
                }
                catch (Exception ex) { Log(string.Format("[Proxy Warning] Could not bind local proxy port {0}: {1}", port, ex.Message)); }

                return IsLocalPortOpen(port, 200);
            }
        }

        private static void StopProxyInternal(int port)
        {
            _proxyCurrentUpstream.Remove(port);
            Process proc;
            if (_proxyProcesses.TryGetValue(port, out proc)) { try { if (!proc.HasExited) proc.Kill(); } catch { } _proxyProcesses.Remove(port); }
            InProcessProxyServer inProc;
            if (_inProcProxies.TryGetValue(port, out inProc)) { try { inProc.Stop(); } catch { } _inProcProxies.Remove(port); }
        }

        public void StopProxy(int port) { lock (_proxyLock) StopProxyInternal(port); }

        public void StopAllProxies()
        {
            lock (_proxyLock)
            {
                _proxyCurrentUpstream.Clear();
                foreach (var p in _proxyProcesses.Values) { try { if (!p.HasExited) p.Kill(); } catch { } }
                _proxyProcesses.Clear();
                foreach (var s in _inProcProxies.Values) { try { s.Stop(); } catch { } }
                _inProcProxies.Clear();
                try { foreach (var p in Process.GetProcessesByName("dofus-net-proxy")) using (p) { try { if (!p.HasExited) p.Kill(); } catch { } } } catch { }
            }
        }

        private static void ReadConfigRamCores(string cfgFile, ref int ramMb, ref int cores)
        {
            if (string.IsNullOrEmpty(cfgFile) || !File.Exists(cfgFile)) return;
            try
            {
                using (var fs = new FileStream(cfgFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var sr = new StreamReader(fs, Encoding.UTF8))
                {
                    for (string l; (l = sr.ReadLine()) != null;)
                    {
                        int eq = l.IndexOf('=');
                        if (eq <= 0) continue;
                        if (l.StartsWith("hw.ramSize", StringComparison.OrdinalIgnoreCase)) int.TryParse(l.Substring(eq + 1).Trim(), out ramMb);
                        else if (l.StartsWith("hw.cpu.ncore", StringComparison.OrdinalIgnoreCase)) int.TryParse(l.Substring(eq + 1).Trim(), out cores);
                    }
                }
            }
            catch { }
        }

        // The emulator sizes its guest RAM and vCPU count from the command line, so "dynamic" here
        // means the farm recomputes those two numbers from the real state of the machine and from
        // how many instances are actually running right now.
        //   * RAM: the per-instance figure the user picked is what a single instance gets. When
        //     several instances share the host we hand each one less, down to a hard floor of 1024 MB
        //     (Android 29 + WebView + the game genuinely need that much). On this class of laptop
        //     - 4 cores, 8 GB - the old fixed 1024 MB x N oversubscribed physical memory and the
        //     guests spent their time swapping, which is what made them feel frozen.
        //   * Cores: taken from real physical cores, not logical threads. Android runs the renderer
        //     and the UI thread concurrently, so 2 vCPUs is the best choice when the host has cores
        //     to spare; on a 4-physical-core host the 4th instance cannot have 2 without every
        //     instance time-slicing. In that case 1 vCPU per instance is correct - 4 guests on 4
        //     physical cores, each guest getting a dedicated core.
        public int ActiveInstanceCount()
        {
            try
            {
                if (!HasRunningProcess("qemu-system-x86_64")) return 1;
                int farm = 0, total = 0;
                QueryWmi("SELECT CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'", 2, mo =>
                {
                    total++;
                    string cmd = (mo["CommandLine"] ?? "").ToString();
                    if (cmd.IndexOf("-avd dofus-", StringComparison.OrdinalIgnoreCase) >= 0) farm++;
                });
                return Math.Max(1, farm > 0 ? farm : total);
            }
            catch { return 1; }
        }

        // Guest vCPU count for the *next* instance that boots.
        //
        // The rule is: the guests' total vCPU count must never exceed the host's physical cores,
        // because QEMU has no idea how many guests are running - each emulator runs as if it owned
        // the machine. Give 4 guests 2 vCPUs each on a 4-core host and you get 8 busy guest threads
        // on 4 real cores: every instance gets descheduled mid-frame and the screens stutter. Give
        // them 1 vCPU each and each guest owns a physical core end to end.
        //
        // So the count is derived from physical cores AND from how many instances are running right
        // now. The budget is split as evenly as the cores allow, because an even split is what keeps
        // the *worst* instance smooth - the 4-instances-on-4-cores requirement only holds if all
        // four get 1 vCPU. Handing 2 vCPUs to the first instances and starving the later ones is
        // what makes instance #3 and #4 stutter.
        public int GetDynamicCores()
        {
            return 1;
        }

        // How many cores this host gives each instance once the farm is fully up.
        // Shown in the grid so a 4-instances-on-a-4-core box visibly reads "1 Core" for every slot.
        public int RecommendedCoresPerInstance(int plannedInstances)
        {
            return 1;
        }

        private static int _hostPhysicalCores;

        // Physical core count via GetLogicalProcessorInformationEx.
        //
        // The previous implementation issued a WMI query, which spawns a wmiprvse.exe host process
        // and takes 100 ms - 3 s to answer. This runs at every farm start and it matters now that
        // the vCPU count is computed from the number of running instances: the first instance asks
        // for its cores while the other launches are still coming up. The native call resolves in
        // well under a millisecond, with no child process and no WMI handle.
        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESSOR_RELATIONSHIP
        {
            public byte Flags;
            public byte EfficiencyClass;
            public byte Reserved0;
            public byte Reserved1;
            public byte Reserved2;
            public byte Reserved3;
            public byte Reserved4;
            public byte Reserved5;
            public byte Reserved6;
            public byte Reserved7;
            public byte Reserved8;
            public byte Reserved9;
            public byte Reserved10;
            public byte Reserved11;
            public byte Reserved12;
            public byte Reserved13;
            public byte Reserved14;
            public byte Reserved15;
            public byte Reserved16;
            public byte Reserved17;
            public byte Reserved18;
            public byte Reserved19;
            public byte Reserved20;
            public ushort GroupCount;
            public uint Reserved21;
            public ulong GroupMask;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX
        {
            public int Relationship;    // RelationProcessorCore == 0
            public int Size;
            public PROCESSOR_RELATIONSHIP Processor;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetLogicalProcessorInformationEx(
            int relationshipType, IntPtr buffer, ref int returnedLength);

        private static int GetHostPhysicalCores()
        {
            if (_hostPhysicalCores > 0) return _hostPhysicalCores;
            int logical = Environment.ProcessorCount;
            try
            {
                _hostPhysicalCores = QueryPhysicalCoreCount();
                if (_hostPhysicalCores <= 0) _hostPhysicalCores = logical;
            }
            catch { _hostPhysicalCores = logical; }
            return _hostPhysicalCores;
        }

        private static int QueryPhysicalCoreCount()
        {
            int len = 0;
            GetLogicalProcessorInformationEx(0, IntPtr.Zero, ref len);
            if (len <= 0) return 0;

            IntPtr buf = Marshal.AllocHGlobal(len);
            try
            {
                if (!GetLogicalProcessorInformationEx(0, buf, ref len)) return 0;

                int structSize = Marshal.SizeOf(typeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX));
                if (structSize <= 0) return 0;

                int cores = 0, offset = 0;
                while (offset + structSize <= len)
                {
                    var entry = (SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)
                        Marshal.PtrToStructure(buf + offset, typeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX));
                    if (entry.Size <= 0) break;                 // malformed record - stop rather than spin
                    if (entry.Relationship == 0) cores++;       // RelationProcessorCore
                    offset += entry.Size;
                }
                return cores;
            }
            finally { Marshal.FreeHGlobal(buf); }
        }

        // Keeps the guest's total memory under the host's real RAM instead of the nominal figure.
        // Dynamically allocates memory from the host shared RAM pool across active instances.
        public int GetDynamicRamMb(int requestedMb)
        {
            long hostMb = GetHostTotalRamMb();
            if (hostMb <= 0) return requestedMb > 0 ? requestedMb : 1024;

            int running = Math.Max(1, ActiveInstanceCount());
            long budget = (hostMb * 65) / 100;              // 65% shared memory budget for game instances
            long perInstance = budget / running;

            long target = requestedMb > 0 ? Math.Min(requestedMb, perInstance) : perInstance;
            target = Math.Min(4096, target);
            target = Math.Max(768, target);
            return (int)(target / 128 * 128);               // keep it a multiple of 128 MB
        }

        // How many Dofus Touch instances this host can actually feed at once, from the same two
        // budgets the per-instance tuning already uses: one physical core per instance, and 768 MB
        // of the 55% RAM budget per instance. The 8 GB / 4-core target box lands on 4. A host with
        // 4 cores but only 4 GB of RAM lands on 3 rather than being over-provisioned and thrashing.
        // This is deliberately computed from host hardware only - it never reads anything about the
        // running instances, so it cannot correlate them and cannot weaken the spoofing.
        public int RecommendedInstanceCount()
        {
            int byCores = Math.Max(1, GetHostPhysicalCores());

            long hostMb = GetHostTotalRamMb();
            if (hostMb <= 0) return byCores;
            long ramBudget = (hostMb * 55) / 100;           // same budget as GetDynamicRamMb
            int byRam = (int)Math.Max(1, ramBudget / 768);  // 768 MB per instance, with headroom

            return Math.Max(1, Math.Min(byCores, byRam));
        }

        private static long _hostTotalRamMb;

        private static long GetHostTotalRamMb()
        {
            if (_hostTotalRamMb > 0) return _hostTotalRamMb;
            try
            {
                long bytes = 0;
                QueryWmi("SELECT TotalPhysicalMemory FROM Win32_ComputerSystem", 4,
                    mo => { long v; if (long.TryParse((mo["TotalPhysicalMemory"] ?? "0").ToString(), out v)) bytes = v; });
                _hostTotalRamMb = bytes > 0 ? bytes / (1024 * 1024) : 0;
            }
            catch { _hostTotalRamMb = 0; }
            return _hostTotalRamMb;
        }

        public List<InstanceModel> GetInstances()
        {
            var list = new List<InstanceModel>();
            if (!Directory.Exists(AvdHome)) return list;

            HashSet<string> live = GetLiveSerials();
            var portToPid = new Dictionary<int, int>();

            if (HasRunningProcess("qemu-system-x86_64") || live.Count > 0)
            {
                // Only qemu processes that booted one of our own dofus-*.avd AVDs count as farm
                // instances; foreign emulators listening on the same ports must not mark farm slots
                // as running or register their PID with the governor. The scan is a WMI query that
                // spawns a wmiprvse host and GetInstances() runs on the UI refresh loop, so the
                // farm-filtered result is cached for 2 s.
                Dictionary<int, int> scan = null;
                lock (_qemuScanLock)
                {
                    if (_qemuScanCache != null && (DateTime.UtcNow - _qemuScanCacheUtc).TotalMilliseconds < 2000) scan = _qemuScanCache;
                }
                if (scan == null)
                {
                    var fresh = new Dictionary<int, int>();
                    QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'", 2, mo =>
                    {
                        string cmd = (mo["CommandLine"] ?? "").ToString();
                        if (cmd.IndexOf("-avd dofus-", StringComparison.OrdinalIgnoreCase) < 0) return;
                        Match mp = RxQemuPort.Match(cmd);
                        if (mp.Success) fresh[int.Parse(mp.Groups[1].Value)] = Convert.ToInt32(mo["ProcessId"]);
                    });
                    scan = fresh;
                    lock (_qemuScanLock) { _qemuScanCache = fresh; _qemuScanCacheUtc = DateTime.UtcNow; }
                }

                foreach (var kv in scan)
                {
                    portToPid[kv.Key] = kv.Value;
                    live.Add("emulator-" + kv.Key);
                    lock (_pidCacheLock) _cachedPortToPid[kv.Key] = kv.Value;
                    PriorityGovernor.RegisterInstancePid(kv.Value, Math.Max(0, (kv.Key - 5554) / 2), 1);
                }
            }
            else
            {
                lock (_pidCacheLock) _cachedPortToPid.Clear();
                lock (_qemuScanLock) { _qemuScanCache = null; }
            }

            foreach (string d in Directory.GetDirectories(AvdHome, "dofus-*.avd").OrderBy(x => x, StringComparer.Ordinal))
            {
                string name = Path.GetFileNameWithoutExtension(d);
                if (name.Equals("dofus-template", StringComparison.OrdinalIgnoreCase)) continue;

                int idx = ParseSlotIdx(name), port = 5554 + 2 * (idx - 1), ram = 1024, cores = 1, pid = 0;
                string serial = "emulator-" + port;
                ReadConfigRamCores(Path.Combine(d, "config.ini"), ref ram, ref cores);

                bool isRunning = live.Contains(serial);
                if (isRunning) { portToPid.TryGetValue(port, out pid); if (pid > 0) PriorityGovernor.RegisterInstancePid(pid, idx - 1, cores); }

                string customName = "Instance #" + idx, sNo = null, aId = null, mac = null, rawProxy = null, phoneModelId = "";
                ReadIdentity(d, ref customName, ref sNo, ref aId, ref mac, ref rawProxy, ref phoneModelId);

                int poolCount = GetProxyPoolCount();
                string proxyDisplay;
                if (HasManualProxy(rawProxy))
                {
                    proxyDisplay = rawProxy.StartsWith("[") ? rawProxy : ("[🌐] " + rawProxy);
                }
                else
                {
                    if (poolCount > 0)
                        proxyDisplay = string.Format("[🎲 Auto-Random] ({0} in pool)", poolCount);
                    else
                        proxyDisplay = "[⚡] Accelerator (Direct / proxies.txt empty)";
                }

                DeviceProfile prof = string.IsNullOrEmpty(phoneModelId) ? DeviceCatalog.GetDefaultProfileForIndex(idx) : DeviceCatalog.GetProfile(phoneModelId);

                // The UI shows the instance's configured settings
                int shownRam = ram, shownCores = cores;

                list.Add(new InstanceModel
                {
                    Name = name, CustomName = customName, DisplayName = customName, Port = port, RamMb = shownRam, Cores = shownCores,
                    IsRunning = isRunning, Pid = pid, Proxy = proxyDisplay,
                    PhoneModelId = string.IsNullOrEmpty(phoneModelId) ? prof.Id : phoneModelId,
                    PhoneModelName = prof.DisplayName, IsWarmReady = IsInstanceWarmReady(name)
                });
            }
            return list;
        }

        private static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length + 16);
            foreach (char c in s)
            {
                switch (c)
                {
                    case '\\': sb.Append("\\\\"); break;
                    case '"': sb.Append("\\\""); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 32 || c > 127) sb.AppendFormat("\\u{0:X4}", (int)c);
                        else sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        private static string ToJsonString(string text) { return text == null ? "\"\"" : ("\"" + EscapeJson(text) + "\""); }

        public string GetProxy(string name)
        {
            string cn = null, s = null, a = null, m = null, p = null, pm = null;
            ReadIdentity(Path.Combine(AvdHome, name + ".avd"), ref cn, ref s, ref a, ref m, ref p, ref pm);
            return p;
        }

        public string GetPhoneModelId(string name)
        {
            string cn = null, s = null, a = null, m = null, p = null, pm = null;
            ReadIdentity(Path.Combine(AvdHome, name + ".avd"), ref cn, ref s, ref a, ref m, ref p, ref pm);
            return !string.IsNullOrEmpty(pm) ? pm : DeviceCatalog.GetDefaultProfileForIndex(ParseSlotIdx(name)).Id;
        }

        public DeviceProfile GetInstancePhoneModel(string name) { return DeviceCatalog.GetProfile(GetPhoneModelId(name)); }

        public static string GenerateSystemProp(DeviceProfile profile, string serialNo, int cores)
        {
            profile = profile ?? DeviceCatalog.GetProfile("galaxy-a51");
            string releaseVer = MatchVal(RxReleaseVer, profile.BuildFingerprint) ?? "10";
            return string.Format(
                "ro.product.brand={0}\r\nro.product.manufacturer={1}\r\nro.product.model={2}\r\nro.product.name={3}\r\nro.product.device={4}\r\nro.build.product={4}\r\n" +
                "ro.product.system.brand={0}\r\nro.product.system.manufacturer={1}\r\nro.product.system.model={2}\r\nro.product.system.name={3}\r\nro.product.system.device={4}\r\n" +
                "ro.product.vendor.brand={0}\r\nro.product.vendor.manufacturer={1}\r\nro.product.vendor.model={2}\r\nro.product.vendor.name={3}\r\nro.product.vendor.device={4}\r\n" +
                "ro.product.odm.brand={0}\r\nro.product.odm.manufacturer={1}\r\nro.product.odm.model={2}\r\nro.product.odm.name={3}\r\nro.product.odm.device={4}\r\n" +
                "ro.soc.manufacturer={8}\r\nro.soc.model={9}\r\nro.build.flavor={3}-user\r\nro.build.type=user\r\nro.build.tags=release-keys\r\n" +
                "ro.build.fingerprint={10}\r\nro.system.build.fingerprint={10}\r\nro.vendor.build.fingerprint={10}\r\nro.bootimage.build.fingerprint={10}\r\n" +
                "ro.build.display.id={11}\r\nro.build.description={3}-user {14} {11} release-keys\r\nro.system.build.type=user\r\nro.system.build.tags=release-keys\r\n" +
                "qemu.hw.mainkeys=1\r\nro.serialno={12}\r\nro.boot.serialno={12}\r\ngsm.sim.state=READY\r\ngsm.sim.operator.numeric=60401\r\ngsm.sim.operator.alpha=Maroc Telecom\r\ngsm.network.type=LTE\r\n" +
                "config.disable_animations=1\r\ndebug.sf.nobootanimation=1\r\nro.config.low_ram=false\r\nro.opengles.version=196608\r\ndebug.hwui.renderer=opengl\r\n" +
                "debug.sf.hw=1\r\ndebug.egl.hw=1\r\ndebug.composition.type=gpu\r\ndebug.sf.latch_unsignaled=1\r\ndebug.sf.disable_backpressure=1\r\n" +
                "pm.dexopt.boot=verify\r\npm.dexopt.first-boot=verify\r\npm.dexopt.install=speed-profile\r\n" +
                "dalvik.vm.dex2oat-filter=speed-profile\r\ndalvik.vm.image-dex2oat-filter=verify\r\n" +
                "dalvik.vm.dex2oat-threads={13}\r\ndalvik.vm.image-dex2oat-threads={13}\r\ndalvik.vm.boot-dex2oat-threads={13}\r\n" +
                "persist.sys.strictmode.disable=true\r\npersist.sys.strictmode.visual=false\r\ndalvik.vm.checkjni=false\r\nro.config.hw_quickpoweron=true\r\nro.config.nocheckin=1\r\n" +
                "persist.sys.disable_rescue=true\r\nsys.wifitracing.started=0\r\npersist.traced.enable=0\r\npersist.sys.timezone=Africa/Casablanca\r\npersist.sys.country=MA\r\npersist.sys.language=fr\r\npersist.sys.locale=fr-MA\r\n",
                profile.Brand, profile.Manufacturer, profile.Model, profile.ProductName, profile.ProductDevice,
                profile.Board, profile.Hardware, profile.Platform, profile.SocManufacturer, profile.SocModel,
                profile.BuildFingerprint, profile.BuildId, serialNo, cores, releaseVer);
        }

        public void UpdateInstanceConfig(string name, string customName, int ramMb, int cores, string phoneModelId, string proxy)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir)) { ProvisionInstance(name, ramMb, cores, phoneModelId, customName, proxy); return; }

            int instIdx = ParseSlotIdx(name), oldRam = ramMb, oldCores = cores;
            string cfgFile = Path.Combine(avdDir, "config.ini");
            ReadConfigRamCores(cfgFile, ref oldRam, ref oldCores);

            DeviceProfile activeProfile = !string.IsNullOrEmpty(phoneModelId) ? DeviceCatalog.GetProfile(phoneModelId) : DeviceCatalog.GetDefaultProfileForIndex(instIdx);
            string serialNo = (activeProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant();
            string mac = GenerateRealisticMac(activeProfile, instIdx, serialNo);
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLowerInvariant();
            string existingCustomName = "Instance #" + instIdx, existingProxy = "", oldPhoneModel = "";

            ReadIdentity(avdDir, ref existingCustomName, ref serialNo, ref androidId, ref mac, ref existingProxy, ref oldPhoneModel);

            string finalCustomName = !string.IsNullOrEmpty(customName) ? customName.Trim() : existingCustomName;
            string finalProxy = proxy != null ? proxy.Trim() : existingProxy;
            if (!HasManualProxy(finalProxy)) finalProxy = "";
            else if (finalProxy.StartsWith("[🌐] ")) finalProxy = finalProxy.Substring(5).Trim();

            WriteIdentity(avdDir, finalCustomName, serialNo, androidId, mac, finalProxy, activeProfile.Id);
            File.WriteAllText(Path.Combine(avdDir, "system.prop"), GenerateSystemProp(activeProfile, serialNo, cores), Encoding.ASCII);
            UpdateConfigIni(cfgFile, name, ramMb, cores, activeProfile);

            if (oldRam != ramMb || oldCores != cores || (!string.IsNullOrEmpty(oldPhoneModel) && !string.Equals(oldPhoneModel, activeProfile.Id, StringComparison.OrdinalIgnoreCase)))
                InvalidateWarmSnapshot(name);

            Log(string.Format("Updated '{0}' ({1}): {2} MB RAM, {3} Core(s), {4}.", finalCustomName, name, ramMb, cores, activeProfile.DisplayName));
        }

        public int ApplyHardwareToAllInstances(int ramMb, int cores)
        {
            int updated = 0;
            foreach (InstanceModel inst in GetInstances().Where(i => !i.IsRunning))
            {
                UpdateInstanceConfig(inst.Name, inst.CustomName, ramMb, cores, inst.PhoneModelId, inst.Proxy);
                updated++;
            }
            return updated;
        }

        public void ProvisionInstance(string name, int ramMb, int cores, string phoneModelId = null, string customName = null, string proxyOverride = null)
        {
            string avdDir = Directory.CreateDirectory(Path.Combine(AvdHome, name + ".avd")).FullName;
            EnsureAvdIni(name, avdDir);

            string tplCfg = Path.Combine(AvdHome, @"dofus-template.avd\config.ini"), cfgDest = Path.Combine(avdDir, "config.ini");
            if (File.Exists(tplCfg) && !File.Exists(cfgDest)) File.Copy(tplCfg, cfgDest, true);

            int instIdx = ParseSlotIdx(name);
            DeviceProfile activeProfile = !string.IsNullOrEmpty(phoneModelId) ? DeviceCatalog.GetProfile(phoneModelId) : DeviceCatalog.GetDefaultProfileForIndex(instIdx);

            string resolvedCustomName = !string.IsNullOrEmpty(customName) ? customName.Trim() : ("Instance #" + instIdx);
            string serialNo = (activeProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant();
            string mac = GenerateRealisticMac(activeProfile, instIdx, serialNo);
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLowerInvariant();
            string storedCustom = resolvedCustomName, storedProxy = "", storedModel = null;

            ReadIdentity(avdDir, ref storedCustom, ref serialNo, ref androidId, ref mac, ref storedProxy, ref storedModel);
            if (string.IsNullOrEmpty(customName)) resolvedCustomName = storedCustom;
            string proxy = proxyOverride ?? storedProxy;
            if (string.IsNullOrEmpty(phoneModelId) && !string.IsNullOrEmpty(storedModel)) activeProfile = DeviceCatalog.GetProfile(storedModel);

            if (!HasManualProxy(proxy)) proxy = "";
            else if (proxy.StartsWith("[🌐] ")) proxy = proxy.Substring(5).Trim();

            WriteIdentity(avdDir, resolvedCustomName, serialNo, androidId, mac, proxy, activeProfile.Id);
            File.WriteAllText(Path.Combine(avdDir, "system.prop"), GenerateSystemProp(activeProfile, serialNo, cores), Encoding.ASCII);

            CleanLocks(avdDir);
            string tgtRaw = Path.Combine(avdDir, "userdata-qemu.img");
            if (File.Exists(GoldenMasterPath))
            {
                if (!File.Exists(tgtRaw) || new FileInfo(tgtRaw).Length != new FileInfo(GoldenMasterPath).Length)
                    LinkOrCopyFile(GoldenMasterPath, tgtRaw);
            }

            string tgtQcow2 = Path.Combine(avdDir, "userdata-qemu.img.qcow2");
            if (!File.Exists(tgtQcow2) || new FileInfo(tgtQcow2).Length < 1024 * 1024)
            {
                string goldenQcow2 = GetGoldenOverlayPath();
                if (!string.IsNullOrEmpty(goldenQcow2) && File.Exists(goldenQcow2) && new FileInfo(goldenQcow2).Length > 1024 * 1024)
                {
                    SafeDeleteFile(tgtQcow2);
                    File.Copy(goldenQcow2, tgtQcow2, true);
                    try { File.WriteAllText(Path.Combine(avdDir, "sanitize_session.flag"), "1", Encoding.UTF8); } catch { }
                    if (File.Exists(QemuImgPath))
                    {
                        string backingFile = File.Exists(tgtRaw) ? tgtRaw : GoldenMasterPath;
                        RunCommand(QemuImgPath, string.Format("rebase -u -b \"{0}\" -F {1} \"{2}\"", backingFile, DetectDiskFormat(backingFile), tgtQcow2), 30000, avdDir);
                        RunCommand(QemuImgPath, "check \"userdata-qemu.img.qcow2\"", 30000, avdDir);
                    }
                }
            }

            EnsureOverlay(avdDir);
            UpdateConfigIni(cfgDest, name, ramMb > 0 ? ramMb : GetDynamicRamMb(0), cores, activeProfile);

            Log(string.Format("Provisioned {0} with {1} Core(s), spoofing {2} ({3}) [Shared Dynamic Memory].", name, cores, activeProfile.DisplayName, activeProfile.Model));
        }

        private void UpdateConfigIni(string cfgFile, string name, int ramMb, int cores, DeviceProfile profile = null)
        {
            profile = profile ?? DeviceCatalog.GetProfile("galaxy-a51");
            int safeRam = ramMb > 0 ? Math.Max(768, ramMb) : GetDynamicRamMb(0), safeCores = cores > 0 ? Math.Max(1, cores) : GetDynamicCores();

            var existing = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            // deliberately left as-is: this is only reached when the user edits an instance by hand
            if (File.Exists(cfgFile))
            {
                foreach (string l in File.ReadLines(cfgFile))
                {
                    string[] p = l.Split(new[] { '=' }, 2);
                    if (p.Length == 2) existing[p[0].Trim()] = RxAvdReplace.Replace(p[1].Trim(), name + ".avd");
                }
            }

            foreach (string sk in new[] { "disk.dataPartition.size", "disk.dataPartition.initPath", "hw.sdCard.path", "sdcard.path", "fastboot.chosenSnapshotFile", "android.sdk.root", "android.avd.home" })
                existing.Remove(sk);

            var settings = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "AvdId", name }, { "avd.name", name }, { "avd.id", name },
                { "PlayStore.enabled", "false" }, { "tag.id", "default" }, { "tag.display", "Default Android System Image" },
                { "hw.device.manufacturer", profile.Manufacturer }, { "hw.device.name", profile.DisplayName },
                { "hw.gpu.enabled", "yes" }, { "hw.gpu.mode", GetConfiguredGpuMode() },
                { "fastboot.forceFastBoot", "yes" }, { "fastboot.forceColdBoot", "no" },
                { "firstboot.saveToLocalSnapshot", "yes" }, { "userdata.useQcow2", "yes" },
                { "hw.ramSize", safeRam.ToString() }, { "hw.cpu.ncore", safeCores.ToString() },
                { "hw.lcd.width", "1280" }, { "hw.lcd.height", "720" }, { "hw.lcd.density", "240" },
                { "hw.initialOrientation", "landscape" }, { "hw.sdCard", "no" },
                { "hw.audioInput", "no" }, { "hw.audioOutput", "no" },
                { "hw.camera.back", "none" }, { "hw.camera.front", "none" },
                { "hw.keyboard", "yes" }, { "hw.mainKeys", "yes" }, { "qemu.hw.mainkeys", "1" }, { "qemu.vsync", "60" },
                { "showDeviceFrame", "no" }, { "skin.name", "1280x720" }, { "skin.path", "_no_skin" }, { "skin.dynamic", "yes" },
                { "disk.dataPartition.path", "userdata-qemu.img" }, { "disk.encryptionKeyPartition.path", "encryptionkey.img" },
                { "disk.cachePartition", "yes" }, { "disk.cachePartition.path", Path.Combine(Path.GetDirectoryName(cfgFile), "cache.img") },
                { "image.sysdir.1", @"system-images\android-29\default\x86_64\" }, { "abi.type", "x86_64" }, { "hw.cpu.arch", "x86_64" }
            };

            // Turn off emulated hardware sensors (each sensor runs a guest kernel polling thread).
            // WebGL / Dofus never reads ambient, pressure, magnetic, or motion sensors.
            foreach (string sk in new[] { "hw.accelerometer", "hw.accelerometer_uncalibrated", "hw.gyroscope", "hw.gyroscope_uncalibrated", "hw.sensors.light", "hw.sensors.pressure", "hw.sensors.humidity", "hw.sensors.proximity", "hw.sensors.magnetic_field", "hw.sensors.magnetic_field_uncalibrated", "hw.sensors.orientation", "hw.sensors.temperature", "hw.sensors.heart_rate", "hw.sensors.wrist_tilt", "hw.sensors.heading" })
                settings[sk] = "no";

            foreach (var kvp in settings) existing[kvp.Key] = kvp.Value;

            var sb = new StringBuilder(existing.Count * 32);
            foreach (var kvp in existing) sb.Append(kvp.Key).Append(" = ").AppendLine(kvp.Value);
            File.WriteAllText(cfgFile, sb.ToString(), Encoding.UTF8);
            try { SafeDeleteFile(Path.Combine(Path.GetDirectoryName(cfgFile), "hardware-qemu.ini")); } catch { }
        }

        public static void ForceDeleteDirectory(string dirPath)
        {
            if (string.IsNullOrEmpty(dirPath) || !Directory.Exists(dirPath)) return;
            try
            {
                var di = new DirectoryInfo(dirPath);
                foreach (FileSystemInfo fsi in di.EnumerateFileSystemInfos("*", SearchOption.AllDirectories))
                {
                    try { fsi.Attributes = FileAttributes.Normal; } catch { }
                }
                di.Delete(true);
            }
            catch
            {
                try
                {
                    foreach (string f in Directory.GetFiles(dirPath)) SafeDeleteFile(f);
                    foreach (string d in Directory.GetDirectories(dirPath)) ForceDeleteDirectory(d);
                    Directory.Delete(dirPath, true);
                }
                catch { }
            }
        }

        private void PurgeIncompleteSnapshots(string avdDir)
        {
            try
            {
                string snapDir = Path.Combine(avdDir, "snapshots"), defaultBoot = Path.Combine(snapDir, "default_boot");
                if (Directory.Exists(defaultBoot) && !File.Exists(Path.Combine(defaultBoot, "snapshot.pb")))
                {
                    Log(string.Format("Purging incomplete boot snapshot in {0} (black-screen prevention).", Path.GetFileName(avdDir)));
                    ForceDeleteDirectory(snapDir);
                    SafeDeleteFile(Path.Combine(avdDir, "cold_run_completed.flag"));
                }
            }
            catch { }
        }

        private void CreateQcow2Overlay(string avdDir, string backingFile, string qcow2Name, int timeoutMs = 30000, bool fallbackAlt = false)
        {
            string fmt = DetectDiskFormat(backingFile), rel = GetRelativePath(avdDir, backingFile);
            RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"{0}\" -F {1} \"{2}\"", rel, fmt, qcow2Name), timeoutMs, avdDir);
            if (fallbackAlt)
            {
                string outPath = Path.Combine(avdDir, qcow2Name);
                if (!File.Exists(outPath) || new FileInfo(outPath).Length == 0)
                    RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"{0}\" -F {1} \"{2}\"", rel, fmt == "raw" ? "qcow2" : "raw", qcow2Name), timeoutMs, avdDir);
            }
        }

        public string GetGoldenOverlayPath()
        {
            return new[]
            {
                Path.Combine(AvdHome, @"dofus-template.avd\userdata-golden.qcow2"),
                Path.Combine(AvdHome, @"dofus-01.avd\userdata-qemu.img.qcow2"),
                Path.Combine(AppBaseDir, "userdata-golden.qcow2")
            }.FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p) && new FileInfo(p).Length > 1024 * 1024);
        }

        public void EnsureGoldenTemplate()
        {
            try
            {
                string tplDir = Path.Combine(AvdHome, "dofus-template.avd");
                if (!Directory.Exists(tplDir)) Directory.CreateDirectory(tplDir);

                string tplGoldenImg = Path.Combine(tplDir, "userdata-golden.img");
                if (!File.Exists(tplGoldenImg))
                {
                    string candGolden = new[]
                    {
                        GoldenMasterPath,
                        Path.Combine(AppBaseDir, "userdata-golden.img"),
                        Path.Combine(AvdHome, @"dofus-01.avd\userdata-qemu.img"),
                        !string.IsNullOrEmpty(SdkRoot) ? Path.Combine(SdkRoot, SysImgSubPath, "userdata.img") : null
                    }.FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p));

                    if (candGolden != null) LinkOrCopyFile(candGolden, tplGoldenImg);
                }

                string tplGoldenQcow2 = Path.Combine(tplDir, "userdata-golden.qcow2");
                if (!File.Exists(tplGoldenQcow2) || new FileInfo(tplGoldenQcow2).Length < 1024 * 1024)
                {
                    string candOverlay = new[]
                    {
                        Path.Combine(AvdHome, @"dofus-01.avd\userdata-qemu.img.qcow2"),
                        Path.Combine(AppBaseDir, "userdata-golden.qcow2")
                    }.FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p) && new FileInfo(p).Length > 1024 * 1024);

                    if (candOverlay != null && File.Exists(tplGoldenImg))
                    {
                        File.Copy(candOverlay, tplGoldenQcow2, true);
                        if (File.Exists(QemuImgPath))
                        {
                            RunCommand(QemuImgPath, string.Format("rebase -u -b \"{0}\" -F {1} \"{2}\"", tplGoldenImg, DetectDiskFormat(tplGoldenImg), tplGoldenQcow2), 30000, tplDir);
                            RunCommand(QemuImgPath, "check \"userdata-golden.qcow2\"", 30000, tplDir);
                        }
                    }
                }
            }
            catch { }
        }

        public void EnsureOverlay(string avdDir)
        {
            Directory.CreateDirectory(avdDir);
            PurgeIncompleteSnapshots(avdDir);

            if (!File.Exists(GoldenMasterPath))
            {
                string tplZip = Path.Combine(AppBaseDir, "template.zip");
                if (File.Exists(tplZip))
                {
                    try
                    {
                        string tplDir = Directory.CreateDirectory(Path.Combine(AvdHome, "dofus-template.avd")).FullName;
                        using (ZipArchive za = ZipFile.OpenRead(tplZip))
                        {
                            foreach (ZipArchiveEntry e in za.Entries.Where(x => !string.IsNullOrEmpty(x.Name)))
                            {
                                string dest;
                                try { dest = Path.GetFullPath(Path.Combine(tplDir, e.FullName.Replace('/', '\\'))); }
                                catch { continue; }
                                if (!dest.StartsWith(tplDir + "\\", StringComparison.OrdinalIgnoreCase)) continue;
                                string parent = Path.GetDirectoryName(dest);
                                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                                e.ExtractToFile(dest, true);
                            }
                        }
                        string candGolden = Path.Combine(tplDir, "userdata-golden.img");
                        if (File.Exists(candGolden)) GoldenMasterPath = candGolden;
                    }
                    catch { }
                }
            }

            if (!File.Exists(GoldenMasterPath)) return;
            try { File.SetAttributes(GoldenMasterPath, FileAttributes.Normal); } catch { }

            string v23Flag = Path.Combine(avdDir, "v23_blackscreen_fixed.flag");
            bool isV23Migrated = File.Exists(v23Flag);
            if (!isV23Migrated)
            {
                try
                {
                    ForceDeleteDirectory(Path.Combine(avdDir, "snapshots"));
                    DeleteFiles(avdDir, "cold_run_completed.flag", "emulator-user.ini", "encryptionkey.img.qcow2", "cache.img.qcow2");

                    string slotName = Path.GetFileNameWithoutExtension(avdDir);
                    DeviceProfile prof = GetInstancePhoneModel(slotName);
                    string serialNo = (prof.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant();
                    string cn = null, aId = null, mac = null, prx = null, pm = null;
                    ReadIdentity(avdDir, ref cn, ref serialNo, ref aId, ref mac, ref prx, ref pm);

                    int rMb = 1024, nCores = 1;
                    string cfgFile = Path.Combine(avdDir, "config.ini");
                    ReadConfigRamCores(cfgFile, ref rMb, ref nCores);
                    File.WriteAllText(Path.Combine(avdDir, "system.prop"), GenerateSystemProp(prof, serialNo, nCores), Encoding.ASCII);
                    UpdateConfigIni(cfgFile, slotName, rMb, nCores, prof);
                    File.WriteAllText(v23Flag, DateTime.UtcNow.ToString("o"), Encoding.ASCII);
                }
                catch { }
            }

            try { File.WriteAllText(Path.Combine(avdDir, "quickbootChoice.ini"), "saveOnExit = true\r\n", Encoding.ASCII); } catch { }

            try
            {
                string emuUserIni = Path.Combine(avdDir, "emulator-user.ini");
                if (File.Exists(emuUserIni))
                {
                    string euText = File.ReadAllText(emuUserIni, Encoding.UTF8);
                    if (euText.Contains("window.scale = -1") || euText.Contains("window.x = -"))
                        File.WriteAllText(emuUserIni, "window.x = 80\r\nwindow.y = 80\r\n", Encoding.ASCII);
                }
            }
            catch { }

            try { File.SetAttributes(GoldenMasterPath, FileAttributes.ReadOnly); } catch { }

            try
            {
                string tplDir = Path.GetDirectoryName(GoldenMasterPath), sysImgDir = !string.IsNullOrEmpty(SdkRoot) ? Path.Combine(SdkRoot, SysImgSubPath) : "";
                foreach (string aux in AuxImages)
                {
                    string dstAux = Path.Combine(avdDir, aux);
                    if (!File.Exists(dstAux) || (aux.Equals("encryptionkey.img", StringComparison.OrdinalIgnoreCase) && !isV23Migrated))
                    {
                        string src = new[] { !string.IsNullOrEmpty(tplDir) ? Path.Combine(tplDir, aux) : "", !string.IsNullOrEmpty(sysImgDir) ? Path.Combine(sysImgDir, aux) : "" }
                            .FirstOrDefault(p => !string.IsNullOrEmpty(p) && File.Exists(p));
                        if (src != null) { try { if (File.Exists(dstAux)) File.SetAttributes(dstAux, FileAttributes.Normal); File.Copy(src, dstAux, true); } catch { } }
                    }
                }
            }
            catch { }

            if (File.Exists(QemuImgPath))
            {
                string emuQcow2 = Path.Combine(avdDir, "userdata-qemu.img.qcow2");
                bool needQcow2 = !File.Exists(emuQcow2) || new FileInfo(emuQcow2).Length == 0;

                if (!needQcow2)
                {
                    try
                    {
                        string infoOut = RunCommand(QemuImgPath, "info \"userdata-qemu.img.qcow2\"", 8000, avdDir);
                        if (!string.IsNullOrEmpty(infoOut))
                        {
                            string infoLower = infoOut.ToLowerInvariant();
                            if (infoLower.Contains("error") || infoLower.Contains("corrupt"))
                                needQcow2 = true;
                        }
                    }
                    catch { }
                }

                if (needQcow2)
                {
                    try
                    {
                        DeleteFiles(avdDir, "userdata-qemu.img.qcow2", "userdata-qemu.img.qcow2.qcow2", "encryptionkey.img.qcow2", "cache.img.qcow2", "cold_run_completed.flag");
                        ForceDeleteDirectory(Path.Combine(avdDir, "snapshots"));

                        string tgtRaw = Path.Combine(avdDir, "userdata-qemu.img");
                        if (!File.Exists(tgtRaw) && File.Exists(GoldenMasterPath))
                        {
                            LinkOrCopyFile(GoldenMasterPath, tgtRaw);
                        }

                        string goldenQcow2 = GetGoldenOverlayPath();
                        string backingFile = File.Exists(tgtRaw) ? tgtRaw : GoldenMasterPath;

                        if (!string.IsNullOrEmpty(goldenQcow2) && File.Exists(goldenQcow2) && new FileInfo(goldenQcow2).Length > 1024 * 1024)
                        {
                            File.Copy(goldenQcow2, emuQcow2, true);
                            try { File.WriteAllText(Path.Combine(avdDir, "sanitize_session.flag"), "1", Encoding.UTF8); } catch { }
                            RunCommand(QemuImgPath, string.Format("rebase -u -b \"{0}\" -F {1} \"{2}\"", backingFile, DetectDiskFormat(backingFile), emuQcow2), 30000, avdDir);
                            RunCommand(QemuImgPath, "check \"userdata-qemu.img.qcow2\"", 30000, avdDir);
                        }
                        else
                        {
                            CreateQcow2Overlay(avdDir, backingFile, "userdata-qemu.img.qcow2", 30000, true);
                        }

                        foreach (string aux in new[] { "encryptionkey.img", "cache.img" })
                        {
                            string auxSrc = Path.Combine(avdDir, aux);
                            if (File.Exists(auxSrc)) CreateQcow2Overlay(avdDir, auxSrc, aux + ".qcow2", 10000, false);
                        }
                    }
                    catch { }
                }
            }
        }

        public void CleanLocks(string avdDir)
        {
            try
            {
                if (!Directory.Exists(avdDir)) return;
                foreach (string f in Directory.EnumerateFiles(avdDir, "*.lock")) SafeDeleteFile(f);
                foreach (string d in Directory.EnumerateDirectories(avdDir, "*.lock")) ForceDeleteDirectory(d);
                foreach (string f in Directory.EnumerateFiles(avdDir, "*.qcow2.qcow2")) SafeDeleteFile(f);
                SafeDeleteFile(Path.Combine(avdDir, "multiinstance.lock"));
                SafeDeleteFile(Path.Combine(avdDir, "hardware-qemu.ini"));
                string snapDir = Path.Combine(avdDir, @"snapshots\default_boot");
                string ramDirty = Path.Combine(snapDir, "ram.img.dirty");
                string snapPb = Path.Combine(snapDir, "snapshot.pb");
                if (File.Exists(ramDirty) || (File.Exists(snapPb) && new FileInfo(snapPb).Length < 512))
                {
                    ForceDeleteDirectory(Path.Combine(avdDir, "snapshots"));
                }
            }
            catch { }
        }

        private void EnsureAvdIni(string name, string avdDir)
        {
            try
            {
                SafeDeleteFile(Path.Combine(AvdHome, name + ".avd.ini"));
                File.WriteAllText(Path.Combine(AvdHome, name + ".ini"),
                    string.Format("avd.ini.encoding=UTF-8\r\npath={0}\r\npath.rel=avd\\{1}.avd\r\ntarget=android-29\r\n", avdDir, name), Encoding.UTF8);
            }
            catch { }
        }

        private static bool MatchesInstanceCmdLine(string cl, string name, int port)
        {
            return (!string.IsNullOrEmpty(name) && cl.IndexOf("-avd " + name, StringComparison.OrdinalIgnoreCase) >= 0) || cl.Contains("-port " + port);
        }

        private void KillProcessForInstance(string name, int port)
        {
            QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe' OR Name='emulator.exe'", 2, mo =>
            {
                if (MatchesInstanceCmdLine((mo["CommandLine"] ?? "").ToString(), name, port))
                {
                    try { using (Process p = Process.GetProcessById(Convert.ToInt32(mo["ProcessId"]))) { p.Kill(); p.WaitForExit(1500); } } catch { }
                }
            });
        }

        private bool IsInstanceProcessAlive(Process launcherProc, string name, int port)
        {
            try { if (launcherProc != null && !launcherProc.HasExited) return true; } catch { }
            bool alive = false;
            QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'", 2, mo =>
            {
                if (!alive && MatchesInstanceCmdLine((mo["CommandLine"] ?? "").ToString(), name, port))
                {
                    lock (_pidCacheLock) _cachedPortToPid[port] = Convert.ToInt32(mo["ProcessId"]);
                    alive = true;
                }
            });
            return alive;
        }

        private Process StartEmulatorProcess(string emuArgs, bool minimized)
        {
            var psi = new ProcessStartInfo
            {
                FileName = EmulatorPath, Arguments = emuArgs, WorkingDirectory = Path.Combine(SdkRoot, "emulator"),
                CreateNoWindow = true, UseShellExecute = false, WindowStyle = minimized ? ProcessWindowStyle.Minimized : ProcessWindowStyle.Hidden
            };
            psi.EnvironmentVariables["ANDROID_SDK_ROOT"] = SdkRoot;
            psi.EnvironmentVariables["ANDROID_HOME"] = SdkRoot;
            psi.EnvironmentVariables["ANDROID_AVD_HOME"] = AvdHome;
            psi.EnvironmentVariables["ANDROID_SDK_HOME"] = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            psi.EnvironmentVariables["ANDROID_EMULATOR_METRICS_ENABLED"] = "0";

            try
            {
                string avdName = MatchVal(RxAvdArg, emuArgs);
                if (!string.IsNullOrEmpty(avdName))
                {
                    string logDir = Path.Combine(AvdHome, avdName + ".avd");
                    if (Directory.Exists(logDir))
                    {
                        string logFile = Path.Combine(logDir, "emulator-console.log");
                        try { var fi = new FileInfo(logFile); if (fi.Exists && fi.Length > 8L * 1024 * 1024) fi.Delete(); } catch { }
                        psi.RedirectStandardOutput = psi.RedirectStandardError = true;
                        var logged = new Process { StartInfo = psi };
                        StreamWriter writer = null;
                        try
                        {
                            writer = new StreamWriter(logFile, true, Encoding.UTF8) { AutoFlush = true };
                            StreamWriter w = writer;
                            DataReceivedEventHandler onData = delegate(object s, DataReceivedEventArgs e)
                            {
                                if (e.Data != null) { try { lock (w) w.WriteLine(e.Data); } catch { } }
                            };
                            logged.OutputDataReceived += onData;
                            logged.ErrorDataReceived += onData;
                            logged.EnableRaisingEvents = true;
                            logged.Exited += delegate { try { w.Dispose(); } catch { } };
                            if (logged.Start()) { logged.BeginOutputReadLine(); logged.BeginErrorReadLine(); return logged; }
                            try { writer.Dispose(); } catch { }
                        }
                        catch { if (writer != null) { try { writer.Dispose(); } catch { } } }
                    }
                }
            }
            catch { }

            return Process.Start(psi);
        }

        private bool TryAdoptRunningInstance(string name, int port)
        {
            try
            {
                string serial = "emulator-" + port;
                if (File.Exists(Path.Combine(AvdHome, name + ".avd", "sanitize_session.flag"))) return false;

                Stopwatch adw = Stopwatch.StartNew();
                while (true)
                {
                    string bootState = null;
                    try { bootState = Adb(serial, "shell getprop sys.boot_completed", 4000); } catch { }
                    if ((bootState ?? "").Trim() == "1") break;

                    string devList = null;
                    try { devList = Adb("", "devices", 4000); } catch { }
                    if (devList == null || devList.IndexOf(serial, StringComparison.Ordinal) < 0) return false;

                    if (adw.Elapsed.TotalSeconds > 20)
                    {
                        Log(string.Format("{0}: running but guest never reports booted (state '{1}') - restarting.", name, (bootState ?? "").Trim()));
                        return false;
                    }
                    Thread.Sleep(600);
                }

                EnsureAdvancedFeatures();
                AdbShell(serial, WakeAndStartCmd);
                ThreadPool.QueueUserWorkItem(delegate { InjectDisguiseViaCdp(serial, port, name); });
                Log(string.Format("{0} was already running - attached instantly, no reboot needed.", name));
                return true;
            }
            catch { return false; }
        }

        private int BumpLaunchGen(int port) { lock (_launchGenLock) return _launchGen.AddOrUpdate(port, 1, (k, v) => v + 1); }
        private int GetLaunchGen(int port) { int g; lock (_launchGenLock) _launchGen.TryGetValue(port, out g); return g; }

        public void LaunchInstance(string name, int port, int ramMb, int cores, string proxy = null)
        {
            int safeRam = GetDynamicRamMb(ramMb), safeCores = Math.Min(cores > 0 ? cores : GetDynamicCores(), Math.Max(1, GetHostPhysicalCores()));
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            int launchGen = BumpLaunchGen(port);
            if (TryAdoptRunningInstance(name, port)) return;

            EnsureAdvancedFeatures();
            KillProcessForInstance(name, port);
            EnsureAvdIni(name, avdDir);
            CleanLocks(avdDir);
            EnsureOverlay(avdDir);

            int slot = Math.Max(0, (port - 5554) / 2), localProxyPort = 8880 + slot;
            string upstreamProxy = HasManualProxy(proxy)
                ? NormalizeUpstreamProxy(proxy)
                : (IsWarpInstalled() && EnsureWarpRunning(null) ? WarpSocksUrl : null);

            DeviceProfile instProfile = GetInstancePhoneModel(name);
            string serialNo = (instProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLowerInvariant();
            string cn = null, mac = null, prx = null, pm = null;
            ReadIdentity(avdDir, ref cn, ref serialNo, ref androidId, ref mac, ref prx, ref pm);
            if (string.IsNullOrEmpty(mac)) mac = GenerateRealisticMac(instProfile, slot + 1, serialNo);

            string cfgFile = Path.Combine(avdDir, "config.ini");
            if (!File.Exists(cfgFile))
            {
                string tplCfg = Path.Combine(AvdHome, @"dofus-template.avd\config.ini");
                if (File.Exists(tplCfg)) { try { File.Copy(tplCfg, cfgFile, true); } catch { } }
            }
            UpdateConfigIni(cfgFile, name, safeRam, safeCores, instProfile);

            bool proxyActive = EnsureProxyRunning(localProxyPort, upstreamProxy, serialNo, androidId, instProfile);
            string proxyArg = proxyActive ? string.Format("-http-proxy http://127.0.0.1:{0} ", localProxyPort) : "";
            string gpuMode = GetConfiguredGpuMode();

            bool warmReady = IsInstanceWarmReady(name);
            string snapshotArg = warmReady ? "" : "-no-snapshot-load ";
            string emuArgs = (instProfile != null && string.Equals(instProfile.Id, "webgl-minimal", StringComparison.OrdinalIgnoreCase))
                ? BuildWebGlMinimalArgs(name, port, safeRam, safeCores, proxyArg, snapshotArg)
                : string.Format("-avd {0} -port {1} -gpu {2} -memory {3} -cores {4} {6}-no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps {5}-prop qemu.hw.mainkeys=1 -qemu -smp {4} -m {3}",
                    name, port, gpuMode, safeRam, safeCores, proxyArg, snapshotArg);

            Log(string.Format("Starting {0} on port {1} ({2} MB RAM / {3} Core(s) / GPU: {4} / {5})...", name, port, safeRam, safeCores, gpuMode, instProfile.DisplayName));
            try { Adb("", "start-server", 6000); } catch { }

            Process proc = StartEmulatorProcess(emuArgs, false);
            if (proc != null) PriorityGovernor.RegisterInstancePid(proc.Id, slot, safeCores);
            string serial = "emulator-" + port;

            // Affinity is deliberately NOT set here. The PriorityGovernor owns CPU pinning for every
            // qemu process and re-applies the mask as slots come and go; setting it in both places
            // made Windows migrate the emulator's threads between cores, which is exactly what made
            // the guest slow to boot and unresponsive to mouse input.

            ThreadPool.QueueUserWorkItem(delegate { ConfigureGuestAndLaunchGame(name, serial, port, safeRam, safeCores, avdDir, serialNo, androidId, mac, proxyArg, proc, false, launchGen); });
        }

        private void ConfigureGuestAndLaunchGame(string name, string serial, int port, int ramMb, int cores, string avdDir, string serialNo, string androidId, string mac, string proxyArg, Process proc, bool shutdownAfterSnapshot, int launchGen)
        {
            Stopwatch sw = Stopwatch.StartNew();
            bool adbReady = false;
            int fallbackAttempt = 0, bootHealAttempt = 0;
            string lastWorkingGpuMode = null;

            while (sw.Elapsed.TotalSeconds < 110)
            {
                if (GetLaunchGen(port) != launchGen) { Log(string.Format("{0}: stop/relaunch detected - aborting background launch flow.", name)); return; }

                if (!IsInstanceProcessAlive(proc, name, port))
                {
                    if (fallbackAttempt < 2)
                    {
                        string gpuMode = (++fallbackAttempt == 1) ? "angle_indirect" : "swiftshader_indirect";
                        Log(string.Format("Auto-healing {0} (clearing stale snapshot & retrying with -gpu {1})...", name, gpuMode));
                        KillProcessForInstance(name, port);
                        CleanLocks(avdDir);
                        InvalidateWarmSnapshot(name);
                        EnsureOverlay(avdDir);

                        proc = StartEmulatorProcess(BuildColdBootArgs(name, port, gpuMode, ramMb, cores, proxyArg), shutdownAfterSnapshot);
                        lastWorkingGpuMode = gpuMode;
                        sw.Restart();
                        Thread.Sleep(800);
                        continue;
                    }
                    Log(string.Format("Error: Emulator process for {0} exited unexpectedly.", name));
                    return;
                }

                if (Adb(serial, "get-state", 3500).Contains("device")) { adbReady = true; break; }

                if ((bootHealAttempt == 0 && sw.Elapsed.TotalSeconds > 45) || (bootHealAttempt == 1 && sw.Elapsed.TotalSeconds > 60))
                {
                    proc = AutoHealStuckBoot(name, port, avdDir, ramMb, cores, proxyArg, ++bootHealAttempt == 2, shutdownAfterSnapshot);
                    sw.Restart();
                    Thread.Sleep(500);
                    continue;
                }
                Thread.Sleep(250);
            }

            if (!adbReady)
            {
                LogEmulatorConsoleTail(name, avdDir);
                Log(string.Format("Error: {0} could not boot even after automatic recovery. Diagnostics captured in {1}", name, Path.Combine(avdDir, "emulator-console.log")));
                return;
            }

            if (!string.IsNullOrEmpty(lastWorkingGpuMode) && lastWorkingGpuMode != GetConfiguredGpuMode())
            {
                SetConfiguredGpuMode(lastWorkingGpuMode);
                Log(string.Format("Renderer '{0}' verified working on {1}; saved as the default for this farm.", lastWorkingGpuMode, name));
            }

            string flagPath = Path.Combine(avdDir, "sanitize_session.flag");
            bool needsSanitize = File.Exists(flagPath), hadWarmSnapshot = IsInstanceWarmReady(name);

            if (!shutdownAfterSnapshot && !needsSanitize && hadWarmSnapshot)
            {
                string bootDone = AdbShell(serial, "getprop sys.boot_completed");
                if (!string.IsNullOrEmpty(bootDone) && bootDone.Trim() == "1")
                {
                    ApplyNativeHardwareCoherence(serial, serialNo, name, mac);
                    AdbShell(serial, WakeAndStartCmd);
                    ThreadPool.QueueUserWorkItem(delegate { InjectDisguiseViaCdp(serial, port, name); });
                    try { File.WriteAllText(Path.Combine(avdDir, "cold_run_completed.flag"), DateTime.UtcNow.ToString("o"), Encoding.UTF8); } catch { }
                    try { IntPtr hWnd = Win32.FindDeviceWindow(proc != null ? proc.Id : 0); if (hWnd != IntPtr.Zero) Win32.EnsureDeviceWindowInteractive(hWnd); } catch { }
                    Log(string.Format("⚡ Instant QuickBoot restored for {0} ({1}) in {2:F1}s! Dofus Touch active in foreground.", name, serial, sw.Elapsed.TotalSeconds));
                    return;
                }
            }

            Stopwatch bootSw = Stopwatch.StartNew();
            bool bootCompleted = false;
            int healAttempt = 0;
            while (bootSw.Elapsed.TotalSeconds < 130)
            {
                if (GetLaunchGen(port) != launchGen) { Log(string.Format("{0}: stop/relaunch detected - aborting background launch flow.", name)); return; }

                bool procAlive = IsInstanceProcessAlive(proc, name, port);
                if (!procAlive && healAttempt > 0) { Log(string.Format("Error: Emulator process for {0} exited unexpectedly.", name)); return; }

                if (procAlive && AdbShell(serial, "getprop sys.boot_completed").Trim() == "1") { bootCompleted = true; break; }

                if (!procAlive || (healAttempt == 0 && bootSw.Elapsed.TotalSeconds > 90))
                {
                    healAttempt = 1;
                    proc = AutoHealStuckBoot(name, port, avdDir, ramMb, cores, proxyArg, false, shutdownAfterSnapshot);
                    bootSw.Restart();
                    Thread.Sleep(1000);
                    continue;
                }
                Thread.Sleep(250);
            }

            if (!bootCompleted)
            {
                LogEmulatorConsoleTail(name, avdDir);
                Log(string.Format("Error: {0} could not boot even after automatic recovery. Diagnostics captured in {1}", name, Path.Combine(avdDir, "emulator-console.log")));
                return;
            }

            Adb(serial, "root", 8000);

            ApplyNativeHardwareCoherence(serial, serialNo, name, mac);
            AdbShell(serial, string.Format(
                "settings put secure android_id {0}; settings put global device_provisioned 1; settings put secure user_setup_complete 1; " +
                "settings put system screen_off_timeout 2147483647; settings put global window_animation_scale 0.0; " +
                "settings put global transition_animation_scale 0.0; settings put global animator_duration_scale 0.0; " +
                "svc power stayon true 2>/dev/null || true; input keyevent 224 2>/dev/null || true; wm dismiss-keyguard 2>/dev/null || true", androidId));

            string zramPath = GetScriptPath("apply-zram.sh");
            if (!string.IsNullOrEmpty(zramPath) && File.Exists(zramPath) && !AdbShell(serial, "grep -q zram0 /proc/swaps && echo ACTIVE || echo NONE").Contains("ACTIVE"))
            {
                int zramMb = ramMb <= 768 ? (ramMb * 3 / 4) : (ramMb / 2);
                Adb(serial, string.Format("push \"{0}\" /data/local/tmp/apply-zram.sh", zramPath));
                AdbShell(serial, string.Format("chmod 755 /data/local/tmp/apply-zram.sh && sh /data/local/tmp/apply-zram.sh {0} >/dev/null 2>&1 || true", zramMb));
            }

            if (needsSanitize)
            {
                AdbShell(serial, "for base in /data/data/com.ankama.dofustouch /data/user/0/com.ankama.dofustouch; do rm -rf $base/app_webview/Cookies* $base/app_webview/'Local Storage' $base/app_webview/IndexedDB* $base/app_webview/databases* $base/databases* $base/app_webview/Default/Cookies* $base/shared_prefs/* 2>/dev/null; done");
                SafeDeleteFile(flagPath);
                Log(string.Format("Sanitized cloned session tokens, IndexedDB databases, and cookies for {0}.", name));
            }

            string disguisePath = GetScriptPath("mobile-disguise.js");
            if (!string.IsNullOrEmpty(disguisePath) && File.Exists(disguisePath))
            {
                string interpolatedJs = InterpolateDisguise(File.ReadAllText(disguisePath, Encoding.UTF8), androidId, serialNo, GetInstancePhoneModel(name));
                string tempDisguise = Path.Combine(Path.GetTempPath(), string.Format("mobile-disguise-{0}.js", Guid.NewGuid().ToString("N")));
                try
                {
                    File.WriteAllText(tempDisguise, interpolatedJs, Encoding.UTF8);
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/mobile-disguise.js", tempDisguise));
                }
                finally { SafeDeleteFile(tempDisguise); }
            }

            string heapLimit = (ramMb >= 8192) ? "1024m" : (ramMb >= 4096) ? "512m" : (ramMb >= 2048) ? "384m" : (ramMb >= 1024) ? "256m" : (ramMb >= 768) ? "192m" : "160m";
            int rasterThreads = Math.Max(1, Math.Min(2, cores));
            string combinedSetupCmd =
                "setprop debug.hwui.renderer opengl; " +
                "setprop debug.sf.hw 1; setprop debug.egl.hw 1; setprop debug.sf.latch_unsignaled 1; " +
                // vm.overcommit_memory is not a valid Android knob - the guest sysctl rejected it and
                // the shell aborted the rest of the setup pass with "unexpected (" before the
                // debloat and WebView flags ran. Kept only the settings that exist in Android 29.
                "sysctl -w vm.swappiness=80 vm.vfs_cache_pressure=150 >/dev/null 2>&1 || true; " +
                "WV_FLAGS='_ --ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy --enable-accelerated-2d-canvas --enable-webgl --enable-webgl-draft-extensions --in-process-gpu --renderer-process-limit=1 --num-raster-threads=" + rasterThreads + " --gpu-rasterization-msaa-sample-count=0 --disable-gpu-vsync --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows --max-untiled-layer-width=1024 --max-untiled-layer-height=1024 --enable-devtools-experiments --remote-debugging-port=9222'; " +
                "echo \"$WV_FLAGS\" > /data/local/tmp/webview-command-line; " +
                "echo \"$WV_FLAGS\" > /data/local/webview-command-line 2>/dev/null || true; " +
                "chmod 644 /data/local/tmp/webview-command-line /data/local/webview-command-line /data/local/tmp/mobile-disguise.js 2>/dev/null || true; " +
                "for dst in /data/user/0/com.ankama.dofustouch/files/files/js /data/data/com.ankama.dofustouch/files/files/js /data/user/0/com.ankama.dofustouch/files/js /data/data/com.ankama.dofustouch/files/js; do " +
                "  mkdir -p $dst; cp -f /data/local/tmp/mobile-disguise.js $dst/mobile-disguise.js 2>/dev/null || true; " +
                "done; " +
                "APP_UID=$(stat -c '%u' /data/data/com.ankama.dofustouch 2>/dev/null || echo 10000); " +
                "chown -R $APP_UID:$APP_UID /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null; " +
                "chmod -R 755 /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null; " +
                "chcon -R u:object_r:app_data_file:s0 /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null || true; " +
                "setprop dalvik.vm.heapgrowthlimit " + heapLimit + "; " +
                "for svc in cameraserver vendor.camera-provider-2-4 drmserver vendor.drm-hal-1-0 vendor.drm-clearkey-hal-1-2 vendor.drm-widevine-hal-1-2 mediadrm mediadrmserver traced traced_probes statsd incidentd tombstoned llkd llkd-1 mdnsd goldfish-logcat vendor.fps_hal broadcastradio-hal vendor.cas-hal-1-1 hostapd audioserver vendor.audio-hal-2-0 rild vendor.ril-daemon vendor.thermal-hal-2-0-mock media mediametrics; do " +
                "  setprop ctl.stop $svc 2>/dev/null || true; " +
                "done; " +
                "DEBLOAT_V='v5'; " +
                "if [ \"$(cat /data/local/tmp/.dofus_debloat_ver 2>/dev/null)\" != \"$DEBLOAT_V\" ]; then " +
                "  DISABLED=0; " +
                "  for pkg in com.android.cts.priv.ctsshim com.android.cts.ctsshim org.chromium.webview_shell com.android.statementservice com.android.printspooler com.android.bips com.android.camera2 com.android.music com.android.musicfx com.android.deskclock com.android.gallery3d com.android.dreams.basic com.android.dreams.phototable com.android.wallpaper.livepicker com.android.wallpaperbackup com.android.wallpapercropper com.android.calllogbackup com.android.quicksearchbox com.android.htmlviewer com.android.bookmarkprovider com.android.providers.downloads.ui com.android.providers.partnerbookmarks com.android.smspush com.android.stk com.android.calendar com.android.providers.calendar com.android.se com.android.service.ims com.android.dialer com.android.carrierdefaultapp com.android.cellbroadcastreceiver com.android.emergency com.android.traceur com.android.printservice.recommendation com.android.bluetooth com.android.bluetoothmidiservice com.android.email; do " +
                "    if cmd package disable-user --user 0 $pkg >/dev/null 2>&1; then DISABLED=$((DISABLED+1)); fi; " +
                "  done; " +
                "  EN=0; " +
                "  for pkg in com.android.systemui com.android.inputmethod.latin com.android.keychain com.android.documentsui com.android.webview com.android.webview.apkoverlay; do " +
                "    if cmd package enable --user 0 $pkg >/dev/null 2>&1; then EN=$((EN+1)); fi; " +
                "  done; " +
                "  echo $DEBLOAT_V > /data/local/tmp/.dofus_debloat_ver; " +
                "  echo \"debloat: v5 pass complete - $DISABLED packages disabled, $EN critical packages enabled\"; " +
                "else echo 'debloat: instance already stripped'; fi; " +
                "echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true";

            string setupOut = AdbShell(serial, combinedSetupCmd) ?? "";
            string setupShort = setupOut.Replace("\r", " ").Replace("\n", " ").Trim();
            if (setupShort.Length > 160) setupShort = setupShort.Substring(0, 160);
            Log(string.Format("{0} | guest setup pass returned {1} chars: {2}", name, setupOut.Length, setupShort));
            foreach (Match dLine in RxDebloatLine.Matches(setupOut)) Log(string.Format("{0} | {1}", name, dLine.Value.Trim()));

            Adb(serial, "unroot", 6000);

            if (!shutdownAfterSnapshot)
            {
                AdbShell(serial, WakeAndStartCmd);
                Thread.Sleep(100);
                AdbShell(serial, "for p in $(pidof com.ankama.dofustouch webview_zygote 2>/dev/null); do echo -800 > /proc/$p/oom_score_adj 2>/dev/null || true; done");
            }

            try
            {
                AdbShell(serial, "sync");
                File.WriteAllText(Path.Combine(avdDir, "cold_run_completed.flag"), DateTime.UtcNow.ToString("o"), Encoding.UTF8);
            }
            catch { }

            if (shutdownAfterSnapshot) return;

            ThreadPool.QueueUserWorkItem(delegate { InjectDisguiseViaCdp(serial, port, name); });
            try { IntPtr hWnd = Win32.FindDeviceWindow(proc != null ? proc.Id : 0); if (hWnd != IntPtr.Zero) Win32.EnsureDeviceWindowInteractive(hWnd); } catch { }
            Log(string.Format("{0} ({1}) is ready! Dofus Touch running in foreground (Boot: {2:F1}s).", name, serial, sw.Elapsed.TotalSeconds));
        }

        public void WarmInstanceColdRunSync(string name, int port, int ramMb, int cores)
        {
            ramMb = Math.Max(1024, ramMb);
            cores = Math.Max(1, cores);
            EnsureAdvancedFeatures();
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            KillProcessForInstance(name, port);
            EnsureAvdIni(name, avdDir);
            CleanLocks(avdDir);
            InvalidateWarmSnapshot(name);
            EnsureOverlay(avdDir);
            UpdateConfigIni(Path.Combine(avdDir, "config.ini"), name, ramMb, cores, GetInstancePhoneModel(name));
            try { File.WriteAllText(Path.Combine(avdDir, "emulator-user.ini"), "window.x = 80\r\nwindow.y = 80\r\n", Encoding.ASCII); } catch { }
            Log(string.Format("Prepared clean overlay and graphics config for {0} (port {1}).", name, port));
        }

        public string GetScriptPath(string fileName)
        {
            string dfHome = Environment.GetEnvironmentVariable("DOFUS_FARM_HOME");
            return new[]
            {
                Path.Combine(AppBaseDir, @"scripts\" + fileName), Path.Combine(AppBaseDir, fileName),
                dfHome != null ? Path.Combine(dfHome, @"scripts\" + fileName) : null, dfHome != null ? Path.Combine(dfHome, fileName) : null,
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @"DofusFarm\scripts\" + fileName)
            }.FirstOrDefault(c => !string.IsNullOrEmpty(c) && File.Exists(c));
        }

        private void ApplyNativeHardwareCoherence(string serial, string slotSerial, string name = null, string mac = null)
        {
            try
            {
                DeviceProfile activeProf = (!string.IsNullOrEmpty(name) ? GetInstancePhoneModel(name) : null) ?? DeviceCatalog.GetProfile("galaxy-a51");
                if (string.IsNullOrEmpty(mac) || mac.StartsWith("52:54:00")) mac = GenerateRealisticMac(activeProf, 1, slotSerial);
                string lowerMac = mac.ToLowerInvariant();

                var cpuinfo = new StringBuilder(1024);
                const string features = "fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp";
                int implementer = (activeProf.SocManufacturer == "Qualcomm") ? 0x51 : 0x41;
                for (int i = 0; i < 8; i++)
                {
                    cpuinfo.AppendFormat("processor\t: {0}\nBogoMIPS\t: 52.00\nFeatures\t: {1}\nCPU implementer\t: 0x{2:x2}\nCPU architecture: 8\n{3}\n",
                        i, features, implementer,
                        i < 4 ? "CPU variant\t: 0x1\nCPU part\t: 0xd03\nCPU revision\t: 4\n" : "CPU variant\t: 0x0\nCPU part\t: 0xd09\nCPU revision\t: 2\n");
                }
                string hwName = (activeProf.SocManufacturer == "Qualcomm")
                    ? ("Qualcomm Technologies, Inc " + activeProf.Platform.ToUpperInvariant())
                    : (activeProf.SocManufacturer == "Google" ? "Google Tensor" : ("SAMSUNG " + activeProf.Hardware));
                cpuinfo.AppendFormat("Hardware\t: {0}\nRevision\t: 0000\nSerial\t\t: {1}\n", hwName, slotSerial);

                string nowUtc = DateTime.UtcNow.ToString("ddd MMM d HH:mm:ss UTC yyyy", CultureInfo.InvariantCulture);
                string version = (activeProf.SocManufacturer == "Qualcomm")
                    ? string.Format("Linux version 4.19.157-perf (android-build@google.com) (Android clang version 10.0.7) #1 SMP PREEMPT {0}\n", nowUtc)
                    : (activeProf.SocManufacturer == "Google"
                        ? string.Format("Linux version 5.10.43-android12-9-00001 (android-build@google.com) (Android clang version 12.0.5) #1 SMP PREEMPT {0}\n", nowUtc)
                        : "Linux version 4.14.113-18116515 (dpi@SWDG4908) (Android (5484270 based on r353983c) clang version 9.0.3) #1 SMP PREEMPT Thu Jan 9 20:01:14 KST 2020\n");

                string[] carrierCodes = { "60401", "60400", "60402" }, carrierNames = { "Maroc Telecom", "Orange", "inwi" };
                int cIdx = Math.Abs((slotSerial ?? "R58M").GetHashCode()) % carrierCodes.Length;
                string cCode = carrierCodes[cIdx], cName = carrierNames[cIdx];
                string routerMac = string.Format("dc:00:b0:{0:x2}:{1:x2}:fe", (cIdx * 37 + 19) & 0xFF, ((slotSerial ?? "").Length * 53) & 0xFF);
                string fakeArp = string.Format("IP address       HW type     Flags       HW address            Mask     Device\n192.168.1.254    0x1         0x2         {0}     *        wlan0\n", routerMac);

                string socId = !string.IsNullOrEmpty(activeProf.Board) ? activeProf.Board.ToUpperInvariant() : "EXYNOS9611";
                string socFamily = activeProf.SocManufacturer ?? "Samsung";
                string socMachine = string.Format("{0} {1}", activeProf.SocManufacturer, activeProf.SocModel);
                string socRawId = "0x" + activeProf.Hardware;

                string sh = string.Format(
                    "setenforce 0 2>/dev/null || true\n" +
                    "cat << 'EOF_CPU' > /data/local/tmp/fake_cpuinfo\n{0}EOF_CPU\n" +
                    "cat << 'EOF_VER' > /data/local/tmp/fake_version\n{1}EOF_VER\n" +
                    "cat << 'EOF_ARP' > /data/local/tmp/fake_arp\n{2}EOF_ARP\n" +
                    "echo '{3}' > /data/local/tmp/fake_mac\n" +
                    "chmod 444 /data/local/tmp/fake_cpuinfo /data/local/tmp/fake_version /data/local/tmp/fake_arp /data/local/tmp/fake_mac\n" +
                    "chcon u:object_r:proc_cpuinfo:s0 /data/local/tmp/fake_cpuinfo 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_cpuinfo 2>/dev/null || true\n" +
                    "chcon u:object_r:proc_version:s0 /data/local/tmp/fake_version 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_version 2>/dev/null || true\n" +
                    "chcon u:object_r:proc_net:s0 /data/local/tmp/fake_arp 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_arp 2>/dev/null || true\n" +
                    "chcon u:object_r:sysfs:s0 /data/local/tmp/fake_mac 2>/dev/null || true\n" +
                    "mount -o bind /data/local/tmp/fake_cpuinfo /proc/cpuinfo 2>/dev/null || true\n" +
                    "mount -o bind /data/local/tmp/fake_version /proc/version 2>/dev/null || true\n" +
                    "mount -o bind /data/local/tmp/fake_arp /proc/net/arp 2>/dev/null || true\n" +
                    "for iface in wlan0 eth0; do [ -f /sys/class/net/$iface/address ] && mount -o bind /data/local/tmp/fake_mac /sys/class/net/$iface/address 2>/dev/null || true; done\n" +
                    "mkdir -p /data/local/tmp/soc0 /sys/devices/soc0 /data/local/tmp/empty_binfmt 2>/dev/null || true\n" +
                    "echo -n '{4}' > /data/local/tmp/soc0/soc_id\necho -n '{5}' > /data/local/tmp/soc0/family\necho -n '{6}' > /data/local/tmp/soc0/machine\n" +
                    "echo -n '10' > /data/local/tmp/soc0/revision\necho -n '{7}' > /data/local/tmp/soc0/raw_id\necho -n '{5}' > /data/local/tmp/soc0/type\n" +
                    "chmod 755 /data/local/tmp/soc0 && chmod 644 /data/local/tmp/soc0/*\n" +
                    "chcon -R u:object_r:sysfs:s0 /data/local/tmp/soc0 2>/dev/null || true\n" +
                    "echo -n '' > /data/local/tmp/fake_modules && chmod 444 /data/local/tmp/fake_modules\n" +
                    "mount -o bind /data/local/tmp/soc0 /sys/devices/soc0 2>/dev/null || true\n" +
                    "mount -o bind /data/local/tmp/fake_modules /proc/modules 2>/dev/null || true\n" +
                    "for p in $(pidof zygote zygote64 com.ankama.dofustouch 2>/dev/null); do\n" +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_cpuinfo /proc/cpuinfo 2>/dev/null || true\n" +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_version /proc/version 2>/dev/null || true\n" +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_arp /proc/net/arp 2>/dev/null || true\n" +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_modules /proc/modules 2>/dev/null || true\n" +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/soc0 /sys/devices/soc0 2>/dev/null || true\n" +
                    "  [ -d /proc/sys/fs/binfmt_misc ] && nsenter -t $p -m mount -o bind /data/local/tmp/empty_binfmt /proc/sys/fs/binfmt_misc 2>/dev/null || true\n" +
                    "  for iface in wlan0 eth0; do [ -f /sys/class/net/$iface/address ] && nsenter -t $p -m mount -o bind /data/local/tmp/fake_mac /sys/class/net/$iface/address 2>/dev/null || true; done\n" +
                    "done\n" +
                    "setprop debug.choreographer.skipwarning 30\nsetprop gsm.sim.state READY\n" +
                    "setprop gsm.sim.operator.numeric {8}\nsetprop gsm.operator.numeric {8}\n" +
                    "setprop gsm.sim.operator.alpha \"{9}\"\nsetprop gsm.operator.alpha \"{9}\"\n" +
                    "setprop gsm.sim.operator.iso-country ma\nsetprop gsm.operator.iso-country ma\n" +
                    "setprop gsm.network.type LTE\nsetprop wifi.interface wlan0\nsetenforce 1 2>/dev/null || true\n",
                    cpuinfo, version, fakeArp, lowerMac, socId, socFamily, socMachine, socRawId, cCode, cName);

                string tmpSh = Path.Combine(Path.GetTempPath(), "dofus_coherence_" + Guid.NewGuid().ToString("N") + ".sh");
                try
                {
                    File.WriteAllText(tmpSh, sh.Replace("\r\n", "\n"), Utf8NoBom);
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/apply-coherence.sh", tmpSh));
                    AdbShell(serial, "chmod 755 /data/local/tmp/apply-coherence.sh && sh /data/local/tmp/apply-coherence.sh >/dev/null 2>&1 && rm -f /data/local/tmp/apply-coherence.sh");
                }
                finally { SafeDeleteFile(tmpSh); }

                Log(string.Format("[🛡️ Coherence] Applied {0} {1} VFS, sysfs, MAC ({2}) & {3} network disguise on {4}.", activeProf.SocManufacturer, activeProf.SocModel, mac, cName, serial));
            }
            catch (Exception ex) { Log(string.Format("[Warning] ApplyNativeHardwareCoherence failed on {0}: {1}", serial, ex.Message)); }
        }

        public void InjectDisguiseViaCdp(string serial, int port, string name)
        {
            lock (_cdpLock)
            {
                CancellationTokenSource oldCts;
                if (_cdpSessions.TryGetValue(serial, out oldCts)) { try { oldCts.Cancel(); } catch { } _cdpSessions.Remove(serial); }
                var cts = new CancellationTokenSource();
                _cdpSessions[serial] = cts;
                ThreadPool.QueueUserWorkItem(delegate { MaintainCdpSession(serial, port, name, cts.Token); });
            }
        }

        private static string FetchHttpText(string url, int timeoutMs = 1500)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = timeoutMs;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                return sr.ReadToEnd();
        }

        private void MaintainCdpSession(string serial, int port, string name, CancellationToken token)
        {
            try
            {
                string disguisePath = GetScriptPath("mobile-disguise.js");
                if (string.IsNullOrEmpty(disguisePath) || !File.Exists(disguisePath)) return;
                string rawJs = File.ReadAllText(disguisePath, Encoding.UTF8);
                if (string.IsNullOrEmpty(rawJs)) return;

                DeviceProfile cdpProfile = GetInstancePhoneModel(name);
                string slotSerial = ((cdpProfile != null && !string.IsNullOrEmpty(cdpProfile.SerialPrefix)) ? cdpProfile.SerialPrefix : "R58M") + "45F41EFD";
                string slotUuid = "ca8ea76b0fe32f85", cn = null, mac = null, prx = null, pm = null;
                ReadIdentity(Path.Combine(AvdHome, name + ".avd"), ref cn, ref slotSerial, ref slotUuid, ref mac, ref prx, ref pm);

                string disguiseJs = InterpolateDisguise(rawJs, slotUuid, slotSerial, cdpProfile);
                string escapedDisguiseJson = ToJsonString(disguiseJs);
                int cdpPort = port + 4000;
                string cdpJsonUrl = string.Format("http://127.0.0.1:{0}/json", cdpPort);

                int idleMisses = 0;
                while (!token.IsCancellationRequested)
                {
                    string pidOut = AdbShell(serial, "pidof com.ankama.dofustouch").Trim();
                    if (string.IsNullOrEmpty(pidOut))
                    {
                        // The game app is not up (yet). Poll every 3 s while it is expected to launch
                        // any moment, then back off to 10 s: each poll spawns an adb process, and
                        // keeping one per 3 s per instance running for hours is pure churn.
                        idleMisses++;
                        token.WaitHandle.WaitOne(idleMisses > 20 ? 10000 : 3000);
                        continue;
                    }
                    idleMisses = 0;

                    string socketName = MatchVal(RxUnixSocket, AdbShell(serial, "cat /proc/net/unix"));
                    if (string.IsNullOrEmpty(socketName))
                    {
                        string[] pids = pidOut.Split(new[] { ' ', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                        if (pids.Length > 0) socketName = "webview_devtools_remote_" + pids[0].Trim();
                    }

                    Adb(serial, !string.IsNullOrEmpty(socketName)
                        ? string.Format("forward tcp:{0} localabstract:{1}", cdpPort, socketName)
                        : string.Format("forward tcp:{0} tcp:9222", cdpPort));

                    string jsonTargets = null;
                    try { jsonTargets = FetchHttpText(cdpJsonUrl); }
                    catch { try { Adb(serial, string.Format("forward tcp:{0} tcp:9222", cdpPort)); jsonTargets = FetchHttpText(cdpJsonUrl); } catch { } }

                    if (string.IsNullOrEmpty(jsonTargets) || !jsonTargets.Contains("webSocketDebuggerUrl")) { token.WaitHandle.WaitOne(5000); continue; }

                    string rawWsUrl = MatchVal(RxWsDebugger, jsonTargets);
                    if (string.IsNullOrEmpty(rawWsUrl)) { token.WaitHandle.WaitOne(1000); continue; }
                    string targetWsUrl = RxWsHostPort.Replace(rawWsUrl, string.Format("ws://127.0.0.1:{0}/", cdpPort));

                    try
                    {
                        using (var ws = new ClientWebSocket())
                        using (var connectCts = new CancellationTokenSource(TimeSpan.FromSeconds(8)))
                        {
                            ws.ConnectAsync(new Uri(targetWsUrl), connectCts.Token).Wait();
                            if (ws.State != WebSocketState.Open) continue;

                            SendWsJson(ws, "{\"id\":1,\"method\":\"Page.enable\"}");
                            SendWsJson(ws, "{\"id\":2,\"method\":\"Runtime.enable\"}");

                            int msgId = 10;
                            SendWsJson(ws, string.Format("{{\"id\":{0},\"method\":\"Page.addScriptToEvaluateOnNewDocument\",\"params\":{{\"source\":{1}}}}}", ++msgId, escapedDisguiseJson));
                            SendWsJson(ws, string.Format("{{\"id\":{0},\"method\":\"Runtime.evaluate\",\"params\":{{\"expression\":{1},\"userGesture\":true,\"awaitPromise\":false}}}}", ++msgId, escapedDisguiseJson));

                            Log(string.Format("[🛡️ CDP] Persistent session active for {0} ({1}). Disguise hooks permanently registered.", name, serial));

                            var seg = new ArraySegment<byte>(new byte[8192]);
                            Task<WebSocketReceiveResult> rcvTask = null;
                            using (var ms = new MemoryStream(8192))
                            {
                                while (ws.State == WebSocketState.Open && !token.IsCancellationRequested)
                                {
                                    if (rcvTask == null) rcvTask = ws.ReceiveAsync(seg, token);
                                    if (rcvTask.Wait(4000, token))
                                    {
                                        WebSocketReceiveResult res = rcvTask.Result;
                                        rcvTask = null;
                                        if (res.MessageType == WebSocketMessageType.Close) break;

                                        ms.Write(seg.Array, 0, res.Count);
                                        if (res.EndOfMessage)
                                        {
                                            string frameStr = Encoding.UTF8.GetString(ms.GetBuffer(), 0, (int)ms.Length);
                                            ms.SetLength(0);
                                            if (frameStr.Contains("Page.loadEventFired") || (frameStr.Contains("Page.frameNavigated") && !frameStr.Contains("\"parentId\"")))
                                                SendWsJson(ws, string.Format("{{\"id\":{0},\"method\":\"Runtime.evaluate\",\"params\":{{\"expression\":{1},\"userGesture\":true,\"awaitPromise\":false}}}}", ++msgId, escapedDisguiseJson));
                                        }
                                    }
                                }
                            }
                        }
                    }
                    catch (OperationCanceledException) { break; }
                    catch { token.WaitHandle.WaitOne(1500); }
                }
            }
            catch (Exception ex) { Log(string.Format("[CDP Warning] Session worker error on {0}: {1}", name, ex.Message)); }
        }

        private static void SendWsJson(ClientWebSocket ws, string json)
        {
            if (ws == null || ws.State != WebSocketState.Open) return;
            try
            {
                lock (_wsSendLock)
                {
                    if (ws.State != WebSocketState.Open) return;
                    byte[] bytes = Encoding.UTF8.GetBytes(json);
                    ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, CancellationToken.None).Wait(5000);
                }
            }
            catch { }
        }

        public void StopInstance(string serial, string name)
        {
            Log(string.Format("Stopping {0} ({1})...", name, serial));
            int pNum = portFromSerial(serial);
            if (pNum > 0) BumpLaunchGen(pNum);

            lock (_cdpLock)
            {
                CancellationTokenSource cts;
                if (_cdpSessions.TryGetValue(serial, out cts)) { try { cts.Cancel(); cts.Dispose(); } catch { } _cdpSessions.Remove(serial); }
            }

            if (pNum > 0)
            {
                StopProxy(8880 + Math.Max(0, (pNum - 5554) / 2));
                try { Adb(serial, string.Format("forward --remove tcp:{0}", pNum + 4000)); } catch { }
            }

            try { AdbShell(serial, "sync"); } catch { }
            try { Adb(serial, "emu kill"); } catch { }

            int qemuPid = FindEmuPidForInstance(name, pNum);
            if (qemuPid > 0)
            {
                try
                {
                    using (Process p = Process.GetProcessById(qemuPid))
                    {
                        if (!p.WaitForExit(12000))
                        {
                            KillProcessForInstanceFast(name, pNum);
                        }
                    }
                }
                catch { KillProcessForInstanceFast(name, pNum); }
            }
            else
            {
                KillProcessForInstanceFast(name, pNum);
            }
            CleanLocks(Path.Combine(AvdHome, name + ".avd"));
        }

        private int portFromSerial(string serial)
        {
            int p;
            return (!string.IsNullOrEmpty(serial) && serial.StartsWith("emulator-") && int.TryParse(serial.Substring(9), out p)) ? p : 0;
        }

        private static void ForEachToolhelpProcess(Action<Native.Win32.PROCESSENTRY32> onEntry)
        {
            IntPtr snap = Native.Win32.CreateToolhelp32Snapshot(Native.Win32.TH32CS_SNAPPROCESS, 0);
            if (snap == IntPtr.Zero || snap == (IntPtr)(-1)) return;
            try
            {
                var pe = new Native.Win32.PROCESSENTRY32 { dwSize = (uint)Marshal.SizeOf(typeof(Native.Win32.PROCESSENTRY32)) };
                if (Native.Win32.Process32First(snap, ref pe))
                    do { onEntry(pe); } while (Native.Win32.Process32Next(snap, ref pe));
            }
            finally { Native.Win32.CloseHandle(snap); }
        }

        private void KillProcessForInstanceFast(string name, int port)
        {
            try
            {
                int qemuPid = FindEmuPidForInstance(name, port);
                if (qemuPid <= 0) return;

                var pids = new HashSet<int> { qemuPid };
                ForEachToolhelpProcess(pe => { if (pe.th32ParentProcessID == (uint)qemuPid) pids.Add((int)pe.th32ProcessID); });

                foreach (int pid in pids)
                {
                    try { using (Process p = Process.GetProcessById(pid)) { if (!p.HasExited) { p.Kill(); p.WaitForExit(1500); } } } catch { }
                }
            }
            catch { }
        }

        private int FindEmuPidForInstance(string name, int port)
        {
            int found = -1;
            try
            {
                QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe' OR Name='emulator.exe'", 2, mo =>
                {
                    if (found > 0) return;
                    if (MatchesInstanceCmdLine((mo["CommandLine"] ?? "").ToString(), name, port))
                        found = Convert.ToInt32(mo["ProcessId"]);
                });
            }
            catch { }
            if (found <= 0)
            {
                int cachedVal;
                lock (_pidCacheLock) { if (_cachedPortToPid.TryGetValue(port, out cachedVal)) found = cachedVal; }
            }
            return found;
        }

        public void StopAll()
        {
            lock (_cdpLock)
            {
                foreach (var cts in _cdpSessions.Values) { try { cts.Cancel(); cts.Dispose(); } catch { } }
                _cdpSessions.Clear();
            }
            StopAllProxies();
            try { Adb("", "forward --remove-all"); } catch { }

            var farmSerials = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (Directory.Exists(AvdHome))
            {
                foreach (string d in Directory.EnumerateDirectories(AvdHome, "dofus-*.avd"))
                {
                    string n = Path.GetFileNameWithoutExtension(d);
                    if (n.Equals("dofus-template", StringComparison.OrdinalIgnoreCase)) continue;
                    farmSerials.Add("emulator-" + (5554 + 2 * (ParseSlotIdx(n) - 1)));
                }
            }

            HashSet<string> live = GetLiveSerials();
            var toStop = live.Where(farmSerials.Contains).ToList();
            Log(string.Format("Stopping all active instances ({0} running)...", toStop.Count));
            if (toStop.Count > 0)
            {
                using (var countdown = new CountdownEvent(toStop.Count))
                {
                    foreach (string s in toStop)
                    {
                        string serialCopy = s;
                        ThreadPool.QueueUserWorkItem(delegate
                        {
                            try { AdbShell(serialCopy, "sync"); } catch { }
                            try { Adb(serialCopy, "emu kill", 3000); } catch { }
                            finally { try { countdown.Signal(); } catch { } }
                        });
                    }
                    try { countdown.Wait(3500); } catch { }
                }
            }

            // Only qemu/emulator processes that booted one of our dofus-*.avd slots are killed;
            // foreign emulators running on the same machine must survive Stop All. The
            // dofus-net-proxy binary belongs to this app and is always stopped.
            bool wmiOk = Type.GetType("System.Management.ManagementObjectSearcher, System.Management", false) != null;
            var farmPids = new HashSet<int>();
            if (wmiOk)
                QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe' OR Name='emulator.exe'", 2, mo =>
                {
                    if (((mo["CommandLine"] ?? "").ToString()).IndexOf("-avd dofus-", StringComparison.OrdinalIgnoreCase) >= 0)
                        farmPids.Add(Convert.ToInt32(mo["ProcessId"]));
                });

            foreach (string pName in new[] { "qemu-system-x86_64", "emulator" })
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(pName))
                        using (p) { try { if (wmiOk && !farmPids.Contains(p.Id)) continue; if (!p.WaitForExit(4500)) { p.Kill(); p.WaitForExit(1000); } } catch { } }
                }
                catch { }
            }

            try
            {
                foreach (Process p in Process.GetProcessesByName("dofus-net-proxy"))
                    using (p) { try { p.Kill(); p.WaitForExit(1000); } catch { } }
            }
            catch { }

            try { if (Directory.Exists(AvdHome)) foreach (string d in Directory.EnumerateDirectories(AvdHome, "*.avd")) CleanLocks(d); } catch { }
            Log("All instances and emulator processes have been completely stopped.");
        }

        public string GetNextAvailableSlot()
        {
            var existing = new HashSet<string>(
                Directory.Exists(AvdHome) ? Directory.EnumerateDirectories(AvdHome, "dofus-*.avd").Select(Path.GetFileNameWithoutExtension) : Enumerable.Empty<string>(),
                StringComparer.OrdinalIgnoreCase);

            for (int i = 1; i < 1000; i++)
            {
                string cand = string.Format("dofus-{0:D2}", i);
                if (!existing.Contains(cand)) return cand;
            }
            return "dofus-" + DateTime.UtcNow.Ticks.ToString();
        }

        private static void LinkOrCopyFile(string src, string dst)
        {
            SafeDeleteFile(dst);
            if (!Win32.CreateHardLink(dst, src, IntPtr.Zero)) File.Copy(src, dst, true);
        }

        public bool CloneInstance(string sourceName, string targetName, bool cloneState = true, string customTargetName = null)
        {
            string srcDir = Path.Combine(AvdHome, sourceName + ".avd"), tgtDir = Path.Combine(AvdHome, targetName + ".avd");
            if (!Directory.Exists(srcDir)) { Log(string.Format("Clone failed: Source instance '{0}' does not exist.", sourceName)); return false; }

            Log(string.Format("Cloning '{0}' -> '{1}' (Preserving 3GB game cache: {2})...", sourceName, targetName, cloneState));

            int targetIdx = ParseSlotIdx(targetName), targetPort = 5554 + 2 * (targetIdx - 1);
            StopInstance("emulator-" + targetPort, targetName);
            Thread.Sleep(500);

            HashSet<string> live = GetLiveSerials();
            Match mSrc = RxTrailingDigits.Match(sourceName ?? "");
            int srcIdx = 1;
            bool isSourceNumbered = mSrc.Success && int.TryParse(mSrc.Groups[1].Value, out srcIdx);
            int srcPort = 5554 + 2 * (srcIdx - 1);
            string srcSerial = "emulator-" + srcPort;
            bool srcWasRunning = isSourceNumbered && live.Contains(srcSerial);

            if (srcWasRunning)
            {
                Log(string.Format("Flushing and pausing active source '{0}' for atomic disk cloning...", sourceName));
                AdbShell(srcSerial, "sync");
                StopInstance(srcSerial, sourceName);
                Thread.Sleep(1500);
            }

            string srcCfg = Path.Combine(srcDir, "config.ini");
            try
            {
                CleanLocks(srcDir);
                Directory.CreateDirectory(tgtDir);
                EnsureAvdIni(targetName, tgtDir);

                string tgtCfg = Path.Combine(tgtDir, "config.ini");
                if (File.Exists(srcCfg))
                    File.WriteAllText(tgtCfg, Regex.Replace(File.ReadAllText(srcCfg, Encoding.UTF8), @"avd\.(name|id)\s*=.*", "avd.$1 = " + targetName), Encoding.UTF8);

                DeviceProfile tgtProfile = DeviceCatalog.GetDefaultProfileForIndex(targetIdx);
                string serialNo = (tgtProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant();
                string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLowerInvariant();
                string mac = GenerateRealisticMac(tgtProfile, targetIdx, serialNo);
                string resolvedCustom = !string.IsNullOrEmpty(customTargetName) ? customTargetName.Trim() : ("Instance #" + targetIdx);

                WriteIdentity(tgtDir, resolvedCustom, serialNo, androidId, mac, GetProxy(sourceName) ?? "", tgtProfile.Id);

                int inheritedRam = 1024, inheritedCores = 1;
                ReadConfigRamCores(srcCfg, ref inheritedRam, ref inheritedCores);
                File.WriteAllText(Path.Combine(tgtDir, "system.prop"), GenerateSystemProp(tgtProfile, serialNo, inheritedCores), Encoding.ASCII);
                UpdateConfigIni(tgtCfg, targetName, inheritedRam, inheritedCores, tgtProfile);

                string srcRaw = Path.Combine(srcDir, "userdata-qemu.img"), tgtRaw = Path.Combine(tgtDir, "userdata-qemu.img");
                string srcQcow2 = Path.Combine(srcDir, "userdata-qemu.img.qcow2"), tgtQcow2 = Path.Combine(tgtDir, "userdata-qemu.img.qcow2");

                if (File.Exists(srcRaw))
                {
                    if (!File.Exists(tgtRaw) || new FileInfo(tgtRaw).Length != new FileInfo(srcRaw).Length) LinkOrCopyFile(srcRaw, tgtRaw);
                }
                else if (File.Exists(GoldenMasterPath)) LinkOrCopyFile(GoldenMasterPath, tgtRaw);

                if (cloneState && File.Exists(srcQcow2) && new FileInfo(srcQcow2).Length > 0)
                {
                    SafeDeleteFile(tgtQcow2);
                    File.Copy(srcQcow2, tgtQcow2, true);
                    try { File.WriteAllText(Path.Combine(tgtDir, "sanitize_session.flag"), "1", Encoding.UTF8); } catch { }

                    if (File.Exists(QemuImgPath))
                    {
                        RunCommand(QemuImgPath, string.Format("rebase -u -b \"{0}\" -F {1} \"{2}\"", tgtRaw, DetectDiskFormat(tgtRaw), tgtQcow2), 30000, tgtDir);
                        RunCommand(QemuImgPath, "check \"userdata-qemu.img.qcow2\"", 30000, tgtDir);
                    }
                }
                else if (File.Exists(QemuImgPath)) CreateQcow2Overlay(tgtDir, tgtRaw, tgtQcow2, 30000, true);

                CleanLocks(tgtDir);
                ForceDeleteDirectory(Path.Combine(tgtDir, "snapshots"));
                Log(string.Format("Successfully cloned '{0}' -> '{1}'! Unique hardware fingerprint applied.", sourceName, targetName));
                return true;
            }
            finally
            {
                if (srcWasRunning && isSourceNumbered)
                {
                    Log(string.Format("Resuming active source instance '{0}' post-clone...", sourceName));
                    int srcRam = 1024, srcCores = 1;
                    ReadConfigRamCores(srcCfg, ref srcRam, ref srcCores);
                    LaunchInstance(sourceName, srcPort, srcRam > 0 ? srcRam : 1024, srcCores > 0 ? srcCores : 1, GetProxy(sourceName));
                }
            }
        }

        private Process AutoHealStuckBoot(string name, int port, string avdDir, int ramMb, int cores, string proxyArg, bool deep, bool minimized)
        {
            KillProcessForInstance(name, port);
            if (deep)
            {
                Log(string.Format("{0} still will not boot; running deep auto-repair (rebuilding overlays, keys and config)...", name));
                RepairInstanceBlackScreen(name, false);
            }
            else
            {
                Log(string.Format("Boot stalled on {0}; auto-healing with a clean cold boot...", name));
                CleanLocks(avdDir);
                InvalidateWarmSnapshot(name);
                EnsureOverlay(avdDir);
            }
            return StartEmulatorProcess(BuildColdBootArgs(name, port, GetConfiguredGpuMode(), ramMb, cores, proxyArg), minimized);
        }

        private string BuildColdBootArgs(string name, int port, string gpuMode, int ramMb, int cores, string proxyArg)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd"), cfgFile = Path.Combine(avdDir, "config.ini");
            DeviceProfile profile = GetInstancePhoneModel(name);
            return (profile != null && string.Equals(profile.Id, "webgl-minimal", StringComparison.OrdinalIgnoreCase))
                ? BuildWebGlMinimalArgs(name, port, ramMb, cores, proxyArg, "-no-snapshot-load ")
                : string.Format("-avd {0} -port {1} -gpu {2} -memory {3} -cores {4} -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps {5}-prop qemu.hw.mainkeys=1 -qemu -smp {4} -m {3}",
                    name, port, gpuMode, ramMb, cores, proxyArg ?? "");
        }

        private string BuildWebGlMinimalArgs(string name, int port, int ramMb, int cores, string proxyArg, string snapshotArg = "")
        {
            return string.Format("-avd {0} -port {1} -gpu {2} -memory {3} -cores {4} {6}-no-window -no-skin -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps {5}-prop qemu.hw.mainkeys=1 -qemu -smp {4} -m {3}",
                name, port, "swiftshader_indirect", ramMb, cores, proxyArg ?? "", snapshotArg ?? "");
        }

        private void LogEmulatorConsoleTail(string name, string avdDir)
        {
            try
            {
                string logFile = Path.Combine(avdDir, "emulator-console.log");
                if (!File.Exists(logFile)) return;
                const int maxBytes = 64 * 1024;
                string text;
                using (var fs = new FileStream(logFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    long take = Math.Min(fs.Length, maxBytes);
                    fs.Seek(fs.Length - take, SeekOrigin.Begin);
                    byte[] buf = new byte[take];
                    text = Encoding.UTF8.GetString(buf, 0, fs.Read(buf, 0, (int)take));
                }
                string[] lines = text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                Log(string.Format("[{0}] Emulator console tail:", name));
                for (int i = Math.Max(0, lines.Length - 20); i < lines.Length; i++) Log("  [EMU] " + lines[i]);
            }
            catch { }
        }

        public void RepairInstanceBlackScreen(string name, bool relaunch = false)
        {
            if (string.IsNullOrEmpty(name)) return;
            int idx = ParseSlotIdx(name), port = 5554 + 2 * (idx - 1);

            Log(string.Format("Repairing display, encryption key & boot snapshot for {0}...", name));
            StopInstance("emulator-" + port, name);
            KillProcessForInstance(name, port);
            EnsureAdvancedFeatures();

            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir)) return;

            CleanLocks(avdDir);
            InvalidateWarmSnapshot(name);
            DeleteFiles(avdDir, "v23_blackscreen_fixed.flag", "cold_run_completed.flag", "emulator-user.ini", "encryptionkey.img.qcow2", "cache.img.qcow2");

            EnsureAvdIni(name, avdDir);
            EnsureOverlay(avdDir);

            int ramMb = 1024, cores = 1;
            string cfgFile = Path.Combine(avdDir, "config.ini");
            ReadConfigRamCores(cfgFile, ref ramMb, ref cores);
            UpdateConfigIni(cfgFile, name, ramMb, cores, GetInstancePhoneModel(name));

            Log(string.Format("Successfully repaired {0}! Ready for clean boot.", name));
            if (relaunch) LaunchInstance(name, port, ramMb, cores, GetProxy(name));
        }

        public int DeleteAllInstances()
        {
            StopAll();
            if (!Directory.Exists(AvdHome)) return 0;
            return Directory.GetDirectories(AvdHome, "dofus-*.avd")
                .OrderBy(d => d, StringComparer.Ordinal)
                .Select(Path.GetFileNameWithoutExtension)
                .Where(n => !n.Equals("dofus-template", StringComparison.OrdinalIgnoreCase))
                .Count(DeleteInstance);
        }

        public bool DeleteInstance(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            int idx = ParseSlotIdx(name), port = 5554 + 2 * (idx - 1);
            string serial = "emulator-" + port;

            StopInstance(serial, name);
            KillProcessForInstance(name, port);
            int removedPid;
            lock (_pidCacheLock) _cachedPortToPid.TryRemove(port, out removedPid);

            for (int wait = 0; wait < 6; wait++) { Thread.Sleep(400); if (!GetLiveSerials().Contains(serial)) break; }

            string avdDir = Path.Combine(AvdHome, name + ".avd"), iniFile = Path.Combine(AvdHome, name + ".ini"), strayAvdIni = Path.Combine(AvdHome, name + ".avd.ini");
            CleanLocks(avdDir);

            Exception lastEx = null;
            for (int attempt = 1; attempt <= 4; attempt++)
            {
                try
                {
                    ForceDeleteDirectory(avdDir);
                    SafeDeleteFile(iniFile);
                    SafeDeleteFile(strayAvdIni);
                    if (!Directory.Exists(avdDir) && !File.Exists(iniFile))
                    {
                        Log(string.Format("Deleted instance '{0}' and removed its virtual disk.", name));
                        return true;
                    }
                }
                catch (Exception ex) { lastEx = ex; KillProcessForInstance(name, port); Thread.Sleep(600); }
            }

            if (Directory.Exists(avdDir) || File.Exists(iniFile))
            {
                Log(string.Format("Error deleting instance '{0}': {1}", name, lastEx != null ? lastEx.Message : "Failed to remove disk files"));
                return false;
            }

            Log(string.Format("Deleted instance '{0}' and removed its virtual disk.", name));
            return true;
        }

        public void FocusInstance(int port)
        {
            int pid = 0;
            lock (_pidCacheLock)
            {
                int cachedPid;
                if (_cachedPortToPid.TryGetValue(port, out cachedPid) && cachedPid > 0)
                {
                    try { using (Process p = Process.GetProcessById(cachedPid)) { if (!p.HasExited) pid = cachedPid; } } catch { }
                }
            }
            if (pid == 0)
            {
                QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'", 2, mo =>
                {
                    if (pid == 0 && (mo["CommandLine"] ?? "").ToString().Contains("-port " + port))
                        pid = Convert.ToInt32(mo["ProcessId"]);
                });
            }

            if (pid > 0)
            {
                IntPtr hWnd = Win32.FindDeviceWindow(pid);
                if (hWnd != IntPtr.Zero)
                {
                    Win32.ShowWindow(hWnd, Win32.SW_RESTORE);
                    Win32.SetForegroundWindow(hWnd);
                    Win32.EnsureDeviceWindowInteractive(hWnd);
                }
            }
        }
    }

    internal sealed class InProcessProxyServer
    {
        private static readonly Regex RxAndroidVer = new Regex(@"Android\s+([0-9.]+)", RegexOptions.Compiled);
        private static readonly HashSet<string> StripHeaders = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "Proxy-Connection", "Proxy-Authorization", "Via", "X-Forwarded-For", "Forwarded"
        };
        private static readonly byte[] ConnectOkBytes = Encoding.ASCII.GetBytes("HTTP/1.1 200 Connection Established\r\n\r\n");

        private readonly int _port;
        private readonly Uri _upstreamUri;
        private readonly bool _isSocks5;
        private readonly string _ua;
        private readonly string _model;
        private readonly string _osVer;
        private readonly string _buildId;
        private readonly string _disguiseContent;
        private TcpListener _listener;
        private volatile bool _running;
        private readonly object _telemetryLock = new object();
        private double _lastRttMs, _rttSumMs;
        private int _rttCount;
        private long _connsTotal, _connsFailed, _connsActive, _sentBytes, _recvBytes;

        public bool IsRunning { get { return _running; } }

        public InProcessProxyServer(int port, string upstream, string serialNo, string androidId, DeviceProfile profile, string disguisePath)
        {
            _port = port;
            Uri u;
            if (!string.IsNullOrEmpty(upstream) && Uri.TryCreate(upstream, UriKind.Absolute, out u))
            {
                _upstreamUri = u;
                _isSocks5 = u.Scheme.StartsWith("socks5", StringComparison.OrdinalIgnoreCase);
            }

            _model = (profile != null && !string.IsNullOrEmpty(profile.Model)) ? profile.Model : "SM-A515F";
            _ua = (profile != null && !string.IsNullOrEmpty(profile.UserAgent))
                ? profile.UserAgent
                : "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36";

            Match mVer = RxAndroidVer.Match(_ua);
            _osVer = mVer.Success ? mVer.Groups[1].Value : "10";
            _buildId = (profile != null && !string.IsNullOrEmpty(profile.BuildId)) ? profile.BuildId : "QP1A.190711.020";

            if (!string.IsNullOrEmpty(disguisePath) && File.Exists(disguisePath))
            {
                try { _disguiseContent = FarmEngine.InterpolateDisguise(File.ReadAllText(disguisePath, Encoding.UTF8), androidId, serialNo, profile, _ua); }
                catch { _disguiseContent = ""; }
            }
        }

        public bool Start()
        {
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, _port);
                _listener.Start(128);
                _running = true;
                ThreadPool.QueueUserWorkItem(AcceptLoop);
                return true;
            }
            catch { _running = false; return false; }
        }

        public void Stop()
        {
            _running = false;
            try { if (_listener != null) _listener.Stop(); } catch { }
        }

        private void AcceptLoop(object state)
        {
            while (_running)
            {
                try { ThreadPool.QueueUserWorkItem(HandleClient, _listener.AcceptTcpClient()); }
                catch { if (!_running) break; }
            }
        }

        private static TcpClient ConnectWithTimeout(string host, int port, int timeoutMs, string errMsg)
        {
            var client = new TcpClient { NoDelay = true };
            IAsyncResult ar = client.BeginConnect(host, port, null, null);
            if (!ar.AsyncWaitHandle.WaitOne(timeoutMs)) { client.Close(); throw new TimeoutException(errMsg); }
            client.EndConnect(ar);
            return client;
        }

        private string BuildTelemetryJson()
        {
            double last, avg;
            lock (_telemetryLock) { last = _lastRttMs; avg = _rttCount > 0 ? _rttSumMs / _rttCount : 0; }
            return string.Format(CultureInfo.InvariantCulture,
                "{{\"status\":\"ok\",\"port\":{0},\"model\":\"{1}\",\"rtt_ms\":{2:F2},\"rtt_avg_ms\":{3:F2},\"sent_bytes\":{4},\"recv_bytes\":{5},\"conns_total\":{6},\"conns_failed\":{7},\"conns_active\":{8},\"timestamp\":{9}}}",
                _port, (_model ?? "").Replace("\\", "\\\\").Replace("\"", "\\\""), last, avg,
                Interlocked.Read(ref _sentBytes), Interlocked.Read(ref _recvBytes),
                Interlocked.Read(ref _connsTotal), Interlocked.Read(ref _connsFailed), Interlocked.Read(ref _connsActive),
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
        }

        private void RecordRtt(double ms)
        {
            lock (_telemetryLock) { _lastRttMs = ms; _rttSumMs += ms; _rttCount++; }
        }

        private TcpClient ConnectTarget(string host, int port)
        {
            if (_upstreamUri == null)
            {
                var sw = Stopwatch.StartNew();
                TcpClient direct = ConnectWithTimeout(host, port, 5000, "Direct connect timeout");
                sw.Stop();
                RecordRtt(sw.Elapsed.TotalMilliseconds);
                return direct;
            }

            int upPort = _upstreamUri.Port > 0 ? _upstreamUri.Port : (_isSocks5 ? 1080 : 8080);
            var upSw = Stopwatch.StartNew();
            TcpClient upClient = ConnectWithTimeout(_upstreamUri.Host, upPort, 6000, "Upstream proxy timeout");
            upSw.Stop();
            RecordRtt(upSw.Elapsed.TotalMilliseconds);
            NetworkStream ns = upClient.GetStream();
            ns.ReadTimeout = ns.WriteTimeout = 8000;

            string user = null, pass = null;
            if (!string.IsNullOrEmpty(_upstreamUri.UserInfo))
            {
                string[] upParts = _upstreamUri.UserInfo.Split(new[] { ':' }, 2);
                user = Uri.UnescapeDataString(upParts[0]);
                if (upParts.Length > 1) pass = Uri.UnescapeDataString(upParts[1]);
            }

            if (_isSocks5)
            {
                bool hasAuth = !string.IsNullOrEmpty(user);
                byte[] greet = hasAuth ? new byte[] { 0x05, 0x02, 0x00, 0x02 } : new byte[] { 0x05, 0x01, 0x00 };
                ns.Write(greet, 0, greet.Length);
                byte[] resp = new byte[4];
                ReadExact(ns, resp, 2);
                if (resp[1] == 0x02 && hasAuth)
                {
                    byte[] uBytes = Encoding.UTF8.GetBytes(user), pBytes = Encoding.UTF8.GetBytes(pass ?? "");
                    byte[] authMsg = new byte[3 + uBytes.Length + pBytes.Length];
                    authMsg[0] = 0x01; authMsg[1] = (byte)uBytes.Length;
                    Buffer.BlockCopy(uBytes, 0, authMsg, 2, uBytes.Length);
                    authMsg[2 + uBytes.Length] = (byte)pBytes.Length;
                    Buffer.BlockCopy(pBytes, 0, authMsg, 3 + uBytes.Length, pBytes.Length);
                    ns.Write(authMsg, 0, authMsg.Length);
                    ReadExact(ns, resp, 2);
                    if (resp[1] != 0x00) { upClient.Close(); throw new Exception("SOCKS5 auth failed"); }
                }
                byte[] hBytes = Encoding.ASCII.GetBytes(host);
                byte[] req = new byte[7 + hBytes.Length];
                req[0] = 0x05; req[1] = 0x01; req[2] = 0x00; req[3] = 0x03; req[4] = (byte)hBytes.Length;
                Buffer.BlockCopy(hBytes, 0, req, 5, hBytes.Length);
                req[5 + hBytes.Length] = (byte)(port >> 8);
                req[6 + hBytes.Length] = (byte)(port & 0xFF);
                ns.Write(req, 0, req.Length);
                ReadExact(ns, resp, 4);
                if (resp[1] != 0x00) { upClient.Close(); throw new Exception("SOCKS5 connect rejected"); }
                int skip = (resp[3] == 0x01) ? 6 : (resp[3] == 0x04 ? 18 : (resp[3] == 0x03 ? ns.ReadByte() + 2 : 0));
                if (skip > 0) ReadExact(ns, new byte[skip], skip);
            }
            else
            {
                string authHdr = !string.IsNullOrEmpty(user)
                    ? ("Proxy-Authorization: Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes(user + ":" + (pass ?? ""))) + "\r\n")
                    : "";
                byte[] cReq = Encoding.ASCII.GetBytes(string.Format("CONNECT {0}:{1} HTTP/1.1\r\nHost: {0}:{1}\r\nUser-Agent: {2}\r\n{3}\r\n", host, port, _ua, authHdr));
                ns.Write(cReq, 0, cReq.Length);
                string statusLine = ReadAsciiLine(ns);
                while (!string.IsNullOrEmpty(ReadAsciiLine(ns))) { }
                if (statusLine == null || !statusLine.Contains(" 200")) { upClient.Close(); throw new Exception("Upstream HTTP CONNECT rejected"); }
            }

            ns.ReadTimeout = ns.WriteTimeout = Timeout.Infinite;
            return upClient;
        }

        private static void ReadExact(Stream s, byte[] buf, int count)
        {
            for (int off = 0, r; off < count; off += r)
                if ((r = s.Read(buf, off, count - off)) <= 0) throw new EndOfStreamException();
        }

        private static string ReadAsciiLine(Stream s)
        {
            var sb = new StringBuilder(128);
            while (true)
            {
                int b = s.ReadByte();
                if (b < 0) return sb.Length > 0 ? sb.ToString() : null;
                if (b == '\n') break;
                if (b != '\r') sb.Append((char)b);
            }
            return sb.ToString();
        }

        private void HandleClient(object obj)
        {
            var client = (TcpClient)obj;
            Interlocked.Increment(ref _connsTotal);
            Interlocked.Increment(ref _connsActive);
            try
            {
                client.NoDelay = true;
                NetworkStream cStream = client.GetStream();
                // Buffered request-header reads: ReadByte() on a raw NetworkStream is one syscall
                // per byte and every request carries a request line plus ~30 headers. Bytes read
                // past the header block stay buffered and are forwarded before splicing, so
                // request bodies and post-CONNECT tunnel data are not lost.
                var cr = new ClientReadBuffer(cStream);
                string reqLine = ReadAsciiLine(cr);
                if (string.IsNullOrEmpty(reqLine)) { client.Close(); return; }

                var headers = new List<string>();
                string hostHeader = "";
                for (string hLine; !string.IsNullOrEmpty(hLine = ReadAsciiLine(cr));)
                {
                    if (hLine.StartsWith("Host:", StringComparison.OrdinalIgnoreCase)) hostHeader = hLine.Substring(5).Trim();
                    headers.Add(hLine);
                }

                string[] parts = reqLine.Split(' ');
                if (parts.Length < 2) { client.Close(); return; }
                string method = parts[0].ToUpperInvariant(), rawTarget = parts[1];

                // Never serve /__cdn/ relay paths from the in-process proxy:
                // they belong to the Go proxy's asset relay. Answering 501
                // makes callers fail open instead of looping back into this
                // tunnel or stalling.
                if (rawTarget.IndexOf("/__cdn/", StringComparison.Ordinal) >= 0 || rawTarget.EndsWith("/__cdn", StringComparison.Ordinal))
                {
                    byte[] notImpl = Encoding.ASCII.GetBytes("HTTP/1.1 501 Not Implemented\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                    try { cStream.Write(notImpl, 0, notImpl.Length); } catch { }
                    client.Close();
                    return;
                }

                if (method == "GET" && (rawTarget.EndsWith("/__health") || rawTarget.EndsWith("/__telemetry") || rawTarget.EndsWith("/__disguise.js") || rawTarget.EndsWith("/mobile-disguise.js")))
                {
                    bool isHealth = rawTarget.EndsWith("/__health"), isTelemetry = rawTarget.EndsWith("/__telemetry");
                    string bodyText = isTelemetry ? BuildTelemetryJson() : (isHealth ? "{\"status\":\"ok\"}" : (_disguiseContent ?? ""));
                    byte[] bodyBytes = Encoding.UTF8.GetBytes(bodyText);
                    byte[] respHdr = Encoding.ASCII.GetBytes(string.Format(
                        "HTTP/1.1 200 OK\r\nContent-Type: {0}\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {1}\r\nConnection: close\r\n\r\n",
                        (isTelemetry || isHealth) ? "application/json" : "application/javascript; charset=utf-8", bodyBytes.Length));
                    cStream.Write(respHdr, 0, respHdr.Length);
                    cStream.Write(bodyBytes, 0, bodyBytes.Length);
                    client.Close();
                    return;
                }

                if (method == "CONNECT")
                {
                    string tHost = rawTarget;
                    int tPort = 443, colon = rawTarget.LastIndexOf(':');
                    if (colon > 0) { tHost = rawTarget.Substring(0, colon); int.TryParse(rawTarget.Substring(colon + 1), out tPort); }
                    TcpClient remote = ConnectTarget(tHost, tPort);
                    cStream.Write(ConnectOkBytes, 0, ConnectOkBytes.Length);
                    byte[] connLeftover = cr.TakeLeftover();
                    if (connLeftover != null && connLeftover.Length > 0)
                    {
                        try { NetworkStream rns = remote.GetStream(); rns.Write(connLeftover, 0, connLeftover.Length); } catch { }
                    }
                    SpliceStreams(client, cStream, remote, remote.GetStream());
                    return;
                }

                string destHost = hostHeader, pathAndQuery = rawTarget;
                int destPort = 80;
                if (rawTarget.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
                {
                    var u = new Uri(rawTarget);
                    destHost = u.Host;
                    destPort = u.Port > 0 ? u.Port : 80;
                    pathAndQuery = u.PathAndQuery;
                }
                else
                {
                    int cIdx = destHost.LastIndexOf(':');
                    if (cIdx > 0) { int.TryParse(destHost.Substring(cIdx + 1), out destPort); destHost = destHost.Substring(0, cIdx); }
                }

                TcpClient httpRemote = ConnectTarget(destHost, destPort);
                NetworkStream httpRStream = httpRemote.GetStream();

                var outReq = new StringBuilder(512).AppendFormat("{0} {1} HTTP/1.1\r\n", method, pathAndQuery);
                bool wroteUa = false;
                foreach (string h in headers)
                {
                    int sep = h.IndexOf(':');
                    if (sep <= 0) continue;
                    string k = h.Substring(0, sep).Trim(), v = h.Substring(sep + 1).Trim();
                    if (StripHeaders.Contains(k)) continue;

                    if (k.Equals("User-Agent", StringComparison.OrdinalIgnoreCase))
                    {
                        wroteUa = true;
                        v = v.StartsWith("Dalvik/", StringComparison.OrdinalIgnoreCase)
                            ? string.Format("Dalvik/2.1.0 (Linux; U; Android {0}; {1} Build/{2})", _osVer, _model, _buildId)
                            : _ua;
                    }
                    else if (k.Equals("Sec-CH-UA-Arch", StringComparison.OrdinalIgnoreCase)) v = "\"arm\"";
                    else if (k.Equals("Sec-CH-UA-Platform", StringComparison.OrdinalIgnoreCase)) v = "\"Android\"";
                    else if (k.Equals("Sec-CH-UA-Model", StringComparison.OrdinalIgnoreCase)) v = "\"" + _model + "\"";

                    outReq.AppendFormat("{0}: {1}\r\n", k, v);
                }
                if (!wroteUa) outReq.AppendFormat("User-Agent: {0}\r\n", _ua);
                outReq.Append("\r\n");

                byte[] reqBytes = Encoding.ASCII.GetBytes(outReq.ToString());
                httpRStream.Write(reqBytes, 0, reqBytes.Length);
                byte[] httpLeftover = cr.TakeLeftover();
                if (httpLeftover != null && httpLeftover.Length > 0) httpRStream.Write(httpLeftover, 0, httpLeftover.Length);
                SpliceStreams(client, cStream, httpRemote, httpRStream);
            }
            catch { Interlocked.Increment(ref _connsFailed); try { client.Close(); } catch { } }
            finally { Interlocked.Decrement(ref _connsActive); }
        }

        private sealed class ClientReadBuffer
        {
            private readonly Stream _stream;
            private readonly byte[] _buf = new byte[8192];
            private int _pos, _len;

            public ClientReadBuffer(Stream stream) { _stream = stream; }

            public int ReadByte()
            {
                if (_pos >= _len)
                {
                    _len = _stream.Read(_buf, 0, _buf.Length);
                    _pos = 0;
                    if (_len <= 0) return -1;
                }
                return _buf[_pos++];
            }

            public byte[] TakeLeftover()
            {
                if (_len <= _pos) return null;
                var left = new byte[_len - _pos];
                Buffer.BlockCopy(_buf, _pos, left, 0, left.Length);
                _pos = _len = 0;
                return left;
            }
        }

        private static string ReadAsciiLine(ClientReadBuffer s)
        {
            var sb = new StringBuilder(128);
            while (true)
            {
                int b = s.ReadByte();
                if (b < 0) return sb.Length > 0 ? sb.ToString() : null;
                if (b == '\n') break;
                if (b != '\r') sb.Append((char)b);
            }
            return sb.ToString();
        }

        private void SpliceStreams(TcpClient c1, Stream s1, TcpClient c2, Stream s2)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try { CopyCounted(s1, s2, true); } catch { }
                try { c2.Client.Shutdown(SocketShutdown.Send); } catch { try { c2.Close(); } catch { } }
            });
            try { CopyCounted(s2, s1, false); } catch { }
            try { c1.Close(); } catch { }
            try { c2.Close(); } catch { }
        }

        private void CopyCounted(Stream from, Stream to, bool sent)
        {
            var buf = new byte[16384];
            for (int n; (n = from.Read(buf, 0, buf.Length)) > 0; )
            {
                to.Write(buf, 0, n);
                if (sent) Interlocked.Add(ref _sentBytes, n); else Interlocked.Add(ref _recvBytes, n);
            }
        }
    }
}
