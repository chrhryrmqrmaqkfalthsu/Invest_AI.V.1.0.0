# Stage 3 aggressive experiment output

이 폴더는 Stage 3 공격형 파이프라인 실험 산출물입니다.
단계는 qualify → entry → exit → validate 순서이며, 각 단계 산출물로 재개할 수 있습니다.
validate 단계는 최소 적격선 통과 개체를 보유·리스크·수익 라벨 카탈로그로 저장합니다.
재현 가능성을 위해 manifest, seed, 기간 정의, 설정값을 함께 저장합니다.
정리 시 삭제 가능한 실험 산출물 폴더입니다.
runner: scripts/research/run_stage3_aggressive.py
