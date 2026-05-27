"""
AI 비서 (Phase F)
================
사용자 자연어 → LLM이 의도 파악 → 필요한 tool 호출 → 데이터 조회 → 답변.

설계:
- OpenAI gpt-4o-mini (function calling)
- Tool은 읽기 전용. 매수/매도 등 액션은 슬래시 명령만.
- 모든 tool은 dict 반환 (LLM에게 JSON으로 전달).
- 대화 컨텍스트 없음 (1 query 1 response). 토큰 절약 + 단순성.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

log = logging.getLogger("ai_assistant")

# ---------- 설정 ----------
MODEL_NAME       = "gpt-4o-mini"
MAX_TOOL_ROUNDS  = 4          # 한 질문당 LLM↔tool 왕복 최대 4회
MAX_OUTPUT_TOK   = 600
TRADE_LOG_PATH   = Path("data/_system/trade_log.csv")
MARKET_STATE     = Path("data/_system/market_state.json")
POSITIONS_PATH   = Path("data/_system/positions.json")
APPROVALS_PATH   = Path("data/_system/approvals.json")

# ---------- 시스템 프롬프트 ----------
SYSTEM_PROMPT = """너는 한국 주식 자동매매 봇 'Kingmaker'의 AI 비서다.

규칙:
1. 사용자가 봇/거래/포지션/시장 관련 질문을 하면 필요한 tool을 호출해서 실제 데이터로 답한다.
2. 데이터가 없거나 비어있으면 "거래 기록 없음" 등 솔직히 답한다. 추측 금지.
3. 답변은 짧고 명확하게. 핵심 숫자는 굵게(*숫자*). 이모지 적당히 (🟢🔴📊🎯).
4. 금액은 천단위 콤마, 수익률은 +/- 부호 + 소수점 2자리.
5. 매수/매도/봇제어(pause/resume/kill) 같은 액션 요청은 거부하고 슬래시 명령 안내.
6. 학습 관련 요청('학습해', '훈련해', '돌려봐')은 start_training 도구로 처리.
   진행 조회는 get_training_status, 취소는 cancel_training.
