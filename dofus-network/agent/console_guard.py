"""Make a dead stdout harmless, because a diagnostic print must never stop a loop.

The host starts the brain daemon with ``RedirectStandardOutput = true`` and drains
the pipe with ``BeginOutputReadLine``. The daemon is a *singleton*: it outlives the
host that spawned it (the next host attaches with "attached to the brain daemon
already on port 8895"). When the spawning host exits, the read end of that pipe
closes -- and on Windows every later write to it fails with

    OSError: [Errno 22] Invalid argument

Measured 2026-11-13 against a daemon 9.7 h old (pid 13592):

    /brain/status -> dofus-02 | state=error | alive=False
                     error: OSError: [Errno 22] Invalid argument
                     traceback: agent/autopilot.py:4719 in _base_policy
                                print(f"[autopilot] policy: {planner.note}; ...")

so the policy could not be built and **no loop ever started** -- while the host
logged "auto started on the brain daemon" and the row correctly said AI OFF. The
operator saw exactly that disagreement: "the ai is running while its shows its not
running". One `print` of a startup note took the whole run down.

The fix is not to stop printing: those lines are how the operator reads a run. It
is to make the *stream* unable to raise. ``install()`` wraps sys.stdout/sys.stderr
in a writer that keeps the last lines in memory and swallows OSError/ValueError. A
live pipe passes through byte for byte, so nothing a working host sees changes.
"""

import sys

# How many lines of swallowed output to keep. The host logs the tail when the pipe
# is found broken, which is the only way to read what the daemon said while it had
# nowhere to say it.
_TAIL_LINES = 40

_MARKER = "_dofus_console_guard"


class GuardedStream(object):
    """A text stream that never raises on write/flush.

    Deliberately minimal: it forwards everything a normal caller touches and
    records the last lines. ``broken`` becomes true the first time the real
    stream refuses a write, and stays true -- a stream that failed once is not
    trusted again, because the failure mode is a closed handle, not a hiccup.
    """

    def __init__(self, real, name="stdout"):
        self._real = real
        self._name = name
        self._tail = []
        self.broken = False
        self.swallowed = 0

    # -- the contract every caller relies on ------------------------------

    def write(self, text):
        if text is None:
            return 0
        try:
            text = str(text)
        except Exception:  # noqa: BLE001 - an unprintable object must not raise either
            text = "<unprintable>"
        if not self.broken:
            try:
                written = self._real.write(text)
                self._remember(text)
                return len(text) if written is None else written
            except (OSError, ValueError):
                self.broken = True
        self.swallowed += 1
        self._remember(text)
        return len(text)

    def flush(self):
        if self.broken:
            return
        try:
            self._real.flush()
        except (OSError, ValueError):
            self.broken = True

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    # -- introspection the rest of the runtime pokes at --------------------

    def isatty(self):
        return False

    def fileno(self):
        # subprocess asks for this; a broken handle must answer with the honest
        # exception rather than a bogus number that the OS will reject later.
        if self.broken:
            raise OSError("console guard: %s is broken" % self._name)
        return self._real.fileno()

    def readable(self):
        return False

    def writable(self):
        return True

    def seekable(self):
        return False

    @property
    def closed(self):
        return False

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8") or "utf-8"

    @property
    def errors(self):
        return getattr(self._real, "errors", "replace") or "replace"

    def reconfigure(self, **kwargs):
        # logging.StreamHandler calls this when it adopts sys.stderr.
        target = getattr(self._real, "reconfigure", None)
        if target is None:
            return
        try:
            target(**kwargs)
        except (OSError, ValueError):
            self.broken = True

    def close(self):
        # Never close the real stream: it may be shared with the parent's console.
        self.flush()

    # -- bookkeeping -------------------------------------------------------

    def _remember(self, text):
        if not text:
            return
        for line in str(text).splitlines():
            if not line.strip():
                continue
            self._tail.append(line[:400])
            if len(self._tail) > _TAIL_LINES:
                del self._tail[0:len(self._tail) - _TAIL_LINES]

    def tail(self, count=12):
        if count <= 0:
            return []
        return list(self._tail[-count:])

    def status(self):
        return {
            "name": self._name,
            "broken": bool(self.broken),
            "swallowed": self.swallowed,
            "tail": self.tail(),
        }


def install(streams=("stdout", "stderr")):
    """Wrap the named std streams. Idempotent; returns the guards installed.

    Called before the daemon imports anything that prints, because the first
    ``print`` in a module-level or constructor path is exactly what used to
    abort a start.
    """
    installed = []
    for name in streams:
        current = getattr(sys, name, None)
        if isinstance(current, GuardedStream):
            installed.append(current)
            continue
        if current is None:
            # A pythonw/console-less start leaves sys.stdout as None, and every
            # print() then raises AttributeError. A guard is still the answer.
            guard = GuardedStream(_NullWriter(), name)
            guard.broken = True
        else:
            guard = GuardedStream(current, name)
        setattr(sys, name, guard)
        setattr(sys, _MARKER, True)
        installed.append(guard)
    return installed


def status():
    """What the guards know, for /brain/health. Empty when not installed."""
    out = {}
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if isinstance(stream, GuardedStream):
            out[name] = stream.status()
    return out


def is_broken():
    return any(row.get("broken") for row in status().values())


class _NullWriter(object):
    """Stand-in for a process started with no console at all."""

    encoding = "utf-8"
    errors = "replace"

    def write(self, text):
        return len(text or "")

    def flush(self):
        return None

    def fileno(self):
        raise OSError("console guard: no console")
