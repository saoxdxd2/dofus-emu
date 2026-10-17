import os
import sys
import yaml
import time
from eval_thx01 import classify

def run():
    config_path = os.path.join(os.path.dirname(__file__), "promptfooconfig.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tests = config.get("tests", [])
    print("=" * 80)
    print(f"🚀 RUNNING THX-01 PROMPTFOO BENCHMARK SUITE ({len(tests)} Test Cases)")
    print(f"   Config: {config_path}")
    print("=" * 80)

    start_time = time.time()
    passed = 0
    failed = 0
    results = []

    for idx, test in enumerate(tests, 1):
        desc = test.get("description", f"Test {idx}")
        query = test.get("vars", {}).get("query", "")
        assertions = test.get("assert", [])
        expected = assertions[0].get("value") if assertions else None

        res = classify(query)
        pred = res["label"]
        conf = res["confidence"]

        is_pass = (pred == expected)
        if is_pass:
            passed += 1
            status_str = "✅ PASS"
        else:
            failed += 1
            status_str = "❌ FAIL"

        results.append({
            "idx": idx,
            "description": desc,
            "query": query,
            "expected": expected,
            "predicted": pred,
            "confidence": conf,
            "passed": is_pass
        })

        q_disp = query if len(query) <= 45 else query[:42] + "..."
        print(f"[{idx:02d}/{len(tests):02d}] {status_str} | Exp: {expected:<16} | Pred: {pred:<16} ({conf*100:5.1f}%) | '{q_disp}'")

    elapsed = time.time() - start_time
    total = len(tests)
    pass_rate = (passed / total) * 100 if total > 0 else 0

    print("=" * 80)
    print(f"📊 BENCHMARK SUMMARY: {passed}/{total} Passed ({pass_rate:.1f}%) in {elapsed:.2f}s")
    if failed == 0:
        print("🎉 PERFECT CERTIFICATION: THX-01 strictly passes all operational & sentinel constraints!")
    else:
        print(f"⚠️ {failed} test cases failed! Review details above.")
    print("=" * 80)

    return failed == 0

if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
