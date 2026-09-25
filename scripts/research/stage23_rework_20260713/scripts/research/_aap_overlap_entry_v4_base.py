from __future__ import annotations

from run_stage3_aap_tradecount_factor_v3_host import *  # noqa: F401,F403
import run_stage3_aap_tradecount_factor_v3_host as _module
for _key, _value in vars(_module).items():
    if not (_key.startswith("__") and _key.endswith("__")):
        globals()[_key] = _value
