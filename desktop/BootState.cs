using System;
using System.Text.RegularExpressions;

namespace DofusFarm.Update
{
    /// <summary>
    /// What to do with a client bundle, decided from whether the last one actually booted.
    ///
    /// The gap this closes is measured, not imagined. On 2026-11-04 all 23 runtime patch rules
    /// matched build 3.3.11's bytes -- `--bundle-selftest` even printed RESULT: IDENTICAL, which
    /// only compares text the rules produced against text the rules produced -- and the patched
    /// client black-screened on "Callback was already called". Every check in the chain was green
    /// because none of them ever asked the running game. So the adoption decision takes the boot
    /// answer as an input now, and a build that has been seen not to boot is never patched again.
    ///
    /// Pure on purpose: no files, no clocks, no WebView2. The gate compiles this file and runs the
    /// fixtures in it, which is the only way to know a revert rule can fail.
    /// </summary>
    internal static class BootState
    {
        public const string Patch = "patch";        // adopt the download and patch it
        public const string Vendor = "vendor";      // adopt the download as Ankama wrote it
        public const string Attest = "attest";      // the client reported a boot: stop watching
        public const string Revert = "revert";      // first failed boot: fix the game, watch the build
        public const string Blacklist = "blacklist";// second failed boot: never patch this build
        public const string Ignore = "ignore";      // an answer about a bundle we are not tracking

        public const string BrainStart = "start";    // nothing answered on the brain port
        public const string BrainOther = "other";    // something that is not the brain answered
        public const string BrainAttach = "attach";  // the brain answered and its code is current
        public const string BrainRestart = "restart";// the brain answered with code older than its files

        /// <summary>Does this rule's replacement still contain the text it was keyed on?</summary>
        /// <remarks>
        /// A rule like that produces output its own anchor matches again, so a second
        /// adoption wraps the callback a second time. Measured 2026-11-04 on build
        /// 3.3.11: the 23 rules take 23/23 on the vendor bundle, and running them over
        /// their own output applies rules 0, 1, 2 and 3 again -- three boundary rules
        /// whose replacement still holds their start marker, and one replace rule whose
        /// replacement contains its needle. "Callback was already called" is what a
        /// doubled wrapper looks like from the client's side.
        /// </remarks>
        public static bool RuleIsSelfEmbedding(string anchor, string replacement)
        {
            if (string.IsNullOrEmpty(anchor) || string.IsNullOrEmpty(replacement)) return false;
            return replacement.IndexOf(anchor, StringComparison.Ordinal) >= 0;
        }

        /// <summary>Skip a self-embedding rule whose effect is already in the bundle.</summary>
        public static bool SkipSelfEmbedded(bool selfEmbedding, bool effectPresent)
        {
            return selfEmbedding && effectPresent;
        }

        /// <summary>What the panel should do about a brain daemon it did not spawn.</summary>
        /// <remarks>
        /// Liveness is not currency. The daemon reports which of its own repository files are newer
        /// than the moment it imported them, because a build that answers `status: ok` while running
        /// pre-fix code makes every later test re-measure the old behaviour -- measured 2026-11-04,
        /// where three fixes landed after the attached daemon started and each relaunch looked like a
        /// failed fix. A daemon that predates the field answers without `code_stale` and counts as
        /// current: this panel cannot tell a stale build from a silent one, and refusing to attach to
        /// every daemon it cannot date would be worse than the failure it guards.
        /// </remarks>
        public static string DecideBrainAttach(string health)
        {
            if (string.IsNullOrEmpty(health)) return BrainStart;
            if (health.IndexOf("dofus-brain", StringComparison.Ordinal) < 0) return BrainOther;
            return StaleModulesOf(health).Length == 0 ? BrainAttach : BrainRestart;
        }

        /// <summary>The module names a health reply says the daemon imported before they changed.</summary>
        public static string StaleModulesOf(string health)
        {
            if (string.IsNullOrEmpty(health)) return "";
            Match m = Regex.Match(health, "\"code_stale\"\\s*:\\s*\\[(?<list>[^\\]]*)\\]");
            if (!m.Success) return "";
            return m.Groups["list"].Value.Replace("\"", "").Trim();
        }

