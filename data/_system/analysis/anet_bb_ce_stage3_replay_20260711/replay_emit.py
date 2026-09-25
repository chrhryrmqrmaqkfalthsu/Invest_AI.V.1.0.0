import sys,json
from replay_job import run
z=run(sys.argv[1],sys.argv[2])
print(z[sys.argv[3]],end='' if sys.argv[3]=='detail_csv' else '\n')
