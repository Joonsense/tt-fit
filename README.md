# tt-fit

**Will your Tiny Tapeout design fit its tiles and meet 50 MHz? Find out in 2 seconds, before the 10 minute hardening run.**

```
$ python3 tt_fit.py path/to/your-tt-project
tt_um_joonsense_pin_dot_b26e52f8: 6,334 um2 of cells before layout (806 cells, 105 flops)
  1x1: predicted utilization 59.8% (likely 52.6% to 68.1%) -> fits (real projects routed up to 92.9%)
  50 MHz timing: +8.56 ns before layout: 0 of 167 real projects in this range (0%) missed 50 MHz at the slow corner after layout
```

It synthesizes your project with Yosys against sky130_fd_sc_hd, then turns the pre-layout cell area into a **predicted
placement utilization** with a model calibrated on 436 real Tiny Tapeout shuttle projects. If OpenSTA and the
sky130 PDK are installed, it also checks setup slack at the slow corner (ss, 100C, 1.60V) with the same constraints the
TT flow uses, and says whether that has always, never, or sometimes closed after layout.

## How good is the prediction

Version 2 is calibrated on **436 real projects** from three Tiny Tapeout sky130 shuttles (ttsky26a, 26b, 26c): for
each, tt-fit ran on the project's own sources at the submitted commit and was compared with the shuttle's published
layout metrics. Every row is in [`calibration.json`](calibration.json).

| | |
|---|---|
| Held out test | fit on 26a and 26b only (321 projects), then predicted the 115 projects of 26c: mean error **4.4 points**, median bias +0.9, **92% inside the 90% band** |
| Band it reports | prediction −7.1 to +8.4 points (5th to 95th percentile of the error) |
| Highest utilization that routed | 92.9% (103 projects above 80%, 23 above 90%); "fits" means the whole band stays under 90% |
| Dense arithmetic | designs that are mostly adders and multipliers land near the top of the band (three of mine: +4.6, +6.8, +9.4 points) |

Timing is reported as what actually happened to real projects with a 20 ns clock and the same pre layout slack:

| Pre layout slow corner slack | Projects | Missed 50 MHz after layout |
|---|---|---|
| below −2 ns | 45 | 37 (82%) |
| −2 to 0 ns | 20 | 12 (60%) |
| 0 to +2 ns | 22 | 5 (23%) |
| +2 to +4 ns | 25 | 8 (32%) |
| +4 to +6 ns | 52 | 7 (13%) |
| +6 to +8 ns | 43 | 2 (5%) |
| +8 ns and up | 167 | **0** |

Version 1 was fit on 13 layouts of one designer and claimed a ±3 point band and a "+1.89 ns always closes" rule.
On the 436 real projects it over predicted by a median 3.8 points, and 17 projects at +2 ns or more missed timing.
Both claims are withdrawn. **It is a prediction, not a signoff**: the TT GitHub Actions flow is still the judge.

## Install

Needs Python 3.10+ and Yosys (`brew install yosys`, `apt install yosys`, or the OSS CAD Suite). The typical corner
Liberty (12.8 MB, Apache 2.0) is downloaded once to `~/.cache/tt-fit` and checked against a pinned SHA-256.

For the timing check, also: OpenSTA (`sta` on PATH) and the sky130 PDK from `ciel` or `volare` (it looks in `~/.ciel`,
`~/.volare` and `$PDK_ROOT`, or set `TT_FIT_SS_LIB` to `sky130_fd_sc_hd__ss_100C_1v60.lib`). Without them it skips timing.

```
python3 tt_fit.py [PROJECT_DIR] [--tiles 1x2] [--json]
```

Exit code is 1 when the design is predicted too big, or when at least half of the real projects with its pre layout
slack missed timing, so it works as a pre-commit or CI gate.

## GitHub Action

Add a report to every push of your Tiny Tapeout repo (`.github/workflows/tt-fit.yaml`):

```yaml
name: tt-fit
on: [push, workflow_dispatch]
jobs:
  fit:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: Joonsense/tt-fit@v1
```

It installs Yosys from the OSS CAD Suite, runs the area and tile prediction, and writes a table to the job summary in
about a minute. Timing is skipped in CI (OpenSTA and the sky130 slow corner Liberty are not installed there).
Inputs: `project-dir`, `tiles`, `fail-on-risk: true` to fail the step on a "too big" verdict.

## Where it comes from

tt-fit is the first check in the agent pipeline that designed these Tiny Tapeout accelerators, where it rejects bad
candidates before any layout time is spent:
[INT4 dot product, 2 MAC/clock](https://github.com/Joonsense/tt-int4-dot-accelerator) ·
[draw-a-digit browser demo](https://joonsense.github.io/tt-int4-dot-accelerator/) ·
[write-up](https://kaikimlabs.com/research/experiments/ai-agent-chip-design-on-free-tiny-tapeout-flow/).

License: Apache 2.0.