        /// <summary>The pid the daemon reported about itself, or 0.</summary>
        public static int BrainPid(string health)
        {
            if (string.IsNullOrEmpty(health)) return 0;
            Match m = Regex.Match(health ?? "", "\"pid\"\\s*:\\s*(?<pid>\\d+)");
            return m.Success ? int.Parse(m.Groups["pid"].Value) : 0;
        }

        /// <summary>Is this exact build stamp on the never-patch-again list?</summary>
        /// <remarks>
        /// Line-exact, not a substring: 3.3.11 and 3.3.110 share a prefix, and a match on the
        /// prefix would quietly blacklist a build nobody has ever booted.
        /// </remarks>
        public static bool IsBad(string badList, string stamp)
        {
            if (string.IsNullOrEmpty(badList) || string.IsNullOrEmpty(stamp)) return false;
            foreach (string line in badList.Split('\n'))
                if (string.Equals(line.Trim(), stamp, StringComparison.Ordinal)) return true;
            return false;
        }

        /// <summary>
        /// Append a stamp to the list. Duplicate-free, and history is never trimmed: forgetting a
        /// bad build is how the same black screen comes back with the next "harmless" update.
        /// </summary>
        public static string AddBad(string badList, string stamp)
        {
            if (string.IsNullOrEmpty(stamp)) return badList == null ? "" : badList;
            if (IsBad(badList, stamp)) return badList == null ? "" : badList;
            string head = string.IsNullOrEmpty(badList) ? "" : badList.TrimEnd('\n', '\r') + "\n";
            return head + stamp + "\n";
        }

        /// <summary>
        /// Should the download for `stamp` be patched, or adopted as the vendor wrote it?
        ///
        /// Two answers and both are evidence-based: a build that has been seen not to boot, and a
        /// rule set that found nothing to change. The second one matters because "no rule matched"
        /// used to be reported as a successful patch run.
        /// </summary>
        public static string DecideAdoption(string badList, string stamp, int rulesMatched)
        {
            if (IsBad(badList, stamp)) return Vendor;
            if (rulesMatched <= 0) return Vendor;
            return Patch;
        }

        /// <summary>
        /// Drop one stamp from a list, keeping the rest. A healthy boot is evidence too, and a
        /// watch list that only ever grows would blacklist a build on a CDN bad day two updates
        /// later.
        /// </summary>
        public static string RemoveBad(string badList, string stamp)
        {
            if (string.IsNullOrEmpty(badList)) return "";
            var kept = new System.Collections.Generic.List<string>();
            foreach (string line in badList.Split('\n'))
            {
                string one = line.Trim();
                if (one.Length > 0 && one != stamp) kept.Add(one);
            }
            return kept.Count == 0 ? "" : string.Join("\n", kept.ToArray()) + "\n";
        }

        /// <summary>
        /// Fold a boot answer from the page into the pending record.
        ///
        /// Only an answer about the bundle we are watching is evidence about our patches. A client
        /// that failed while running the vendor's bytes tells us nothing about a patch, and one
        /// that reports a build we already replaced must not clear the pending entry for the
        /// current build -- that would be a false green.
        ///
        /// Two strikes, on purpose. `ASSET_MISSING` is the vendor's own reason as well as our
        /// patch's signature, so one failed boot cannot tell "our series callback doubled fired"
        /// from "Ankama's CDN 403'd for ten minutes". The first failure is still worth acting on
        /// -- the operator's screen is black right now, so it reverts -- but it only *watches* the
        /// build. A second failure, after the updater re-patched and a client failed again, is the
        /// pattern no network hiccup repeats, and that is what stops this build being patched.
        /// </summary>
        public static string OnCanary(string pendingStamp, string canaryStamp, bool ok, bool seenBefore)
        {
            if (string.IsNullOrEmpty(pendingStamp)) return Ignore;
            if (string.IsNullOrEmpty(canaryStamp)) return Ignore;
            if (!string.Equals(pendingStamp.Trim(), canaryStamp.Trim(), StringComparison.Ordinal))
                return Ignore;
            if (ok) return Attest;
            return seenBefore ? Blacklist : Revert;
        }
    }
}
