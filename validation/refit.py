import csv, json, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np
VAL = Path('/private/tmp/claude-501/ttexp/val')
rows = []
for l in open(VAL / 'results.jsonl'):
    r = json.loads(l)
    if 'fit' not in r or not r['utilization']:
        continue
    m = {x['Metric']: x['Value'] for x in csv.DictReader((VAL / r['shuttle'] / 'projects' / r['project'] / 'stats' / 'metrics.csv').read_text().splitlines())}
    cfg = VAL / 'src' / r['project'] / 'src' / 'config.json'
    try:
        c = json.loads(cfg.read_text()) if cfg.exists() else {}
    except json.JSONDecodeError:
        c = {}
    rows.append({**r, 'core': float(m['design__core__area']), 'est': r['fit']['area_um2'],
                 'density': c.get('PL_TARGET_DENSITY_PCT'), 'period': c.get('CLOCK_PERIOD'), 'pre': r['fit']['pre_layout_ss_slack_ns']})
cores = defaultdict(set)
for r in rows: cores[r['tiles']].add(round(r['core'], 1))
print('core area by tiles', {t: sorted(v) for t, v in sorted(cores.items())})
def X(rs): return np.array([[r['est'] / r['core'], 1.0] for r in rs])
def y(rs): return np.array([r['utilization'] for r in rs])
train = [r for r in rows if r['shuttle'] != 'sky-26c']; test = [r for r in rows if r['shuttle'] == 'sky-26c']
coef, *_ = np.linalg.lstsq(X(train), y(train), rcond=None)
print('fit on 26a+26b: util = %.4f * est/core + %.4f  (n=%d)' % (coef[0], coef[1], len(train)))
res_tr = y(train) - X(train) @ coef; res_te = y(test) - X(test) @ coef
q = lambda e, p: float(np.quantile(e, p))
print('train residual p5 %+.3f p95 %+.3f' % (q(res_tr, .05), q(res_tr, .95)))
print('test 26c n=%d: median %+.3f p5 %+.3f p95 %+.3f mean|e| %.3f, inside train p5..p95: %.0f%%' % (len(test), q(res_te, .5), q(res_te, .05), q(res_te, .95), np.mean(abs(res_te)),
      100 * np.mean((res_te >= q(res_tr, .05)) & (res_te <= q(res_tr, .95)))))
old = lambda r: r['fit']['utilization']
print('old model on 26c: mean|e| %.3f' % np.mean([abs(r['utilization'] - old(r)) for r in test]))
for d in (60, 80, None):
    e = [r['utilization'] - float(X([r])[0] @ coef) for r in rows if r['density'] == d]
    if e: print('density', d, 'n', len(e), 'median resid %+.3f' % st.median(e))
# final fit on all rows
coef_all, *_ = np.linalg.lstsq(X(rows), y(rows), rcond=None)
res_all = y(rows) - X(rows) @ coef_all
print('final (all %d): util = %.4f * est/core + %.4f, residual p5 %+.3f p95 %+.3f' % (len(rows), coef_all[0], coef_all[1], q(res_all, .05), q(res_all, .95)))
json.dump({'rows': [{k: r[k] for k in ('shuttle', 'project', 'tiles', 'est', 'core', 'utilization', 'density', 'period', 'pre', 'setup_ws_ss')} for r in rows],
           'coef': coef_all.tolist(), 'p5': q(res_all, .05), 'p95': q(res_all, .95)}, open(VAL / 'refit.json', 'w'))
