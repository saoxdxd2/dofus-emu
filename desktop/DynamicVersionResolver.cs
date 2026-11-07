using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace DofusFarm.Engine
{
    public class DofusVersionInfo
    {
        public string AppVersion { get; set; }
        public string VersionCode { get; set; }
        public string BuildVersion { get; set; }
        public DateTime LastCheckedUtc { get; set; }
        public string Source { get; set; }

        public override string ToString()
        {
            return string.Format("{0} (Build: {1}, Code: {2}, Source: {3})", AppVersion, BuildVersion, VersionCode, Source);
        }
    }

    /// <summary>
    /// Multi-tier dynamic version resolver for Dofus Touch.
    /// Automatically queries official store metadata so protocol handshakes
    /// and client versioning remain synchronized without requiring manual recompilation.
    /// </summary>
    public static class DynamicVersionResolver
    {
        private const string PackageId = "com.ankama.dofustouch";
        private const string StoreUrl = "https://play.google.com/store/apps/details?id=com.ankama.dofustouch&hl=en";
        public const string DefaultAppVersion = "3.14.2";
        public const string DefaultVersionCode = "3014002";
        public const string DefaultBuildVersion = "1.74.9";

        private static readonly object _lock = new object();
        private static DofusVersionInfo _cachedResolved = null;

        public static string CalculateVersionCode(string semver)
        {
            if (string.IsNullOrEmpty(semver)) return DefaultVersionCode;
            try
            {
                Match m = Regex.Match(semver, @"^(\d+)\.(\d+)(?:\.(\d+))?");
                if (m.Success)
                {
                    int major = int.Parse(m.Groups[1].Value);
                    int minor = int.Parse(m.Groups[2].Value);
                    int patch = m.Groups[3].Success ? int.Parse(m.Groups[3].Value) : 0;
                    long code = ((long)major * 1000000) + ((long)minor * 1000) + patch;
                    return code.ToString();
                }
            }
            catch { }
            return DefaultVersionCode;
        }

        public static string GetConfigFilePath(string customDir = null)
        {
            if (!string.IsNullOrEmpty(customDir))
            {
                return Path.Combine(customDir, "app_version.json");
            }

            // Try local app directory first
            string localPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app_version.json");
            if (File.Exists(localPath)) return localPath;

            // Otherwise use LocalAppData
            string appData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm");
            try { Directory.CreateDirectory(appData); } catch { }
            return Path.Combine(appData, "app_version.json");
        }

        public static DofusVersionInfo LoadCached(string filePath)
        {
            try
            {
                if (File.Exists(filePath))
                {
                    string json = File.ReadAllText(filePath, Encoding.UTF8);
                    Match mApp = Regex.Match(json, @"""appVersion""\s*:\s*""([^""]+)""");
                    Match mBuild = Regex.Match(json, @"""buildVersion""\s*:\s*""([^""]+)""");
                    Match mCode = Regex.Match(json, @"""versionCode""\s*:\s*""([^""]+)""");
                    Match mDate = Regex.Match(json, @"""lastCheckedUtc""\s*:\s*""([^""]+)""");

                    if (mApp.Success)
                    {
                        DateTime dt = DateTime.UtcNow;
                        if (mDate.Success) DateTime.TryParse(mDate.Groups[1].Value, out dt);

                        string app = mApp.Groups[1].Value;
                        string build = mBuild.Success ? mBuild.Groups[1].Value : DefaultBuildVersion;
                        string code = mCode.Success ? mCode.Groups[1].Value : CalculateVersionCode(app);

                        return new DofusVersionInfo
                        {
                            AppVersion = app,
                            BuildVersion = build,
                            VersionCode = code,
                            LastCheckedUtc = dt,
                            Source = "LocalCache"
                        };
                    }
                }
            }
            catch { }
            return null;
        }

        public static void SaveCache(DofusVersionInfo info, string filePath)
        {
            if (info == null || string.IsNullOrEmpty(filePath)) return;
            try
            {
                string dir = Path.GetDirectoryName(filePath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                string json = string.Format(
                    "{{\n  \"appVersion\": \"{0}\",\n  \"versionCode\": \"{1}\",\n  \"buildVersion\": \"{2}\",\n  \"lastCheckedUtc\": \"{3:yyyy-MM-ddTHH:mm:ssZ}\",\n  \"source\": \"{4}\"\n}}\n",
                    info.AppVersion,
                    info.VersionCode,
                    info.BuildVersion,
                    info.LastCheckedUtc,
                    info.Source
                );
                File.WriteAllText(filePath, json, Encoding.UTF8);
            }
            catch { }
        }

        public static string QueryGooglePlayStore(int timeoutMs = 3500)
        {
            try
            {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12 | (SecurityProtocolType)768 | (SecurityProtocolType)3072;
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(StoreUrl);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
                req.Headers.Add(HttpRequestHeader.AcceptEncoding, "gzip, deflate");
                req.AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate;
                req.Headers.Add(HttpRequestHeader.AcceptLanguage, "en-US,en;q=0.9");

                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (Stream stream = resp.GetResponseStream())
                using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                {
                    string html = reader.ReadToEnd();
                    // Match AF_initDataCallback version string pattern: [[[ "3.14.2" ]
                    Match m = Regex.Match(html, @"\[\[\[""([0-9]+\.[0-9]+\.[0-9]+)""\]");
                    if (m.Success)
                    {
                        return m.Groups[1].Value;
                    }

                    // Fallback pattern: inspect 3.x semvers on page
                    MatchCollection matches = Regex.Matches(html, @"\b([3-9]\.[0-9]+\.[0-9]+)\b");
                    string newest = null;
                    Version maxVer = new Version(3, 0, 0);
                    foreach (Match mc in matches)
                    {
                        try
                        {
                            Version v = new Version(mc.Groups[1].Value);
                            if (v >= maxVer)
                            {
                                maxVer = v;
                                newest = mc.Groups[1].Value;
                            }
                        }
                        catch { }
                    }
                    if (!string.IsNullOrEmpty(newest)) return newest;
                }
            }
            catch { }
            return null;
        }

        /// <summary>
        /// Synchronously resolves the active Dofus Touch version using the multi-tier hierarchy:
        /// 1. Fresh local cache (&lt; 4 hours)
        /// 2. Google Play Store live metadata
        /// 3. Stale local cache
        /// 4. Built-in hardcoded fallback
        /// </summary>
        public static DofusVersionInfo ResolveVersion(string customDir = null, bool forceRefresh = false, Action<string> logCallback = null)
        {
            lock (_lock)
            {
                if (!forceRefresh && _cachedResolved != null)
                {
                    return _cachedResolved;
                }

                string configFile = GetConfigFilePath(customDir);
                DofusVersionInfo cached = LoadCached(configFile);

                // If cache is fresh (< 4 hours old) and not forcing refresh, return immediately
                if (!forceRefresh && cached != null && (DateTime.UtcNow - cached.LastCheckedUtc).TotalHours < 4)
                {
                    logCallback?.Invoke(string.Format("[VersionResolver] Using cached version: {0}", cached));
                    _cachedResolved = cached;
                    return cached;
                }

                // Tier 2: Query Google Play
                logCallback?.Invoke("[VersionResolver] Querying Google Play Store for latest Dofus Touch version...");
                string storeVersion = QueryGooglePlayStore(3500);

                if (!string.IsNullOrEmpty(storeVersion))
                {
                    string code = CalculateVersionCode(storeVersion);
                    string build = cached != null && !string.IsNullOrEmpty(cached.BuildVersion)
                        ? cached.BuildVersion
                        : DefaultBuildVersion;

                    var resolved = new DofusVersionInfo
                    {
                        AppVersion = storeVersion,
                        VersionCode = code,
                        BuildVersion = build,
                        LastCheckedUtc = DateTime.UtcNow,
                        Source = "GooglePlayStore"
                    };

                    SaveCache(resolved, configFile);
                    logCallback?.Invoke(string.Format("[VersionResolver] Successfully resolved live version: {0}", resolved));
                    _cachedResolved = resolved;
                    return resolved;
                }

                // Tier 3: Stale cache fallback
                if (cached != null)
                {
                    logCallback?.Invoke(string.Format("[VersionResolver] Store query failed; falling back to existing cache: {0}", cached));
                    _cachedResolved = cached;
                    return cached;
                }

                // Tier 4: Built-in default
                var fallback = new DofusVersionInfo
                {
                    AppVersion = DefaultAppVersion,
                    VersionCode = DefaultVersionCode,
                    BuildVersion = DefaultBuildVersion,
                    LastCheckedUtc = DateTime.UtcNow,
                    Source = "BuiltInFallback"
                };

                SaveCache(fallback, configFile);
                logCallback?.Invoke(string.Format("[VersionResolver] Falling back to default baseline: {0}", fallback));
                _cachedResolved = fallback;
                return fallback;
            }
        }

        public static Task<DofusVersionInfo> ResolveVersionAsync(string customDir = null, bool forceRefresh = false, Action<string> logCallback = null)
        {
            return Task.Factory.StartNew(delegate
            {
                return ResolveVersion(customDir, forceRefresh, logCallback);
            });
        }
    }
}
