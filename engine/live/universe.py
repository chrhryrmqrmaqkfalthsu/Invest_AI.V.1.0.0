"""Validated live-universe loading shared by startup and hot reload.

The live universe is an approval boundary, not a directory listing.  A ticker is
eligible only when its ``parameters.json`` is parseable and its ticker, market,
asset metadata, direction, and optional promotion id agree.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from engine.live.market_clock import market_region_for_ticker
from engine.strategies.rulebook import Rulebook

DEFAULT_LIVE_PROMOTION_ID = "bo2_promote_85_20260605"
DEFAULT_SYMBOLS_DIR = Path("data/symbols")
VALID_MARKETS = {"US", "KRX"}
VALID_UNIVERSE_MODES = {"promoted", "parameters"}


class LiveUniverseError(RuntimeError):
    """Raised when live-universe configuration or on-disk metadata is unsafe."""


@dataclass(frozen=True)
class LiveUniverseConfig:
    """Immutable policy used for startup and every subsequent hot reload."""

    market: str = "US"
    universe_mode: str = "promoted"
    promotion_id: str | None = DEFAULT_LIVE_PROMOTION_ID
    symbols_dir: Path = DEFAULT_SYMBOLS_DIR

    def normalized(self) -> "LiveUniverseConfig":
        market = normalize_market(self.market)
        mode = str(self.universe_mode or "").strip().lower()
        if mode not in VALID_UNIVERSE_MODES:
            raise LiveUniverseError(
                f"unsupported universe mode {self.universe_mode!r}; expected {sorted(VALID_UNIVERSE_MODES)}"
            )
        promotion_id = str(self.promotion_id or "").strip() or None
        if mode == "promoted" and not promotion_id:
            raise LiveUniverseError("promoted universe requires an exact promotion_id")
        return LiveUniverseConfig(
            market=market,
            universe_mode=mode,
            promotion_id=promotion_id,
            symbols_dir=Path(self.symbols_dir),
        )


@dataclass(frozen=True)
class LiveUniverseResult:
    """Validated symbols plus an audit summary of intentionally excluded dirs."""

    config: LiveUniverseConfig
    symbols: tuple[str, ...]
    scanned_directories: int
    parameter_files: int
    excluded_reason_counts: dict[str, int] = field(default_factory=dict)
    excluded: tuple[dict[str, Any], ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "market": self.config.market,
            "universe_mode": self.config.universe_mode,
            "promotion_id": self.config.promotion_id,
            "scanned_directories": self.scanned_directories,
            "parameter_files": self.parameter_files,
            "eligible": len(self.symbols),
            "excluded_reason_counts": dict(self.excluded_reason_counts),
        }


def normalize_market(value: Any) -> str:
    """Normalize ticker/metadata market labels to the live clock regions."""
    raw = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "US": "US",
        "USA": "US",
        "NYSE": "US",
        "NASDAQ": "US",
        "NYSE/NASDAQ": "US",
        "NASDAQ/NYSE": "US",
        "KR": "KRX",
        "KOREA": "KRX",
        "KRX": "KRX",
        "KOSPI": "KRX",
        "KOSDAQ": "KRX",
    }
    normalized = aliases.get(raw)
    if normalized not in VALID_MARKETS:
        raise LiveUniverseError(f"unsupported or missing market label: {value!r}")
    return normalized


def _currency_region(value: Any) -> str:
    raw = str(value or "").strip().upper()
    mapping = {"USD": "US", "KRW": "KRX"}
    region = mapping.get(raw)
    if region is None:
        raise LiveUniverseError(f"unsupported or missing asset_meta.currency: {value!r}")
    return region


def _asset_type_region(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("us_"):
        return "US"
    if raw.startswith("korean_"):
        return "KRX"
    raise LiveUniverseError(f"unsupported or missing asset_type: {value!r}")


def _read_parameters(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LiveUniverseError(f"invalid parameters JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LiveUniverseError(f"parameters root must be an object: {path}")
    return payload


def _validate_parameters(ticker: str, path: Path, payload: Mapping[str, Any]) -> tuple[str, str | None]:
    """Return normalized region and promotion id after strict cross-validation."""
    asset_meta = payload.get("asset_meta")
    raw_rulebook = payload.get("rulebook")
    if not isinstance(asset_meta, Mapping) or not asset_meta:
        raise LiveUniverseError(f"{ticker}: asset_meta missing or invalid: {path}")
    if not isinstance(raw_rulebook, Mapping) or not raw_rulebook:
        raise LiveUniverseError(f"{ticker}: rulebook missing or invalid: {path}")

    ticker_region = market_region_for_ticker(ticker)
    meta_market_region = normalize_market(asset_meta.get("market"))
    currency_region = _currency_region(asset_meta.get("currency"))
    meta_asset_region = _asset_type_region(asset_meta.get("asset_type"))

    try:
        rulebook = Rulebook.from_dict(dict(raw_rulebook))
    except Exception as exc:
        raise LiveUniverseError(f"{ticker}: Rulebook.from_dict failed: {path}: {exc}") from exc
    rulebook_asset_region = _asset_type_region(rulebook.asset_type)

    if str(rulebook.ticker).strip().upper() != ticker.upper():
        raise LiveUniverseError(
            f"{ticker}: rulebook ticker mismatch: {rulebook.ticker!r} in {path}"
        )
    if str(rulebook.direction).strip().lower() != "long":
        raise LiveUniverseError(
            f"{ticker}: live universe is long-only, got direction={rulebook.direction!r}"
        )

    regions = {
        "ticker": ticker_region,
        "asset_meta.market": meta_market_region,
        "asset_meta.currency": currency_region,
        "asset_meta.asset_type": meta_asset_region,
        "rulebook.asset_type": rulebook_asset_region,
    }
    if len(set(regions.values())) != 1:
        raise LiveUniverseError(f"{ticker}: market metadata mismatch: {regions}")

    promotion = payload.get("promotion")
    if promotion is not None and not isinstance(promotion, Mapping):
        raise LiveUniverseError(f"{ticker}: promotion must be an object when present: {path}")
    promotion_id = None
    if isinstance(promotion, Mapping):
        promotion_id = str(promotion.get("promotion_id") or "").strip() or None
    return ticker_region, promotion_id


def load_live_universe(config: LiveUniverseConfig) -> LiveUniverseResult:
    """Load a strictly validated single-market live universe.

    Metadata inconsistencies are fatal.  Expected policy exclusions such as a
    different market, missing parameters, or a different promotion id are
    reported but do not fail the load.
    """
    config = config.normalized()
    symbols_dir = config.symbols_dir
    if not symbols_dir.exists():
        raise LiveUniverseError(f"symbols directory missing: {symbols_dir}")

    eligible: list[str] = []
    excluded: list[dict[str, Any]] = []
    parameter_files = 0
    directories = sorted(path for path in symbols_dir.iterdir() if path.is_dir())

    for directory in directories:
        ticker = directory.name.strip().upper()
        parameters_path = directory / "parameters.json"
        if not parameters_path.exists():
            excluded.append({"ticker": ticker, "reason": "PARAMETERS_MISSING"})
            continue

        parameter_files += 1
        payload = _read_parameters(parameters_path)
        region, promotion_id = _validate_parameters(ticker, parameters_path, payload)
        if region != config.market:
            excluded.append({"ticker": ticker, "reason": "MARKET_FILTER", "region": region})
            continue
        if config.universe_mode == "promoted" and promotion_id != config.promotion_id:
            excluded.append(
                {
                    "ticker": ticker,
                    "reason": "PROMOTION_ID_MISMATCH",
                    "promotion_id": promotion_id,
                }
            )
            continue
        eligible.append(ticker)

    reason_counts = Counter(row["reason"] for row in excluded)
    return LiveUniverseResult(
        config=config,
        symbols=tuple(sorted(eligible)),
        scanned_directories=len(directories),
        parameter_files=parameter_files,
        excluded_reason_counts=dict(sorted(reason_counts.items())),
        excluded=tuple(sorted(excluded, key=lambda row: (row["reason"], row["ticker"]))),
    )
