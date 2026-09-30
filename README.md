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

1. **Trace generator (Python, done).** Turns a model shape (layers, hidden
   size, heads, KV heads, dtype, batch, past positions) into the ordered stream
   of 32-byte memory requests one decode step issues on one GPU. No GPU needed:
   the pattern is deterministic from the model's shape. KV-cache layout and
   issue order are knobs because they change row locality.
2. **Address mapping (done) + timing core (Phase 3), Python + C++.** A swappable
   bit-slice mapping from linear address to (channel, pseudo channel, SID, bank
   group, bank, row, column) with four policies, then a per-bank state machine
   enforcing JEDEC HBM3 timing (tRCD, tRP, tRAS, tRC, tCCD_S/L, tRRD_S/L, tFAW,
   read/write turnaround, tREFI/tRFC). Reports cycles, achieved bandwidth,
   row-buffer hit rate, and a stall breakdown.
3. **Sweep (Python, Phase 5).** Varies mapping, KV layout, refresh policy, batch
   size and sequence length; plots what actually matters.

[Ramulator 2.1](https://github.com/CMU-SAFARI/ramulator2) is the reference:
it supplies the HBM3 timing parameters (`configs/hbm3/`) and, in Phase 4, the
ground truth the C++ core is validated against.

## Status

| Phase | Scope | State |
|------:|-------|-------|
| 0 | Repo, build system, Ramulator 2.1 built and run, HBM3 parameters extracted | done 2026-09-30 |
| 1 | Decode-step trace generator, Llama 3 8B/70B configs, Python/C++ cross-checked trace format | done 2026-09-30 |
| 2 | Four address-mapping policies, bit-exact with Ramulator's default, Python/C++ mirrored, measured on real traffic | done 2026-09-30 |
| 3 | C++ timing core, one constraint at a time | next |
| 4 | Validation against Ramulator 2.1 | planned |
| 5 | Sweeps and findings | planned |
| 6 | Writeup | planned |

`PROGRESS.md` is the session log with open design questions. `RESULTS.md` has
every measured number, the command that produced it, and the date. Nothing in
`RESULTS.md` is estimated or typed by hand.

## Provenance rules (read before quoting any number)

- **HBM3 timings are "per Ramulator 2.1's `HBM3_6400Mbps` preset", never "per
  JEDEC".** Thirteen core timings in that preset (tCL, tCWL, tFAW, tRAS,
  tRCDRD, tRCDWR, tRP, tRRD_L, tRRD_S, tRTP, tWR, tWTR_L, tWTR_S) sit inside a
  block Ramulator's source labels `Ramulator Guesstimate`; JEDEC JESD238 leaves
  them to vendor datasheets. Every timing in `configs/hbm3/*.yaml` carries a
  `source` tag saying whether it is a speed-bin value, an estimate, a derived
  formula, or a user override. Plot captions and RESULTS entries repeat this.
- **Model shapes come from published `config.json` files**, fetched by
  `scripts/import_hf_config.py`, which records the URL that answered, the
  SHA-256 of the bytes, and keeps the raw file under `configs/models/raw/`.
  The official Meta repos are gated (HTTP 401); the NousResearch mirrors
  served identical files, and the record says so.
- **Phase 0 and Phase 1 bandwidth figures are toolchain checks** on synthetic
  traffic or a single channel. Project findings start with Phase 4.

### Swapping in real vendor timings

A memory architect's first question is "what if tRCD is really X?". The
override path is one flag per timing, in clock cycles, and the output file is
forced to carry a distinct name so it cannot be mistaken for the preset:

```bash
python scripts/extract_hbm3_params.py --override nRCDRD=28 --override nRP=24 --name vendor_x
# -> configs/hbm3/hbm3_16gb_8hi_6400_vendor_x.yaml, overridden keys tagged "override: user-supplied"
```

