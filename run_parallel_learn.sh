#!/bin/bash
cd ~/kingmaker
mkdir -p /tmp/learn_logs
TICKERS="AAPL MSFT NVDA JPM KO XOM INTC BA"
echo "$(date '+%F %T') 병렬 학습 시작(8종목): $TICKERS" | tee /tmp/learn_logs/_master.log
for T in $TICKERS; do
    nohup venv/bin/python scripts/_learn_one.py "$T" > /tmp/learn_logs/${T}.log 2>&1 &
    echo "$(date '+%T')  $T 시작" | tee -a /tmp/learn_logs/_master.log
    sleep 2
done
echo "$(date '+%T') 8종목 전부 띄움" | tee -a /tmp/learn_logs/_master.log
