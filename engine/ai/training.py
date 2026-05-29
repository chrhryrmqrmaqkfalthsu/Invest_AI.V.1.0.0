"""
백그라운드 학습 워커 (Phase G-3)
=================================
사용자 자연어 "코덱스200 학습해" → 백그라운드 스레드에서 GA 학습 실행.

기능:
  - 단일 학습 잡 (동시 학습 X, VM 1GB RAM 보호)
  - GA 세대마다 텔레그램 progress 메시지 edit
  - 취소 지원 (cancel_event)
  - 완료 시 parameters.json 저장 + Runner.reload_symbols() 자동 호출
  - 모든 결과는 사용자에게 텔레그램으로 알림

스레드 안전:
  - _job_lock 으로 동시 학습 차단
  - 학습 종료 시 콜백은 메인 스레드가 아니라 학습 스레드에서 실행
    (단, notifier/repo 는 자체적으로 thread-safe)
"""
from __future__ import annotations

import logging
import threading
from collections import deque
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger("ai_training")

# Progress 메시지 갱신 간격 (세대 단위). 너무 자주 edit하면 텔레그램 rate limit.
PROGRESS_EVERY_N_GEN = 2
SEED_FITNESS_THRESHOLD = 30.0


@dataclass
class TrainingJob:
    """진행 중인 학습 작업 상태."""
    ticker: str
    ticker_name: str            # "KODEX 200" 같은 표시명
    started_at: datetime
    chat_id: Optional[int]      # progress edit 대상
    progress_msg_id: Optional[int] = None
    current_gen: int = 0
    total_gen: int = 0
    best_fitness: float = 0.0
    avg_fitness: float = 0.0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


# ============================================================
# 매니저 (싱글톤 패턴)
# ============================================================

