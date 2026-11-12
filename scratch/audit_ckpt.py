import pickle, json, sys
import numpy as np
sys.path.insert(0, r"C:\Users\sao\Documents\dofus-emu\dofus-network")
o = pickle.load(open(r"C:\Users\sao\Documents\dofus-emu\dofus-network\models\encounter_selfplay.pkl", "rb"))
tot = 0
def walk(prefix, v):
    global tot
    if isinstance(v, dict):
        for k, x in v.items():
            walk(prefix + "/" + str(k), x)
    elif isinstance(v, (list, tuple)):
        for i, x in enumerate(v):
            walk(prefix + "[%d]" % i, x)
    else:
        a = np.asarray(v)
        tot += a.size
        print("  %-46s %-18s %s" % (prefix, a.shape, a.dtype))
walk("params", o["params"])
print("TOTAL SCALARS", tot)
print("config", o["config"])
print("hidden", o["hidden"], "heads", o["heads"], "feat_dim", o["feat_dim"],
      "global_dim", o["global_dim"], "samples", o["samples"])
m = o["metrics"]
print("metrics len", len(m))
print("metrics[0]", json.dumps(m[0]))
print("metrics[-1]", json.dumps(m[-1]))
for key in ("win", "player_win", "reward", "eval_win"):
    vals = [r.get(key) for r in m if isinstance(r, dict) and key in r]
    if vals:
        print("metric", key, "first", vals[0], "last", vals[-1], "max", max(vals))
print("metric keys", sorted(m[-1].keys()) if isinstance(m[-1], dict) else type(m[-1]))