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
