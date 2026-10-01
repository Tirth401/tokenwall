# RESULTS

Every number here comes from an actual run on this machine. Each entry lists
the command that produced it and the date. Labels:

- **toolchain check**: proves something runs; synthetic input; not a finding.
- **measured**: a Tokenwall result on LLM decode traffic (none yet).
- **derived**: arithmetic on extracted parameters, shown as such.

Nothing here is resume material yet. That starts after Phase 4, when the C++
core is validated against Ramulator 2.1 and run on real model shapes.

---

## Phase 0, 2026-09-30

### Environment

macOS (Darwin 25.5), Apple M5, 10 cores, 16 GB RAM. Apple Clang 21.0.0,
CMake 4.4.3, Python 3.12.10. Ramulator 2.1 at commit
`72427a1bba3771564c4fb0e494ba02242fd1eaa7` (2026-08-30) with
`patches/ramulator2/0001-apple-clang-build-fixes.patch`.

### Ramulator 2.1 builds and passes its own HBM3 tests (toolchain check)

```
cd external/ramulator2 && ../../.venv/bin/python -m pytest tests/device_timings/test_hbm3.py tests/controller_scheduling/HBMController -q
125 passed in 0.11s
```
Raw: `results/phase0/ramulator2_hbm3_pytest.txt`. The README's DDR4 example also
runs (`results/phase0/ramulator2_ddr4_example.txt`).

### HBM3 parameters extracted (toolchain check)

```
python scripts/extract_hbm3_params.py
wrote configs/hbm3/hbm3_16gb_8hi_6400.yaml: 27 timings, 51 constraints
estimate-tagged preset keys: nCL nCWL nFAW nRAS nRCDRD nRCDWR nRP nRRDL nRRDS nRTP nWR nWTRL nWTRS
```

Preset `HBM3_16Gb_8hi` + `HBM3_6400Mbps`: tCK 0.625 ns, 32 B access, 2 pseudo
channels x 2 SIDs x 4 bank groups x 4 banks, 16384 rows of 1 KB per pseudo
channel, 16 GB per 16-channel stack. Peak bandwidth (derived: 32 B / (2 CK x
0.625 ns)): 25.6 GB/s per pseudo channel, 51.2 GB/s per channel, 819.2 GB/s per
stack. Key timings in ns: tRCDRD 19.4, tRP 16.3, tRAS 28.1, tRC 44.4, tCCD_S
1.25, tCCD_L 2.5, tFAW 15.0, tRFC 350, tREFI 3900. Thirteen speed-bin values
are Ramulator estimates, not JEDEC values (see the YAML `source` tags).

### Device model probe (toolchain check)

```
python scripts/ramulator2_probe_demo.py
```
Ramulator's HBM3 device reports, in half-CK ticks of 312.5 ps: ACT to RD 63,
ACT to PREpb 92, PREpb to ACT 50, ACT to ACT same bank 142, RD to RD same bank
group 8, other bank group 4, other SID 6, tFAW window 48 (fifth ACT after four
spaced 8 apart), REFab to ACT 1118. Raw: `results/phase0/probe_demo.txt`.

### Smoke run on synthetic reads (toolchain check)

```
python scripts/ramulator2_hbm3_smoke.py --requests 20000 --refresh allbank
```
One HBM3 channel (2 pseudo channels), HBM34 controller, FRFCFS, open-row
policy, RoBaRaCoCh mapping, all-bank refresh. 20 000 32-byte reads per pattern;
bandwidth counts only requests served before the frontend finished (32 and 43
were still queued).

| Pattern | Served | Ticks | Sim time | Achieved | % of 51.2 GB/s peak | Row hits / misses / conflicts | Avg read latency |
|---|---:|---:|---:|---:|---:|---|---:|
| sequential | 19 968 | 94 259 | 29.46 us | 21.69 GB/s | 42.4% | 19330 / 491 / 147 (96.8% hits) | 61.5 ns |
| random over 1 GiB | 19 957 | 136 215 | 42.57 us | 15.00 GB/s | 29.3% | 0 / 520 / 19466 (0% hits) | 101.9 ns |

Wall time per run: under 0.1 s. Raw: `results/phase0/smoke_summary_allbank.json`,
`results/phase0/smoke_*_allbank.stats.yaml`, `results/phase0/smoke_allbank.log`.

Analytical cross-checks (derived, to be confirmed by Phase 2 and 3 instrumentation):

- Sequential stream confined to one bank per row is capped by tCCD_L: 32 B /
  2.5 ns = 12.8 GB/s per pseudo channel = 50% of channel peak. Measured 42.4%;
  refresh duty (tRFC / tREFI = 9.0%) and about 640 row switches account for
  most of the difference.
- Random stream is capped by tFAW: 4 ACT x 32 B / 15 ns = 8.53 GB/s per pseudo
  channel = 17.07 GB/s per channel = 33.3% of peak. Measured 29.3%, which is
  88% of that cap; 17.07 x (1 - 0.09) = 15.5 GB/s predicted, 15.0 measured.

### Ramulator accounting quirks found (for Phase 4)

