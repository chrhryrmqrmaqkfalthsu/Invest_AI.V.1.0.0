# AAP 새 fitness v2 정식 독립 실행 readout

- host role: vm
- workers: 6
- 규모: qualify 100/40 × 3 fold; all3 발생 시 entry/exit/validate 연속 실행
- RNG: 장비별 독립 parent에서만 소비, fitness만 로컬 process 분산
- 병합: candidate input index 순서
- 시장 기준: 사용 가능한 root snapshot 마지막 거래일로 고정
- qualify 통과: False
- all3/all2/all1/all0: 0/0/191/109
- entry survivor: 0
- validate survivor: 0
- CE/BOIL zero: True

## Fold별 hard gate와 penalty

| fold | 후보 | 거래<12 | 거래충족·승률<60 | 두 gate 통과 | 실현손실 감점 | MAE 감점 | 거래 min/med/max | 12~13 비율 | qualify pass |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| train_1 | 300 | 214 (71.33%) | 17 (5.67%) | 69 (23.00%) | 206 (68.67%), 평균 8.899359 | 290 (96.67%), 평균 7.337504 | 1/8.0/16 | 22.00% | 52 |
| train_2 | 300 | 212 (70.67%) | 5 (1.67%) | 83 (27.67%) | 214 (71.33%), 평균 0.648503 | 292 (97.33%), 평균 0.558450 | 1/5.0/16 | 24.00% | 78 |
| train_3 | 300 | 223 (74.33%) | 10 (3.33%) | 67 (22.33%) | 298 (99.33%), 평균 1.581484 | 300 (100.00%), 평균 1.744734 | 2/6.0/16 | 24.33% | 61 |

## 새 fitness 활성

- 주목표: 평균 비용차감 실현수익 / 보유일
- 실현손실 벌점: avg(max(0, -1.0 - pnl_pct))
- 승: 비용차감 실현수익 > 0.5%
- hard gate: 거래수 >= 12 AND 승률 >= 60%
- MAE 벌점과 실현손실 벌점은 독립 차감
- mutation bias는 fitness·gate가 아니라 interval width mutation에만 사용

## 안전성

- manifest gate: True
- 보호 SHA 불변: True
- daemon proxy/starttime 불변: True
- 병렬 재현성 probe: True
- 새 fitness activation probe: True
