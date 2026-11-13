// uidispatcher.cs - the single funnel for every cross-thread UI update.
//
// Why this exists
// ---------------
// The host used to scatter `Invoke`/`BeginInvoke` across forms, panels and backends
// (110 call sites), and each one was its own little queue. Three consequences:
//
//   * ordering was per-call-site, not per-event, so a status line could overtake the
//     log line that explained it;
//   * a log flood posted one message-pump entry per line, so a directive that logged
//     200 lines queued 200 separate UI callbacks and the window stopped repainting;
//   * an exception thrown on any of those posted actions killed the process, because
//     nothing on the far side of BeginInvoke had a try/catch.
//
// This class fixes all three with one bounded queue:
//
//   * bounded      - `Capacity` items; beyond that the newest is dropped and counted
//                    (`Dropped`). Losing a status line is survivable; growing the
//                    queue until the host runs out of memory is not.
//   * coalescing   - `Post(key, action)` keeps at most one pending action per key and
//                    replaces its payload, so a 400 Hz progress meter costs one
//                    callback per frame.
//   * batched      - log lines are collected and handed to their sink once per frame,
//                    newline-joined, at ~30 Hz (33 ms). That is the difference between
//                    200 pump entries and one.
//   * guarded      - `Guard` is the centralized async boundary: an `async` body that
//                    throws reports the exception and returns, instead of taking the
//                    process down through an unobserved `async void`.
//
// The pump is a WinForms timer created on the UI thread, so `Drain` always runs where
// controls may be touched. When no message loop exists yet (early startup, a unit
// probe) `Drain()` can be called directly.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace DofusFarm.UI
{
    public sealed class UiDispatcher
    {
        /// <summary>One dispatcher for the process: one queue, one pump, one order.</summary>
        public static readonly UiDispatcher Instance = new UiDispatcher();

        /// <summary>Hard ceiling on queued work items. Older items are kept; new ones are dropped.</summary>
        public const int Capacity = 4096;

        /// <summary>~30 Hz. Set DOFUS_UI_HZ=60 for a 60 Hz pump.</summary>
        public static readonly int FrameMilliseconds = FrameIntervalFromEnvironment();

        /// <summary>Wall-clock budget for one frame's work, so a burst cannot freeze the paint.</summary>
        private const int WorkBudgetMs = 8;

        /// <summary>Where unhandled async exceptions are reported. Set once by the host.</summary>
        public static Action<string> ErrorSink;

        private sealed class WorkItem
        {
            public string Label;
            public string Key;
            public Action Action;
        }

        private sealed class LogItem
        {
            public Action<string> Sink;
            public string Line;
        }

        private readonly object _gate = new object();
        private readonly Queue<WorkItem> _pending = new Queue<WorkItem>(256);
        private readonly Dictionary<string, WorkItem> _byKey = new Dictionary<string, WorkItem>(StringComparer.Ordinal);
        private readonly List<LogItem> _logs = new List<LogItem>(256);
        private readonly List<string> _batch = new List<string>(256);

        private System.Windows.Forms.Timer _pump;
        private int _uiThreadId;

        private long _posted;
        private long _drained;
        private long _dropped;
        private long _logLines;
        private long _logBatches;
        private long _frames;
        private long _faults;
        private long _lastFrameTicks;

        private UiDispatcher() { }

        // -- lifecycle ----------------------------------------------------------

        /// <summary>Must be called on the UI thread: captures it and starts the pump.</summary>
        public void Start()
        {
            int current = Thread.CurrentThread.ManagedThreadId;
            if (_uiThreadId == 0) Interlocked.CompareExchange(ref _uiThreadId, current, 0);
            if (_uiThreadId != current) return;      // a background caller never owns the pump

            if (_pump == null)
            {
                _pump = new System.Windows.Forms.Timer();
                _pump.Interval = FrameMilliseconds;
                _pump.Tick += delegate { Drain(); };
            }
            if (!_pump.Enabled) _pump.Start();
        }

        /// <summary>Stops the pump and drains what is already queued. Safe to call twice.</summary>
        public void Stop()
        {
            try { if (_pump != null) _pump.Stop(); } catch { }
            try { Drain(); } catch { }
        }

        /// <summary>True when the caller is the thread that owns the message loop.</summary>
        public bool OnUiThread
        {
            get { return _uiThreadId != 0 && Thread.CurrentThread.ManagedThreadId == _uiThreadId; }
        }

        // -- posting ------------------------------------------------------------

        /// <summary>Queue work. Always deferred, matching the BeginInvoke semantics it replaces.</summary>
        public void Post(Action action)
        {
            Post("ui", null, action);
        }

        /// <summary>Queue work that a later call with the same key may replace before it runs.</summary>
        public void Post(string key, Action action)
        {
            Post(key, key, action);
        }

        /// <summary>Queue work with a diagnostic label and an optional coalescing key.</summary>
        public void Post(string label, string key, Action action)
        {
            if (action == null) return;
            lock (_gate)
            {
                if (key != null)
                {
                    WorkItem existing;
                    if (_byKey.TryGetValue(key, out existing))
                    {
                        // Keep the queued position, adopt the newest payload: the point of
                        // coalescing is that the last writer wins, not that the queue grows.
                        existing.Action = action;
                        existing.Label = label;
                        _posted++;
                        return;
                    }
                }
                if (_pending.Count >= Capacity)
                {
                    _dropped++;
                    return;
                }
                WorkItem item = new WorkItem();
                item.Label = label ?? "ui";
                item.Key = key;
                item.Action = action;
                _pending.Enqueue(item);
                if (key != null) _byKey[key] = item;
                _posted++;
            }
            EnsurePump();
        }

        /// <summary>
        /// Run on the UI thread and wait for the result. Only for short, non-nested work:
        /// calling this from the UI thread itself executes inline, which is what Invoke does.
        /// </summary>
        public void Send(Action action)
        {
            if (action == null) return;
            if (OnUiThread) { Execute(action, "send"); return; }

            // Posted-and-awaited rather than Control.Invoke: Invoke on a form whose handle is
            // gone throws, and on a busy pump it can deadlock the caller. A bounded wait gives
            // the caller an answer either way.
            ManualResetEventSlim done = new ManualResetEventSlim(false);
            Post("send-wait", null, delegate
            {
                try { action(); }
                finally { done.Set(); }
            });
            if (!done.Wait(2000)) ReportUnhandled("send", new TimeoutException("UI did not run the action within 2 s"));
        }

        /// <summary>Queue one log line. Sinks receive a newline-joined batch once per frame.</summary>
        public void Log(Action<string> sink, string line)
        {
            if (sink == null || string.IsNullOrEmpty(line)) return;
            lock (_gate)
            {
                if (_logs.Count >= Capacity) { _dropped++; return; }
                LogItem item = new LogItem();
                item.Sink = sink;
                item.Line = line;
                _logs.Add(item);
                _logLines++;
            }
            EnsurePump();
        }

        // -- the centralized exception boundary ---------------------------------

        /// <summary>
        /// Start an async operation whose failure must never reach the process. This is the
        /// replacement for `async void`: the body returns a Task, and its exception is
        /// reported through <see cref="ErrorSink"/> instead of being rethrown on the pool.
        /// </summary>
        public void Guard(string label, Func<Task> body)
        {
            if (body == null) return;
            Task task;
            try
            {
                task = body();
            }
            catch (Exception ex)
            {
                ReportUnhandled(label, ex);
                return;
            }
            if (task == null) return;
            task.ContinueWith(delegate(Task t)
            {
                // OnlyOnFaulted: the delegate still sees a faulted task, so unwrap here.
                ReportUnhandled(label, t.Exception);
            }, CancellationToken.None,
               TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously,
               TaskScheduler.Default);
        }

        /// <summary>Synchronous variant of <see cref="Guard"/> for event handlers.</summary>
        public void Guard(string label, Action body)
        {
            if (body == null) return;
            try { body(); }
            catch (Exception ex) { ReportUnhandled(label, ex); }
        }

        public static void ReportUnhandled(string label, Exception error)
        {
            if (error == null) return;
            AggregateException aggregate = error as AggregateException;
            if (aggregate != null) error = aggregate.Flatten().InnerException;
            string text = "[UI] unhandled " + (label ?? "task") + ": "
                + error.GetType().Name + ": " + error.Message;
            try { Trace.WriteLine(text); } catch { }
            try
            {
                Action<string> sink = ErrorSink;
                if (sink != null) sink(text);
            }
            catch { }
        }

        // -- draining -----------------------------------------------------------

        /// <summary>Runs whatever is queued now. Called by the pump once per frame.</summary>
        public void Drain()
        {
            try
            {
                DrainWork();
                DrainLogs();
                Interlocked.Increment(ref _frames);
                Interlocked.Exchange(ref _lastFrameTicks, DateTime.UtcNow.Ticks);
            }
            catch (Exception ex)
            {
                Interlocked.Increment(ref _faults);
                ReportUnhandled("drain", ex);
            }
        }

        private void DrainWork()
        {
            Stopwatch clock = Stopwatch.StartNew();
            while (clock.ElapsedMilliseconds < WorkBudgetMs)
            {
                WorkItem item;
                lock (_gate)
                {
                    if (_pending.Count == 0) return;
                    item = _pending.Dequeue();
                    if (item.Key != null) _byKey.Remove(item.Key);
                }
                Execute(item.Action, item.Label);
                Interlocked.Increment(ref _drained);
            }
        }

        private void DrainLogs()
        {
            if (!HasQueuedLogs()) return;
            List<LogItem> items;
            lock (_gate)
            {
                if (_logs.Count == 0) return;
                items = new List<LogItem>(_logs);
                _logs.Clear();
            }

            // Consecutive lines for the same sink travel as one call. A directive that logs
            // 200 lines therefore costs one sink call, not 200, and the sink keeps the order
            // the producer wrote them in.
            int start = 0;
            while (start < items.Count)
            {
                Action<string> sink = items[start].Sink;
                int end = start + 1;
                while (end < items.Count && ReferenceEquals(items[end].Sink, sink)) end++;

                _batch.Clear();
                for (int i = start; i < end; i++) _batch.Add(items[i].Line);
                string text = string.Join("\n", _batch.ToArray());
                try { sink(text); }
                catch (Exception ex) { ReportUnhandled("log-sink", ex); }
                Interlocked.Increment(ref _logBatches);
                start = end;
            }
        }

        private bool HasQueuedLogs()
        {
            lock (_gate) { return _logs.Count > 0; }
        }

        private void Execute(Action action, string label)
        {
            try { action(); }
            catch (Exception ex)
            {
                Interlocked.Increment(ref _faults);
                ReportUnhandled(label, ex);
            }
        }

        private void EnsurePump()
        {
            if (_pump != null || _uiThreadId == 0) return;
            // No pump yet: the UI thread will call Start() during Capture()/form construction.
            // Until then nothing may block a background producer.
        }

        // -- diagnostics --------------------------------------------------------

        /// <summary>One line an operator can paste when the UI feels "slow".</summary>
        public string StatsLine()
        {
            lock (_gate)
            {
                return string.Format(
                    "[UI] queued={0} coalesced={1} posted={2} drained={3} dropped={4} "
                    + "logLines={5} logBatches={6} frames={7} faults={8} hz={9}",
                    _pending.Count, _byKey.Count, Interlocked.Read(ref _posted),
                    Interlocked.Read(ref _drained), Interlocked.Read(ref _dropped),
                    Interlocked.Read(ref _logLines), Interlocked.Read(ref _logBatches),
                    Interlocked.Read(ref _frames), Interlocked.Read(ref _faults),
                    1000 / Math.Max(1, FrameMilliseconds));
            }
        }

        public long Dropped { get { return Interlocked.Read(ref _dropped); } }
        public long Pending { get { lock (_gate) { return _pending.Count; } } }

        private static int FrameIntervalFromEnvironment()
        {
            string raw = null;
            try { raw = Environment.GetEnvironmentVariable("DOFUS_UI_HZ"); } catch { }
            int hz;
            if (!string.IsNullOrEmpty(raw) && int.TryParse(raw, out hz))
            {
                if (hz >= 60) return 16;
                if (hz >= 30) return 33;
                if (hz >= 20) return 50;
                if (hz > 0) return Math.Max(16, 1000 / hz);
            }
            return 33;
        }
    }
}
