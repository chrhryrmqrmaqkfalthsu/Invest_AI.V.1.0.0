#!/usr/bin/env python3
import argparse,json
from replay_common import SPECS
from replay_job import run
p=argparse.ArgumentParser();p.add_argument('--ticker',choices=sorted(SPECS),required=True);p.add_argument('--period',choices=['stress_pre_2022h1','train_1','train_2','recent_1y'],required=True)
a=p.parse_args();print(json.dumps(run(a.ticker,a.period),ensure_ascii=False,separators=(',',':')))
