# market_history 재생성 검증 리포트

- replacement_status: `ATOMIC_REPLACE_SUCCESS`
- target_sha_before: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- tmp_path: `data/_system/market_history.csv.regen_tmp`
- target_path: `data/_system/market_history.csv`

## Fetch 결과

| symbol | success | rows | first | last |
|---|---|---|---|---|
| ^GSPC | PASS | 1759 | 2019-07-11 | 2026-07-10 |
| ^VIX | PASS | 1761 | 2019-07-10 | 2026-07-10 |
| XLK | PASS | 1759 | 2019-07-11 | 2026-07-10 |
| XLF | PASS | 1759 | 2019-07-11 | 2026-07-10 |
| XLE | PASS | 1759 | 2019-07-11 | 2026-07-10 |
| XLV | PASS | 1759 | 2019-07-11 | 2026-07-10 |
| XLY | PASS | 1759 | 2019-07-11 | 2026-07-10 |
| XLI | PASS | 1759 | 2019-07-11 | 2026-07-10 |

## 8개 게이트

| gate | name | result | detail |
|---|---|---|---|
| 1 | non_empty | PASS | rows=1759 |
| 2 | minimum_rows | PASS | rows=1759, required>=1700 |
| 3 | seven_year_date_coverage | PASS | first=2019-07-11 00:00:00, last=2026-07-10 00:00:00, required_first<=2019-08-31, required_last>=2026-07-01 |
| 4 | exact_schema_and_order | PASS | columns=['date', 'score', 'regime', 'kospi_60d', 'sp500_60d', 'vix', 'sector_tech', 'sector_finance', 'sector_energy', 'sector_healthcare', 'sector_consumer', 'sector_industrials'] |
| 5 | score_complete_and_bounded | PASS | nulls=0, out_of_range=0 |
| 6 | unique_sorted_dates | PASS | duplicates=0, date_nulls=0, monotonic=True |
| 7 | sector_series_not_all_neutral | PASS | sector_tech:unique=978,all_50=False; sector_finance:unique=1200,all_50=False; sector_energy:unique=977,all_50=False; sector_healthcare:unique=1497,all_50=False; sector_consumer:unique=1069,all_50=False; sector_industrials:unique=1199,all_50=False |
| 8 | before_6y_score_reproducibility | PASS | overlap=500, mean_abs_diff=2.157752, median_abs_diff=0.000000, p90_abs_diff=9.615818, max_abs_diff=33.333333, share_abs_diff<=1.0=88.2000%; required overlap>=450, share>=80%, median<=0.5, mean<=5.0 |

## .before_6y 겹침 요약

- overlap_dates: `500`
- mean_abs_diff: `2.1577515570627184`
- median_abs_diff: `0.0`
- p90_abs_diff: `9.615817891097045`
- max_abs_diff: `33.33333333333334`
- share_abs_diff_le_1.0: `0.882`

## 겹치는 구간 score 대조 일부

| date | regen | before_6y | abs_diff |
|---|---|---|---|
| 2024-07-11 | 96.749067 | 63.415733 | 33.333333 |
| 2024-07-18 | 93.397933 | 60.064600 | 33.333333 |
| 2024-07-12 | 97.261200 | 63.927867 | 33.333333 |
| 2024-07-16 | 96.448467 | 63.115134 | 33.333333 |
| 2024-07-17 | 95.012267 | 61.678934 | 33.333333 |
| 2024-07-15 | 96.526400 | 63.193067 | 33.333333 |
| 2024-07-23 | 94.745066 | 61.411733 | 33.333333 |
| 2024-07-10 | 96.707222 | 63.493666 | 33.213555 |
| 2024-07-22 | 93.588228 | 61.200200 | 32.388028 |
| 2024-07-29 | 88.886575 | 59.318666 | 29.567909 |

## 최종 파일

- rows: `1759`
- first_date: `2019-07-11`
- last_date: `2026-07-10`
- sha256: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`
- empty_backup: `data/_system/market_history.csv.empty_20260712_bak`
- empty_backup_sha256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
