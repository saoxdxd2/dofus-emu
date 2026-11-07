using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.RegularExpressions;
using System.Threading;
using System.Runtime.InteropServices;
using DofusFarm.Native;

namespace DofusFarm.Engine
{
    public class PriorityGovernor
    {
        private Timer _timer;
        public FarmEngine Engine { get; set; }          // optional: lets the governor size instances from live state
        public IClientBackend Backend { get; set; }     // optional: lets the governor balance WebView2 instances
        private int _lastForegroundPid = -1;
        private readonly HashSet<int> _pinnedPids = new HashSet<int>();
        // Last power-throttling state applied per WebView2 PID. Applying it
        // opens and closes a process handle, so only reapply when the
        // foreground/background role actually changes.
        private readonly Dictionary<int, bool> _throttleState = new Dictionary<int, bool>();
        // Role last applied per PID. Reading Process.PriorityClass opens a
        // handle, and the tick runs every 1.5 s; only re-read on a role change
        // or during the periodic reassert (in case something changed it).
        private readonly Dictionary<int, bool> _priorityState = new Dictionary<int, bool>();
        private DateTime _priorityReassertUtc = DateTime.MinValue;
        private int _runningState = 0;
        public volatile bool Enabled = true;
        // Process ancestry changes only when a child is spawned. Snapshotting the
        // whole process table every 1.5 s tick costs ~5.4 ms on this host; a short
        // cache keeps the parent map across 2-3 ticks. A new child may wait one
        // extra priority tick, which is harmless for a performance role.
        private readonly object _parentsLock = new object();
        private Dictionary<int, List<int>> _parentsCache;
        private DateTime _parentsCacheUtc = DateTime.MinValue;
        private BackendType? _parentsCacheBackend;
        private const double ParentCacheSeconds = 4.0;

        // Precompiled once: GetInstanceSlot parses emulator command lines, and
        // re-parsing the pattern from source on every new PID was pure waste.
        private static readonly Regex RxPortArg = new Regex(@"-port\s+(\d+)", RegexOptions.Compiled);
        private static readonly Regex RxCoresArg = new Regex(@"-cores\s+(\d+)", RegexOptions.Compiled);

        public event Action<string> OnLog;

        public PriorityGovernor()
        {
            Enabled = true;
        }

        public void Start()
        {
            Enabled = true;
            lock (_parentsLock) { _parentsCache = null; _parentsCacheUtc = DateTime.MinValue; }
            if (_timer == null)
            {
                _timer = new Timer(Tick, null, 2000, 1500);
            }
        }

        public void Stop()
        {
            Enabled = false;
            if (_timer != null)
            {
                try { _timer.Dispose(); } catch { }
                _timer = null;
            }
            lock (_pinnedPids) { _pinnedPids.Clear(); }
            lock (_slotCache)
            {
                _slotCache.Clear();
                _slotCoresCache.Clear();
            }
            lock (_throttleState) { _throttleState.Clear(); }
            lock (_parentsLock) { _parentsCache = null; _parentsCacheUtc = DateTime.MinValue; }
        }

        private Dictionary<int, List<int>> CachedParents(BackendType? backend)
        {
            lock (_parentsLock)
            {
                DateTime now = DateTime.UtcNow;
                if (_parentsCache != null
                    && _parentsCacheBackend == backend
                    && (now - _parentsCacheUtc).TotalSeconds < ParentCacheSeconds)
                    return _parentsCache;
                Dictionary<int, List<int>> fresh = null;
                try { fresh = Win32.SnapshotParentMap(); } catch { }
                if (fresh != null)
                {
                    _parentsCache = fresh;
                    _parentsCacheUtc = now;
                    _parentsCacheBackend = backend;
                }
                return fresh;
            }
        }

        private static void PrunePids(Dictionary<int, bool> state, HashSet<int> live)
        {
            List<int> stale = null;
            foreach (int pid in state.Keys)
            {
                if (live.Contains(pid)) continue;
                if (stale == null) stale = new List<int>();
                stale.Add(pid);
            }
            if (stale != null)
                for (int i = 0; i < stale.Count; i++) state.Remove(stale[i]);
        }

