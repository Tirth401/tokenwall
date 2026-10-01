# Tokenwall architecture and glossary

How the pieces fit, what each file format is, how the tests are layered, and
what the DRAM words mean. The phase walkthroughs (`docs/phase0_*.md` to
`phase5_*.md`) tell the story in the order it was built; this document is the
map.

## 1. Pipeline

```
 configs/models/*.yaml            configs/hbm3/*.yaml  +  *.spec
 (from published config.json)     (from Ramulator 2.1's HBM3 DSL, with provenance tags)
          |                                   |
          v                                   |
 python -m tokenwall gen  --------> traces/<run>.segs  (segment list: a recipe for 10^8..10^9 requests)
   tracegen.py / layout.py                   |
                                             |  expanded identically by
                           +-----------------+------------------+
                           v                                    v
              python/tokenwall/segments.py             cpp/.../segments.h (Expander)
              (numpy expander, stream hash,                      |
               Ramulator LD/ST export)                           v
                           |                      cpp/.../addrmap.h  (policy: address -> ch,pc,sid,bg,bank,row,col)
                           v                                    v
              python/tokenwall/addrmap.py              cpp/.../controller.h  (per channel: queues, FR-FCFS, refresh,
              (same 4 policies, locality analysis)        HBM3 command-bus rules, stall attribution)
                           |                                    v
                           |                           cpp/.../device.h      (timing tree + bank state machine,
                           |                                                  rules from the .spec file)
                           v                                    v
          Ramulator 2.1 (external/ramulator2, patched)   tokenwall sim  -> JSON: ticks, GB/s, % of peak, hits,
          fed the same traffic, flat or pre-mapped         misses, conflicts, latency, per-slot attribution,
                           |                                  refresh postponement, per-class stats
                           +--------- scripts/tokenwall_vs_ramulator.py, cmd_trace_diff.py ---------+
                                                   (identical statistics and command traces)
```

Phase 5 drives `tokenwall sim` over knobs with `scripts/sweep.py` and draws
`results/phase5/plots/*.png` with `scripts/plot_sweeps.py`.

## 2. The three data formats

**`.segs` (segment list).** Text. Header `tokenwall-segments 1`,
`request_bytes 32`, tensor table lines `T id kind layer base nbytes name`,
then groups. `G <chunk_bytes>` starts a group; each `S R|W base run_bytes runs
stride outer_runs outer_stride tensor_id` expands to
`outer_runs x runs x run_bytes/32` requests with two-level striding. A group
with `chunk_bytes 0` plays its segments in order; otherwise it round-robins
`chunk_bytes` from each (parallel streams). Both expanders must produce the
identical request sequence; `tests/test_cross_expander.py` hashes both.

**`.spec` (DRAM rule table).** Text, written by `scripts/export_dram_spec.py`
from Ramulator's own resolved tables: levels and counts, commands and their
command-bus lengths in half-CK ticks, 29 timings in ticks, read latency, and
60 constraint entries `constraint id level latency window sibling group name
P <preceding ids> F <following ids>`. Names like `BankGroup:nCCDL` are what
the attribution prints. `--override nRCDRD=28 --out ...` produces a vendor
variant.

**Command trace CSV.** `tokenwall sim --cmd-trace` writes
`clock,command,Channel,PseudoChannel,Sid,BankGroup,Bank,Row,Column,type`
per issued command; Ramulator's `CmdTraceRecorder` writes the same columns
plus `source`, one file per channel. `scripts/cmd_trace_diff.py` compares them.

Run summaries are JSON (`--json`): the `result` object holds every statistic
and the `slot_reasons` map.

## 3. Where the numbers come from

- HBM3 organisation and timings: Ramulator 2.1's `python/ramulator/dram/hbm3.py`
  (commit `72427a1b`), extracted by `scripts/extract_hbm3_params.py` into
  `configs/hbm3/hbm3_16gb_8hi_6400.yaml` with a `source` tag per timing
  (speed bin, Ramulator estimate, derived formula, user override). Thirteen
  core timings are Ramulator's estimates.
- Model shapes: Hugging Face `config.json` files fetched by
  `scripts/import_hf_config.py` with URL, SHA-256 and the raw file kept.
- Peak bandwidth: 32 B per 2 CK per pseudo channel at tCK 0.625 ns = 25.6 GB/s;
  51.2 per channel; 819.2 per 16-channel stack.
- Every reported number: `RESULTS.md`, with the producing command and date.

## 4. The test pyramid

| Layer | What it checks | Where |
|---|---|---|
| Unit, per rule | each timing rule's earliest legal tick, hand-derived | `cpp/tests/test_timing.cpp` (17) |
| Unit, controller | command-by-command timelines incl. refresh | `cpp/tests/test_controller.cpp` (8) |
| Unit, Python | generator byte totals, layouts, slices, mapping bijections, bit-exact mapping vs a transcription of Ramulator's C++ | `tests/test_tracegen.py`, `tests/test_addrmap.py`, `tests/test_locality.py` |
| Cross-language | Python and C++ expanders and mappers produce identical streams | `tests/test_cross_expander.py`, `tests/test_cross_addrmap.py` |
| End to end | the CLI serves every request, splits 64 B requests | `tests/test_sim_cli.py` |
| Simulator identity | Tokenwall equals Ramulator 2.1: statistics on 30 cases, command traces | `scripts/tokenwall_vs_ramulator.py`, `scripts/cmd_trace_diff.py` |
| Negative control | a removed rule is located at the exact command | Phase 4 injected tCCD_L deviation |

`ctest --test-dir build` runs 35 C++ cases; `python -m pytest -q` runs 94.

## 5. What is modelled and what is not