Ramulator applies overrides after it computes derived timings, so nRC, nRTW,
nREFIpb and friends are not recomputed from an overridden input; override them
too when a datasheet changes their inputs (the YAML's `override_note` repeats
this). The same keyword overrides work directly in Ramulator:
`ramulator.dram.HBM3(org_preset=..., timing_preset=..., nRCDRD=28)`.

## Models and why one shard is the honest unit

Two shapes, `configs/models/llama3_8b.yaml` and `llama3_70b.yaml`. There is no
third model: `--n-kv-heads 32` on the 8B config gives full multi-head attention
so the GQA effect on KV traffic shows up in isolation, without confounding it
against a different layer count and hidden size.

A single HBM3 stack holds 16 GiB, and Llama 3 70B in bf16 is 131 GiB. Real 70B
serving shards the model across 8 GPUs with tensor parallelism, so a single
GPU's HBM genuinely holds about an eighth of the weights plus its share of the
KV cache. That is the actual deployment shape, not a workaround: Tokenwall
simulates **one tensor-parallel shard** (`--tp 8` for 70B, `--tp 1` for 8B) on
a GPU with `--stacks` HBM3 stacks (an H100 has five). The generator refuses a
run whose footprint does not fit and names the stack count that would.

## Trace format: a recipe, not a list

One decode step of Llama 3 8B is 486 million 32-byte requests; a plain list is
tens of gigabytes. Tokenwall stores a *segment list* instead: "start at base,
read run_bytes, repeat runs times with this stride", grouped so that a group
can either play its segments in order or round-robin chunks between them
(many streams hitting memory at once). Both `python/tokenwall/segments.py` and
`cpp/include/tokenwall/segments.h` expand the same file to the same request
sequence, and `tests/test_cross_expander.py` hashes both streams and compares
them line by line, so the two implementations cannot drift silently. A full
8B step expands in 1.2 s in C++ and has a stable fingerprint
(`RESULTS.md`, "Stream fingerprint").

## Address mapping: which bits pick the bank

An address is a big binary number, and a mapping policy says which of its bits
pick the channel, which pick the bank, and which pick the row. Put the bank bits
low and consecutive accesses spread across banks like cards dealt around a
table; put them high and they pile onto one bank. Four policies live in
`python/tokenwall/addrmap.py`, mirrored in `cpp/include/tokenwall/addrmap.h`:

| Policy | Bit order (low to high, above the 32 B line) | What a streaming sweep does |
|---|---|---|
| `ramulator` | channel, column, pseudo channel, SID, bank group, bank, row | one 1 KiB row of one bank at a time; back-to-back reads share a bank group and pay tCCD_L |
| `bank_low` | channel, pseudo channel, bank group, bank, SID, column, row | alternates bank groups every access (tCCD_S pace), keeps every bank open |
| `bank_high` | channel, column, pseudo channel, row, SID, bank group, bank | walks all rows of one bank before touching another; the anti-pattern |
| `bank_low_xor` | `bank_low` with low row bits XORed into the bank bits | breaks power-of-two strides that would alias onto one bank |

`ramulator` reproduces Ramulator 2.1's `CacheLineInterleave` + `RoBaRaCoCh`
bit for bit: `tests/test_addrmap.py` checks it against a transcription of the
C++, and `scripts/ramulator2_mapping_check.py` feeds Ramulator the same traffic
flat and pre-mapped and gets identical statistics. Every policy takes a channel
interleave granularity (32 B to 1 KiB). `python -m tokenwall map --policy X`
prints the bit layout and how often each field changes; `python -m tokenwall
locality` reports ideal row-hit rate and bank spread for a trace under a
policy before any timing is simulated. Measured bandwidth per policy is in
`RESULTS.md` (Phase 2).

## Layout

