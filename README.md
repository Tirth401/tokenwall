# Tokenwall

**An HBM3 timing model driven by LLM decode traces.**

When a large language model generates one token in decode mode, the GPU streams
every weight matrix and the whole KV cache out of memory while doing very little
arithmetic per byte. The token rate is set by memory, not math. HBM3 quotes a
peak bandwidth, but real address streams never get all of it: rows must be
opened and closed, banks have timing rules measured in nanoseconds, and refresh
takes banks offline. Tokenwall answers one question with a simulator:

> For the exact address stream of one decode step, what fraction of HBM3 peak
> bandwidth is achieved, and which timing rules ate the rest?

## Three pieces

1. **Trace generator (Python).** Turns a model shape (layers, hidden size,
   heads, KV heads, dtype, batch, sequence length) into the ordered stream of
   memory requests one decode step issues. No GPU needed: the pattern is
   deterministic from the model's shape. KV-cache layout is configurable
   because it changes row locality.
2. **Address mapping + timing core (Python + C++).** A swappable bit-slice
   mapping from linear address to (channel, pseudo channel, bank group, bank,
   row, column), then a per-bank state machine enforcing JEDEC HBM3 timing
   (tRCD, tRP, tRAS, tRC, tCCD_S/L, tRRD_S/L, tFAW, read/write turnaround,
   tREFI/tRFC). Reports cycles, achieved bandwidth, row-buffer hit rate, and
   a stall breakdown.
3. **Sweep (Python).** Varies mapping, KV layout, refresh policy, batch size and
   sequence length; plots what actually matters.

[Ramulator 2.1](https://github.com/CMU-SAFARI/ramulator2) is the reference:
it supplies the HBM3 timing parameters (see `configs/hbm3/`) and, in Phase 4,
the ground truth the C++ core is validated against.

## Status

| Phase | Scope | State |
|------:|-------|-------|
| 0 | Repo, build system, Ramulator 2.1 built and run, HBM3 parameters extracted | done (2026-09-30) |
| 1 | Decode-step trace generator | next |
| 2 | Address mapping policies + unit tests | planned |
| 3 | C++ timing core, one constraint at a time | planned |
| 4 | Validation against Ramulator 2.1 | planned |
| 5 | Sweeps and findings | planned |
| 6 | Writeup | planned |

`PROGRESS.md` has the session-by-session log and open design questions.
`RESULTS.md` has every measured number, the command that produced it, and the
date. Nothing in `RESULTS.md` is estimated or typed by hand.

## Layout

```
cpp/                 C++ timing core (Phase 3), CMake, dependency-free unit tests
python/tokenwall/    trace generator, address mapping, analysis (Phases 1, 2, 5)
configs/hbm3/        HBM3 parameters extracted from Ramulator 2.1, with provenance
configs/models/      model shapes for the trace generator (Phase 1)
scripts/             setup_ramulator2.sh, extract_hbm3_params.py, smoke and probe runs
patches/ramulator2/  two-line build fix for Apple Clang (see below)
docs/                phase walkthroughs written for study
results/             raw outputs behind every number in RESULTS.md
external/ramulator2  Ramulator 2.1 git submodule (pinned commit)
```

## Setup (macOS or Linux laptop, no paid tools)

Requirements: git, CMake 3.16+, a C++20 compiler, Python 3.10+.

```bash
git clone --recursive <this repo> tokenwall && cd tokenwall
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/setup_ramulator2.sh        # builds Ramulator 2.1 into external/, pip-installs it into .venv
cmake -S . -B build && cmake --build build && ctest --test-dir build   # Tokenwall C++ core
```

`scripts/setup_ramulator2.sh` applies `patches/ramulator2/0001-apple-clang-build-fixes.patch`:
a missing `template` keyword in `param.h` that Apple Clang 21 rejects, and a
bump of the fetched `fmt` library from 10.2.1 to 11.2.0 (10.2.1 fails to
compile under Clang 21). Nothing in the patch touches simulation behaviour.

## Reproduce Phase 0

```bash
source .venv/bin/activate
python scripts/extract_hbm3_params.py        # -> configs/hbm3/hbm3_16gb_8hi_6400.yaml
python scripts/ramulator2_hbm3_smoke.py      # -> results/phase0/smoke_summary_allbank.json
python scripts/ramulator2_probe_demo.py      # asks the device model when commands become legal
```

Read `docs/phase0_ramulator2_walkthrough.md` for what each number means.

## Honesty notes

- Ramulator 2.1's HBM3 speed bin marks 13 core timings (tCL, tRCD, tRP, tRAS,
  tFAW, ...) as "Ramulator Guesstimate": JEDEC JESD238 leaves those to vendor
  datasheets. Every Tokenwall result is therefore "HBM3 per Ramulator 2.1's
  `HBM3_6400Mbps` preset", never "HBM3 per JEDEC".
- Phase 0 bandwidth figures come from synthetic traffic. They prove the
  toolchain works and are not project findings.
