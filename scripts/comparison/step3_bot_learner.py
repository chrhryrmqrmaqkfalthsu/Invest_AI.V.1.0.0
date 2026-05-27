"""
Step 3: 봇 학습기로 GA 학습
============================
- 캐시된 Train 데이터만 사용 (Val 절대 차단)
- 봇 GA (pop=40, gen=25, 토너먼트+가우시안+조기종료)
- 결과 룰북 저장
"""
import sys
import pickle
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.strategies.rulebook import Rulebook, default_rulebook
from engine.learning.genetic import GAConfig, run_ga

from scripts.comparison.common_backtest import run_backtest


CACHE_PATH = Path("data/_system/comparison_069500.pkl")
OUTPUT_PATH = Path("data/_system/comparison_bot_rulebook.json")
TICKER = "069500"


def main():
    print("=" * 70)
    print(f"Step 3: 봇 학습기 GA ({TICKER})")
    print("=" * 70)

    # 데이터 로드
    with open(CACHE_PATH, "rb") as f:
        data = pickle.load(f)
    df = data["df"]
    train_idx = data["train_indices"]
    val_idx = data["val_indices"]
    holding_days = data["holding_days"]
    print(f"Train: {len(train_idx)} 진입 포인트 ({df.index[0].date()} ~ {df.index[data['split_idx']-1].date()})")
    print(f"Val:   {len(val_idx)} 진입 포인트 (학습에 사용 안 함)")

    # 봇 기본 룰북 (KOSPI ETF, long)
    base_rb = default_rulebook(TICKER, asset_type="korean_etf", direction="long")
    base_rb.sector_name = "tech"  # KODEX 200 → 대형주, tech 비중 큼

    # 평가 함수: Train 인덱스만 사용 + 공통 백테스트
    eval_count = [0]
    def evaluate_fn(rb: Rulebook) -> float:
        eval_count[0] += 1
        stats = run_backtest(df, rb, train_idx, holding_days=holding_days)
        return stats.fitness

    # GA 설정 (봇 기본값)
    cfg = GAConfig(
        population=40,
        generations=25,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.0,  # 비교 공정성 — 시드 없이 무작위로 시작
        early_stop_no_improve=8,
        random_seed=42,
    )

    print(f"\nGA 시작: pop={cfg.population}, gen={cfg.generations}, seed=42")
    print(f"각 세대 마다 fitness 출력됩니다...\n")

    t0 = time.time()
    result = run_ga(
        base_rulebook=base_rb,
        evaluate_fn=evaluate_fn,
        ga_config=cfg,
        seed_rulebooks=None,  # 비교 공정성
        on_generation=None,
    )
    elapsed = time.time() - t0

    best = result.best
    print(f"\n학습 완료: {elapsed:.1f}초, 총 {eval_count[0]}회 평가, {result.generations_run}세대 진행")
    print(f"최고 Train fitness: {best.fitness:.3f}")

    # Train + Val 둘 다 백테스트
    print("\n=== 최종 룰북 검증 ===")
    train_stats = run_backtest(df, best, train_idx, holding_days=holding_days)
    val_stats   = run_backtest(df, best, val_idx,   holding_days=holding_days)
    print(f"  Train: trades={train_stats.trades}, win_rate={train_stats.win_rate:.1f}%, "
          f"avg_pnl={train_stats.avg_pnl:+.2f}%, fitness={train_stats.fitness:.2f}")
    print(f"  Val:   trades={val_stats.trades}, win_rate={val_stats.win_rate:.1f}%, "
          f"avg_pnl={val_stats.avg_pnl:+.2f}%, fitness={val_stats.fitness:.2f}")
    gap = train_stats.win_rate - val_stats.win_rate
    print(f"  Gap (Train - Val 승률): {gap:+.1f}%p")

    # 저장
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "learner": "bot",
        "ticker": TICKER,
        "elapsed_sec": elapsed,
        "evaluations": eval_count[0],
        "generations_run": result.generations_run,
        "ga_config": {
            "population": cfg.population, "generations": cfg.generations,
            "elite_ratio": cfg.elite_ratio, "mutation_rate": cfg.mutation_rate,
            "tournament_size": cfg.tournament_size, "random_seed": cfg.random_seed,
        },
        "rulebook": {k: v for k, v in best.__dict__.items() if not k.startswith("_")},
        "train_stats": train_stats.to_dict(),
        "val_stats":   val_stats.to_dict(),
        "overfit_gap_winrate_pp": round(gap, 2),
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ 저장: {OUTPUT_PATH}")
    print("\n다음: python scripts/comparison/step4_colab_learner.py")


if __name__ == "__main__":
    main()
