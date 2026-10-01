# Resume bullets, each with the command that reproduces it

Every number below is in `RESULTS.md` with its date and command. Nothing is
estimated. Read the qualifiers before quoting; say them out loud when asked.

**Standing qualifiers (apply to every bullet):** HBM3 timings are Ramulator
2.1's `HBM3_6400Mbps` preset, 13 of them marked as Ramulator's estimates, not
JEDEC-published values; the memory controller is Ramulator's HBM34 model
(FR-FCFS, 32-entry queues, open-row policy); requests enter from a trace
replay at one request per channel per tick; "one stack" is 16 channels at
819.2 GB/s peak. Times quoted are on an Apple M5 laptop.

---

### 1. The simulator and its validation

> Built Tokenwall, an HBM3 timing simulator for LLM decode traffic (C++20
> core, Python generator and tooling, 35 C++ and 94 Python tests), and
> validated it against Ramulator 2.1: identical ticks, row hits, misses and
> conflicts on 30 configurations spanning 4 address mappings, 3 refresh modes,
> 3 channel interleaves, 16 and 32 channels, writes, and three workloads;
> 2,081,313 consecutive DRAM commands identical; 1.5 to 1.8x faster.

Reproduce (about 3 minutes; the last line of each case must say
`integer statistics identical: True`):
```bash
source .venv/bin/activate
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies all --refresh none,allbank,perbank
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies ramulator --refresh allbank --cmd-trace --label demo
```
Qualifier: the core deliberately loads Ramulator's resolved rule table and
mirrors its scheduler, so identity proves a faithful implementation, not that
either simulator matches silicon.

### 2. Address mapping is the first-order knob

> Showed that address mapping alone moves a Llama 3 8B decode step from 42% to
> 84% of HBM3 peak (45.2 ms to 22.7 ms per token on one stack, against a 19.0
> ms floor) at a near-constant 96% row-hit rate, and attributed the loss with
> per-slot stall accounting: under the default mapping 33% of column-command
> slots wait on tCCD_L, the same-bank-group spacing rule.

Reproduce the per-layer version (about 1 minute each; full steps take 7 to 13
minutes with `--drain` on `traces/llama3_8b_tp1_b1_s4096.segs`):
```bash
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 4096 --layers 0:1 --out traces/l0
./build/cpp/tokenwall sim --segs traces/l0.segs --policy ramulator --stacks 1 --refresh allbank --drain
./build/cpp/tokenwall sim --segs traces/l0.segs --policy bank_low  --stacks 1 --refresh allbank --drain
```
Expected: `pct_of_peak` about 42.0 and 83.8; the slot breakdown lists
`RD:BankGroup:nCCDL` near 33% in the first run.

### 3. KV-cache layout versus kernel walk order

> Found that a KV-cache memory layout mismatched to the attention kernel's
> traversal order costs 2 to 5x bandwidth under either mapping while the
> row-hit rate stays above 95%, a channel-parallelism loss that hit-rate
> metrics cannot see; a prior static locality analysis had predicted no
> effect.

Reproduce (about 4 minutes, 8 runs):
```bash
python scripts/sweep.py --sweeps kv_layout --workers 4
```
Expected: `bank_low/position_major/head_outer` near 17.9% of peak with about
95% row hits; matched combinations near 83.7%.

### 4. Refresh policy is a controller decision

> Quantified refresh as a controller-policy question: per-bank refresh costs 26
> points of peak under priority-first scheduling and 2 points once other banks
> keep scheduling with the refresh target reserved; caught and fixed a
> refresh-starvation bug in the first version by instrumenting refresh
> postponement (567 µs, then 0.15 µs).

Reproduce (about 1 minute, 8 runs):
```bash
python scripts/sweep.py --sweeps controller --workers 8
```
Expected: `bank_low/perbank/blocking` near 67.7%, `bank_low/perbank/nonblocking`
near 92.2%; each run's JSON reports `max_refresh_wait_ticks` under 1000.
Qualifier: non-blocking scheduling is a Tokenwall extension; Ramulator's rule
is the blocking one.

### 5. Exact decode traces from published model configs

> Wrote a generator that turns a published Llama 3 config into the exact
> address stream of one decode step (485,839,360 32-byte requests for 8B at
> batch 1) using a compact segment format expanded identically in Python and
> C++ and verified by stream hash; one tensor-parallel shard models 70B.

Reproduce (seconds):
```bash
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 4096 --out traces/step
./build/cpp/tw_expand traces/step.segs         # requests=485839360 ... hash=0x8fdf6134cc81fb25
python -m pytest -q tests/test_cross_expander.py
```

### 6. Rule ablation and sensitivity

> Ranked HBM3 timing rules by cost on decode traffic through single-rule
> ablation: tCCD_L 15 points under the default mapping, tCCD_R 2.4, tRP 1.5
> and tFAW 1.3 under bank-group interleaving, write turnarounds 0; and showed
> the fraction of peak is invariant to batch (1 to 32), context (512 to 8192),
> GQA versus MHA, and 8B versus a 70B shard.

Reproduce (about 6 minutes):
```bash
python scripts/sweep.py --sweeps ablation,batch,gqa --workers 6
```

---

## A three-minute live demo

1. `python scripts/ramulator2_probe_demo.py` (5 s): Ramulator's own device
   answers when each command becomes legal; the numbers match our unit tests.
2. `python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 300000 --policies ramulator,bank_low --refresh allbank` (30 s): identical statistics, two mappings, 42% versus 84%.
3. `./build/cpp/tokenwall sim --segs traces/l0.segs --policy bank_low --stacks 1 --refresh perbank --refresh-nonblocking --max-requests 2000000` (10 s): 92% with the refresh breakdown and the postponement line.

## Questions to expect, and where the answer lives

| Question | Where |
|---|---|
| How do you know the simulator is right? | `docs/phase4_validation.md`, three layers: per-rule tests, identical statistics, identical command traces plus a planted bug the diff tool found |
| Why does hit rate not predict bandwidth? | `docs/phase2_address_mapping.md` section 3, `docs/phase5_findings.md` section 3 |
| What does refresh really cost? | `docs/phase5_findings.md` section 4 |
| Where do your timing numbers come from? | `docs/phase0_ramulator2_walkthrough.md` section 5, `configs/hbm3/*.yaml` source tags |
| What would you do with vendor datasheet timings? | `README.md`, "Swapping in real vendor timings" |
| What are the model's limits? | `docs/phase5_findings.md` section 7, `docs/architecture.md` |
