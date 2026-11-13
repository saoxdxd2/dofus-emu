using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace DofusFarm.Engine
{
    /// <summary>
    /// Everything one account owns across launches: a profile directory, a
    /// device identity, fingerprint seeds and one sticky egress.
    ///
    /// The key is the account login, not the slot name. accounts.txt is
    /// positional, so the same slot can be handed a different login after an
    /// edit or a reset; keying the browser profile by slot is what let two
    /// logins share cookies, storage and a device serial. Hashing the normalized
    /// login makes the profile follow the account instead.
    /// </summary>
    public sealed class AccountCapsule
    {
        public string Login;
        public string AccountId;
        public string ProfileDir;
        public DeviceProfile Profile;
        public string DeviceSerial;
        public string DeviceUuid;
        public uint FingerprintSeed;
        public uint WebGlSeed;
        public uint CanvasSeed;
        public string StickyProxy;
        public bool ProxyWasSticky;
        public bool CreatedProfile;
    }

    public static class AccountIsolation
    {
        public const string MarkerFile = "account.isolation";
        public const string IdentityFile = "account.identity";
        public const string ProxyAffinityFile = "proxy.affinity";

        private static readonly object _gate = new object();

        public static string NormalizeLogin(string login)
        {
            if (string.IsNullOrEmpty(login)) return null;
            return login.Trim().ToLowerInvariant();
        }

        /// <summary>Stable 16-hex account id: SHA-256 of the normalized login.</summary>
        public static string AccountIdFor(string login)
        {
            string norm = NormalizeLogin(login);
            if (norm == null) return null;
            using (SHA256 sha = SHA256.Create())
            {
                byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(norm));
                StringBuilder sb = new StringBuilder(16);
                for (int i = 0; i < 8; i++) sb.Append(hash[i].ToString("x2"));
                return sb.ToString();
            }
        }

        public static string ProfileRoot
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DofusFarm", "webview2_profiles");
            }
        }

        public static string ProfileDirFor(string login)
        {
            string id = AccountIdFor(login);
            return id == null ? null : Path.Combine(ProfileRoot, "acct_" + id);
        }

        /// <summary>Deterministic FNV-1a seed over account id plus a purpose salt.</summary>
        public static uint SeedFor(string accountId, string salt)
        {
            uint h = 2166136261U;
            string s = (accountId ?? "") + "|" + (salt ?? "");
            for (int i = 0; i < s.Length; i++)
            {
                h ^= s[i];
                h *= 16777619U;
            }
            return h;
        }

        /// <summary>
        /// Resolve (and, on first use, create) the account capsule.
        ///
        /// Fail-closed: a marker naming another login, an account already live in
        /// another instance, or an attempt to move a pinned account to a
        /// different egress returns null with a reason instead of falling back to
        /// a shared profile or a fresh IP.
        /// </summary>
        public static AccountCapsule Resolve(string login, string instanceId, DeviceProfile requestedProfile,
            string requestedProxy, out string failure)
        {
            failure = null;
            string norm = NormalizeLogin(login);
            if (norm == null) { failure = "account login is empty"; return null; }
            string id = AccountIdFor(norm);
            string dir = ProfileDirFor(norm);
            if (string.IsNullOrEmpty(dir)) { failure = "account profile path is empty"; return null; }

            lock (_gate)
            {
                bool existed = Directory.Exists(dir);
                try { Directory.CreateDirectory(dir); }
                catch (Exception ex)
                {
                    failure = "cannot create account profile " + dir + ": " + ex.Message;
                    return null;
                }

                // The profile belongs to exactly one login. A different marker is
                // either a hash collision or two logins pointed at one profile;
                // both are isolation failures, not something to overwrite.
                string identityPath = Path.Combine(dir, IdentityFile);
                string existingLogin = ReadTrimmed(identityPath);
                if (!string.IsNullOrEmpty(existingLogin) &&
                    !string.Equals(NormalizeLogin(existingLogin), norm, StringComparison.Ordinal))
                {
                    failure = string.Format("profile {0} already belongs to account {1}", dir, existingLogin);
                    return null;
                }

                string markerPath = Path.Combine(dir, MarkerFile);
                string existingMarker = ReadTrimmed(markerPath);
                if (!string.IsNullOrEmpty(existingMarker))
                {
                    string markerId = MarkerValue(existingMarker, "account");
                    if (!string.IsNullOrEmpty(markerId) &&
                        !string.Equals(markerId, id, StringComparison.OrdinalIgnoreCase))
                    {
                        failure = string.Format("profile {0} is bound to account id {1}, not {2}", dir, markerId, id);
                        return null;
                    }
                }

                // Two live clients on one login is the shared-account signal the
                // rest of the guard exists to prevent. Check it before the
                // profile is touched, not after the page registers.
                string conflict = FarmEngine.AccountActiveElsewhere(norm, instanceId);
                if (!string.IsNullOrEmpty(conflict))
                {
                    failure = string.Format("account {0} is already live on '{1}'", norm, conflict);
                    return null;
                }

                // The device is derived, not random: the same login presents the
                // same phone on every launch and every host, while a different
                // login gets a different one without a persisted id file. An
                // explicitly pinned profile still keeps the derived serial/uuid,
                // because those are the values the server correlates.
                List<DeviceProfile> all = DeviceCatalog.GetAllProfiles();
                int profileIndex = (int)(SeedFor(id, "device") % (uint)Math.Max(1, all.Count));
                DeviceProfile profile = all.Count > 0 ? all[profileIndex] : requestedProfile;
                string prefix = (profile != null && !string.IsNullOrEmpty(profile.SerialPrefix))
                    ? profile.SerialPrefix : "R58M";
                uint serialSeed = SeedFor(id, "serial");
                string serial = prefix + ((serialSeed % 90000000U) + 10000000U).ToString();
                string uuid = SeedFor(id, "uuid").ToString("x8") + SeedFor(id, "uuid2").ToString("x8");
                uint fpSeed = SeedFor(id, "fingerprint");
                uint glSeed = SeedFor(id, "webgl");
                uint canvasSeed = SeedFor(id, "canvas");

                // Sticky egress. The first real upstream this account launches
                // with is recorded; later launches reuse it even when the farm's
                // network mode now resolves somewhere else. An explicit different
                // proxy is refused rather than silently moving the account.
                string affinityPath = Path.Combine(dir, ProxyAffinityFile);
                string storedProxy = ReadTrimmed(affinityPath);
                string requested = FarmEngine.NormalizeUpstreamProxy(requestedProxy);
                string stickyProxy = storedProxy;
                bool sticky = !string.IsNullOrEmpty(storedProxy);
                if (sticky)
                {
                    if (!string.IsNullOrEmpty(requested) &&
                        !string.Equals(requested, storedProxy, StringComparison.OrdinalIgnoreCase))
                    {
                        failure = string.Format(
                            "account {0} is pinned to egress {1}; refusing to switch to {2} (delete {3} to rebind)",
                            norm, storedProxy, requested, affinityPath);
                        return null;
                    }
                }
                else if (!string.IsNullOrEmpty(requested))
                {
                    try { File.WriteAllText(affinityPath, requested, Encoding.UTF8); }
                    catch (Exception ex) { failure = "cannot record proxy affinity: " + ex.Message; return null; }
                    stickyProxy = requested;
                    sticky = true;
                }

                AccountCapsule capsule = new AccountCapsule();
                capsule.Login = norm;
                capsule.AccountId = id;
                capsule.ProfileDir = dir;
                capsule.Profile = profile;
                capsule.DeviceSerial = serial;
                capsule.DeviceUuid = uuid;
                capsule.FingerprintSeed = fpSeed;
                capsule.WebGlSeed = glSeed;
                capsule.CanvasSeed = canvasSeed;
                capsule.StickyProxy = stickyProxy;
                capsule.ProxyWasSticky = sticky;
                capsule.CreatedProfile = !existed;

                // Markers are written only after every check passed. The page-side
                // guard already understands account.identity; account.isolation
                // adds the hash and the seeds the host uses.
                try
                {
                    if (string.IsNullOrEmpty(existingLogin))
                        File.WriteAllText(identityPath, norm, Encoding.UTF8);
                    File.WriteAllText(markerPath, string.Format(
                        "account={0};login={1};device={2};serial={3};uuid={4};fp={5:x8};webgl={6:x8};canvas={7:x8}",
                        id, norm, profile != null ? profile.Id : "", serial, uuid, fpSeed, glSeed, canvasSeed),
                        Encoding.UTF8);
                }
                catch (Exception ex)
                {
                    failure = "cannot write account marker: " + ex.Message;
                    return null;
                }

                return capsule;
            }
        }

        /// <summary>Re-read the profile markers and refuse a launch that drifted.</summary>
        public static bool Preflight(AccountCapsule capsule, out string failure)
        {
            failure = null;
            if (capsule == null) { failure = "account capsule is missing"; return false; }
            try
            {
                if (!Directory.Exists(capsule.ProfileDir))
                {
                    failure = "account profile does not exist: " + capsule.ProfileDir;
                    return false;
                }
                string marker = ReadTrimmed(Path.Combine(capsule.ProfileDir, MarkerFile));
                if (string.IsNullOrEmpty(marker))
                {
                    failure = "account profile has no isolation marker";
                    return false;
                }
                string markerId = MarkerValue(marker, "account");
                if (!string.Equals(markerId, capsule.AccountId, StringComparison.OrdinalIgnoreCase))
                {
                    failure = "account marker does not match capsule id";
                    return false;
                }
                string identity = ReadTrimmed(Path.Combine(capsule.ProfileDir, IdentityFile));
                if (!string.IsNullOrEmpty(identity) &&
                    !string.Equals(NormalizeLogin(identity), capsule.Login, StringComparison.Ordinal))
                {
                    failure = "account profile identity mismatch: " + identity;
                    return false;
                }
                string affinity = ReadTrimmed(Path.Combine(capsule.ProfileDir, ProxyAffinityFile));
                if (!string.IsNullOrEmpty(affinity) &&
                    !string.Equals(affinity, capsule.StickyProxy, StringComparison.OrdinalIgnoreCase))
                {
                    failure = "account egress affinity changed after resolve";
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                failure = ex.Message;
                return false;
            }
        }

        /// <summary>Record the egress a capsule resolved to when no affinity existed yet.</summary>
        public static bool BindProxy(AccountCapsule capsule, string upstream, out string failure)
        {
            failure = null;
            if (capsule == null) { failure = "account capsule is missing"; return false; }
            string normalized = FarmEngine.NormalizeUpstreamProxy(upstream);
            if (string.IsNullOrEmpty(normalized)) return true;   // direct stays unbound
            try
            {
                lock (_gate)
                {
                    string path = Path.Combine(capsule.ProfileDir, ProxyAffinityFile);
                    string stored = ReadTrimmed(path);
                    if (!string.IsNullOrEmpty(stored))
                    {
                        if (!string.Equals(stored, normalized, StringComparison.OrdinalIgnoreCase))
                        {
                            failure = string.Format("account {0} is already pinned to {1}", capsule.Login, stored);
                            return false;
                        }
                        capsule.StickyProxy = stored;
                        capsule.ProxyWasSticky = true;
                        return true;
                    }
                    File.WriteAllText(path, normalized, Encoding.UTF8);
                    capsule.StickyProxy = normalized;
                    capsule.ProxyWasSticky = true;
                    return true;
                }
            }
            catch (Exception ex)
            {
                failure = ex.Message;
                return false;
            }
        }

        private static string ReadTrimmed(string path)
        {
            try { return File.Exists(path) ? File.ReadAllText(path).Trim() : null; }
            catch { return null; }
        }

        private static string MarkerValue(string marker, string key)
        {
            if (string.IsNullOrEmpty(marker) || string.IsNullOrEmpty(key)) return null;
            foreach (string part in marker.Split(';'))
            {
                int eq = part.IndexOf('=');
                if (eq <= 0) continue;
                if (string.Equals(part.Substring(0, eq).Trim(), key, StringComparison.OrdinalIgnoreCase))
                    return part.Substring(eq + 1).Trim();
            }
            return null;
        }
    }
}
