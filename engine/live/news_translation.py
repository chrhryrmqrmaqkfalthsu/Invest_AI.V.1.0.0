"""Korean translation cache for dashboard news snippets.

This module is deliberately optional and cache-first:
- no API key -> keep English text and mark translation_source=disabled_no_key
- cache hit -> no network call
- GPT failure -> keep English text and mark translation_source=fallback_error

The .env file is read only through python-dotenv at runtime; values are never
printed or returned by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "_system" / "holding_news_translation_cache.json"
DEFAULT_MODEL = "gpt-4o-mini"


def _clean(text: Any, limit: int = 500) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _bool_env(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        try:
            env = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
            raw = str(env.get(name) or "").strip().lower()
        except Exception:
            raw = ""
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y"}


def _env_value(name: str, default: str = "") -> str:
    raw = str(os.getenv(name) or "").strip()
    if raw:
        return raw
    try:
        env = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
        return str(env.get(name) or default or "").strip()
    except Exception:
        return default


def _load_cache(path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("schema_version", 1)
            data.setdefault("entries", {})
            if not isinstance(data.get("entries"), dict):
                data["entries"] = {}
            return data
    except Exception:
        pass
    return {"schema_version": 1, "entries": {}}


def _atomic_write_cache(data: dict[str, Any], path: Path = DEFAULT_CACHE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def article_cache_key(article: dict[str, Any]) -> str:
    raw = "|".join([
        str(article.get("url") or ""),
        str(article.get("ticker") or ""),
        str(article.get("published_at") or article.get("published_raw") or ""),
        str(article.get("title") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _fallback_article(article: dict[str, Any], source: str) -> dict[str, Any]:
    out = dict(article)
    title = _clean(out.get("title"), 180)
    summary = _clean(out.get("summary"), 260)
    out.setdefault("title_en", title)
    out.setdefault("summary_en", summary)
    out.setdefault("title_ko", title)
    out.setdefault("summary_ko", summary)
    out["translated"] = False
    out["translation_source"] = source
    return out


def _parse_translation_payload(text: str) -> tuple[str, str]:
    data = json.loads(text)
    title_ko = _clean(data.get("title_ko"), 120)
    summary_ko = _clean(data.get("summary_ko"), 180)
    if not title_ko or not summary_ko:
        raise ValueError("translation response missing title_ko/summary_ko")
    return title_ko, summary_ko


def _call_openai_translation(article: dict[str, Any], *, api_key: str, model: str) -> tuple[str, str]:
    from openai import OpenAI

    title = _clean(article.get("title"), 240)
    summary = _clean(article.get("summary"), 650)
    source = _clean(article.get("source"), 80)
    ticker = _clean(article.get("ticker"), 16)
    published_at = _clean(article.get("published_at") or article.get("published_raw"), 40)
    client = OpenAI(api_key=api_key, timeout=12.0)
    res = client.chat.completions.create(
        model=model,
        temperature=0.0,
        max_tokens=220,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 투자 대시보드용 뉴스 번역기다. "
                    "원문 의미를 보존하되 한국어로 짧고 즉시 이해되게 요약한다. "
                    "투자 조언을 추가하지 말고, 기사에 없는 내용은 만들지 마라. "
                    "반드시 JSON만 출력하라: {\"title_ko\":..., \"summary_ko\":...}."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"티커: {ticker}\n"
                    f"출처: {source}\n"
                    f"발행시각: {published_at}\n"
                    f"제목: {title}\n"
                    f"요약: {summary}\n\n"
                    "요구사항:\n"
                    "- title_ko: 45자 안팎의 한국어 제목\n"
                    "- summary_ko: 한 문장, 90자 안팎\n"
                    "- 숫자/기업명/티커는 유지"
                ),
            },
        ],
    )
    content = res.choices[0].message.content or ""
    return _parse_translation_payload(content)


def translate_article_to_ko(
    article: dict[str, Any],
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    force: bool = False,
) -> dict[str, Any]:
    """Attach Korean dashboard fields to one article.

    The visible dashboard fields intentionally become Korean by default:
    - title, summary are replaced with Korean text when GPT succeeds
    - title_en, summary_en preserve the original English text
    """
    if not isinstance(article, dict):
        return {}
    title_en = _clean(article.get("title"), 180)
    summary_en = _clean(article.get("summary"), 260)
    base = dict(article)
    base["title_en"] = title_en
    base["summary_en"] = summary_en

    enabled = _bool_env("NEWS_TRANSLATION_ENABLED", True)
    api_key = _env_value("OPENAI_API_KEY")
    if not enabled:
        return _fallback_article(base, "disabled_by_env")
    if not api_key:
        return _fallback_article(base, "disabled_no_key")
    # Prevent tests from accidentally charging external APIs unless a test explicitly forces it.
    if os.getenv("PYTEST_CURRENT_TEST") and not force:
        return _fallback_article(base, "disabled_pytest")

    key = article_cache_key(base)
    cache = _load_cache(cache_path)
    entries = cache.setdefault("entries", {})
    cached = entries.get(key) if isinstance(entries, dict) else None
    if isinstance(cached, dict) and cached.get("title_ko") and cached.get("summary_ko"):
        out = dict(base)
        out["title_ko"] = _clean(cached.get("title_ko"), 140)
        out["summary_ko"] = _clean(cached.get("summary_ko"), 220)
        out["title"] = out["title_ko"]
        out["summary"] = out["summary_ko"]
        out["translated"] = True
        out["translation_source"] = cached.get("source") or "cache"
        return out

    model = _env_value("NEWS_TRANSLATION_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    try:
        title_ko, summary_ko = _call_openai_translation(base, api_key=api_key, model=model)
    except Exception as exc:
        out = _fallback_article(base, "fallback_error")
        out["translation_error"] = str(exc)[:160]
        return out

    row = {
        "title_ko": title_ko,
        "summary_ko": summary_ko,
        "title_en": title_en,
        "summary_en": summary_en,
        "source": f"openai:{model}",
        "translated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": base.get("ticker", ""),
        "published_at": base.get("published_at", ""),
        "url": base.get("url", ""),
    }
    entries[key] = row
    cache["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write_cache(cache, cache_path)

    out = dict(base)
    out["title_ko"] = title_ko
    out["summary_ko"] = summary_ko
    out["title"] = title_ko
    out["summary"] = summary_ko
    out["translated"] = True
    out["translation_source"] = row["source"]
    return out


def translate_articles_for_dashboard(articles: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = articles[: limit or len(articles)] if isinstance(articles, list) else []
    return [translate_article_to_ko(row) for row in rows]
