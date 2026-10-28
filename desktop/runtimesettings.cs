// Optional dashboard/agent behaviours in one small persisted file.
//
// These used to be constants: the grid refreshed every 4 s, the watchdog always
// ran, every window polled memory, and every autopilot run wrote fight-board
// snapshots. Those defaults stay, so an existing install behaves the same until
// the user changes a toggle in Settings.

using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace DofusFarm.Engine
{
    public sealed class RuntimeSettings
    {
        // 0 = no periodic refresh (manual/event refreshes still happen).
        public int RefreshMs = 4000;
        public bool Watchdog = true;
        public bool Governor = true;
        public bool Telemetry = true;
        public bool FightBoards = true;
        // Always-on AI: every client that launches is driven without pressing Start.
        // A manual Stop still wins until the next launch.
        public bool AutoAi = true;

        private static readonly object _lock = new object();
        private static RuntimeSettings _current;

        public static RuntimeSettings Current
        {
            get
            {
                lock (_lock)
                {
                    if (_current == null) _current = Load();
                    return _current;
                }
            }
        }

        public static string PathFor()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "DofusFarm", "runtime.ini");
        }

        public static RuntimeSettings Load()
        {
            var settings = new RuntimeSettings();
            try
            {
                string path = PathFor();
                if (!File.Exists(path)) return settings;
                foreach (string line in File.ReadLines(path))
                {
                    string key, value;
                    if (!Ini.TryKeyValue(line, out key, out value,
                                         skipHash: false, skipSemicolon: false)) continue;
                    if (Same(key, "refresh_ms"))
                    {
                        int ms;
                        if (int.TryParse(value, NumberStyles.Integer,
                                         CultureInfo.InvariantCulture, out ms))
                            settings.RefreshMs = ClampRefresh(ms);
                    }
                    else if (Same(key, "watchdog")) settings.Watchdog = ParseBool(value, settings.Watchdog);
                    else if (Same(key, "governor")) settings.Governor = ParseBool(value, settings.Governor);
                    else if (Same(key, "telemetry")) settings.Telemetry = ParseBool(value, settings.Telemetry);
                    else if (Same(key, "fight_boards")) settings.FightBoards = ParseBool(value, settings.FightBoards);
                    else if (Same(key, "auto_ai")) settings.AutoAi = ParseBool(value, settings.AutoAi);
                }
            }
            catch { }
            return settings;
        }

        public void Save()
        {
            try
            {
                string path = PathFor();
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var text = new StringBuilder();
                text.AppendLine("refresh_ms=" + ClampRefresh(RefreshMs).ToString(CultureInfo.InvariantCulture));
                text.AppendLine("watchdog=" + (Watchdog ? "1" : "0"));
                text.AppendLine("governor=" + (Governor ? "1" : "0"));
                text.AppendLine("telemetry=" + (Telemetry ? "1" : "0"));
                text.AppendLine("fight_boards=" + (FightBoards ? "1" : "0"));
                text.AppendLine("auto_ai=" + (AutoAi ? "1" : "0"));
                File.WriteAllText(path, text.ToString(), Encoding.UTF8);
                lock (_lock) { _current = this; }
            }
            catch { }
        }

        public static int ClampRefresh(int ms)
        {
            if (ms <= 0) return 0;
            if (ms < 1000) return 1000;
            if (ms > 60000) return 60000;
            return ms;
        }

        private static bool Same(string a, string b)
        {
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        private static bool ParseBool(string value, bool fallback)
        {
            if (string.IsNullOrEmpty(value)) return fallback;
            value = value.Trim();
            return value == "1" || Same(value, "true") || Same(value, "on") || Same(value, "yes");
        }
    }
}