7. 종목명이 모호하면 resolve_ticker로 확인 후 진행.
8. 모르는 건 모른다고 한다. 잘못된 정보 만들지 않는다.
9. 답변은 텔레그램 메시지로 가니까 마크다운 사용 가능 (*굵게*, `코드`)."""

# ---------- Tool 스키마 (OpenAI function calling 형식) ----------
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_trade_log",
            "description": "체결 완료된 거래 기록 조회 (매수→매도 청산 완료된 것). 수익률/승률/종목별 성과 분석에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_back": {"type": "integer", "description": "오늘부터 며칠 전까지. 7=이번 주, 30=이번 달, 0=전체", "default": 7},
                    "ticker": {"type": "string", "description": "특정 종목코드 (예: 379800). 생략하면 전체"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_positions",
            "description": "현재 보유 중인 포지션 (아직 청산 안 된 종목). 평균가/손익/목표가/손절가/달성률 포함.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "계좌 잔고. 가용 현금 + 보유 종목 평가금 + 총자산.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_context",
            "description": "현재 시장 상황. 점수(0~100), 국면(bull/neutral/bear), KOSPI/S&P500 추세, VIX, 섹터 강도.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_approvals",
            "description": "대기 중인 추가매수 승인 요청. 강한 시그널 떴는데 사용자 응답 기다리는 것들.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rulebook_stats",
            "description": "종목별 학습된 룰북 통계. 승률, fitness, 거래 횟수.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "종목코드 (예: 379800). 생략하면 전체 종목"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_training",
            "description": "지정 종목의 GA 학습 시작. 종목명('코덱스200') 또는 티커('069500') 둘 다 가능. 동시 1개만 가능하며 진행 중이면 거부됨.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker_or_name": {"type": "string", "description": "종목코드 또는 종목명 (예: '069500', '코덱스200', 'KODEX 200')"},
                    "force": {"type": "boolean", "description": "진행 중 학습 강제 취소 후 시작", "default": False},
                },
                "required": ["ticker_or_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_status",
            "description": "현재 진행 중인 학습 상태 조회. 진행률, 세대, fitness 등.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_training",
            "description": "진행 중인 학습 취소.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_ticker",
            "description": "종목명('삼성전자', '코덱스200') → 티커코드 변환. 모호하면 후보 목록 반환.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "종목명 또는 일부 키워드"},
                },
                "required": ["query"],
            },
        },
    },
]



# ==========================================================
# Tool 구현 (모두 dict 반환, 예외는 {"error": "..."} 형태)
# ==========================================================

def _read_csv_safe(path: Path) -> Optional[pd.DataFrame]:
    try:
        if not path.exists():
            return None
        df = pd.read_csv(path)
        return df if not df.empty else None
    except Exception as e:
        log.warning(f"_read_csv_safe {path}: {e}")
        return None


def _read_json_safe(path: Path) -> Optional[Any]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception as e:
        log.warning(f"_read_json_safe {path}: {e}")
        return None


def tool_get_trade_log(days_back: int = 7, ticker: Optional[str] = None) -> dict:
    """체결 완료된 거래 기록."""
    df = _read_csv_safe(TRADE_LOG_PATH)
    if df is None:
        return {"trades": [], "summary": "거래 기록 없음"}

    # 날짜 필터
    if days_back > 0 and "exit_date" in df.columns:
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        df = df[df["exit_date"] >= cutoff]

    # 종목 필터
    if ticker:
        df = df[df["ticker"].astype(str) == str(ticker)]

    if df.empty:
        return {"trades": [], "summary": f"기간({days_back}일)/종목({ticker})에 해당하는 거래 없음"}

    # 요약 통계
    trades = df.to_dict(orient="records")
    pnl_col = "pnl_pct" if "pnl_pct" in df.columns else None
    summary = {"count": len(trades)}
    if pnl_col:
        pnls = pd.to_numeric(df[pnl_col], errors="coerce").dropna()
        if len(pnls) > 0:
            wins = (pnls > 0).sum()
            summary.update({
                "win_rate_pct": round(wins / len(pnls) * 100, 1),
                "wins": int(wins),
                "losses": int((pnls < 0).sum()),
                "avg_pnl_pct": round(float(pnls.mean()), 2),
                "best_pnl_pct": round(float(pnls.max()), 2),
                "worst_pnl_pct": round(float(pnls.min()), 2),
            })
    # exit_reason 분포
    if "exit_reason" in df.columns:
        summary["exit_reasons"] = df["exit_reason"].value_counts().to_dict()

    return {"trades": trades[-20:], "summary": summary, "total_count": len(trades)}


def tool_get_positions(broker=None, position_manager=None) -> dict:
    """현재 보유 포지션."""
    result = {"positions": []}

    if broker is not None:
        try:
            holdings = broker.get_holdings()
            for h in holdings:
                result["positions"].append({
                    "ticker": h.ticker,
                    "shares": h.shares,
                    "avg_price": float(h.avg_price),
                    "current_price": float(h.current_price),
                    "market_value": float(h.market_value),
                    "unrealized_pnl": float(h.unrealized_pnl),
                    "unrealized_pnl_pct": round(float(h.unrealized_pnl_pct), 2),
                })
        except Exception as e:
            result["broker_error"] = str(e)

    # PositionManager의 메타 (stop/target/probability)
    if position_manager is not None:
        try:
            for ticker, pos in position_manager._positions.items():
                meta = {
                    "ticker": ticker,
                    "entry_price": float(pos.entry_price),
                    "stop_price": float(pos.stop_price),
                    "target_price": float(pos.target_price),
                    "trailing_stop": float(pos.trailing_stop),
                    "entry_date": pos.entry_date,
                    "max_holding_days": pos.max_holding_days,
                    "win_rate_at_entry": float(getattr(pos, "win_rate_at_entry", 0.0)),
                }
                # broker positions에 매칭해서 병합
                matched = next((p for p in result["positions"] if p["ticker"] == ticker), None)
                if matched:
                    matched.update(meta)
                else:
                    result["positions"].append(meta)
        except Exception as e:
            result["pm_error"] = str(e)

    if not result["positions"]:
        result["summary"] = "보유 종목 없음"
    else:
        result["summary"] = f"보유 {len(result['positions'])}종목"
    return result


def tool_get_balance(broker=None) -> dict:
    """계좌 잔고."""
    if broker is None:
        return {"error": "broker 미연결"}
    try:
        bal = broker.get_balance()
        return {
            "cash_krw": float(bal.cash_krw),
            "total_value_krw": float(bal.total_value_krw),
            "holdings_value_krw": float(bal.total_value_krw - bal.cash_krw),
            "holdings_count": len(bal.holdings),
            "unrealized_pnl": float(sum(h.unrealized_pnl for h in bal.holdings)),
        }
    except Exception as e:
        return {"error": f"잔고 조회 실패: {e}"}


def tool_get_market_context() -> dict:
    """캐시된 시장 컨텍스트."""
    data = _read_json_safe(MARKET_STATE)
    if data is None:
        return {"error": "market_state.json 없음 (아직 tick_offmarket 한 번도 안 돌았을 수 있음)"}
    # 너무 큰 sector_strength는 상위 5개만
    if isinstance(data.get("sector_strength"), dict):
        top5 = dict(sorted(data["sector_strength"].items(), key=lambda x: -x[1])[:5])
        data["sector_strength_top5"] = top5
        data.pop("sector_strength", None)
    return data


def tool_get_pending_approvals(approval_manager=None) -> dict:
    """대기 중인 추가매수 승인 요청."""
    if approval_manager is None:
        # 파일에서 직접 읽기 (fallback)
        data = _read_json_safe(APPROVALS_PATH)
        if data is None:
            return {"pending": [], "summary": "승인 요청 없음"}
        pending = [r for r in (data.get("requests") or []) if r.get("status") == "pending"]
        return {"pending": pending, "summary": f"대기 중 {len(pending)}건"}

    try:
        pending = []
        for req in approval_manager._requests.values():
            if req.status == "pending":
                pending.append({
                    "request_id": req.request_id[:8],
                    "ticker": req.ticker,
                    "strength": req.strength,
                    "score": req.score,
                    "threshold": req.threshold,
                    "options_krw": req.options_krw,
                    "created_at": req.created_at,
                    "elapsed_sec": int((datetime.now() - datetime.fromisoformat(req.created_at)).total_seconds()),
                })
        return {"pending": pending, "summary": f"대기 중 {len(pending)}건"}
    except Exception as e:
        return {"error": f"approval_manager 조회 실패: {e}"}


def tool_get_rulebook_stats(rulebook=None, ticker: Optional[str] = None) -> dict:
    """학습 룰북 통계."""
    if rulebook is None:
        return {"error": "rulebook 미연결"}

    stats = []
    try:
        # LearnedRuleBook은 _rulebook_by_ticker dict 사용
        rb_dict = getattr(rulebook, "_rulebook_by_ticker", None) or getattr(rulebook, "_rulebook_cache", None)
        if not rb_dict:
            return {"stats": [], "summary": "로드된 룰북 없음 (아직 평가 안 됨)"}

        items = rb_dict.items() if not ticker else [(ticker, rb_dict.get(ticker))]
        for t, rb in items:
            if rb is None:
                continue
            stats.append({
                "ticker": t,
                "direction": getattr(rb, "direction", "?"),
                "exit_strategy": getattr(rb, "exit_strategy", "?"),
                "win_rate": round(float(getattr(rb, "win_rate", 0)), 3),
                "fitness": round(float(getattr(rb, "fitness", 0)), 2),
                "trade_count": int(getattr(rb, "trade_count", 0)),
            })
    except Exception as e:
        return {"error": f"룰북 통계 실패: {e}"}

    return {"stats": stats, "summary": f"{len(stats)}개 룰북"}


# Tool name → 함수 매핑 (context는 호출 시 주입)
# ==========================================================
# 학습 관련 tool (v6 신규)
# ==========================================================

def tool_start_training(ticker_or_name: str, force: bool = False, training_manager=None) -> dict:
    """학습 시작. 종목명/티커 자동 해석."""
    if training_manager is None:
        return {"error": "training_manager 미연결"}

    # 종목 해석
    from engine.ai.ticker_resolver import resolve_ticker, get_ticker_name
    q = (ticker_or_name or "").strip()
    if not q:
        return {"error": "종목을 지정해주세요"}

    # 숫자 6자리면 티커 직접 사용
    if q.isdigit() and len(q) == 6:
        ticker = q
        name = get_ticker_name(ticker) or ticker
    else:
        r = resolve_ticker(q, limit=5)
        candidates = r.get("candidates", [])
        if not candidates:
            return {"error": f"'{q}'에 해당하는 종목을 찾지 못했습니다"}
        if len(candidates) > 1 and not r.get("exact_match"):
            return {
                "ambiguous": True,
                "query": q,
                "candidates": [{"ticker": c["ticker"], "name": c["name"]} for c in candidates[:5]],
                "message": "여러 후보가 있습니다. 정확한 종목명이나 티커를 지정해주세요.",
            }
        top = candidates[0]
        ticker = top["ticker"]
        name = top["name"]

    result = training_manager.start(
        ticker=ticker,
        ticker_name=name,
        force=force,
    )
    if result.get("started"):
        return {"started": True, "ticker": ticker, "ticker_name": name, "message": f"{name}({ticker}) 학습을 시작했습니다. 진행률은 별도 메시지로 갱신됩니다."}
    return result


def tool_get_training_status(training_manager=None) -> dict:
    """학습 상태 조회."""
    if training_manager is None:
        return {"error": "training_manager 미연결"}
    return training_manager.status()


def tool_cancel_training(training_manager=None) -> dict:
    """학습 취소."""
    if training_manager is None:
        return {"error": "training_manager 미연결"}
    return training_manager.cancel()


def tool_resolve_ticker(query: str) -> dict:
    """종목명 → 티커 변환."""
    from engine.ai.ticker_resolver import resolve_ticker
    return resolve_ticker(query, limit=5)



TOOL_DISPATCH = {
    "get_trade_log":         lambda args, ctx: tool_get_trade_log(**args),
    "get_positions":         lambda args, ctx: tool_get_positions(broker=ctx.get("broker"), position_manager=ctx.get("position_manager")),
    "get_balance":           lambda args, ctx: tool_get_balance(broker=ctx.get("broker")),
    "get_market_context":    lambda args, ctx: tool_get_market_context(),
    "get_pending_approvals": lambda args, ctx: tool_get_pending_approvals(approval_manager=ctx.get("approval_manager")),
    "get_rulebook_stats":    lambda args, ctx: tool_get_rulebook_stats(rulebook=ctx.get("rulebook"), **args),
    "start_training":        lambda args, ctx: tool_start_training(training_manager=ctx.get("training_manager"), **args),
    "get_training_status":   lambda args, ctx: tool_get_training_status(training_manager=ctx.get("training_manager")),
    "cancel_training":       lambda args, ctx: tool_cancel_training(training_manager=ctx.get("training_manager")),
    "resolve_ticker":        lambda args, ctx: tool_resolve_ticker(**args),
}



# ==========================================================
# LLM 호출 메인
# ==========================================================
class AIAssistant:
    """
    AI 비서. Bot에서 ask(user_text)로 호출.
    의존성:
      - broker, position_manager, approval_manager, rulebook
      모두 optional (None이면 해당 tool은 error 응답)
    """

    def __init__(
        self,
        broker=None,
        position_manager=None,
        approval_manager=None,
        rulebook=None,
        training_manager=None,
        model: str = MODEL_NAME,
    ):
        self.ctx = {
            "broker":            broker,
            "position_manager":  position_manager,
            "approval_manager":  approval_manager,
            "rulebook":          rulebook,
            "training_manager":  training_manager,
        }
        self.model = model
        self._client = None
        log.info(f"AIAssistant 초기화: model={model}")

    def _client_lazy(self):
        """OpenAI 클라이언트 지연 초기화 (.env 로드 후 import)."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
        return self._client

    def ask(self, user_text: str) -> str:
        """사용자 자연어 → LLM 호출 → 답변 문자열."""
        if not user_text or not user_text.strip():
            return "질문이 비어있어요."

        try:
            client = self._client_lazy()
        except Exception as e:
            log.error(f"OpenAI 클라이언트 초기화 실패: {e}")
            return f"❌ AI 비서 초기화 실패: {e}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_text.strip()},
        ]

        # 최대 MAX_TOOL_ROUNDS회 tool 왕복
        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS_SCHEMA,
                    tool_choice="auto",
                    max_tokens=MAX_OUTPUT_TOK,
                    temperature=0.3,
                )
            except Exception as e:
                log.error(f"OpenAI API 호출 실패: {e}")
                return f"❌ AI API 호출 실패: {type(e).__name__}: {e}"

            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []

            # tool 호출 없으면 → 최종 답변
            if not tool_calls:
                content = msg.content or "(빈 응답)"
                log.info(f"AI 응답 완료: round={round_idx}, 토큰={resp.usage.total_tokens}, 길이={len(content)}")
                return content

            # assistant 메시지 추가 (tool_calls 포함)
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    } for tc in tool_calls
                ],
            })

            # 각 tool 실행 결과를 messages에 추가
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}

                fn = TOOL_DISPATCH.get(name)
                if fn is None:
                    result = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = fn(args, self.ctx)
                    except Exception as e:
                        log.warning(f"tool {name} 실패: {e}")
                        result = {"error": f"{type(e).__name__}: {e}"}

                log.info(f"tool 호출: {name}({args}) → {str(result)[:120]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

        # MAX_TOOL_ROUNDS 초과
        log.warning(f"AI tool 왕복 한도 도달 ({MAX_TOOL_ROUNDS}회)")
        return "❌ AI가 답변 생성에 너무 오래 걸리고 있어요. 더 구체적인 질문으로 다시 시도해주세요."


# ==========================================================
# 단위 테스트
# ==========================================================
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(".env"))

    print("\n===== AIAssistant 단위 테스트 =====\n")

    # 1) Tool 함수 직접 호출 (broker 없이)
    print("[1] tool_get_trade_log() - 전체")
    r = tool_get_trade_log(days_back=0)
    print(f"    summary={r.get('summary')}\n")

    print("[2] tool_get_market_context()")
    r = tool_get_market_context()
    print(f"    keys={list(r.keys())[:5]}\n")

    print("[3] tool_get_pending_approvals()")
    r = tool_get_pending_approvals()
    print(f"    summary={r.get('summary')}\n")

    # 2) AIAssistant 실제 LLM 호출
    print("[4] AIAssistant.ask() - 자연어 질문")
    ai = AIAssistant()  # 의존성 없이 (tool 일부만 가능)

    questions = [
        "이번 주 거래 어땠어?",
        "지금 시장 상황 알려줘",
    ]
    for q in questions:
        print(f"\n  Q: {q}")
        ans = ai.ask(q)
        print(f"  A: {ans[:300]}")

    print("\n✅ AIAssistant 검증 완료")
