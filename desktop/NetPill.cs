using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace DofusFarm.Net
{
    /// <summary>
    /// The nav pill's one-line verdict on the network pipeline.
    ///
    /// It lives in its own file so the gate can compile and RUN it (the same way the recorder's
    /// redaction regex is executed rather than grepped). Painting this in MainForm would mean the
    /// only proof that a red pill means red is a screenshot.
    ///
    /// Why it exists at all: every failure in this pipeline presented as a black game window while
    /// some counter somewhere still said green. The pill is the counter that has to say the truth
    /// out loud, in the one place the operator looks before opening DevTools.
    /// </summary>
    internal static class NetPill
    {
        public const int Unknown = 0;   // no report to read at all
        public const int Bad = 1;       // the report names something broken
        public const int Stale = 2;     // nothing named, but nobody has checked recently
        public const int Good = 3;

        // A report older than this is not a status: the watchdog that writes it is stopped or
        // stuck, and its green would be hours old. Older than DeadSeconds and the "ok" itself
        // stops meaning anything, so it goes amber with its age.
        private const long StaleSeconds = 300;
        private const long DeadSeconds = 3600;

        private static readonly Regex Problems =
            new Regex("\"problems\"\\s*:\\s*\\[([^\\]]*)\\]");
        private static readonly Regex CheckedAt =
            new Regex("\"checked_at\"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)");
        private static readonly Regex QuotedName = new Regex("\"([^\"]+)\"");

        /// <summary>
        /// Decide the text and the level from the report body and the current unix time.
        ///
        /// Both fields are read from the LAST occurrence, not the first: an `ensure` report nests
        /// `before`, then `actions` (whose rows carry their own `problems` and re-checks), then
        /// `after`, and only then the top-level verdict. The pill describes the state we ended in,
        /// so the first match would paint the pre-repair view as live.
        /// </summary>
        public static int From(string json, long nowUnix, out string text)
        {
            text = "net ?";
            if (string.IsNullOrEmpty(json)) return Unknown;

            string problems = LastGroup(Problems, json);
            double then;
            bool dated = double.TryParse(LastGroup(CheckedAt, json), NumberStyles.Float,
                                         CultureInfo.InvariantCulture, out then);
            long age = dated ? nowUnix - (long)then : -1;

            if (problems != null && problems.Trim().Length > 0)
            {
                string named = JoinNames(QuotedName.Matches(problems));
                text = "net! " + named + (age >= StaleSeconds ? " " + Human(age) : "");
                return Bad;
            }
            if (!dated) return Unknown;
            if (age < 0)
            {
                // A report from the future means the clock moved, not that the pipeline is fine.
                text = "net ? clock";
                return Unknown;
            }
            if (age >= DeadSeconds)
            {
                text = "net ? " + Human(age);
                return Stale;
            }
            if (age >= StaleSeconds)
            {
                text = "net ok " + Human(age);
                return Stale;
            }
            text = "net ok";
            return Good;
        }

        private static string LastGroup(Regex regex, string json)
        {
            Match last = null;
            foreach (Match m in regex.Matches(json)) last = m;
            return last == null ? null : last.Groups[1].Value;
        }

        /// <summary>Two names fit the pill; the rest become one plus sign.</summary>
        private static string JoinNames(MatchCollection found)
        {
            List<string> names = new List<string>();
            foreach (Match m in found)
                if (m.Groups[1].Value.Length > 0) names.Add(m.Groups[1].Value);
            if (names.Count == 0) return "?";
            if (names.Count <= 2) return string.Join(",", names.ToArray());
            return names[0] + "," + names[1] + "+";
        }

        private static string Human(long seconds)
        {
            if (seconds < 0) return "?";
            if (seconds < 60) return seconds.ToString(CultureInfo.InvariantCulture) + "s";
            if (seconds < 3600) return (seconds / 60).ToString(CultureInfo.InvariantCulture) + "m";
            return (seconds / 3600).ToString(CultureInfo.InvariantCulture) + "h";
        }
    }
}