- Internal tick stored as 312 ps (625 // 2): Ramulator's own `total_throughput_MBps`
  reads 21727.4 for the sequential run versus 21693 from a 312.5 ps tick.
- Simulation ends when the frontend sends its last request; queued requests
  are never served and do not appear in `num_read_reqs_served`.

---

## Phase 1, 2026-09-30

### Model shapes imported with provenance (toolchain check)

```
python scripts/import_hf_config.py --repo meta-llama/Meta-Llama-3-8B  --mirror NousResearch/Meta-Llama-3-8B  --name llama3_8b
python scripts/import_hf_config.py --repo meta-llama/Meta-Llama-3-70B --mirror NousResearch/Meta-Llama-3-70B --name llama3_70b
```

| Model | Official repo | Served by | Layers | Hidden | Heads / KV heads | Intermediate | Vocab | Params (derived) |
|---|---|---|---:|---:|---|---:|---:|---:|
| llama3_8b | HTTP 401 (gated) | NousResearch mirror, HTTP 200 | 32 | 4096 | 32 / 8 | 14336 | 128256 | 8,030,261,248 |
| llama3_70b | HTTP 401 (gated) | NousResearch mirror, HTTP 200 | 80 | 8192 | 64 / 8 | 28672 | 128256 | 70,553,706,496 |

URLs, SHA-256 of the exact bytes and the raw files are in `configs/models/`. The
derived parameter counts match the published 8.03B and 70.6B sizes.

### Test suite (toolchain check)

```
python -m pytest -q          -> 36 passed in 0.49s   (results/phase1/pytest.txt)
ctest --test-dir build       -> 1/1 (5 C++ cases)
```
The 36 include 13 Python-versus-C++ cross-expander cases: 12 tiny-model
configurations (both KV layouts x both issue orders x three interleave
settings) compared by stream hash and line by line, plus a Llama 3 8B layer
slice compared on a 300 000-request prefix and on total counts.

### Decode-step traffic per shard (generator accounting)

Commands are in `results/phase1/gen_runs.log` and in each `traces/*.meta.json`.
All runs: 4096 past positions, bf16, KV layout head_major, issue order head_outer.

| Run | TP | Batch | Stacks | Footprint | Weights read | KV read | Total per step | Requests (32 B) | Floor at preset peak |
|---|---:|---:|---:|---|---|---|---:|---:|---:|
| llama3_8b | 1 | 1 | 1 | 15.71 GiB (98.2%) | 13.98 GiB (96.5%) | 512 MiB (3.5%) | 14.48 GiB | 485,839,360 | 18.98 ms |
| llama3_8b | 1 | 32 | 2 | 31.21 GiB (97.5%) | 13.98 GiB (46.6%) | 16.00 GiB (53.4%) | 29.98 GiB | 1,006,067,968 | 19.65 ms |
| llama3_8b, `--n-kv-heads 32` (full MHA) | 1 | 1 | 2 | 18.71 GiB (58.5%) | 15.48 GiB (88.6%) | 2.00 GiB (11.4%) | 17.48 GiB | 586,514,944 | 11.46 ms |
| llama3_70b | 8 | 1 | 2 | 16.90 GiB (52.8%) | 16.18 GiB (99.0%) | 160 MiB (1.0%) | 16.34 GiB | 548,309,248 | 10.71 ms |

Writes (KV append) per step: 4,096 / 131,072 / 16,384 / 1,280 requests.
"Floor" is derived: total bytes / (stacks x 819.2 GB/s), the time if every byte
moved at 100% of HBM3 peak per Ramulator 2.1's preset. It is a bound, not a
simulation; Phases 3 to 5 measure how far below it real streams land. For
llama3_8b at batch 1 on one stack the floor is 52.7 tokens/s.

Arithmetic observations, not simulation results:

- At batch 1 the step is 96.5% weight streaming. Batch 32 reads the weights
  once for 32 tokens but KV reads grow 32x to 16 GiB and become the majority.
- Going from 8 KV heads (GQA) to 32 (MHA) multiplies KV-read bytes by 4 and
  adds 1.5 GiB of Wk/Wv weights per step.
- Llama 3 70B at TP=8 leaves one KV head per GPU; at batch 1 the step is 99%
  weight streaming and the 141 GiB model needs two 16 GiB stacks per shard.

### Stream fingerprint (reproducibility)

```
./build/cpp/tw_expand traces/llama3_8b_tp1_b1_s4096.segs
requests=485839360 reads=485835264 writes=4096 bytes=15546859520 hash=0x8fdf6134cc81fb25   (1.2 s wall)
```
Regenerate the trace with the command in its `.meta.json` and rerun; the hash
must match. Raw: `results/phase1/tw_expand_llama3_8b_tp1_b1_s4096.txt`.

### Ramulator 2.1 accepts the exported trace (format check, not a finding)

```
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 1024 --layers 0:1 --out traces/llama3_8b_layer0_tp1_b1_s1024
python -m tokenwall export-ramulator traces/llama3_8b_layer0_tp1_b1_s1024.segs --out traces/llama3_8b_layer0_tp1_b1_s1024_prefix1800k.txt --max-requests 1800000
python scripts/ramulator2_run_trace.py traces/llama3_8b_layer0_tp1_b1_s1024_prefix1800k.txt --label llama3_8b_layer0_prefix1800k --out results/phase1/ramulator_format_check_llama3_8b_layer0_prefix1800k.json
```
The first 1.8 M requests of layer 0 (input norm, Wq, Wk, Wv, all KV reads, the
128 KV-append writes, start of Wo) on ONE HBM3 channel with Ramulator's default
RoBaRaCoCh mapping: 21.52 GB/s = 42.0% of the 51.2 GB/s channel peak, 96.8% row
hits, 62.0 ns average read latency, 3.8 s wall (about 0.47 M requests/s).
One channel and a sequential mapping are not the deployment shape (a GPU
spreads this over 16 to 80 channels), so this only shows the pipeline runs end
to end. It matches the Phase 0 sequential smoke (42.4%) for the reason given
there: consecutive accesses stay in one bank and pay tCCD_L.

---

## Phase 2, 2026-09-30

### Test suite (toolchain check)

```
python -m pytest -q          -> 92 passed in 2.54s   (results/phase2/pytest.txt)
ctest --test-dir build       -> 1/1 (10 C++ cases)
```
New this phase: 36 mapping tests (bijection and ranges for every policy at
16 and 32 channels and three interleaves; bit-exactness against a literal
transcription of Ramulator's C++ mapper), 2 locality tests against
hand-computed answers, 17 Python-versus-C++ mapping cross-checks.

### `ramulator` policy is bit-exact with Ramulator 2.1 (validation)

```
python scripts/ramulator2_mapping_check.py --requests 1800000 --seq 1024        (Part A)
```
The first 1.8 M reads of Llama 3 8B layer 0 (batch 1, 1024 past positions,
read-only) fed to Ramulator twice: flat addresses mapped by Ramulator's
`CacheLineInterleave` + `RoBaRaCoCh`, and the same addresses pre-mapped by our
`ramulator` policy through pass-through mappers.

| Channels | Interleave | Ticks | Served | Row hits | Misses | Conflicts | Result |
|---:|---:|---:|---:|---:|---:|---:|---|
| 16 | 32 B | 534,313 | 1,799,488 | 1,742,063 | 43,809 | 13,632 | identical |
| 16 | 256 B | 589,121 | 1,799,540 | 1,741,888 | 48,135 | 9,525 | identical |
| 32 | 32 B | 267,485 | 1,798,968 | 1,741,407 | 43,904 | 13,665 | identical |

Every statistic equal in all three configurations. Raw:
`results/phase2/mapping_check_llama3_8b_layer0_b1_s1024_reads1800000.json`.

### Mapping policies on a full decode layer (measured, Ramulator 2.1 timing)

```
python scripts/ramulator2_mapping_check.py --part b --requests 20000000 --seq 1024
```
All 13,763,072 reads of Llama 3 8B layer 0 (batch 1, 1024 past positions),
pre-mapped by each policy, one HBM3 stack = 16 channels (peak 819.2 GB/s),
HBM34 controller, FR-FCFS, open-row policy, all-bank refresh, 32-entry queues,
frontend limited to 16 requests per tick. Exports by `tw_expand`.

| Policy | Achieved | % of peak | Row hits | Misses | Conflicts | Hit rate | Avg read latency | Ticks | Sim time | Wall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ramulator` | 344.5 GB/s | 42.1% | 13,324,063 | 333,745 | 104,752 | 96.81% | 61.9 ns | 4,090,635 | 1278 us | 30.5 s |
| `bank_low` | 686.5 GB/s | 83.8% | 13,175,344 | 163,152 | 424,064 | 95.73% | 38.6 ns | 2,052,919 | 642 us | 19.8 s |
| `bank_high` | 246.4 GB/s | 30.1% | 13,320,797 | 12,881 | 428,898 | 96.79% | 76.2 ns | 5,719,661 | 1787 us | 39.1 s |
| `bank_low_xor` | 685.8 GB/s | 83.7% | 13,171,320 | 165,368 | 425,872 | 95.70% | 38.6 ns | 2,054,903 | 642 us | 18.2 s |

Stability check on the first 1.8 M reads (13% of the layer), same setup:
344.9 / 687.7 / 246.7 / 687.5 GB/s, all within 2 GB/s of the full layer.
Raw: `results/phase2/mapping_check_llama3_8b_layer0_b1_s1024_reads13763072.json`
and `..._reads1800000.json`, logs alongside.

What this is and is not: a measurement with **Ramulator 2.1's** timing model
(Tokenwall's own core is Phase 3), on reads only, one layer of one model at
batch 1, HBM3 timings per Ramulator's `HBM3_6400Mbps` preset. Within those
qualifiers the number is stable enough to quote:

> On a full Llama 3 8B decode layer, changing only the address mapping moved
> Ramulator-simulated HBM3 bandwidth from 42% to 84% of peak while the
> row-hit rate stayed near 96%.

Reproduce live: the command above, about two minutes on this laptop.

Derived, not measured: `bank_low` shows 148,719 more row switches than
`ramulator` although its stream has the same ideal hit rate. Its 642 us run
contains 164 all-bank refresh intervals, each of which closes every open row
in the 1024 banks; 148,719 / (164 x 1024) = 0.89. Phase 3's stall breakdown
should attribute this explicitly.

### Static locality, full Llama 3 8B step (486 M requests, 16 channels)

```
python -m tokenwall locality traces/llama3_8b_tp1_b1_s4096.segs --policy <P> --stacks 1 --out results/phase2/locality_llama3_8b_tp1_b1_s4096_<P>.json
```
Ideal open-row hit rate (infinitely patient scheduler, no timing), distinct
banks per 32 consecutive requests, and how back-to-back requests inside a
channel pair up.

| Policy | Ideal hit rate | Row switches | Distinct banks / 32 | Same PC, same bank group | Same PC, other group | Other PC |
|---|---:|---:|---:|---:|---:|---:|
| `ramulator` | 96.87% | 15,188,000 | 16.02 | 96.9% | 0.0% | 3.1% |
| `bank_low` | 96.87% | 15,206,400 | 32.00 | 0.0% | 0.0% | 100.0% |
| `bank_high` | 96.87% | 15,188,000 | 16.02 | 96.9% | 0.0% | 3.1% |
| `bank_low_xor` | 96.87% | 15,206,144 | 32.00 | 0.0% | 0.0% | 100.0% |

Channel balance max/mean = 1.000 for all four. Hit rate does not separate the
policies; the pairing column does, and it predicted the measured ordering.

### Static locality, KV-heavy slice (layer 0, batch 32, 4096 past positions, 32 channels, 30,413,312 requests)

```
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 32 --seq 4096 --stacks 2 --layers 0:1 --kv-layout <L> --out traces/llama3_8b_layer0_tp1_b32_s4096_<L>
python -m tokenwall locality traces/llama3_8b_layer0_tp1_b32_s4096_<L>.segs --policy <P> --stacks 2
```

| KV layout | Policy | Ideal hit rate | KV-read hit rate | Weights hit rate | Row switches | Distinct banks / 32 | Same PC, same bank group |
|---|---|---:|---:|---:|---:|---:|---:|
| head_major | `ramulator` | 96.86% | 96.87% | 96.88% | 954,560 | 32.00 | 96.9% |
| head_major | `bank_low` | 96.85% | 96.85% | 96.88% | 958,960 | 32.00 | 0.0% |
| head_major | `bank_high` | 96.86% | 96.87% | 96.88% | 954,560 | 32.00 | 96.9% |
| head_major | `bank_low_xor` | 96.85% | 96.85% | 96.88% | 958,960 | 32.00 | 0.0% |
| position_major | `ramulator` | 95.14% | 93.74% | 96.88% | 1,478,720 | 19.59 | 95.2% |
| position_major | `bank_low` | 96.85% | 96.85% | 96.88% | 958,848 | 32.00 | 0.0% |
| position_major | `bank_high` | 95.14% | 93.74% | 96.88% | 1,478,720 | 19.59 | 95.2% |
| position_major | `bank_low_xor` | 96.85% | 96.85% | 96.88% | 958,848 | 32.00 | 0.0% |

The position-major KV layout costs 3 points of KV-read hits under Ramulator's
mapping and nothing under `bank_low`. With 32 channels the distinct-banks
metric saturates at the channel count and stops discriminating. Raw:
`results/phase2/locality_llama3_8b_layer0_tp1_b32_s4096_*.json`.

---

## Phase 3, 2026-09-30

### Test suite (toolchain check)

```
ctest --test-dir build        -> 34 C++ cases pass: 17 timing rules with hand-derived ticks, 7 controller timelines, 10 earlier
python -m pytest -q           -> 92 passed (unchanged)
```
Timing-rule expectations (half-CK ticks): tRCD read 63, tRCD write 31, tRAS 92,
tRP 50, tRC 142, tCCD_L 8, tCCD_S 4, tCCD_R 6, tRRD_S 8, tRRD_L 10, tFAW 48,
tWTR_S 38, tWTR_L 44, tRTW 34, tPPD 4, row bus 3, column bus 2, tRFC 1118.
Those that Phase 0 probed in Ramulator's device (63, 92, 50, 142, 8, 4, 6, 48,
1118) agree with it.

### Rule table exported from Ramulator (toolchain check)

```
python scripts/export_dram_spec.py
wrote configs/hbm3/hbm3_16gb_8hi_6400.spec: 60 constraints, 29 timings, read_latency 44 ticks
```
The exporter re-derives Ramulator's constraint expansion with a name per
entry and verifies every entry against Ramulator's own `to_config()` output
before writing.

### Tokenwall versus Ramulator 2.1 (validation)

```
python scripts/tokenwall_vs_ramulator.py --requests 1800000
python scripts/tokenwall_vs_ramulator.py --requests 20000000      # capped to the layer: 13,763,072 reads
```
Same traffic (reads of Llama 3 8B layer 0, batch 1, 1024 past positions),
same mapping, 16 channels, frontend limited to 16 requests per tick, both
simulators stopping when the last request is sent.

| Reads | Mapping, refresh | Ticks | Served | Hits | Misses | Conflicts | GB/s | Avg read latency (ticks) | Wall R2 / TW |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1.8 M | `ramulator`, none | 567,661 | 1,799,488 | 1,743,200 | 1,024 | 55,280 | 324.608 | 207.426 | 4.5 s / 3.0 s |
| 1.8 M | `ramulator`, all-bank | 534,313 | 1,799,488 | 1,742,063 | 43,809 | 13,632 | 344.868 | 197.960 | 4.3 s / 2.7 s |
| 1.8 M | `bank_low`, none | 239,251 | 1,799,488 | 1,742,800 | 1,024 | 55,664 | 770.185 | 114.345 | 2.3 s / 1.3 s |
| 1.8 M | `bank_low`, all-bank | 267,947 | 1,799,488 | 1,722,704 | 21,632 | 55,152 | 687.702 | 122.877 | 2.4 s / 1.4 s |
| 13.76 M | `ramulator`, none | 4,352,953 | 13,762,560 | 13,332,416 | 1,024 | 429,120 | 323.754 | 207.915 | 37.1 s / 24.6 s |
| 13.76 M | `ramulator`, all-bank | 4,090,635 | 13,762,560 | 13,324,063 | 333,745 | 104,752 | 344.515 | 198.185 | 35.9 s / 22.0 s |
| 13.76 M | `bank_low`, none | 1,828,879 | 13,762,560 | 13,331,856 | 1,024 | 429,680 | 770.574 | 114.357 | 19.1 s / 10.5 s |
| 13.76 M | `bank_low`, all-bank | 2,052,919 | 13,762,560 | 13,175,344 | 163,152 | 424,064 | 686.479 | 123.429 | 21.0 s / 11.5 s |

Every integer statistic is identical between the two simulators in all eight
cases. Bandwidth and average latency derive from those integers; after fixing
Tokenwall's JSON writer (it first printed six significant digits) they agree
to about 1e-7 relative on the 1.8 M cases, the residue being Ramulator's
32-bit float latency accumulator. Tokenwall ran 1.5 to 1.8x faster. Raw:
`results/phase3/validation_*.json`, `validation_*.log`.

Reading this honestly: the core loads Ramulator's resolved rule table and
mirrors its node tree, FR-FCFS scheduler and HBM3 command-bus rules on
purpose, so identity means the implementation is faithful, not that HBM3
silicon behaves this way. Timings are still Ramulator's preset, 13 of them
estimates. The Phase 4 error to record is therefore 0 on these cases;
Phase 4 widens the matrix (writes, interleaves, 32 channels, per-bank refresh).

### Where the bandwidth goes (measured by Tokenwall's slot attribution)

Each (pseudo channel, rising edge) is one column-command slot. `data` means a
burst occupies that pseudo channel's wires; other labels name the command and
rule blocking the oldest schedulable request. Full layer 0, 16 channels,
reads only.

| Mapping, refresh | data | RD: tCCD_L | RD: tRCD | RD: column bus | RD: tCCD_R | ACT: tRP | ACT: tRFC | refresh bookkeeping |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `ramulator`, none | 39.52% | 34.85% | 19.71% | 3.75% | | 2.16% | | |
| `ramulator`, all-bank | 42.06% | 32.59% | 6.39% | 8.67% | | 0.55% | 8.68% | 0.93% |
| `bank_low`, none | 94.06% | | 3.17% | 1.38% | 1.38% | | | |
| `bank_low`, all-bank | 83.80% | | 4.00% | 1.24% | 1.22% | | 8.72% | 0.92% |

Rows sum to about 100% (entries under 0.1% omitted). Observations:

- Under the default mapping the single largest loss is tCCD_L, the
  same-bank-group spacing rule, which Phase 0 predicted from the rule table.
- All-bank refresh costs 8.7 points of slots in both mappings, consistent
  with tRFC / tREFI = 350 / 3900 = 9.0%.
- Under the `ramulator` mapping refresh makes the run 6.0% shorter (4,352,953
  to 4,090,635 ticks): its PREab closes all rows at once, so later visits
  are misses (one ACT) rather than conflicts (PREpb, tRP, ACT); conflicts
  fall from 429,120 to 104,752 and the tRCD + tRP share from 21.9% to 6.9%.
  Analysis built on the attribution; Phase 5's refresh sweep can test it.
- Attribution only counts requests the scheduler could pick in the current
  read/write mode. An earlier version counted parked writes as "arbitration"
  (19.7% on this layer); the fix leaves the simulation untouched (identical
  ticks) and the breakdown identical with or without writes.

### First full decode step through Tokenwall (measured, Tokenwall only)

```
./build/cpp/tokenwall sim --segs traces/llama3_8b_tp1_b1_s4096.segs --policy <P> --stacks 1 --refresh allbank --drain --json results/phase3/fullstep_llama3_8b_tp1_b1_s4096_<P>.json
```
All 485,839,360 requests of one Llama 3 8B decode step (batch 1, 4096 past
positions, bf16, 4,096 KV-append writes included), one HBM3 stack = 16
channels, all-bank refresh, run until every request is served.

| Mapping | Step time | Achieved | % of 819.2 GB/s | Row hit rate | Avg read latency | Tokens/s on one stack | Wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ramulator` | 45.15 ms | 344.3 GB/s | 42.0% | 96.81% | 62.0 ns | 22.1 | 772 s |
| `bank_low` | 22.66 ms | 686.1 GB/s | 83.7% | 95.73% | 38.6 ns | 44.1 | 411 s |

The floor from Phase 1 was 18.98 ms (52.7 tokens/s) at 100% of the preset
peak. Commands issued, `ramulator` / `bank_low`: ACT 15,484,160 / 20,771,193,
PREpb 3,704,584 / 15,018,465, PREab and REFab 370,496 / 185,920 each, RD
485,835,264 both, WR 4,096 both. Per class, `bank_low`: weights 469,041,152
served at 95.73% hits, KV reads 16,777,216 at 95.68%, KV writes 4,096,
embedding and norms 16,896. Raw: `results/phase3/fullstep_*.json`, logs
alongside.

Slot attribution for the full step (writes included, corrected accounting):

| Mapping | data | RD: tCCD_L | ACT: tRFC | RD: column bus | RD: tRCD | RD: tCCD_R | ACT: tRP | refresh bookkeeping |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `ramulator` | 42.03% | 32.57% | 8.70% | 8.67% | 6.39% | | 0.56% | 1.03% |
| `bank_low` | 83.75% | | 8.74% | 1.24% | 4.02% | 1.22% | | 0.92% |

Qualifiers for quoting: Tokenwall's core, validated identical to Ramulator on
reads only; HBM3 per Ramulator 2.1's preset; one model, batch 1; frontend
issues at most one request per channel per tick. With those stated:

> On one full Llama 3 8B decode step (486 M requests), Tokenwall measures
> 42.0% of HBM3 peak under Ramulator's default address mapping and 83.7%
> under a bank-group-interleaved mapping, 45.2 ms versus 22.7 ms per token on
> a single stack, against a 19.0 ms floor.

---

## Phase 4, 2026-09-30

### Test suites (toolchain check)

```
ctest --test-dir build   -> 35 C++ cases (adds the per-bank refresh timeline: REFpb at 391/393, next bank at 781/783,
                            ACT to the refreshed bank at 1029, ACT elsewhere in the pseudo channel at 415)
python -m pytest -q      -> 94 passed (adds two end-to-end CLI tests, one with 64 B requests split into two accesses)
```

### Ramulator patch 0002: pre-mapped traces carry the flat address (toolchain check)

Ramulator's `ReadWriteTrace` left `req.addr` unset, so its write coalescing and
read forwarding matched every request against every other. The patch accepts
an optional third token. Four-line traces, one channel, all four requests in
one tick group:

| Trace | Flat address present | Forwarded reads | Coalesced writes | Correct? |
|---|---|---:|---:|---|
| W 4096, R 4096, W 4096 | yes | 1 | 1 | yes |
| W 4096, R 12288, W 16384 | yes | 0 | 0 | yes |
| same two traces | no (old format) | 1 | 1 | wrong for the second trace |

### Validation matrix: Tokenwall versus Ramulator 2.1, writes included

```
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies all --refresh none,allbank,perbank
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies ramulator,bank_low --refresh allbank --interleave 3,5 --label il
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies ramulator,bank_low --refresh allbank --channels 32 --label ch32
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 20000000 --policies ramulator,bank_low --refresh allbank,perbank
python scripts/tokenwall_vs_ramulator.py --trace layer0_b32 --requests 40000000 --policies ramulator,bank_low --refresh allbank
python scripts/tokenwall_vs_ramulator.py --trace 70b_layer0 --requests 10000000 --policies ramulator,bank_low --refresh allbank,perbank
```
"Identical" means ticks, requests served, row hits, misses and conflicts are
equal; bandwidth and latency then agree to printing precision. Traffic is one
layer with its KV-append writes; frontend limited to one request per channel
per tick; both simulators stop at the last send.

| Trace | Requests | Ch | Mapping | Interleave | Refresh | Ticks (both) | GB/s (both) | % of peak | Identical |
|---|---:|---:|---|---:|---|---:|---:|---:|---|
| 8B layer 0, b1 | 2,000,000 | 16 | `ramulator` | 32 B | none | 630,897 | 324.5 | 39.6 | yes |
| | | | `ramulator` | 32 B | all-bank | 594,037 | 344.6 | 42.1 | yes |
| | | | `ramulator` | 32 B | per-bank | 862,415 | 237.4 | 29.0 | yes |
| | | | `bank_low` | 32 B | none | 265,837 | 770.1 | 94.0 | yes |
| | | | `bank_low` | 32 B | all-bank | 297,309 | 688.6 | 84.1 | yes |
| | | | `bank_low` | 32 B | per-bank | 369,227 | 554.4 | 67.7 | yes |
| | | | `bank_high` | 32 B | none | 746,495 | 274.3 | 33.5 | yes |
| | | | `bank_high` | 32 B | all-bank | 830,363 | 246.6 | 30.1 | yes |
| | | | `bank_high` | 32 B | per-bank | 799,805 | 256.0 | 31.3 | yes |
| | | | `bank_low_xor` | 32 B | none | 265,909 | 769.9 | 94.0 | yes |
| | | | `bank_low_xor` | 32 B | all-bank | 297,427 | 688.3 | 84.0 | yes |
| | | | `bank_low_xor` | 32 B | per-bank | 368,835 | 555.0 | 67.7 | yes |
| | | | `ramulator` | 256 B | all-bank | 654,145 | 313.0 | 38.2 | yes |
| | | | `ramulator` | 1 KiB | all-bank | 1,252,899 | 163.4 | 19.9 | yes |
| | | | `bank_low` | 256 B | all-bank | 379,845 | 539.0 | 65.8 | yes |
| | | | `bank_low` | 1 KiB | all-bank | 1,252,807 | 163.5 | 20.0 | yes |
| | | 32 | `ramulator` | 32 B | all-bank | 296,505 | 690.3 | 42.1 | yes |
| | | 32 | `bank_low` | 32 B | all-bank | 148,117 | 1,381.9 | 84.3 | yes (after harness fix) |
| 8B layer 0, b1 | 13,763,200 | 16 | `ramulator` | 32 B | all-bank | 4,090,635 | 344.5 | 42.1 | yes |
| | | | `ramulator` | 32 B | per-bank | 5,940,451 | 237.2 | 29.0 | yes |
| | | | `bank_low` | 32 B | all-bank | 2,052,919 | 686.5 | 83.8 | yes |
| | | | `bank_low` | 32 B | per-bank | 2,541,151 | 554.6 | 67.7 | yes |
| 8B layer 0, b32 (55% KV) | 30,413,312 | 32 | `ramulator` | 32 B | all-bank | 4,532,631 | 687.1 | 41.9 | yes |
| | | | `bank_low` | 32 B | all-bank | 2,270,331 | 1,371.7 | 83.7 | yes |
| 70B TP=8 layer 0, b1 | 6,751,248 | 32 | `ramulator` | 32 B | all-bank | 1,003,361 | 688.9 | 42.0 | yes |
| | | | `ramulator` | 32 B | per-bank | 1,457,011 | 474.4 | 29.0 | yes |
| | | | `bank_low` | 32 B | all-bank | 503,439 | 1,373.0 | 83.8 | yes |
| | | | `bank_low` | 32 B | per-bank | 623,061 | 1,109.4 | 67.7 | yes |

Peak is 51.2 GB/s per channel: 819.2 GB/s at 16 channels, 1,638.4 at 32.
Raw: `results/phase4/validation_*.json`, `matrix_*.log`, `matrix_reruns.log`.
Final validation error on every statistic, every case: **0**.

### Command-level identity and the diff tool

```
python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 --policies ramulator --refresh allbank --cmd-trace --label cmdtrace
  command traces identical: 2,081,313 commands over 16 channels
```
Every DRAM command, with its tick and address vector, is the same in both
simulators, which is stronger than agreeing on totals.

To prove the diff tool reports something when there is something to report,
Tokenwall ran once with tCCD_L removed (`--disable nCCDL`):

```
first divergence: channel 0, command index 2 (of 130083 / 130803)
  ramulator  tick        73 RD     pc 0 sid 0 bg 0 bank 0 row   1002 col   1 type 0
  tokenwall  tick        69 RD     pc 0 sid 0 bg 0 bank 0 row   1002 col   1 type 0
  bank pc0/sid0/bg0/bank0 in ramulator: open_row=1002 last={ACT:1, RD:65}
  bank pc0/sid0/bg0/bank0 in tokenwall: open_row=1002 last={ACT:1, RD:65}
```
The second read to the bank came 4 ticks after the first instead of 8, which
is tCCD_S instead of tCCD_L. Totals: 594,037 versus 438,535 ticks. Removing
tPPD instead produced identical traces: tPPD never binds on this traffic.

### Fidelity log

1. **No simulator discrepancy found** in 30 matrix cases, including writes,
   per-bank refresh, three interleaves, 32 channels, a KV-heavy batch-32
   slice and a 70B tensor-parallel slice.
2. **Harness bug, found by the matrix and fixed.** The 32-channel `bank_low`
   case first reported Ramulator at 297,309 ticks, exactly its 16-channel
   result: the pre-mapped export still used a 16-channel geometry, so
   Ramulator's 32-channel system received traffic for 16. `tw_expand` now
   takes `--channels` and the script passes it; rerun identical.
3. **Reporting bug, Phase 3, already logged**: attribution counted parked
   writes as arbitration.
4. **Null injection**: disabling tPPD changed nothing, so the first
   demonstration of the diff tool had to use tCCD_L.

### Findings from the matrix (Tokenwall attribution, same traffic)

- **Per-bank refresh is worse than all-bank under this controller**: 29.0%
  versus 42.1% of peak with `ramulator`, 67.7% versus 84% with `bank_low`,
  on every trace. Attribution: 25% of all slots are
  `refresh:REFpb:Bank:nRP`, the whole channel's scheduling paused while the
  refresh's precharge waits tRP before the REFpb may issue, plus 4% to 14%
  `ACT:Bank:nRFCpb`. The controller mirrors Ramulator's rule that no read or
  write is scheduled while a priority request waits; a controller that kept
  serving other banks during that 16 ns would not pay most of this.
- **Coarser channel interleave collapses throughput** for this single-stream
  frontend: 1 KiB interleave gives 163 GB/s under both mappings, because a
  sequential stream then has at most one 1 KiB row in flight per channel and
  the frontend blocks on a full queue. A GPU with many concurrent streams
  would not see this; it is a property of the trace-replay frontend.
- **The percentages do not depend on the model or batch**: 42% and 84% on
  the 8B layer, the batch-32 KV-heavy slice and the 70B shard alike. Weight
  sweeps and KV reads are both long sequential runs under these mappings.

---

## Phase 5, 2026-10-01

All Phase 5 numbers are Tokenwall measurements (core validated identical to
Ramulator 2.1 in Phases 3 and 4). HBM3 timings are Ramulator 2.1's
`HBM3_6400Mbps` preset. Unless stated, each run is one decode layer, every
request served (drained), KV-append writes included, frontend limited to one
request per channel per tick. Peak is 51.2 GB/s per channel.

```
python scripts/sweep.py --sweeps all --workers 6        # 84 runs, 578 s wall
python scripts/sweep.py --sweeps controller --workers 8 # rerun after the bank-reservation fix
python scripts/plot_sweeps.py                           # results/phase5/plots/*.png
```
Raw: `results/phase5/sweep_*.json`, `sweeps.csv`, `runs/*.json`, `sweep_run.log`.

### Mapping x refresh (Llama 3 8B layer, batch 1, 4096 past positions, 16 channels)

| Mapping | No refresh | All-bank refresh | Per-bank refresh (Ramulator's blocking controller) |
|---|---:|---:|---:|
| `ramulator` | 39.5% | 42.0% | 28.9% |
| `bank_low` | 94.0% | 83.8% | 67.7% |
| `bank_high` | 33.5% | 30.1% | 31.3% |
| `bank_low_xor` | 94.0% | 83.7% | 67.8% |

### Batch, sequence length, GQA and model shape (percent of peak)

| Sweep | Setting | KV share of bytes | `ramulator` | `bank_low` |
|---|---|---:|---:|---:|
| batch (2 stacks, 4096 past) | 1 / 4 / 16 / 32 | 3.5 / 13 / 37 / 55% | 42.0 / 42.0 / 41.9 / 41.9 | 83.8 / 83.8 / 83.7 / 83.7 |
| past positions (batch 32, 4 stacks) | 512 / 1024 / 2048 / 4096 / 8192 | 13 / 23 / 38 / 55 / 71% | 41.9 / 41.9 / 41.9 / 41.9 / 42.0 | 83.6 / 83.7 / 83.8 / 83.7 / 83.7 |
| KV heads (8B, batch 1, 2 stacks) | 8 (GQA) / 32 (MHA) | bytes per layer 0.42 / 0.52 GiB | 42.0 / 42.0 | 83.8 / 83.7 |
| Llama 3 70B TP=8 shard | batch 1, 8; stacks 2, 4 | about 1% | 42.0 to 42.1 | 83.7 to 83.8 |

### KV layout x kernel walk order (8B layer, batch 32, 55% KV reads, 2 stacks, all-bank refresh)

| Layout | Walk order | Match | `ramulator` % peak | `bank_low` % peak | KV-read row hits, `ramulator` / `bank_low` |
|---|---|---|---:|---:|---|
| head-major | by head | yes | 41.9 | 83.7 | 96.8 / 95.7% |
| head-major | by position | no | 21.9 | 34.2 | 84.4 / 49.3% |
| position-major | by head | no | 16.5 | 17.9 | 93.6 / 95.1% |
| position-major | by position | yes | 42.0 | 83.8 | 96.8 / 95.7% |

### Ablation: percentage points of peak recovered when one rule is removed (8B layer, batch 1, 16 channels, all-bank refresh)

| Rule removed | `ramulator` (base 42.0) | `bank_low` (base 83.8) |
|---|---:|---:|
| tCCD_L | +15.0 | +0.2 |
| tRCD (read) | +3.8 | +0.4 |
| tRP | +2.6 | +1.5 |
| tCCD_R | 0.0 | +2.4 |
| tFAW | 0.0 | +1.3 |
| tRRD_S | 0.0 | +0.1 |
| row-bus occupancy of ACT | 0.0 | +0.1 |
| tRRD_L, tWTR_S/L, tRTW, tPPD, tRAS | 0.0 | 0.0 |

Removing a rule is unphysical; the number is the rule's share of the loss on
this traffic. Gains overlap, so they do not sum to the gap between mappings.

### Controller experiment: scheduling during refresh (8B layer, batch 1, 16 channels)

| Refresh | Scheduling while a refresh waits | `ramulator` | `bank_low` | Refreshes served (`bank_low`) | Longest refresh postponement |
|---|---|---:|---:|---:|---:|
| all-bank | blocking (Ramulator's rule) | 42.0% | 83.8% | 5,408 | 0.06 us |
| all-bank | non-blocking, PC reserved from new row opens | 42.0% | 84.7% | 5,344 | 1.28 us |
| per-bank | blocking (Ramulator's rule) | 28.9% | 67.7% | 214,464 | 0.06 us |
| per-bank | non-blocking, bank reserved from new row opens | 42.7% | 92.2% | 157,536 | 0.15 us |

Refresh counts scale with run length (per-bank non-blocking runs 0.73x as long
and serves 0.73x the refreshes), so no refresh is skipped; tREFI is 3.9 us.

Logged first attempt, superseded: without the reservation, "non-blocking"
reached 92.4% (`bank_low`, per-bank) and 85.7% (all-bank), but the longest
refresh postponement was 567 us and 584 us, more than a hundred tREFI, with
only 34,560 of the expected refreshes served inside the run. The cause is a
two-tick race: after the refresh's PREpb, a queued read's ACT is legal at
tRP minus the ACT length (50 ticks) while the REFpb is legal at 52, so under
load a read reopens the bank first, every time. Forbidding new row opens on a
bank with a pending refresh removes the race. Numbers in
`results/phase5/sweep_run.log` (first run) versus `sweep_controller.json` (fixed).

### Full decode step anchors (8B, batch 1, 4096 past positions, 16 channels, 485,839,360 requests, drained)

```
./build/cpp/tokenwall sim --segs traces/llama3_8b_tp1_b1_s4096.segs --policy <P> --stacks 1 --refresh <R> [--refresh-nonblocking] --drain --json ...
```

| Mapping | Refresh | Scheduling | Step time | Tokens/s on one stack | % of peak | Wall |
|---|---|---|---:|---:|---:|---:|
| `ramulator` | all-bank | blocking | 45.15 ms | 22.1 | 42.0 | 742 s |
| `ramulator` | per-bank | blocking | 65.58 ms | 15.2 | 28.9 | 2,136 s |
| `bank_low` | none | | 20.18 ms | 49.6 | 94.0 | 623 s |
| `bank_low` | all-bank | blocking | 22.66 ms | 44.1 | 83.7 | 395 s |
| `bank_low` | per-bank | blocking | 28.04 ms | 35.7 | 67.7 | 687 s |
| `bank_low` | per-bank | non-blocking, bank reserved | 20.59 ms | 48.6 | 92.2 | 674 s |

The floor from Phase 1 is 18.98 ms (52.7 tokens/s). The per-bank non-blocking
step served 5,405,984 refresh commands with a longest postponement of 2,222
ticks (0.69 us, tREFI is 3.9 us) and an average of 97 ticks. Raw:
`results/phase3/fullstep_*.json`, `results/phase5/fullstep/*.json`.

### Findings (see docs/phase5_findings.md)

- Mapping is the first-order knob: 42% versus 84% of peak in every refresh
  mode; XOR hashing adds nothing; the bank-high anti-pattern gets 30%.
- Batch, context length, GQA versus MHA and 8B versus 70B leave the fraction
  of peak unchanged to the first decimal; they change bytes per step.
- A KV layout that does not match the kernel's walk order costs 2 to 5x under
  either mapping while row hits stay above 95% (channel parallelism loss);
  this contradicts Phase 2's static analysis, which lacked a channel-spread
  metric.
- Refresh cost is a controller decision: all-bank 10 points, per-bank 26
  points under Ramulator's blocking rule, 2 points with non-blocking
  scheduling and bank reservation; the unreserved attempt starved refresh and
  was caught by the postponement statistic.
- Ablation: tCCD_L is 15 points under the default mapping; under bank_low the
  leftovers are tCCD_R 2.4, tRP 1.5, tFAW 1.3; turnaround rules cost nothing.
