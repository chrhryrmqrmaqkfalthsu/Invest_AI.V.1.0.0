"""
공통 백테스트 함수
==================
- 봇 룰북 (28파라미터, engine.strategies.rulebook.Rulebook) 평가 가능
- Colab 룰북 (12파라미터, dict) 평가 가능
- 진입/청산/PnL 로직은 동일 (Colab의 backtest_with_log 베이스)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

import numpy as np
import pandas as pd


# =====================================================================
# 시그널 평가기 — 두 룰북 타입 모두 처리
# =====================================================================

def _eval_signal_colab(row: pd.Series, rules: dict) -> tuple[float, list[str]]:
    """Colab 12파라미터 시그널 평가 (원본 코드 그대로)"""
    score = 0.0
    reasons = []
    
    if row['MA20'] > row['MA60']:
        score += rules['weight_trend']
        reasons.append('정배열')
    
    if rules['rsi_low'] <= row['RSI'] <= rules['rsi_high']:
        score += rules['weight_rsi']
        reasons.append(f'RSI구간({row["RSI"]:.0f})')
    
    if row['MACD'] > row['MACD_signal']:
        score += rules['weight_macd']
        reasons.append('MACD골든')
    
    if row['Close'] <= row['BB_lower'] * rules['bb_proximity']:
        score += rules['weight_bb']
        reasons.append('볼린저하단')
    
    if row['Volume'] >= row['Vol_MA5'] * rules['volume_threshold']:
        score += rules['weight_volume']
        reasons.append(f'거래량{row["Volume"]/row["Vol_MA5"]:.1f}배')
    
    return score, reasons


def _eval_signal_bot(row: pd.Series, rb) -> tuple[float, list[str]]:
    """봇 28파라미터 룰북 시그널 평가 (5가지 공통 시그널만 사용 — 공정 비교)
    
    봇 룰북의 28개 파라미터 중 Colab과 직접 비교 가능한 5개 시그널만 사용.
    추가 파라미터(news, market, sector, vix 등)는 학습된 가중치는 그대로 두되
    이 백테스트에서는 비활성화. 진짜 알고리즘 + 5개 기본 시그널의 성능을 봄.
    """
    score = 0.0
    reasons = []
    
    # 1) MA 정배열
    if row['MA20'] > row['MA60']:
        score += rb.weight_ma_align
        reasons.append('정배열')
    
    # 2) RSI 구간
    if rb.rsi_low <= row['RSI'] <= rb.rsi_high:
        score += rb.weight_rsi_zone
        reasons.append(f'RSI구간({row["RSI"]:.0f})')
    
    # 3) MACD 골든
    macd_hist = row['MACD'] - row['MACD_signal']
    if macd_hist > rb.macd_min_hist:
        score += rb.weight_macd_golden
        reasons.append('MACD골든')
    
    # 4) 볼린저 하단
    if row['Close'] <= row['BB_lower'] * rb.bb_proximity:
        score += rb.weight_bb_near_lower
        reasons.append('볼린저하단')
    
    # 5) 거래량 급증
    if row['Volume'] >= row['Vol_MA5'] * rb.volume_surge_ratio:
        score += rb.weight_volume_surge
        reasons.append(f'거래량{row["Volume"]/row["Vol_MA5"]:.1f}배')
    
    return score, reasons


def _get_threshold(rules_or_rb) -> float:
    """양쪽 룰북에서 진입 임계값 추출"""
    if isinstance(rules_or_rb, dict):
        return rules_or_rb['buy_threshold']
    return rules_or_rb.signal_threshold


def _get_stop_atr(rules_or_rb) -> float:
    if isinstance(rules_or_rb, dict):
        return rules_or_rb['stop_atr']
    return rules_or_rb.stop_loss_atr


def _get_target_atr(rules_or_rb) -> float:
    if isinstance(rules_or_rb, dict):
        return rules_or_rb['target_atr']
    return rules_or_rb.take_profit_atr


# =====================================================================
# 백테스트 결과 컨테이너
# =====================================================================

@dataclass
class BacktestStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    sharpe_like: float = 0.0
    fitness: float = -100.0
    trade_list: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 2),
            "avg_pnl": round(self.avg_pnl, 3),
            "avg_win": round(self.avg_win, 3),
            "avg_loss": round(self.avg_loss, 3),
            "expectancy": round(self.expectancy, 3),
            "max_drawdown": round(self.max_drawdown, 2),
            "profit_factor": round(self.profit_factor, 3),
            "sharpe_like": round(self.sharpe_like, 3),
            "fitness": round(self.fitness, 3),
        }


# =====================================================================
# 메인 백테스트 함수 (공통)
# =====================================================================

def run_backtest(
    df: pd.DataFrame,
    rules_or_rb: Union[dict, Any],
    indices: list[int],
    holding_days: int = 20,
) -> BacktestStats:
    """
    Args:
        df: OHLCV + 지표 (MA20, MA60, MA200, RSI, MACD, MACD_signal, BB_*, ATR, Vol_MA5)
        rules_or_rb: Colab dict 또는 봇 Rulebook
        indices: 진입 후보 시점 인덱스 리스트
        holding_days: 최대 보유 일수
    """
    is_colab = isinstance(rules_or_rb, dict)
    threshold = _get_threshold(rules_or_rb)
    stop_atr  = _get_stop_atr(rules_or_rb)
    target_atr = _get_target_atr(rules_or_rb)
    
    trades_pnl = []
    trade_list = []
    
    for i in indices:
        if i >= len(df) - holding_days:
            continue
        row = df.iloc[i]
        if pd.isna(row.get('MA60')) or pd.isna(row.get('ATR')) or pd.isna(row.get('Vol_MA5')):
            continue
        
        # 시그널 평가
        if is_colab:
            score, reasons = _eval_signal_colab(row, rules_or_rb)
        else:
            score, reasons = _eval_signal_bot(row, rules_or_rb)
        
        if score < threshold:
            continue
        
        # 진입
        entry = float(row['Close'])
        atr = float(row['ATR'])
        stop = entry - stop_atr * atr
        target = entry + target_atr * atr
        
        # 청산 시뮬레이션
        exit_price = None
        exit_reason = "만기"
        hold = holding_days
        for j in range(1, holding_days + 1):
            if i + j >= len(df):
                break
            future = df.iloc[i + j]
            if future['Low'] <= stop:
                exit_price = stop
                exit_reason = "손절"
                hold = j
                break
            if future['High'] >= target:
                exit_price = target
                exit_reason = "익절"
                hold = j
                break
        
        if exit_price is None:
            exit_price = float(df.iloc[min(i + holding_days, len(df) - 1)]['Close'])
        
        pnl_pct = (exit_price - entry) / entry * 100
        trades_pnl.append(pnl_pct)
        trade_list.append({
            "entry_date": str(df.index[i].date()),
            "entry_price": round(entry, 2),
            "exit_reason": exit_reason,
            "pnl_pct": round(pnl_pct, 2),
            "hold_days": hold,
            "score": round(score, 2),
        })
    
    # 통계 집계
    stats = BacktestStats(trade_list=trade_list)
    n = len(trades_pnl)
    if n == 0:
        stats.fitness = -100
        return stats
    
    pnls = np.array(trades_pnl)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    
    stats.trades = n
    stats.wins = len(wins)
    stats.losses = len(losses)
    stats.win_rate = float(len(wins) / n * 100)
    stats.avg_pnl = float(np.mean(pnls))
    stats.avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    stats.avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    stats.expectancy = stats.avg_pnl  # 거래당 기대값
    
    # Max drawdown (누적 수익률 기준)
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    stats.max_drawdown = float(np.min(dd)) if len(dd) > 0 else 0.0
    
    # Profit factor
    gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = float(abs(np.sum(losses))) if len(losses) > 0 else 0.0
    stats.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
    
    # Sharpe-like (mean / std)
    if len(pnls) > 1 and np.std(pnls) > 0:
        stats.sharpe_like = float(np.mean(pnls) / np.std(pnls))
    
    # Fitness (Colab 공식 그대로 — 공정 비교)
    fitness = stats.avg_pnl + (stats.win_rate - 50) * 0.08
    if n < 8:    fitness -= 100
    elif n < 15: fitness -= 10
    elif n < 25: fitness -= 3
    stats.fitness = float(fitness)
    
    return stats


if __name__ == "__main__":
    # 자체 테스트
    import sys
    import pickle
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    
    cache = Path("data/_system/comparison_069500.pkl")
    with open(cache, "rb") as f:
        data = pickle.load(f)
    
    df = data["df"]
    train_idx = data["train_indices"]
    val_idx = data["val_indices"]
    
    # Colab 룰북으로 테스트
    dummy_colab = {
        'weight_trend': 1.0, 'weight_rsi': 1.0, 'weight_macd': 1.0,
        'weight_bb': 1.0, 'weight_volume': 1.0,
        'rsi_low': 30, 'rsi_high': 70,
        'volume_threshold': 1.5, 'bb_proximity': 1.05,
        'buy_threshold': 2.5, 'stop_atr': 2.0, 'target_atr': 3.0,
    }
    print("=== Colab 더미 룰북 백테스트 (Train) ===")
    s = run_backtest(df, dummy_colab, train_idx, holding_days=20)
    print(s.to_dict())
    print("\n=== Colab 더미 룰북 백테스트 (Val) ===")
    s = run_backtest(df, dummy_colab, val_idx, holding_days=20)
    print(s.to_dict())
    
    # 봇 룰북으로 테스트 (seed_patterns.json 의 379800 룰북 재사용)
    print("\n=== 봇 룰북 (seed의 379800) 백테스트 ===")
    from engine.strategies.rulebook import Rulebook
    import json
    seeds = json.load(open("data/_system/seed_patterns.json"))
    rb_dict = seeds["long"][0]["rulebook"]
    rb = Rulebook.from_dict(rb_dict) if hasattr(Rulebook, 'from_dict') else None
    if rb is None:
        # 수동 생성
        rb = Rulebook(ticker="TEST", asset_type="korean_etf", direction="long")
        for k, v in rb_dict.items():
            if hasattr(rb, k):
                setattr(rb, k, v)
    print("(Train)")
    s = run_backtest(df, rb, train_idx, holding_days=20)
    print(s.to_dict())
    print("(Val)")
    s = run_backtest(df, rb, val_idx, holding_days=20)
    print(s.to_dict())
