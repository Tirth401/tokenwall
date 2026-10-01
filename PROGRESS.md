# PROGRESS

Session log and open questions so a future session can pick up where we left
off. Newest session first. Numbers live in `RESULTS.md`, not here.

## Working agreement (do not skip)

Explain in plain language with a concrete example FIRST, then connect it to
the technical terms. Explain before building and after. End every turn with an
interview note. Quiz every few turns. One phase at a time, stop and wait.
Never invent a metric. Prioritise an end-to-end working pipeline over a
complete one (deadline). Keep "HBM3 per Ramulator 2.1's preset, never per
JEDEC" visible everywhere; keep the vendor-override path obvious.

## Phase status

- Phase 0 Setup and orientation: **done** 2026-09-30
- Phase 1 Trace generator: **done** 2026-09-30
- Phase 2 Address mapping: **done** 2026-09-30
- Phase 3 Timing core: **done** 2026-09-30 (cycle-exact with Ramulator on the first validation run)
- Phase 4 Validation: **next**. Scope proposal below.
- Phases 5 to 6: not started

Repo is public at https://github.com/Tirth401/tokenwall (pushed 2026-09-30 on
Tirth's instruction; author is the GitHub no-reply address). Push after each
phase unless told otherwise.

## Session 4, 2026-09-30: Phase 3

Decisions taken by Tirth: go with the recommendations (half-CK ticks, rule
order, FR-FCFS controller like Ramulator's, binding-constraint attribution).

What exists now:

- `scripts/export_dram_spec.py` -> `configs/hbm3/hbm3_16gb_8hi_6400.spec`:
  Ramulator's resolved runtime tables (29 timings in ticks, 11 command
  lengths, 60 constraint entries with names), self-checked entry by entry
  against `dram.to_config()`. `--override`/`--out` for vendor timings.
- `cpp/include/tokenwall/{dram_spec,device,controller,system}.h` + sources:
  spec loader with per-constraint enable flags; timing tree + bank state
  machine mirroring Ramulator's node/device; controller mirroring HBM34
  (buffers, FR-FCFS, write watermarks, all-bank refresh, edge and pairing
  rules); memory system, trace frontend with Ramulator's loop interleaving,
  drain mode, JSON output; per pseudo-channel slot attribution.
- `tokenwall sim` CLI; `scripts/tokenwall_vs_ramulator.py` side-by-side.
- Tests: 34 C++ cases (17 per-rule timing tests with hand-derived ticks, 7
  controller timelines, 10 earlier), 92 Python tests unchanged.
- `results/phase3/`: validation JSON/logs, full-step runs.

Decisions made this session (alternative in brackets):

- Table-driven timing engine fed by Ramulator's resolved table [hand-coded
  if-statements per rule]. Same rule semantics by construction; "one
  constraint at a time" lives in the per-rule unit tests and in the
  `--disable` ablation switch, not in 51 bespoke code paths.
- Mirror Ramulator's controller tick structure and FR-FCFS exactly [a
  simpler scheduler]. Chosen so Phase 4 diffs isolate timing bugs; it paid
  off: zero difference on the first run.
- Attribution per (pseudo channel, rising edge) slot [per idle tick per bank].
  A slot is `data` if a burst is on that PC's wires, else the named rule that
  blocks the oldest request to that PC, else `arbitration`/`empty`;
  refresh-in-progress is reported first because Ramulator's controller stops
  all read/write scheduling while a priority request waits.
- Ramulator-comparable stop condition by default (stop at last send),
  `--drain` for full-step bandwidth over every byte.

Facts learned (verified this session):

- Tokenwall == Ramulator 2.1 on 1.8 M reads of layer 0, 16 channels, for
  `ramulator` and `bank_low`, refresh none and allbank: ticks, served, hits,
  misses, conflicts, bandwidth, latency all identical. Tokenwall ran 1.6 to
  1.8x faster (3.0 s vs 4.5 s).
