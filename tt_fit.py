#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""tt-fit: will this Tiny Tapeout project fit its tiles and meet 50 MHz? Answer in seconds, before the 10 minute hardening run.

Synthesizes the project with Yosys against the sky130_fd_sc_hd typical corner, then maps the pre-layout cell area to a
predicted placement utilization with a model calibrated on real LibreLane hardenings of TT tiles (calibration.json).
If OpenSTA and the sky130 slow corner Liberty are available, it also reports pre-layout setup slack at the slow corner
(ss, 100C, 1.60V) with the same constraints as the TT flow, and maps it to a pass / boundary / fail verdict.

Usage:
  tt_fit.py [PROJECT_DIR] [--tiles 1x1] [--json]
PROJECT_DIR is a Tiny Tapeout project (info.yaml with project.source_files and project.top_module, sources in src/).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('TT_FIT_CACHE', Path.home() / '.cache' / 'tt-fit'))
TT_LIB_URL = ('https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/'
              'f52850725e4ffe4030175328aa976cd8fb4943b9/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib')
TT_LIB_SHA256 = 'ec0e1067a35c8bf20b11e58d1e8ac53326067e4dac84a125cc1b917a3518d0d9'
SS_LIB_NAME = 'sky130_fd_sc_hd__ss_100C_1v60.lib'
TILES = {'1x1': 1, '1x2': 2, '2x2': 4, '3x2': 6, '4x2': 8, '6x2': 12, '8x2': 16}
# Same values as the TT LibreLane base.sdc: 20 ns clock, 20% IO delay, 0.25 ns uncertainty, 5% derate
SDC = """create_clock [get_ports clk] -name clk -period 20
set inputs [lreplace [all_inputs] [lsearch [all_inputs] [get_ports clk]] [lsearch [all_inputs] [get_ports clk]]]
set_input_delay 4 -clock [get_clocks clk] $inputs
set_output_delay 4 -clock [get_clocks clk] [all_outputs]
set_max_transition 0.75 [current_design]
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y $inputs
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y [get_ports clk]
set_load 0.033442 [all_outputs]
set_clock_uncertainty 0.25 [get_clocks clk]
set_clock_transition 0.15 [get_clocks clk]
set_timing_derate -early 0.95
set_timing_derate -late 1.05
"""


def model() -> dict:
    return json.loads((HERE / 'calibration.json').read_text())


def tt_library() -> Path:
    path = CACHE / 'sky130_fd_sc_hd__tt_025C_1v80.lib'
    if not path.is_file():
        CACHE.mkdir(parents=True, exist_ok=True)
        print('downloading sky130_fd_sc_hd typical Liberty (12.8 MB, once)...', file=sys.stderr)
        with urllib.request.urlopen(TT_LIB_URL, timeout=180) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != TT_LIB_SHA256:
            raise SystemExit('downloaded Liberty does not match the pinned SHA-256')
        path.write_bytes(data)
    return path


def ss_library() -> Path | None:
    """The slow corner Liberty ships with the sky130 PDK (ciel or volare), not as a separate download."""
    if os.environ.get('TT_FIT_SS_LIB'):
        return Path(os.environ['TT_FIT_SS_LIB'])
    for root in (Path.home() / '.ciel', Path.home() / '.volare', Path(os.environ.get('PDK_ROOT', '/nonexistent'))):
        if root.is_dir():
            found = sorted(root.glob(f'**/sky130A/libs.ref/sky130_fd_sc_hd/lib/{SS_LIB_NAME}'))
            if found:
                return found[-1]
    return None


