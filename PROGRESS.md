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
- Phase 2 Address mapping: **next**. Settle the open questions below first.
- Phases 3 to 6: not started

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

## Open questions to settle at the start of Phase 2

1. **Which three mapping policies?** Proposal: (a) `ramulator_robarcoch`, a
   bit-exact copy of Ramulator's RoBaRaCoCh for HBM3 (column low, then pseudo
   channel, SID, bank group, bank, row) so mapping can be validated separately
   from timing via Ramulator's `ReadWriteTrace`; (b) `bank_low`: column bits,
   then bank group and bank, then pseudo channel and channel, then row, so
   consecutive 32 B accesses spread across bank groups first; (c) `bank_high`:
   channel and pseudo channel interleaved at 256 B or 1 KiB, bank bits above
   the row bits' low part, plus an XOR of row bits into bank bits (GPU-style
   hashing, like Ramulator's MOP4CLXOR). Spec asks for at least one bank-low
   and one bank-high policy.
2. **Channel count and Ramulator.** Channels = 16 x stacks. Ramulator's
   `CacheLineInterleave` needs a power-of-two channel count, so 16, 32, 64 work
   for validation; 80 (five stacks) only in our core.
3. **Interleave granularity knob.** 32 B (one access), 256 B, 1 KiB (one row)
   per channel: which values to expose and which is the default.
4. **Request size 64 B path.** `request_bytes=64` currently emits 64 B
   requests; Phase 2 mapper must split them into two 32 B accesses (adjacent
   columns), or the generator emits 32 B always and the knob only tags pairs.
