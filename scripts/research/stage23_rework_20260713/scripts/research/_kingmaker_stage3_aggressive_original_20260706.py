from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().with_name("run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001")
_loader = importlib.machinery.SourceFileLoader(__name__ + "_loaded", str(_PATH))
_spec = importlib.util.spec_from_loader(__name__ + "_loaded", _loader)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load Stage3 original backup: {_PATH}")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
for _key, _value in vars(_module).items():
    if _key not in {"__name__", "__loader__", "__package__", "__spec__"}:
        globals()[_key] = _value
