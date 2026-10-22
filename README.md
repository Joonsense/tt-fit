# tt-fit

**Will your Tiny Tapeout design fit its tiles and meet 50 MHz? Find out in 2 seconds, before the 10 minute hardening run.**

```
$ python3 tt_fit.py path/to/your-tt-project
tt_um_joonsense_pin_dot_b26e52f8: 6,334 um2 of cells before layout (806 cells, 105 flops)
  1x1: predicted utilization 63.4% (likely 59.5% to 70.1%) -> fits (routed fine up to 82.4% in calibration)
  50 MHz timing, slow corner, before layout: +8.56 ns -> meets
```

It synthesizes your project with Yosys against sky130_fd_sc_hd, then turns the pre-layout cell area into a **predicted
placement utilization** with a model calibrated on real LibreLane hardenings of Tiny Tapeout tiles. If OpenSTA and the
sky130 PDK are installed, it also checks setup slack at the slow corner (ss, 100C, 1.60V) with the same constraints the
TT flow uses, and says whether that has always, never, or sometimes closed after layout.

## How good is the prediction

The model was fit on 13 hardenings (LibreLane 3.0.14, sky130A, TT template) and then frozen. It was then checked on
**13 later layouts it had never seen** (9 at 1x1, 4 at 1x2):

| | |
|---|---|
| Error on the 9 unseen 1x1 layouts | **−1.4 to +2.2 percentage points** of utilization |
| Error on the 4 unseen 1x2 layouts | +0.5 to +6.4 points (dense logic came out bigger than predicted) |
| Error band it reports (from the fit set, wider on purpose) | −3.9 to +6.7 points |
| Highest utilization that routed cleanly in the data | 82.4% |
| Timing rule, 1x1 (20 ns clock) | pre-layout ss slack ≥ +1.89 ns always closed; ≤ −1.07 ns never did; in between, run the layout |
| Timing rule, larger tiles | ≥ +8.4 ns (one dense 1x2 design missed at +4.46 and +6.32 ns, closed at +8.42 ns) |

Every data point is in [`calibration.json`](calibration.json), including the failures. The larger tile line comes from
[tt-digit-classifier](https://github.com/Joonsense/tt-digit-classifier): at +4.46 ns it missed by 0.65 ns after routing
(each input pin fed about 80 gates, and wire delay does not exist before layout), at +6.32 ns it missed by 0.004 ns
(a 10 way comparison), and at +8.42 ns it closed with +5.26 ns. If you are close, register high fanout inputs and split
wide comparisons. The designs are small accelerators
and peripherals; very different designs (lots of memories, long carry chains) may sit outside the data. **It is a
prediction, not a signoff**: the TT GitHub Actions flow is still the judge.

## Install

Needs Python 3.10+ and Yosys (`brew install yosys`, `apt install yosys`, or the OSS CAD Suite). The typical corner
Liberty (12.8 MB, Apache 2.0) is downloaded once to `~/.cache/tt-fit` and checked against a pinned SHA-256.

For the timing check, also: OpenSTA (`sta` on PATH) and the sky130 PDK from `ciel` or `volare` (it looks in `~/.ciel`,
`~/.volare` and `$PDK_ROOT`, or set `TT_FIT_SS_LIB` to `sky130_fd_sc_hd__ss_100C_1v60.lib`). Without them it skips timing.

```
python3 tt_fit.py [PROJECT_DIR] [--tiles 1x2] [--json]
```

Exit code is 1 when the design is predicted too big or to fail timing, so it works as a pre-commit or CI gate.

## Where it comes from

tt-fit is the first check in the agent pipeline that designed these Tiny Tapeout accelerators, where it rejects bad
candidates before any layout time is spent:
[INT4 dot product, 2 MAC/clock](https://github.com/Joonsense/tt-int4-dot-accelerator) ·
[draw-a-digit browser demo](https://joonsense.github.io/tt-int4-dot-accelerator/) ·
[write-up](https://kaikimlabs.com/research/experiments/ai-agent-chip-design-on-free-tiny-tapeout-flow/).

License: Apache 2.0.