Modelled: one HBM3 stack (or a power-of-two number of stacks) of 16 channels,
each with 2 pseudo channels, 2 SIDs, 4 bank groups, 4 banks, 16384 rows of
1 KiB; all 51 of Ramulator's HBM3 timing rules in half-CK ticks including
command-bus occupancy; FR-FCFS over 32-entry read and write queues with write
watermarks, open-row policy, all-bank and per-bank refresh, HBM3's split
command bus with rising/falling-edge rules; writes with coalescing and read
forwarding; one decode step's weights, KV reads, KV appends, embedding rows,
norms and lm_head for one tensor-parallel shard.

Not modelled: an L2 cache in front of HBM; the GPU's many concurrent request
streams (a single ordered stream with per-channel queues stands in; knobs
exist to interleave streams); prefill; MoE; speculative decoding; paged KV
caches; non-power-of-two stack counts; refresh deadline enforcement beyond
reporting postponement; thermal or power effects; vendor-specific timings
(available through the override path).

## 6. Glossary

**Decode step.** Generating one token: every weight matrix is read once and
the KV cache for all past positions is read; arithmetic per byte is tiny, so
memory bandwidth sets the token rate.

**KV cache.** Per layer, per past position, per KV head: one key vector and
one value vector (256 bytes each here). Read in full at every step; appended
by one position per step.

**GQA / MHA.** Grouped-query attention shares one KV head among several query
heads (Llama 3 8B: 32 query heads, 8 KV heads); full multi-head attention has
one KV head per query head and 4x the KV traffic.

**Tensor parallelism (TP).** Splitting heads and MLP columns across GPUs; each
GPU holds its share of the weights and KV cache. Tokenwall simulates one
shard.

**Channel / pseudo channel.** An HBM3 stack has 16 independent 64-bit
channels; each runs as two 32-bit pseudo channels sharing command wires but
with separate data wires and independent bank state.

**SID.** Stack ID: which of two dies serves a channel. Behaves like a rank,
another independent set of banks behind the same wires.

**Bank, bank group, row, column.** A bank is an array with one row buffer; a
row is 1 KiB per pseudo channel; a column access moves 32 bytes; banks are
grouped so that consecutive accesses within a group are slower (tCCD_L) than
across groups (tCCD_S).

**ACT, RD, WR, PRE (PREpb / PREab), REFab / REFpb.** Activate (open a row into
the row buffer), read, write, precharge (close the row; per bank or all
banks), refresh (all banks of a pseudo channel, or one bank).

**Row hit / miss / conflict.** The requested row is open (hit: RD now); the
bank is closed (miss: ACT then RD); another row is open (conflict: PRE, ACT,
RD).

**tRCD, tRP, tRAS, tRC.** ACT to RD; PRE to ACT; minimum row-open time; ACT
to ACT in the same bank (= tRAS + tRP).

**tCCD_S / tCCD_L / tCCD_R.** Minimum spacing between column commands to
different bank groups, the same bank group, and a different SID. nBL, the
burst length, is 2 CK; tCCD_S equals it, tCCD_L is twice it.

**tRRD_S / tRRD_L, tFAW.** ACT-to-ACT spacing across and within bank groups;
at most four ACTs per pseudo channel in any tFAW window (15 ns).

**tWTR_S / tWTR_L, tRTW, tPPD.** Write-to-read turnaround (other / same bank
group), read-to-write turnaround, precharge-to-precharge spacing.

**tREFI, tRFC, tREFIpb, tRFCpb.** All-bank refresh interval (3.9 µs) and
duration (350 ns); per-bank refresh interval (122 ns) and duration (200 ns).

**Half-CK tick.** Ramulator models HBM3 in half-clock steps (312.5 ps) because
an ACT occupies the command bus for 1.5 CK and a PRE for 0.5 CK; rule
latencies are adjusted for command lengths, so ACT to RD measures 63 ticks
for a 31 CK tRCD.

**FR-FCFS.** First-ready, first-come-first-served: among requests whose next
command is legal now, the oldest wins; a row hit beats an older miss.

**Open-row policy.** Leave a row open after an access hoping the next access
hits it; the alternative closes rows eagerly.

**Write mode / watermarks.** Writes are buffered and drained in batches when
the write queue passes 80% or no reads wait, until it falls under 20%.

**Address mapping policy.** Which bits of the 32 B line index select channel,
pseudo channel, SID, bank group, bank, row and column. `ramulator`,
`bank_low`, `bank_high`, `bank_low_xor` in this project.

**Stall attribution.** Each (pseudo channel, rising edge) is a column-command
slot: labelled `data` when a burst occupies the wires, otherwise the command
and rule blocking the oldest schedulable request, or `empty` / `arbitration`
/ `refresh:*`.

**Segment list.** The compact trace format: "start here, read this much,
repeat with this stride", instead of listing hundreds of millions of lines.

## 7. Phase documents

| Doc | What you learn |
|---|---|
| `phase0_ramulator2_walkthrough.md` | where HBM3 timings live in Ramulator 2.1, what each one means, the 42% smoke result and why |
| `phase1_trace_generator.md` | what a decode step reads, the KV cache, layouts, the segment format, why one shard |
| `phase2_address_mapping.md` | the four policies, bit-exactness proof, 42% vs 84% under Ramulator timing, hit rate vs pairing |
| `phase3_timing_core.md` | the timing tree, the controller, per-rule tests, first validation identical, attribution |
| `phase4_validation.md` | writes, per-bank refresh, the matrix, command-level diff, the harness bug, the planted bug |
| `phase5_findings.md` | sweeps, the KV-layout surprise, refresh as a controller decision, ablation, resume-grade claims |
| `resume_bullets.md` | the claims, each with its command and qualifiers, and a three-minute demo |
