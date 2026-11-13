"""The generated contracts: valid, invalid, and byte-identical to the schema.

The point of codegen is that no runtime can drift. This suite checks the Python side
directly, asserts every other target was actually emitted, and runs the generator's
``--check`` so a hand-edit or a stale output fails the gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generated.contracts import (  # noqa: E402
    ContractError, parse_bridge_directive, parse_tool_call,
    validate_bridge_directive, validate_bridge_health, validate_proxy_telemetry,
    validate_tool_call,
)

REPO = ROOT.parent
TARGETS = {
    "csharp": REPO / "desktop" / "generated" / "contracts.cs",
    "go": REPO / "scripts" / "contracts_gen.go",
    "js": REPO / "scripts" / "contracts.generated.js",
    "python": ROOT / "generated" / "contracts.py",
}


def test_directive_nested_id_does_not_win() -> None:
    payload = {"id": "outer", "tool": "nav.goto", "args": {"id": "inner", "tool": "decoy"}}
    assert validate_bridge_directive(payload) == []
    parsed = parse_bridge_directive(payload)
    # The regex extractor this replaced returned `inner` for `id` whenever it won
    # the race; the typed reader takes the envelope's own field, always.
    assert parsed.id == "outer"
    assert parsed.tool == "nav.goto"
    assert parsed.args["id"] == "inner"


def test_directive_rejects_a_missing_id_and_a_wrong_type() -> None:
    assert validate_bridge_directive({"tool": "nav.goto"}) == ["missing required field 'id'"]
    errors = validate_bridge_directive({"id": 7})
    assert errors and errors[0] == "id: expected string"


def test_tool_call_is_closed_and_typed() -> None:
    assert validate_tool_call({"tool": "nav.goto", "args": {}}) == []
    assert validate_tool_call({"tool": "", "args": {}})
    assert validate_tool_call({"tool": "nav.goto", "args": {}, "extra": 1}) == \
        ["unexpected field 'extra'"]
    with_id = parse_tool_call({"tool": "nav.goto", "args": {"x": 1}})
    assert with_id.to_dict() == {"args": {"x": 1}, "tool": "nav.goto"}
    try:
        parse_tool_call({"tool": "nav.goto"})
    except ContractError as exc:
        assert exc.contract == "tool_call"
    else:  # pragma: no cover - the contract must reject this
        raise AssertionError("missing args was accepted")


def test_health_and_telemetry_shapes() -> None:
    health = {"status": "ok", "service": "dofus-agent-bridge", "port": 8894,
              "instances": 2, "time": 1}
    assert validate_bridge_health(health) == []
    assert validate_bridge_health(dict(health, port=0)) == ["port: below minimum 1"]
    assert validate_bridge_health(dict(health, status="fine")) == \
        ["status: must be one of ok, degraded, error"]

    telemetry = {"status": "ok", "port": 8893, "model": "iPhone", "rtt_ms": 1.5,
                 "rtt_avg_ms": 1.25, "sent_bytes": 1, "recv_bytes": 2,
                 "conns_total": 3, "conns_failed": 0, "conns_active": 1, "timestamp": 9}
    assert validate_proxy_telemetry(telemetry) == []


def test_every_target_exists_and_names_the_contract() -> None:
    for name, path in TARGETS.items():
        assert path.is_file(), "missing generated target: %s (%s)" % (name, path)
        text = path.read_text(encoding="utf-8")
        assert "bridge_directive" in text or "BridgeDirective" in text, name
        assert "DO NOT EDIT" in text or "Do not edit" in text, name


def test_codegen_reports_no_drift() -> None:
    script = REPO / "tools" / "codegen_schemas.py"
    completed = subprocess.run([sys.executable, str(script), "--check"],
                               cwd=str(REPO), capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _main() -> int:
    failures = 0
    names = [name for name in sorted(globals()) if name.startswith("test_")]
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d contract checks, %d failed" % (len(names), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
