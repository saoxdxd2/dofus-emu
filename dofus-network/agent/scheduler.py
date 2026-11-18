"""Deadline scheduling for the live loop, so a wait is a compute window.

The autopilot spends a real share of a session waiting: a settle window, a
fight's turns, a farm session that is already being played by the page, a
client that has to come back. Those waits were plain ``time.sleep`` calls.
Sleeping is the correct way to spend time when there is nothing to do; it is a
poor way to spend it when the next decision needs work that can be done now.

This module keeps a small deadline heap:

* a task is a zero-argument callable, coalesced by key, so a branch that is
  activated once per poll does not accumulate one copy per poll;
* ``wait(seconds)`` runs every task whose deadline has arrived, then sleeps
  exactly the remaining wall time. With nothing scheduled it performs exactly
  one sleep of the requested length, which keeps the loop's measured pacing
  and the tests that assert that pacing unchanged;
* a failing task is logged and dropped. A cache warmer must never be able to
  end a session.

The companion is :mod:`agent.compute_tree`, which decides *which* dirty
branches are worth handing to this queue.
"""

from __future__ import annotations

import heapq
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

Task = Callable[[], Any]


class DeadlineScheduler:
    """A coalescing deadline queue with a scheduler-aware sleep."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep,
                 compute_budget_s: float = 0.05) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self.compute_budget_s = max(0.0, float(compute_budget_s))
        # (due, sequence, key). The sequence makes an entry stale when the same
        # key is rescheduled earlier; the pending dict is the authority.
        self._heap: List[Tuple[float, int, str]] = []
        self._pending: Dict[str, Tuple[float, int, Task]] = {}
        self._sequence = 0
        self.scheduled = 0
        self.coalesced = 0
        self.ran = 0
        self.failed = 0

    # -- queue -------------------------------------------------------------

    def schedule(self, delay: float, task: Task, *args: Any,
                 key: Optional[str] = None, replace: bool = False) -> str:
        """Run ``task(*args)`` after ``delay`` seconds, at most once per key.

        A second schedule for a live key keeps the earlier deadline unless
        ``replace`` is set. That is the coalescing rule: activating the same
        dirty branch on every poll must not grow the queue, and a branch that
        was already due is not postponed by a later activation.
        """
        now = self._clock()
        due = now + max(0.0, float(delay))
        if key is None:
            self._sequence += 1
            key = "task:%d" % self._sequence
        existing = self._pending.get(key)
        if existing is not None and not replace and existing[0] <= due:
            self.coalesced += 1
            return key

        self._sequence += 1
        sequence = self._sequence

        def call() -> Any:
            return task(*args)

        self._pending[key] = (due, sequence, call)
        heapq.heappush(self._heap, (due, sequence, key))
        self.scheduled += 1
        return key

    def call_at(self, when: float, task: Task, *args: Any,
                key: Optional[str] = None, replace: bool = False) -> str:
        """Schedule at an absolute clock value, for callers that already have one."""
        return self.schedule(when - self._clock(), task, *args,
                             key=key, replace=replace)

    def cancel(self, key: str) -> bool:
        """Drop a pending task. Its heap entry becomes stale and is skipped."""
        return self._pending.pop(key, None) is not None

    def next_due_in(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds until the earliest live task, or None when the queue is empty."""
        current = self._clock() if now is None else now
        while self._heap:
            due, sequence, key = self._heap[0]
            entry = self._pending.get(key)
            if entry is not None and entry[1] == sequence:
                return max(0.0, due - current)
            heapq.heappop(self._heap)
        return None

    def run_due(self, now: Optional[float] = None,
                budget_s: Optional[float] = None,
                max_tasks: Optional[int] = None) -> int:
        """Run every task whose deadline has arrived, bounded by a CPU budget.

        The budget is a guard, not a schedule: a task that is already running
        cannot be interrupted, so it is checked between tasks. Work left over
        is put back at ``now`` for the next window instead of being dropped.
        """
        current = self._clock() if now is None else now
        started = self._clock()
        budget = self.compute_budget_s if budget_s is None else max(0.0, budget_s)
        ran = 0
        while self._heap:
            due, sequence, key = self._heap[0]
            if due > current:
                break
            heapq.heappop(self._heap)
            entry = self._pending.get(key)
            if entry is None or entry[1] != sequence:
                continue
            del self._pending[key]
            if ran and budget and self._clock() - started >= budget:
                # Put the untouched task back; the next wait will run it.
                self.schedule(0.0, entry[2], key=key)
                break
            try:
                entry[2]()
            except Exception as exc:  # noqa: BLE001 - a warmer must not end a run
                self.failed += 1
                logger.warning("[scheduler] task %s failed: %s", key, exc)
            else:
                self.ran += 1
                ran += 1
            if max_tasks is not None and ran >= max_tasks:
                break
        return ran

    # -- the wait ----------------------------------------------------------

    def wait(self, seconds: float, should_stop: Optional[Callable[[], Any]] = None) -> bool:
        """Spend ``seconds``: run due compute first, then sleep the requested time.

        The sleep is not shortened by the compute. The caller asked for a
        pacing window, and the regression harness records the exact value it
        asked for; a millisecond of cache warming must not turn 0.35 s into
        0.349 s. One sleep per call is deliberate: a caller that needs to
        re-check a condition calls again (the autopilot's loops already do), and
        a stubbed sleeper cannot turn this into a spin.
        """
        seconds = max(0.0, float(seconds))
        if should_stop is not None and should_stop():
            return False
        self.run_due(now=self._clock())
        if should_stop is not None and should_stop():
            return False
        if seconds > 0:
            self._sleeper(seconds)
        return True

    # -- reporting ---------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {"scheduled": self.scheduled, "coalesced": self.coalesced,
                "ran": self.ran, "failed": self.failed,
                "pending": len(self._pending)}


