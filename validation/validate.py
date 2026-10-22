"""Large scale check of tt-fit: run it on real Tiny Tapeout sky130 shuttle projects and compare with their published layout metrics.
Sources are third party code: one folder per project, tt_fit reads them only through file name allow listing and fixed copies.
"""
import csv, json, os, re, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VAL = Path('/private/tmp/claude-501/ttexp/val')
WORK = VAL / 'src'
OUT = VAL / 'results.jsonl'
TTFIT = Path('/private/tmp/claude-501/ttexp/ttfit/tt_fit.py')
TOKEN = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True).stdout.strip()
KEYS = {'utilization': 'design__instance__utilization', 'setup_ws_ss': 'timing__setup__ws__corner:nom_ss_100C_1v60',
        'instance_area': 'design__instance__area'}


def api(url):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def raw(owner, repo, commit, path):
    url = f'https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path}'
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def project(shuttle, folder):
    info = (folder / 'info.yaml').read_text(errors='replace')
    lang = (re.search(r'^\s*language:\s*["\']?([^"\'#\n]+)', info, re.M) or [None, ''])[1].strip().lower()
    if 'verilog' not in lang or 'mixed' in lang or 'gds' in lang:
        return None
    meta = json.loads((folder / 'commit_id.json').read_text())
    m = folder / 'stats' / 'metrics.csv'
    if not m.exists():
        return None
    rows = {r['Metric']: r['Value'] for r in csv.DictReader(m.read_text().splitlines())}
    tiles = (re.search(r'^\s*tiles:\s*["\']?([0-9]x[0-9])', info, re.M) or [None, None])[1]
    rec = {'shuttle': shuttle, 'project': folder.name, 'repo': meta['repo'], 'commit': meta['commit'], 'tiles': tiles,
           **{k: float(rows[v]) if rows.get(v) not in (None, '', 'inf') else None for k, v in KEYS.items()}}
    return rec


def fetch(rec):
    owner, repo = rec['repo'].rstrip('/').removesuffix('.git').split('/')[-2:]
    dest = WORK / rec['project']
    if (dest / 'info.yaml').exists():
        return dest
    tree = api(f'https://api.github.com/repos/{owner}/{repo}/git/trees/{rec["commit"]}?recursive=1')
    files = [t['path'] for t in tree.get('tree', []) if t['type'] == 'blob' and (t['path'] == 'info.yaml' or
             (t['path'].startswith('src/') and re.search(r'\.(v|sv|vh|svh|json)$', t['path'])))]
    if 'info.yaml' not in files:
        raise RuntimeError('no info.yaml at commit')
    for path in files:
        if '..' in path.split('/'):
            continue
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw(owner, repo, rec['commit'], path))
    return dest


def run(rec):
    try:
        dest = fetch(rec)
        cfg = dest / 'src' / 'config.json'
        period = None
        if cfg.exists():
            try:
                period = json.loads(cfg.read_text()).get('CLOCK_PERIOD')
            except json.JSONDecodeError:
                period = 'unparsed'
        args = [sys.executable, '-I', str(TTFIT), str(dest), '--json'] + (['--tiles', rec['tiles']] if rec['tiles'] else [])
        p = subprocess.run(args, capture_output=True, text=True, timeout=600, cwd=str(VAL))
        if p.returncode not in (0, 1) or not p.stdout.strip().startswith('{'):
            return {**rec, 'clock_period': period, 'error': (p.stderr or p.stdout)[-300:]}
        return {**rec, 'clock_period': period, 'fit': json.loads(p.stdout)}
    except Exception as e:  # noqa: BLE001
        return {**rec, 'error': f'{type(e).__name__}: {e}'[:300]}


def main():
    done = {json.loads(l)['project'] for l in OUT.read_text().splitlines()} if OUT.exists() else set()
    recs = [r for s in ('sky-26a', 'sky-26b', 'sky-26c') for f in sorted((VAL / s / 'projects').glob('tt_um_*'))
            if (r := project(s, f)) and r['project'] not in done]
    print(len(recs), 'to run', flush=True)
    with ThreadPoolExecutor(6) as pool, OUT.open('a') as out:
        for i, res in enumerate(pool.map(run, recs)):
            out.write(json.dumps(res) + '\n'); out.flush()
            if i % 25 == 0:
                print(i, res['project'], 'error' if 'error' in res else res['fit']['utilization'], flush=True)


if __name__ == '__main__':
    main()
