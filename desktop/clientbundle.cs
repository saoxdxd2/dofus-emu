// clientbundle.cs - updating the client without breaking it.
//
// The client bundle is not shipped in the APK: Ankama serves manifest.json and
// the app downloads script.js at runtime. The old updater did that correctly
// (download beside the target, swap on success), and still broke the game after
// every update, for a reason that had nothing to do with the download:
//
//   **script.js is patched at BUILD time.** tools/patch-engine.js rewrites the
//   bundle (touch input, map-transition watchdog, Adjust SDK removal, WebGL
//   fingerprint). The runtime updater replaced script.js with the server's raw
//   copy, so every patch vanished the first time the server published a new
//   build -- login failed, or the page came up black, and it looked like "the
//   update broke it again" with no error pointing at the real cause.
//
// So the updater is now a small transaction:
//
//   download to staging  ->  apply the same rules  ->  validate  ->  commit
//
// Rules live in tools\patch-rules.txt, generated from patch-engine.js by
// tools/make-patch-rules.py, so there is exactly one description of what a
// patch is. If the staged bundle cannot be patched or does not validate, it is
// discarded and the working bundle stays. Nothing is deleted before its
// replacement exists, and script.js.bak always holds the unpatched original so
// a rebuild can start from a clean file.
//
// Kept C# 5 compatible.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using DofusFarm.Update;

namespace DofusFarm.Engine
{
    public static class ClientBundle
    {
        /// <summary>
        /// WebClient with a timeout and a User-Agent that survives.
        ///
        /// The stock WebClient has no timeout, so a dead proxy made the manifest
        /// fetch block for ~100 s and then log "The operation has timed out".
        ///
        /// The Agent property exists because of something measured, not guessed:
        /// `Headers["User-Agent"]` is applied to the FIRST request and then the
        /// collection is emptied -- a second request from the same instance carries
        /// no User-Agent at all (proved by printing the two built requests). The
        /// login CDN answers 403 (server=awselb/2.0) to a request without the mobile
        /// UA and 200 to the same URL with it, so one client per update run could
        /// read the manifest and then never download a single file. Setting the UA
        /// on every request built here is the only form that cannot be lost.
        /// </summary>
        private sealed class TimedWebClient : WebClient
        {
            public int TimeoutMs = 15000;
            public string Agent = null;

            protected override WebRequest GetWebRequest(Uri address)
            {
                WebRequest request = base.GetWebRequest(address);
                if (request != null)
                {
                    request.Timeout = TimeoutMs;
                    var http = request as HttpWebRequest;
                    if (http != null)
                    {
                        http.ReadWriteTimeout = TimeoutMs;
                        http.KeepAlive = false;
                        if (!string.IsNullOrEmpty(Agent)) http.UserAgent = Agent;
                    }
                }
                return request;
            }
        }

        public sealed class Rule
        {
            public string Id;
            public string Kind;         // "replace" | "boundary"
            public bool Core;
            public string Old;
            public string New;
            public string Start;
            public string End;
            public string Replacement;
            public int MaxLen;
        }

        /// <summary>Below this, a script.js is truncated or an error page.</summary>
        public const long MinScriptBytes = 1024 * 1024;

        public static string BundleDir()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string p = Path.Combine(baseDir, @"desktop\client\www");
            if (Directory.Exists(p)) return p;
            p = Path.Combine(baseDir, @"client\www");
            return Directory.Exists(p) ? p : null;
        }

