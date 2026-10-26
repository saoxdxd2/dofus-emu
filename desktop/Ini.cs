// Shared key=value parser for the app's small INI-style files.

namespace DofusFarm.Engine
{
    internal static class Ini
    {
        public static bool TryKeyValue(string raw, out string key, out string value,
                                       bool skipHash = true, bool skipSemicolon = true)
        {
            key = null;
            value = null;
            string line = (raw ?? "").Trim();
            if (line.Length == 0) return false;
            if (skipHash && line.StartsWith("#")) return false;
            if (skipSemicolon && line.StartsWith(";")) return false;
            int eq = line.IndexOf('=');
            if (eq <= 0) return false;
            key = line.Substring(0, eq).Trim();
            value = line.Substring(eq + 1).Trim();
            return true;
        }
    }
}
