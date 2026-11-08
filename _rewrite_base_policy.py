path = r"c:\Users\sao\Documents\dofus-emu\dofus-network\agent\autopilot.py"
with open(path, "r") as f:
    src = f.read()

old = '''def _base_policy(args: argparse.Namespace):
    if args.policy == "rule":
        return RulePolicy()
    # "auto" is what /brain/start and the panel send when nobody chose a policy.
    # It now tries the trained encoder first (the NPL prompt->embedding model),
    # falling back to the learned planner, then the deterministic rule policy.
    # Every failure is logged so the operator can see which branch fired.
    if args.policy in ("auto", "encoder", "planner"):
        # 1. Try the encoder: torch checkpoint first (full precision, what was
        #    trained), then the INT8 ONNX export (deployment, no torch needed).
        if args.policy in ("auto", "encoder"):
            try:
                from ai.encoder_serve import EncoderToolPolicy
                encoder_dir = REPO_ROOT / "models" / "encoder30_v15"
                checkpoint = encoder_dir / "model.pt"
                onnx = encoder_dir / "encoder_int8.onnx"
                tokenizer = encoder_dir / "tokenizer.json"
                policy_json = encoder_dir / "policy.json"
                if checkpoint.is_file() and tokenizer.is_file() and policy_json.is_file():
                    print(f"[autopilot] policy: encoder (torch checkpoint, {encoder_dir.name})")
                    return EncoderToolPolicy(
                        checkpoint=checkpoint,
                        tokenizer=tokenizer,
                        policy_json=policy_json,
                    )
                if onnx.is_file() and tokenizer.is_file() and policy_json.is_file():
                    print(f"[autopilot] policy: encoder (ONNX INT8, {encoder_dir.name})")
                    return EncoderToolPolicy(
                        onnx=onnx,
                        tokenizer=tokenizer,
                        policy_json=policy_json,
                    )
                print(f"[autopilot] policy: encoder artifacts incomplete in {encoder_dir}, skipping")
            except Exception as exc:  # noqa: BLE001 - encoder must not crash the loop
                print(f"[autopilot] policy: encoder unavailable ({type(exc).__name__}: {exc}), falling through")
        # 2. Try the learned planner (falls back to deterministic without a model).
        if args.policy in ("auto", "planner"):
            from agent.planner_nn import NNPlanner
            planner = NNPlanner(level_hint=int(args.level_hint), store=None,
                                stack_mode=args.ensemble)
            print(f"[autopilot] policy: {planner.note}; {planner.stack_note}")
            return planner
    return RulePolicy()'''

new = '''def _base_policy(args: argparse.Namespace):
    if args.policy == "rule":
        return RulePolicy()
    # "auto" is what /brain/start and the panel send when nobody chose a policy.
    # It now tries the trained encoder first (the NPL prompt->embedding model,
    # models/encoder30_v15), falling back to the learned planner, then the
    # deterministic rule policy. Every failure is logged so the operator can see
    # which branch fired.
    if args.policy in ("auto", "encoder", "planner"):
        # 1. Try the encoder: INT8 ONNX first (deployment path, loads on the
        #    embedded runtime without torch/jax), then the torch checkpoint
        #    (full precision, what was trained -- may mismatch the current
        #      schema and fall through).
        if args.policy in ("auto", "encoder"):
            try:
                from ai.encoder_serve import EncoderToolPolicy
                encoder_dir = REPO_ROOT / "models" / "encoder30_v15"
                onnx = encoder_dir / "encoder_int8.onnx"
                checkpoint = encoder_dir / "model.pt"
                tokenizer = encoder_dir / "tokenizer.json"
                policy_json = encoder_dir / "policy.json"
                if onnx.is_file() and tokenizer.is_file() and policy_json.is_file():
                    print(f"[autopilot] policy: encoder (ONNX INT8, {encoder_dir.name})")
                    return EncoderToolPolicy(
                        onnx=onnx,
                        tokenizer=tokenizer,
                        policy_json=policy_json,
                    )
                if checkpoint.is_file() and tokenizer.is_file() and policy_json.is_file():
                    print(f"[autopilot] policy: encoder (torch checkpoint, {encoder_dir.name})")
                    return EncoderToolPolicy(
                        checkpoint=checkpoint,
                        tokenizer=tokenizer,
                        policy_json=policy_json,
                    )
                print(f"[autopilot] policy: encoder artifacts incomplete in {encoder_dir}, skipping")
            except Exception as exc:  # noqa: BLE001 - encoder must not crash the loop
                print(f"[autopilot] policy: encoder unavailable ({type(exc).__name__}: {exc}), falling through")
        # 2. Try the learned planner (falls back to deterministic without a model).
        if args.policy in ("auto", "planner"):
            from agent.planner_nn import NNPlanner
            planner = NNPlanner(level_hint=int(args.level_hint), store=None,
                                stack_mode=args.ensemble)
            print(f"[autopilot] policy: {planner.note}; {planner.stack_note}")
            return planner
    return RulePolicy()'''

if old not in src:
    print("ERROR: target block not found in autopilot.py")
    print("Searching for partial matches...")
    for token in ["def _base_policy", "args.policy in (\"auto\", \"encoder\", \"planner\")", "EncoderToolPolicy("]:
        if token in src:
            print(f"  found: {token[:60]}")
        else:
            print(f"  missing: {token[:60]}")
    raise SystemExit(1)

src = src.replace(old, new)
with open(path, "w") as f:
    f.write(src)
print("Rewrote _base_policy: auto tries ONNX encoder first, then torch checkpoint, then planner, then rule")