- Where the bandwidth goes (slot attribution, 16 channels, reads only):
  `ramulator` mapping, no refresh: data 39.6%, RD blocked by tCCD_L 34.7%,
  RD waiting for tRCD 19.5%, column bus 4.0%, ACT waiting for tRP 2.2%.
  `bank_low`, no refresh: data 94.0%, tRCD 3.2%, column bus 1.4%, tCCD_R
  1.4%. With all-bank refresh `bank_low` drops to 84.0% data and nRFC takes
  8.55%.
- Surprise: under the `ramulator` mapping, all-bank refresh made the run 5.9%
  SHORTER (534,313 vs 567,661 ticks). Refresh's PREab closes all 64 rows at
  once, so the next visit to each bank is a miss (ACT only) instead of a
  conflict (PREpb, then tRP, then ACT); conflicts fell from 55,280 to
  13,632. For streaming traffic under an open-row policy, batched precharge
  can be worth more than tRFC costs. Analysis, backed by the attribution
  shift from tRCD/tRP (21.6%) to nRFC + tRCD/tRP (15.5%).
- HBM3 edge rules cost a tick here and there: a RD whose timing expires on an
  even tick issues on the next odd one (tests pin this: ACT at 1, RD at 65).

## Phase 4 scope proposal (validation)

1. Writes: patch Ramulator's `ReadWriteTrace` to carry the flat address (so
   its write coalescing works) and validate with the KV-append writes
   included, both policies, refresh on and off.
2. Matrix: policies x interleave (32 B, 256 B, 1 KiB) x channels (16, 32) x
   refresh, on a full layer; record the error table (expected zero).
3. Command-level diff tool: Ramulator's `CmdTraceRecorder` plugin versus
   `tokenwall sim --cmd-trace`, first divergence reported per bank; needed
   only if a case ever disagrees.
4. Per-bank refresh (`HBM34PerBankRefresh`) in the core, validated, so Phase
   5 can sweep refresh policy.
5. Batch-32 and 70B slices through both (KV-heavy, TP shard).

## Session 3, 2026-09-30: Phase 2

Decisions taken by Tirth: go with the recommended four policies, publish the
repo, keep moving.

What exists now:

- `python/tokenwall/addrmap.py`: `Geometry` (from the HBM3 YAML, channels =
  16 x stacks), `Policy` (ordered (field, bits) pieces over the 32 B line
  index plus optional XOR rules), `make_policy` for `ramulator`, `bank_low`,
  `bank_high`, `bank_low_xor`, each with a channel-interleave knob; vectorised
  `map`/`unmap`, `describe`, ReadWriteTrace export.
- `python/tokenwall/locality.py`: static analysis (ideal open-row hit rate per
  bank, distinct banks per window, per-channel back-to-back pair classes,
  channel balance, per request class).
- `cpp/include/tokenwall/addrmap.h` + `cpp/src/addrmap.cpp`: mirror; `tw_expand
  --map` writes pre-mapped Ramulator traces from C++ (13.8 M lines in seconds).
- CLI: `python -m tokenwall map | locality | export-ramulator --policy --reads-only`.
- Tests: 92 total (36 mapping incl. Ramulator C++ transcription, 2 locality,
  17 Python-vs-C++ mapping cross-checks).
- `scripts/ramulator2_mapping_check.py`: bit-exactness (flat vs pre-mapped,
  identical stats) and policy comparison under Ramulator timing, 16 channels;
  `scripts/r2util.py` runs N channels, pre-mapped traces, frontend ratio.
- `results/phase2/`: mapping check JSON + logs, locality JSON for the full 8B
  step (4 policies) and a KV-heavy layer slice (2 layouts x 4 policies).

Decisions made this session (alternative in brackets):

- Generic ordered-piece layout with a field allowed in several pieces
  [hard-coded per-policy bit arithmetic]. One `map` implementation serves all
  policies and all interleave granularities; the C++ mirror is 40 lines.
