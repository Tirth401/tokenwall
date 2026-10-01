# Phase 4 walkthrough: proving two simulators agree, and what to do when they don't

Written for study. Plain words and an example first, then the technical term.
Numbers are from `RESULTS.md` (Phase 4).

## 1. Two librarians keeping logs

Plain version: Phase 3 showed that Tokenwall and Ramulator end their shift
with the same tallies: same number of books opened, same number of lines
read, same closing time. That could still hide two different days that
happened to add up the same. Phase 4 makes both librarians write down every
action with its tick, lays the two logs side by side, and stops at the first
line that differs. Then it widens the test to writing, to a second refresh
scheme, to other floor plans, and to two more workloads.

Technical version: `tokenwall sim --cmd-trace` streams every issued DRAM
command as CSV; Ramulator's `CmdTraceRecorder` plugin does the same per
channel. `scripts/cmd_trace_diff.py` normalizes both, walks each channel's
sequence and reports the first mismatch with the preceding commands to that
bank from both logs and the bank state each log implies (open row, last
tick per command). On the 2 M-request layer slice, 2,081,313 commands were
identical across 16 channels.

## 2. Writes needed a one-line favour from Ramulator

Plain version: Ramulator's pre-mapped trace reader never learned each
request's street address, only its room number. Its rule "if a write to the
same address is already waiting, merge them" therefore merged everything with
everything.

Technical version: `ReadWriteTrace` left `req.addr` at -1, and the
controller keys write coalescing and read forwarding on `req.addr`. Patch
`0002` accepts an optional third token with the flat address. The check in
RESULTS shows the old format forwarding a read to a different address and the
patched one forwarding only the true match. With that fixed, every matrix
case ran with the KV-append writes included. (The writes are too few to move
the tick counts on these traces, which is itself worth knowing.)

## 3. The matrix

Thirty cases, every one identical in ticks, served requests, hits, misses and
conflicts:

- four mappings, three refresh modes (none, all-bank, per-bank), 2 M requests;
- channel interleave of 32 B, 256 B and 1 KiB;
- 32 channels;
- the whole layer, all-bank and per-bank refresh;
- a batch-32 slice where 55% of the traffic is KV reads, on 32 channels;
- a Llama 3 70B tensor-parallel shard layer on 32 channels.

The one case that first came out different was the harness, not a simulator:
the 32-channel `bank_low` run gave Ramulator exactly its 16-channel tick
count. The pre-mapped trace had been built with a 16-channel geometry, so
half the channels never saw traffic. That is precisely the kind of thing a
matrix is for; the exporter now takes `--channels` and the rerun is
identical. The honest fidelity log therefore reads: zero simulator
discrepancies, one harness bug, one reporting bug from Phase 3, and one
injection that changed nothing.

## 4. Proving the magnifying glass works

Plain version: a smoke detector that never beeps might be perfect or might be
broken. So we lit a match.

Technical version: Tokenwall ran with tCCD_L removed (`--disable nCCDL`).
The diff tool reported the first divergence at the third command of channel
0: a read at tick 69 instead of 73, four ticks early, which is the difference
between tCCD_S and tCCD_L, with both banks' histories printed. An earlier
attempt removed tPPD and found nothing, because two precharges never fall
within 4 ticks of each other on this traffic. Both facts are logged.

## 5. Per-bank refresh: a controller problem wearing a DRAM costume

Plain version: all-bank refresh closes the whole room every 3.9 microseconds
for 350 nanoseconds. Per-bank refresh instead taps one desk every 122
nanoseconds for 200. Spread out should be better. It is worse here, by a
lot: 29% of peak instead of 42% with the default mapping, 68% instead of 84%
with `bank_low`.

Technical version: the attribution says why. A quarter of all column slots
are `refresh:REFpb:Bank:nRP`. Each REFpb targets a bank that is almost always
open under an open-row policy, so it first needs a PREpb, then must wait tRP
(16 ns) before the REFpb itself may issue. During that wait the controller,
following Ramulator's rule that nothing else is scheduled while a priority
request is pending, serves no reads or writes on either pseudo channel. Add
`ACT:Bank:nRFCpb` for the bank actually being refreshed. The DRAM protocol
would allow other banks to keep working; the controller policy does not. That
is a Phase 5 experiment and an interview answer: refresh policy and controller
policy cannot be evaluated separately.

## 6. Coarse interleave and the frontend

With a 1 KiB channel interleave, both mappings drop to 163 GB/s. A sequential
stream then has a single row's worth of requests in flight per channel, and
the trace frontend blocks whenever the head request's channel queue is full,
so channels take turns instead of overlapping. This is a limitation of the
single-stream replay model, not a property of HBM3; a GPU's many concurrent
warps would keep every channel busy. Say it out loud when quoting the
interleave rows.

## 7. What "identical" buys and what it does not

- It buys a core we fully understand, 1.5 to 1.8 times faster than Ramulator,
  with attribution and ablation Ramulator lacks, and the right to trust the
  Phase 5 sweeps as "what Ramulator would have said".
- It does not make the timings true: they are Ramulator's HBM3 preset, 13 of
  them estimates, and the controller is Ramulator's model of a controller,
  with a 32-entry queue and a priority-first refresh rule that real GPU
  controllers do not share.

## 8. Reproduce

```bash
source .venv/bin/activate
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies all --refresh none,allbank,perbank
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies ramulator --refresh allbank --cmd-trace --label cmdtrace
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies ramulator --refresh allbank --cmd-trace --disable nCCDL --label inject
python scripts/tokenwall_vs_ramulator.py --trace 70b_layer0 --requests 10000000 --policies ramulator,bank_low --refresh allbank,perbank
```
