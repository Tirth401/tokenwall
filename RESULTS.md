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
