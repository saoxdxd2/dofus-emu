import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import inspect
from ai.dofus_native_model import DofusBrainTransformer
print("DofusBrainTransformer.__init__:", inspect.signature(DofusBrainTransformer.__init__))

from networkhealth.trm_moe import TRMMoEConfig, TRMMoE
print("TRMMoE imported OK:", TRMMoE)

import torch
print("torch:", torch.__version__)
from networkhealth.model import NetworkHealthTCN
print("NetworkHealthTCN imported OK")

from networkhealth.inference import RollingWindow, Observation
print("RollingWindow methods:", [m for m in dir(RollingWindow) if not m.startswith('_')])