```
cpp/                 C++: trace format + expander, address mapping mirror (tw_expand), timing core (Phase 3), unit tests
python/tokenwall/    trace generator, address mapping (addrmap.py), locality analysis, CLI (python -m tokenwall)
configs/hbm3/        HBM3 parameters extracted from Ramulator 2.1, with provenance
configs/models/      Llama 3 shapes imported from published config.json, raw files under raw/
tests/               pytest suite incl. the Python-vs-C++ cross-expander check
scripts/             setup_ramulator2.sh, extract_hbm3_params.py, import_hf_config.py, Ramulator runners
patches/ramulator2/  two-hunk build fix for Apple Clang
docs/                phase walkthroughs written for study
results/             raw outputs behind every number in RESULTS.md
traces/              generated traces (.segs gitignored, .meta.json committed)
external/ramulator2  Ramulator 2.1 git submodule (pinned commit)
```

## Setup (macOS or Linux laptop, no paid tools)

Requirements: git, CMake 3.16+, a C++20 compiler, Python 3.10+.

```bash
git clone --recursive <this repo> tokenwall && cd tokenwall
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .
scripts/setup_ramulator2.sh        # builds Ramulator 2.1 into external/, pip-installs it into .venv
cmake -S . -B build && cmake --build build && ctest --test-dir build   # Tokenwall C++
.venv/bin/python -m pytest -q      # 92 tests incl. Python-vs-C++ expander and mapper cross-checks
```

`scripts/setup_ramulator2.sh` applies `patches/ramulator2/0001-apple-clang-build-fixes.patch`:
a missing `template` keyword in `param.h` that Apple Clang 21 rejects, and a
bump of the fetched `fmt` library from 10.2.1 to 11.2.0. Nothing in the patch
touches simulation behaviour.

## Reproduce

Phase 0 (HBM3 parameters and toolchain checks):

```bash
source .venv/bin/activate
python scripts/extract_hbm3_params.py        # -> configs/hbm3/hbm3_16gb_8hi_6400.yaml
python scripts/ramulator2_hbm3_smoke.py      # synthetic sequential/random reads on one channel
python scripts/ramulator2_probe_demo.py      # asks the device model when commands become legal
```

Phase 1 (decode-step traces):

```bash
python scripts/import_hf_config.py --repo meta-llama/Meta-Llama-3-8B --mirror NousResearch/Meta-Llama-3-8B --name llama3_8b
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 4096 --out traces/llama3_8b_tp1_b1_s4096
./build/cpp/tw_expand traces/llama3_8b_tp1_b1_s4096.segs             # count + fingerprint hash
python -m tokenwall gen --model configs/models/llama3_70b.yaml --tp 8 --stacks 2 --batch 1 --seq 4096 --out traces/llama3_70b_tp8_b1_s4096
python -m tokenwall gen --model configs/models/llama3_8b.yaml --n-kv-heads 32 --stacks 2 --batch 1 --seq 4096 --out traces/llama3_8b_mha32_tp1_b1_s4096
python -m tokenwall export-ramulator traces/llama3_8b_tp1_b1_s4096.segs --out traces/slice.txt --max-requests 1000000
```

Phase 2 (mapping policies):

```bash
python -m tokenwall map --policy bank_low --stacks 1                  # bit layout and field periods
python -m tokenwall locality traces/llama3_8b_tp1_b1_s4096.segs --policy bank_low --stacks 1
python scripts/ramulator2_mapping_check.py                            # bit-exactness + 4 policies under Ramulator timing
./build/cpp/tw_expand traces/x.segs --map bank_low --stacks 1 --ramulator-out x.txt   # pre-mapped export from C++
```

`python -m tokenwall gen --help` lists every knob: `--kv-layout`, `--kv-order`,
`--weight-streams`, `--chunk-bytes`, `--kv-chunk-bytes`, `--layers a:b`,
`--request-bytes`, `--stacks`, `--tp`, `--n-kv-heads`, `--dtype-bytes`.
`docs/phase1_trace_generator.md` explains what each one models.