        private static string RulesFile()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string p = Path.Combine(baseDir, @"tools\patch-rules.txt");
            if (File.Exists(p)) return p;
            p = Path.Combine(baseDir, @"..\tools\patch-rules.txt");
            return File.Exists(p) ? p : null;
        }

        public static List<Rule> LoadRules(out string error)
        {
            error = null;
            var rules = new List<Rule>();
            string path = RulesFile();
            if (path == null) { error = "tools\\patch-rules.txt not found"; return rules; }

            Rule cur = null;
            try
            {
                foreach (string raw in File.ReadAllLines(path))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    if (line.StartsWith("[") && line.EndsWith("]"))
                    {
                        cur = new Rule { Id = line.Substring(1, line.Length - 2) };
                        rules.Add(cur);
                        continue;
                    }
                    if (cur == null) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq);
                    string val = line.Substring(eq + 1);
                    switch (key)
                    {
                        case "kind": cur.Kind = val; break;
                        case "core": cur.Core = val == "1"; break;
                        case "old": cur.Old = FromB64(val); break;
                        case "new": cur.New = FromB64(val); break;
                        case "start": cur.Start = FromB64(val); break;
                        case "end": cur.End = FromB64(val); break;
                        case "replacement": cur.Replacement = FromB64(val); break;
                        case "maxlen": int.TryParse(val, out cur.MaxLen); break;
                    }
                }
            }
            catch (Exception ex) { error = ex.Message; }
            return rules;
        }

        private static string FromB64(string s)
        {
            try { return Encoding.UTF8.GetString(Convert.FromBase64String(s)); }
            catch { return ""; }
        }

        /// <summary>Apply every rule, reporting which matched and which did not.</summary>
        public static string ApplyPatches(string js, List<Rule> rules,
                                          out List<string> matched, out List<string> skipped)
        {
            matched = new List<string>();
            skipped = new List<string>();
            foreach (Rule r in rules)
            {
                if (r.Kind == "boundary")
                {
                    if (BootState.SkipSelfEmbedded(
                            BootState.RuleIsSelfEmbedding(r.Start, r.Replacement),
                            js.IndexOf(r.Replacement, StringComparison.Ordinal) >= 0))
                    { skipped.Add(r.Id + ": already applied"); continue; }
                    int start = js.IndexOf(r.Start, StringComparison.Ordinal);
                    if (start < 0) { skipped.Add(r.Id + ": start marker missing"); continue; }
                    int end = js.IndexOf(r.End, start, StringComparison.Ordinal);
                    if (end < 0) { skipped.Add(r.Id + ": end marker missing"); continue; }
                    end += r.End.Length;
                    int span = end - start;
                    if (r.MaxLen > 0 && span > r.MaxLen)
                    {
                        skipped.Add(r.Id + ": span " + span + " chars, refused");
                        continue;
                    }
                    js = js.Substring(0, start) + r.Replacement + js.Substring(end);
                    matched.Add(r.Id);
                }
                else
                {
                    if (BootState.SkipSelfEmbedded(
                            BootState.RuleIsSelfEmbedding(r.Old, r.New),
                            js.IndexOf(r.New, StringComparison.Ordinal) >= 0))
                    { skipped.Add(r.Id + ": already applied"); continue; }
                    int at = js.IndexOf(r.Old, StringComparison.Ordinal);
                    if (at < 0) { skipped.Add(r.Id + ": needle missing"); continue; }
                    js = js.Substring(0, at) + r.New + js.Substring(at + r.Old.Length);
                    matched.Add(r.Id);
                }
            }
            return js;
        }

        /// <summary>A bundle is usable if the files the page loads are present and
        /// look like code, not like an HTML error page saved under a .js name.</summary>
        public static bool Validate(string dir, out string why) { return Validate(dir, true, out why); }

        /// <summary>
        /// `requirePage` is false for a staging tree: a manifest update carries only
        /// the build/* files, so index.html is not there -- and demanding it made
        /// every download end in "staged bundle rejected (index.html missing)", so
        /// the updater could never adopt anything. The live tree is checked with the
        /// page after the swap, which is where that requirement belongs.
        /// </summary>
        public static bool Validate(string dir, bool requirePage, out string why)
        {
            why = null;
            string script = Path.Combine(dir, @"build\script.js");
            if (!File.Exists(script)) { why = "build/script.js missing"; return false; }
            long len = new FileInfo(script).Length;
            if (len < MinScriptBytes) { why = "build/script.js is only " + len + " bytes"; return false; }

            string head;
            using (var fs = new FileStream(script, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var sr = new StreamReader(fs, Encoding.UTF8, true, 4096))
            {
                char[] buf = new char[512];
                int n = sr.Read(buf, 0, buf.Length);
                head = new string(buf, 0, Math.Max(0, n));
            }
            if (head.TrimStart().StartsWith("<")) { why = "build/script.js starts with HTML"; return false; }

            string css = Path.Combine(dir, @"build\styles-native.css");
            if (File.Exists(css) && new FileInfo(css).Length < 200) { why = "styles-native.css is empty"; return false; }

            if (requirePage && !File.Exists(Path.Combine(dir, "index.html"))) { why = "index.html missing"; return false; }
            return true;
        }

        /// <summary>One retry, because a 403 from the CDN was transient here.</summary>
        private static string DownloadString(WebClient wc, string url)
        {
            Exception last = null;
            for (int attempt = 1; attempt <= 2; attempt++)
            {
                try { return wc.DownloadString(url); }
                catch (Exception ex)
                {
                    last = ex;
                    if (attempt == 1) System.Threading.Thread.Sleep(400);
                }
            }
            throw last;
        }

        private static void DownloadFile(WebClient wc, string url, string dest)
        {
            Exception last = null;
            for (int attempt = 1; attempt <= 2; attempt++)
            {
                try { wc.DownloadFile(url, dest); return; }
                catch (Exception ex)
                {
                    last = ex;
                    try { if (File.Exists(dest)) File.Delete(dest); } catch { }
                    if (attempt == 1) System.Threading.Thread.Sleep(400);
                }
            }
            throw last;
        }

        /// <summary>Restore the last known-good bundle if the live one is unusable.</summary>
        public static bool SelfHeal(Action<string> log)
        {
            string dir = BundleDir();
            if (dir == null) return false;

            string why;
            if (Validate(dir, out why)) return true;

            log?.Invoke("[UPDATER] bundle is not usable (" + why + ") - trying the backup");
            string script = Path.Combine(dir, @"build\script.js");
            string bak = script + ".bak";
            if (File.Exists(bak) && new FileInfo(bak).Length >= MinScriptBytes)
            {
                try
                {
                    File.Copy(bak, script, true);
                    log?.Invoke("[UPDATER] restored build/script.js from script.js.bak");
                    return Validate(dir, out why);
                }
                catch (Exception ex) { log?.Invoke("[UPDATER] restore failed: " + ex.Message); }
            }

            // Nothing to restore from: a rebuild is the only way back, and saying
            // so is more useful than a black screen with no explanation.
            log?.Invoke("[UPDATER] no usable backup. Reinstall (or rebuild) to restore the client bundle.");
            return false;
        }

        /// <summary>
        /// Point the page's asset URLs at a stamp derived from the bundle itself.
        ///
        /// standalone.html ships with `?v=3.14.2_mapfix10` on every script and
        /// stylesheet. That string never changes, so after an update Chromium keeps
        /// serving the *cached* old bundle and the update is invisible -- the page
        /// looks broken in a way no log explains. Re-stamping on every launch makes
        /// the cache key follow the file.
        /// </summary>
        private static string Sha10(string path)
        {
            using (var sha = System.Security.Cryptography.SHA1.Create())
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                return BitConverter.ToString(sha.ComputeHash(fs)).Replace("-", "").Substring(0, 10).ToLowerInvariant();
        }

        private static void RewriteCacheBusters(string dir, Action<string> log)
        {
            try
            {
                string html = Path.Combine(dir, "standalone.html");
                string script = Path.Combine(dir, @"build\script.js");
                if (!File.Exists(html) || !File.Exists(script)) return;

                string text = File.ReadAllText(html);
                // One stamp per FILE, not one stamp for the page. The old code hashed build/script.js
                // and pasted that onto every reference, so editing a shim left its URL byte-identical
                // and Chromium kept serving the cached copy: three relaunches on 2026-11-04 tested
                // code that was not running, and the log printed a report string the file on disk no
                // longer contained. A cache buster that does not follow the file is a constant.
                int changed = 0;
                string stamped = Regex.Replace(text,
                    "(src|href)=\"([^\"?#]+\\.(?:js|css))(?:\\?v=[0-9A-Za-z_.\\-]*)?\"",
                    (Match m) =>
                    {
                        string rel = m.Groups[2].Value;
                        if (rel.StartsWith("http", StringComparison.OrdinalIgnoreCase)
                            || rel.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) return m.Value;
                        string abs = Path.Combine(dir, rel.Replace('/', Path.DirectorySeparatorChar));
                        if (!File.Exists(abs)) return m.Value;
                        string stamp;
                        try { stamp = Sha10(abs); } catch { return m.Value; }
                        string want = string.Format("{0}=\"{1}?v={2}\"", m.Groups[1].Value, rel, stamp);
                        if (want == m.Value) return m.Value;
                        changed++;
                        return want;
                    });
                if (changed > 0)
                {
                    File.WriteAllText(html, stamped, new UTF8Encoding(false));
                    log?.Invoke(string.Format(
                        "[UPDATER] asset URLs re-stamped per file: {0} reference(s), bundle {1}",
                        changed, Sha10(script)));
                }
            }
            catch (Exception ex)
            {
                log?.Invoke("[UPDATER] could not re-stamp asset URLs: " + ex.Message);
            }
        }

        /// <summary>
        /// Bring the local bundle up to date, patch it, and only then adopt it.
        /// Returns the number of files adopted (0 = nothing changed or refused).
        ///
        /// Serialized: "Launch All" starts every instance at once and each one calls
        /// this from its own pool thread. Four concurrent transactions over the same
        /// script.js means four staging trees, four copies onto the same live file
        /// and a page that can load a half-written bundle. The first caller does the
        /// work; the rest read the stamp file it wrote and return 0.
        /// </summary>
        // ------------------------------------------------------------------ boot canary
        //
        // A patched bundle is a hypothesis about the next Ankama build, and the only instrument
        // that can say whether it is true is the running client. `--bundle-selftest` compares text
        // the rules produced against text the rules produced; on 2026-11-04 it printed IDENTICAL
        // for build 3.3.11 while that bundle black-screened. So the page now answers "did it
        // boot", `boot-pending.json` remembers the bundle that has not answered yet, and
        // `bad-builds.txt` lists the stamps the client said it did NOT boot with. Adopting those
        // continues; patching them stops. The vendor's bytes are degraded rather than blank, which
        // is the better of two bad options -- and it is what the manual
        // `client-patches.quarantine` flag does for a whole rule set, reached here per build.
        private static string FlagPath(string name)
        {
            return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, name);
        }

        private static string ReadFlag(string name)
        {
            try
            {
                string p = FlagPath(name);
                return File.Exists(p) ? File.ReadAllText(p) : "";
            }
            catch { return ""; }
        }

        /// <summary>The never-patch-again list, newline separated.</summary>
        public static string BadBuilds() { return ReadFlag("bad-builds.txt"); }

        /// <summary>
        /// The version token the manifest gave `build/script.js` -- the bundle's identity.
        ///
        /// Both sides of the canary read it from here: the updater when it arms the pending note,
        /// the host when it tells the page which bundle it is running. A second way of naming the
        /// build would be a second way for the answer to never match the question.
        /// </summary>
        public static string BundleStamp()
        {
            try
            {
                string dir = BundleDir();
                if (dir == null) return "";
                string p = Path.Combine(dir, "client_version.json");
                if (!File.Exists(p)) return "";
                foreach (Match m in Regex.Matches(File.ReadAllText(p),
                             "\"([^\"]+)\"\\s*:\\s*\"([^\"]*)\""))
                    if (m.Groups[1].Value == "build/script.js") return m.Groups[2].Value;
                return "";
            }
            catch { return ""; }
        }

        /// <summary>
        /// The build number the served bundle will actually report to the login server.
        ///
        /// `build/script.js` hardcodes `window.buildVersion="1.74.12"` and assigns it
        /// unconditionally at module load, so it wins over anything the host sets
        /// beforehand -- including the `buildVersion=` query parameter the host puts on
        /// the URL, which the bundle never reads. Reading the number from the resolver
        /// instead therefore answered a different question than the one the server asks:
        /// the resolver's build version is only ever its compiled-in default (Google Play
        /// publishes an app version, not a build), so it drifts stale the moment Ankama
        /// ships a bundle.
        ///
        /// That drift is not cosmetic. A stale number is rejected at login with
        /// INCOMPATIBLE_BUILD_VERSION, which the client renders as "A new version is
        /// available!" -- an error that names neither the bundle nor the number, so the
        /// only visible symptom is a login that never happens. Taking the value from the
        /// bytes being served means the host and the page cannot disagree, and a stale
        /// bundle is visible in the log before the login is attempted.
        /// </summary>
        public static string ServedBuildVersion()
        {
            try
            {
                string dir = BundleDir();
                if (dir == null) return "";
                string p = Path.Combine(Path.Combine(dir, "build"), "script.js");
                if (!File.Exists(p)) return "";
                // The assignment sits near the top of the bundle; 64 KB is plenty and
                // keeps this off the hot path for a 5 MB file.
                string head;
                using (var fs = new FileStream(p, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var sr = new StreamReader(fs, Encoding.UTF8))
                {
                    var buf = new char[64 * 1024];
                    int n = sr.Read(buf, 0, buf.Length);
                    head = new string(buf, 0, n < 0 ? 0 : n);
                }
                Match m = Regex.Match(head, "window\\.buildVersion\\s*=\\s*\"([^\"]+)\"");
                return m.Success ? m.Groups[1].Value : "";
            }
            catch { return ""; }
        }

        /// <summary>The stamp whose patched bundle has not reported a boot, or "".</summary>
        private static string PendingStamp()
        {
            try
            {
                string p = FlagPath("boot-pending.json");
                if (!File.Exists(p)) return "";
                Match m = Regex.Match(File.ReadAllText(p), "\"stamp\"\\s*:\\s*\"([^\"]*)\"");
                return m.Success ? m.Groups[1].Value : "";
            }
            catch { return ""; }
        }

        private static void ArmBootCanary(string stamp, Action<string> log)
        {
            if (string.IsNullOrEmpty(stamp)) return;
            try
            {
                File.WriteAllText(FlagPath("boot-pending.json"), string.Format(
                    "{{\n  \"stamp\": \"{0}\",\n  \"at\": \"{1}\"\n}}\n",
                    stamp, DateTime.UtcNow.ToString("s")));
                log?.Invoke("[UPDATER] patched " + stamp + " is armed for a boot answer");
            }
            catch (Exception ex)
            {
                log?.Invoke("[UPDATER] could not arm the boot canary: " + ex.Message);
            }
        }

        /// <summary>
        /// Fold the page's boot answer into the pending record. Called from the WebView2 handler.
        ///
        /// The revert is a copy, not a re-download: `script.js.bak` holds exactly the bytes Ankama
        /// served for this stamp (written before the rules touched them), so coming back works with
        /// the network down and cannot fetch a different build underneath us.
        /// </summary>
        public static void RecordBootCanary(bool booted, string stamp, string reason, Action<string> log)
        {
            string pending = PendingStamp();
            string suspect = ReadFlag("boot-suspect.txt");
            string action = BootState.OnCanary(pending, stamp, booted, BootState.IsBad(suspect, pending));
            if (action == BootState.Ignore) return;
            try
            {
                if (action == BootState.Attest)
                {
                    try { File.Delete(FlagPath("boot-pending.json")); } catch { }
                    // A healthy boot answers the whole question, so it clears the watch as well:
                    // a build that came up after one bad day on the CDN is not sitting one failure
                    // away from being blacklisted.
                    try { File.WriteAllText(FlagPath("boot-suspect.txt"), BootState.RemoveBad(suspect, pending)); } catch { }
                    log?.Invoke("[UPDATER] boot attested for " + pending + "; the patch survives");
                    return;
                }
                bool blacklist = action == BootState.Blacklist;
                if (blacklist)
                    File.WriteAllText(FlagPath("bad-builds.txt"), BootState.AddBad(BadBuilds(), pending));
                else
                    File.WriteAllText(FlagPath("boot-suspect.txt"), BootState.AddBad(suspect, pending));
                string dir = BundleDir();
                string live = dir == null ? null : Path.Combine(dir, @"build\script.js");
                string vendor = dir == null ? null : Path.Combine(dir, @"build\script.js.bak");
                bool reverted = false;
                if (live != null && File.Exists(live) && File.Exists(vendor))
                {
                    try { File.Copy(vendor, live, true); reverted = true; }
                    catch (Exception ex) { log?.Invoke("[UPDATER] revert failed: " + ex.Message); }
                }
                try { File.Delete(FlagPath("boot-pending.json")); } catch { }
                string why = string.IsNullOrEmpty(reason) ? "no reason given"
                    : reason.Substring(0, Math.Min(reason.Length, 90));
                log?.Invoke(string.Format("[UPDATER] build {0} reported no boot ({1}): {2}{3}",
                    pending, why,
                    reverted ? "reverted to Ankama's bytes" : "NOT reverted, no vendor copy on disk",
                    blacklist ? ", and it joins the never-patch list"
                              : "; it is being watched -- a second failure stops patching this build"));
            }
            catch (Exception ex)
            {
                log?.Invoke("[UPDATER] boot canary dropped: " + ex.Message);
            }
        }

        private static readonly object _gate = new object();

        public static int EnsureUpToDate(int proxyPort, Action<string> log)
        {
            lock (_gate) { return UpdateLocked(proxyPort, log); }
        }

        private static int UpdateLocked(int proxyPort, Action<string> log)
        {
            try
            {
                string dir = BundleDir();
                if (dir == null) return 0;

                // Neither of these is an update: one repairs a broken bundle, the
                // other keeps the page pointing at whatever is on disk. Both have to
                // run even when auto-update is switched off.
                SelfHeal(log);
                RewriteCacheBusters(dir, log);

                if (File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "no-client-update.flag")))
                {
                    log?.Invoke("[UPDATER] disabled by no-client-update.flag; keeping the local bundle");
                    return 0;
                }

                string stampPath = Path.Combine(dir, "client_version.json");
                var known = new Dictionary<string, string>();
                if (File.Exists(stampPath))
                {
                    foreach (Match m in Regex.Matches(File.ReadAllText(stampPath),
                                 "\"([^\"]+)\"\\s*:\\s*\"([^\"]*)\""))
                        known[m.Groups[1].Value] = m.Groups[2].Value;
                }

                string error;
                List<Rule> rules = LoadRules(out error);
                if (rules.Count == 0)
                    log?.Invoke("[UPDATER] warning: no patch rules (" + error + "); updates will be adopted unpatched");

                using (var wc = new TimedWebClient())
                {
                    if (proxyPort > 0) wc.Proxy = new WebProxy("http://127.0.0.1:" + proxyPort);
                    wc.Agent = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36";
                    wc.Encoding = Encoding.UTF8;

                    string server = "https://" + NetworkSettings.For().LoginHost;
                    string manifest = DownloadString(wc, server + "/manifest.json");
                    var files = new Dictionary<string, string>();
                    foreach (Match m in Regex.Matches(manifest,
                                 "\"filename\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"version\"\\s*:\\s*\"([^\"]+)\""))
                        files[m.Groups[1].Value] = m.Groups[2].Value;
                    if (files.Count == 0)
                    {
                        log?.Invoke("[UPDATER] manifest has no files; keeping the local bundle");
                        return 0;
                    }

                    var todo = new List<string>();
                    foreach (var pair in files)
                    {
                        string local = Path.Combine(dir, pair.Key.Replace('/', Path.DirectorySeparatorChar));
                        string have;
                        bool upToDate = known.TryGetValue(pair.Key, out have) && have == pair.Value && File.Exists(local);
                        if (!upToDate) todo.Add(pair.Key);
                    }
                    if (todo.Count == 0) return 0;

                    string staging = Path.Combine(dir, "staging-" + DateTime.UtcNow.Ticks);
                    Directory.CreateDirectory(staging);
                    var staged = new List<string>();
                    try
                    {
                        foreach (string rel in todo)
                        {
                            string target = Path.Combine(staging, rel.Replace('/', Path.DirectorySeparatorChar));
                            Directory.CreateDirectory(Path.GetDirectoryName(target));
                            try
                            {
                                DownloadFile(wc, server + "/" + rel, target);
                                if (new FileInfo(target).Length == 0)
                                    throw new IOException("empty response");
                                staged.Add(rel);
                            }
                            catch (Exception ex)
                            {
                                log?.Invoke("[UPDATER] " + rel + " failed: " + ex.Message);
                                try { if (File.Exists(target)) File.Delete(target); } catch { }
                            }
                        }
                        if (staged.Count == 0)
                        {
                            log?.Invoke("[UPDATER] nothing downloaded; keeping the local bundle");
                            return 0;
                        }

                        // Patch the staged script BEFORE it can become live. This is
                        // the whole point: an unpatched script.js is what used to
                        // break the game after every update.
                        string stagedScript = Path.Combine(staging, @"build\script.js");
                        int coreTotal = 0, coreHit = 0, skippedTotal = -1;
                        string skippedNames = "";
                        bool patching = false;
                        if (File.Exists(stagedScript))
                        {
                            // The unpatched copy is what a future build must patch from -- and the
                            // bytes a failed boot is reverted to.
                            try { File.Copy(stagedScript, Path.Combine(dir, @"build\script.js.bak"), true); } catch { }

                            string js = File.ReadAllText(stagedScript, Encoding.UTF8);
                            List<string> matched, skipped;
                            string patched = ApplyPatches(js, rules, out matched, out skipped);

                            foreach (Rule r in rules) if (r.Core) coreTotal++;
                            foreach (string id in matched)
                                foreach (Rule r in rules) if (r.Core && r.Id == id) coreHit++;
                            skippedTotal = skipped.Count;
                            skippedNames = string.Join(", ", skipped);

                            // Rules matching is not evidence the patch is right -- that question is
                            // answered by the client, later. But it IS evidence about whether a
                            // build we already watched fail should be tried again: it should not.
                            string bad = BadBuilds();
                            string stamp = files.ContainsKey("build/script.js") ? files["build/script.js"] : "";
                            patching = BootState.DecideAdoption(bad, stamp, matched.Count) == BootState.Patch;
                            if (patching)
                            {
                                File.WriteAllText(stagedScript, patched, new UTF8Encoding(false));
                                log?.Invoke(string.Format("[UPDATER] patched staged bundle: {0}/{1} rules, {2}/{3} core",
                                    matched.Count, rules.Count, coreHit, coreTotal));
                                foreach (string s in skipped) log?.Invoke("[UPDATER]   skipped rule " + s);
                            }
                            else
                            {
                                // The staged file keeps Ankama's own bytes; the patched text is
                                // dropped on the floor. 23/23 rules matching is what produced the
                                // 3.3.11 black screen, so "everything applied" is not a reason to
                                // ship it once a client has said it did not boot.
                                log?.Invoke("[UPDATER] " + stamp + " is NOT being patched ("
                                    + (BootState.IsBad(bad, stamp)
                                       ? "a client reported it never booted with these patches"
                                       : "no rule matched this build")
                                    + "); adopting Ankama's bytes for it");
                            }
                        }

                        // Validate the staged files, not the installed tree: the
                        // manifest never ships index.html.
                        string stagedWhy;
                        if (!Validate(staging, false, out stagedWhy))
                        {
                            log?.Invoke("[UPDATER] staged bundle rejected (" + stagedWhy + "); keeping the working one");
                            return 0;
                        }

                        // Fail open, the way an application-compat layer does. Before this, only
                        // "no core patch matched" refused (coreTotal>0 && coreHit==0), and exactly
                        // ONE of the 23 rules is core -- so a bundle that silently lost a dozen
                        // anchors was adopted and ran half-patched. A half-patched loader is the
                        // black screen, and pristine is the vendor's own behaviour: degraded, not
                        // blank. Consequence, stated plainly: a rule that has genuinely rotted must
                        // be pruned from patch-rules.txt or updates stop being adopted, because the
                        // skipped names now block adoption. Matching is necessary but not sufficient
                        // either -- build 3.3.11 took 23/23 rules and the patched series callback
                        // still double-fired -- so js/compat-shim.js binds by name at runtime and
                        // counts what actually fired.
                        if (patching
                            && (skippedTotal > 0 || (coreTotal > 0 && coreHit < coreTotal)))
                        {
                            log?.Invoke("[UPDATER] staged bundle refused: " + skippedTotal
                                + " rule(s) did not match (" + skippedNames + "), core "
                                + coreHit + "/" + coreTotal + "; keeping the working bundle");
                            return 0;
                        }

                        int adopted = 0;
                        foreach (string rel in staged)
                        {
                            string src = Path.Combine(staging, rel.Replace('/', Path.DirectorySeparatorChar));
                            string dst = Path.Combine(dir, rel.Replace('/', Path.DirectorySeparatorChar));
                            try
                            {
                                Directory.CreateDirectory(Path.GetDirectoryName(dst));
                                // Old copy to .prev, new copy in, then drop .prev.
                                // Never delete the live file before its replacement
                                // is on disk -- that is how a 403 used to leave the
                                // page with no bundle at all.
                                string prev = dst + ".prev";
                                if (File.Exists(dst))
                                {
                                    try { File.Copy(dst, prev, true); } catch { }
                                }
                                File.Copy(src, dst, true);
                                try { if (File.Exists(prev)) File.Delete(prev); } catch { }
                                adopted++;
                            }
                            catch (Exception ex)
                            {
                                log?.Invoke("[UPDATER] could not adopt " + rel + ": " + ex.Message);
                                // Put the previous copy back so the tree stays coherent.
                                string prev = dst + ".prev";
                                try { if (File.Exists(prev)) File.Copy(prev, dst, true); } catch { }
                            }
                        }

                        if (adopted > 0)
                        {
                            var stamp = new StringBuilder("{\n");
                            int i = 0;
                            foreach (var pair in files)
                            {
                                stamp.AppendFormat("  \"{0}\": \"{1}\"{2}\n", pair.Key, pair.Value,
                                    ++i < files.Count ? "," : "");
                            }
                            stamp.Append("}\n");
                            File.WriteAllText(stampPath, stamp.ToString());

                            string why;
                            if (!Validate(dir, out why))
                                log?.Invoke("[UPDATER] WARNING: live bundle failed validation after update: " + why);
                            else
                                log?.Invoke(string.Format(patching
                                    ? "[UPDATER] {0} file(s) updated and patched ({1}/{2} core rules)"
                                    : "[UPDATER] {0} file(s) updated unpatched ({1}/{2} core rules)",
                                    adopted, coreHit, coreTotal));

                            // The stamp written at the top of this run describes the bundle
                            // that is now replaced. Without re-stamping, the page keeps
                            // asking for the old ?v= and Chromium serves the cached old
                            // build -- an update that installs and stays invisible.
                            RewriteCacheBusters(dir, log);

                            // A patched bundle is a hypothesis. Until a client says it booted,
                            // this is the bundle being judged -- and if the answer never comes,
                            // the note stays and says which build nobody got a verdict on.
                            if (patching) ArmBootCanary(BundleStamp(), log);
                        }
                        return adopted;
                    }
                    finally
                    {
                        try { if (Directory.Exists(staging)) Directory.Delete(staging, true); } catch { }
                    }
                }
            }
            catch (Exception ex)
            {
                log?.Invoke("[UPDATER] skipped: " + ex.Message);
                return 0;
            }
        }
    }
}