class TrainingManager:
    """전체 학습 잡 라이프사이클 관리. AIAssistant 가 보유."""

    def __init__(self, notifier=None, runner=None):
        self.notifier = notifier
        self.runner = runner
        self._current: Optional[TrainingJob] = None
        self._lock = threading.Lock()
        # 학습 대기열 (FIFO): [{"ticker": ..., "ticker_name": ..., "years": ..., "position_limit_krw": ...}, ...]
        self._queue: deque = deque()

    def attach(self, notifier=None, runner=None) -> None:
        """런타임에 의존성 주입 (TelegramBot 초기화 후)."""
        if notifier is not None:
            self.notifier = notifier
        if runner is not None:
            self.runner = runner

    # --------------------------------------------------------
    # 상태 조회
    # --------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            j = self._current
            if j is None or not j.is_alive():
                return {
                    "running": False,
                    "queue_size": len(self._queue),
                    "queue": [{"ticker": q["ticker"], "ticker_name": q["ticker_name"]} for q in self._queue],
                }
            return {
                "running": True,
                "ticker": j.ticker,
                "ticker_name": j.ticker_name,
                "started_at": j.started_at.isoformat(timespec="seconds"),
                "elapsed_sec": int((datetime.now() - j.started_at).total_seconds()),
                "current_gen": j.current_gen,
                "total_gen": j.total_gen,
                "progress_pct": round(j.current_gen / max(j.total_gen, 1) * 100, 1),
                "best_fitness": round(j.best_fitness, 2),
                "avg_fitness": round(j.avg_fitness, 2),
                "queue_size": len(self._queue),
                "queue": [{"ticker": q["ticker"], "ticker_name": q["ticker_name"]} for q in self._queue],
            }

    # --------------------------------------------------------
    # 취소
    # --------------------------------------------------------
    def cancel(self) -> dict:
        with self._lock:
            j = self._current
            if j is None or not j.is_alive():
                return {"cancelled": False, "reason": "진행 중인 학습 없음"}
            j.cancel_event.set()
            log.info(f"[CANCEL] {j.ticker} 학습 취소 요청")
            return {
                "cancelled": True,
                "ticker": j.ticker,
                "ticker_name": j.ticker_name,
                "stopped_at_gen": j.current_gen,
            }

    def clear_queue(self) -> dict:
        """대기열 전체 비우기 (진행 중 학습은 그대로)."""
        with self._lock:
            cleared = list(self._queue)
            self._queue.clear()
        return {
            "cleared_count": len(cleared),
            "items": [{"ticker": q["ticker"], "ticker_name": q["ticker_name"]} for q in cleared],
        }

    def enqueue_many(self, items: list, years: int = 5, position_limit_krw: float = 120000.0) -> dict:
        """
        여러 종목을 한 번에 큐 등록.
        items: [{"ticker": "069500", "ticker_name": "KODEX 200"}, ...]
        첫 항목은 진행중 없으면 즉시 시작, 나머지는 큐로.
        """
        if not items:
            return {"started": 0, "queued": 0, "items": []}

        results = []
        for idx, it in enumerate(items):
            r = self.start(
                ticker=it["ticker"],
                ticker_name=it["ticker_name"],
                years=years,
                position_limit_krw=position_limit_krw,
                queue_if_busy=True,
            )
            results.append({
                "ticker": it["ticker"],
                "ticker_name": it["ticker_name"],
                "started": bool(r.get("started")),
                "queued": bool(r.get("queued")),
                "queue_position": r.get("queue_position"),
            })
        started_n = sum(1 for r in results if r["started"])
        queued_n = sum(1 for r in results if r["queued"])
        return {"started": started_n, "queued": queued_n, "items": results}

    # --------------------------------------------------------
    # 학습 시작
    # --------------------------------------------------------
    def start(
        self,
        ticker: str,
        ticker_name: str,
        chat_id: Optional[int] = None,
        years: int = 5,
        position_limit_krw: float = 120000.0,
        force: bool = False,
        queue_if_busy: bool = False,
    ) -> dict:
        """
        학습 시작. 이미 진행 중이면 force=True 가 아닌 한 거부.
        Returns:
          {"started": True, ...} 또는 {"started": False, "reason": ..., "current": ...}
        """
        with self._lock:
            # 진행 중 잡 확인
            if self._current is not None and self._current.is_alive():
                if queue_if_busy:
                    # 대기열에 추가
                    self._queue.append({
                        "ticker": ticker,
                        "ticker_name": ticker_name,
                        "years": years,
                        "position_limit_krw": position_limit_krw,
                        "chat_id": chat_id,
                    })
                    queue_pos = len(self._queue)
                    log.info(f"[QUEUE] {ticker} ({ticker_name}) 대기열 추가 (위치 #{queue_pos})")
                    return {
                        "started": False,
                        "queued": True,
                        "ticker": ticker,
                        "ticker_name": ticker_name,
                        "queue_position": queue_pos,
                        "current": {
                            "ticker": self._current.ticker,
                            "ticker_name": self._current.ticker_name,
                            "current_gen": self._current.current_gen,
                            "total_gen": self._current.total_gen,
                        },
                    }
                if not force:
                    return {
                        "started": False,
                        "reason": "이미 학습이 진행 중입니다. 취소하거나 끝날 때까지 기다려주세요.",
                        "current": {
                            "ticker": self._current.ticker,
                            "ticker_name": self._current.ticker_name,
                            "current_gen": self._current.current_gen,
                            "total_gen": self._current.total_gen,
                        },
                    }
                # force: 기존 잡 취소
                self._current.cancel_event.set()
                log.info(f"[FORCE] {self._current.ticker} 취소 후 {ticker} 재시작")
                # 기존 스레드가 cancel을 인지할 시간을 잠깐 줌 (선택)
                # join 안 함 — 무한 대기 방지

            job = TrainingJob(
                ticker=ticker,
                ticker_name=ticker_name,
                started_at=datetime.now(),
                chat_id=chat_id,
                total_gen=0,  # GA 시작 후 갱신
            )
            self._current = job

        # 초기 progress 메시지 전송
        if self.notifier is not None:
            try:
                msg = (
                    f"📊 *학습 시작*\n"
                    f"종목: {ticker_name} (`{ticker}`)\n"
                    f"예상 시간: 약 5~10분\n"
                    f"_진행 상황은 이 메시지가 갱신됩니다._"
                )
                msg_id = self.notifier.send_progress(msg)
                job.progress_msg_id = msg_id if msg_id else None
            except Exception as e:
                log.warning(f"초기 progress 메시지 전송 실패: {e}")

        # 워커 스레드 시작
        t = threading.Thread(
            target=self._run_worker,
            args=(job, years, position_limit_krw),
            name=f"train-{ticker}",
            daemon=True,
        )
        job.thread = t
        t.start()

        return {
            "started": True,
            "ticker": ticker,
            "ticker_name": ticker_name,
            "thread_name": t.name,
        }

    # --------------------------------------------------------
    # 워커 본체 (백그라운드 스레드)
    # --------------------------------------------------------
    def _run_worker(self, job: TrainingJob, years: int, position_limit_krw: float):
        """실제 GA 학습 실행."""
        ticker = job.ticker
        try:
            # Lazy import (학습 모듈은 무거움)
            from engine.learning.learner import learn
            from engine.learning.genetic import GAConfig
            from engine.storage import repository as repo

            ga_cfg = GAConfig()  # 기본값 사용 (population=40, generations=25)
            job.total_gen = ga_cfg.generations

            # 세대별 콜백
            def on_gen(gen: int, best_rb, avg_fitness: float):
                # 취소 체크 (예외 발생시켜 GA 중단)
                if job.cancel_event.is_set():
                    raise InterruptedError("학습 취소 요청됨")

                job.current_gen = gen
                job.best_fitness = float(getattr(best_rb, "fitness", 0.0) or 0.0)
                job.avg_fitness = float(avg_fitness or 0.0)

                # progress edit (N세대마다)
                if (gen % PROGRESS_EVERY_N_GEN == 0) or (gen == job.total_gen):
                    self._update_progress(job)

            # 자기 자신의 이전 학습 결과를 시드로 로드 (있으면 계승)
            seed_rbs = []
            try:
                prev_rb = repo.load_rulebook(ticker)
                if prev_rb is not None:
                    seed_rbs.append(prev_rb)
                    log.info(f"[TRAIN] {ticker} 이전 룰북 시드로 사용")
            except Exception as e:
                log.info(f"[TRAIN] {ticker} 이전 룰북 없음 (신규 학습): {e}")

            log.info(f"[TRAIN] {ticker} ({job.ticker_name}) GA 시작: gen={job.total_gen}, seeds={len(seed_rbs)}")
            t0 = time.time()
            result = learn(
                ticker=ticker,
                years=years,
                position_limit_krw=position_limit_krw,
                ga_config=ga_cfg,
                on_generation=on_gen,
                seed_rulebooks=seed_rbs if seed_rbs else None,
            )
            elapsed = time.time() - t0

            # 저장 (analyze.py 패턴 그대로)
            rb = result.best_rulebook
            bt = result.backtest
            meta = result.asset_meta  # dict

            repo.add_symbol(ticker, meta)
            repo.save_rulebook(rb, meta)
            # backtest.json에 train + test + overfit_ratio 모두 저장
            bt_payload = bt.to_dict()
            if getattr(result, "test_result", None) is not None:
                bt_payload["test_result"] = result.test_result.to_dict()
                bt_payload["train_period"] = list(result.train_period)
                bt_payload["test_period"] = list(result.test_period)
                bt_payload["overfit_ratio"] = result.overfit_ratio
            repo.save_backtest(ticker, bt_payload)
            if result.ga_result and hasattr(result.ga_result, "history"):
                repo.save_fitness_history(ticker, result.ga_result.history)
            seed_added = False
            if bt.fitness >= SEED_FITNESS_THRESHOLD:
                try:
                    repo.add_seed_rulebook(rb, min_fitness=SEED_FITNESS_THRESHOLD)
                    seed_added = True
                except Exception as e:
                    log.warning(f"시드 패턴 등록 실패: {e}")

            # Runner hot-reload
            reload_info = {"added": [], "total": 0}
            if self.runner is not None:
                try:
                    reload_info = self.runner.reload_symbols()
                except Exception as e:
                    log.warning(f"Runner reload 실패: {e}")

            # 완료 알림
            self._notify_done(job, bt, elapsed, reload_info, seed_added, learn_result=result)
            log.info(f"[TRAIN] {ticker} 완료: fitness={bt.fitness:.2f}, elapsed={elapsed:.1f}s")

        except InterruptedError:
            log.info(f"[TRAIN] {ticker} 사용자에 의해 취소됨 (gen={job.current_gen}/{job.total_gen})")
            self._notify_cancelled(job)

        except Exception as e:
            log.exception(f"[TRAIN] {ticker} 학습 실패: {e}")
            self._notify_error(job, e)

        finally:
            # 잡 슬롯 해제 + 다음 큐 항목 자동 시작
            next_item = None
            with self._lock:
                if self._current is job:
                    pass  # _current 는 유지(상태 조회용), 다음 start() 가 덮어씀
                if self._queue:
                    next_item = self._queue.popleft()
            if next_item is not None:
                log.info(f"[QUEUE] 다음 학습 자동 시작: {next_item['ticker']} ({next_item['ticker_name']})")
                # 워커가 아직 finally 안에 있으므로 self._current.is_alive()가 True
                # → force=True로 자기 자신 덮어쓰기 (어차피 자기 워커가 끝나는 중)
                try:
                    r = self.start(
                        ticker=next_item["ticker"],
                        ticker_name=next_item["ticker_name"],
                        chat_id=next_item.get("chat_id"),
                        years=next_item.get("years", 5),
                        position_limit_krw=next_item.get("position_limit_krw", 120000.0),
                        force=True,
                    )
                    if not r.get("started"):
                        log.error(f"[QUEUE] 자동 시작 실패: {r}")
                        if self.notifier is not None:
                            try:
                                self.notifier.send_error(f"⚠ 큐 자동 시작 실패: {next_item['ticker']}\n{r.get('reason','')}")
                            except Exception:
                                pass
                except Exception as e:
                    log.exception(f"큐 자동 시작 실패: {e}")
                    if self.notifier is not None:
                        try:
                            self.notifier.send_error(f"큐 자동 시작 실패: {next_item['ticker']} - {e}")
                        except Exception:
                            pass

    # --------------------------------------------------------
    # 알림 헬퍼
    # --------------------------------------------------------
    def _update_progress(self, job: TrainingJob):
        if self.notifier is None or job.progress_msg_id is None:
            return
        pct = round(job.current_gen / max(job.total_gen, 1) * 100, 1)
        bar_len = 16
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        text = (
            f"📊 *학습 중*\n"
            f"종목: {job.ticker_name} (`{job.ticker}`)\n"
            f"`[{bar}]` {pct:.0f}%\n"
            f"세대: {job.current_gen}/{job.total_gen}\n"
            f"best fitness: *{job.best_fitness:.2f}*\n"
            f"avg fitness: {job.avg_fitness:.2f}"
        )
        try:
            self.notifier.edit_message(job.progress_msg_id, text, parse_mode="Markdown")
        except Exception as e:
            log.debug(f"progress edit 실패: {e}")

    def _notify_done(self, job: TrainingJob, bt, elapsed: float, reload_info: dict, seed_added: bool, learn_result=None):
        if self.notifier is None:
            return
        added = reload_info.get("added", [])
        added_str = "✅ 거래 후보 즉시 편입" if job.ticker in added else "ℹ️ 이미 추적 중"

        text = (
            f"✅ *학습 완료*\n"
            f"종목: {job.ticker_name} (`{job.ticker}`)\n"
            f"소요: {int(elapsed)}초\n\n"
            f"📈 *결과*\n"
            f"• 적합도: *{bt.fitness:.2f}*\n"
            f"• 승률: *{bt.win_rate:.1f}%* ({bt.win_count}승 / {bt.loss_count}패)\n"
            f"• 평균 수익률: {bt.avg_return_pct:+.2f}%\n"
            f"• 기대값: {bt.expectancy_pct:+.3f}%\n"
            f"• 거래 수: {bt.trade_count}회\n"
            f"• MDD: {bt.max_drawdown_pct:.2f}%\n"
            f"• Profit Factor: {bt.profit_factor:.2f}\n"
        )

        # TEST(out-of-sample) 결과 + 과적합 판정
        if learn_result is not None and getattr(learn_result, "test_result", None) is not None:
            tr = learn_result.test_result
            ov = getattr(learn_result, "overfit_ratio", None)
            if ov is None:
                verdict = "?"
                ov_str = "N/A"
            else:
                ov_str = f"{ov:.2f}"
                if ov >= 0.5:
                    verdict = "✅ 양호"
                elif ov >= 0.3:
                    verdict = "⚠️ 주의"
                else:
                    verdict = "🚨 과적합 의심"
            tp = getattr(learn_result, "test_period", ("?", "?"))
            text += (
                f"\n🧪 *Out-of-Sample 검증* ({tp[0]}~{tp[1]})\n"
                f"• 적합도: {tr.fitness:.2f}\n"
                f"• 승률: {tr.win_rate:.1f}% ({tr.win_count}승 / {tr.loss_count}패)\n"
                f"• 기대값: {tr.expectancy_pct:+.3f}%\n"
                f"• 거래 수: {tr.trade_count}회\n"
                f"• test/train: {ov_str} → {verdict}\n"
            )

        text += f"\n{added_str}"
        if seed_added:
            text += f"\n🌱 시드 패턴 등록됨 (fitness ≥ {SEED_FITNESS_THRESHOLD:.0f})"

        # progress 메시지 최종 edit (또는 새 메시지)
        try:
            if job.progress_msg_id is not None:
                self.notifier.edit_message(job.progress_msg_id, text, parse_mode="Markdown")
            else:
                self.notifier.send(text, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"완료 알림 실패: {e}")
            try:
                self.notifier.send_info(f"학습 완료: {job.ticker_name} ({job.ticker}) fitness={bt.fitness:.2f}")
            except Exception:
                pass

    def _notify_cancelled(self, job: TrainingJob):
        if self.notifier is None:
            return
        text = (
            f"🛑 *학습 취소됨*\n"
            f"종목: {job.ticker_name} (`{job.ticker}`)\n"
            f"중단 지점: {job.current_gen}/{job.total_gen} 세대"
        )
        try:
            if job.progress_msg_id is not None:
                self.notifier.edit_message(job.progress_msg_id, text, parse_mode="Markdown")
            else:
                self.notifier.send(text, parse_mode="Markdown")
        except Exception:
            pass

    def _notify_error(self, job: TrainingJob, err: Exception):
        if self.notifier is None:
            return
        text = (
            f"❌ *학습 실패*\n"
            f"종목: {job.ticker_name} (`{job.ticker}`)\n"
            f"오류: `{type(err).__name__}: {err}`"
        )
        try:
            if job.progress_msg_id is not None:
                self.notifier.edit_message(job.progress_msg_id, text, parse_mode="Markdown")
            else:
                self.notifier.send_error(text)
        except Exception:
            pass


# ============================================================
# 전역 싱글톤 (AIAssistant 가 import 해서 사용)
# ============================================================

_MANAGER: Optional[TrainingManager] = None


def get_training_manager() -> TrainingManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = TrainingManager()
    return _MANAGER
