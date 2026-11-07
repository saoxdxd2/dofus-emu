// netsettings.cs - one file decides how every instance leaves the machine.
//
// The proxy used to be hard-wired: "if wireproxy.exe exists, start it, point the
// relay at 127.0.0.1:40000, fail closed". That is fine until the tunnel is what
// breaks the machine. Cloudflare's *system* WARP client installs an adapter and
// rewrites the routing table and DNS for the whole PC, so when its tunnel drops
// the user loses their normal WiFi too -- the only recovery is to disable WARP.
//
// So the egress is a setting now, not an assumption:
//
//   wireproxy  userspace WireGuard (bin\warp\wireproxy.exe). It binds a local
//              SOCKS5 port and nothing else: Windows routing, DNS and the WiFi
//              adapter are untouched. This is the default and the safe choice.
//   warp       the system Cloudflare WARP client via warp-cli. It takes over
//              all machine traffic, which is exactly why it can kill WiFi.
//   custom     an explicit upstream (socks5:// or http://), or proxies.txt.
//   direct     no upstream at all. The real IP reaches Ankama. Off by default.
//
// The relay itself (dofus-net-proxy) still fails closed whenever it is given an
// upstream: a dead tunnel refuses traffic rather than quietly dialling direct.
// FailClosed here only decides what happens when the tunnel cannot be *started*.
//
// Kept C# 5 compatible: no string interpolation, no expression-bodied members.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace DofusFarm.Engine
{
    public sealed class NetworkSettings
    {
        public const string ModeAuto = "auto";
        public const string ModeWireproxy = "wireproxy";
        public const string ModeWarp = "warp";
        public const string ModeCustom = "custom";
        public const string ModeDirect = "direct";

        // auto: a supervisor tests every candidate (tunnel, configured upstream,
        // proxies.txt) and keeps the best healthy one, failing over on its own.
        public string Mode = ModeAuto;

        // Local SOCKS5 port the userspace tunnel binds (wireproxy) or that the
        // system client exposes (warp). 40000 is what both default to.
        public int SocksPort = 40000;

        // custom mode only. Empty means "use proxies.txt round-robin".
        public string Upstream = "";

        // First relay port. Instance slot N listens on BasePort + N, so 8880
        // gives 8880/8881/8882...
        public int BasePort = 8880;

        // false = if the tunnel cannot start, run direct instead of refusing.
        // That exposes the real IP, so it stays on unless the user turns it off.
        public bool FailClosed = true;

        /// <summary>
        /// A proxy whose address is the identity the game server sees, reached through the
        /// disposable pool.
        ///
        /// This is the "sticky session" shape: one stable exit, many hops. Public proxies die in
        /// under a minute — measured, eleven alive then none sixty seconds later — so any session
        /// riding one of them directly ends when it does, and the reconnect arrives from a new
        /// address, which the server treats as a hijacked account. With an exit that is a host
        /// we control, a hop dying costs a retry and the address never moves.
        ///
        /// Empty means off. Set it to e.g. socks5://your-vps:1080.
        /// </summary>
        public string ExitProxy = "";

        // Raw extra flags handed to dofus-net-proxy.exe verbatim, e.g.
        //   -dns 1.1.1.1 -prefetch-radius 3 -mods-dir C:\mods
        public string ExtraArgs = "";

        // mode=warp runs the *system* Cloudflare client, and `warp-cli mode proxy` takes
        // over routing and DNS for every program on the machine - not just this one. When
        // that tunnel drops, the user's whole internet drops with it, and their other apps
        // change exit address without being asked. That is outside the box this emulator
        // is supposed to stay inside, so it now needs an explicit yes.
        //
        // Everything else (auto, wireproxy, custom, direct) binds a local port on
        // 127.0.0.1 and leaves Windows routing and DNS untouched.
        public bool AllowSystemWarp = false;

        // Blank = bin\warp\wireproxy.conf. A relative path is resolved against
        // the install folder.
        public string WireproxyConf = "";

        // Blank = <cache>\proxy.log
        public string LogPath = "";

        private static readonly object _gate = new object();
        private static readonly Dictionary<string, NetworkSettings> _cache =
            new Dictionary<string, NetworkSettings>(StringComparer.OrdinalIgnoreCase);

        public static string AppBase
        {
            get
            {
                try { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'); }
                catch { return Environment.CurrentDirectory; }
            }
        }

        public static string PathFor(string baseDir)
        {
            if (string.IsNullOrEmpty(baseDir)) baseDir = AppBase;
            return Path.Combine(baseDir, "network.ini");
        }

        /// <summary>Cached instance for an install folder; the launch path calls
        /// this per instance, so it must not re-read the disk every time.</summary>
        public static NetworkSettings For(string baseDir)
        {
            if (string.IsNullOrEmpty(baseDir)) baseDir = AppBase;
            lock (_gate)
            {
                NetworkSettings s;
                if (_cache.TryGetValue(baseDir, out s) && s != null) return s;
                s = Load(baseDir);
                _cache[baseDir] = s;
                return s;
            }
        }

        /// <summary>Drop the cache so the next launch sees a saved change.</summary>
        public static void Invalidate()
        {
            lock (_gate) { _cache.Clear(); }
        }

        public static NetworkSettings Load(string baseDir)
        {
            var s = new NetworkSettings();
            try
            {
                string path = PathFor(baseDir);
                if (!File.Exists(path)) return s;
                foreach (string raw in File.ReadAllLines(path))
                {
                    string key, val;
                    if (!Ini.TryKeyValue(raw, out key, out val)) continue;
                    switch (key.ToLowerInvariant())
                    {
                        case "mode": s.Mode = NormalizeMode(val); break;
                        case "socks_port": s.SocksPort = ClampInt(val, s.SocksPort, 1, 65535); break;
                        case "upstream": s.Upstream = val; break;
                        case "base_port": s.BasePort = ClampInt(val, s.BasePort, 1024, 65000); break;
                        case "fail_closed": s.FailClosed = ParseBool(val, s.FailClosed); break;
                        case "exit_proxy": s.ExitProxy = (val ?? "").Trim(); break;
                        case "allow_system_warp": s.AllowSystemWarp = ParseBool(val, s.AllowSystemWarp); break;
                        case "extra_args": s.ExtraArgs = val; break;
                        case "wireproxy_conf": s.WireproxyConf = val; break;
                        case "log_path": s.LogPath = val; break;
                    }
                }
            }
            catch { }
            return s;
        }

        public void Save(string baseDir)
        {
            string path = PathFor(baseDir);
            try
            {
                string dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) Directory.CreateDirectory(dir);

                var sb = new StringBuilder();
                sb.AppendLine("# DofusFarm network settings");
                sb.AppendLine("# mode = wireproxy | warp | custom | direct");
                sb.AppendLine("#   wireproxy : userspace WireGuard, local SOCKS5 only, does NOT touch Windows routing/DNS");
                sb.AppendLine("#   warp      : system Cloudflare WARP client, takes over ALL machine traffic");
                sb.AppendLine("#   custom    : upstream= socks5://host:port (or leave blank to use proxies.txt)");
                sb.AppendLine("#   direct    : no tunnel, the real IP reaches Ankama");
                sb.AppendLine("mode=" + NormalizeMode(Mode));
                sb.AppendLine("socks_port=" + SocksPort);
                sb.AppendLine("upstream=" + (Upstream ?? ""));
                sb.AppendLine("base_port=" + BasePort);
                sb.AppendLine("fail_closed=" + (FailClosed ? "1" : "0"));
                if (!string.IsNullOrEmpty(ExitProxy)) sb.AppendLine("exit_proxy=" + ExitProxy);
                if (AllowSystemWarp) sb.AppendLine("allow_system_warp=1");
                sb.AppendLine("extra_args=" + (ExtraArgs ?? ""));
                sb.AppendLine("wireproxy_conf=" + (WireproxyConf ?? ""));
                sb.AppendLine("log_path=" + (LogPath ?? ""));
                // No BOM: the relay reads this file too, and a BOM turns the first
                // line into a key it cannot match.
                File.WriteAllText(path, sb.ToString(), new UTF8Encoding(false));
            }
            catch { }
            Invalidate();
        }

        public static string NormalizeMode(string raw)
        {
            string m = (raw ?? "").Trim().ToLowerInvariant();
            if (m == ModeAuto || m == ModeWarp || m == ModeCustom || m == ModeDirect || m == ModeWireproxy) return m;
            if (m == "best" || m == "supervisor" || m == "failover") return ModeAuto;
            if (m == "wireguard" || m == "wg" || m == "wireproxy.exe") return ModeWireproxy;
            if (m == "cloudflare" || m == "warp-cli" || m == "system") return ModeWarp;
            if (m == "proxy" || m == "manual" || m == "socks5") return ModeCustom;
            if (m == "off" || m == "none" || m == "plain") return ModeDirect;
            return ModeAuto;
        }

        public static string[] Modes()
        {
            return new string[] { ModeAuto, ModeWireproxy, ModeWarp, ModeCustom, ModeDirect };
        }

        public static string ModeLabel(string mode)
        {
            switch (NormalizeMode(mode))
            {
                case ModeAuto: return "Auto - pick and keep the best working egress (recommended)";
                case ModeWireproxy: return "WireGuard userspace tunnel";
                case ModeWarp: return "System Cloudflare WARP (takes over Windows routing)";
                case ModeCustom: return "Custom upstream (SOCKS5 / HTTP / proxies.txt)";
                default: return "Direct - no tunnel (exposes your IP)";
            }
        }

        public static string ModeNote(string mode)
        {
            switch (NormalizeMode(mode))
            {
                case ModeAuto:
                    return "Tests the tunnel, the configured upstream and every proxies.txt entry for real, keeps the " +
                           "fastest healthy one, and fails over by itself when it dies. A candidate whose exit IP equals " +
                           "this machine's own address is rejected, so a transparent proxy can never be trusted.";
                case ModeWireproxy:
                    return "Runs bin\\warp\\wireproxy.exe: a local SOCKS5 port and nothing else. " +
                           "Windows routing, DNS and WiFi are untouched, so a dead tunnel cannot break your normal connection.";
                case ModeWarp:
                    return "Drives the installed Cloudflare WARP client. It installs an adapter and rewrites routing/DNS " +
                           "for the whole PC - if it drops, your WiFi drops with it. Use WireGuard mode instead.";
                case ModeCustom:
                    return "Every instance leaves through the upstream below. Leave it blank to round-robin proxies.txt.";
                default:
                    return "No upstream. Ankama sees this machine's real address. Only use it for local testing.";
            }
        }

        public string SocksUrl
        {
            get { return "socks5://127.0.0.1:" + SocksPort; }
        }

        public bool IsTunnelMode
        {
            get { return NormalizeMode(Mode) == ModeWireproxy || NormalizeMode(Mode) == ModeWarp; }
        }

        public string Summary
        {
            get
            {
                switch (NormalizeMode(Mode))
                {
                    case ModeAuto:
                        return "auto: supervisor picks (see Settings > Test)";
                    case ModeWireproxy: return "WireGuard " + SocksUrl + " (userspace)";
                    case ModeWarp: return "system WARP " + SocksUrl;
                    case ModeCustom:
                        return string.IsNullOrEmpty(Upstream) ? "custom: proxies.txt round-robin" : "custom: " + Upstream;
                    default: return "DIRECT (no tunnel)";
                }
            }
        }

        private static int ClampInt(string raw, int fallback, int min, int max)
        {
            int v;
            if (!int.TryParse((raw ?? "").Trim(), out v)) return fallback;
            if (v < min || v > max) return fallback;
            return v;
        }

        private static bool ParseBool(string raw, bool fallback)
        {
            string v = (raw ?? "").Trim().ToLowerInvariant();
            if (v == "1" || v == "true" || v == "yes" || v == "on") return true;
            if (v == "0" || v == "false" || v == "no" || v == "off") return false;
            return fallback;
        }
    }
}