        private void Log(string message)
        {
            var handler = OnLog;
            if (handler != null && Enabled)
            {
                try
                {
                    handler(string.Format("[{0:HH:mm:ss}] [GOVERNOR] {1}", DateTime.Now, message));
                }
                catch { }
            }
        }

        private static readonly Dictionary<int, int> _slotCache = new Dictionary<int, int>();
        private static readonly Dictionary<int, int> _slotCoresCache = new Dictionary<int, int>();

        public static void RegisterInstancePid(int pid, int slot, int cores = 1)
        {
            lock (_slotCache)
            {
                _slotCache[pid] = slot;
                if (cores > 0)
                {
                    _slotCoresCache[slot] = cores;
                }
            }
        }

        // How many logical CPUs each running instance gets: totalCores / runningInstances, clamped
        // to [1, 4]. Recomputed on every tick, so instances that start or stop re-partition the host
        // without a restart (a new slot takes its share instead of everyone keeping stale masks).
        //
        // One CPU per instance is allowed here on purpose. The guest's vCPU count (how many cores
        // Android thinks it has) is a separate number set at launch - this only controls how the
        // host schedules the emulator's threads. With 4 instances on 4 physical cores each instance
        // owns its own physical core, which is the strongest split available: no two instances share
        // a core, so nothing gets descheduled mid-frame. Asking for 2 logical CPUs per instance on
        // that host would put 8 emulator threads on 8 logical CPUs that are really only 4 cores, so
        // every instance would end up time-slicing and the screen would stutter.
        public int LiveCoresPerInstance(int requestedCores, int totalCores)
        {
            int running = 1;
            try
            {
                if (Engine != null) running = Math.Max(1, Engine.ActiveInstanceCount());
                else running = Math.Max(1, Process.GetProcessesByName("qemu-system-x86_64").Length);
            }
            catch { }

            int share = Math.Max(1, totalCores) / Math.Max(1, running);
            return Math.Max(1, Math.Min(4, share));
        }

        public static long ComputeAffinityMask(int slot, int cores, int totalCores)
        {
            int safeTotal = Math.Min(62, Math.Max(1, totalCores));
            // Respect the requested core count even when it is 1 (was forced to 2 before, which
            // robbed single-core instances of a whole logical CPU each).
            int span = Math.Min(safeTotal, Math.Max(1, cores)); // already fixed (was Math.Max(2, cores))
            // Step by the instance's own span so slots pack without overlap: 1-core instances take
            // logical CPUs 0,1,2,... (Windows numbers the primary thread of each physical core first),
            // 2-core instances take {0,1},{2,3},... instead of a hardcoded stride of 2.
            int baseCore = (Math.Max(0, slot) * Math.Max(1, span)) % safeTotal;
            long mask = 0L;
            for (int c = 0; c < span; c++)
            {
                int bit = (baseCore + c) % safeTotal;
                mask |= (1L << bit);
            }
            return mask != 0L ? mask : 1L;
        }

        private static int GetInstanceSlot(int pid, out int assignedCores)
        {
            assignedCores = 1;
            lock (_slotCache)
            {
                int cached;
                if (_slotCache.TryGetValue(pid, out cached))
                {
                    int cCores;
                    if (_slotCoresCache.TryGetValue(cached, out cCores) && cCores > 0)
                        assignedCores = cCores;
                    return cached;
                }
            }

            int resolved = -1;
            int parsedCores = 1;
            try
            {
                // Use CreateToolhelp32Snapshot + command-line cache to avoid WMI.
                string cmd = GetCachedCommandLine(pid);
                if (!string.IsNullOrEmpty(cmd))
                {
                    var mp = RxPortArg.Match(cmd);
                    if (mp.Success)
                    {
                        int port = int.Parse(mp.Groups[1].Value);
                        resolved = Math.Max(0, (port - 5554) / 2);
                    }
                    var mc = RxCoresArg.Match(cmd);
                    if (mc.Success)
                    {
                        int.TryParse(mc.Groups[1].Value, out parsedCores);
                    }
                }
            }
            catch { }

            lock (_slotCache)
            {
                _slotCache[pid] = resolved;
                if (resolved >= 0)
                {
                    int cCores;
                    if (_slotCoresCache.TryGetValue(resolved, out cCores) && cCores > 0)
                        assignedCores = cCores;
                    else
                    {
                        assignedCores = Math.Max(1, parsedCores);
                        _slotCoresCache[resolved] = assignedCores;
                    }
                }
            }
            return resolved;
        }