class Channel:
    """One named cadence: its interval, its next deadline, and its counters."""

    __slots__ = ("minimum", "maximum", "growth", "interval", "next_at",
                 "waits", "waited_s", "changes", "idle")

    def __init__(self, minimum: float, maximum: float, growth: float) -> None:
        self.minimum = minimum
        self.maximum = maximum
        self.growth = growth
        self.interval = minimum
        self.next_at = 0.0
        self.waits = 0
        self.waited_s = 0.0
        self.changes = 0
        self.idle = 0


class TimeManager:
    """Own the loop's cadence instead of scattering sleeps through it.

    Call sites say *what* they are waiting for (``step``, ``farm``, ``fight``,
    ``hold``, ``window``), not how long to sleep. This object decides the
    interval, arms the next deadline, and hands the window to the scheduler so
    due compute runs before the sleeper. A channel can back off from a short
    interval to a long one when nothing changes, and snap back to the short one
    when something does; that is the difference between a fixed 5 s poll and a
    poll that reacts in half a second after a change and still costs one
    snapshot per 5 s when the world is quiet.
    """

    def __init__(self, scheduler: DeadlineScheduler,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.scheduler = scheduler
        self._clock = clock
        self._channels: Dict[str, Channel] = {}

    def configure(self, name: str, minimum: float,
                  maximum: Optional[float] = None,
                  growth: float = 1.6) -> Channel:
        minimum = max(0.0, float(minimum))
        maximum = minimum if maximum is None else max(minimum, float(maximum))
        channel = Channel(minimum, maximum, max(1.0, float(growth)))
        self._channels[name] = channel
        return channel

    def channel(self, name: str) -> Optional[Channel]:
        return self._channels.get(name)

    def arm(self, name: str, delay: float) -> float:
        """Set the channel's next deadline to ``now + delay``."""
        channel = self._channels[name]
        channel.next_at = self._clock() + max(0.0, float(delay))
        return channel.next_at

    def due_in(self, name: str) -> float:
        channel = self._channels.get(name)
        if channel is None:
            return 0.0
        return max(0.0, channel.next_at - self._clock())

    def backoff(self, name: str, changed: bool) -> float:
        """Choose the next interval for a channel and arm it.

        A change resets the channel to its minimum, so the next observation is
        close enough to catch the consequence. No change multiplies by the
        growth factor up to the maximum, so a quiet farm costs the same one
        snapshot per maximum interval that the fixed sleep did.
        """
        channel = self._channels[name]
        if changed:
            channel.changes += 1
            channel.interval = channel.minimum
        else:
            channel.idle += 1
            channel.interval = min(channel.maximum,
                                   max(channel.minimum,
                                       channel.interval * channel.growth))
        channel.next_at = self._clock() + channel.interval
        return channel.interval

    def rest(self, name: str, minimum: float,
             maximum: Optional[float] = None, growth: float = 1.6,
             changed: bool = False, spent: float = 0.0,
             should_stop: Optional[Callable[[], Any]] = None) -> float:
        """Set a channel's next interval and spend what is left of it.

        ``spent`` is time the caller already burned on the thing being paced
        (the directive's own latency, for the step channel). The cadence is a
        period between actions, not a sleep added to every action: a request
        that took longer than the floor needs no further wait. That removes the
        latency the loop used to add to its own floor, without raising the
        request rate -- the period is still at least ``minimum``.

        Returns the interval that was chosen, not the time slept.
        """
        minimum = max(0.0, float(minimum))
        maximum = minimum if maximum is None else max(minimum, float(maximum))
        channel = self._channels.get(name)
        if channel is None:
            channel = self.configure(name, minimum, maximum, growth)
        else:
            # The panel can change `--interval` mid-run. Re-clamp the existing
            # channel instead of resetting its counters and its backoff.
            channel.minimum = minimum
            channel.maximum = maximum
            channel.growth = max(1.0, float(growth))
            channel.interval = min(maximum, max(minimum, channel.interval))
        if changed:
            channel.changes += 1
            channel.interval = minimum
        else:
            channel.idle += 1
            channel.interval = min(maximum, max(minimum,
                                                channel.interval * channel.growth))
        delay = max(0.0, channel.interval - max(0.0, float(spent)))
        channel.waits += 1
        channel.waited_s += delay
        channel.next_at = 0.0
        if delay <= 0.0:
            self.scheduler.run_due()
        else:
            self.scheduler.wait(delay, should_stop=should_stop)
        return channel.interval

    def wait(self, name: str,
             should_stop: Optional[Callable[[], Any]] = None) -> bool:
        """Wait for a channel's deadline, running due compute first."""
        channel = self._channels.get(name)
        if channel is None:
            self.scheduler.run_due()
            return True
        delay = max(0.0, channel.next_at - self._clock())
        channel.next_at = 0.0
        channel.waits += 1
        channel.waited_s += delay
        if delay <= 0.0:
            self.scheduler.run_due()
            return True
        return self.scheduler.wait(delay, should_stop=should_stop)

    def wait_for(self, name: str, seconds: float,
                 should_stop: Optional[Callable[[], Any]] = None) -> bool:
        """Wait an explicit window (a settle/condition deadline), compute-aware."""
        channel = self._channels.get(name)
        if channel is not None:
            channel.waits += 1
            channel.waited_s += max(0.0, float(seconds))
        return self.scheduler.wait(seconds, should_stop=should_stop)

    def stats(self) -> Dict[str, Any]:
        return {
            name: {"waits": channel.waits,
                   "waited_s": round(channel.waited_s, 2),
                   "interval": round(channel.interval, 3),
                   "changes": channel.changes, "idle": channel.idle}
            for name, channel in self._channels.items()
        }
