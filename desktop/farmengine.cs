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
            Make("webgl-minimal", "WebGL Minimal (Chrome Dofus)", "generic", "generic", "WebGLMinimal", "webgl_minimal", "webgl_minimal", "ranchu", "ranchu", "ranchu", "Google", "virgl", "QSR1.210820.001", "generic/webgl_minimal/webgl_minimal:10/QSR1.210820.001/7663313:userdebug/test-keys", 1280, 720, 240, "Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)", 16384, "Mozilla/5.0 (Linux; Android 10; WebGLMinimal Build/QSR1.210820.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/112.0.5615.137 Mobile Safari/537.36", "WGLM"),
            Make("galaxy-tab-s6", "Samsung Galaxy Tab S6 (16:9 Farm HD)", "samsung", "samsung", "SM-T860", "gts6lwifixx", "gts6l", "msmnile", "qcom", "msmnile", "Qualcomm", "Snapdragon 855", "QP1A.190711.020.T860XXU1BTJ2", "samsung/gts6lwifixx/gts6l:10/QP1A.190711.020/T860XXU1BTJ2:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 640", 16384, "Mozilla/5.0 (Linux; Android 10; SM-T860 Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Safari/537.36", "R52M"),
            Make("galaxy-tab-a", "Samsung Galaxy Tab A (4:3 Tablet Mode)", "samsung", "samsung", "SM-T510", "gta3xlwifixx", "gta3xlwifi", "universal7904", "samsungexynos7904", "exynos5", "Samsung", "Exynos 7904", "QP1A.190711.020.T510XXU3BTE1", "samsung/gta3xlwifixx/gta3xlwifi:10/QP1A.190711.020/T510XXU3BTE1:user/release-keys", 1024, 768, 213, "ARM", "Mali-G71 MP2", 8192, "Mozilla/5.0 (Linux; Android 10; SM-T510 Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Safari/537.36", "R52N"),
            Make("galaxy-a51", "Samsung Galaxy A51", "samsung", "samsung", "SM-A515F", "a51nsxx", "a51", "exynos9611", "exynos9611", "exynos9611", "Samsung", "Exynos 9611", "QP1A.190711.020.A515FXXU1ATA7", "samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys", 1280, 720, 240, "ARM", "Mali-G72 MP3", 8192, "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36", "R58M"),
            Make("pixel-4a", "Google Pixel 4a", "google", "Google", "Pixel 4a", "sunfish", "sunfish", "sunfish", "sunfish", "sm6150", "Qualcomm", "Snapdragon 730G", "RQ3A.211001.001", "google/sunfish/sunfish:11/RQ3A.211001.001/7641976:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 618", 16384, "Mozilla/5.0 (Linux; Android 11; Pixel 4a Build/RQ3A.211001.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/94.0.4606.85 Mobile Safari/537.36", "0A23"),
            Make("redmi-note-9-pro", "Xiaomi Redmi Note 9 Pro", "Redmi", "Xiaomi", "Redmi Note 9 Pro", "joyeuse_eea", "joyeuse", "atoll", "qcom", "atoll", "Qualcomm", "Snapdragon 720G", "QKQ1.191215.002", "Redmi/joyeuse_eea/joyeuse:10/QKQ1.191215.002/V11.0.4.0.QJZEUXM:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 618", 16384, "Mozilla/5.0 (Linux; Android 10; Redmi Note 9 Pro Build/QKQ1.191215.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.152 Mobile Safari/537.36", "8F1B"),
            Make("galaxy-s20", "Samsung Galaxy S20", "samsung", "samsung", "SM-G980F", "hubblenadx", "hubble", "exynos990", "exynos990", "exynos990", "Samsung", "Exynos 990", "QP1A.190711.020.G980FXXU1ATCT", "samsung/hubblenadx/hubble:10/QP1A.190711.020/G980FXXU1ATCT:user/release-keys", 1280, 720, 240, "ARM", "Mali-G77 MP11", 8192, "Mozilla/5.0 (Linux; Android 10; SM-G980F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36", "RF8M"),
            Make("oneplus-8", "OnePlus 8", "OnePlus", "OnePlus", "IN2013", "OnePlus8_EEA", "OnePlus8", "kona", "qcom", "kona", "Qualcomm", "Snapdragon 865", "QKQ1.200114.002", "OnePlus/OnePlus8_EEA/OnePlus8:10/QKQ1.200114.002/2004111400:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 650", 16384, "Mozilla/5.0 (Linux; Android 10; IN2013 Build/QKQ1.200114.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/85.0.4183.127 Mobile Safari/537.36", "1F7E"),
            Make("poco-x3", "Xiaomi POCO X3 NFC", "POCO", "Xiaomi", "M2007J20CG", "surya_eea", "surya", "surya", "qcom", "sm7150", "Qualcomm", "Snapdragon 732G", "QKQ1.200512.002", "POCO/surya_eea/surya:10/QKQ1.200512.002/V12.0.4.0.QJGEUXM:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 618", 16384, "Mozilla/5.0 (Linux; Android 10; M2007J20CG Build/QKQ1.200512.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.152 Mobile Safari/537.36", "9B4C"),
            Make("galaxy-s10", "Samsung Galaxy S10", "samsung", "samsung", "SM-G973F", "beyond1lte", "beyond1", "exynos9820", "exynos9820", "exynos9820", "Samsung", "Exynos 9820", "QP1A.190711.020.G973FXXS4BTB3", "samsung/beyond1lte/beyond1:10/QP1A.190711.020/G973FXXS4BTB3:user/release-keys", 1280, 720, 240, "ARM", "Mali-G76 MP12", 8192, "Mozilla/5.0 (Linux; Android 10; SM-G973F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/81.0.4044.138 Mobile Safari/537.36", "RZ8M"),
            Make("pixel-5", "Google Pixel 5", "google", "Google", "Pixel 5", "redfin", "redfin", "redfin", "redfin", "lito", "Qualcomm", "Snapdragon 765G", "RD2A.211001.002", "google/redfin/redfin:11/RD2A.211001.002/7644766:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 620", 16384, "Mozilla/5.0 (Linux; Android 11; Pixel 5 Build/RD2A.211001.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/95.0.4638.74 Mobile Safari/537.36", "0B14"),
            Make("galaxy-s21-ultra", "Samsung Galaxy S21 Ultra", "samsung", "samsung", "SM-G998B", "p3sxxx", "p3s", "exynos2100", "exynos2100", "exynos2100", "Samsung", "Exynos 2100", "RP1A.200720.012.G998BXXU1AUB6", "samsung/p3sxxx/p3s:11/RP1A.200720.012/G998BXXU1AUB6:user/release-keys", 1280, 720, 240, "ARM", "Mali-G78 MP14", 8192, "Mozilla/5.0 (Linux; Android 11; SM-G998B Build/RP1A.200720.012; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/89.0.4389.105 Mobile Safari/537.36", "R5CR"),
            Make("pixel-6-pro", "Google Pixel 6 Pro", "google", "Google", "Pixel 6 Pro", "raven", "raven", "raven", "raven", "gs101", "Google", "Google Tensor", "SD1A.210817.036", "google/raven/raven:12/SD1A.210817.036/7805190:user/release-keys", 1280, 720, 240, "ARM", "Mali-G78 MP20", 8192, "Mozilla/5.0 (Linux; Android 12; Pixel 6 Pro Build/SD1A.210817.036; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/96.0.4664.45 Mobile Safari/537.36", "1815"),
            Make("xiaomi-11t-pro", "Xiaomi 11T Pro", "Xiaomi", "Xiaomi", "2107113SG", "vili_global", "vili", "lahaina", "qcom", "lahaina", "Qualcomm", "Snapdragon 888", "RKQ1.201112.002", "Xiaomi/vili_global/vili:11/RKQ1.201112.002/V12.5.7.0.RKDMIXM:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 660", 16384, "Mozilla/5.0 (Linux; Android 11; 2107113SG Build/RKQ1.201112.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/93.0.4577.82 Mobile Safari/537.36", "8C2F"),
            Make("oneplus-9-pro", "OnePlus 9 Pro", "OnePlus", "OnePlus", "LE2123", "OnePlus9Pro_EEA", "OnePlus9Pro", "lahaina", "qcom", "lahaina", "Qualcomm", "Snapdragon 888", "RKQ1.201105.002", "OnePlus/OnePlus9Pro_EEA/OnePlus9Pro:11/RKQ1.201105.002/2103242200:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 660", 16384, "Mozilla/5.0 (Linux; Android 11; LE2123 Build/RKQ1.201105.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.210 Mobile Safari/537.36", "2B3A"),
            Make("xperia-1-iii", "Sony Xperia 1 III", "Sony", "Sony", "XQ-BC52", "XQ-BC52_EEA", "pdx215", "lahaina", "qcom", "lahaina", "Qualcomm", "Snapdragon 888", "61.0.A.11.31", "Sony/XQ-BC52_EEA/pdx215:11/61.0.A.11.31/1:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 660", 16384, "Mozilla/5.0 (Linux; Android 11; XQ-BC52 Build/61.0.A.11.31; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/92.0.4515.159 Mobile Safari/537.36", "CQ30"),
            Make("moto-edge-20-pro", "Motorola Edge 20 Pro", "motorola", "motorola", "XT2153-1", "pstar_reteu", "pstar", "kona", "qcom", "kona", "Qualcomm", "Snapdragon 870", "RRA31.Q3-19-50", "motorola/pstar_reteu/pstar:11/RRA31.Q3-19-50/789a2:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 650", 16384, "Mozilla/5.0 (Linux; Android 11; XT2153-1 Build/RRA31.Q3-19-50; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/94.0.4606.85 Mobile Safari/537.36", "ZY22"),
            Make("realme-gt-master", "Realme GT Master Edition", "realme", "realme", "RMX3363", "RMX3363EEA", "RE5473", "lahaina", "qcom", "lahaina", "Qualcomm", "Snapdragon 778G", "RKQ1.201217.002", "realme/RMX3363EEA/RE5473:11/RKQ1.201217.002/1628169123:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 642L", 16384, "Mozilla/5.0 (Linux; Android 11; RMX3363 Build/RKQ1.201217.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/92.0.4515.131 Mobile Safari/537.36", "RM33"),
            Make("rog-phone-5", "ASUS ROG Phone 5", "asus", "asus", "ASUS_I005D", "WW_I005D", "ASUS_I005D", "lahaina", "qcom", "lahaina", "Qualcomm", "Snapdragon 888", "RKQ1.201022.002", "asus/WW_I005D/ASUS_I005D:11/RKQ1.201022.002/18.0840.2103.26-0:user/release-keys", 1280, 720, 240, "Qualcomm", "Adreno (TM) 660", 16384, "Mozilla/5.0 (Linux; Android 11; ASUS_I005D Build/RKQ1.201022.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.91 Mobile Safari/537.36", "M6AI")
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
        public string SdkRoot { get; private set; }
        public string AdbPath { get; private set; }
        public string EmulatorPath { get; private set; }
        public string QemuImgPath { get; private set; }
        public string AvdHome { get; private set; }
        public string AppBaseDir { get; private set; }
        public string ProxyExePath { get; private set; }
        public string GoldenMasterPath { get; private set; }

        public event Action<string> OnLog;

        private readonly Dictionary<string, CancellationTokenSource> _cdpSessions = new Dictionary<string, CancellationTokenSource>();
        private readonly object _cdpLock = new object();
        private static readonly ConcurrentDictionary<int, int> _cachedPortToPid = new ConcurrentDictionary<int, int>();
        private static readonly object _pidCacheLock = new object();
        private static readonly Dictionary<int, Process> _proxyProcesses = new Dictionary<int, Process>();
        private static readonly Dictionary<int, InProcessProxyServer> _inProcProxies = new Dictionary<int, InProcessProxyServer>();
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
            try
            {
                using (var searcher = new ManagementObjectSearcher(query))
                {
                    searcher.Options.Timeout = TimeSpan.FromSeconds(timeoutSec);
                    using (ManagementObjectCollection col = searcher.Get())
                        foreach (ManagementObject mo in col) using (mo) onObject(mo);
                }
            }
            catch { }
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

        internal static string InterpolateDisguise(string rawJs, string uuid, string serial, DeviceProfile p, string defaultUa = "")
        {
            if (string.IsNullOrEmpty(rawJs)) return "";
            return new StringBuilder(rawJs, rawJs.Length + 256)
                .Replace("__DEVICE_UUID__", uuid ?? "ca8ea76b0fe32f85")
                .Replace("__DEVICE_SERIAL__", serial ?? "R58M45F41EFD")
                .Replace("__DEVICE_MODEL__", (p != null && !string.IsNullOrEmpty(p.Model)) ? p.Model : "SM-A515F")
                .Replace("__DEVICE_MANUFACTURER__", (p != null && !string.IsNullOrEmpty(p.Manufacturer)) ? p.Manufacturer : "samsung")
                .Replace("__DEVICE_UA__", (p != null && !string.IsNullOrEmpty(p.UserAgent)) ? p.UserAgent : defaultUa)
                .Replace("__DEVICE_GL_VENDOR__", (p != null && !string.IsNullOrEmpty(p.GlVendor)) ? p.GlVendor : "ARM")
                .Replace("__DEVICE_GL_RENDERER__", (p != null && !string.IsNullOrEmpty(p.GlRenderer)) ? p.GlRenderer : "Mali-G72 MP3")
                .Replace("__DEVICE_MAX_TEXTURE_SIZE__", ((p != null && p.MaxTextureSize > 0) ? p.MaxTextureSize : 8192).ToString())
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
            return Directory.Exists(avdDir) && (File.Exists(Path.Combine(avdDir, "cold_run_completed.flag")) || File.Exists(Path.Combine(avdDir, @"snapshots\default_boot\snapshot.pb")));
        }

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
                    Log("Renderer check: dedicated GPU detected - using Host (DirectGL) rendering: " + adapters + ".");
                    return "host";
                }
                if (new[] { "intel", "uhd", "iris", "hd graphics" }.Any(adapters.Contains))
                {
                    Log("Renderer check: Intel integrated GPU detected - using ANGLE (Direct3D 11) to prevent WebGL buffer corruption: " + adapters + ".");
                    return "angle_indirect";
                }
                Log("Renderer check: integrated GPU detected - using ANGLE (Direct3D 11) rendering: " + adapters + ".");
                return "angle_indirect";
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

        public void EnsureAdvancedFeatures()
        {
            try
            {
                string dotAndroid = Directory.CreateDirectory(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".android")).FullName;
                const string content =
                    "GrallocSync = off\r\nGLPipeChecksum = on\r\nLogcatPipe = on\r\nGLAsyncSwap = on\r\n" +
                    "GLESDynamicVersion = on\r\nGLDMA = on\r\nGLDirectMem = on\r\nVulkan = on\r\n" +
                    "VirtioInput = on\r\nEncryptUserData = off\r\nIntelPerformanceMonitoringUnit = on\r\n" +
                    "Wifi = on\r\nHostComposition = on\r\nRefCountPipe = on\r\nDynamicPartition = on\r\nHardwareDecoder = on\r\n";

                File.WriteAllText(Path.Combine(dotAndroid, "advancedFeatures.ini"), content, Encoding.ASCII);
                if (!string.IsNullOrEmpty(SdkRoot))
                {
                    string sdkSysDir = Path.Combine(SdkRoot, SysImgSubPath);
                    if (Directory.Exists(sdkSysDir)) File.WriteAllText(Path.Combine(sdkSysDir, "advancedFeatures.ini"), content, Encoding.ASCII);
                }
            }
            catch { }
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
                var pi = new PROCESS_INFORMATION();
                var si = new STARTUPINFOW { cb = 68, dwFlags = 0x00000001, wShowWindow = 0x00000004 };
                string cmdLine = string.IsNullOrEmpty(args) ? exe : (exe + " " + args);
                string wDir = string.IsNullOrEmpty(workingDir) ? AppBaseDir : workingDir;

                IntPtr dirH = NativeMethods.CreateDirectoryW(wDir, IntPtr.Zero);
                IntPtr dirUsed = dirH != IntPtr.Zero ? dirH : NativeMethods.CreateDirectoryW(AppBaseDir, IntPtr.Zero);
                if (dirUsed == IntPtr.Zero) dirUsed = NativeMethods.CreateDirectoryW(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), IntPtr.Zero);

                bool ok = NativeMethods.CreateProcessW("", cmdLine, IntPtr.Zero, IntPtr.Zero, true, 0x00000004 | 0x00000002, IntPtr.Zero, null, ref si, out pi);
                try
                {
                    if (!ok) return "ERROR: CreateProcess failed for " + exe;
                    try
                    {
                        if (NativeMethods.WaitForSingleObject(pi.hProcess, (uint)timeoutMs) == 0) NativeMethods.TerminateProcess(pi.hProcess, 1);
                        else NativeMethods.WaitForSingleObject(pi.hProcess, 5000);
                    }
                    finally { NativeMethods.CloseHandle(pi.hThread); NativeMethods.CloseHandle(pi.hProcess); }
                }
                finally
                {
                    if (dirH != IntPtr.Zero) NativeMethods.CloseHandle(dirH);
                    if (dirUsed != IntPtr.Zero && dirUsed != dirH) NativeMethods.CloseHandle(dirUsed);
                }
                return "";
            }
            catch (Exception ex) { return "ERROR: " + ex.Message; }
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

        public static string NormalizeUpstreamProxy(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return null;
            string s = raw.Trim();
            if (s.StartsWith("[🌐] ")) s = s.Substring(5).Trim();
            if (s.StartsWith("[⚡]") || s.IndexOf("Accelerator", StringComparison.OrdinalIgnoreCase) >= 0 ||
                s.Equals("DIRECT", StringComparison.OrdinalIgnoreCase) || s.Equals("[DIRECT] DIRECT", StringComparison.OrdinalIgnoreCase) ||
                s.StartsWith("127.0.0.1:888", StringComparison.OrdinalIgnoreCase)) return null;

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
                    bool open = ar.AsyncWaitHandle.WaitOne(timeoutMs);
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
                if (IsLocalPortOpen(port, 180))
                {
                    if ((_proxyProcesses.TryGetValue(port, out existingProc) && !existingProc.HasExited) ||
                        (_inProcProxies.TryGetValue(port, out existingInProc) && existingInProc.IsRunning)) return true;

                    try
                    {
                        var tracked = new HashSet<int>(_proxyProcesses.Values.Where(p => { try { return !p.HasExited; } catch { return false; } }).Select(p => p.Id));
                        foreach (var p in Process.GetProcessesByName("dofus-net-proxy"))
                            using (p) { if (!tracked.Contains(p.Id)) { try { if (!p.HasExited) p.Kill(); } catch { } } }
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
                        Thread.Sleep(250);
                        if (proc != null && !proc.HasExited && IsLocalPortOpen(port, 250)) { _proxyProcesses[port] = proc; return true; }
                        if (proc != null && !proc.HasExited) { try { proc.Kill(); } catch { } }
                    }
                    catch { }
                }

                try
                {
                    var inProc = new InProcessProxyServer(port, normalizedUpstream, serialNo, androidId, deviceProfile, GetScriptPath("mobile-disguise.js"));
                    if (inProc.Start())
                    {
                        _inProcProxies[port] = inProc;
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

        public List<InstanceModel> GetInstances()
        {
            var list = new List<InstanceModel>();
            if (!Directory.Exists(AvdHome)) return list;

            HashSet<string> live = GetLiveSerials();
            var portToPid = new Dictionary<int, int>();

            if (HasRunningProcess("qemu-system-x86_64") || live.Count > 0)
            {
                QueryWmi("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'", 2, mo =>
                {
                    Match mp = RxQemuPort.Match((mo["CommandLine"] ?? "").ToString());
                    if (mp.Success)
                    {
                        int pNum = int.Parse(mp.Groups[1].Value), qPid = Convert.ToInt32(mo["ProcessId"]);
                        portToPid[pNum] = qPid;
                        live.Add("emulator-" + pNum);
                        lock (_pidCacheLock) _cachedPortToPid[pNum] = qPid;
                        PriorityGovernor.RegisterInstancePid(qPid, Math.Max(0, (pNum - 5554) / 2));
                    }
                });
            }
            else { lock (_pidCacheLock) _cachedPortToPid.Clear(); }

            foreach (string d in Directory.GetDirectories(AvdHome, "dofus-*.avd").OrderBy(x => x, StringComparer.Ordinal))
            {
                string name = Path.GetFileNameWithoutExtension(d);
                if (name.Equals("dofus-template", StringComparison.OrdinalIgnoreCase)) continue;

                int idx = ParseSlotIdx(name), port = 5554 + 2 * (idx - 1), ram = 1024, cores = 1, pid = 0;
                string serial = "emulator-" + port;
                ReadConfigRamCores(Path.Combine(d, "config.ini"), ref ram, ref cores);

                bool isRunning = live.Contains(serial);
                if (isRunning) portToPid.TryGetValue(port, out pid);

                string customName = "Instance #" + idx, sNo = null, aId = null, mac = null, rawProxy = null, phoneModelId = "";
                ReadIdentity(d, ref customName, ref sNo, ref aId, ref mac, ref rawProxy, ref phoneModelId);

                string proxyDisplay = !string.IsNullOrEmpty(rawProxy)
                    ? (rawProxy.StartsWith("[") ? rawProxy : ("[🌐] " + rawProxy))
                    : "[⚡] Accelerator (127.0.0.1:8880)";

                DeviceProfile prof = string.IsNullOrEmpty(phoneModelId) ? DeviceCatalog.GetDefaultProfileForIndex(idx) : DeviceCatalog.GetProfile(phoneModelId);

                list.Add(new InstanceModel
                {
                    Name = name, CustomName = customName, DisplayName = customName, Port = port, RamMb = ram, Cores = cores,
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
                "qemu.hw.mainkeys=1\r\nro.serialno={12}\r\nro.boot.serialno={12}\r\ngsm.sim.state=READY\r\ngsm.sim.operator.numeric=20801\r\ngsm.sim.operator.alpha=Orange\r\ngsm.network.type=LTE\r\n" +
                "config.disable_animations=1\r\nro.config.low_ram=false\r\nro.opengles.version=196608\r\ndebug.hwui.renderer=opengl\r\n" +
                "debug.sf.hw=1\r\ndebug.egl.hw=1\r\ndebug.composition.type=gpu\r\ndebug.sf.latch_unsignaled=1\r\ndebug.sf.disable_backpressure=1\r\n" +
                "pm.dexopt.boot=verify\r\npm.dexopt.first-boot=verify\r\npm.dexopt.install=speed-profile\r\n" +
                "dalvik.vm.dex2oat-filter=speed-profile\r\ndalvik.vm.image-dex2oat-filter=verify\r\n" +
                "dalvik.vm.dex2oat-threads={13}\r\ndalvik.vm.image-dex2oat-threads={13}\r\ndalvik.vm.boot-dex2oat-threads={13}\r\n" +
                "persist.sys.strictmode.disable=true\r\npersist.sys.strictmode.visual=false\r\ndalvik.vm.checkjni=false\r\nro.config.hw_quickpoweron=true\r\nro.config.nocheckin=1\r\n" +
                "persist.sys.disable_rescue=true\r\nsys.wifitracing.started=0\r\npersist.traced.enable=0\r\npersist.sys.timezone=Europe/Paris\r\npersist.sys.country=FR\r\npersist.sys.language=fr\r\npersist.sys.locale=fr-FR\r\n",
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
            if (finalProxy.StartsWith("[🌐] ")) finalProxy = finalProxy.Substring(5).Trim();
            if (finalProxy.StartsWith("[⚡]") || finalProxy.Contains("127.0.0.1:8880") || finalProxy.Contains("Accelerator")) finalProxy = "";

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

            if (proxy.StartsWith("[🌐] ")) proxy = proxy.Substring(5).Trim();
            if (proxy.StartsWith("[⚡]") || proxy.Contains("127.0.0.1:8880")) proxy = "";

            WriteIdentity(avdDir, resolvedCustomName, serialNo, androidId, mac, proxy, activeProfile.Id);
            File.WriteAllText(Path.Combine(avdDir, "system.prop"), GenerateSystemProp(activeProfile, serialNo, cores), Encoding.ASCII);
            EnsureOverlay(avdDir);
            UpdateConfigIni(cfgDest, name, ramMb, cores, activeProfile);

            Log(string.Format("Provisioned {0} with {1} MB RAM, {2} Core(s), spoofing {3} ({4}).", name, ramMb, cores, activeProfile.DisplayName, activeProfile.Model));
        }

        private void UpdateConfigIni(string cfgFile, string name, int ramMb, int cores, DeviceProfile profile = null)
        {
            profile = profile ?? DeviceCatalog.GetProfile("galaxy-tab-s6");
            int safeRam = Math.Max(512, ramMb), safeCores = Math.Max(1, cores);

            var existing = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
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
                { "disk.cachePartition", "no" }, { "disk.cachePartition.path", "cache.img" },
                { "image.sysdir.1", @"system-images\android-29\default\x86_64\" }, { "abi.type", "x86_64" }, { "hw.cpu.arch", "x86_64" }
            };

            if (string.Equals(profile.Id, "webgl-minimal", StringComparison.OrdinalIgnoreCase))
            {
                foreach (string sk in new[] { "hw.accelerometer", "hw.accelerometer_uncalibrated", "hw.gyroscope", "hw.gyroscope_uncalibrated", "hw.sensors.light", "hw.sensors.pressure", "hw.sensors.humidity", "hw.sensors.proximity", "hw.sensors.magnetic_field", "hw.sensors.magnetic_field_uncalibrated", "hw.sensors.orientation", "hw.sensors.temperature", "hw.sensors.heart_rate", "hw.sensors.wrist_tilt", "hw.sensors.heading" })
                    settings[sk] = "no";
            }

            foreach (var kvp in settings) existing[kvp.Key] = kvp.Value;

            var sb = new StringBuilder(existing.Count * 32);
            foreach (var kvp in existing) sb.Append(kvp.Key).Append(" = ").AppendLine(kvp.Value);
            File.WriteAllText(cfgFile, sb.ToString(), Encoding.UTF8);
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
                                string dest = Path.Combine(tplDir, e.FullName.Replace('/', '\\')), parent = Path.GetDirectoryName(dest);
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

            try { File.WriteAllText(Path.Combine(avdDir, "quickbootChoice.ini"), "saveOnExit = false\r\n", Encoding.ASCII); } catch { }

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
                            if (infoLower.Contains("error") || infoLower.Contains("corrupt") || !infoLower.Contains(GoldenMasterPath.ToLowerInvariant().Replace('\\', '/')))
                                needQcow2 = true;
                        }
                    }
                    catch { }
                }

                if (needQcow2)
                {
                    try
                    {
                        DeleteFiles(avdDir, "userdata-qemu.img.qcow2", "encryptionkey.img.qcow2", "cache.img.qcow2", "cold_run_completed.flag");
                        ForceDeleteDirectory(Path.Combine(avdDir, "snapshots"));
                        CreateQcow2Overlay(avdDir, GoldenMasterPath, "userdata-qemu.img.qcow2", 30000, true);
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
                SafeDeleteFile(Path.Combine(avdDir, "multiinstance.lock"));
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
                        psi.RedirectStandardOutput = psi.RedirectStandardError = true;
                        var logged = new Process { StartInfo = psi };
                        var writer = new StreamWriter(Path.Combine(logDir, "emulator-console.log"), false, Encoding.UTF8) { AutoFlush = true };
                        DataReceivedEventHandler onData = delegate(object s, DataReceivedEventArgs e)
                        {
                            if (e.Data != null) { try { lock (writer) writer.WriteLine(e.Data); } catch { } }
                        };
                        logged.OutputDataReceived += onData;
                        logged.ErrorDataReceived += onData;
                        if (logged.Start()) { logged.BeginOutputReadLine(); logged.BeginErrorReadLine(); return logged; }
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
            int safeRam = Math.Max(512, ramMb), safeCores = Math.Max(1, cores);
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            int launchGen = BumpLaunchGen(port);
            if (TryAdoptRunningInstance(name, port)) return;

            EnsureAdvancedFeatures();
            KillProcessForInstance(name, port);
            EnsureAvdIni(name, avdDir);
            CleanLocks(avdDir);
            EnsureOverlay(avdDir);

            int slot = Math.Max(0, (port - 5554) / 2), localProxyPort = 8880 + slot;
            string pTrim = (proxy ?? "").Trim(), upstreamProxy = null;
            bool useProxy = !pTrim.Equals("DIRECT", StringComparison.OrdinalIgnoreCase) && !pTrim.Equals("[DIRECT] DIRECT", StringComparison.OrdinalIgnoreCase);
            if (useProxy && pTrim.Length > 0 && !pTrim.Contains("[⚡]") && !pTrim.Equals("127.0.0.1:8880", StringComparison.OrdinalIgnoreCase))
                upstreamProxy = NormalizeUpstreamProxy(proxy);

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

            bool proxyActive = useProxy && EnsureProxyRunning(localProxyPort, upstreamProxy, serialNo, androidId, instProfile);
            string proxyArg = proxyActive ? string.Format("-http-proxy http://127.0.0.1:{0} ", localProxyPort) : "";
            string gpuMode = GetConfiguredGpuMode();

            string emuArgs = (instProfile != null && string.Equals(instProfile.Id, "webgl-minimal", StringComparison.OrdinalIgnoreCase))
                ? BuildWebGlMinimalArgs(name, port, safeRam, safeCores, proxyArg, avdDir, cfgFile)
                : string.Format("-avd {0} -port {1} -gpu {2} -memory {3} -cores {4} -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps {5}-prop qemu.hw.mainkeys=1",
                    name, port, gpuMode, safeRam, safeCores, proxyArg);

            Log(string.Format("Starting {0} on port {1} ({2} MB RAM / {3} Core(s) / GPU: {4} / {5})...", name, port, safeRam, safeCores, gpuMode, instProfile.DisplayName));
            try { Adb("", "start-server", 6000); } catch { }

            Process proc = StartEmulatorProcess(emuArgs, false);
            string serial = "emulator-" + port;

            try
            {
                int totalCores = Environment.ProcessorCount;
                if (totalCores >= 4 && proc != null && !proc.HasExited)
                {
                    long mask = PriorityGovernor.ComputeAffinityMask(slot, safeCores, totalCores);
                    proc.ProcessorAffinity = IntPtr.Size == 8 ? new IntPtr(mask) : new IntPtr(unchecked((int)mask));
                    Log(string.Format("Pinned {0} (PID: {1}, {2} Core(s)) to Host CPU Affinity 0x{3:X}.", name, proc.Id, safeCores, mask));
                }
            }
            catch { }

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
                    Thread.Sleep(800);
                    continue;
                }
                Thread.Sleep(500);
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
                string warmState = AdbShell(serial, "test -f /data/local/tmp/fake_cpuinfo && grep -q fake_cpuinfo /proc/mounts && getprop sys.boot_completed");
                if (!string.IsNullOrEmpty(warmState) && warmState.Trim().EndsWith("1"))
                {
                    AdbShell(serial, WakeAndStartCmd);
                    ThreadPool.QueueUserWorkItem(delegate { InjectDisguiseViaCdp(serial, port, name); });
                    try { File.WriteAllText(Path.Combine(avdDir, "cold_run_completed.flag"), DateTime.UtcNow.ToString("o"), Encoding.UTF8); } catch { }
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
                    Thread.Sleep(2000);
                    continue;
                }
                Thread.Sleep(1000);
            }

            if (!bootCompleted)
            {
                LogEmulatorConsoleTail(name, avdDir);
                Log(string.Format("Error: {0} could not boot even after automatic recovery. Diagnostics captured in {1}", name, Path.Combine(avdDir, "emulator-console.log")));
                return;
            }

            Thread.Sleep(1500);
            Adb(serial, "root", 8000);
            Thread.Sleep(400);

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
                "setprop debug.sf.hw 1; setprop debug.egl.hw 1; setprop debug.sf.latch_unsignaled 1; setprop debug.sf.disable_backpressure 1; " +
                "sysctl -w vm.swappiness=80 vm.vfs_cache_pressure=150 vm.overcommit_memory=1 >/dev/null 2>&1 || true; " +
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
                "for svc in cameraserver vendor.camera-provider-2-4 drmserver vendor.drm-hal-1-0 vendor.drm-clearkey-hal-1-2 vendor.drm-widevine-hal-1-2 mediadrmserver traced traced_probes statsd incidentd tombstoned llkd-1 mdnsd goldfish-logcat vendor.fps_hal broadcastradio-hal vendor.cas-hal-1-1 vendor.gnss_service hostapd; do " +
                "  setprop ctl.stop $svc 2>/dev/null || true; " +
                "done; " +
                "DEBLOAT_V='v4'; " +
                "if [ \"$(cat /data/local/tmp/.dofus_debloat_ver 2>/dev/null)\" != \"$DEBLOAT_V\" ]; then " +
                "  DISABLED=0; " +
                "  for pkg in com.android.cts.priv.ctsshim com.android.cts.ctsshim org.chromium.webview_shell com.android.statementservice com.android.printspooler com.android.bips com.android.camera2 com.android.music com.android.deskclock com.android.gallery3d com.android.dreams.basic com.android.dreams.phototable com.android.wallpaper.livepicker com.android.wallpaperbackup com.android.wallpapercropper com.android.calllogbackup com.android.quicksearchbox com.android.htmlviewer com.android.bookmarkprovider com.android.providers.downloads.ui com.android.providers.partnerbookmarks com.android.smspush com.android.stk com.android.calendar com.android.providers.calendar; do " +
                "    if pm disable-user --user 0 $pkg >/dev/null 2>&1; then DISABLED=$((DISABLED+1)); fi; " +
                "  done; " +
                "  EN=0; " +
                "  for pkg in com.android.inputmethod.latin com.android.keychain com.android.documentsui com.android.webview com.android.webview.apkoverlay; do " +
                "    if pm enable --user 0 $pkg >/dev/null 2>&1; then EN=$((EN+1)); fi; " +
                "  done; " +
                "  echo $DEBLOAT_V > /data/local/tmp/.dofus_debloat_ver; " +
                "  echo \"debloat: v4 pass complete - $DISABLED package(s) disabled, $EN critical package(s) enabled\"; " +
                "else echo 'debloat: instance already stripped'; fi; " +
                "echo -n 'debloat: total disabled packages now = '; pm list packages -d 2>/dev/null | wc -l; " +
                "GLES_INFO=$(dumpsys SurfaceFlinger 2>/dev/null | grep 'GLES:' | head -n 1); " +
                "WV_INFO=$(dumpsys webviewupdate 2>/dev/null | grep -E 'Current WebView package|Valid:' | tr '\n' ' '); " +
                "EGL_OK=$(ls /vendor/lib64/egl/libGLESv2_emulation.so /system/fonts/Roboto-Regular.ttf 2>/dev/null | wc -l); " +
                "echo \"debloat: webgl-stack EGL+Fonts=$EGL_OK/2 | $GLES_INFO | $WV_INFO\"; " +
                "am force-stop com.ankama.dofustouch 2>/dev/null || true";

            string setupOut = AdbShell(serial, combinedSetupCmd) ?? "";
            string setupShort = setupOut.Replace("\r", " ").Replace("\n", " ").Trim();
            if (setupShort.Length > 160) setupShort = setupShort.Substring(0, 160);
            Log(string.Format("{0} | guest setup pass returned {1} chars: {2}", name, setupOut.Length, setupShort));
            foreach (Match dLine in RxDebloatLine.Matches(setupOut)) Log(string.Format("{0} | {1}", name, dLine.Value.Trim()));

            Adb(serial, "unroot", 6000);
            Thread.Sleep(250);

            if (!shutdownAfterSnapshot)
            {
                AdbShell(serial, WakeAndStartCmd);
                Thread.Sleep(1500);
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
            Log(string.Format("{0} ({1}) is ready! Dofus Touch running in foreground (Boot: {2:F1}s).", name, serial, sw.Elapsed.TotalSeconds));
        }

        public void WarmInstanceColdRunSync(string name, int port, int ramMb, int cores)
        {
            ramMb = Math.Max(512, ramMb);
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

                string[] carrierCodes = { "20801", "20810", "20820", "20815" }, carrierNames = { "Orange F", "SFR", "Bouygues Telecom", "Free" };
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
                    "setprop gsm.sim.operator.iso-country fr\nsetprop gsm.operator.iso-country fr\n" +
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

                while (!token.IsCancellationRequested)
                {
                    string pidOut = AdbShell(serial, "pidof com.ankama.dofustouch").Trim();
                    if (string.IsNullOrEmpty(pidOut)) { token.WaitHandle.WaitOne(3000); continue; }

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

            KillProcessForInstanceFast(name, pNum);
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
                ForEachToolhelpProcess(pe =>
                {
                    if (found > 0) return;
                    if (pe.szExeFile == "qemu-system-x86_64.exe" || pe.szExeFile == "emulator.exe")
                    {
                        int pid = (int)pe.th32ProcessID, cachedVal;
                        lock (_pidCacheLock)
                        {
                            if ((_cachedPortToPid.TryGetValue(port, out cachedVal) && cachedVal == pid) ||
                                (_cachedPortToPid.TryGetValue(pid, out cachedVal) && cachedVal == port))
                                found = pid;
                        }
                    }
                });
            }
            catch { }
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

            HashSet<string> live = GetLiveSerials();
            Log(string.Format("Stopping all active instances ({0} running)...", live.Count));
            if (live.Count > 0)
            {
                using (var countdown = new CountdownEvent(live.Count))
                {
                    foreach (string s in live)
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

            foreach (string pName in new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy" })
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(pName))
                        using (p) { try { if (pName == "dofus-net-proxy" || !p.WaitForExit(4500)) { p.Kill(); p.WaitForExit(1000); } } catch { } }
                }
                catch { }
            }

            try { if (Directory.Exists(AvdHome)) foreach (string d in Directory.EnumerateDirectories(AvdHome, "*.avd")) CleanLocks(d); } catch { }
            Log("All instances and emulator processes have been completely stopped.");
        }

        public string GetNextAvailableSlot()
        {
            var existing = new HashSet<string>(
                Directory.Exists(AvdHome) ? Directory.EnumerateDirectories(AvdHome, "dofus-*.avd").Select(Path.GetFileNameWithoutExtension) : Enumerable.Empty<string>(),
                StringComparer.OrdinalIgnoreCase);

            return Enumerable.Range(1, 32).Select(i => string.Format("dofus-{0:D2}", i)).FirstOrDefault(c => !existing.Contains(c))
                   ?? ("dofus-" + (existing.Count + 1));
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
                ? BuildWebGlMinimalArgs(name, port, ramMb, cores, proxyArg, avdDir, cfgFile)
                : string.Format("-avd {0} -port {1} -gpu {2} -memory {3} -cores {4} -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps {5}-prop qemu.hw.mainkeys=1",
                    name, port, gpuMode, ramMb, cores, proxyArg ?? "");
        }

        private string BuildWebGlMinimalArgs(string name, int port, int ramMb, int cores, string proxyArg, string avdDir, string cfgFile)
        {
            return string.Format("-avd {0} -port {1} -gpu {2} -memory {3} -cores {4} -no-snapshot-load -no-audio -accel on -skip-adb-auth -no-metrics -no-boot-anim -no-passive-gps {5}-prop qemu.hw.mainkeys=1",
                name, port, GetConfiguredGpuMode(), ramMb, cores, proxyArg ?? "");
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
                if (hWnd != IntPtr.Zero) { Win32.ShowWindow(hWnd, Win32.SW_RESTORE); Win32.SetForegroundWindow(hWnd); }
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

        private TcpClient ConnectTarget(string host, int port)
        {
            if (_upstreamUri == null) return ConnectWithTimeout(host, port, 5000, "Direct connect timeout");

            int upPort = _upstreamUri.Port > 0 ? _upstreamUri.Port : (_isSocks5 ? 1080 : 8080);
            TcpClient upClient = ConnectWithTimeout(_upstreamUri.Host, upPort, 6000, "Upstream proxy timeout");
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
            try
            {
                client.NoDelay = true;
                NetworkStream cStream = client.GetStream();
                string reqLine = ReadAsciiLine(cStream);
                if (string.IsNullOrEmpty(reqLine)) { client.Close(); return; }

                var headers = new List<string>();
                string hostHeader = "";
                for (string hLine; !string.IsNullOrEmpty(hLine = ReadAsciiLine(cStream));)
                {
                    if (hLine.StartsWith("Host:", StringComparison.OrdinalIgnoreCase)) hostHeader = hLine.Substring(5).Trim();
                    headers.Add(hLine);
                }

                string[] parts = reqLine.Split(' ');
                if (parts.Length < 2) { client.Close(); return; }
                string method = parts[0].ToUpperInvariant(), rawTarget = parts[1];

                if (method == "GET" && (rawTarget.EndsWith("/__health") || rawTarget.EndsWith("/__disguise.js") || rawTarget.EndsWith("/mobile-disguise.js")))
                {
                    bool isHealth = rawTarget.EndsWith("/__health");
                    byte[] bodyBytes = Encoding.UTF8.GetBytes(isHealth ? "{\"status\":\"ok\"}" : (_disguiseContent ?? ""));
                    byte[] respHdr = Encoding.ASCII.GetBytes(string.Format(
                        "HTTP/1.1 200 OK\r\nContent-Type: {0}\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {1}\r\nConnection: close\r\n\r\n",
                        isHealth ? "application/json" : "application/javascript; charset=utf-8", bodyBytes.Length));
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
                SpliceStreams(client, cStream, httpRemote, httpRStream);
            }
            catch { try { client.Close(); } catch { } }
        }

        private static void SpliceStreams(TcpClient c1, Stream s1, TcpClient c2, Stream s2)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try { s1.CopyTo(s2, 16384); } catch { }
                try { c2.Client.Shutdown(SocketShutdown.Send); } catch { try { c2.Close(); } catch { } }
            });
            try { s2.CopyTo(s1, 16384); } catch { }
            try { c1.Close(); } catch { }
            try { c2.Close(); } catch { }
        }
    }
}
