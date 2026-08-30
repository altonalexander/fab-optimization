import io
import os
import subprocess
import sys
import threading
import time

# Use this repo's virtualenv interpreter by default; override with PYSCFABSIM_PYTHON
# (e.g. pypy3, which the original authors used and which is much faster).
PYTHON = os.environ.get('PYSCFABSIM_PYTHON') or sys.executable
DAYS = int(os.environ.get('PYSCFABSIM_DAYS', 365 * 2))
SEEDS = [int(x) for x in os.environ.get('PYSCFABSIM_SEEDS', '0,1,2,3,4,5,6,7,8,9').split(',')]
# dataset:dispatcher pairs to sweep. We standardise on LVHM, so the default
# compares dispatch rules on the same fab; override to bring HVLM back in.
MATRIX = [tuple(p.split(':')) for p in
          os.environ.get('PYSCFABSIM_MATRIX', 'LVHM:fifo,LVHM:cr').split(',')]
os.environ.setdefault('WANDB_MODE', 'offline')
os.environ.setdefault('WANDB_SILENT', 'true')

threads = []

if not os.path.exists('greedy'):
    os.mkdir('greedy')

for seed in SEEDS:
    for day in [DAYS]:
        for dataset, dispatcher in MATRIX:
            def s(day_, dataset_, dispatcher_):
                name_ = f'greedy/greedy_seed{seed}_{day}days_{dataset}_{dispatcher}.txt'
                with io.open(name_, 'w') as f:
                    print(name_)
                    subprocess.call([PYTHON, 'main.py', '--days', str(day_),
                                     '--dataset', dataset_, '--dispatcher', dispatcher_, '--seed', str(seed),
                                     '--alg', 'l4m'], stdout=f)


            t = threading.Thread(target=s, args=(day, dataset, dispatcher))
            t.start()
            time.sleep(2)
            threads.append(t)

for t in threads:
    t.join()

print('Done')