- `ramulator` policy reproduces Ramulator's default exactly, so mapping and
  timing can be validated separately [only our own policies]. Validation is
  end to end: same traffic flat and pre-mapped gives identical statistics.
- Mapping validation uses read-only traces [patch Ramulator's ReadWriteTrace
  to carry the flat address]. Ramulator keys write coalescing and read
  forwarding on `req.addr`, which ReadWriteTrace leaves at -1. Patch deferred
  to Phase 4 where writes must be compared too.
- XOR rule folds row bits [1:0] into bank group and [3:2] into bank
  [Ramulator's MOP4CLXOR variant]. Simple, invertible, testable.
- Channel counts must be powers of two (16, 32, 64) [modulo channel select].
  Same limit as Ramulator's CacheLineInterleave; five stacks (H100) later.

Facts learned (verified this session):

- `ramulator` policy is bit-exact with Ramulator 2.1 at 16 and 32 channels and
  at 32 B and 256 B interleave: every statistic identical (RESULTS Phase 2).
- On real Llama 3 8B layer-0 reads, one HBM3 stack, Ramulator timing: mapping
  alone moves bandwidth from 42% (`ramulator`) to 84% (`bank_low`) of peak
  while the row-hit rate stays about 96%. `bank_high` gets 30%. Hit rate does
  not rank mappings; bank parallelism and tCCD_S pacing do.
- `bank_low_xor` equals `bank_low` on every stream measured so far: decode
  traffic has no power-of-two stride that aliases banks under `bank_low`.
  An expectation that did not pan out; keep it as a knob, say so in writeups.
- Under `bank_low` all 1024 banks hold an open row, so each all-bank refresh
  (every 3.9 us) closes 1024 rows: about 21 refreshes x 1024 = 21.5 k extra
  row switches in the 84 us run, matching the measured surplus of 19.3 k over
  `ramulator`. Refresh policy is therefore not independent of mapping
  (Phase 5 sweep; Phase 3 stall breakdown should attribute it).
- KV layout matters under Ramulator's mapping but not under `bank_low`:
  position_major KV reads drop to 93.7% ideal hits under `ramulator`, stay at
  96.85% under `bank_low` (static analysis, B=32 slice).
- Static "distinct banks per 32 requests" saturates at the channel count when
  channels >= 32 (it measures channel spread); the per-channel back-to-back
  pair classes are the discriminating static metric.
- Ramulator with 16 channels runs the 1.8 M-request prefix in 2.5 to 5 s
  (0.35 to 0.7 M requests/s); a full layer (13.8 M reads) is 20 to 40 s.

## Phase 3 questions, as resolved

Half-CK ticks with Ramulator's command-length adjustments; the whole rule
table at once (table-driven) with per-rule tests and an ablation switch
instead of a hand-coded incremental build; controller mirrors HBM34; stall
attribution per column-command slot by binding constraint; writes deferred
to Phase 4 (Ramulator-side patch).

## Session 2, 2026-09-30: Phase 1

Decisions taken by Tirth at the start of the session: 32-byte requests with a
`request_bytes` knob; one tensor-parallel shard as the unit with a `stacks`
knob; compact segment list expanded in both Python and C++ with a hash test in
the suite; Llama 3 8B and 70B from published config.json with provenance, no
third model, `n_kv_heads` as a sweep knob on 8B; Guesstimate provenance
visible everywhere with an obvious override path; end-to-end over complete.

What exists now:

- `python/tokenwall/`: `model_config.py` (shape from YAML, overrides),
  `hbm_config.py` (reads the extracted HBM3 YAML), `layout.py` (address
  space), `segments.py` (format, numpy expander, hash, Ramulator export),
  `tracegen.py` (decode step), `cli.py` (`python -m tokenwall gen|export-ramulator|hash|stats`).
- `cpp/include/tokenwall/segments.h` + `cpp/src/segments.cpp`: parser and
  streaming `Expander`; `tw_expand` tool (count, hash, Ramulator export).
  Expands a full 8B step (486 M requests) in 1.2 s.
- `tests/`: 36 pytest cases (format vs naive loop, closed-form byte totals,
  GQA knob, layouts, slices, TP, capacity error, 13 Python-vs-C++ cross cases).
- `configs/models/llama3_8b.yaml`, `llama3_70b.yaml` + raw JSON, imported by
  `scripts/import_hf_config.py` (official repos gated, mirrors recorded).
- `traces/*.meta.json` for four runs (8B b1, 8B b32, 8B MHA, 70B tp8) and a
  layer-0 slice; `.segs` are gitignored and regenerate in under a second.
- `scripts/r2util.py`, `ramulator2_run_trace.py`: run any LD/ST file through
  the HBM3 one-channel configuration; `extract_hbm3_params.py --override`.
- `docs/phase1_trace_generator.md`.

Decisions made this session (alternative in brackets):

- Segment format with two-level striding and groups that optionally
  round-robin [flat binary records; generate on the fly inside the C++ core].
  Two-level striding expresses every KV layout/order combination in one
  segment per (batch, head); groups model parallel streams. Text format so C++
  needs no JSON library.
- Stream hash is FNV-1a over 64-bit words `(addr << 1) | is_write` [SHA-256 of
  packed bytes]. One multiply per request in Python, trivial in C++.
- Placement mimics a caching allocator: tensors >= 2 MiB on 2 MiB boundaries,
  smaller ones (norm weights) packed on 512 B [uniform 2 MiB alignment wasted
  about 2 MiB per norm].
- Issue order per layer: input norm, Wq, Wk, Wv, all K reads then all V reads,
  KV append writes, Wo, post norm, gate, up, down; embedding rows first and
  final norm + lm_head last [fused QKV / fused gate-up as one sweep; interleaved
  K and V]. Byte totals are identical; order is a documented modeling choice.
- lm_head is included even though the spec did not list it: it is 1 GiB per
  token on 8B (6.5% of the weights) and omitting it would understate traffic.
- Default KV layout head_major (HF `past_key_values` shape), default issue
  order head_outer (flash-decoding walks one KV head over all positions).
  position_major is the other layout; a paged layout is a future knob.
- Default `weight_streams=1` (one sequential sweep per tensor) with knobs to
  split into N round-robin streams [default to an SM-count-like value]. A GPU
  with 132 SMs really produces many interleaved streams; Phase 5 sweeps this.

Facts learned (verified this session):

- Official `meta-llama` Hugging Face repos answer HTTP 401 without a licence
  click; NousResearch mirrors serve byte-identical configs (SHA recorded).
- The python.org macOS Python has no CA bundle; `certifi` fixes urllib SSL.
- Ramulator 2.1 ingests our LD/ST export; on one channel it runs about 0.47 M
  requests/s (1.8 M requests in 3.8 s). A full 8B step (486 M) would take
  about 17 minutes and 8 GB of RAM for Ramulator's trace vector, so Phase 4
  validation runs on layer slices, as planned.
- A KV layout is a permutation of the same bytes: with the cache exactly
  full, head_major and position_major touch the same byte set in different
  orders (test_kv_layout_is_a_permutation_of_the_same_bytes).
- Llama 3 8B at batch 1, 4096 past positions, fits one 16 GiB stack at 98.2%.
  Batch 32 or full MHA needs two stacks; 70B at TP=8 needs two stacks.

## Phase 2 questions, as resolved

Policies: `ramulator` (bit-exact baseline), `bank_low`, `bank_high`,
`bank_low_xor`. Channel counts: powers of two. Interleave knob: 32 B to 1 KiB,
default 32 B (Ramulator's default). 64 B requests: still emitted as 64 B by
the generator; the timing core will treat `request_bytes / 32` adjacent
accesses per request (open item carried into Phase 3).