        private static Dictionary<int, string> _cmdLineCache = new Dictionary<int, string>();
        private static readonly object _cmdLineLock = new object();
        private static string GetCachedCommandLine(int pid)
        {
            lock (_cmdLineLock)
            {
                string cached;
                if (_cmdLineCache.TryGetValue(pid, out cached)) return cached;
            }
            try
            {
                // Use OpenProcess + NtQueryInformationProcess to read the command line without WMI.
                IntPtr hProcess = NativeMethods.OpenProcess(0x0400 | 0x0010, false, pid);
                if (hProcess == IntPtr.Zero) return null;
                try
                {
                    // Use PROCESS_BASIC_INFORMATION to get PEB address
                    NativeMethods.PROCESS_BASIC_INFORMATION_X pbi = new NativeMethods.PROCESS_BASIC_INFORMATION_X();
                    uint returnLen = 0;
                    uint err = NativeMethods.NtQueryInformationProcess(hProcess, 0, ref pbi, (uint)System.Runtime.InteropServices.Marshal.SizeOf(typeof(NativeMethods.PROCESS_BASIC_INFORMATION_X)), ref returnLen);
                    if (err == 0 && pbi.PebBaseAddress != IntPtr.Zero)
                    {
                        // Read the PEB structure from the remote process
                        byte[] pebBytes = new byte[System.Runtime.InteropServices.Marshal.SizeOf(typeof(NativeMethods.PEB_X))];
                        uint bytesRead;
                        if (NativeMethods.ReadProcessMemory(hProcess, pbi.PebBaseAddress, pebBytes, (uint)pebBytes.Length, out bytesRead))
                        {
                            // Parse PEB to get ProcessParameters pointer
                            IntPtr processParams = IntPtr.Zero;
                            // Offset depends on architecture: x64 = 0x20, x86 = 0x10
                            int offset = IntPtr.Size == 8 ? 0x20 : 0x10;
                            processParams = IntPtr.Add(pbi.PebBaseAddress, offset);

                            // Read RTL_USER_PROCESS_PARAMETERS
                            byte[] paramsBytes = new byte[System.Runtime.InteropServices.Marshal.SizeOf(typeof(NativeMethods.RTL_USER_PROCESS_PARAMETERS_X))];
                            if (NativeMethods.ReadProcessMemory(hProcess, processParams, paramsBytes, (uint)paramsBytes.Length, out bytesRead))
                            {
                                // Parse command line from parameters
                                IntPtr cmdLinePtr = IntPtr.Zero;
                                int cmdLineOffset = IntPtr.Size == 8 ? 0x70 : 0x38;
                                cmdLinePtr = IntPtr.Add(processParams, cmdLineOffset);

                                // Read the command line length and buffer pointer
                                byte[] cmdLineInfo = new byte[8]; // 2 ushort + 1 IntPtr
                                if (NativeMethods.ReadProcessMemory(hProcess, cmdLinePtr, cmdLineInfo, (uint)cmdLineInfo.Length, out bytesRead))
                                {
                                    ushort cmdLen = System.BitConverter.ToUInt16(cmdLineInfo, 0);
                                    ushort cmdMaxLen = System.BitConverter.ToUInt16(cmdLineInfo, 2);
                                    IntPtr cmdBuffer = IntPtr.Size == 8 ? (IntPtr)System.BitConverter.ToInt64(cmdLineInfo, 4) : (IntPtr)System.BitConverter.ToInt32(cmdLineInfo, 4);

                                    if (cmdLen > 0 && cmdBuffer != IntPtr.Zero)
                                    {
                                        byte[] buffer = new byte[cmdLen * sizeof(char)];
                                        if (NativeMethods.ReadProcessMemory(hProcess, cmdBuffer, buffer, (uint)buffer.Length, out bytesRead))
                                        {
                                            string cmdLineRead = System.Text.Encoding.Unicode.GetString(buffer, 0, (int)(bytesRead / sizeof(char)));
                                            lock (_cmdLineLock)
                                            {
                                                if (!_cmdLineCache.ContainsKey(pid))
                                                {
                                                    if (_cmdLineCache.Count > 512) _cmdLineCache.Clear();
                                                    _cmdLineCache[pid] = cmdLineRead;
                                                }
                                            }
                                            return cmdLineRead;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                finally
                {
                    NativeMethods.CloseHandle(hProcess);
                }
            }
            catch { return null; }
            return null;
        }

        // The farm engine pins each emulator at launch time. When the governor also
        // re-pins the same qemu process to a different mask, Windows migrates the
        // threads across cores and the guest loses its cached TCG blocks - that is
        // what makes boot slow and the device feel unresponsive to mouse input.
        // One owner only: when the engine handles affinity, the governor stays out.
        public bool EngineOwnsAffinity { get; set; }

        private int _lastForegroundWvPid = -1;

        private void Tick(object state)
        {
            if (!Enabled || Interlocked.CompareExchange(ref _runningState, 1, 0) != 0) return;

            try
            {
                IntPtr hFg = Win32.GetForegroundWindow();
                uint fgPid = 0;
                if (hFg != IntPtr.Zero) Win32.GetWindowThreadProcessId(hFg, out fgPid);

                // One backend owns the session: the other process family cannot
                // exist. Skipping its walk saves a full process-table scan every
                // tick; its role caches are cleared so a stale PID is never kept.
                BackendType? backend = Backend != null ? (BackendType?)Backend.Type : null;
                Dictionary<int, List<int>> parents = CachedParents(backend);
                if (backend == BackendType.AndroidEmulator)
                {
                    lock (_throttleState) { _throttleState.Clear(); }
                    lock (_priorityState) { _priorityState.Clear(); }
                }
                else
                {
                    GovernWebView2Processes(hFg, fgPid, parents);
                }
                if (backend == BackendType.WebView2Standalone)
                {
                    lock (_pinnedPids) { _pinnedPids.Clear(); }
                    lock (_slotCache) { _slotCache.Clear(); }
                }
                else
                {
                    GovernQemuProcesses(hFg, fgPid, parents);
                }
            }
            catch { }
            finally
            {
                Interlocked.Exchange(ref _runningState, 0);
            }
        }

        private void GovernWebView2Processes(IntPtr hFg, uint fgPid,
            Dictionary<int, List<int>> parents)
        {
            Process[] wvProcs = null;
            try
            {
                wvProcs = Process.GetProcessesByName("msedgewebview2");
                if (wvProcs == null || wvProcs.Length == 0)
                {
                    // No WebView2 process is alive. Drop the cached roles so a
                    // recycled PID is never mistaken for a process we already
                    // throttled.
                    lock (_throttleState) { _throttleState.Clear(); }
                    lock (_priorityState) { _priorityState.Clear(); }
                    return;
                }

                int activeBrowserPid = -1;
                List<int> browserPids = (Backend != null) ? Backend.GetAllBrowserProcessIds() : null;

                if (fgPid > 0)
                {
                    foreach (Process p in wvProcs)
                    {
                        if (p.Id == (int)fgPid)
                        {
                            activeBrowserPid = p.Id;
                            break;
                        }
                    }

                    if (activeBrowserPid <= 0 && browserPids != null && browserPids.Count > 0)
                    {
                        HashSet<int> tree = null;
                        try { tree = Win32.GetProcessTrees(new[] { (int)fgPid }, parents); } catch { }
                        foreach (int bPid in browserPids)
                        {
                            if (bPid == (int)fgPid || (tree != null && tree.Contains(bPid)))
                            {
                                activeBrowserPid = bPid;
                                break;
                            }
                        }
                    }
                }

                HashSet<int> activeTree = null;
                if (activeBrowserPid > 0)
                {
                    try { activeTree = Win32.GetProcessTrees(new[] { activeBrowserPid }, parents); }
                    catch { activeTree = new HashSet<int> { activeBrowserPid }; }
                }

                if (activeBrowserPid > 0 && activeBrowserPid != _lastForegroundWvPid)
                {
                    _lastForegroundWvPid = activeBrowserPid;
                    Log(string.Format("Foreground WebView2 prioritized (PID: {0} -> AboveNormal, EcoQoS off). Background renderers throttled.", activeBrowserPid));
                }
                else if (activeBrowserPid <= 0 && _lastForegroundWvPid != 0)
                {
                    _lastForegroundWvPid = 0;
                }

                bool reassert = (DateTime.UtcNow - _priorityReassertUtc).TotalSeconds >= 20;
                if (reassert)
                {
                    lock (_priorityState) { _priorityState.Clear(); }
                    _priorityReassertUtc = DateTime.UtcNow;
                }

                HashSet<int> live = new HashSet<int>(wvProcs.Length);
                for (int i = 0; i < wvProcs.Length; i++)
                {
                    Process p = wvProcs[i];
                    live.Add(p.Id);
                    try
                    {
                        bool isForeground = activeTree != null && activeTree.Contains(p.Id);
                        bool applied;
                        bool known;
                        lock (_priorityState) known = _priorityState.TryGetValue(p.Id, out applied);
                        if (!known || applied != isForeground)
                        {
                            ProcessPriorityClass want = isForeground
                                ? ProcessPriorityClass.AboveNormal
                                : ProcessPriorityClass.BelowNormal;
                            if (p.PriorityClass != want) p.PriorityClass = want;
                            lock (_priorityState) _priorityState[p.Id] = isForeground;
                        }
                        bool throttled = !isForeground;
                        lock (_throttleState)
                        {
                            bool previous;
                            if (!_throttleState.TryGetValue(p.Id, out previous) || previous != throttled)
                            {
                                // OpenProcess + SetProcessInformation + CloseHandle:
                                // do it only when this process changes role.
                                if (Win32.SetProcessPowerThrottling(p.Id, throttled))
                                    _throttleState[p.Id] = throttled;
                            }
                        }
                    }
                    catch { }
                }
                lock (_throttleState) { PrunePids(_throttleState, live); }
                lock (_priorityState) { PrunePids(_priorityState, live); }
            }
            catch { }
            finally
            {
                if (wvProcs != null)
                {
                    for (int i = 0; i < wvProcs.Length; i++)
                    {
                        try { wvProcs[i].Dispose(); } catch { }
                    }
                }
            }
        }

        private void GovernQemuProcesses(IntPtr hFg, uint fgPid,
            Dictionary<int, List<int>> parents)
        {
            Process[] qemuProcs = null;
            try
            {
                qemuProcs = Process.GetProcessesByName("qemu-system-x86_64");
                if (qemuProcs == null || qemuProcs.Length == 0)
                {
                    lock (_pinnedPids) { _pinnedPids.Clear(); }
                    lock (_slotCache) { _slotCache.Clear(); }
                    return;
                }

                // Dynamic CPU core affinity assignment respecting user's configured core count per instance
                int totalCores = Environment.ProcessorCount;
                if (totalCores >= 4 && !EngineOwnsAffinity)
                {
                    lock (_pinnedPids)
                    {
                        HashSet<int> livePids = new HashSet<int>();
                        for (int i = 0; i < qemuProcs.Length; i++)
                        {
                            Process p = qemuProcs[i];
                            livePids.Add(p.Id);
                            if (!_pinnedPids.Contains(p.Id))
                            {
                                try
                                {
                                    int instCores;
                                    int slot = GetInstanceSlot(p.Id, out instCores);
                                    if (slot < 0) slot = i;
                                    long mask = ComputeAffinityMask(slot, LiveCoresPerInstance(instCores, totalCores), totalCores);
                                    if (IntPtr.Size == 8)
                                        p.ProcessorAffinity = new IntPtr(mask);
                                    else
                                        p.ProcessorAffinity = new IntPtr(unchecked((int)mask));

                                    _pinnedPids.Add(p.Id);
                                    Log(string.Format("Governor pinned PID {0} (Slot #{1}, {2} threads) to Affinity Mask 0x{3:X}.", p.Id, slot + 1, Math.Max(1, instCores), mask));
                                }
                                catch { }
                            }
                        }
                        _pinnedPids.RemoveWhere(pid => !livePids.Contains(pid));

                        lock (_slotCache)
                        {
                            List<int> dead = new List<int>();
                            foreach (int pId in _slotCache.Keys)
                            {
                                if (!livePids.Contains(pId)) dead.Add(pId);
                            }
                            foreach (int pId in dead) _slotCache.Remove(pId);
                        }
                    }
                }

                if (hFg == IntPtr.Zero || fgPid == 0) return;

                // Check if the foreground window is one of the QEMU instances or their launcher wrapper (emulator.exe)
                bool isQemuActive = false;
                int activeTargetPid = -1;
                foreach (Process p in qemuProcs)
                {
                    if (p.Id == (int)fgPid)
                    {
                        isQemuActive = true;
                        activeTargetPid = p.Id;
                        break;
                    }
                }

                if (!isQemuActive)
                {
                    try
                    {
                        HashSet<int> tree = Win32.GetProcessTrees(new[] { (int)fgPid }, parents);
                        foreach (Process p in qemuProcs)
                        {
                            if (tree.Contains(p.Id))
                            {
                                isQemuActive = true;
                                activeTargetPid = p.Id;
                                break;
                            }
                        }
                    }
                    catch { }
                }

                if (isQemuActive && activeTargetPid > 0)
                {
                    if (_lastForegroundPid != activeTargetPid)
                    {
                        _lastForegroundPid = activeTargetPid;
                        foreach (Process p in qemuProcs)
                        {
                            try
                            {
                                if (p.Id == activeTargetPid)
                                {
                                    if (p.PriorityClass != ProcessPriorityClass.AboveNormal)
                                        p.PriorityClass = ProcessPriorityClass.AboveNormal;
                                }
                                else
                                {
                                    if (p.PriorityClass != ProcessPriorityClass.Normal)
                                        p.PriorityClass = ProcessPriorityClass.Normal;
                                }
                            }
                            catch { }
                        }
                        Log(string.Format("Foreground instance prioritized (PID: {0} -> AboveNormal). Background instances balanced at Normal.", activeTargetPid));
                    }
                }
                else
                {
                    // If user is outside the game, balance all to Normal
                    if (_lastForegroundPid != 0)
                    {
                        _lastForegroundPid = 0;
                        foreach (Process p in qemuProcs)
                        {
                            try
                            {
                                if (p.PriorityClass != ProcessPriorityClass.Normal)
                                    p.PriorityClass = ProcessPriorityClass.Normal;
                            }
                            catch { }
                        }
                    }
                }
            }
            catch { }
            finally
            {
                if (qemuProcs != null)
                {
                    for (int i = 0; i < qemuProcs.Length; i++)
                    {
                        if (qemuProcs[i] != null)
                        {
                            try { qemuProcs[i].Dispose(); } catch { }
                        }
                    }
                }
            }
        }
    }
}
