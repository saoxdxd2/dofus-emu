using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;

namespace DofusFarm.Engine
{
    public class EmulatorBackend : IClientBackend
    {
        private readonly FarmEngine _engine;
        private readonly ConcurrentDictionary<string, ClientInstanceState> _states = new ConcurrentDictionary<string, ClientInstanceState>(StringComparer.OrdinalIgnoreCase);
        private readonly ConcurrentDictionary<string, byte> _startsInFlight = new ConcurrentDictionary<string, byte>(StringComparer.OrdinalIgnoreCase);
        private readonly Action<string> _logForwarder;

        public string Name { get { return "Android AOSP Ranchu Emulator"; } }
        public BackendType Type { get { return BackendType.AndroidEmulator; } }

        public event Action<string> OnLog;
        public event Action<string, ClientInstanceState> OnStateChanged;

        public EmulatorBackend(FarmEngine engine)
        {
            _engine = engine ?? new FarmEngine();
            _logForwarder = msg => OnLog?.Invoke(msg);
            _engine.OnLog += _logForwarder;
        }

        public bool IsSupported(out string failureReason)
        {
            failureReason = null;
            if (!File.Exists(_engine.EmulatorPath))
            {
                failureReason = "Android emulator executable not found at: " + _engine.EmulatorPath;
                return false;
            }
            return true;
        }

        public void Initialize()
        {
            try { _engine.Adb("", "start-server", 5000); } catch { }
        }

        public void Start(ClientLaunchOptions options)
        {
            if (options == null || string.IsNullOrEmpty(options.InstanceId)) return;
            string id = options.InstanceId;
            if (!_startsInFlight.TryAdd(id, 0)) return;
            SetState(id, ClientInstanceState.Starting);

            try
            {
                _engine.LaunchInstance(id, options.SlotPort, options.RamMb, options.Cores, options.Proxy);
                bool running = _engine.GetInstances().Any(i => string.Equals(i.Name, id, StringComparison.OrdinalIgnoreCase) && i.IsRunning);
                SetState(id, running ? ClientInstanceState.Running : ClientInstanceState.Faulted);
                if (!running) OnLog?.Invoke(string.Format("[EMULATOR-BACKEND] {0} did not reach the running state after launch.", id));
            }
            catch (Exception ex)
            {
                SetState(id, ClientInstanceState.Faulted);
                OnLog?.Invoke(string.Format("[EMULATOR-BACKEND] Failed to launch {0}: {1}", id, ex.Message));
            }
            finally { byte b; _startsInFlight.TryRemove(id, out b); }
        }

        public void Stop(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return;
            SetState(instanceId, ClientInstanceState.Stopping);

            try
            {
                var instances = _engine.GetInstances();
                var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
                if (target == null)
                {
                    // Unknown id: never fall back to a guessed serial - that used to stop whatever
                    // happened to run on emulator-5554.
                    OnLog?.Invoke(string.Format("[EMULATOR-BACKEND] Stop ignored: unknown instance '{0}'.", instanceId));
                    return;
                }
                _engine.StopInstance(target.Serial, instanceId);
            }
            finally
            {
                SetState(instanceId, ClientInstanceState.Stopped);
            }
        }

        public void Focus(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return;
            var instances = _engine.GetInstances();
            var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
            if (target != null && target.Port > 0)
            {
                _engine.FocusInstance(target.Port);
            }
        }

        public void TileWindows(bool edgeToEdge)
        {
            var pids = RunningPids();
            if (pids.Count > 0)
            {
                DofusFarm.Native.Win32.ApplyFarmLayout(pids, edgeToEdge);
            }
        }

        public void CascadeWindows()
        {
            var pids = RunningPids();
            if (pids.Count > 0)
            {
                DofusFarm.Native.Win32.ApplyCascadeLayout(pids);
            }
        }

        private List<int> RunningPids()
        {
            return _engine.GetInstances().Where(i => i.IsRunning && i.Pid > 0).Select(i => i.Pid).ToList();
        }

        public ClientInstanceState GetState(string instanceId)
        {
            if (string.IsNullOrEmpty(instanceId)) return ClientInstanceState.Stopped;
            ClientInstanceState state;
            if (!_states.TryGetValue(instanceId, out state))
            {
                try
                {
                    return _engine.GetInstances().Any(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase) && i.IsRunning)
                        ? ClientInstanceState.Running : ClientInstanceState.Stopped;
                }
                catch { return ClientInstanceState.Stopped; }
            }

            if (state != ClientInstanceState.Running) return state;

            // A cached Running entry is only trusted while the engine still sees the instance.
            // Stop All or an external emulator kill used to leave the grid showing "Running"
            // forever because nothing invalidated the cache.
            try
            {
                if (_engine.GetInstances().Any(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase) && i.IsRunning))
                    return ClientInstanceState.Running;
            }
            catch { return state; }

            _states[instanceId] = ClientInstanceState.Stopped;
            return ClientInstanceState.Stopped;
        }

        public ClientDiagnostics GetDiagnostics(string instanceId)
        {
            var diag = new ClientDiagnostics
            {
                InstanceId = instanceId,
                Backend = BackendType.AndroidEmulator,
                State = GetState(instanceId)
            };

            try
            {
                var instances = _engine.GetInstances();
                var target = instances.FirstOrDefault(i => string.Equals(i.Name, instanceId, StringComparison.OrdinalIgnoreCase));
                if (target != null && target.Pid > 0)
                {
                    using (var proc = Process.GetProcessById(target.Pid))
                    {
                        diag.WorkingSetMb = proc.WorkingSet64 / (1024.0 * 1024.0);
                        diag.PrivateCommitMb = proc.PrivateMemorySize64 / (1024.0 * 1024.0);
                        diag.Details = string.Format("PID: {0} | Priority: {1}", target.Pid, proc.PriorityClass);
                    }
                }
            }
            catch { }

            return diag;
        }

        private void SetState(string instanceId, ClientInstanceState newState)
        {
            _states[instanceId] = newState;
            try { OnStateChanged?.Invoke(instanceId, newState); } catch { }
        }

        public ICollection<string> GetActiveInstanceIds()
        {
            var list = new List<string>();
            try
            {
                foreach (var inst in _engine.GetInstances())
                {
                    if (inst.IsRunning) list.Add(inst.Name);
                }
            }
            catch { }
            return list;
        }

        public List<int> GetAllBrowserProcessIds()
        {
            return new List<int>();
        }

        public void Dispose()
        {
            try { _engine.OnLog -= _logForwarder; } catch { }
            _states.Clear();
        }
    }
}
