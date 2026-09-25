from __future__ import annotations

from run_stage3_official_2sym import *  # noqa: F401,F403
import run_stage3_official_2sym as _module
for _key, _value in vars(_module).items():
    if not (_key.startswith("__") and _key.endswith("__")):
        globals()[_key] = _value