def read_project(directory: Path) -> tuple[list[Path], str]:
    text = (directory / 'info.yaml').read_text()
    top = re.search(r'^\s*top_module:\s*"?([A-Za-z0-9_]+)"?', text, re.M)
    block = re.search(r'^\s*source_files:\s*\n((?:\s*-\s*.+\n?)+)', text, re.M)
    if not top or not block:
        raise SystemExit('info.yaml needs project.top_module and project.source_files')
    # info.yaml may come from someone else's repo: accept plain file names only, inside src/
    names = [item.strip().strip('"\'') for item in re.findall(r'-\s*(.+)', block.group(1))]
    bad = [n for n in names if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*\.(v|sv|vh|svh)', n) or '..' in n.split('/')]
    if bad or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', top.group(1)):
        raise SystemExit('unsupported source file name or top module in info.yaml: ' + ', '.join(bad or [top.group(1)]))
    src = (directory / 'src').resolve()
    files = [(src / n).resolve() for n in names]
    if any(src not in f.parents for f in files):
        raise SystemExit('source files must be inside src/')
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise SystemExit('missing source files: ' + ', '.join(missing))
    return files, top.group(1)


def synthesize(files: list[Path], top: str, work: Path) -> dict:
    lib = tt_library()
    # sources are copied under generated names so no user text reaches the Yosys script (includes resolve from src/)
    copies = []
    for index, source in enumerate(files):
        name = f'src{index}{source.suffix}'
        shutil.copyfile(source, work / name)   # source was resolved and checked to be inside src/
        copies.append(name)
    # `include files: only regular HDL files whose real path stays inside src/, no symlinks, under a fixed name
    src = files[0].parent if files else work
    root = Path(src).resolve()
    for path in root.rglob('*'):
        if path.suffix in ('.v', '.sv', '.vh', '.svh') and path.is_file() and not path.is_symlink() and root in path.resolve().parents:
            target = work / 'inc' / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    (work / 'inc').mkdir(exist_ok=True)
    script = (f"read_verilog -sv -Iinc {' '.join(copies)}; synth -flatten -top {top}; "
              f"dfflibmap -liberty {lib}; abc -liberty {lib}; opt_clean; "
              f"tee -q -o stat.json stat -json -liberty {lib}; write_verilog -noattr mapped.v")
    result = subprocess.run(['yosys', '-q', '-p', script], cwd=work, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('yosys failed:\n' + (result.stderr or result.stdout)[-1500:])
    stat = json.loads((work / 'stat.json').read_text())
    module = stat.get('design') or next(iter(stat['modules'].values()))
    kinds = module.get('num_cells_by_type', {})
    return {'area_um2': float(module['area']), 'cells': module.get('num_cells', 0),
            'flops': sum(n for name, n in kinds.items() if 'df' in name.lower())}


def slack(work: Path, top: str) -> float | None:
    sta, lib = shutil.which('sta'), ss_library()
    if not sta or not lib:
        return None
    netlist = work / 'mapped.v'
    netlist.write_text(re.sub(r'^(\s*(?:wire|input|output|inout|reg))\s+signed\b', r'\1', netlist.read_text(), flags=re.M))
    (work / 'base.sdc').write_text(SDC)
    (work / 'sta.tcl').write_text(f'read_liberty {lib}\nread_verilog mapped.v\nlink_design {top}\nread_sdc base.sdc\n'
                                  'puts "WNS [sta::format_time [sta::worst_slack_cmd max] 4]"\n')
    out = subprocess.run([sta, '-no_init', '-no_splash', '-exit', 'sta.tcl'], cwd=work, capture_output=True, text=True).stdout
    found = re.findall(r'^WNS (-?[0-9.]+)', out, re.M)
    return float(found[-1]) if found else None


def predict(area: float, tiles: int, m: dict) -> dict:
    core = m['core_area_um2'].get(str(tiles), m['core_area_um2']['1'] * tiles)
    utilization = (m['placed_per_estimate'] * area + m['placed_per_core'] * core) / core
    low, high = utilization + m['error'][0], utilization + m['error'][1]
    limit = m['max_routed_utilization']
    verdict = 'fits' if high <= limit else 'boundary' if low <= limit else 'too big'
    return {'utilization': round(utilization, 3), 'range': [round(low, 3), round(high, 3)], 'verdict': verdict}


def timing_verdict(value: float | None, m: dict, tiles: int) -> str:
    if value is None:
        return 'not checked'
    rule = m['timing']['1x1' if tiles == 1 else 'larger']
    if value >= rule['pass_min_ns']:
        return 'meets'
    return 'fails' if value <= rule['fail_max_ns'] else 'boundary'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('project', nargs='?', default='.')
    parser.add_argument('--tiles', help='tile size to check (default: smallest that fits)')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if not shutil.which('yosys'):
        raise SystemExit('yosys not found (brew install yosys, apt install yosys, or the OSS CAD Suite)')
    m = model()
    files, top = read_project(Path(args.project).resolve())
    with tempfile.TemporaryDirectory(prefix='tt-fit-') as directory:
        synth = synthesize(files, top, Path(directory))
        pre_slack = slack(Path(directory), top)
    sizes = [args.tiles] if args.tiles else list(TILES)
    checks = {name: predict(synth['area_um2'], TILES[name], m) for name in sizes}
    # smallest tile that is not too big; 'boundary' means the likely range crosses the highest utilization seen to route
    chosen = next((name for name in sizes if checks[name]['verdict'] != 'too big'), sizes[-1])
    report = {'top': top, **synth, 'tiles': chosen, **checks[chosen], 'pre_layout_ss_slack_ns': pre_slack,
              'timing': timing_verdict(pre_slack, m, TILES[chosen]), 'model': m['summary']}
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"{top}: {synth['area_um2']:,.0f} um2 of cells before layout ({synth['cells']} cells, {synth['flops']} flops)")
        print(f"  {chosen}: predicted utilization {report['utilization']:.1%} "
              f"(likely {report['range'][0]:.1%} to {report['range'][1]:.1%}) -> {report['verdict']}"
              f" (routed fine up to {m['max_routed_utilization']:.1%} in calibration)")
        if pre_slack is None:
            print('  50 MHz timing: not checked (needs OpenSTA `sta` and the sky130 PDK from ciel or volare, or TT_FIT_SS_LIB)')
        else:
            print(f"  50 MHz timing, slow corner, before layout: {pre_slack:+.2f} ns -> {report['timing']}")
        print(f"  model: {m['summary']}")
    return 0 if report['verdict'] != 'too big' and report['timing'] != 'fails' else 1


if __name__ == '__main__':
    sys.exit(main())
