#!/usr/bin/env python3
"""Code complexity reports: cyclomatic complexity estimates for key modules.

Uses only the standard library (ast) to estimate complexity without external deps.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Modules to analyze for complexity
MODULES = [
    "networkhealth/clean.py",
    "networkhealth/features.py",
    "networkhealth/splits.py",
    "networkhealth/baselines.py",
    "networkhealth/evaluation.py",
    "networkhealth/model.py",
    "networkhealth/trm_moe.py",
    "networkhealth/identity_mask.py",
    "networkhealth/inference/runtime.py",
    "networkhealth/cli.py",
    "networkhealth/training.py",
    "networkhealth/bridge.py",
    "combat/damage_calculator.py",
    "ai/dofus_native_model.py",
    "networkhealth/sentinel.py",
]


def _cyclomatic_complexity(source: str) -> dict:
    """Estimate cyclomatic complexity per function using AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"error": "syntax_error"}

    results = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity = 1  # base
            for child in ast.walk(node):
                if isinstance(child, (ast.If, ast.IfExp)):
                    complexity += 1
                elif isinstance(child, (ast.For, ast.AsyncFor)):
                    complexity += 1
                elif isinstance(child, ast.While):
                    complexity += 1
                elif isinstance(child, (ast.ExceptHandler,)):
                    complexity += 1
                elif isinstance(child, (ast.And, ast.Or)):
                    complexity += 1
                elif isinstance(child, (ast.Assert,)):
                    complexity += 1
                elif isinstance(child, (ast.comprehension,)):
                    complexity += 1
            results[node.name] = complexity
    return results


class TestComplexity(unittest.TestCase):
    def test_all_modules_parse(self):
        for rel in MODULES:
            path = REPO_ROOT / rel
            self.assertTrue(path.exists(), f"missing module: {rel}")
            source = path.read_text(encoding="utf-8")
            try:
                ast.parse(source)
            except SyntaxError as e:
                self.fail(f"{rel} has a syntax error: {e}")

    def test_complexity_reported(self):
        """Report complexity for every function; flag functions > 15."""
        high_complexity = []
        for rel in MODULES:
            path = REPO_ROOT / rel
            source = path.read_text(encoding="utf-8")
            funcs = _cyclomatic_complexity(source)
            if "error" in funcs:
                continue
            for name, cc in funcs.items():
                if cc > 15:
                    high_complexity.append((rel, name, cc))

        # Print the report (also captured by test runner output)
        print("\n" + "=" * 70)
        print("CYCLOMATIC COMPLEXITY REPORT (functions with CC > 15)")
        print("=" * 70)
        if not high_complexity:
            print("  No functions exceed complexity threshold of 15.")
        else:
            for rel, name, cc in sorted(high_complexity, key=lambda x: -x[2]):
                print(f"  {cc:3d}  {rel}::{name}")
        print("=" * 70 + "\n")

        # We don't fail on complexity — we just report it.
        # But functions over 25 are flagged as warnings.
        critical = [(r, n, c) for r, n, c in high_complexity if c > 25]
        if critical:
            print("WARNING: functions with CC > 25 should be refactored:")
            for r, n, c in critical:
                print(f"  {c:3d}  {r}::{n}")

    def test_no_function_excessively_complex(self):
        """Hard gate: no function should exceed CC 75 (large dispatchers are OK)."""
        for rel in MODULES:
            path = REPO_ROOT / rel
            source = path.read_text(encoding="utf-8")
            funcs = _cyclomatic_complexity(source)
            for name, cc in funcs.items():
                self.assertLess(cc, 75,
                                f"{rel}::{name} has cyclomatic complexity {cc} (>75)")


if __name__ == "__main__":
    unittest.main()