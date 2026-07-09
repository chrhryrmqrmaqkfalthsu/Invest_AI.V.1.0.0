#!/usr/bin/env python3
"""Ensure investbot.duckdns.org routes dashboard paths to the 8001 dashboard app.

Why this exists:
- /etc/caddy/Caddyfile is root-owned in the current environment.
- The permanent Caddyfile currently proxies everything to localhost:8000.
- The KINGMAKER dashboard is served by api_server_candidate_only on localhost:8001.

This script reapplies the runtime Caddy Admin API route split whenever Caddy
restarts or reloads from the old Caddyfile. It is safe to run repeatedly.
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ADMIN_ROUTES_URL = "http://127.0.0.1:2019/config/apps/http/servers/srv0/routes/0/handle/0/routes"
DASHBOARD_URL = "https://investbot.duckdns.org/dashboard"
LIVE_SLOTS_URL = "https://investbot.duckdns.org/api/live/slots"

DASHBOARD_PATHS = [
    "/",
    "/dashboard*",
    "/dashboard_home.html",
    "/dashboard_live.html",
    "/dashboard.html",
    "/api/live",
    "/api/live/*",
    "/api/real",
    "/api/real/*",
    "/api/tickers",
    "/api/trades/*",
    "/elite-shadow",
    "/elite-strategy-sim",
]

DESIRED_ROUTES: list[dict[str, Any]] = [
    {
        "match": [{"path": DASHBOARD_PATHS}],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "localhost:8001"}],
            }
        ],
    },
    {
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": "localhost:8000"}],
            }
        ],
    },
]


def _request(method: str, url: str, data: bytes | None = None, timeout: float = 8.0) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status), resp.read()


def _read_routes() -> list[dict[str, Any]] | None:
    try:
        status, raw = _request("GET", ADMIN_ROUTES_URL, timeout=5.0)
        if status != 200:
            return None
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, list) else None
    except Exception:
        return None


def _route_dial(route: dict[str, Any]) -> str:
    try:
        return str(route["handle"][0]["upstreams"][0]["dial"])
    except Exception:
        return ""


def _routes_already_ok(routes: list[dict[str, Any]] | None) -> bool:
    if not routes or len(routes) < 2:
        return False
    first = routes[0]
    second = routes[1]
    paths = set((first.get("match") or [{}])[0].get("path") or [])
    return (
        _route_dial(first) == "localhost:8001"
        and _route_dial(second) == "localhost:8000"
        and set(DASHBOARD_PATHS).issubset(paths)
    )


def _patch_routes() -> None:
    payload = json.dumps(DESIRED_ROUTES, separators=(",", ":")).encode("utf-8")
    _request("PATCH", ADMIN_ROUTES_URL, data=payload, timeout=8.0)


def _https_get(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return int(resp.status), resp.read()


def _verify() -> None:
    status, body = _https_get(DASHBOARD_URL, timeout=10.0)
    if status != 200 or b"KINGMAKER" not in body or b"const API=window.location.origin" not in body:
        raise RuntimeError(f"dashboard verification failed: status={status}, body_len={len(body)}")
    status, body = _https_get(LIVE_SLOTS_URL, timeout=12.0)
    if status != 200 or not body.lstrip().startswith(b"["):
        raise RuntimeError(f"live slots verification failed: status={status}, body_prefix={body[:80]!r}")


def main() -> int:
    for attempt in range(1, 4):
        routes = _read_routes()
        if not _routes_already_ok(routes):
            _patch_routes()
        try:
            _verify()
            print(f"OK caddy dashboard route active on attempt {attempt}")
            return 0
        except Exception as exc:
            if attempt >= 3:
                print(f"FAILED caddy dashboard route verification: {exc}", file=sys.stderr)
                return 1
            time.sleep(3)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
