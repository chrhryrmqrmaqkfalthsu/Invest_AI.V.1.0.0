#!/bin/bash
cd ~/kingmaker
mkdir -p /tmp/learn_logs
TICKERS="AAPL MSFT NVDA JPM KO XOM INTC BA"
echo "$(date '+%F %T') 병렬 학습 시작(8종목): $TICKERS" | tee /tmp/learn_logs/_master.log
PIDS=""
for T in $TICKERS; do
    nohup venv/bin/python scripts/_learn_one.py "$T" > /tmp/learn_logs/${T}.log 2>&1 &
    P=$!
    PIDS="$PIDS $P"
    echo "$(date '+%T')  $T 시작 PID=$P" | tee -a /tmp/learn_logs/_master.log
    sleep 2
done
echo "$(date '+%T') 8종목 전부 띄움. 완료 대기..." | tee -a /tmp/learn_logs/_master.log
wait $PIDS
echo "$(date '+%F %T') === 전체 학습 완료 ===" | tee -a /tmp/learn_logs/_master.log
ls -la data/_system/ga_population_dump_*.json 2>/dev/null | tee -a /tmp/learn_logs/_master.log
echo "5분 후 자동 종료. 취소: sudo shutdown -c" | tee -a /tmp/learn_logs/_master.log
sudo shutdown -h +5
