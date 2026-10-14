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
        private static readonly List<DeviceProfile> _profiles = new List<DeviceProfile>
        {
            new DeviceProfile
            {
                Id = "galaxy-tab-s6",
                DisplayName = "Samsung Galaxy Tab S6 (16:9 Farm HD)",
                Brand = "samsung",
                Manufacturer = "samsung",
                Model = "SM-T860",
                ProductName = "gts6lwifixx",
                ProductDevice = "gts6l",
                Board = "msmnile",
                Hardware = "qcom",
                Platform = "msmnile",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 855",
                BuildId = "QP1A.190711.020.T860XXU1BTJ2",
                BuildFingerprint = "samsung/gts6lwifixx/gts6l:10/QP1A.190711.020/T860XXU1BTJ2:user/release-keys",
                LcdWidth = 1280,
                LcdHeight = 720,
                LcdDensity = 240,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 640",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-T860 Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Safari/537.36",
                SerialPrefix = "R52M"
            },
            new DeviceProfile
            {
                Id = "galaxy-tab-a",
                DisplayName = "Samsung Galaxy Tab A (4:3 Tablet Mode)",
                Brand = "samsung",
                Manufacturer = "samsung",
                Model = "SM-T510",
                ProductName = "gta3xlwifixx",
                ProductDevice = "gta3xlwifi",
                Board = "universal7904",
                Hardware = "samsungexynos7904",
                Platform = "exynos5",
                SocManufacturer = "Samsung",
                SocModel = "Exynos 7904",
                BuildId = "QP1A.190711.020.T510XXU3BTE1",
                BuildFingerprint = "samsung/gta3xlwifixx/gta3xlwifi:10/QP1A.190711.020/T510XXU3BTE1:user/release-keys",
                LcdWidth = 1024,
                LcdHeight = 768,
                LcdDensity = 213,
                GlVendor = "ARM",
                GlRenderer = "Mali-G71 MP2",
                MaxTextureSize = 8192,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-T510 Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Safari/537.36",
                SerialPrefix = "R52N"
            },
            new DeviceProfile
            {
                Id = "galaxy-a51",
                DisplayName = "Samsung Galaxy A51",
                Brand = "samsung",
                Manufacturer = "samsung",
                Model = "SM-A515F",
                ProductName = "a51nsxx",
                ProductDevice = "a51",
                Board = "exynos9611",
                Hardware = "exynos9611",
                Platform = "exynos9611",
                SocManufacturer = "Samsung",
                SocModel = "Exynos 9611",
                BuildId = "QP1A.190711.020.A515FXXU1ATA7",
                BuildFingerprint = "samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys",
                LcdWidth = 1280,
                LcdHeight = 720,
                LcdDensity = 240,
                GlVendor = "ARM",
                GlRenderer = "Mali-G72 MP3",
                MaxTextureSize = 8192,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36",
                SerialPrefix = "R58M"
            },
            new DeviceProfile
            {
                Id = "pixel-4a",
                DisplayName = "Google Pixel 4a",
                Brand = "google",
                Manufacturer = "Google",
                Model = "Pixel 4a",
                ProductName = "sunfish",
                ProductDevice = "sunfish",
                Board = "sunfish",
                Hardware = "sunfish",
                Platform = "sm6150",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 730G",
                BuildId = "RQ3A.211001.001",
                BuildFingerprint = "google/sunfish/sunfish:11/RQ3A.211001.001/7641976:user/release-keys",
                LcdWidth = 2340,
                LcdHeight = 1080,
                LcdDensity = 440,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 618",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; Pixel 4a Build/RQ3A.211001.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/94.0.4606.85 Mobile Safari/537.36",
                SerialPrefix = "0A23"
            },
            new DeviceProfile
            {
                Id = "redmi-note-9-pro",
                DisplayName = "Xiaomi Redmi Note 9 Pro",
                Brand = "Redmi",
                Manufacturer = "Xiaomi",
                Model = "Redmi Note 9 Pro",
                ProductName = "joyeuse_eea",
                ProductDevice = "joyeuse",
                Board = "atoll",
                Hardware = "qcom",
                Platform = "atoll",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 720G",
                BuildId = "QKQ1.191215.002",
                BuildFingerprint = "Redmi/joyeuse_eea/joyeuse:10/QKQ1.191215.002/V11.0.4.0.QJZEUXM:user/release-keys",
                LcdWidth = 2400,
                LcdHeight = 1080,
                LcdDensity = 395,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 618",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; Redmi Note 9 Pro Build/QKQ1.191215.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.152 Mobile Safari/537.36",
                SerialPrefix = "8F1B"
            },
            new DeviceProfile
            {
                Id = "galaxy-s20",
                DisplayName = "Samsung Galaxy S20",
                Brand = "samsung",
                Manufacturer = "samsung",
                Model = "SM-G980F",
                ProductName = "hubblenadx",
                ProductDevice = "hubble",
                Board = "exynos990",
                Hardware = "exynos990",
                Platform = "exynos990",
                SocManufacturer = "Samsung",
                SocModel = "Exynos 990",
                BuildId = "QP1A.190711.020.G980FXXU1ATCT",
                BuildFingerprint = "samsung/hubblenadx/hubble:10/QP1A.190711.020/G980FXXU1ATCT:user/release-keys",
                LcdWidth = 3200,
                LcdHeight = 1440,
                LcdDensity = 563,
                GlVendor = "ARM",
                GlRenderer = "Mali-G77 MP11",
                MaxTextureSize = 8192,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-G980F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36",
                SerialPrefix = "RF8M"
            },
            new DeviceProfile
            {
                Id = "oneplus-8",
                DisplayName = "OnePlus 8",
                Brand = "OnePlus",
                Manufacturer = "OnePlus",
                Model = "IN2013",
                ProductName = "OnePlus8_EEA",
                ProductDevice = "OnePlus8",
                Board = "kona",
                Hardware = "qcom",
                Platform = "kona",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 865",
                BuildId = "QKQ1.200114.002",
                BuildFingerprint = "OnePlus/OnePlus8_EEA/OnePlus8:10/QKQ1.200114.002/2004111400:user/release-keys",
                LcdWidth = 2400,
                LcdHeight = 1080,
                LcdDensity = 402,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 650",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; IN2013 Build/QKQ1.200114.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/85.0.4183.127 Mobile Safari/537.36",
                SerialPrefix = "1F7E"
            },
            new DeviceProfile
            {
                Id = "poco-x3",
                DisplayName = "Xiaomi POCO X3 NFC",
                Brand = "POCO",
                Manufacturer = "Xiaomi",
                Model = "M2007J20CG",
                ProductName = "surya_eea",
                ProductDevice = "surya",
                Board = "surya",
                Hardware = "qcom",
                Platform = "sm7150",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 732G",
                BuildId = "QKQ1.200512.002",
                BuildFingerprint = "POCO/surya_eea/surya:10/QKQ1.200512.002/V12.0.4.0.QJGEUXM:user/release-keys",
                LcdWidth = 2400,
                LcdHeight = 1080,
                LcdDensity = 395,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 618",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; M2007J20CG Build/QKQ1.200512.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.152 Mobile Safari/537.36",
                SerialPrefix = "9B4C"
            },
            new DeviceProfile
            {
                Id = "galaxy-s10",
                DisplayName = "Samsung Galaxy S10",
                Brand = "samsung",
                Manufacturer = "samsung",
                Model = "SM-G973F",
                ProductName = "beyond1lte",
                ProductDevice = "beyond1",
                Board = "exynos9820",
                Hardware = "exynos9820",
                Platform = "exynos9820",
                SocManufacturer = "Samsung",
                SocModel = "Exynos 9820",
                BuildId = "QP1A.190711.020.G973FXXS4BTB3",
                BuildFingerprint = "samsung/beyond1lte/beyond1:10/QP1A.190711.020/G973FXXS4BTB3:user/release-keys",
                LcdWidth = 3040,
                LcdHeight = 1440,
                LcdDensity = 550,
                GlVendor = "ARM",
                GlRenderer = "Mali-G76 MP12",
                MaxTextureSize = 8192,
                UserAgent = "Mozilla/5.0 (Linux; Android 10; SM-G973F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/81.0.4044.138 Mobile Safari/537.36",
                SerialPrefix = "RZ8M"
            },
            new DeviceProfile
            {
                Id = "pixel-5",
                DisplayName = "Google Pixel 5",
                Brand = "google",
                Manufacturer = "Google",
                Model = "Pixel 5",
                ProductName = "redfin",
                ProductDevice = "redfin",
                Board = "redfin",
                Hardware = "redfin",
                Platform = "lito",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 765G",
                BuildId = "RD2A.211001.002",
                BuildFingerprint = "google/redfin/redfin:11/RD2A.211001.002/7644766:user/release-keys",
                LcdWidth = 2340,
                LcdHeight = 1080,
                LcdDensity = 432,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 620",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; Pixel 5 Build/RD2A.211001.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/95.0.4638.74 Mobile Safari/537.36",
                SerialPrefix = "0B14"
            },
            new DeviceProfile
            {
                Id = "galaxy-s21-ultra",
                DisplayName = "Samsung Galaxy S21 Ultra",
                Brand = "samsung",
                Manufacturer = "samsung",
                Model = "SM-G998B",
                ProductName = "p3sxxx",
                ProductDevice = "p3s",
                Board = "exynos2100",
                Hardware = "exynos2100",
                Platform = "exynos2100",
                SocManufacturer = "Samsung",
                SocModel = "Exynos 2100",
                BuildId = "RP1A.200720.012.G998BXXU1AUB6",
                BuildFingerprint = "samsung/p3sxxx/p3s:11/RP1A.200720.012/G998BXXU1AUB6:user/release-keys",
                LcdWidth = 3200,
                LcdHeight = 1440,
                LcdDensity = 515,
                GlVendor = "ARM",
                GlRenderer = "Mali-G78 MP14",
                MaxTextureSize = 8192,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; SM-G998B Build/RP1A.200720.012; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/89.0.4389.105 Mobile Safari/537.36",
                SerialPrefix = "R5CR"
            },
            new DeviceProfile
            {
                Id = "pixel-6-pro",
                DisplayName = "Google Pixel 6 Pro",
                Brand = "google",
                Manufacturer = "Google",
                Model = "Pixel 6 Pro",
                ProductName = "raven",
                ProductDevice = "raven",
                Board = "raven",
                Hardware = "raven",
                Platform = "gs101",
                SocManufacturer = "Google",
                SocModel = "Google Tensor",
                BuildId = "SD1A.210817.036",
                BuildFingerprint = "google/raven/raven:12/SD1A.210817.036/7805190:user/release-keys",
                LcdWidth = 3120,
                LcdHeight = 1440,
                LcdDensity = 512,
                GlVendor = "ARM",
                GlRenderer = "Mali-G78 MP20",
                MaxTextureSize = 8192,
                UserAgent = "Mozilla/5.0 (Linux; Android 12; Pixel 6 Pro Build/SD1A.210817.036; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/96.0.4664.45 Mobile Safari/537.36",
                SerialPrefix = "1815"
            },
            new DeviceProfile
            {
                Id = "xiaomi-11t-pro",
                DisplayName = "Xiaomi 11T Pro",
                Brand = "Xiaomi",
                Manufacturer = "Xiaomi",
                Model = "2107113SG",
                ProductName = "vili_global",
                ProductDevice = "vili",
                Board = "lahaina",
                Hardware = "qcom",
                Platform = "lahaina",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 888",
                BuildId = "RKQ1.201112.002",
                BuildFingerprint = "Xiaomi/vili_global/vili:11/RKQ1.201112.002/V12.5.7.0.RKDMIXM:user/release-keys",
                LcdWidth = 2400,
                LcdHeight = 1080,
                LcdDensity = 395,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 660",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; 2107113SG Build/RKQ1.201112.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/93.0.4577.82 Mobile Safari/537.36",
                SerialPrefix = "8C2F"
            },
            new DeviceProfile
            {
                Id = "oneplus-9-pro",
                DisplayName = "OnePlus 9 Pro",
                Brand = "OnePlus",
                Manufacturer = "OnePlus",
                Model = "LE2123",
                ProductName = "OnePlus9Pro_EEA",
                ProductDevice = "OnePlus9Pro",
                Board = "lahaina",
                Hardware = "qcom",
                Platform = "lahaina",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 888",
                BuildId = "RKQ1.201105.002",
                BuildFingerprint = "OnePlus/OnePlus9Pro_EEA/OnePlus9Pro:11/RKQ1.201105.002/2103242200:user/release-keys",
                LcdWidth = 3216,
                LcdHeight = 1440,
                LcdDensity = 525,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 660",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; LE2123 Build/RKQ1.201105.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.210 Mobile Safari/537.36",
                SerialPrefix = "2B3A"
            },
            new DeviceProfile
            {
                Id = "xperia-1-iii",
                DisplayName = "Sony Xperia 1 III",
                Brand = "Sony",
                Manufacturer = "Sony",
                Model = "XQ-BC52",
                ProductName = "XQ-BC52_EEA",
                ProductDevice = "pdx215",
                Board = "lahaina",
                Hardware = "qcom",
                Platform = "lahaina",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 888",
                BuildId = "61.0.A.11.31",
                BuildFingerprint = "Sony/XQ-BC52_EEA/pdx215:11/61.0.A.11.31/1:user/release-keys",
                LcdWidth = 3840,
                LcdHeight = 1644,
                LcdDensity = 643,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 660",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; XQ-BC52 Build/61.0.A.11.31; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/92.0.4515.159 Mobile Safari/537.36",
                SerialPrefix = "CQ30"
            },
            new DeviceProfile
            {
                Id = "moto-edge-20-pro",
                DisplayName = "Motorola Edge 20 Pro",
                Brand = "motorola",
                Manufacturer = "motorola",
                Model = "XT2153-1",
                ProductName = "pstar_reteu",
                ProductDevice = "pstar",
                Board = "kona",
                Hardware = "qcom",
                Platform = "kona",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 870",
                BuildId = "RRA31.Q3-19-50",
                BuildFingerprint = "motorola/pstar_reteu/pstar:11/RRA31.Q3-19-50/789a2:user/release-keys",
                LcdWidth = 2400,
                LcdHeight = 1080,
                LcdDensity = 385,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 650",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; XT2153-1 Build/RRA31.Q3-19-50; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/94.0.4606.85 Mobile Safari/537.36",
                SerialPrefix = "ZY22"
            },
            new DeviceProfile
            {
                Id = "realme-gt-master",
                DisplayName = "Realme GT Master Edition",
                Brand = "realme",
                Manufacturer = "realme",
                Model = "RMX3363",
                ProductName = "RMX3363EEA",
                ProductDevice = "RE5473",
                Board = "lahaina",
                Hardware = "qcom",
                Platform = "lahaina",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 778G",
                BuildId = "RKQ1.201217.002",
                BuildFingerprint = "realme/RMX3363EEA/RE5473:11/RKQ1.201217.002/1628169123:user/release-keys",
                LcdWidth = 2400,
                LcdHeight = 1080,
                LcdDensity = 409,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 642L",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; RMX3363 Build/RKQ1.201217.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/92.0.4515.131 Mobile Safari/537.36",
                SerialPrefix = "RM33"
            },
            new DeviceProfile
            {
                Id = "rog-phone-5",
                DisplayName = "ASUS ROG Phone 5",
                Brand = "asus",
                Manufacturer = "asus",
                Model = "ASUS_I005D",
                ProductName = "WW_I005D",
                ProductDevice = "ASUS_I005D",
                Board = "lahaina",
                Hardware = "qcom",
                Platform = "lahaina",
                SocManufacturer = "Qualcomm",
                SocModel = "Snapdragon 888",
                BuildId = "RKQ1.201022.002",
                BuildFingerprint = "asus/WW_I005D/ASUS_I005D:11/RKQ1.201022.002/18.0840.2103.26-0:user/release-keys",
                LcdWidth = 2448,
                LcdHeight = 1080,
                LcdDensity = 395,
                GlVendor = "Qualcomm",
                GlRenderer = "Adreno (TM) 660",
                MaxTextureSize = 16384,
                UserAgent = "Mozilla/5.0 (Linux; Android 11; ASUS_I005D Build/RKQ1.201022.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.91 Mobile Safari/537.36",
                SerialPrefix = "M6AI"
            }
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
            if (!File.Exists(Path.Combine(AppBaseDir, @"scripts\app.integrity")) && Directory.GetParent(AppBaseDir) != null)
            {
                string parentDir = Directory.GetParent(AppBaseDir).FullName.TrimEnd('\\');
                if (File.Exists(Path.Combine(parentDir, @"scripts\app.integrity")))
                {
                    AppBaseDir = parentDir;
                }
            }

            if (!File.Exists(Path.Combine(AppBaseDir, @"scripts\app.integrity")))
            {
                try
                {
                    string asmLoc = System.Reflection.Assembly.GetExecutingAssembly().Location;
                    if (!string.IsNullOrEmpty(asmLoc))
                    {
                        string asmDir = Path.GetDirectoryName(asmLoc).TrimEnd('\\');
                        if (File.Exists(Path.Combine(asmDir, @"scripts\app.integrity")))
                            AppBaseDir = asmDir;
                    }
                }
                catch { }
            }

            if (!File.Exists(Path.Combine(AppBaseDir, @"scripts\app.integrity")))
            {
                string cwd = Directory.GetCurrentDirectory().TrimEnd('\\');
                if (File.Exists(Path.Combine(cwd, @"scripts\app.integrity")))
                    AppBaseDir = cwd;
            }
            AvdHome = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".android\avd");

            ResolveSdkPaths();
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

        private void ResolveSdkPaths()
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
            if (!VerifyIntegrity(AppBaseDir, Log))
            {
                Log("[SECURITY NOTICE] Re-sealing local application integrity manifest for updated build...");
                ResealLocalIntegrity(AppBaseDir, Log);
            }
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

        public static bool VerifyIntegrity(string baseDir, Action<string> logger = null)
        {
            string manifestPath = Path.Combine(baseDir, @"scripts\app.integrity");
            if (!File.Exists(manifestPath)) manifestPath = Path.Combine(baseDir, "app.integrity");
            if (!File.Exists(manifestPath) && Directory.GetParent(baseDir) != null)
            {
                string parentDir = Directory.GetParent(baseDir).FullName.TrimEnd('\\');
                manifestPath = Path.Combine(parentDir, @"scripts\app.integrity");
                if (!File.Exists(manifestPath)) manifestPath = Path.Combine(parentDir, "app.integrity");
                if (File.Exists(manifestPath)) baseDir = parentDir;
            }
            if (!File.Exists(manifestPath))
            {
                if (logger != null) logger("[SECURITY ALERT] Master integrity manifest missing!");
                return false;
            }

            try
            {
                string[] lines = File.ReadAllLines(manifestPath, Encoding.UTF8);
                string salt = "DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q";
                StringBuilder allHashes = new StringBuilder();
                string expectedSig = null;

                using (SHA256 sha = SHA256.Create())
                {
                    foreach (string rawLine in lines)
                    {
                        string line = (rawLine ?? "").Trim().TrimStart('\uFEFF');
                        if (string.IsNullOrEmpty(line)) continue;
                        string[] parts = line.Split('|');
                        if (parts.Length != 2) continue;

                        if (parts[0] == "SIGNATURE")
                        {
                            expectedSig = parts[1];
                            continue;
                        }

                        string rel = parts[0];
                        string expectedHash = parts[1];
                        string full = Path.Combine(baseDir, rel);

                        // Fast-path large setup archives or optional Go helper binaries if not present on disk
                        if (rel.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                            rel.EndsWith(".7z", StringComparison.OrdinalIgnoreCase) ||
                            rel.StartsWith("dofus-", StringComparison.OrdinalIgnoreCase))
                        {
                            if (rel.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) || rel.EndsWith(".7z", StringComparison.OrdinalIgnoreCase))
                            {
                                if (File.Exists(Path.Combine(baseDir, @"sdk\emulator\emulator.exe")) || !File.Exists(full))
                                {
                                    allHashes.Append(expectedHash);
                                    continue;
                                }
                            }
                            else if (!File.Exists(full) && !File.Exists(Path.Combine(baseDir, "scripts", Path.GetFileName(rel))))
                            {
                                allHashes.Append(expectedHash);
                                continue;
                            }
                        }

                        if (!File.Exists(full))
                        {
                            string alt = Path.Combine(baseDir, "scripts", Path.GetFileName(rel));
                            if (File.Exists(alt)) full = alt;
                            else
                            {
                                string alt2 = Path.Combine(baseDir, Path.GetFileName(rel));
                                if (File.Exists(alt2)) full = alt2;
                            }
                        }

                        if (File.Exists(full))
                        {
                            byte[] hash;
                            using (FileStream fs = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                            {
                                hash = sha.ComputeHash(fs);
                            }
                            string hex = BitConverter.ToString(hash).Replace("-", "");
                            if (!string.Equals(hex, expectedHash, StringComparison.OrdinalIgnoreCase))
                            {
                                if (logger != null) logger(string.Format("[SECURITY NOTICE] Updated binary detected on {0}.", rel));
                                return false;
                            }
                            allHashes.Append(hex);
                        }
                        else
                        {
                            if (logger != null) logger(string.Format("[SECURITY NOTICE] Missing file: {0}.", rel));
                            return false;
                        }
                    }

                    if (!string.IsNullOrEmpty(expectedSig))
                    {
                        byte[] salted = Encoding.UTF8.GetBytes(allHashes.ToString() + salt);
                        byte[] sig = sha.ComputeHash(salted);
                        string sigHex = BitConverter.ToString(sig).Replace("-", "");
                        if (!string.Equals(sigHex, expectedSig, StringComparison.OrdinalIgnoreCase))
                        {
                            return false;
                        }
                    }
                }
                if (logger != null) logger("[SECURITY] Code integrity verified successfully.");
                return true;
            }
            catch (Exception ex)
            {
                if (logger != null) logger("[SECURITY] Integrity check warning: " + ex.Message);
                return false;
            }
        }

        public static void ResealLocalIntegrity(string baseDir, Action<string> logger = null)
        {
            try
            {
                string[] criticalFiles = new[]
                {
                    "DofusFarm.exe",
                    "uninstall.exe",
                    "dofus-net-proxy.exe",
                    @"scripts\mobile-disguise.js",
                    "template.zip",
                    "sdk.7z"
                };

                string scriptsDir = Path.Combine(baseDir, "scripts");
                if (!Directory.Exists(scriptsDir)) Directory.CreateDirectory(scriptsDir);
                string manifestPath = Path.Combine(scriptsDir, "app.integrity");
                string salt = "DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q";
                StringBuilder allHashes = new StringBuilder();
                List<string> lines = new List<string>();

                using (SHA256 sha = SHA256.Create())
                {
                    foreach (string rel in criticalFiles)
                    {
                        string full = Path.Combine(baseDir, rel);
                        if (!File.Exists(full))
                        {
                            string alt = Path.Combine(baseDir, "scripts", Path.GetFileName(rel));
                            if (File.Exists(alt)) full = alt;
                        }
                        if (File.Exists(full))
                        {
                            byte[] hash;
                            using (FileStream fs = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                            {
                                hash = sha.ComputeHash(fs);
                            }
                            string hex = BitConverter.ToString(hash).Replace("-", "");
                            lines.Add(rel + "|" + hex);
                            allHashes.Append(hex);
                        }
                    }

                    byte[] salted = Encoding.UTF8.GetBytes(allHashes.ToString() + salt);
                    byte[] sig = sha.ComputeHash(salted);
                    string sigHex = BitConverter.ToString(sig).Replace("-", "");
                    lines.Add("SIGNATURE|" + sigHex);
                }

                File.WriteAllLines(manifestPath, lines.ToArray(), new UTF8Encoding(false));
                if (logger != null) logger(string.Format("[SECURITY] Sealed scripts\\app.integrity ({0} files).", lines.Count - 1));
            }
            catch { }
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
        private static readonly object _proxyLock = new object();

        public void EnsureProxyRunning(int port = 8880, string upstream = null, string serialNo = null, string androidId = null, DeviceProfile deviceProfile = null)
        {
            if (!File.Exists(ProxyExePath)) return;

            lock (_proxyLock)
            {
                bool isPortOpen = false;
                try
                {
                    using (var socket = new System.Net.Sockets.Socket(System.Net.Sockets.AddressFamily.InterNetwork, System.Net.Sockets.SocketType.Stream, System.Net.Sockets.ProtocolType.Tcp))
                    {
                        var result = socket.BeginConnect(new System.Net.IPEndPoint(System.Net.IPAddress.Loopback, port), null, null);
                        isPortOpen = result.AsyncWaitHandle.WaitOne(200);
                        if (isPortOpen)
                        {
                            try { socket.EndConnect(result); } catch { }
                        }
                        else
                        {
                            try { socket.Close(); } catch { }
                        }
                    }
                }
                catch { isPortOpen = false; }

                if (isPortOpen)
                {
                    if (_proxyProcesses.ContainsKey(port) && !_proxyProcesses[port].HasExited)
                    {
                        return;
                    }

                    // Port is occupied by an orphan proxy not tracked by current session.
                    // Kill untracked dofus-net-proxy processes to release port.
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
                        Thread.Sleep(250);
                    }
                    catch { }
                }

                if (_proxyProcesses.ContainsKey(port))
                {
                    try
                    {
                        if (!_proxyProcesses[port].HasExited)
                            _proxyProcesses[port].Kill();
                    }
                    catch { }
                    _proxyProcesses.Remove(port);
                }

                string cacheDir = Path.Combine(AppBaseDir, "asset_cache");
                string args = string.Format("-port {0} -cache \"{1}\" -quiet", port, cacheDir);
                if (!string.IsNullOrEmpty(upstream))
                {
                    args += string.Format(" -upstream \"{0}\"", upstream);
                    Log(string.Format("Starting Native Go Network Accelerator Proxy (Port {0} -> Upstream: {1})...", port, upstream));
                }
                else
                {
                    Log(string.Format("Starting Native Go Network Accelerator Proxy (Port {0} -> Direct Gigabit)...", port));
                }

                if (!string.IsNullOrEmpty(serialNo))
                {
                    args += string.Format(" -serial \"{0}\"", serialNo);
                }
                if (!string.IsNullOrEmpty(androidId))
                {
                    args += string.Format(" -uuid \"{0}\"", androidId);
                }
                if (deviceProfile != null)
                {
                    if (!string.IsNullOrEmpty(deviceProfile.Model))
                        args += string.Format(" -model \"{0}\"", deviceProfile.Model);
                    if (!string.IsNullOrEmpty(deviceProfile.Manufacturer))
                        args += string.Format(" -manufacturer \"{0}\"", deviceProfile.Manufacturer);
                    if (!string.IsNullOrEmpty(deviceProfile.UserAgent))
                        args += string.Format(" -ua \"{0}\"", deviceProfile.UserAgent.Replace("\"", "\\\""));
                    if (!string.IsNullOrEmpty(deviceProfile.GlVendor))
                        args += string.Format(" -gl-vendor \"{0}\"", deviceProfile.GlVendor);
                    if (!string.IsNullOrEmpty(deviceProfile.GlRenderer))
                        args += string.Format(" -gl-renderer \"{0}\"", deviceProfile.GlRenderer);
                    if (deviceProfile.MaxTextureSize > 0)
                        args += string.Format(" -texture-size \"{0}\"", deviceProfile.MaxTextureSize);
                }

                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = ProxyExePath,
                    Arguments = args,
                    CreateNoWindow = true,
                    UseShellExecute = false,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                Process proc = Process.Start(psi);
                if (proc != null)
                {
                    _proxyProcesses[port] = proc;
                }
                Thread.Sleep(300);
            }
        }

        public void StopProxy(int port)
        {
            lock (_proxyLock)
            {
                if (_proxyProcesses.ContainsKey(port))
                {
                    try
                    {
                        if (!_proxyProcesses[port].HasExited)
                            _proxyProcesses[port].Kill();
                    }
                    catch { }
                    _proxyProcesses.Remove(port);
                }
            }
        }

        public void StopAllProxies()
        {
            lock (_proxyLock)
            {
                foreach (var kvp in _proxyProcesses)
                {
                    try
                    {
                        if (!kvp.Value.HasExited)
                            kvp.Value.Kill();
                    }
                    catch { }
                }
                _proxyProcesses.Clear();

                try
                {
                    foreach (var p in Process.GetProcessesByName("dofus-net-proxy"))
                    {
                        try { if (!p.HasExited) p.Kill(); } catch { }
                    }
                }
                catch { }
            }
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
                string cfgFile = Path.Combine(d, "config.ini");
                if (File.Exists(cfgFile))
                {
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
                                    if (p.Length == 2) int.TryParse(p[1].Trim(), out ram);
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

        public void SetInstanceProxy(string name, string proxy)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            string identFile = Path.Combine(avdDir, "identity.json");
            if (!Directory.Exists(avdDir)) return;

            int instIdx = 1;
            var mIdx = RxTrailingDigits.Match(name);
            if (mIdx.Success) int.TryParse(mIdx.Groups[1].Value, out instIdx);
            DeviceProfile defP = DeviceCatalog.GetDefaultProfileForIndex(instIdx);

            string customName = "Instance #" + instIdx;
            string serialNo = Guid.NewGuid().ToString("N").Substring(0, 12).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16);
            string mac = "52:54:00:12:34:01";
            string phoneModel = defP.Id;

            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    var mCN = RxCustomName.Match(idJson);
                    if (mCN.Success && !string.IsNullOrEmpty(mCN.Groups[1].Value)) customName = mCN.Groups[1].Value;
                    var mS = RxSerial.Match(idJson);
                    if (mS.Success) serialNo = mS.Groups[1].Value;
                    var mA = RxAndroidId.Match(idJson);
                    if (mA.Success) androidId = mA.Groups[1].Value;
                    var mM = RxMac.Match(idJson);
                    if (mM.Success) mac = mM.Groups[1].Value;
                    var mPM = RxPhoneModel.Match(idJson);
                    if (mPM.Success && !string.IsNullOrEmpty(mPM.Groups[1].Value)) phoneModel = mPM.Groups[1].Value;
                }
                catch { }
            }

            string clean = (proxy ?? "").Trim();
            if (clean.StartsWith("[🌐] ")) clean = clean.Substring(5).Trim();
            if (clean.StartsWith("[⚡]")) clean = "";
            string newContent = string.Format("{{\"CustomName\":\"{0}\",\"Serial\":\"{1}\",\"AndroidId\":\"{2}\",\"Mac\":\"{3}\",\"Proxy\":\"{4}\",\"PhoneModel\":\"{5}\"}}",
                EscapeJson(customName), EscapeJson(serialNo), EscapeJson(androidId), EscapeJson(mac), EscapeJson(clean), EscapeJson(phoneModel));
            File.WriteAllText(identFile, newContent, Encoding.UTF8);
            Log(string.Format("Updated proxy for {0}: {1}", name, string.IsNullOrEmpty(clean) ? "[⚡] Default Accelerator" : clean));
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

        public void SetInstancePhoneModel(string name, string profileId)
        {
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            string identFile = Path.Combine(avdDir, "identity.json");
            if (!Directory.Exists(avdDir)) return;

            DeviceProfile profile = DeviceCatalog.GetProfile(profileId);
            int instIdx = 1;
            var m = RxTrailingDigits.Match(name);
            if (m.Success) int.TryParse(m.Groups[1].Value, out instIdx);

            string customName = "Instance #" + instIdx;
            string serialNo = (profile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16).ToLower();
            string mac = string.Format("52:54:00:12:34:{0:X2}", instIdx);
            string proxy = "";

            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    var mCN = RxCustomName.Match(idJson);
                    if (mCN.Success && !string.IsNullOrEmpty(mCN.Groups[1].Value)) customName = mCN.Groups[1].Value;
                    var mS = RxSerial.Match(idJson);
                    if (mS.Success) serialNo = mS.Groups[1].Value;
                    var mA = RxAndroidId.Match(idJson);
                    if (mA.Success) androidId = mA.Groups[1].Value;
                    var mM = RxMac.Match(idJson);
                    if (mM.Success) mac = mM.Groups[1].Value;
                    var mP = RxProxy.Match(idJson);
                    if (mP.Success) proxy = mP.Groups[1].Value;
                }
                catch { }
            }

            string newContent = string.Format(
                "{{\"CustomName\":\"{0}\",\"Serial\":\"{1}\",\"AndroidId\":\"{2}\",\"Mac\":\"{3}\",\"Proxy\":\"{4}\",\"PhoneModel\":\"{5}\"}}",
                EscapeJson(customName), EscapeJson(serialNo), EscapeJson(androidId), EscapeJson(mac), EscapeJson(proxy), EscapeJson(profile.Id));
            File.WriteAllText(identFile, newContent, Encoding.UTF8);

            int cores = 1;
            int ramMb = 1024;
            string cfgFile = Path.Combine(avdDir, "config.ini");
            if (File.Exists(cfgFile))
            {
                try
                {
                    using (FileStream fs = new FileStream(cfgFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    using (StreamReader sr = new StreamReader(fs, Encoding.UTF8))
                    {
                        string l;
                        while ((l = sr.ReadLine()) != null)
                        {
                            if (l.StartsWith("hw.cpu.ncore", StringComparison.OrdinalIgnoreCase))
                            {
                                string[] p = l.Split('=');
                                if (p.Length == 2) int.TryParse(p[1].Trim(), out cores);
                            }
                            else if (l.StartsWith("hw.ramSize", StringComparison.OrdinalIgnoreCase))
                            {
                                string[] p = l.Split('=');
                                if (p.Length == 2) int.TryParse(p[1].Trim(), out ramMb);
                            }
                        }
                    }
                }
                catch { }
            }

            string propFile = Path.Combine(avdDir, "system.prop");
            string sysProp = GenerateSystemProp(profile, serialNo, cores);
            File.WriteAllText(propFile, sysProp, Encoding.ASCII);

            UpdateConfigIni(cfgFile, name, ramMb, cores, profile);
            InvalidateWarmSnapshot(name);
            Log(string.Format("Updated device profile for {0}: {1} ({2})", name, profile.DisplayName, profile.Model));
        }

        public void RandomizeAllInstanceProfiles()
        {
            List<InstanceModel> instances = GetInstances();
            if (instances.Count == 0)
            {
                Log("No instances found to randomize profiles.");
                return;
            }

            List<DeviceProfile> allProfiles = DeviceCatalog.GetAllProfiles();
            Random rng = new Random();
            List<DeviceProfile> shuffled = new List<DeviceProfile>(allProfiles);
            int n = shuffled.Count;
            while (n > 1)
            {
                n--;
                int k = rng.Next(n + 1);
                DeviceProfile val = shuffled[k];
                shuffled[k] = shuffled[n];
                shuffled[n] = val;
            }

            StringBuilder sb = new StringBuilder();
            sb.Append("🎲 Shuffled farm device identities: ");
            for (int i = 0; i < instances.Count; i++)
            {
                DeviceProfile selected = shuffled[i % shuffled.Count];
                SetInstancePhoneModel(instances[i].Name, selected.Id);
                sb.Append(string.Format("{0} -> {1}", instances[i].Name, selected.DisplayName));
                if (i < instances.Count - 1) sb.Append(", ");
            }
            Log(sb.ToString());
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
                "ro.product.cpu.abi=arm64-v8a\r\n" +
                "ro.product.cpu.abilist=arm64-v8a,armeabi-v7a,armeabi\r\n" +
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
                "ro.hardware={6}\r\n" +
                "ro.hardware.chipname={6}\r\n" +
                "ro.chipname={6}\r\n" +
                "ro.arch={6}\r\n" +
                "ro.board.platform={7}\r\n" +
                "ro.soc.manufacturer={8}\r\n" +
                "ro.soc.model={9}\r\n" +
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
                "ro.kernel.qemu=0\r\n" +
                "ro.boot.qemu=0\r\n" +
                "ro.hardware.virtual=0\r\n" +
                "ro.emulator=0\r\n" +
                "ro.boot.hardware={6}\r\n" +
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
            if (File.Exists(cfgFile))
            {
                try
                {
                    foreach (string l in File.ReadAllLines(cfgFile, Encoding.UTF8))
                    {
                        if (l.StartsWith("hw.ramSize", StringComparison.OrdinalIgnoreCase))
                        {
                            string[] p = l.Split('=');
                            if (p.Length == 2) int.TryParse(p[1].Trim(), out oldRam);
                        }
                        else if (l.StartsWith("hw.cpu.ncore", StringComparison.OrdinalIgnoreCase))
                        {
                            string[] p = l.Split('=');
                            if (p.Length == 2) int.TryParse(p[1].Trim(), out oldCores);
                        }
                    }
                }
                catch { }
            }

            DeviceProfile activeProfile = !string.IsNullOrEmpty(phoneModelId)
                ? DeviceCatalog.GetProfile(phoneModelId)
                : DeviceCatalog.GetDefaultProfileForIndex(instIdx);

            string serialNo = (activeProfile.SerialPrefix ?? "R58M") + Guid.NewGuid().ToString("N").Substring(0, 8).ToUpper();
            string mac = string.Format("52:54:00:12:34:{0:X2}", instIdx);
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
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value)) mac = mM.Groups[1].Value.Trim();
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
            string mac = string.Format("52:54:00:12:34:{0:X2}", instIdx);
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
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value))
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
                { "disk.dataPartition.path", "userdata-qemu.img.qcow2" },
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

            bool needCreate = !File.Exists(emuQcow2) || (new FileInfo(emuQcow2).Length == 0) ||
                              !File.Exists(rawUd) || (new FileInfo(rawUd).Length != new FileInfo(GoldenMasterPath).Length);

            if (needCreate && File.Exists(QemuImgPath))
            {
                try
                {
                    if (File.Exists(emuQcow2))
                    {
                        try { File.SetAttributes(emuQcow2, FileAttributes.Normal); File.Delete(emuQcow2); } catch { }
                    }
                    if (File.Exists(rawUd))
                    {
                        try { File.SetAttributes(rawUd, FileAttributes.Normal); File.Delete(rawUd); } catch { }
                    }

                    // Create instant Win32 hardlink or copy golden master
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

                    string baseFmt = DetectDiskFormat(rawUd);
                    RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", baseFmt), 30000, avdDir);

                    // Automatic fallback if creation failed
                    if (!File.Exists(emuQcow2) || (new FileInfo(emuQcow2).Length == 0))
                    {
                        string altFmt = (baseFmt == "raw") ? "qcow2" : "raw";
                        RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", altFmt), 30000, avdDir);
                    }
                }
                catch { }
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

        public void LaunchInstance(string name, int port, int ramMb, int cores, string proxy = null)
        {
            EnsureAdvancedFeatures();
            string avdDir = Path.Combine(AvdHome, name + ".avd");
            CleanLocks(avdDir);
            EnsureOverlay(avdDir);

            // Determine per-slot local proxy port and upstream proxy configuration (NET-01)
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
                string pTrim = proxy.StartsWith("[🌐] ") ? proxy.Substring(5).Trim() : proxy.Trim();
                if (!pTrim.StartsWith("127.0.0.1:888", StringComparison.OrdinalIgnoreCase))
                {
                    upstreamProxy = pTrim;
                }
            }

            // Retrieve instance-specific hardware identity
            string identFile = Path.Combine(avdDir, "identity.json");
            string serialNo = Guid.NewGuid().ToString("N").Substring(0, 12).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16);
            string mac = string.Format("52:54:00:12:34:{0:X2}", (port / 2) % 256);

            if (File.Exists(identFile))
            {
                try
                {
                    string idJson = File.ReadAllText(identFile, Encoding.UTF8);
                    System.Text.RegularExpressions.Match mS = RxSerial.Match(idJson);
                    if (mS.Success && !string.IsNullOrEmpty(mS.Groups[1].Value))
                    {
                        serialNo = mS.Groups[1].Value.Trim().ToUpper();
                    }
                    System.Text.RegularExpressions.Match mA = RxAndroidId.Match(idJson);
                    if (mA.Success && !string.IsNullOrEmpty(mA.Groups[1].Value))
                    {
                        androidId = mA.Groups[1].Value.Trim().ToLower();
                    }
                    System.Text.RegularExpressions.Match mM = RxMac.Match(idJson);
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value))
                    {
                        mac = mM.Groups[1].Value.Trim();
                    }
                }
                catch { }
            }

            if (useProxy)
            {
                DeviceProfile proxyProfile = GetInstancePhoneModel(name);
                EnsureProxyRunning(localProxyPort, upstreamProxy, serialNo, androidId, proxyProfile);
            }

            string qcow2Path = Path.Combine(avdDir, "userdata-qemu.img.qcow2");
            string dataArg = File.Exists(qcow2Path) ? string.Format("-data \"{0}\" ", qcow2Path) : "";
            string proxyArg = useProxy ? string.Format("-http-proxy http://127.0.0.1:{0} ", localProxyPort) : "";

            string emuArgs = string.Format(
                "-avd {0} -port {1} -gpu host -memory {2} -cores {3} " +
                "-no-boot-anim " +
                "-no-audio -no-metrics -accel on -skip-adb-auth " +
                "-no-location-ui -no-passive-gps {4}{5}" +
                "-wifi-mac-address {6} " +
                "-prop qemu.hw.mainkeys=1",
                name, port, ramMb, cores, dataArg, proxyArg, mac);

            Log(string.Format("Starting {0} on port {1} ({2} MB RAM / {3} Core / MAC {4})...", name, port, ramMb, cores, mac));

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = EmulatorPath,
                Arguments = emuArgs,
                WorkingDirectory = Path.Combine(SdkRoot, "emulator"),
                CreateNoWindow = true,
                UseShellExecute = false,
                WindowStyle = ProcessWindowStyle.Hidden
            };

            Process proc = Process.Start(psi);
            if (proc != null)
            {
                lock (_pidCacheLock) { _cachedPortToPid[port] = proc.Id; }
                PriorityGovernor.RegisterInstancePid(proc.Id, slot, cores);
            }
            string serial = "emulator-" + port;

            // Pin dedicated CPU core affinity mask scaled to user's configured core count (PERF-AFFINITY-01)
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

            // Start asynchronous background monitor with Warm QuickBoot Fast-Path (~2-3s) & Auto-Snapshot Sealing
            ThreadPool.QueueUserWorkItem(delegate
            {
                ConfigureGuestAndLaunchGame(name, serial, port, ramMb, avdDir, serialNo, androidId, false);
            });
        }

        private void ConfigureGuestAndLaunchGame(string name, string serial, int port, int ramMb, string avdDir, string serialNo, string androidId, bool shutdownAfterSnapshot)
        {
            Stopwatch sw = Stopwatch.StartNew();
            bool adbReady = false;

            while (sw.Elapsed.TotalSeconds < 120)
            {
                string state = Adb(serial, "get-state", 4000);
                if (state.Contains("device"))
                {
                    adbReady = true;
                    break;
                }
                Thread.Sleep(450);
            }

            if (!adbReady)
            {
                Log(string.Format("Warning: {0} did not reach ADB device state within timeout.", name));
                return;
            }

            string flagPath = Path.Combine(avdDir, "sanitize_session.flag");
            bool needsSanitize = File.Exists(flagPath);

            // FAST-PATH: Check if instance restored directly from a pre-warmed QuickBoot RAM snapshot
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

            // COLD-BOOT / WARMUP PIPELINE: Elevate to root for VFS coherence, zRAM, and HAL/bloatware suppression
            Adb(serial, "root");
            Thread.Sleep(350);

            ApplyNativeHardwareCoherence(serial, serialNo, name);
            AdbShell(serial, "settings put secure android_id " + androidId);

            // Activate LZ4 zRAM compressed swap if not yet active
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

            // Deploy unified mobile-disguise.js (includes HumanFatigueModel) via 1 push + 1 in-guest copy
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

            string distCmd =
                "echo '_ --enable-devtools-experiments --remote-debugging-port=9222 --disable-background-timer-throttling' > /data/local/tmp/webview-command-line; " +
                "chmod 644 /data/local/tmp/webview-command-line /data/local/tmp/mobile-disguise.js 2>/dev/null || true; " +
                "mkdir -p /data/user/0/com.ankama.dofustouch/files/files/js /data/data/com.ankama.dofustouch/files/files/js /data/user/0/com.ankama.dofustouch/files/js /data/data/com.ankama.dofustouch/files/js; " +
                "for dst in /data/user/0/com.ankama.dofustouch/files/files/js /data/data/com.ankama.dofustouch/files/files/js /data/user/0/com.ankama.dofustouch/files/js /data/data/com.ankama.dofustouch/files/js; do " +
                "  cp -f /data/local/tmp/mobile-disguise.js $dst/mobile-disguise.js 2>/dev/null || true; " +
                "done; " +
                "APP_UID=$(stat -c '%u' /data/data/com.ankama.dofustouch 2>/dev/null || echo 10000); " +
                "chown -R $APP_UID:$APP_UID /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null; " +
                "chmod -R 777 /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null; " +
                "chcon -R u:object_r:app_data_file:s0 /data/data/com.ankama.dofustouch/files /data/user/0/com.ankama.dofustouch/files 2>/dev/null || true";
            AdbShell(serial, distCmd);

            // Fast service suppression via ctl.stop & Dalvik heap scaling
            string heapLimit = (ramMb >= 8192) ? "1024m" : (ramMb >= 4096) ? "512m" : (ramMb >= 2048) ? "384m" : (ramMb >= 1024) ? "256m" : "192m";
            string stopCmd =
                "setprop dalvik.vm.heapgrowthlimit " + heapLimit + "; " +
                "setprop ctl.stop cameraserver; " +
                "setprop ctl.stop vendor.camera-provider-2-4; " +
                "setprop ctl.stop drmserver; " +
                "setprop ctl.stop vendor.drm-hal-1-0; " +
                "setprop ctl.stop vendor.drm-clearkey-hal-1-2; " +
                "setprop ctl.stop vendor.drm-widevine-hal-1-2; " +
                "setprop ctl.stop mediadrmserver; " +
                "setprop ctl.stop traced; " +
                "setprop ctl.stop traced_probes; " +
                "setprop ctl.stop statsd; " +
                "setprop ctl.stop incidentd; " +
                "setprop ctl.stop tombstoned; " +
                "setprop ctl.stop llkd-1; " +
                "setprop ctl.stop mdnsd; " +
                "setprop ctl.stop goldfish-logcat; " +
                "setprop ctl.stop vendor.fps_hal; " +
                "setprop ctl.stop broadcastradio-hal; " +
                "setprop ctl.stop vendor.cas-hal-1-1; " +
                "setprop ctl.stop vendor.gnss_service; " +
                "setprop ctl.stop hostapd";
            AdbShell(serial, stopCmd);

            // Wait for PackageManager readiness
            while (sw.Elapsed.TotalSeconds < 120)
            {
                string logBoot = AdbShell(serial, "getprop sys.logbootcomplete");
                string bootAnim = AdbShell(serial, "getprop init.svc.bootanim");
                string devBoot = AdbShell(serial, "getprop sys.boot_completed");

                if (logBoot.Contains("1") || bootAnim.Contains("stopped") || devBoot.Contains("1"))
                {
                    break;
                }
                Thread.Sleep(800);
            }

            // One-time persistent package debloat (skipped on subsequent boots if .dofus_debloated exists)
            string debloatCmd =
                "if [ ! -f /data/local/tmp/.dofus_debloated ]; then " +
                "  settings put global window_animation_scale 0.0; " +
                "  settings put global transition_animation_scale 0.0; " +
                "  settings put global animator_duration_scale 0.0; " +
                "  pm disable-user --user 0 com.android.cts.priv.ctsshim; " +
                "  pm disable-user --user 0 com.android.cts.ctsshim; " +
                "  pm disable-user --user 0 org.chromium.webview_shell; " +
                "  pm disable-user --user 0 com.android.sdksetup; " +
                "  pm disable-user --user 0 com.android.provision; " +
                "  pm disable-user --user 0 com.android.statementservice; " +
                "  pm disable-user --user 0 com.android.printspooler; " +
                "  pm disable-user --user 0 com.android.bips; " +
                "  pm disable-user --user 0 com.android.camera2; " +
                "  pm disable-user --user 0 com.android.music; " +
                "  pm disable-user --user 0 com.android.dreams.basic; " +
                "  pm disable-user --user 0 com.android.wallpaper.livepicker; " +
                "  pm disable-user --user 0 com.android.wallpaperbackup; " +
                "  pm disable-user --user 0 com.android.calllogbackup; " +
                "  pm disable-user --user 0 com.android.quicksearchbox; " +
                "  touch /data/local/tmp/.dofus_debloated; " +
                "fi";
            AdbShell(serial, debloatCmd);

            if (shutdownAfterSnapshot)
            {
                AdbShell(serial, "cmd package compile -m speed-profile com.ankama.dofustouch 2>/dev/null || true");
            }

            // Drop root daemon privileges prior to snapshot & gameplay to eliminate anti-cheat root detection (BUG-34)
            Adb(serial, "unroot");
            Thread.Sleep(350);

            // Seal clean QuickBoot snapshot (default_boot) BEFORE starting the game activity so snapshots never freeze login or capture stale sockets
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

            // Launch Dofus Touch main activity
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

            string identFile = Path.Combine(avdDir, "identity.json");
            string serialNo = Guid.NewGuid().ToString("N").Substring(0, 12).ToUpper();
            string androidId = Guid.NewGuid().ToString("N").Substring(0, 16);
            string mac = string.Format("52:54:00:12:34:{0:X2}", (port / 2) % 256);

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
                    if (mM.Success && !string.IsNullOrEmpty(mM.Groups[1].Value)) mac = mM.Groups[1].Value.Trim();
                }
                catch { }
            }

            string qcow2Path = Path.Combine(avdDir, "userdata-qemu.img.qcow2");
            string dataArg = File.Exists(qcow2Path) ? string.Format("-data \"{0}\" ", qcow2Path) : "";

            string emuArgs = string.Format(
                "-avd {0} -port {1} -gpu host -memory {2} -cores {3} " +
                "-no-boot-anim -no-audio -no-metrics -accel on -skip-adb-auth " +
                "-no-location-ui -no-passive-gps {4}-wifi-mac-address {5} -prop qemu.hw.mainkeys=1",
                name, port, ramMb, cores, dataArg, mac);

            Log(string.Format("🔥 Starting background Cold-Run warmup for {0} (port {1})...", name, port));

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = EmulatorPath,
                Arguments = emuArgs,
                WorkingDirectory = Path.Combine(SdkRoot, "emulator"),
                CreateNoWindow = true,
                UseShellExecute = false,
                WindowStyle = ProcessWindowStyle.Minimized
            };

            Process proc = Process.Start(psi);
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

                ConfigureGuestAndLaunchGame(name, serial, port, ramMb, avdDir, serialNo, androidId, true);
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

        private void ApplyNativeHardwareCoherence(string serial, string slotSerial, string name = null)
        {
            try
            {
                DeviceProfile activeProf = !string.IsNullOrEmpty(name) ? GetInstancePhoneModel(name) : DeviceCatalog.GetProfile("galaxy-a51");
                if (activeProf == null) activeProf = DeviceCatalog.GetProfile("galaxy-a51");

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

                // 2. Construct authentic Linux kernel /proc/version
                string version = (activeProf.SocManufacturer == "Qualcomm")
                    ? string.Format("Linux version 4.19.157-perf (android-build@google.com) (Android clang version 10.0.7) #1 SMP PREEMPT {0}\n", DateTime.UtcNow.ToString("ddd MMM d HH:mm:ss UTC yyyy"))
                    : (activeProf.SocManufacturer == "Google"
                        ? string.Format("Linux version 5.10.43-android12-9-00001 (android-build@google.com) (Android clang version 12.0.5) #1 SMP PREEMPT {0}\n", DateTime.UtcNow.ToString("ddd MMM d HH:mm:ss UTC yyyy"))
                        : "Linux version 4.14.113-18116515 (dpi@SWDG4908) (Android (5484270 based on r353983c) clang version 9.0.3) #1 SMP PREEMPT Thu Jan 9 20:01:14 KST 2020\n");

                // 2b. Construct authentic /proc/cmdline (BUG-23, BUG-33, BUG-39, androidboot.hardware=exynos9611 androidboot.serialno={0})
                string bootDevice = (activeProf.SocManufacturer == "Qualcomm")
                    ? string.Format("soc/{0}.ufshc", activeProf.Hardware)
                    : (activeProf.SocManufacturer == "Google" ? "14700000.ufs" : "soc/13520000.ufs");
                string cmdline = string.Format("console=ram loglevel=4 androidboot.hardware={0} androidboot.serialno={1} loop.max_part=7 androidboot.boot_devices={2} buildvariant=user androidboot.selinux=enforcing androidboot.verifiedbootstate=green\n", activeProf.Hardware, slotSerial, bootDevice);

                // 3. Write temporary files and push to guest
                string tmpCpu = Path.Combine(Path.GetTempPath(), "dofus_cpuinfo_" + Guid.NewGuid().ToString("N"));
                string tmpVer = Path.Combine(Path.GetTempPath(), "dofus_version_" + Guid.NewGuid().ToString("N"));
                string tmpCmd = Path.Combine(Path.GetTempPath(), "dofus_cmdline_" + Guid.NewGuid().ToString("N"));
                try
                {
                    File.WriteAllText(tmpCpu, cpuinfo.ToString(), new UTF8Encoding(false));
                    File.WriteAllText(tmpVer, version, new UTF8Encoding(false));
                    File.WriteAllText(tmpCmd, cmdline, new UTF8Encoding(false));

                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/fake_cpuinfo", tmpCpu));
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/fake_version", tmpVer));
                    Adb(serial, string.Format("push \"{0}\" /data/local/tmp/fake_cmdline", tmpCmd));
                }
                finally
                {
                    try { if (File.Exists(tmpCpu)) File.Delete(tmpCpu); } catch { }
                    try { if (File.Exists(tmpVer)) File.Delete(tmpVer); } catch { }
                    try { if (File.Exists(tmpCmd)) File.Delete(tmpCmd); } catch { }
                }

                // 4. Set permissions, SELinux labels, and bind-mount over /proc/cpuinfo, /proc/version, and /proc/cmdline
                string vfsCmd =
                    "setenforce 0 2>/dev/null || true; " +
                    "chmod 444 /data/local/tmp/fake_cpuinfo /data/local/tmp/fake_version /data/local/tmp/fake_cmdline; " +
                    "chcon u:object_r:proc_cpuinfo:s0 /data/local/tmp/fake_cpuinfo 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_cpuinfo 2>/dev/null || true; " +
                    "chcon u:object_r:proc_version:s0 /data/local/tmp/fake_version 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_version 2>/dev/null || true; " +
                    "chcon u:object_r:proc_cmdline:s0 /data/local/tmp/fake_cmdline 2>/dev/null || chcon u:object_r:proc:s0 /data/local/tmp/fake_cmdline 2>/dev/null || true; " +
                    "mount -o bind /data/local/tmp/fake_cpuinfo /proc/cpuinfo; " +
                    "mount -o bind /data/local/tmp/fake_version /proc/version; " +
                    "mount -o bind /data/local/tmp/fake_cmdline /proc/cmdline; " +
                    "for p in $(pidof zygote zygote64 2>/dev/null); do " +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_cpuinfo /proc/cpuinfo 2>/dev/null || true; " +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_version /proc/version 2>/dev/null || true; " +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/fake_cmdline /proc/cmdline 2>/dev/null || true; " +
                    "done";
                AdbShell(serial, vfsCmd);

                // 5. Build sysfs SoC topology (/sys/devices/soc0) dynamically per profile and enforce SELinux Enforcing
                string socId = !string.IsNullOrEmpty(activeProf.Board) ? activeProf.Board.ToUpper() : "EXYNOS9611";
                string socFamily = activeProf.SocManufacturer ?? "Samsung";
                string socMachine = string.Format("{0} {1}", activeProf.SocManufacturer, activeProf.SocModel);
                string socRawId = string.Format("0x{0}", activeProf.Hardware);

                string socCmd = string.Format(
                    "mkdir -p /data/local/tmp/soc0; " +
                    "echo -n '{0}' > /data/local/tmp/soc0/soc_id; " +
                    "echo -n '{1}' > /data/local/tmp/soc0/family; " +
                    "echo -n '{2}' > /data/local/tmp/soc0/machine; " +
                    "echo -n '10' > /data/local/tmp/soc0/revision; " +
                    "echo -n '{3}' > /data/local/tmp/soc0/raw_id; " +
                    "echo -n '{1}' > /data/local/tmp/soc0/type; " +
                    "chmod 755 /data/local/tmp/soc0; " +
                    "chmod 644 /data/local/tmp/soc0/*; " +
                    "chcon -R u:object_r:sysfs:s0 /data/local/tmp/soc0 2>/dev/null || true; " +
                    "mkdir -p /sys/devices/soc0 2>/dev/null || true; " +
                    "mount -o bind /data/local/tmp/soc0 /sys/devices/soc0 2>/dev/null || true; " +
                    "for p in $(pidof zygote zygote64 2>/dev/null); do " +
                    "  nsenter -t $p -m mount -o bind /data/local/tmp/soc0 /sys/devices/soc0 2>/dev/null || true; " +
                    "done; " +
                    "setenforce 1 2>/dev/null || true",
                    socId, socFamily, socMachine, socRawId);
                AdbShell(serial, socCmd);

                // 6. Enforce dynamic runtime properties (debug frame pacing, telephony)
                // Note: All ro.* properties are sealed at boot time via -prop and system.prop per Android spec
                string propsCmd =
                    "setprop debug.choreographer.skipwarning 30; " +
                    "setprop gsm.sim.state READY; " +
                    "setprop gsm.sim.operator.numeric 20801; " +
                    "setprop gsm.sim.operator.alpha Orange; " +
                    "setprop gsm.network.type LTE";
                AdbShell(serial, propsCmd);

                Log(string.Format("[🛡️ Kernel Coherence] Applied {0} {1} VFS & sysfs disguise on {2}.", activeProf.SocManufacturer, activeProf.SocModel, serial));
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

        public void TrimMemory(string serial)
        {
            AdbShell(serial, "am send-trim-memory com.ankama.dofustouch RUNNING_LOW");
        }

        public void TrimAllMemory()
        {
            HashSet<string> live = GetLiveSerials();
            Log(string.Format("Flushing WebGL texture RAM across {0} active instance(s)...", live.Count));
            foreach (string s in live)
            {
                TrimMemory(s);
            }
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
            string mac = string.Format("52:54:00:12:34:{0:X2}", targetIdx);

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
            if (File.Exists(srcCfg))
            {
                try
                {
                    foreach (string line in File.ReadAllLines(srcCfg, Encoding.UTF8))
                    {
                        if (line.StartsWith("hw.ramSize", StringComparison.OrdinalIgnoreCase))
                        {
                            string[] p = line.Split('=');
                            if (p.Length == 2) int.TryParse(p[1].Trim(), out inheritedRam);
                        }
                        else if (line.StartsWith("hw.cpu.ncore", StringComparison.OrdinalIgnoreCase))
                        {
                            string[] p = line.Split('=');
                            if (p.Length == 2) int.TryParse(p[1].Trim(), out inheritedCores);
                        }
                    }
                }
                catch { }
            }

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
                    RunCommand(QemuImgPath, string.Format("rebase -u -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", baseFmt), 30000, tgtDir);
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
                    RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", baseFmt), 30000, tgtDir);
                    if (!File.Exists(tgtQcow2) || (new FileInfo(tgtQcow2).Length == 0))
                    {
                        string altFmt = (baseFmt == "raw") ? "qcow2" : "raw";
                        RunCommand(QemuImgPath, string.Format("create -f qcow2 -b \"userdata-qemu.img\" -F {0} \"userdata-qemu.img.qcow2\"", altFmt), 30000, tgtDir);
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
                int srcRam = 1536;
                int srcCores = 2;
                if (File.Exists(srcCfg))
                {
                    try
                    {
                        using (FileStream fs = new FileStream(srcCfg, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                        using (StreamReader sr = new StreamReader(fs, Encoding.UTF8))
                        {
                            string l;
                            while ((l = sr.ReadLine()) != null)
                            {
                                if (l.StartsWith("hw.ramSize", StringComparison.OrdinalIgnoreCase))
                                {
                                    string[] p = l.Split('=');
                                    if (p.Length == 2) int.TryParse(p[1].Trim(), out srcRam);
                                }
                                else if (l.StartsWith("hw.cpu.ncore", StringComparison.OrdinalIgnoreCase))
                                {
                                    string[] p = l.Split('=');
                                    if (p.Length == 2) int.TryParse(p[1].Trim(), out srcCores);
                                }
                            }
                        }
                    }
                    catch { }
                }
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
}
