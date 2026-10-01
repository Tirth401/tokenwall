# Tokenwall

An HBM3 timing simulator driven by the exact memory traffic of a large
language model generating one token, validated cycle-exact against
[Ramulator 2.1](https://github.com/CMU-SAFARI/ramulator2).

**The question.** When an LLM generates a token in decode mode, the GPU
streams every weight and the whole KV cache out of HBM while doing almost no
arithmetic per byte, so the token rate is set by memory. HBM3 quotes a peak
bandwidth. How much of it does a real decode step get, and which DRAM timing
rules eat the rest?

**The answer for Llama 3 8B at batch 1 on one HBM3 stack** (qualifiers in
"What is validated"; every number in `RESULTS.md` with its command):

| Configuration | % of peak | ms per token | tokens/s |
|---|---:|---:|---:|
| Floor: every byte at peak | 100 | 19.0 | 52.7 |
| Ramulator's default address mapping, all-bank refresh | 42.0 | 45.2 | 22.1 |
| Bank-group-interleaved mapping (`bank_low`), all-bank refresh | 83.7 | 22.7 | 44.1 |
| `bank_low`, per-bank refresh, controller keeps scheduling during refresh | 92.2 | 20.6 | 48.6 |

Under the default mapping a third of all column-command slots wait on one
rule, tCCD_L, the minimum spacing between reads to the same bank group.
Changing which address bits pick the bank group doubles the bandwidth at an
unchanged 96% row-hit rate.

![mapping and refresh](results/phase5/plots/mapping_refresh.png)

## How it works

1. **Trace generator (Python).** A published Llama 3 `config.json` plus batch,
   context length and tensor-parallel degree becomes the ordered stream of
   32-byte reads and writes of one decode step: 485,839,360 requests for 8B at
   batch 1. The stream is stored as a compact "segment list" recipe that
   Python and C++ expand identically (hash-verified).
2. **Address mapping + timing core (C++).** Four bit-slice policies map each
   address to channel, pseudo channel, SID, bank group, bank, row and column.
   A per-channel controller (FR-FCFS, 32-entry queues, open-row policy,
   all-bank or per-bank refresh, HBM3's split command bus) issues commands
   against a timing tree enforcing all 51 of Ramulator's HBM3 rules in
   half-clock ticks. It reports cycles, bandwidth, row hits, misses and
   conflicts, and labels every idle column-command slot with the rule that
   blocked the oldest request.
3. **Sweeps (Python).** Mapping, refresh, batch, context, KV layout, GQA,
   model size and single-rule ablations, with plots.

Ramulator 2.1 supplies the HBM3 timing table (extracted with provenance into
`configs/hbm3/`) and the ground truth: on 30 configurations, including
writes, per-bank refresh and three workloads, Tokenwall's ticks, hits, misses
and conflicts are identical to Ramulator's, and 2,081,313 consecutive DRAM
commands match one for one. Tokenwall runs 1.5 to 1.8x faster.

`docs/architecture.md` has the pipeline diagram, file formats, test layers
and a glossary of every DRAM term used here.

## Quick start

Requirements: git, CMake 3.16+, a C++20 compiler, Python 3.10+ (tested with
3.12). Ramulator is needed only for the validation scripts; the timing table
it provides is committed.

```bash
git clone --recursive https://github.com/Tirth401/tokenwall.git && cd tokenwall
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
cmake -S . -B build && cmake --build build && ctest --test-dir build      # 35 C++ cases
scripts/setup_ramulator2.sh                                             # builds Ramulator 2.1 (5 to 10 minutes), optional
.venv/bin/python -m pytest -q                                           # 94 tests
```

Reproduce the headline comparison in about a minute:

```bash
source .venv/bin/activate
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 300000 --policies ramulator,bank_low --refresh allbank
```
Each case prints both simulators' statistics side by side with
`integer statistics identical: True`, and the two mappings land near 42% and
84% of peak.

Run a decode step yourself (full step: 7 to 13 minutes; add `--layers 0:1`
to the generator for a one-minute layer):

```bash
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 4096 --out traces/step
./build/cpp/tokenwall sim --segs traces/step.segs --policy bank_low --stacks 1 --refresh allbank --drain --json step.json
```

## Findings

All from one-layer sweeps (the per-layer fraction of peak matches the full
step within 0.1 point) on the Tokenwall core; `docs/phase5_findings.md` has
the full tables, the mechanisms and the caveats.

1. **Address mapping is the first-order knob.** 42% versus 84% of peak in
   every refresh mode. XOR hashing of bank bits adds nothing; the
   bank-bits-high anti-pattern gets 30%.
2. **Batch size, context length, GQA versus MHA, and 8B versus a 70B shard do
   not move the fraction of peak**; they move bytes per step. Under a matched
   layout, KV reads are long sequential runs like weight sweeps.
3. **The KV-cache layout must match the attention kernel's walk order, under
   either mapping.** A mismatch costs 2 to 5x while row hits stay above 95%:
   one head's stream lands on a quarter of the channels. A static locality
   analysis in Phase 2 missed this; timing simulation did not.

   ![kv layout](results/phase5/plots/kv_layout.png)

4. **Refresh cost is a controller decision.** All-bank refresh costs 10 points
   under `bank_low`. Per-bank refresh costs 26 points under Ramulator's rule
   that nothing is scheduled while a refresh waits, and 2 points once the
   controller keeps scheduling other banks and reserves the target bank. The
   first non-blocking attempt starved refresh (longest postponement 567 µs);
   a refresh-postponement statistic caught it.

   ![controller](results/phase5/plots/controller.png)

5. **Rule ablation ranks the losses.** Default mapping: tCCD_L 15 points, tRCD
   4, tRP 3. `bank_low`: tCCD_R 2.4, tRP 1.5, tFAW 1.3. Write turnarounds cost
   nothing on decode traffic. (`results/phase5/plots/ablation.png`,
   `attribution.png`, `batch_seq.png`, `gqa_70b.png`)

## What is validated, and what is not

**Validated.** The implementation, against Ramulator 2.1: identical integer
statistics on 30 configurations (`RESULTS.md`, Phases 3 and 4), identical
command traces on a 2 M-request slice, and a diff tool that locates a
deliberately removed rule at the exact command. Per-rule unit tests carry
hand-derived expected ticks that match Ramulator's own device model.

**Not validated, and said with every number.**

- The HBM3 timings are Ramulator 2.1's `HBM3_6400Mbps` preset. Thirteen core
  timings (tCL, tCWL, tFAW, tRAS, tRCDRD, tRCDWR, tRP, tRRD_L, tRRD_S, tRTP,
  tWR, tWTR_L, tWTR_S) are marked `Ramulator Guesstimate` in its source;
  JEDEC leaves them to vendor datasheets. Results are "HBM3 per Ramulator
  2.1's preset", never "per JEDEC". Every timing in `configs/hbm3/*.yaml`
  carries a `source` tag.
- The controller is Ramulator's HBM34 model: FR-FCFS, 32-entry queues,
  priority-first refresh. Production GPU controllers have deeper queues and
  their own hashing; the non-blocking refresh variant is a Tokenwall
  extension.
- Requests enter from a trace replay at one request per channel per tick, a
  single ordered stream standing in for thousands of concurrent warps. This
  is why coarse channel interleaves look catastrophic here.
- No L2 cache, prefill, MoE, speculative decoding or paged KV cache; stack
  counts are powers of two.

**Swapping in real vendor timings.** One flag per timing, in clock cycles;
the output file is forced to carry a distinct name, and overridden keys are
tagged:

```bash
python scripts/extract_hbm3_params.py --override nRCDRD=28 --override nRP=24 --name vendor_x   # YAML with source tags
python scripts/export_dram_spec.py --override nRCDRD=28 --override nRP=24 --out configs/hbm3/vendor_x.spec
./build/cpp/tokenwall sim --spec configs/hbm3/vendor_x.spec --segs ... --policy bank_low --stacks 1
```
Derived timings (tRC, tRTW, tREFIpb and friends) are not recomputed from
overridden inputs; override them too when a datasheet changes their inputs.

## Repository map

```
cpp/                 C++20: segment expander, address mapping, DRAM spec loader, timing tree + bank
                     state machine, controller, simulation loop, `tokenwall sim`, 35 unit tests
python/tokenwall/    trace generator, segment format, address mapping, locality analysis, CLI
configs/hbm3/        HBM3 tables extracted from Ramulator 2.1 with provenance (YAML for people, .spec for C++)
configs/models/      Llama 3 8B and 70B shapes imported from published config.json, raw files kept
scripts/             setup, extraction, importers, Ramulator runners, validation, sweeps, plots
tests/               94 pytest cases incl. Python-vs-C++ cross-checks and CLI end-to-end
patches/ramulator2/  0001 Apple Clang build fixes; 0002 flat address on pre-mapped traces
docs/                architecture + glossary, six phase walkthroughs, resume bullets
results/             raw outputs behind every number in RESULTS.md, plots under phase5/plots
traces/              generated traces (.segs regenerate in seconds; .meta.json committed)
external/ramulator2  Ramulator 2.1 git submodule, pinned at 72427a1b
```

`RESULTS.md` is the only source of numbers: each entry has the command that
produced it and the date. `PROGRESS.md` is the session log with decisions,
their alternatives, and what to do next.

## Documentation

- `docs/architecture.md`: how the pieces fit, the three file formats, the
  test pyramid, what is and is not modelled, glossary.
- `docs/resume_bullets.md`: the claims this project supports, each with the
  command that reproduces it, its qualifiers, and a three-minute live demo.
- Phase walkthroughs, written to be studied in order:
  `docs/phase0_ramulator2_walkthrough.md` (where the HBM3 numbers live),
  `phase1_trace_generator.md`, `phase2_address_mapping.md`,
  `phase3_timing_core.md`, `phase4_validation.md`, `phase5_findings.md`.

## Status

| Phase | Scope | State |
|------:|-------|-------|
| 0 | Repo, build, Ramulator 2.1 built and run, HBM3 parameters extracted with provenance | done 2026-09-30 |
| 1 | Decode-step trace generator, Llama 3 configs, Python/C++ cross-checked trace format | done 2026-09-30 |
| 2 | Four mapping policies, bit-exact with Ramulator's default, measured on a full layer | done 2026-09-30 |
| 3 | C++ timing core with stall attribution, cycle-exact with Ramulator on first run | done 2026-09-30 |
| 4 | Validation matrix: writes, per-bank refresh, interleaves, 32 channels, 70B; command-level diff | done 2026-09-30 |
| 5 | Sweeps and findings, 92 runs, 7 figures | done 2026-10-01 |
| 6 | Writeup, pinned versions, fresh-clone check | done 2026-10-01 |

Tested with macOS (Apple M5), Apple Clang 21, CMake 4.4.3, Python 3.12.10,
Ramulator 2.1 at commit 72427a1b, package versions pinned in `requirements.txt`.
Author: Tirth Shah, with Claude as pair programmer; built as interview
preparation for memory-architecture roles.
