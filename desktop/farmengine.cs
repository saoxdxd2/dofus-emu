using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.Win32;
using System.Security.Cryptography;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Threading.Tasks;
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

        // All phone profiles use optimized 1280x720 @ 240dpi (or 1024x768 @ 213dpi for 4:3 tablet)
        // to cut Guest VRAM/SurfaceFlinger memory by 70-85% and guarantee uniform tiled aspect ratios.
        private static readonly List<DeviceProfile> _profiles = new List<DeviceProfile>
        {
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

        public static List<DeviceProfile> GetAllProfiles()
        {
            return new List<DeviceProfile>(_profiles);
        }

        public static DeviceProfile GetProfile(string id)
        {
            if (string.IsNullOrEmpty(id)) return _profiles[0];
            foreach (var p in _profiles)
            {
                if (string.Equals(p.Id, id, StringComparison.OrdinalIgnoreCase))
                    return p;
            }
            return _profiles[0];
        }

        public static DeviceProfile GetDefaultProfileForIndex(int slotIndex)
        {
            int idx = slotIndex - 1;
            if (idx < 0) idx = 0;
            return _profiles[idx % _profiles.Count];
        }
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
        private static readonly Dictionary<int, int> _cachedPortToPid = new Dictionary<int, int>();
        private static readonly object _pidCacheLock = new object();

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

        public FarmEngine() : this(null, false)
        {
        }

        public FarmEngine(string explicitBaseDir, bool skipIntegrityCheck = false)
        {
            AppBaseDir = !string.IsNullOrEmpty(explicitBaseDir)
                ? explicitBaseDir.TrimEnd('\\')
                : AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            if (!File.Exists(Path.Combine(AppBaseDir, @"scripts\mobile-disguise.js")) && Directory.GetParent(AppBaseDir) != null)
            {
                string parentDir = Directory.GetParent(AppBaseDir).FullName.TrimEnd('\\');
                if (File.Exists(Path.Combine(parentDir, @"scripts\mobile-disguise.js")))
                {
                    AppBaseDir = parentDir;
                }
            }

            if (!File.Exists(Path.Combine(AppBaseDir, @"scripts\mobile-disguise.js")))
            {
                try
                {
                    string asmLoc = System.Reflection.Assembly.GetExecutingAssembly().Location;
                    if (!string.IsNullOrEmpty(asmLoc))
                    {
                        string asmDir = Path.GetDirectoryName(asmLoc).TrimEnd('\\');
                        if (File.Exists(Path.Combine(asmDir, @"scripts\mobile-disguise.js")))
                            AppBaseDir = asmDir;
                    }
                }
                catch { }
            }

            if (!File.Exists(Path.Combine(AppBaseDir, @"scripts\mobile-disguise.js")))
            {
                string cwd = Directory.GetCurrentDirectory().TrimEnd('\\');
                if (File.Exists(Path.Combine(cwd, @"scripts\mobile-disguise.js")))
                    AppBaseDir = cwd;
            }
            AvdHome = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".android\avd");

            ResolveSdkPaths(skipIntegrityCheck);
            EnsureDirectories();

            // Export environment variables for emulator and child processes
            Environment.SetEnvironmentVariable("ANDROID_SDK_ROOT", SdkRoot);
            Environment.SetEnvironmentVariable("ANDROID_HOME", SdkRoot);
            Environment.SetEnvironmentVariable("ANDROID_AVD_HOME", AvdHome);

            string curPath = Environment.GetEnvironmentVariable("PATH") ?? "";
            string emuDir = Path.Combine(SdkRoot, "emulator");
            string ptDir = Path.Combine(SdkRoot, "platform-tools");
            if (!curPath.Contains(emuDir))
            {
                Environment.SetEnvironmentVariable("PATH", emuDir + ";" + ptDir + ";" + curPath);
            }
        }

        private void Log(string message)
        {
            var handler = OnLog;
            if (handler != null)
            {
                try
                {
                    handler(string.Format("[{0:HH:mm:ss}] {1}", DateTime.Now, message));
                }
                catch { }
            }
        }

        private void ResolveSdkPaths(bool skipIntegrityCheck = false)
        {
            List<string> candidates = new List<string>
            {
                Path.Combine(AppBaseDir, "sdk"),
                Path.Combine(AppBaseDir, @"sdk\sdk"),
                Environment.GetEnvironmentVariable("DOFUS_FARM_HOME") != null ? Path.Combine(Environment.GetEnvironmentVariable("DOFUS_FARM_HOME"), "sdk") : null,
                Environment.GetEnvironmentVariable("DOFUS_FARM_HOME") != null ? Path.Combine(Environment.GetEnvironmentVariable("DOFUS_FARM_HOME"), @"sdk\sdk") : null,
                GetRegistrySdkPath(),
                Environment.GetEnvironmentVariable("ANDROID_SDK_ROOT"),
                Environment.GetEnvironmentVariable("ANDROID_HOME"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @"DofusFarm\sdk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @"DofusFarm\sdk\sdk"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @"AppData\Local\Android\Sdk")
            };

            foreach (string c in candidates)
            {
                if (!string.IsNullOrEmpty(c) && File.Exists(Path.Combine(c, @"platform-tools\adb.exe")))
                {
                    SdkRoot = c;
                    break;
                }
            }

            if (string.IsNullOrEmpty(SdkRoot))
            {
                SdkRoot = Path.Combine(AppBaseDir, "sdk");
            }

            AdbPath = Path.Combine(SdkRoot, @"platform-tools\adb.exe");
            EmulatorPath = Path.Combine(SdkRoot, @"emulator\emulator.exe");
            QemuImgPath = Path.Combine(SdkRoot, @"emulator\qemu-img.exe");

            ProxyExePath = Path.Combine(AppBaseDir, "dofus-net-proxy.exe");
            if (!File.Exists(ProxyExePath)) ProxyExePath = Path.Combine(AppBaseDir, @"scripts\dofus-net-proxy.exe");
            if (!File.Exists(ProxyExePath) && Environment.GetEnvironmentVariable("DOFUS_FARM_HOME") != null)
            {
                ProxyExePath = Path.Combine(Environment.GetEnvironmentVariable("DOFUS_FARM_HOME"), "dofus-net-proxy.exe");
            }

            GoldenMasterPath = Path.Combine(AvdHome, @"dofus-template.avd\userdata-golden.img");
            if (!File.Exists(GoldenMasterPath)) GoldenMasterPath = Path.Combine(AvdHome, @"dofus.avd\userdata-golden.img");
            if (!File.Exists(GoldenMasterPath)) GoldenMasterPath = Path.Combine(AppBaseDir, "userdata-golden.img");
            if (!File.Exists(GoldenMasterPath) && !string.IsNullOrEmpty(SdkRoot))
            {
                string sdkUd = Path.Combine(SdkRoot, @"system-images\android-29\default\x86_64\userdata.img");
                if (File.Exists(sdkUd))
                {
                    try
                    {
                        string tplDir = Path.Combine(AvdHome, "dofus-template.avd");
                        if (!Directory.Exists(tplDir)) Directory.CreateDirectory(tplDir);
                        string destGolden = Path.Combine(tplDir, "userdata-golden.img");
                        File.Copy(sdkUd, destGolden, true);
                        GoldenMasterPath = destGolden;
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
                    using (FileStream fs = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    {
                        byte[] magic = new byte[4];
                        if (fs.Read(magic, 0, 4) == 4)
                        {
                            if (magic[0] == 0x51 && magic[1] == 0x46 && magic[2] == 0x49 && magic[3] == 0xFB)
                            {
                                return "qcow2";
                            }
                        }
                    }
                }
            }
            catch { }
            return "raw";
        }

        public bool IsInstanceWarmReady(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir)) return false;
            if (File.Exists(Path.Combine(avdDir, "cold_run_completed.flag"))) return true;
            if (File.Exists(Path.Combine(avdDir, @"snapshots\default_boot\snapshot.pb"))) return true;
            return false;
        }

        public void InvalidateWarmSnapshot(string name)
        {
            if (string.IsNullOrEmpty(name)) return;
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir)) return;
            try
            {
                string flag = Path.Combine(avdDir, "cold_run_completed.flag");
                if (File.Exists(flag)) File.Delete(flag);
                string snapDir = Path.Combine(avdDir, @"snapshots\default_boot");
                if (Directory.Exists(snapDir)) ForceDeleteDirectory(snapDir);
            }
            catch { }
        }

        public void EnsureAdvancedFeatures()
        {
            try
            {
                string dotAndroid = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".android");
                if (!Directory.Exists(dotAndroid)) Directory.CreateDirectory(dotAndroid);
                string iniPath = Path.Combine(dotAndroid, "advancedFeatures.ini");
                string content = 
                    "GrallocSync = on\r\n" +
                    "LogcatPipe = on\r\n" +
                    "GLAsyncSwap = on\r\n" +
                    "GLESDynamicVersion = on\r\n" +
                    "GLDMA = on\r\n" +
                    "VirtioInput = on\r\n" +
                    "EncryptUserData = off\r\n" +
                    "IntelPerformanceMonitoringUnit = on\r\n" +
                    "Wifi = on\r\n" +
                    "HostComposition = on\r\n" +
                    "DynamicPartition = on\r\n" +
                    "HardwareDecoder = on\r\n" +
                    "GLDirectMem = on\r\n";
                File.WriteAllText(iniPath, content, Encoding.ASCII);

                // Also ensure in SDK system images dir if present
                if (!string.IsNullOrEmpty(SdkRoot))
                {
                    string sdkIni = Path.Combine(SdkRoot, @"system-images\android-29\default\x86_64\advancedFeatures.ini");
                    if (Directory.Exists(Path.GetDirectoryName(sdkIni)))
                    {
                        File.WriteAllText(sdkIni, content, Encoding.ASCII);
                    }
                }
            }
            catch { }
        }

        private string GetRegistrySdkPath()
        {
            try
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\DofusFarm"))
                {
                    if (k != null)
                    {
                        object val = k.GetValue("InstallPath");
                        if (val != null)
                        {
                            string p1 = Path.Combine(val.ToString(), "sdk");
                            if (File.Exists(Path.Combine(p1, @"platform-tools\adb.exe"))) return p1;
                            string p2 = Path.Combine(p1, "sdk");
                            if (File.Exists(Path.Combine(p2, @"platform-tools\adb.exe"))) return p2;
                            return p1;
                        }
                    }
                }
            }
            catch { }
            return null;
        }

        private void EnsureDirectories()
        {
            if (!Directory.Exists(AvdHome))
            {
                Directory.CreateDirectory(AvdHome);
            }
        }

        public string RunCommand(string exe, string args, int timeoutMs = 30000, string workingDir = null)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = exe,
                    Arguments = args,
                    CreateNoWindow = true,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    WorkingDirectory = string.IsNullOrEmpty(workingDir) ? AppBaseDir : workingDir
                };

                StringBuilder stdOut = new StringBuilder();
                StringBuilder stdErr = new StringBuilder();
                object outLock = new object();

                using (Process p = new Process())
                {
                    p.StartInfo = psi;
                    p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock (outLock) { stdOut.AppendLine(e.Data); } };
                    p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock (outLock) { stdErr.AppendLine(e.Data); } };

                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();

                    if (!p.WaitForExit(timeoutMs))
                    {
                        try { p.Kill(); } catch { }
                    }
                    else
                    {
                        p.WaitForExit();
                    }

                    lock (outLock)
                    {
                        return stdOut.ToString().Trim();
                    }
                }
            }
            catch (Exception ex)
            {
                return "ERROR: " + ex.Message;
            }
        }

        public string Adb(string serial, string args, int timeoutMs = 30000)
        {
            string fullArgs = string.IsNullOrEmpty(serial) ? args : string.Format("-s {0} {1}", serial, args);
            return RunCommand(AdbPath, fullArgs, timeoutMs);
        }

        public string AdbShell(string serial, string shellCommand)
        {
            return Adb(serial, "shell " + shellCommand);
        }

        public HashSet<string> GetLiveSerials()
        {
            HashSet<string> live = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            bool anyEmuProcess = false;
            try
            {
                Process[] qProcs = Process.GetProcessesByName("qemu-system-x86_64");
                if (qProcs != null && qProcs.Length > 0)
                {
                    anyEmuProcess = true;
                    for (int i = 0; i < qProcs.Length; i++) { try { qProcs[i].Dispose(); } catch { } }
                }
                if (!anyEmuProcess)
                {
                    Process[] eProcs = Process.GetProcessesByName("emulator");
                    if (eProcs != null && eProcs.Length > 0)
                    {
                        anyEmuProcess = true;
                        for (int i = 0; i < eProcs.Length; i++) { try { eProcs[i].Dispose(); } catch { } }
                    }
                }
            }
            catch { anyEmuProcess = true; }
            if (!anyEmuProcess) return live;

            string outDevices = Adb("", "devices");
            using (StringReader sr = new StringReader(outDevices))
            {
                string line;
                while ((line = sr.ReadLine()) != null)
                {
                    line = line.Trim();
                    if (line.StartsWith("emulator-"))
                    {
                        string[] parts = line.Split(new[] { '\t', ' ' }, StringSplitOptions.RemoveEmptyEntries);
                        if (parts.Length >= 2 && parts[0].StartsWith("emulator-") && parts[1].Equals("device", StringComparison.OrdinalIgnoreCase))
                        {
                            live.Add(parts[0].Trim());
                        }
                    }
                }
            }
            return live;
        }

        private static readonly Dictionary<int, Process> _proxyProcesses = new Dictionary<int, Process>();
        private static readonly Dictionary<int, InProcessProxyServer> _inProcProxies = new Dictionary<int, InProcessProxyServer>();
        private static readonly object _proxyLock = new object();

        public static string NormalizeUpstreamProxy(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return null;
            string s = raw.Trim();
            if (s.StartsWith("[🌐] ")) s = s.Substring(5).Trim();
            if (s.StartsWith("[⚡]") || s.IndexOf("Accelerator", StringComparison.OrdinalIgnoreCase) >= 0 ||
                s.Equals("DIRECT", StringComparison.OrdinalIgnoreCase) || s.Equals("[DIRECT] DIRECT", StringComparison.OrdinalIgnoreCase) ||
                s.StartsWith("127.0.0.1:888", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }
            string scheme = "http";
            string rest = s;
            int schemeIdx = s.IndexOf("://", StringComparison.Ordinal);
            if (schemeIdx > 0)
            {
                scheme = s.Substring(0, schemeIdx).ToLowerInvariant();
                rest = s.Substring(schemeIdx + 3);
            }
            if (!rest.Contains("@"))
            {
                string[] parts = rest.Split(':');
                if (parts.Length == 4)
                {
                    // Convert host:port:user:pass -> scheme://user:pass@host:port
                    return string.Format("{0}://{1}:{2}@{3}:{4}", scheme, Uri.EscapeDataString(parts[2]), Uri.EscapeDataString(parts[3]), parts[0], parts[1]);
                }
            }
            return string.Format("{0}://{1}", scheme, rest);
        }

        public static string GenerateRealisticMac(DeviceProfile profile, int slotIdx, string seedStr = null)
        {
            string manuf = (profile != null && !string.IsNullOrEmpty(profile.Manufacturer)) ? profile.Manufacturer.ToLowerInvariant() : "samsung";
            string oui = "F8:E6:1A"; // Samsung physical IEEE OUI
            if (manuf.Contains("google")) oui = "3C:28:6D";
            else if (manuf.Contains("xiaomi") || manuf.Contains("redmi") || manuf.Contains("poco")) oui = "64:CC:2E";
            else if (manuf.Contains("oneplus")) oui = "94:65:2D";
            else if (manuf.Contains("sony")) oui = "D0:51:62";
            else if (manuf.Contains("motorola")) oui = "F8:E0:79";
            else if (manuf.Contains("realme")) oui = "C4:06:83";
            else if (manuf.Contains("asus")) oui = "04:D9:F5";

            uint h = (uint)(2166136261UL ^ (uint)slotIdx);
            string s = seedStr ?? Guid.NewGuid().ToString("N");
            for (int i = 0; i < s.Length; i++)
            {
                h ^= s[i];
                h *= 16777619U;
            }
            byte b1 = (byte)((h & 0xFF) ^ (slotIdx & 0xFF));
            byte b2 = (byte)((h >> 8) & 0xFF);
            byte b3 = (byte)(((h >> 16) & 0xFE) | 0x01);
            return string.Format("{0}:{1:X2}:{2:X2}:{3:X2}", oui, b1, b2, b3);
        }

        private static bool IsLocalPortOpen(int port, int timeoutMs = 200)
        {
            try
            {
                using (var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp))
                {
                    var result = socket.BeginConnect(new IPEndPoint(IPAddress.Loopback, port), null, null);
                    bool open = result.AsyncWaitHandle.WaitOne(timeoutMs);
                    if (open)
                    {
                        try { socket.EndConnect(result); } catch { }
                    }
                    else
                    {
                        try { socket.Close(); } catch { }
                    }
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
                if (IsLocalPortOpen(port, 180))
                {
                    if ((_proxyProcesses.ContainsKey(port) && !_proxyProcesses[port].HasExited) ||
                        (_inProcProxies.ContainsKey(port) && _inProcProxies[port].IsRunning))
                    {
                        return true;
                    }

                    try
                    {
                        var trackedIds = new HashSet<int>();
                        foreach (var kvp in _proxyProcesses)
                        {
                            try { if (!kvp.Value.HasExited) trackedIds.Add(kvp.Value.Id); } catch { }
                        }
                        foreach (var p in Process.GetProcessesByName("dofus-net-proxy"))
                        {
                            using (p)
                            {
                                if (!trackedIds.Contains(p.Id))
                                {
                                    try { if (!p.HasExited) p.Kill(); } catch { }
                                }
                            }
                        }
                        Thread.Sleep(200);
                    }
                    catch { }
                }

                if (_proxyProcesses.ContainsKey(port))
                {
                    try { if (!_proxyProcesses[port].HasExited) _proxyProcesses[port].Kill(); } catch { }
                    _proxyProcesses.Remove(port);
                }
                if (_inProcProxies.ContainsKey(port))
                {
                    try { _inProcProxies[port].Stop(); } catch { }
                    _inProcProxies.Remove(port);
                }

                string cacheDir = Path.Combine(AppBaseDir, "asset_cache");
                if (File.Exists(ProxyExePath))
                {
                    string args = string.Format("-port {0} -cache \"{1}\" -quiet", port, cacheDir);
                    if (!string.IsNullOrEmpty(normalizedUpstream))
                    {
                        args += string.Format(" -upstream \"{0}\"", normalizedUpstream);
                        Log(string.Format("Starting Go Network Spoofing Proxy (Port {0} -> Upstream: {1})...", port, normalizedUpstream));
                    }
                    else
                    {
                        Log(string.Format("Starting Go Network Spoofing Proxy (Port {0} -> Direct)...", port));
                    }

                    string fullArgs = args;
                    if (!string.IsNullOrEmpty(serialNo)) fullArgs += string.Format(" -serial \"{0}\"", serialNo);
                    if (!string.IsNullOrEmpty(androidId)) fullArgs += string.Format(" -uuid \"{0}\"", androidId);
                    if (deviceProfile != null)
                    {
                        if (!string.IsNullOrEmpty(deviceProfile.Model)) fullArgs += string.Format(" -model \"{0}\"", deviceProfile.Model);
                        if (!string.IsNullOrEmpty(deviceProfile.Manufacturer)) fullArgs += string.Format(" -manufacturer \"{0}\"", deviceProfile.Manufacturer);
                        if (!string.IsNullOrEmpty(deviceProfile.UserAgent)) fullArgs += string.Format(" -ua \"{0}\"", deviceProfile.UserAgent.Replace("\"", "\\\""));
                        if (!string.IsNullOrEmpty(deviceProfile.GlVendor)) fullArgs += string.Format(" -gl-vendor \"{0}\"", deviceProfile.GlVendor);
                        if (!string.IsNullOrEmpty(deviceProfile.GlRenderer)) fullArgs += string.Format(" -gl-renderer \"{0}\"", deviceProfile.GlRenderer);
                        if (deviceProfile.MaxTextureSize > 0) fullArgs += string.Format(" -texture-size \"{0}\"", deviceProfile.MaxTextureSize);
                    }

                    try
                    {
                        Process proc = Process.Start(new ProcessStartInfo
                        {
                            FileName = ProxyExePath,
                            Arguments = fullArgs,
                            WorkingDirectory = AppBaseDir,
                            CreateNoWindow = true,
                            UseShellExecute = false,
                            WindowStyle = ProcessWindowStyle.Hidden
                        });
                        Thread.Sleep(250);
                        if (proc != null && !proc.HasExited && IsLocalPortOpen(port, 250))
                        {
                            _proxyProcesses[port] = proc;
                            return true;
                        }
                        if (proc != null && !proc.HasExited)
                        {
                            try { proc.Kill(); } catch { }
                        }
                    }
                    catch { }
                }

                // Built-in C# In-Process Network Spoofing & SOCKS5/HTTP Proxy Engine (100% guaranteed fallback even without Go)
                try
                {
                    string disguisePath = GetScriptPath("mobile-disguise.js");
                    InProcessProxyServer inProc = new InProcessProxyServer(port, normalizedUpstream, serialNo, androidId, deviceProfile, disguisePath);
                    if (inProc.Start())
                    {
                        _inProcProxies[port] = inProc;
                        Log(string.Format("[🛡️ Net-Spoof] Active C# Network Spoofing Proxy listening on 127.0.0.1:{0} ({1}).", port, deviceProfile != null ? deviceProfile.Model : "Mobile"));
                        return true;
                    }
                }
                catch (Exception ex)
                {
                    Log(string.Format("[Proxy Warning] Could not bind local proxy port {0}: {1}", port, ex.Message));
                }

                return IsLocalPortOpen(port, 200);
            }
        }

        public void StopProxy(int port)
        {
            lock (_proxyLock)
            {
                if (_proxyProcesses.ContainsKey(port))
                {
                    try { if (!_proxyProcesses[port].HasExited) _proxyProcesses[port].Kill(); } catch { }
                    _proxyProcesses.Remove(port);
                }
                if (_inProcProxies.ContainsKey(port))
                {
                    try { _inProcProxies[port].Stop(); } catch { }
                    _inProcProxies.Remove(port);
                }
            }
        }

        public void StopAllProxies()
        {
            lock (_proxyLock)
            {
                foreach (var kvp in _proxyProcesses)
                {
                    try { if (!kvp.Value.HasExited) kvp.Value.Kill(); } catch { }
                }
                _proxyProcesses.Clear();

                foreach (var kvp in _inProcProxies)
                {
                    try { kvp.Value.Stop(); } catch { }
                }
                _inProcProxies.Clear();

                try
                {
                    foreach (var p in Process.GetProcessesByName("dofus-net-proxy"))
                    {
                        using (p)
                        {
                            try { if (!p.HasExited) p.Kill(); } catch { }
                        }
                    }
                }
                catch { }
            }
        }

        private static void ReadConfigRamCores(string cfgFile, ref int ramMb, ref int cores)
        {
            if (string.IsNullOrEmpty(cfgFile) || !File.Exists(cfgFile)) return;
            try
            {
                using (FileStream fs = new FileStream(cfgFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (StreamReader sr = new StreamReader(fs, Encoding.UTF8))
                {
                    string l;
                    while ((l = sr.ReadLine()) != null)
                    {
                        if (l.StartsWith("hw.ramSize", StringComparison.OrdinalIgnoreCase))
                        {
                            string[] p = l.Split('=');
                            if (p.Length == 2) int.TryParse(p[1].Trim(), out ramMb);
                        }
                        else if (l.StartsWith("hw.cpu.ncore", StringComparison.OrdinalIgnoreCase))
                        {
                            string[] p = l.Split('=');
                            if (p.Length == 2) int.TryParse(p[1].Trim(), out cores);
                        }
                    }
                }
            }
            catch { }
        }

        public List<InstanceModel> GetInstances()
        {
            List<InstanceModel> list = new List<InstanceModel>();
            HashSet<string> live = GetLiveSerials();

            if (!Directory.Exists(AvdHome)) return list;

            Dictionary<int, int> portToPid = new Dictionary<int, int>();
            if (live.Count > 0)
            {
                bool needWmiLookup = false;
                lock (_pidCacheLock)
                {
                    foreach (string s in live)
                    {
                        int pNum;
                        if (s.StartsWith("emulator-") && int.TryParse(s.Substring(9), out pNum))
                        {
                            int cachedPid;
                            if (_cachedPortToPid.TryGetValue(pNum, out cachedPid) && cachedPid > 0)
                            {
                                try
                                {
                                    using (Process p = Process.GetProcessById(cachedPid))
                                    {
                                        if (!p.HasExited)
                                        {
                                            portToPid[pNum] = cachedPid;
                                            continue;
                                        }
                                    }
                                }
                                catch { }
                            }
                            needWmiLookup = true;
                        }
                    }
                }

                if (needWmiLookup)
                {
                    try
                    {
                        using (System.Management.ManagementObjectSearcher searcher = new System.Management.ManagementObjectSearcher(
                            "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'"))
                        {
                            searcher.Options.Timeout = TimeSpan.FromSeconds(2);
                            using (System.Management.ManagementObjectCollection collection = searcher.Get())
                            {
                                foreach (System.Management.ManagementObject mo in collection)
                                {
                                    using (mo)
                                    {
                                        string cl = (mo["CommandLine"] ?? "").ToString();
                                        Match mp = RxQemuPort.Match(cl);
                                        if (mp.Success)
                                        {
                                            int pNum = int.Parse(mp.Groups[1].Value);
                                            int qPid = Convert.ToInt32(mo["ProcessId"]);
                                            portToPid[pNum] = qPid;
                                            lock (_pidCacheLock) { _cachedPortToPid[pNum] = qPid; }
                                            PriorityGovernor.RegisterInstancePid(qPid, Math.Max(0, (pNum - 5554) / 2));
                                        }
                                    }
                                }
                            }
                        }
                    }
                    catch { }
                }
            }
            else
            {
                lock (_pidCacheLock) { _cachedPortToPid.Clear(); }
            }

            string[] dirs = Directory.GetDirectories(AvdHome, "dofus-*.avd");
            Array.Sort(dirs);

            foreach (string d in dirs)
            {
                string name = Path.GetFileNameWithoutExtension(d);
                if (name.Equals("dofus-template", StringComparison.OrdinalIgnoreCase)) continue;

                int idx = 1;
                System.Text.RegularExpressions.Match m = RxTrailingDigits.Match(name);
                if (m.Success) int.TryParse(m.Groups[1].Value, out idx);

                int port = 5554 + 2 * (idx - 1);
                string serial = "emulator-" + port;

                int ram = 1024;
                int cores = 1;
                ReadConfigRamCores(Path.Combine(d, "config.ini"), ref ram, ref cores);

                bool isRunning = live.Contains(serial);
                int pid = (isRunning && portToPid.ContainsKey(port)) ? portToPid[port] : 0;

                string proxyDisplay = "[⚡] Accelerator (127.0.0.1:8880)";
                string identFile = Path.Combine(d, "identity.json");
                string phoneModelId = "";
                string phoneModelDisplay = "";
                string customName = "Instance #" + idx;

                if (File.Exists(identFile))
                {
                    try
                    {
                        string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                        System.Text.RegularExpressions.Match mCN = RxCustomName.Match(idJson);
                        if (mCN.Success && !string.IsNullOrEmpty(mCN.Groups[1].Value))
                        {
                            customName = mCN.Groups[1].Value.Trim();
                        }
                        System.Text.RegularExpressions.Match mP = RxProxy.Match(idJson);
                        if (mP.Success && !string.IsNullOrEmpty(mP.Groups[1].Value))
                        {
                            string pVal = mP.Groups[1].Value.Trim();
                            proxyDisplay = pVal.StartsWith("[") ? pVal : ("[🌐] " + pVal);
                        }
                        System.Text.RegularExpressions.Match mPM = RxPhoneModel.Match(idJson);
                        if (mPM.Success && !string.IsNullOrEmpty(mPM.Groups[1].Value))
                        {
                            phoneModelId = mPM.Groups[1].Value.Trim();
                        }
                    }
                    catch { }
                }

                if (string.IsNullOrEmpty(phoneModelId))
                {
                    DeviceProfile defP = DeviceCatalog.GetDefaultProfileForIndex(idx);
                    phoneModelId = defP.Id;
                    phoneModelDisplay = defP.DisplayName;
                }
                else
                {
                    DeviceProfile p = DeviceCatalog.GetProfile(phoneModelId);
                    phoneModelDisplay = p.DisplayName;
                }

                list.Add(new InstanceModel
                {
                    Name = name,
                    CustomName = customName,
                    DisplayName = customName,
                    Port = port,
                    RamMb = ram,
                    Cores = cores,
                    IsRunning = isRunning,
                    Pid = pid,
                    Proxy = proxyDisplay,
                    PhoneModelId = phoneModelId,
                    PhoneModelName = phoneModelDisplay,
                    IsWarmReady = IsInstanceWarmReady(name)
                });
            }

            return list;
        }

        private static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n").Replace("\t", "\\t");
        }

        public string GetProxy(string name)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            string identFile = Path.Combine(avdDir, "identity.json");
            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    System.Text.RegularExpressions.Match mP = RxProxy.Match(idJson);
                    if (mP.Success && !string.IsNullOrEmpty(mP.Groups[1].Value))
                    {
                        return mP.Groups[1].Value.Trim();
                    }
                }
                catch { }
            }
            return null;
        }

        public string GetPhoneModelId(string name)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            string identFile = Path.Combine(avdDir, "identity.json");
            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    var mPM = RxPhoneModel.Match(idJson);
                    if (mPM.Success && !string.IsNullOrEmpty(mPM.Groups[1].Value))
                    {
                        return mPM.Groups[1].Value.Trim();
                    }
                }
                catch { }
            }
            int instIdx = 1;
            var m = RxTrailingDigits.Match(name);
            if (m.Success) int.TryParse(m.Groups[1].Value, out instIdx);
            return DeviceCatalog.GetDefaultProfileForIndex(instIdx).Id;
        }

        public DeviceProfile GetInstancePhoneModel(string name)
        {
            string id = GetPhoneModelId(name);
            return DeviceCatalog.GetProfile(id);
        }

        public static string GenerateSystemProp(DeviceProfile profile, string serialNo, int cores)
        {
            if (profile == null) profile = DeviceCatalog.GetProfile("galaxy-a51");
            string releaseVer = "10";
            if (!string.IsNullOrEmpty(profile.BuildFingerprint))
            {
                var mVer = System.Text.RegularExpressions.Regex.Match(profile.BuildFingerprint, @":(\d+)/");
                if (mVer.Success) releaseVer = mVer.Groups[1].Value;
            }
            return string.Format(
                "ro.product.brand={0}\r\n" +
                "ro.product.manufacturer={1}\r\n" +
                "ro.product.model={2}\r\n" +
                "ro.product.name={3}\r\n" +
                "ro.product.device={4}\r\n" +
                "ro.product.board={5}\r\n" +
                "ro.build.product={4}\r\n" +
                "ro.product.system.brand={0}\r\n" +
                "ro.product.system.manufacturer={1}\r\n" +
                "ro.product.system.model={2}\r\n" +
                "ro.product.system.name={3}\r\n" +
                "ro.product.system.device={4}\r\n" +
                "ro.product.vendor.brand={0}\r\n" +
                "ro.product.vendor.manufacturer={1}\r\n" +
                "ro.product.vendor.model={2}\r\n" +
                "ro.product.vendor.name={3}\r\n" +
                "ro.product.vendor.device={4}\r\n" +
                "ro.product.odm.brand={0}\r\n" +
                "ro.product.odm.manufacturer={1}\r\n" +
                "ro.product.odm.model={2}\r\n" +
                "ro.product.odm.name={3}\r\n" +
                "ro.product.odm.device={4}\r\n" +
                "ro.product.bootimage.brand={0}\r\n" +
                "ro.product.bootimage.manufacturer={1}\r\n" +
                "ro.product.bootimage.model={2}\r\n" +
                "ro.product.bootimage.name={3}\r\n" +
                "ro.product.bootimage.device={4}\r\n" +
                "ro.hardware.chipname={6}\r\n" +
                "ro.chipname={6}\r\n" +
                "ro.arch=arm64-v8a\r\n" +
                "ro.board.platform={7}\r\n" +
                "ro.soc.manufacturer={8}\r\n" +
                "ro.soc.model={9}\r\n" +
                "ro.enable.native.bridge.exec=1\r\n" +
                "ro.enable.native.bridge.exec64=1\r\n" +
                "ro.dalvik.vm.isa.arm=x86\r\n" +
                "ro.dalvik.vm.isa.arm64=x86_64\r\n" +
                "ro.build.flavor={3}-user\r\n" +
                "ro.build.type=user\r\n" +
                "ro.build.tags=release-keys\r\n" +
                "ro.build.fingerprint={10}\r\n" +
                "ro.system.build.fingerprint={10}\r\n" +
                "ro.vendor.build.fingerprint={10}\r\n" +
                "ro.bootimage.build.fingerprint={10}\r\n" +
                "ro.build.display.id={11}\r\n" +
                "ro.build.description={3}-user {14} {11} release-keys\r\n" +
                "ro.system.build.type=user\r\n" +
                "ro.system.build.tags=release-keys\r\n" +
                "ro.hardware.virtual=0\r\n" +
                "ro.emulator=0\r\n" +
                "qemu.hw.mainkeys=1\r\n" +
                "ro.serialno={12}\r\n" +
                "ro.boot.serialno={12}\r\n" +
                "gsm.sim.state=READY\r\n" +
                "gsm.sim.operator.numeric=20801\r\n" +
                "gsm.sim.operator.alpha=Orange\r\n" +
                "gsm.network.type=LTE\r\n" +
                "config.disable_animations=1\r\n" +
                "debug.sf.nobootanimation=1\r\n" +
                "pm.dexopt.boot=verify\r\n" +
                "pm.dexopt.first-boot=verify\r\n" +
                "pm.dexopt.install=quicken\r\n" +
                "dalvik.vm.dex2oat-filter=verify\r\n" +
                "dalvik.vm.dex2oat-threads={13}\r\n" +
                "dalvik.vm.image-dex2oat-threads={13}\r\n" +
                "dalvik.vm.boot-dex2oat-threads={13}\r\n" +
                "persist.sys.strictmode.disable=true\r\n" +
                "persist.sys.strictmode.visual=false\r\n" +
                "dalvik.vm.checkjni=false\r\n" +
                "debug.sf.latch_unsignaled=1\r\n" +
                "debug.sf.disable_backpressure=1\r\n" +
                "sys.use_fifo_ui=1\r\n" +
                "ro.config.hw_quickpoweron=true\r\n" +
                "ro.config.nocheckin=1\r\n" +
                "persist.sys.disable_rescue=true\r\n" +
                "sys.wifitracing.started=0\r\n" +
                "persist.traced.enable=0\r\n" +
                "persist.sys.timezone=Europe/Paris\r\n" +
                "persist.sys.country=FR\r\n" +
                "persist.sys.language=fr\r\n" +
                "persist.sys.locale=fr-FR\r\n",
                profile.Brand, profile.Manufacturer, profile.Model,
                profile.ProductName, profile.ProductDevice, profile.Board,
                profile.Hardware, profile.Platform, profile.SocManufacturer,
                profile.SocModel, profile.BuildFingerprint, profile.BuildId,
                serialNo, cores, releaseVer);
        }

        public void UpdateInstanceConfig(string name, string customName, int ramMb, int cores, string phoneModelId, string proxy)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            if (!Directory.Exists(avdDir))
            {
                ProvisionInstance(name, ramMb, cores, phoneModelId, customName, proxy);
                return;
            }

            int instIdx = 1;
            System.Text.RegularExpressions.Match m = RxTrailingDigits.Match(name);
            if (m.Success) int.TryParse(m.Groups[1].Value, out instIdx);

            // Check existing config to see if hardware dimensions changed
            int oldRam = ramMb;
            int oldCores = cores;
            string oldPhoneModel = "";
            string cfgFile = Path.Combine(avdDir, "config.ini");
            ReadConfigRamCores(cfgFile, ref oldRam, ref oldCores);

            DeviceProfile activeProfile = !string.IsNullOrEmpty(phoneModelId)
                ? DeviceCatalog.GetProfile(phoneModelId)
                : DeviceCatalog.GetDefaultProfileForIndex(instIdx);

            string serialNo = (activeProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string mac = GenerateRealisticMac(activeProfile, instIdx, serialNo);
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLower();
            string existingCustomName = "Instance #" + instIdx;
            string existingProxy = "";

            string identFile = Path.Combine(avdDir, "identity.json");
            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    var mCN = RxCustomName.Match(idJson);
                    if (mCN.Success && !string.IsNullOrEmpty(mCN.Groups[1].Value)) existingCustomName = mCN.Groups[1].Value.Trim();
                    var mS = RxSerial.Match(idJson);
                    if (mS.Success && !string.IsNullOrEmpty(mS.Groups[1].Value)) serialNo = mS.Groups[1].Value.Trim().ToUpper();
                    var mA = RxAndroidId.Match(idJson);
                    if (mA.Success && !string.IsNullOrEmpty(mA.Groups[1].Value)) androidId = mA.Groups[1].Value.Trim().ToLower();
                    var mM = RxMac.Match(idJson);
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value) && !mM.Groups[1].Value.Trim().StartsWith("52:54:00")) mac = mM.Groups[1].Value.Trim();
                    var mP = RxProxy.Match(idJson);
                    if (mP.Success && !string.IsNullOrEmpty(mP.Groups[1].Value)) existingProxy = mP.Groups[1].Value.Trim();
                    var mPM = RxPhoneModel.Match(idJson);
                    if (mPM.Success && !string.IsNullOrEmpty(mPM.Groups[1].Value)) oldPhoneModel = mPM.Groups[1].Value.Trim();
                }
                catch { }
            }

            string finalCustomName = !string.IsNullOrEmpty(customName) ? customName.Trim() : existingCustomName;
            string finalProxy = proxy != null ? proxy.Trim() : existingProxy;
            if (finalProxy.StartsWith("[🌐] ")) finalProxy = finalProxy.Substring(5).Trim();
            if (finalProxy.StartsWith("[⚡]") || finalProxy.Contains("127.0.0.1:8880") || finalProxy.Contains("Accelerator")) finalProxy = "";

            string identContent = string.Format(
                "{{\"CustomName\":\"{0}\",\"Serial\":\"{1}\",\"AndroidId\":\"{2}\",\"Mac\":\"{3}\",\"Proxy\":\"{4}\",\"PhoneModel\":\"{5}\"}}",
                EscapeJson(finalCustomName), EscapeJson(serialNo), EscapeJson(androidId), EscapeJson(mac), EscapeJson(finalProxy), EscapeJson(activeProfile.Id));
            File.WriteAllText(identFile, identContent, Encoding.UTF8);

            string propFile = Path.Combine(avdDir, "system.prop");
            File.WriteAllText(propFile, GenerateSystemProp(activeProfile, serialNo, cores), Encoding.ASCII);
            UpdateConfigIni(cfgFile, name, ramMb, cores, activeProfile);

            if (oldRam != ramMb || oldCores != cores || (!string.IsNullOrEmpty(oldPhoneModel) && !string.Equals(oldPhoneModel, activeProfile.Id, StringComparison.OrdinalIgnoreCase)))
            {
                InvalidateWarmSnapshot(name);
            }

            Log(string.Format("Updated '{0}' ({1}): {2} MB RAM, {3} Core(s), {4}.", finalCustomName, name, ramMb, cores, activeProfile.DisplayName));
        }

        public int ApplyHardwareToAllInstances(int ramMb, int cores)
        {
            List<InstanceModel> instances = GetInstances();
            int updated = 0;
            foreach (InstanceModel inst in instances)
            {
                if (inst.IsRunning) continue;
                UpdateInstanceConfig(inst.Name, inst.CustomName, ramMb, cores, inst.PhoneModelId, inst.Proxy);
                updated++;
            }
            return updated;
        }

        public void ProvisionInstance(string name, int ramMb, int cores, string phoneModelId = null, string customName = null, string proxyOverride = null)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            string iniFile = Path.Combine(AvdHome, name + ".ini");

            if (!Directory.Exists(avdDir))
                Directory.CreateDirectory(avdDir);

            // Write AVD pointer .ini
            string iniContent = string.Format("avd.ini.encoding=UTF-8\r\npath={0}\r\npath.rel=avd\\{1}.avd\r\ntarget=android-29\r\n", avdDir, name);
            File.WriteAllText(iniFile, iniContent, Encoding.UTF8);

            // Copy base template config.ini if template exists
            string templateDir = Path.Combine(AvdHome, "dofus-template.avd");
            string cfgDest = Path.Combine(avdDir, "config.ini");
            if (File.Exists(Path.Combine(templateDir, "config.ini")) && !File.Exists(cfgDest))
            {
                File.Copy(Path.Combine(templateDir, "config.ini"), cfgDest, true);
            }

            int instIdx = 1;
            System.Text.RegularExpressions.Match m = RxTrailingDigits.Match(name);
            if (m.Success) int.TryParse(m.Groups[1].Value, out instIdx);

            DeviceProfile activeProfile = !string.IsNullOrEmpty(phoneModelId)
                ? DeviceCatalog.GetProfile(phoneModelId)
                : DeviceCatalog.GetDefaultProfileForIndex(instIdx);

            string resolvedCustomName = !string.IsNullOrEmpty(customName) ? customName.Trim() : ("Instance #" + instIdx);
            string serialNo = (activeProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string mac = GenerateRealisticMac(activeProfile, instIdx, serialNo);
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLower();
            string proxy = proxyOverride ?? "";

            // Write identity.json if absent, or load existing identity
            string identFile = Path.Combine(avdDir, "identity.json");
            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    if (string.IsNullOrEmpty(customName))
                    {
                        var mCN = RxCustomName.Match(idJson);
                        if (mCN.Success && !string.IsNullOrEmpty(mCN.Groups[1].Value))
                            resolvedCustomName = mCN.Groups[1].Value.Trim();
                    }
                    var mS = RxSerial.Match(idJson);
                    if (mS.Success && !string.IsNullOrEmpty(mS.Groups[1].Value))
                    {
                        serialNo = mS.Groups[1].Value.Trim().ToUpper();
                    }
                    var mA = RxAndroidId.Match(idJson);
                    if (mA.Success && !string.IsNullOrEmpty(mA.Groups[1].Value))
                    {
                        androidId = mA.Groups[1].Value.Trim().ToLower();
                    }
                    var mM = RxMac.Match(idJson);
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value) && !mM.Groups[1].Value.Trim().StartsWith("52:54:00"))
                    {
                        mac = mM.Groups[1].Value.Trim();
                    }
                    if (proxyOverride == null)
                    {
                        var mP = RxProxy.Match(idJson);
                        if (mP.Success && !string.IsNullOrEmpty(mP.Groups[1].Value))
                        {
                            proxy = mP.Groups[1].Value.Trim();
                        }
                    }
                    if (string.IsNullOrEmpty(phoneModelId))
                    {
                        var mPM = RxPhoneModel.Match(idJson);
                        if (mPM.Success && !string.IsNullOrEmpty(mPM.Groups[1].Value))
                        {
                            activeProfile = DeviceCatalog.GetProfile(mPM.Groups[1].Value.Trim());
                        }
                    }
                }
                catch { }
            }

            if (proxy.StartsWith("[🌐] ")) proxy = proxy.Substring(5).Trim();
            if (proxy.StartsWith("[⚡]") || proxy.Contains("127.0.0.1:8880")) proxy = "";

            string identContent = string.Format("{{\"CustomName\":\"{0}\",\"Serial\":\"{1}\",\"AndroidId\":\"{2}\",\"Mac\":\"{3}\",\"Proxy\":\"{4}\",\"PhoneModel\":\"{5}\"}}",
                EscapeJson(resolvedCustomName), EscapeJson(serialNo), EscapeJson(androidId), EscapeJson(mac), EscapeJson(proxy), EscapeJson(activeProfile.Id));
            File.WriteAllText(identFile, identContent, Encoding.UTF8);

            // Write Samsung identity system.prop
            string propFile = Path.Combine(avdDir, "system.prop");
            string sysProp = GenerateSystemProp(activeProfile, serialNo, cores);
            File.WriteAllText(propFile, sysProp, Encoding.ASCII);

            // Ensure QCOW2 differential overlay
            EnsureOverlay(avdDir);

            // Append or update config.ini
            UpdateConfigIni(cfgDest, name, ramMb, cores, activeProfile);
            Log(string.Format("Provisioned {0} with {1} MB RAM, {2} Core(s), spoofing {3} ({4}).", name, ramMb, cores, activeProfile.DisplayName, activeProfile.Model));
        }

        private void UpdateConfigIni(string cfgFile, string name, int ramMb, int cores, DeviceProfile profile = null)
        {
            if (profile == null) profile = DeviceCatalog.GetProfile("galaxy-tab-s6");
            Dictionary<string, string> settings = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "avd.name", name },
                { "avd.id", name },
                { "hw.device.manufacturer", profile.Manufacturer },
                { "hw.device.name", profile.DisplayName },
                { "hw.gpu.enabled", "yes" },
                { "hw.gpu.mode", "host" },
                { "fastboot.forceFastBoot", "yes" },
                { "fastboot.forceColdBoot", "no" },
                { "firstboot.saveToLocalSnapshot", "yes" },
                { "hw.ramSize", ramMb.ToString() },
                { "hw.cpu.ncore", cores.ToString() },
                { "hw.lcd.width", profile.LcdWidth.ToString() },
                { "hw.lcd.height", profile.LcdHeight.ToString() },
                { "hw.lcd.density", profile.LcdDensity.ToString() },
                { "hw.initialOrientation", "landscape" },
                { "hw.audioInput", "no" },
                { "hw.audioOutput", "no" },
                { "hw.camera.back", "none" },
                { "hw.camera.front", "none" },
                { "hw.keyboard", "yes" },
                { "hw.mainKeys", "yes" },
                { "qemu.hw.mainkeys", "1" },
                { "qemu.vsync", "30" },
                { "showDeviceFrame", "no" },
                { "skin.name", profile.LcdWidth + "x" + profile.LcdHeight },
                { "skin.path", "_no_skin" },
                { "skin.dynamic", "yes" },
                { "disk.dataPartition.path", "userdata-qemu.img" },
                { "image.sysdir.1", @"system-images\android-29\default\x86_64\" },
                { "abi.type", "x86_64" },
                { "hw.cpu.arch", "x86_64" }
            };

            Dictionary<string, string> existing = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (File.Exists(cfgFile))
            {
                foreach (string l in File.ReadAllLines(cfgFile))
                {
                    string[] p = l.Split(new[] { '=' }, 2);
                    if (p.Length == 2) existing[p[0].Trim()] = p[1].Trim();
                }
            }

            foreach (var kvp in settings)
                existing[kvp.Key] = kvp.Value;

            StringBuilder sb = new StringBuilder();
            foreach (var kvp in existing)
                sb.AppendLine(string.Format("{0} = {1}", kvp.Key, kvp.Value));

            File.WriteAllText(cfgFile, sb.ToString(), Encoding.UTF8);
        }

        public static void ForceDeleteDirectory(string dirPath)
        {
            if (string.IsNullOrEmpty(dirPath) || !Directory.Exists(dirPath)) return;
            try
            {
                DirectoryInfo di = new DirectoryInfo(dirPath);
                foreach (FileInfo fi in di.GetFiles("*", SearchOption.AllDirectories))
                {
                    try
                    {
                        fi.Attributes = FileAttributes.Normal;
                        fi.Delete();
                    }
                    catch { }
                }
                foreach (DirectoryInfo sub in di.GetDirectories("*", SearchOption.AllDirectories))
                {
                    try
                    {
                        sub.Attributes = FileAttributes.Normal;
                    }
                    catch { }
                }
                Directory.Delete(dirPath, true);
            }
            catch
            {
                try
                {
                    foreach (string f in Directory.GetFiles(dirPath))
                    {
                        try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); } catch { }
                    }
                    foreach (string d in Directory.GetDirectories(dirPath))
                    {
                        ForceDeleteDirectory(d);
                    }
                    Directory.Delete(dirPath, true);
                }
                catch { }
            }
        }

        public void EnsureOverlay(string avdDir)
        {
            if (!File.Exists(GoldenMasterPath)) return;
            try { File.SetAttributes(GoldenMasterPath, FileAttributes.Normal); } catch { }

            string emuQcow2 = Path.Combine(avdDir, "userdata-qemu.img.qcow2");
            string rawUd = Path.Combine(avdDir, "userdata-qemu.img");

            bool needRaw = !File.Exists(rawUd) || (new FileInfo(rawUd).Length != new FileInfo(GoldenMasterPath).Length);
            bool needQcow2 = needRaw || !File.Exists(emuQcow2) || (new FileInfo(emuQcow2).Length == 0);

            if (needRaw)
            {
                try
                {
                    if (File.Exists(rawUd))
                    {
                        try { File.SetAttributes(rawUd, FileAttributes.Normal); File.Delete(rawUd); } catch { }
                    }
                    try
                    {
                        bool linked = Win32.CreateHardLink(rawUd, GoldenMasterPath, IntPtr.Zero);
                        if (!linked) File.Copy(GoldenMasterPath, rawUd, true);
                    }
                    catch
                    {
                        try { File.Copy(GoldenMasterPath, rawUd, true); } catch { }
                    }
                    try { if (File.Exists(rawUd)) File.SetAttributes(rawUd, FileAttributes.Normal); } catch { }
                }
                catch { }
            }

            if (File.Exists(QemuImgPath) && File.Exists(rawUd))
            {
                string baseFmt = DetectDiskFormat(rawUd);
                if (needQcow2)
                {
                    try
                    {
                        if (File.Exists(emuQcow2))
                        {
                            try { File.SetAttributes(emuQcow2, FileAttributes.Normal); File.Delete(emuQcow2); } catch { }
                        }
                        RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"{0}\" -F {1} \"{2}\"", rawUd, baseFmt, emuQcow2), 30000, avdDir);
                        if (!File.Exists(emuQcow2) || (new FileInfo(emuQcow2).Length == 0))
                        {
                            string altFmt = (baseFmt == "raw") ? "qcow2" : "raw";
                            RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"{0}\" -F {1} \"{2}\"", rawUd, altFmt, emuQcow2), 30000, avdDir);
                        }
                    }
                    catch { }
                }
                else if (File.Exists(emuQcow2))
                {
                    // Ensure backing path in existing QCOW2 is absolute so emulator.exe running from sdk\emulator never fails to open backing file
                    try
                    {
                        RunCommand(QemuImgPath, string.Format("rebase -u -b \"{0}\" -F {1} \"{2}\"", rawUd, baseFmt, emuQcow2), 15000, avdDir);
                    }
                    catch { }
                }
            }
        }

        public void CleanLocks(string avdDir)
        {
            try
            {
                if (Directory.Exists(avdDir))
                {
                    foreach (string f in Directory.GetFiles(avdDir, "*.lock"))
                    {
                        try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); } catch { }
                    }
                    foreach (string d in Directory.GetDirectories(avdDir, "*.lock"))
                    {
                        ForceDeleteDirectory(d);
                    }
                    string multiLock = Path.Combine(avdDir, "multiinstance.lock");
                    if (File.Exists(multiLock))
                    {
                        try { File.SetAttributes(multiLock, FileAttributes.Normal); File.Delete(multiLock); } catch { }
                    }
                }
            }
            catch { }
        }

        private Process StartEmulatorProcess(string emuArgs, bool minimized)
        {
            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = EmulatorPath,
                Arguments = emuArgs,
                WorkingDirectory = Path.Combine(SdkRoot, "emulator"),
                CreateNoWindow = true,
                UseShellExecute = false,
                WindowStyle = minimized ? ProcessWindowStyle.Minimized : ProcessWindowStyle.Normal
            };
            psi.EnvironmentVariables["ANDROID_SDK_ROOT"] = SdkRoot;
            psi.EnvironmentVariables["ANDROID_HOME"] = SdkRoot;
            psi.EnvironmentVariables["ANDROID_AVD_HOME"] = AvdHome;
            psi.EnvironmentVariables["ANDROID_EMULATOR_HOME"] = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".android");
            psi.EnvironmentVariables["ANDROID_EMULATOR_METRICS_ENABLED"] = "0";
            return Process.Start(psi);
        }

        public void LaunchInstance(string name, int port, int ramMb, int cores, string proxy = null)
        {
            EnsureAdvancedFeatures();
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            CleanLocks(avdDir);
            EnsureOverlay(avdDir);

            int slot = Math.Max(0, (port - 5554) / 2);
            int localProxyPort = 8880 + slot;
            string upstreamProxy = null;
            bool useProxy = true;

            if (string.IsNullOrEmpty(proxy) || proxy.Contains("[⚡]") || proxy.Trim().Equals("127.0.0.1:8880", StringComparison.OrdinalIgnoreCase))
            {
                upstreamProxy = null;
            }
            else if (proxy.Trim().Equals("DIRECT", StringComparison.OrdinalIgnoreCase) || proxy.Trim().Equals("[DIRECT] DIRECT", StringComparison.OrdinalIgnoreCase))
            {
                useProxy = false;
            }
            else
            {
                upstreamProxy = NormalizeUpstreamProxy(proxy);
            }

            DeviceProfile instProfile = GetInstancePhoneModel(name);
            string identFile = Path.Combine(avdDir, "identity.json");
            string serialNo = (instProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLower();
            string mac = GenerateRealisticMac(instProfile, slot + 1, serialNo);

            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    Match mS = RxSerial.Match(idJson);
                    if (mS.Success && !string.IsNullOrEmpty(mS.Groups[1].Value))
                        serialNo = mS.Groups[1].Value.Trim().ToUpper();
                    Match mA = RxAndroidId.Match(idJson);
                    if (mA.Success && !string.IsNullOrEmpty(mA.Groups[1].Value))
                        androidId = mA.Groups[1].Value.Trim().ToLower();
                    Match mM = RxMac.Match(idJson);
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value) && !mM.Groups[1].Value.Trim().StartsWith("52:54:00"))
                        mac = mM.Groups[1].Value.Trim();
                    else
                        mac = GenerateRealisticMac(instProfile, slot + 1, serialNo);
                }
                catch { }
            }

            // Ensure config.ini has disk.dataPartition.path = userdata-qemu.img before booting
            string cfgFile = Path.Combine(avdDir, "config.ini");
            if (File.Exists(cfgFile))
            {
                UpdateConfigIni(cfgFile, name, ramMb, cores, instProfile);
            }

            bool proxyActive = false;
            if (useProxy)
            {
                proxyActive = EnsureProxyRunning(localProxyPort, upstreamProxy, serialNo, androidId, instProfile);
            }

            string proxyArg = (useProxy && proxyActive) ? string.Format("-http-proxy http://127.0.0.1:{0} ", localProxyPort) : "";

            // Clean, universally supported emulator.exe CLI flags (no invalid -wifi-mac-address or -no-location-ui)
            string emuArgs = string.Format(
                "-avd {0} -port {1} -gpu host -memory {2} -cores {3} " +
                "-no-boot-anim -no-audio -accel on -skip-adb-auth " +
                "-no-passive-gps {4}-prop qemu.hw.mainkeys=1",
                name, port, ramMb, cores, proxyArg);

            Log(string.Format("Starting {0} on port {1} ({2} MB RAM / {3} Core(s) / {4} / MAC {5})...", name, port, ramMb, cores, instProfile.DisplayName, mac));

            try { Adb("", "start-server", 6000); } catch { }

            Process proc = StartEmulatorProcess(emuArgs, false);
            if (proc != null)
            {
                lock (_pidCacheLock) { _cachedPortToPid[port] = proc.Id; }
                PriorityGovernor.RegisterInstancePid(proc.Id, slot, cores);
            }
            string serial = "emulator-" + port;

            try
            {
                int totalCores = Environment.ProcessorCount;
                if (totalCores >= 4 && proc != null && !proc.HasExited)
                {
                    long affinityMask = PriorityGovernor.ComputeAffinityMask(slot, cores, totalCores);
                    if (IntPtr.Size == 8)
                        proc.ProcessorAffinity = new IntPtr(affinityMask);
                    else
                        proc.ProcessorAffinity = new IntPtr(unchecked((int)affinityMask));
                    Log(string.Format("Pinned {0} (PID: {1}, {2} Core(s)) to Host CPU Affinity 0x{3:X}.", name, proc.Id, cores, affinityMask));
                }
            }
            catch { }

            ThreadPool.QueueUserWorkItem(delegate
            {
                ConfigureGuestAndLaunchGame(name, serial, port, ramMb, cores, avdDir, serialNo, androidId, mac, proxyArg, proc, false);
            });
        }

        private void ConfigureGuestAndLaunchGame(string name, string serial, int port, int ramMb, int cores, string avdDir, string serialNo, string androidId, string mac, string proxyArg, Process proc, bool shutdownAfterSnapshot)
        {
            Stopwatch sw = Stopwatch.StartNew();
            bool adbReady = false;
            bool retriedGpuFallback = false;

            while (sw.Elapsed.TotalSeconds < 90)
            {
                if (proc != null && proc.HasExited)
                {
                    if (!retriedGpuFallback)
                    {
                        retriedGpuFallback = true;
                        CleanLocks(avdDir);
                        string fallbackArgs = string.Format(
                            "-avd {0} -port {1} -gpu angle_indirect -memory {2} -cores {3} " +
                            "-no-boot-anim -no-audio -accel on -skip-adb-auth " +
                            "-no-passive-gps {4}-prop qemu.hw.mainkeys=1",
                            name, port, ramMb, cores, proxyArg ?? "");
                        Log(string.Format("Host GPU init exited early on {0}; automatically retrying with ANGLE Direct3D renderer...", name));
                        proc = StartEmulatorProcess(fallbackArgs, shutdownAfterSnapshot);
                        if (proc != null)
                        {
                            int slot = Math.Max(0, (port - 5554) / 2);
                            lock (_pidCacheLock) { _cachedPortToPid[port] = proc.Id; }
                            PriorityGovernor.RegisterInstancePid(proc.Id, slot, cores);
                        }
                        sw.Restart();
                        Thread.Sleep(500);
                        continue;
                    }
                    Log(string.Format("Error: Emulator process for {0} exited unexpectedly (code {1}).", name, proc.ExitCode));
                    return;
                }

                string state = Adb(serial, "get-state", 3500);
                if (state.Contains("device"))
                {
                    adbReady = true;
                    break;
                }
                Thread.Sleep(450);
            }

            if (!adbReady)
            {
                Log(string.Format("Warning: {0} did not reach ADB device state within 90s.", name));
                return;
            }

            string flagPath = Path.Combine(avdDir, "sanitize_session.flag");
            bool needsSanitize = File.Exists(flagPath);

            // 1. FAST-PATH: Check if instance restored directly from a pre-warmed QuickBoot RAM snapshot
            if (!shutdownAfterSnapshot && !needsSanitize)
            {
                string warmState = AdbShell(serial, "test -f /data/local/tmp/fake_cpuinfo && grep -q fake_cpuinfo /proc/mounts && getprop sys.boot_completed");
                if (!string.IsNullOrEmpty(warmState) && warmState.Trim().EndsWith("1"))
                {
                    AdbShell(serial, "am start -n com.ankama.dofustouch/.MainActivity");
                    ThreadPool.QueueUserWorkItem(delegate
                    {
                        InjectDisguiseViaCdp(serial, port, name);
                    });
                    try
                    {
                        File.WriteAllText(Path.Combine(avdDir, "cold_run_completed.flag"), DateTime.UtcNow.ToString("o"), Encoding.UTF8);
                    }
                    catch { }
                    Log(string.Format("⚡ Instant QuickBoot restored for {0} ({1}) in {2:F1}s! Dofus Touch active in foreground.", name, serial, sw.Elapsed.TotalSeconds));
                    return;
                }
            }

            // 2. COLD-BOOT WAIT: Let Android finish booting FIRST before running root/settings/mounts so system_server & zygote never stall!
            Stopwatch bootSw = Stopwatch.StartNew();
            while (bootSw.Elapsed.TotalSeconds < 120)
            {
                if (proc != null && proc.HasExited) return;
                string bootCheck = AdbShell(serial, "getprop sys.boot_completed; getprop sys.logbootcomplete; getprop init.svc.bootanim");
                if (!string.IsNullOrEmpty(bootCheck) && (bootCheck.Contains("1") || bootCheck.Contains("stopped")))
                {
                    break;
                }
                Thread.Sleep(1000);
            }

            // 3. Elevate to root now that zygote & system_server are fully initialized
            Adb(serial, "root", 8000);
            Thread.Sleep(300);

            // 4. Apply Kernel, VFS, sysfs & Network Interface / ARP / MAC / Carrier Coherence in a single fast pass
            ApplyNativeHardwareCoherence(serial, serialNo, name, mac);
            AdbShell(serial, string.Format("settings put secure android_id {0}; settings put global window_animation_scale 0.0; settings put global transition_animation_scale 0.0; settings put global animator_duration_scale 0.0", androidId));

            // 5. Activate LZ4 zRAM compressed swap if not yet active
            string zramPath = GetScriptPath("apply-zram.sh");
            if (!string.IsNullOrEmpty(zramPath) && File.Exists(zramPath))
            {
                string swapCheck = AdbShell(serial, "grep -q zram0 /proc/swaps && echo ACTIVE || echo NONE");
                if (!swapCheck.Contains("ACTIVE"))
                {
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/apply-zram.sh", zramPath));
                    AdbShell(serial, "chmod 755 /data/local/tmp/apply-zram.sh && sh /data/local/tmp/apply-zram.sh >/dev/null 2>&1 || true");
                }
            }

            if (needsSanitize)
            {
                AdbShell(serial, "rm -rf /data/data/com.ankama.dofustouch/app_webview/Cookies* /data/data/com.ankama.dofustouch/app_webview/'Local Storage' /data/data/com.ankama.dofustouch/app_webview/IndexedDB* /data/data/com.ankama.dofustouch/app_webview/databases* /data/data/com.ankama.dofustouch/databases* /data/data/com.ankama.dofustouch/app_webview/Default/Cookies* /data/data/com.ankama.dofustouch/shared_prefs/* /data/user/0/com.ankama.dofustouch/app_webview/Cookies* /data/user/0/com.ankama.dofustouch/app_webview/'Local Storage' /data/user/0/com.ankama.dofustouch/app_webview/IndexedDB* /data/user/0/com.ankama.dofustouch/app_webview/databases* /data/user/0/com.ankama.dofustouch/databases* /data/user/0/com.ankama.dofustouch/app_webview/Default/Cookies* /data/user/0/com.ankama.dofustouch/shared_prefs/* 2>/dev/null");
                try { File.Delete(flagPath); } catch { }
                Log(string.Format("Sanitized cloned session tokens, IndexedDB databases, and cookies for {0}.", name));
            }

            // 6. Deploy interpolated mobile-disguise.js
            string disguisePath = GetScriptPath("mobile-disguise.js");
            if (!string.IsNullOrEmpty(disguisePath) && File.Exists(disguisePath))
            {
                DeviceProfile instProfile = GetInstancePhoneModel(name);
                string rawJs = File.ReadAllText(disguisePath, Encoding.UTF8);
                string interpolatedJs = rawJs.Replace("__DEVICE_UUID__", androidId)
                                             .Replace("__DEVICE_SERIAL__", serialNo)
                                             .Replace("__DEVICE_MODEL__", instProfile.Model)
                                             .Replace("__DEVICE_MANUFACTURER__", instProfile.Manufacturer)
                                             .Replace("__DEVICE_UA__", instProfile.UserAgent)
                                             .Replace("__DEVICE_GL_VENDOR__", instProfile.GlVendor)
                                             .Replace("__DEVICE_GL_RENDERER__", instProfile.GlRenderer)
                                             .Replace("__DEVICE_MAX_TEXTURE_SIZE__", instProfile.MaxTextureSize.ToString());
                string tempDisguise = Path.Combine(Path.GetTempPath(), string.Format("mobile-disguise-{0}.js", Guid.NewGuid().ToString("N")));
                try
                {
                    File.WriteAllText(tempDisguise, interpolatedJs, Encoding.UTF8);
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/mobile-disguise.js", tempDisguise));
                }
                finally
                {
                    try { if (File.Exists(tempDisguise)) File.Delete(tempDisguise); } catch { }
                }
            }

            // 7. Single combined shell pass for WebView flags, JS placement, service suppression & one-time package debloat
            string heapLimit = (ramMb >= 8192) ? "1024m" : (ramMb >= 4096) ? "512m" : (ramMb >= 2048) ? "384m" : (ramMb >= 1024) ? "256m" : "192m";
            string combinedSetupCmd =
                "echo '_ --enable-devtools-experiments --remote-debugging-port=9222 --disable-background-timer-throttling' > /data/local/tmp/webview-command-line; " +
                "chmod 644 /data/local/tmp/webview-command-line /data/local/tmp/mobile-disguise.js 2>/dev/null || true; " +
                "mkdir -p /data/user/0/com.ankama.dofustouch/files/files/js /data/data/com.ankama.dofustouch/files/files/js /data/user/0/com.ankama.dofustouch/files/js /data/data/com.ankama.dofustouch/files/js; " +
                "for dst in /data/user/0/com.ankama.dofustouch/files/files/js /data/data/com.ankama.dofustouch/files/files/js /data/user/0/com.ankama.dofustouch/files/js /data/data/com.ankama.dofustouch/files/js; do " +
                "  cp -f /data/local/tmp/mobile-disguise.js $dst/mobile-disguise.js 2>/dev/null || true; " +
                "done; " +
                "APP_UID=$(stat -c '%u' /data/data/com.ankama.dofustouch 2>/dev/null || echo 10000); " +
                "chown -R $APP_UID:$APP_UID /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null; " +
                "chmod -R 755 /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null; " +
                "chcon -R u:object_r:app_data_file:s0 /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null || true; " +
                "setprop dalvik.vm.heapgrowthlimit " + heapLimit + "; " +
                "for svc in cameraserver vendor.camera-provider-2-4 drmserver vendor.drm-hal-1-0 vendor.drm-clearkey-hal-1-2 vendor.drm-widevine-hal-1-2 mediadrmserver traced traced_probes statsd incidentd tombstoned llkd-1 mdnsd goldfish-logcat vendor.fps_hal broadcastradio-hal vendor.cas-hal-1-1 vendor.gnss_service hostapd; do " +
                "  setprop ctl.stop $svc 2>/dev/null || true; " +
                "done; " +
                "if [ ! -f /data/local/tmp/.dofus_debloated ]; then " +
                "  for pkg in com.android.cts.priv.ctsshim com.android.cts.ctsshim org.chromium.webview_shell com.android.sdksetup com.android.provision com.android.statementservice com.android.printspooler com.android.bips com.android.camera2 com.android.music com.android.dreams.basic com.android.wallpaper.livepicker com.android.wallpaperbackup com.android.calllogbackup com.android.quicksearchbox; do " +
                "    pm disable-user --user 0 $pkg 2>/dev/null || true; " +
                "  done; " +
                "  touch /data/local/tmp/.dofus_debloated; " +
                "fi";
            AdbShell(serial, combinedSetupCmd);

            if (shutdownAfterSnapshot)
            {
                AdbShell(serial, "cmd package compile -m speed-profile com.ankama.dofustouch 2>/dev/null || true");
            }

            // Drop root daemon privileges prior to snapshot & gameplay
            Adb(serial, "unroot", 6000);
            Thread.Sleep(250);

            try
            {
                AdbShell(serial, "sync");
                Adb(serial, "emu avd snapshot save default_boot", 25000);
                File.WriteAllText(Path.Combine(avdDir, "cold_run_completed.flag"), DateTime.UtcNow.ToString("o"), Encoding.UTF8);
            }
            catch { }

            if (shutdownAfterSnapshot)
            {
                return;
            }

            AdbShell(serial, "am start -n com.ankama.dofustouch/.MainActivity");

            ThreadPool.QueueUserWorkItem(delegate
            {
                InjectDisguiseViaCdp(serial, port, name);
            });

            Log(string.Format("{0} ({1}) is ready! Dofus Touch running in foreground (Boot: {2:F1}s).", name, serial, sw.Elapsed.TotalSeconds));
        }

        public void WarmInstanceColdRunSync(string name, int port, int ramMb, int cores)
        {
            EnsureAdvancedFeatures();
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            CleanLocks(avdDir);
            EnsureOverlay(avdDir);

            int slot = Math.Max(0, (port - 5554) / 2);
            DeviceProfile instProfile = GetInstancePhoneModel(name);
            string identFile = Path.Combine(avdDir, "identity.json");
            string serialNo = (instProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLower();
            string mac = GenerateRealisticMac(instProfile, slot + 1, serialNo);

            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    var mS = RxSerial.Match(idJson);
                    if (mS.Success && !string.IsNullOrEmpty(mS.Groups[1].Value)) serialNo = mS.Groups[1].Value.Trim().ToUpper();
                    var mA = RxAndroidId.Match(idJson);
                    if (mA.Success && !string.IsNullOrEmpty(mA.Groups[1].Value)) androidId = mA.Groups[1].Value.Trim().ToLower();
                    var mM = RxMac.Match(idJson);
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value) && !mM.Groups[1].Value.Trim().StartsWith("52:54:00"))
                        mac = mM.Groups[1].Value.Trim();
                }
                catch { }
            }

            string emuArgs = string.Format(
                "-avd {0} -port {1} -gpu host -memory {2} -cores {3} " +
                "-no-boot-anim -no-audio -accel on -skip-adb-auth " +
                "-no-passive-gps -prop qemu.hw.mainkeys=1",
                name, port, ramMb, cores);

            Log(string.Format("🔥 Starting background Cold-Run warmup for {0} (port {1})...", name, port));

            try { Adb("", "start-server", 6000); } catch { }
            Process proc = StartEmulatorProcess(emuArgs, true);
            string serial = "emulator-" + port;

            try
            {
                ThreadPool.QueueUserWorkItem(delegate
                {
                    for (int i = 0; i < 40; i++)
                    {
                        if (proc == null || proc.HasExited) break;
                        Win32.HideAllProcessWindows(proc.Id);
                        Thread.Sleep(400);
                    }
                });

                ConfigureGuestAndLaunchGame(name, serial, port, ramMb, cores, avdDir, serialNo, androidId, mac, "", proc, true);
            }
            finally
            {
                try
                {
                    Adb(serial, "emu kill", 4000);
                    if (proc != null && !proc.HasExited)
                    {
                        if (!proc.WaitForExit(6000)) proc.Kill();
                    }
                }
                catch { }
                CleanLocks(avdDir);
                try
                {
                    File.WriteAllText(Path.Combine(avdDir, "emulator-user.ini"), "window.x = 80\r\nwindow.y = 80\r\nwindow.scale = -1\r\n", Encoding.ASCII);
                }
                catch { }
            }
        }

        public string GetScriptPath(string fileName)
        {
            string[] candidates = new string[]
            {
                Path.Combine(AppBaseDir, @"scripts\" + fileName),
                Path.Combine(AppBaseDir, fileName),
                Environment.GetEnvironmentVariable("DOFUS_FARM_HOME") != null ? Path.Combine(Environment.GetEnvironmentVariable("DOFUS_FARM_HOME"), @"scripts\" + fileName) : null,
                Environment.GetEnvironmentVariable("DOFUS_FARM_HOME") != null ? Path.Combine(Environment.GetEnvironmentVariable("DOFUS_FARM_HOME"), fileName) : null,
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @"DofusFarm\scripts\" + fileName)
            };
            foreach (string c in candidates)
            {
                if (!string.IsNullOrEmpty(c) && File.Exists(c)) return c;
            }
            return null;
        }

        private void ApplyNativeHardwareCoherence(string serial, string slotSerial, string name = null, string mac = null)
        {
            try
            {
                DeviceProfile activeProf = !string.IsNullOrEmpty(name) ? GetInstancePhoneModel(name) : DeviceCatalog.GetProfile("galaxy-a51");
                if (activeProf == null) activeProf = DeviceCatalog.GetProfile("galaxy-a51");
                if (string.IsNullOrEmpty(mac) || mac.StartsWith("52:54:00"))
                {
                    mac = GenerateRealisticMac(activeProf, 1, slotSerial);
                }
                string lowerMac = mac.ToLowerInvariant();

                // 1. Construct CPU topology & hardware string dynamically for profile SoC
                StringBuilder cpuinfo = new StringBuilder();
                string features = "fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp";
                int implementer = (activeProf.SocManufacturer == "Qualcomm") ? 0x51 : 0x41;
                for (int i = 0; i < 8; i++)
                {
                    cpuinfo.Append(string.Format("processor\t: {0}\n", i));
                    cpuinfo.Append("BogoMIPS\t: 52.00\n");
                    cpuinfo.Append(string.Format("Features\t: {0}\n", features));
                    cpuinfo.Append(string.Format("CPU implementer\t: 0x{0:x2}\n", implementer));
                    cpuinfo.Append("CPU architecture: 8\n");
                    if (i < 4)
                    {
                        cpuinfo.Append("CPU variant\t: 0x1\n");
                        cpuinfo.Append("CPU part\t: 0xd03\n");
                        cpuinfo.Append("CPU revision\t: 4\n");
                    }
                    else
                    {
                        cpuinfo.Append("CPU variant\t: 0x0\n");
                        cpuinfo.Append("CPU part\t: 0xd09\n");
                        cpuinfo.Append("CPU revision\t: 2\n");
                    }
                    cpuinfo.Append("\n");
                }
                string hwName = (activeProf.SocManufacturer == "Qualcomm")
                    ? string.Format("Qualcomm Technologies, Inc {0}", activeProf.Platform.ToUpper())
                    : (activeProf.SocManufacturer == "Google" ? "Google Tensor" : string.Format("SAMSUNG {0}", activeProf.Hardware));
                cpuinfo.Append(string.Format("Hardware\t: {0}\n", hwName));
                cpuinfo.Append("Revision\t: 0000\n");
                cpuinfo.Append(string.Format("Serial\t\t: {0}\n", slotSerial));

                // 2. Construct authentic Linux kernel /proc/version & /proc/cmdline
                string version = (activeProf.SocManufacturer == "Qualcomm")
                    ? string.Format("Linux version 4.19.157-perf (android-build@google.com) (Android clang version 10.0.7) #1 SMP PREEMPT {0}\n", DateTime.UtcNow.ToString("ddd MMM d HH:mm:ss UTC yyyy"))
                    : (activeProf.SocManufacturer == "Google"
                        ? string.Format("Linux version 5.10.43-android12-9-00001 (android-build@google.com) (Android clang version 12.0.5) #1 SMP PREEMPT {0}\n", DateTime.UtcNow.ToString("ddd MMM d HH:mm:ss UTC yyyy"))
                        : "Linux version 4.14.113-18116515 (dpi@SWDG4908) (Android (5484270 based on r353983c) clang version 9.0.3) #1 SMP PREEMPT Thu Jan 9 20:01:14 KST 2020\n");

                string bootDevice = (activeProf.SocManufacturer == "Qualcomm")
                    ? string.Format("soc/{0}.ufshc", activeProf.Hardware)
                    : (activeProf.SocManufacturer == "Google" ? "14700000.ufs" : "soc/13520000.ufs");
                string cmdline = string.Format("console=ram loglevel=4 androidboot.hardware={0} androidboot.serialno={1} androidboot.wifimacaddr={2} loop.max_part=7 androidboot.boot_devices={3} buildvariant=user androidboot.selinux=enforcing androidboot.verifiedbootstate=green\n", activeProf.Hardware, slotSerial, lowerMac, bootDevice);

                // 3. Per-instance French mobile carrier rotation & residential ARP table (hiding QEMU 10.0.2.2 SLIRP MAC 52:55:0a:00:02:02)
                string[] carrierCodes = new[] { "20801", "20810", "20820", "20815" };
                string[] carrierNames = new[] { "Orange F", "SFR", "Bouygues Telecom", "Free" };
                int cIdx = Math.Abs((slotSerial ?? "R58M").GetHashCode()) % carrierCodes.Length;
                string cCode = carrierCodes[cIdx];
                string cName = carrierNames[cIdx];
                string routerMac = string.Format("dc:00:b0:{0:x2}:{1:x2}:fe", (cIdx * 37 + 19) & 0xFF, (slotSerial.Length * 53) & 0xFF);
                string fakeArp = string.Format("IP address       HW type     Flags       HW address            Mask     Device\n192.168.1.254    0x1         0x2         {0}     *        wlan0\n", routerMac);

                string socId = !string.IsNullOrEmpty(activeProf.Board) ? activeProf.Board.ToUpper() : "EXYNOS9611";
                string socFamily = activeProf.SocManufacturer ?? "Samsung";
                string socMachine = string.Format("{0} {1}", activeProf.SocManufacturer, activeProf.SocModel);
                string socRawId = string.Format("0x{0}", activeProf.Hardware);

                // 4. Build a single self-contained shell script to apply all VFS/sysfs/network bind-mounts in 1 ADB round-trip
                StringBuilder sh = new StringBuilder();
                sh.Append("setenforce 0 2>/dev/null || true\n");
                sh.Append("cat << 'EOF_CPU' > /data/local/tmp/fake_cpuinfo\n").Append(cpuinfo.ToString()).Append("EOF_CPU\n");
                sh.Append("cat << 'EOF_VER' > /data/local/tmp/fake_version\n").Append(version).Append("EOF_VER\n");
                sh.Append("cat << 'EOF_CMD' > /data/local/tmp/fake_cmdline\n").Append(cmdline).Append("EOF_CMD\n");
                sh.Append("cat << 'EOF_ARP' > /data/local/tmp/fake_arp\n").Append(fakeArp).Append("EOF_ARP\n");
                sh.Append(string.Format("echo '{0}' > /data/local/tmp/fake_mac\n", lowerMac));
                sh.Append("chmod 444 /data/local/tmp/fake_cpuinfo /data/local/tmp/fake_version /data/local/tmp/fake_cmdline /data/local/tmp/fake_arp /data/local/tmp/fake_mac\n");
                sh.Append("chcon u:object_r:proc_cpuinfo:s0 /data/local/tmp/fake_cpuinfo 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_cpuinfo 2>/dev/null || true\n");
                sh.Append("chcon u:object_r:proc_version:s0 /data/local/tmp/fake_version 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_version 2>/dev/null || true\n");
                sh.Append("chcon u:object_r:proc_cmdline:s0 /data/local/tmp/fake_cmdline 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_cmdline 2>/dev/null || true\n");
                sh.Append("chcon u:object_r:proc_net:s0 /data/local/tmp/fake_arp 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_arp 2>/dev/null || true\n");
                sh.Append("chcon u:object_r:sysfs:s0 /data/local/tmp/fake_mac 2>/dev/null || true\n");
                sh.Append("mount -o bind /data/local/tmp/fake_cpuinfo /proc/cpuinfo 2>/dev/null || true\n");
                sh.Append("mount -o bind /data/local/tmp/fake_version /proc/version 2>/dev/null || true\n");
                sh.Append("mount -o bind /data/local/tmp/fake_cmdline /proc/cmdline 2>/dev/null || true\n");
                sh.Append("mount -o bind /data/local/tmp/fake_arp /proc/net/arp 2>/dev/null || true\n");
                sh.Append("if [ -f /sys/class/net/wlan0/address ]; then mount -o bind /data/local/tmp/fake_mac /sys/class/net/wlan0/address 2>/dev/null || true; fi\n");
                sh.Append("if [ -f /sys/class/net/eth0/address ]; then mount -o bind /data/local/tmp/fake_mac /sys/class/net/eth0/address 2>/dev/null || true; fi\n");
                sh.Append("mkdir -p /data/local/tmp/soc0\n");
                sh.Append(string.Format("echo -n '{0}' > /data/local/tmp/soc0/soc_id\n", socId));
                sh.Append(string.Format("echo -n '{0}' > /data/local/tmp/soc0/family\n", socFamily));
                sh.Append(string.Format("echo -n '{0}' > /data/local/tmp/soc0/machine\n", socMachine));
                sh.Append("echo -n '10' > /data/local/tmp/soc0/revision\n");
                sh.Append(string.Format("echo -n '{0}' > /data/local/tmp/soc0/raw_id\n", socRawId));
                sh.Append(string.Format("echo -n '{0}' > /data/local/tmp/soc0/type\n", socFamily));
                sh.Append("chmod 755 /data/local/tmp/soc0 && chmod 644 /data/local/tmp/soc0/*\n");
                sh.Append("chcon -R u:object_r:sysfs:s0 /data/local/tmp/soc0 2>/dev/null || true\n");
                sh.Append("mkdir -p /sys/devices/soc0 /data/local/tmp/empty_binfmt 2>/dev/null || true\n");
                sh.Append("echo -n '' > /data/local/tmp/fake_modules && chmod 444 /data/local/tmp/fake_modules\n");
                sh.Append(string.Format("if [ -f /system/build.prop ]; then sed -e 's/x86_64/arm64-v8a/g' -e 's/x86/armeabi-v7a/g' -e 's/ranchu/{0}/g' -e 's/goldfish/{0}/g' -e 's/sdk_gphone_arm64-v8a/{1}/g' /system/build.prop > /data/local/tmp/fake_build.prop; chmod 644 /data/local/tmp/fake_build.prop; chcon u:object_r:system_file:s0 /data/local/tmp/fake_build.prop 2>/dev/null || true; mount -o bind /data/local/tmp/fake_build.prop /system/build.prop 2>/dev/null || true; fi\n", activeProf.Hardware, activeProf.Model));
                sh.Append("mount -o bind /data/local/tmp/soc0 /sys/devices/soc0 2>/dev/null || true\n");
                sh.Append("mount -o bind /data/local/tmp/fake_modules /proc/modules 2>/dev/null || true\n");
                sh.Append("for p in $(pidof zygote zygote64 com.ankama.dofustouch 2>/dev/null); do\n");
                sh.Append("  nsenter -t $p -m mount -o bind /data/local/tmp/fake_cpuinfo /proc/cpuinfo 2>/dev/null || true\n");
                sh.Append("  nsenter -t $p -m mount -o bind /data/local/tmp/fake_version /proc/version 2>/dev/null || true\n");
                sh.Append("  nsenter -t $p -m mount -o bind /data/local/tmp/fake_cmdline /proc/cmdline 2>/dev/null || true\n");
                sh.Append("  nsenter -t $p -m mount -o bind /data/local/tmp/fake_arp /proc/net/arp 2>/dev/null || true\n");
                sh.Append("  nsenter -t $p -m mount -o bind /data/local/tmp/fake_modules /proc/modules 2>/dev/null || true\n");
                sh.Append("  nsenter -t $p -m mount -o bind /data/local/tmp/soc0 /sys/devices/soc0 2>/dev/null || true\n");
                sh.Append("  if [ -f /data/local/tmp/fake_build.prop ]; then nsenter -t $p -m mount -o bind /data/local/tmp/fake_build.prop /system/build.prop 2>/dev/null || true; fi\n");
                sh.Append("  if [ -d /proc/sys/fs/binfmt_misc ]; then nsenter -t $p -m mount -o bind /data/local/tmp/empty_binfmt /proc/sys/fs/binfmt_misc 2>/dev/null || true; fi\n");
                sh.Append("  if [ -f /sys/class/net/wlan0/address ]; then nsenter -t $p -m mount -o bind /data/local/tmp/fake_mac /sys/class/net/wlan0/address 2>/dev/null || true; fi\n");
                sh.Append("  if [ -f /sys/class/net/eth0/address ]; then nsenter -t $p -m mount -o bind /data/local/tmp/fake_mac /sys/class/net/eth0/address 2>/dev/null || true; fi\n");
                sh.Append("done\n");
                sh.Append("setprop debug.choreographer.skipwarning 30\n");
                sh.Append("setprop gsm.sim.state READY\n");
                sh.Append(string.Format("setprop gsm.sim.operator.numeric {0}\n", cCode));
                sh.Append(string.Format("setprop gsm.operator.numeric {0}\n", cCode));
                sh.Append(string.Format("setprop gsm.sim.operator.alpha \"{0}\"\n", cName));
                sh.Append(string.Format("setprop gsm.operator.alpha \"{0}\"\n", cName));
                sh.Append("setprop gsm.sim.operator.iso-country fr\n");
                sh.Append("setprop gsm.operator.iso-country fr\n");
                sh.Append("setprop gsm.network.type LTE\n");
                sh.Append("setprop wifi.interface wlan0\n");
                sh.Append("setenforce 1 2>/dev/null || true\n");

                string tmpSh = Path.Combine(Path.GetTempPath(), "dofus_coherence_" + Guid.NewGuid().ToString("N") + ".sh");
                try
                {
                    File.WriteAllText(tmpSh, sh.ToString().Replace("\r\n", "\n"), new UTF8Encoding(false));
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/apply-coherence.sh", tmpSh));
                    AdbShell(serial, "chmod 755 /data/local/tmp/apply-coherence.sh && sh /data/local/tmp/apply-coherence.sh >/dev/null 2>&1 && rm -f /data/local/tmp/apply-coherence.sh");
                }
                finally
                {
                    try { if (File.Exists(tmpSh)) File.Delete(tmpSh); } catch { }
                }

                Log(string.Format("[🛡️ Coherence] Applied {0} {1} VFS, sysfs, MAC ({2}) & {3} network disguise on {4}.", activeProf.SocManufacturer, activeProf.SocModel, mac, cName, serial));
            }
            catch (Exception ex)
            {
                Log(string.Format("[Warning] ApplyNativeHardwareCoherence failed on {0}: {1}", serial, ex.Message));
            }
        }

        public void InjectDisguiseViaCdp(string serial, int port, string name)
        {
            lock (_cdpLock)
            {
                if (_cdpSessions.ContainsKey(serial))
                {
                    try { _cdpSessions[serial].Cancel(); } catch { }
                    _cdpSessions.Remove(serial);
                }
                CancellationTokenSource cts = new CancellationTokenSource();
                _cdpSessions[serial] = cts;

                ThreadPool.QueueUserWorkItem(delegate
                {
                    MaintainCdpSession(serial, port, name, cts.Token);
                });
            }
        }

        private void MaintainCdpSession(string serial, int port, string name, CancellationToken token)
        {
            try
            {
                string disguisePath = GetScriptPath("mobile-disguise.js");
                string disguiseJs = (!string.IsNullOrEmpty(disguisePath) && File.Exists(disguisePath)) ? File.ReadAllText(disguisePath, Encoding.UTF8) : "";

                if (string.IsNullOrEmpty(disguiseJs))
                {
                    return;
                }

                // Synchronize DOM disguise identity with instance hardware identity (AC-01)
                DeviceProfile cdpProfile = GetInstancePhoneModel(name);
                string defaultPrefix = (cdpProfile != null && !string.IsNullOrEmpty(cdpProfile.SerialPrefix)) ? cdpProfile.SerialPrefix : "R58M";
                string avdDir = Path.Combine(AvdHome, name + ".avd");
                string identFile = Path.Combine(avdDir, "identity.json");
                string slotSerial = defaultPrefix + "45F41EFD";
                string slotUuid = "ca8ea76b0fe32f85";

                if (File.Exists(identFile))
                {
                    try
                    {
                        string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                        var mS = RxSerial.Match(idJson);
                        if (mS.Success && !string.IsNullOrEmpty(mS.Groups[1].Value))
                        {
                            slotSerial = mS.Groups[1].Value.Trim().ToUpper();
                        }
                        var mA = RxAndroidId.Match(idJson);
                        if (mA.Success && !string.IsNullOrEmpty(mA.Groups[1].Value))
                        {
                            slotUuid = mA.Groups[1].Value.Trim().ToLower();
                        }
                    }
                    catch { }
                }
                if (!string.IsNullOrEmpty(disguiseJs))
                {
                    disguiseJs = disguiseJs.Replace("__DEVICE_UUID__", slotUuid)
                                           .Replace("__DEVICE_SERIAL__", slotSerial)
                                           .Replace("__DEVICE_MODEL__", cdpProfile.Model)
                                           .Replace("__DEVICE_MANUFACTURER__", cdpProfile.Manufacturer)
                                           .Replace("__DEVICE_UA__", cdpProfile.UserAgent)
                                           .Replace("__DEVICE_GL_VENDOR__", cdpProfile.GlVendor)
                                           .Replace("__DEVICE_GL_RENDERER__", cdpProfile.GlRenderer)
                                           .Replace("__DEVICE_MAX_TEXTURE_SIZE__", cdpProfile.MaxTextureSize.ToString());
                }

                string escapedDisguiseJson = ToJsonString(disguiseJs);
                int cdpPort = port + 4000;

                while (!token.IsCancellationRequested)
                {
                    // Verify if process is still alive
                    string pidOut = AdbShell(serial, "pidof com.ankama.dofustouch").Trim();
                    if (string.IsNullOrEmpty(pidOut))
                    {
                        for (int w = 0; w < 6 && !token.IsCancellationRequested; w++) Thread.Sleep(500);
                        continue;
                    }

                    // Discover active DevTools unix domain socket or guest port
                    string unixList = AdbShell(serial, "cat /proc/net/unix");
                    string socketName = null;
                    Match match = RxUnixSocket.Match(unixList);
                    if (match.Success)
                    {
                        socketName = match.Groups[1].Value;
                    }
                    else
                    {
                        string[] pids = pidOut.Split(new[] { ' ', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                        if (pids.Length > 0) socketName = "webview_devtools_remote_" + pids[0].Trim();
                    }

                    if (!string.IsNullOrEmpty(socketName))
                    {
                        Adb(serial, string.Format("forward tcp:{0} localabstract:{1}", cdpPort, socketName));
                    }
                    else
                    {
                        Adb(serial, string.Format("forward tcp:{0} tcp:9222", cdpPort));
                    }

                    string jsonTargets = null;
                    try
                    {
                        HttpWebRequest req = (HttpWebRequest)WebRequest.Create(string.Format("http://127.0.0.1:{0}/json", cdpPort));
                        req.Timeout = 1500;
                        using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                        using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                        {
                            jsonTargets = sr.ReadToEnd();
                        }
                    }
                    catch
                    {
                        try
                        {
                            Adb(serial, string.Format("forward tcp:{0} tcp:9222", cdpPort));
                            HttpWebRequest req2 = (HttpWebRequest)WebRequest.Create(string.Format("http://127.0.0.1:{0}/json", cdpPort));
                            req2.Timeout = 1500;
                            using (HttpWebResponse resp2 = (HttpWebResponse)req2.GetResponse())
                            using (StreamReader sr2 = new StreamReader(resp2.GetResponseStream(), Encoding.UTF8))
                            {
                                jsonTargets = sr2.ReadToEnd();
                            }
                        }
                        catch { }
                    }

                    if (string.IsNullOrEmpty(jsonTargets) || !jsonTargets.Contains("webSocketDebuggerUrl"))
                    {
                        // DevTools debugging endpoint absent (release APK or WebView not ready); back off to prevent CPU churn
                        for (int w = 0; w < 10 && !token.IsCancellationRequested; w++) Thread.Sleep(500);
                        continue;
                    }

                    MatchCollection wsMatches = RxWsDebugger.Matches(jsonTargets);
                    if (wsMatches.Count == 0)
                    {
                        Thread.Sleep(1000);
                        continue;
                    }

                    // Connect to primary page target
                    string targetWsUrl = null;
                    foreach (System.Text.RegularExpressions.Match m in wsMatches)
                    {
                        targetWsUrl = m.Groups[1].Value;
                        targetWsUrl = RxWsHostPort.Replace(targetWsUrl, string.Format("ws://127.0.0.1:{0}/", cdpPort));
                        break;
                    }

                    if (string.IsNullOrEmpty(targetWsUrl))
                    {
                        Thread.Sleep(1000);
                        continue;
                    }

                    try
                    {
                        using (ClientWebSocket ws = new ClientWebSocket())
                        using (CancellationTokenSource connectCts = new CancellationTokenSource(TimeSpan.FromSeconds(8)))
                        {
                            ws.ConnectAsync(new Uri(targetWsUrl), connectCts.Token).Wait();
                            if (ws.State != WebSocketState.Open) continue;

                            SendWsJson(ws, "{\"id\":1,\"method\":\"Page.enable\"}");
                            SendWsJson(ws, "{\"id\":2,\"method\":\"Runtime.enable\"}");

                            int msgId = 10;
                            string addDoc = string.Format("{{\"id\":{0},\"method\":\"Page.addScriptToEvaluateOnNewDocument\",\"params\":{{\"source\":{1}}}}}", ++msgId, escapedDisguiseJson);
                            SendWsJson(ws, addDoc);

                            string eval = string.Format("{{\"id\":{0},\"method\":\"Runtime.evaluate\",\"params\":{{\"expression\":{1},\"userGesture\":true,\"awaitPromise\":false}}}}", ++msgId, escapedDisguiseJson);
                            SendWsJson(ws, eval);

                            Log(string.Format("[🛡️ CDP] Persistent session active for {0} ({1}). Disguise hooks permanently registered.", name, serial));

                            // Maintain connection loop - reassemble multi-chunk messages and avoid premature aborts (CORE-VULN-01)
                            byte[] buffer = new byte[8192];
                            Task<WebSocketReceiveResult> rcvTask = null;
                            using (MemoryStream ms = new MemoryStream())
                            {
                                while (ws.State == WebSocketState.Open && !token.IsCancellationRequested)
                                {
                                    if (rcvTask == null)
                                    {
                                        rcvTask = ws.ReceiveAsync(new ArraySegment<byte>(buffer), token);
                                    }

                                    if (rcvTask.Wait(4000, token))
                                    {
                                        var res = rcvTask.Result;
                                        rcvTask = null;
                                        if (res.MessageType == WebSocketMessageType.Close) break;

                                        ms.Write(buffer, 0, res.Count);
                                        if (res.EndOfMessage)
                                        {
                                            string frameStr = Encoding.UTF8.GetString(ms.ToArray());
                                            ms.SetLength(0);

                                            bool isTopLevelNav = frameStr.Contains("Page.loadEventFired") ||
                                                (frameStr.Contains("Page.frameNavigated") && !frameStr.Contains("\"parentId\""));
                                            if (isTopLevelNav)
                                            {
                                                // Re-assert live evaluation on top-level page transition
                                                if (!string.IsNullOrEmpty(disguiseJs))
                                                {
                                                    string reEval = string.Format("{{\"id\":{0},\"method\":\"Runtime.evaluate\",\"params\":{{\"expression\":{1},\"userGesture\":true,\"awaitPromise\":false}}}}", ++msgId, escapedDisguiseJson);
                                                    SendWsJson(ws, reEval);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    catch (OperationCanceledException)
                    {
                        break;
                    }
                    catch
                    {
                        // Target navigated or reloaded; retry connection seamlessly
                        Thread.Sleep(1500);
                    }
                }
            }
            catch (Exception ex)
            {
                Log(string.Format("[CDP Warning] Session worker error on {0}: {1}", name, ex.Message));
            }
        }

        private static readonly object _wsSendLock = new object();

        private static void SendWsJson(ClientWebSocket ws, string json)
        {
            if (ws == null || ws.State != WebSocketState.Open) return;
            try
            {
                lock (_wsSendLock)
                {
                    if (ws.State != WebSocketState.Open) return;
                    byte[] bytes = Encoding.UTF8.GetBytes(json);
                    ArraySegment<byte> segment = new ArraySegment<byte>(bytes);
                    ws.SendAsync(segment, WebSocketMessageType.Text, true, CancellationToken.None).Wait(5000);
                }
            }
            catch { }
        }

        private static string ToJsonString(string text)
        {
            if (text == null) return "\"\"";
            StringBuilder sb = new StringBuilder(text.Length + 100);
            sb.Append('"');
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
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
                        if (c < 32 || c > 127)
                        {
                            sb.AppendFormat("\\u{0:X4}", (int)c);
                        }
                        else
                        {
                            sb.Append(c);
                        }
                        break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        public void StopInstance(string serial, string name)
        {
            Log(string.Format("Stopping {0} ({1})...", name, serial));
            lock (_cdpLock)
            {
                if (_cdpSessions.ContainsKey(serial))
                {
                    try { _cdpSessions[serial].Cancel(); _cdpSessions[serial].Dispose(); } catch { }
                    _cdpSessions.Remove(serial);
                }
            }
            if (!string.IsNullOrEmpty(serial) && serial.StartsWith("emulator-"))
            {
                int pNum;
                if (int.TryParse(serial.Substring(9), out pNum))
                {
                    int sSlot = Math.Max(0, (pNum - 5554) / 2);
                    StopProxy(8880 + sSlot);
                    try { Adb(serial, string.Format("forward --remove tcp:{0}", pNum + 4000)); } catch { }
                }
            }
            try { AdbShell(serial, "sync"); } catch { }
            try { Adb(serial, "emu kill", 3000); } catch { }

            // Allow QEMU up to 5 seconds to flush QCOW2 and save QuickBoot snapshot cleanly before forced termination
            try
            {
                using (System.Management.ManagementObjectSearcher searcher = new System.Management.ManagementObjectSearcher(
                    "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe' OR Name='emulator.exe'"))
                {
                    searcher.Options.Timeout = TimeSpan.FromSeconds(2);
                    using (System.Management.ManagementObjectCollection collection = searcher.Get())
                    {
                        foreach (System.Management.ManagementObject mo in collection)
                        {
                            using (mo)
                            {
                                string cl = (mo["CommandLine"] ?? "").ToString();
                                if ((!string.IsNullOrEmpty(name) && cl.Contains("-avd " + name)) ||
                                    (!string.IsNullOrEmpty(serial) && serial.StartsWith("emulator-") && cl.Contains(serial.Substring(9))))
                                {
                                    int pid = Convert.ToInt32(mo["ProcessId"]);
                                    try
                                    {
                                        using (Process p = Process.GetProcessById(pid))
                                        {
                                            if (!p.WaitForExit(5000))
                                            {
                                                p.Kill();
                                            }
                                        }
                                    }
                                    catch { }
                                }
                            }
                        }
                    }
                }
            }
            catch { }

            string avdDir = Path.Combine(AvdHome, name + ".avd");
            CleanLocks(avdDir);
        }

        public void StopAll()
        {
            lock (_cdpLock)
            {
                foreach (var cts in _cdpSessions.Values)
                {
                    try { cts.Cancel(); cts.Dispose(); } catch { }
                }
                _cdpSessions.Clear();
            }
            StopAllProxies();
            try { Adb("", "forward --remove-all"); } catch { }
            HashSet<string> live = GetLiveSerials();
            Log(string.Format("Stopping all active instances ({0} running)...", live.Count));
            if (live.Count > 0)
            {
                using (CountdownEvent countdown = new CountdownEvent(live.Count))
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

            // Wait up to 4.5 seconds for QEMU processes to finish saving QuickBoot state cleanly before forced kill
            string[] procNames = new[] { "qemu-system-x86_64", "emulator", "dofus-net-proxy" };
            foreach (string pName in procNames)
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(pName))
                    {
                        using (p)
                        {
                            try
                            {
                                if (pName == "dofus-net-proxy" || !p.WaitForExit(4500))
                                {
                                    p.Kill();
                                    p.WaitForExit(1000);
                                }
                            }
                            catch { }
                        }
                    }
                }
                catch { }
            }

            // Clear all locks in AVD home
            try
            {
                if (Directory.Exists(AvdHome))
                {
                    foreach (string d in Directory.GetDirectories(AvdHome, "*.avd"))
                    {
                        CleanLocks(d);
                    }
                }
            }
            catch { }
            Log("All instances and emulator processes have been completely stopped.");
        }

        public string GetNextAvailableSlot()
        {
            HashSet<string> existing = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (Directory.Exists(AvdHome))
            {
                foreach (string d in Directory.GetDirectories(AvdHome, "dofus-*.avd"))
                {
                    existing.Add(Path.GetFileNameWithoutExtension(d));
                }
            }

            for (int i = 1; i <= 32; i++)
            {
                string cand = string.Format("dofus-{0:D2}", i);
                if (!existing.Contains(cand))
                    return cand;
            }
            return "dofus-" + (existing.Count + 1);
        }

        public bool CloneInstance(string sourceName, string targetName, bool cloneState = true, string customTargetName = null)
        {
            string srcDir = Path.Combine(AvdHome, sourceName + ".avd");
            string tgtDir = Path.Combine(AvdHome, targetName + ".avd");
            string tgtIni = Path.Combine(AvdHome, targetName + ".ini");

            if (!Directory.Exists(srcDir))
            {
                Log(string.Format("Clone failed: Source instance '{0}' does not exist.", sourceName));
                return false;
            }

            Log(string.Format("Cloning '{0}' -> '{1}' (Preserving 3GB game cache: {2})...", sourceName, targetName, cloneState));

            int targetIdx = 1;
            System.Text.RegularExpressions.Match m = RxTrailingDigits.Match(targetName);
            if (m.Success) int.TryParse(m.Groups[1].Value, out targetIdx);
            int targetPort = 5554 + 2 * (targetIdx - 1);
            string targetSerial = "emulator-" + targetPort;

            StopInstance(targetSerial, targetName);
            Thread.Sleep(500);

            // Safe live-clone guard: if source is running, flush disk caches and pause cleanly to prevent QCOW2 corruption
            HashSet<string> live = GetLiveSerials();
            bool isSourceNumbered = false;
            int srcIdx = 1;
            System.Text.RegularExpressions.Match mSrc = RxTrailingDigits.Match(sourceName);
            if (mSrc.Success)
            {
                isSourceNumbered = true;
                int.TryParse(mSrc.Groups[1].Value, out srcIdx);
            }
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
                if (!Directory.Exists(tgtDir))
                    Directory.CreateDirectory(tgtDir);

            // Write target AVD pointer .ini
            string iniContent = string.Format("avd.ini.encoding=UTF-8\r\npath={0}\r\npath.rel=avd\\{1}.avd\r\ntarget=android-29\r\n", tgtDir, targetName);
            File.WriteAllText(tgtIni, iniContent, Encoding.UTF8);

            // Copy and adapt config.ini
            string tgtCfg = Path.Combine(tgtDir, "config.ini");
            if (File.Exists(srcCfg))
            {
                string cfgText = File.ReadAllText(srcCfg, Encoding.UTF8);
                cfgText = System.Text.RegularExpressions.Regex.Replace(cfgText, @"avd\.name\s*=.*", "avd.name = " + targetName);
                cfgText = System.Text.RegularExpressions.Regex.Replace(cfgText, @"avd\.id\s*=.*", "avd.id = " + targetName);
                File.WriteAllText(tgtCfg, cfgText, Encoding.UTF8);
            }

            // Generate brand-new unique physical device fingerprint for anti-cheat isolation
            DeviceProfile tgtProfile = DeviceCatalog.GetDefaultProfileForIndex(targetIdx);
            string serialNo = (tgtProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLower();
            string mac = GenerateRealisticMac(tgtProfile, targetIdx, serialNo);

            // Preserve source instance's upstream proxy configuration (BUG-27)
            string srcProxy = GetProxy(sourceName);
            string resolvedCustom = !string.IsNullOrEmpty(customTargetName) ? customTargetName.Trim() : ("Instance #" + targetIdx);
            string identFile = Path.Combine(tgtDir, "identity.json");
            string identContent = string.Format("{{\"CustomName\":\"{0}\",\"Serial\":\"{1}\",\"AndroidId\":\"{2}\",\"Mac\":\"{3}\",\"Proxy\":\"{4}\",\"PhoneModel\":\"{5}\"}}",
                EscapeJson(resolvedCustom), EscapeJson(serialNo), EscapeJson(androidId), EscapeJson(mac), EscapeJson(srcProxy ?? ""), EscapeJson(tgtProfile.Id));
            File.WriteAllText(identFile, identContent, Encoding.UTF8);

            // Inherit RAM and CPU Core allocation from source instance
            int inheritedRam = 1024;
            int inheritedCores = 1;
            ReadConfigRamCores(srcCfg, ref inheritedRam, ref inheritedCores);

            // Generate target system.prop matching target phone model
            string tgtProp = Path.Combine(tgtDir, "system.prop");
            string sysProp = GenerateSystemProp(tgtProfile, serialNo, inheritedCores);
            File.WriteAllText(tgtProp, sysProp, Encoding.ASCII);
            UpdateConfigIni(tgtCfg, targetName, inheritedRam, inheritedCores, tgtProfile);

            // Clone virtual disk storage
            string srcRaw = Path.Combine(srcDir, "userdata-qemu.img");
            string tgtRaw = Path.Combine(tgtDir, "userdata-qemu.img");
            string srcQcow2 = Path.Combine(srcDir, "userdata-qemu.img.qcow2");
            string tgtQcow2 = Path.Combine(tgtDir, "userdata-qemu.img.qcow2");

            // Fast hardlink or copy base golden image
            if (File.Exists(srcRaw))
            {
                if (!File.Exists(tgtRaw) || new FileInfo(tgtRaw).Length != new FileInfo(srcRaw).Length)
                {
                    try { if (File.Exists(tgtRaw)) File.Delete(tgtRaw); } catch { }
                    bool linked = Win32.CreateHardLink(tgtRaw, srcRaw, IntPtr.Zero);
                    if (!linked)
                    {
                        File.Copy(srcRaw, tgtRaw, true);
                    }
                }
            }
            else if (File.Exists(GoldenMasterPath))
            {
                bool linked = Win32.CreateHardLink(tgtRaw, GoldenMasterPath, IntPtr.Zero);
                if (!linked) File.Copy(GoldenMasterPath, tgtRaw, true);
            }

            // Copy differential QCOW2 overlay (containing all downloaded 3GB maps & game state)
            if (cloneState && File.Exists(srcQcow2) && new FileInfo(srcQcow2).Length > 0)
            {
                try { if (File.Exists(tgtQcow2)) File.Delete(tgtQcow2); } catch { }
                File.Copy(srcQcow2, tgtQcow2, true);

                // Mark cloned instance for session token sanitization on first boot (CLONE-01)
                try { File.WriteAllText(Path.Combine(tgtDir, "sanitize_session.flag"), "1", Encoding.UTF8); } catch { }

                // Update backing link in QCOW2 header to relative local base image
                if (File.Exists(QemuImgPath))
                {
                    string baseFmt = DetectDiskFormat(tgtRaw);
                    RunCommand(QemuImgPath, string.Format("rebase -u -b \"{0}\" -F {1} \"{2}\"", tgtRaw, baseFmt, tgtQcow2), 30000, tgtDir);
                    // Validate QCOW2 cluster consistency post-rebase (VULN-12)
                    RunCommand(QemuImgPath, "check \"userdata-qemu.img.qcow2\"", 30000, tgtDir);
                }
            }
            else
            {
                // Create fresh differential overlay
                if (File.Exists(QemuImgPath))
                {
                    string baseFmt = DetectDiskFormat(tgtRaw);
                    RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"{0}\" -F {1} \"{2}\"", tgtRaw, baseFmt, tgtQcow2), 30000, tgtDir);
                    if (!File.Exists(tgtQcow2) || (new FileInfo(tgtQcow2).Length == 0))
                    {
                        string altFmt = (baseFmt == "raw") ? "qcow2" : "raw";
                        RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"{0}\" -F {1} \"{2}\"", tgtRaw, altFmt, tgtQcow2), 30000, tgtDir);
                    }
                }
            }

            CleanLocks(tgtDir);

            // Clean snapshots so clone boots clean
            string snapDir = Path.Combine(tgtDir, "snapshots");
            if (Directory.Exists(snapDir))
            {
                try { Directory.Delete(snapDir, true); } catch { }
            }

            Log(string.Format("Successfully cloned '{0}' -> '{1}'! Unique hardware fingerprint applied.", sourceName, targetName));
            return true;
        }
        finally
        {
            // Resume active source instance if it was running before clone (VULN-11)
            if (srcWasRunning && isSourceNumbered)
            {
                Log(string.Format("Resuming active source instance '{0}' post-clone...", sourceName));
                int srcRam = 1024;
                int srcCores = 1;
                ReadConfigRamCores(srcCfg, ref srcRam, ref srcCores);
                string srcProxy = GetProxy(sourceName);
                LaunchInstance(sourceName, srcPort, srcRam > 0 ? srcRam : 1536, srcCores > 0 ? srcCores : 2, srcProxy);
            }
        }
    }

        public bool DeleteInstance(string name)
        {
            int idx = 1;
            System.Text.RegularExpressions.Match m = RxTrailingDigits.Match(name);
            if (m.Success) int.TryParse(m.Groups[1].Value, out idx);
            int port = 5554 + 2 * (idx - 1);
            string serial = "emulator-" + port;

            StopInstance(serial, name);

            // Forcibly terminate lingering QEMU process for this port/avd to guarantee file handle release
            try
            {
                using (System.Management.ManagementObjectSearcher searcher = new System.Management.ManagementObjectSearcher(
                    "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'"))
                using (System.Management.ManagementObjectCollection collection = searcher.Get())
                {
                    foreach (System.Management.ManagementObject mo in collection)
                    {
                        using (mo)
                        {
                            string cl = (mo["CommandLine"] ?? "").ToString();
                            if (cl.Contains("-port " + port) || cl.Contains("-avd " + name))
                            {
                                int pid = Convert.ToInt32(mo["ProcessId"]);
                                try { Process.GetProcessById(pid).Kill(); } catch { }
                            }
                        }
                    }
                }
            }
            catch { }

            // Wait up to 3000ms for QEMU process exit and handle release
            for (int wait = 0; wait < 6; wait++)
            {
                Thread.Sleep(500);
                if (!GetLiveSerials().Contains(serial)) break;
            }

            string avdDir = Path.Combine(AvdHome, name + ".avd");
            string iniFile = Path.Combine(AvdHome, name + ".ini");
            CleanLocks(avdDir);

            Exception lastEx = null;
            for (int attempt = 1; attempt <= 3; attempt++)
            {
                try
                {
                    if (Directory.Exists(avdDir))
                    {
                        ForceDeleteDirectory(avdDir);
                    }
                    if (File.Exists(iniFile))
                    {
                        try { File.SetAttributes(iniFile, FileAttributes.Normal); } catch { }
                        File.Delete(iniFile);
                    }

                    if (!Directory.Exists(avdDir) && !File.Exists(iniFile))
                    {
                        Log(string.Format("Deleted instance '{0}' and its virtual disks.", name));
                        return true;
                    }
                }
                catch (Exception ex)
                {
                    lastEx = ex;
                    Thread.Sleep(800);
                }
            }

            if (Directory.Exists(avdDir) || File.Exists(iniFile))
            {
                Log(string.Format("Error deleting instance '{0}': {1}", name, lastEx != null ? lastEx.Message : "Failed to remove disk files"));
                return false;
            }

            Log(string.Format("Deleted instance '{0}' and its virtual disks.", name));
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
                    try
                    {
                        using (Process p = Process.GetProcessById(cachedPid))
                        {
                            if (!p.HasExited) pid = cachedPid;
                        }
                    }
                    catch { }
                }
            }
            if (pid == 0)
            try
            {
                using (System.Management.ManagementObjectSearcher searcher = new System.Management.ManagementObjectSearcher(
                    "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='qemu-system-x86_64.exe'"))
                using (System.Management.ManagementObjectCollection collection = searcher.Get())
                {
                    foreach (System.Management.ManagementObject mo in collection)
                    {
                        using (mo)
                        {
                            string cl = (mo["CommandLine"] ?? "").ToString();
                            if (cl.Contains("-port " + port))
                            {
                                pid = Convert.ToInt32(mo["ProcessId"]);
                                break;
                            }
                        }
                    }
                }
            }
            catch { }

            if (pid > 0)
            {
                IntPtr hWnd = Win32.FindDeviceWindow(pid);
                if (hWnd != IntPtr.Zero)
                {
                    Win32.ShowWindow(hWnd, Win32.SW_RESTORE);
                    Win32.SetForegroundWindow(hWnd);
                }
            }
        }
    }

    internal sealed class InProcessProxyServer
    {
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
            if (!string.IsNullOrEmpty(upstream))
            {
                Uri u;
                if (Uri.TryCreate(upstream, UriKind.Absolute, out u))
                {
                    _upstreamUri = u;
                    _isSocks5 = u.Scheme.StartsWith("socks5", StringComparison.OrdinalIgnoreCase);
                }
            }
            _model = (profile != null && !string.IsNullOrEmpty(profile.Model)) ? profile.Model : "SM-A515F";
            _ua = (profile != null && !string.IsNullOrEmpty(profile.UserAgent))
                ? profile.UserAgent
                : "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36";
            _osVer = "10";
            Match mVer = Regex.Match(_ua, @"Android\s+([0-9.]+)");
            if (mVer.Success) _osVer = mVer.Groups[1].Value;
            _buildId = (profile != null && !string.IsNullOrEmpty(profile.BuildId)) ? profile.BuildId : "QP1A.190711.020";

            if (!string.IsNullOrEmpty(disguisePath) && File.Exists(disguisePath))
            {
                try
                {
                    string raw = File.ReadAllText(disguisePath, Encoding.UTF8);
                    _disguiseContent = raw
                        .Replace("__DEVICE_UUID__", androidId ?? "ca8ea76b0fe32f85")
                        .Replace("__DEVICE_SERIAL__", serialNo ?? "R58M45F41EFD")
                        .Replace("__DEVICE_MODEL__", _model)
                        .Replace("__DEVICE_MANUFACTURER__", (profile != null && !string.IsNullOrEmpty(profile.Manufacturer)) ? profile.Manufacturer : "samsung")
                        .Replace("__DEVICE_UA__", _ua)
                        .Replace("__DEVICE_GL_VENDOR__", (profile != null && !string.IsNullOrEmpty(profile.GlVendor)) ? profile.GlVendor : "ARM")
                        .Replace("__DEVICE_GL_RENDERER__", (profile != null && !string.IsNullOrEmpty(profile.GlRenderer)) ? profile.GlRenderer : "Mali-G72 MP3")
                        .Replace("__DEVICE_MAX_TEXTURE_SIZE__", (profile != null && profile.MaxTextureSize > 0) ? profile.MaxTextureSize.ToString() : "8192");
                }
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
            catch
            {
                _running = false;
                return false;
            }
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
                try
                {
                    TcpClient client = _listener.AcceptTcpClient();
                    ThreadPool.QueueUserWorkItem(HandleClient, client);
                }
                catch
                {
                    if (!_running) break;
                }
            }
        }

        private TcpClient ConnectTarget(string host, int port)
        {
            if (_upstreamUri != null)
            {
                string upHost = _upstreamUri.Host;
                int upPort = _upstreamUri.Port > 0 ? _upstreamUri.Port : (_isSocks5 ? 1080 : 8080);
                TcpClient upClient = new TcpClient();
                upClient.NoDelay = true;
                IAsyncResult ar = upClient.BeginConnect(upHost, upPort, null, null);
                if (!ar.AsyncWaitHandle.WaitOne(6000))
                {
                    upClient.Close();
                    throw new TimeoutException("Upstream proxy timeout");
                }
                upClient.EndConnect(ar);
                NetworkStream ns = upClient.GetStream();
                ns.ReadTimeout = 8000;
                ns.WriteTimeout = 8000;

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
                    byte[] gResp = new byte[2];
                    ReadExact(ns, gResp, 2);
                    if (gResp[1] == 0x02 && hasAuth)
                    {
                        byte[] uBytes = Encoding.UTF8.GetBytes(user);
                        byte[] pBytes = Encoding.UTF8.GetBytes(pass ?? "");
                        byte[] authMsg = new byte[3 + uBytes.Length + pBytes.Length];
                        authMsg[0] = 0x01;
                        authMsg[1] = (byte)uBytes.Length;
                        Buffer.BlockCopy(uBytes, 0, authMsg, 2, uBytes.Length);
                        authMsg[2 + uBytes.Length] = (byte)pBytes.Length;
                        Buffer.BlockCopy(pBytes, 0, authMsg, 3 + uBytes.Length, pBytes.Length);
                        ns.Write(authMsg, 0, authMsg.Length);
                        byte[] aResp = new byte[2];
                        ReadExact(ns, aResp, 2);
                        if (aResp[1] != 0x00) { upClient.Close(); throw new Exception("SOCKS5 auth failed"); }
                    }
                    byte[] hBytes = Encoding.ASCII.GetBytes(host);
                    byte[] req = new byte[7 + hBytes.Length];
                    req[0] = 0x05; req[1] = 0x01; req[2] = 0x00; req[3] = 0x03;
                    req[4] = (byte)hBytes.Length;
                    Buffer.BlockCopy(hBytes, 0, req, 5, hBytes.Length);
                    req[5 + hBytes.Length] = (byte)(port >> 8);
                    req[6 + hBytes.Length] = (byte)(port & 0xFF);
                    ns.Write(req, 0, req.Length);
                    byte[] cResp = new byte[4];
                    ReadExact(ns, cResp, 4);
                    if (cResp[1] != 0x00) { upClient.Close(); throw new Exception("SOCKS5 connect rejected"); }
                    int skip = (cResp[3] == 0x01) ? 6 : (cResp[3] == 0x04 ? 18 : 0);
                    if (cResp[3] == 0x03)
                    {
                        int dLen = ns.ReadByte();
                        skip = dLen + 2;
                    }
                    if (skip > 0) ReadExact(ns, new byte[skip], skip);
                    ns.ReadTimeout = Timeout.Infinite;
                    ns.WriteTimeout = Timeout.Infinite;
                    return upClient;
                }
                else
                {
                    string authHdr = "";
                    if (!string.IsNullOrEmpty(user))
                    {
                        string b64 = Convert.ToBase64String(Encoding.UTF8.GetBytes(user + ":" + (pass ?? "")));
                        authHdr = "Proxy-Authorization: Basic " + b64 + "\r\n";
                    }
                    byte[] cReq = Encoding.ASCII.GetBytes(string.Format("CONNECT {0}:{1} HTTP/1.1\r\nHost: {0}:{1}\r\nUser-Agent: {2}\r\n{3}\r\n", host, port, _ua, authHdr));
                    ns.Write(cReq, 0, cReq.Length);
                    string statusLine = ReadAsciiLine(ns);
                    while (!string.IsNullOrEmpty(ReadAsciiLine(ns))) { }
                    if (statusLine == null || !statusLine.Contains(" 200"))
                    {
                        upClient.Close();
                        throw new Exception("Upstream HTTP CONNECT rejected");
                    }
                    ns.ReadTimeout = Timeout.Infinite;
                    ns.WriteTimeout = Timeout.Infinite;
                    return upClient;
                }
            }

            TcpClient direct = new TcpClient();
            direct.NoDelay = true;
            IAsyncResult dAr = direct.BeginConnect(host, port, null, null);
            if (!dAr.AsyncWaitHandle.WaitOne(5000))
            {
                direct.Close();
                throw new TimeoutException("Direct connect timeout");
            }
            direct.EndConnect(dAr);
            return direct;
        }

        private static void ReadExact(Stream s, byte[] buf, int count)
        {
            int off = 0;
            while (off < count)
            {
                int r = s.Read(buf, off, count - off);
                if (r <= 0) throw new EndOfStreamException();
                off += r;
            }
        }

        private static string ReadAsciiLine(Stream s)
        {
            List<byte> bytes = new List<byte>(128);
            while (true)
            {
                int b = s.ReadByte();
                if (b < 0) return bytes.Count > 0 ? Encoding.ASCII.GetString(bytes.ToArray()) : null;
                if (b == '\n') break;
                if (b != '\r') bytes.Add((byte)b);
            }
            return Encoding.ASCII.GetString(bytes.ToArray());
        }

        private void HandleClient(object obj)
        {
            TcpClient client = (TcpClient)obj;
            try
            {
                client.NoDelay = true;
                NetworkStream cStream = client.GetStream();
                string reqLine = ReadAsciiLine(cStream);
                if (string.IsNullOrEmpty(reqLine)) { client.Close(); return; }

                List<string> headers = new List<string>();
                string hostHeader = "";
                while (true)
                {
                    string hLine = ReadAsciiLine(cStream);
                    if (string.IsNullOrEmpty(hLine)) break;
                    if (hLine.StartsWith("Host:", StringComparison.OrdinalIgnoreCase))
                        hostHeader = hLine.Substring(5).Trim();
                    headers.Add(hLine);
                }

                string[] parts = reqLine.Split(' ');
                if (parts.Length < 2) { client.Close(); return; }
                string method = parts[0].ToUpperInvariant();
                string rawTarget = parts[1];

                if (method == "GET" && (rawTarget.EndsWith("/__health") || rawTarget.EndsWith("/__disguise.js") || rawTarget.EndsWith("/mobile-disguise.js")))
                {
                    string body = rawTarget.EndsWith("/__health") ? "{\"status\":\"ok\"}" : (_disguiseContent ?? "");
                    string cType = rawTarget.EndsWith("/__health") ? "application/json" : "application/javascript; charset=utf-8";
                    byte[] bodyBytes = Encoding.UTF8.GetBytes(body);
                    byte[] respHdr = Encoding.ASCII.GetBytes(string.Format("HTTP/1.1 200 OK\r\nContent-Type: {0}\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {1}\r\nConnection: close\r\n\r\n", cType, bodyBytes.Length));
                    cStream.Write(respHdr, 0, respHdr.Length);
                    cStream.Write(bodyBytes, 0, bodyBytes.Length);
                    client.Close();
                    return;
                }

                if (method == "CONNECT")
                {
                    string tHost = rawTarget;
                    int tPort = 443;
                    int colon = rawTarget.LastIndexOf(':');
                    if (colon > 0)
                    {
                        tHost = rawTarget.Substring(0, colon);
                        int.TryParse(rawTarget.Substring(colon + 1), out tPort);
                    }
                    TcpClient remote = ConnectTarget(tHost, tPort);
                    NetworkStream rStream = remote.GetStream();
                    byte[] okBytes = Encoding.ASCII.GetBytes("HTTP/1.1 200 Connection Established\r\n\r\n");
                    cStream.Write(okBytes, 0, okBytes.Length);
                    SpliceStreams(client, cStream, remote, rStream);
                    return;
                }

                // Standard HTTP or WebSocket request: parse host/port & rewrite spoofing headers
                string destHost = hostHeader;
                int destPort = 80;
                string pathAndQuery = rawTarget;
                if (rawTarget.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
                {
                    Uri u = new Uri(rawTarget);
                    destHost = u.Host;
                    destPort = u.Port > 0 ? u.Port : 80;
                    pathAndQuery = u.PathAndQuery;
                }
                else if (destHost.Contains(":"))
                {
                    int cIdx = destHost.LastIndexOf(':');
                    int.TryParse(destHost.Substring(cIdx + 1), out destPort);
                    destHost = destHost.Substring(0, cIdx);
                }

                TcpClient httpRemote = ConnectTarget(destHost, destPort);
                NetworkStream httpRStream = httpRemote.GetStream();

                StringBuilder outReq = new StringBuilder();
                outReq.AppendFormat("{0} {1} HTTP/1.1\r\n", method, pathAndQuery);
                bool wroteUa = false;
                foreach (string h in headers)
                {
                    int sep = h.IndexOf(':');
                    if (sep <= 0) continue;
                    string k = h.Substring(0, sep).Trim();
                    string v = h.Substring(sep + 1).Trim();
                    if (k.Equals("Proxy-Connection", StringComparison.OrdinalIgnoreCase) ||
                        k.Equals("Proxy-Authorization", StringComparison.OrdinalIgnoreCase) ||
                        k.Equals("Via", StringComparison.OrdinalIgnoreCase) ||
                        k.Equals("X-Forwarded-For", StringComparison.OrdinalIgnoreCase) ||
                        k.Equals("Forwarded", StringComparison.OrdinalIgnoreCase))
                        continue;

                    if (k.Equals("User-Agent", StringComparison.OrdinalIgnoreCase))
                    {
                        wroteUa = true;
                        if (v.StartsWith("Dalvik/", StringComparison.OrdinalIgnoreCase))
                            v = string.Format("Dalvik/2.1.0 (Linux; U; Android {0}; {1} Build/{2})", _osVer, _model, _buildId);
                        else
                            v = _ua;
                    }
                    else if (k.Equals("Sec-CH-UA-Arch", StringComparison.OrdinalIgnoreCase))
                        v = "\"arm\"";
                    else if (k.Equals("Sec-CH-UA-Platform", StringComparison.OrdinalIgnoreCase))
                        v = "\"Android\"";
                    else if (k.Equals("Sec-CH-UA-Model", StringComparison.OrdinalIgnoreCase))
                        v = "\"" + _model + "\"";

                    outReq.AppendFormat("{0}: {1}\r\n", k, v);
                }
                if (!wroteUa) outReq.AppendFormat("User-Agent: {0}\r\n", _ua);
                outReq.Append("\r\n");

                byte[] reqBytes = Encoding.ASCII.GetBytes(outReq.ToString());
                httpRStream.Write(reqBytes, 0, reqBytes.Length);
                SpliceStreams(client, cStream, httpRemote, httpRStream);
            }
            catch
            {
                try { client.Close(); } catch { }
            }
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
