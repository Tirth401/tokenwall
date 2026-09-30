# Phase 1 walkthrough: what one decode step reads, and how we write it down

Written for study. Each idea is explained in plain words with an example
first, then tied to the technical term. Every number comes from
`RESULTS.md` (Phase 1) or from `configs/`.

## 1. One token = read the whole library once

Plain version: imagine a librarian who, to write one more word of a story,
must re-read every book in the library from cover to cover. The books never
change; the librarian just has to look at all of them again for every word.

Technical version: in decode mode the model processes one new token per step.
Each layer multiplies a single vector (the token's hidden state) by every
weight matrix. That is a matrix-vector product (GEMV): each weight element is
read from memory once and used once. So the bytes read per step are simply the
size of the weights, whatever the batch size. For Llama 3 8B in bf16 that is
13.98 GiB per step (the layer weights plus the 1 GiB output projection called
lm_head; the embedding table is only looked up, one row per sequence).

The whole point of the project follows from this: at 819.2 GB/s (one HBM3
stack at the Ramulator preset's peak) 13.98 GiB takes at least 19 ms, so one
stack cannot produce more than 52.7 tokens per second for this model even with
perfect memory. Anything lost to DRAM timing rules lowers that number.

## 2. The KV cache = notes that grow by one line per word

Plain version: while writing, the librarian also keeps notes about every word
written so far, and re-reads all the notes before writing the next word. The
notes grow by one line per word, and if 32 stories are being written at once
there are 32 notebooks.

Technical version: attention needs the key and value vectors of every past
position. They are cached rather than recomputed. Per layer, each past
position stores one key vector and one value vector per KV head, each
`head_dim x dtype = 128 x 2 = 256 bytes`. Reading them all is

    bytes = layers x 2 (K and V) x batch x kv_heads x past_positions x 256 B

For 8B, batch 1, 4096 past positions: 32 x 2 x 1 x 8 x 4096 x 256 B = 512 MiB,
only 3.5% of the step. At batch 32 it is 16 GiB, more than the weights. This
is why batching helps throughput (weights amortised over 32 tokens) but makes
the KV cache the new wall. After attention, the new position's K and V are
appended: one small write per KV head per sequence.

**GQA** (grouped-query attention): plain version, four readers share one
notebook instead of each keeping their own. Llama 3 8B has 32 attention heads
but only 8 KV heads, so each KV vector is read once and used by 4 heads. The
`--n-kv-heads 32` knob gives every head its own notebook: KV reads x4 (2 GiB)
and 1.5 GiB more Wk/Wv weights per step, which is exactly what the generator
reports.

## 3. Where things sit in memory

Plain version: a parking garage where each car (tensor) gets a numbered bay,
big trucks must start at a bay whose number is a multiple of 64, small
scooters can park right next to each other.

Technical version: the generator lays tensors out in one flat address space in
load order (embedding, then every layer's weights, final norm, lm_head), then
the KV caches (allocated when serving starts). Tensors of 2 MiB or more start
on a 2 MiB boundary, smaller ones on 512 B, mimicking a caching allocator's
large-page and small-block pools. Every request address is a multiple of 32 B
because every tensor dimension we use is. The footprint is checked against
`stacks x 16 GiB`; the run is refused with the stack count that would fit.

## 4. KV layout and issue order: filing by time or by topic

Plain version: you can file your notes by date (one page per day, all topics
on it) or by topic (one folder per topic, pages in date order). If you read
topic by topic but filed by date, you flip through every page and use one
line from each. Same notes, very different amount of page turning.

Technical version: `--kv-layout` sets the memory order of the cache,
`--kv-order` sets the order the attention kernel walks it.

| layout | memory order | `head_outer` walk (default) | `position_outer` walk |
|---|---|---|---|
| `head_major` (HF `past_key_values`) | [batch][kv_head][position][128] | one contiguous 1 MiB run per head at 4096 positions | 256 B every 1 MiB, 8 heads per position |
| `position_major` | [batch][position][kv_head][128] | 256 B every 2 KiB, 4096 times per head | one contiguous 8 MiB run per sequence |

DRAM only sees addresses, and a 1 KiB row serves 32 consecutive 32 B
accesses. A contiguous run turns into 32 accesses per row (row hits); a
256 B stride pattern turns into 8 accesses then a jump to another row. The
mismatched combinations are how "same bytes, different order" becomes "same
bytes, more row misses". A subtle point the tests pin down: a layout is a
permutation. With the cache exactly full, both layouts touch the identical
byte set; only the order differs.

Default choices, and why: `head_major` is what Hugging Face uses;
`head_outer` is how flash-decoding kernels work (one thread block streams one
KV head over all positions). A paged layout (vLLM, 16-position blocks) is a
future knob.

## 5. The segment list: a recipe instead of a list of every spoonful

Plain version: instead of writing down every single glance the librarian
makes (486 million of them per word for 8B), write the recipe: "start at
shelf 12, read 1 MiB straight through", "read 256 bytes, skip 2 KiB, repeat
4096 times". Two cooks, one speaking Python and one C++, follow the same
recipe; then we compare the dishes bite for bite.

Technical version: `traces/<run>.segs` holds groups of segments. A segment
is `(R|W, base, run_bytes, runs, stride, outer_runs, outer_stride)` and
expands to `outer_runs x runs x (run_bytes / 32)` requests with two-level
striding, which covers every layout/order combination in one segment per
(batch, head). A group either plays its segments in order (`chunk_bytes 0`)
or round-robins `chunk_bytes` from each (`--weight-streams N` splits a weight
sweep into N interleaved streams; `--kv-chunk-bytes` interleaves KV heads).
`python/tokenwall/segments.py` (numpy) and `cpp/include/tokenwall/segments.h`
(streaming `Expander`) both expand it. `tests/test_cross_expander.py` runs
both on 12 tiny-model configurations and a real 8B layer slice, compares an
order-sensitive 64-bit hash of every (address, read/write) pair and, for the
small cases, the exported text line by line. The full 8B step has the
fingerprint `0x8fdf6134cc81fb25` over 485,839,360 requests; regenerate and
compare.

## 6. Why one tensor-parallel shard

Plain version: eight chefs each hold one eighth of the cookbook and cook one
eighth of every dish; a diner sees one plate, but each chef only ever reads
their own eighth.

Technical version: Llama 3 70B in bf16 is 131 GiB; no single GPU holds it.
Production serving uses tensor parallelism across 8 GPUs: attention heads and
MLP columns are split, so each GPU stores `heads/8` query heads, `kv_heads/8`
KV heads and `intermediate/8` MLP columns, plus replicated norms. Each GPU's
HBM genuinely holds about an eighth of the weights and an eighth of the KV
cache. Simulating one shard is therefore the deployment shape, not a
workaround. For 70B at TP=8 the shard is 16.9 GiB (two stacks), it reads
16.18 GiB of weights per step, and holds exactly one KV head, so at batch 1
the step is 99% weight streaming.

## 7. What the generator says (arithmetic, not simulation)

| Run | Total per step | Weights | KV read | Requests | Floor at preset peak |
|---|---:|---:|---:|---:|---:|
| 8B, batch 1, 1 stack | 14.48 GiB | 96.5% | 3.5% | 485.8 M | 18.98 ms |
| 8B, batch 32, 2 stacks | 29.98 GiB | 46.6% | 53.4% | 1006.1 M | 19.65 ms |
| 8B full MHA, batch 1, 2 stacks | 17.48 GiB | 88.6% | 11.4% | 586.5 M | 11.46 ms |
| 70B TP=8, batch 1, 2 stacks | 16.34 GiB | 99.0% | 1.0% | 548.3 M | 10.71 ms |

"Floor" divides bytes by `stacks x 819.2 GB/s`, the peak of HBM3 per
Ramulator 2.1's preset. It is the best case; Phases 3 to 5 measure the
shortfall. Note the batch-32 line: 32 tokens per 19.65 ms floor, versus 1
token per 18.98 ms, is the whole economic argument for batching, and the
KV cache is what stops it scaling further.

## 8. Knobs

| Flag | Models |
|---|---|
| `--batch`, `--seq` | sequences in flight, past positions per sequence |
| `--tp`, `--stacks` | tensor-parallel degree (one shard simulated), HBM3 stacks on the GPU |
| `--kv-layout`, `--kv-order`, `--kv-capacity` | cache memory order, kernel walk order, allocated positions |
| `--n-kv-heads` | GQA group size sweep (32 on 8B = full MHA) |
| `--weight-streams`, `--chunk-bytes` | how many parallel streams sweep a weight tensor, and how finely they interleave |
| `--kv-chunk-bytes` | interleave KV heads instead of reading them one after another |
| `--layers a:b`, `--head-tail` | slice for validation runs; include or drop embedding and lm_head |
| `--request-bytes` | 32 (HBM3 native); 64 emits pairs of adjacent accesses |
| `--dtype-bytes` | 2 for bf16; 1 to model int8/fp8 weights |
| `--alignment`, `--small-alignment` | placement boundaries |

## 9. Modeling limits to say out loud

- One ordered stream stands in for thousands of concurrent warps. The
  `weight_streams` and `kv_chunk_bytes` knobs interleave streams, but the
  memory controller's queue depth decides how much of that parallelism it can
  see. This is the largest simplification, and Phase 5 sweeps it.
- Issue order within a layer is a choice (K fully, then V; unfused QKV). Byte
  totals do not depend on it; row locality can.
- Token ids for the embedding lookup are deterministic placeholders; the
  traffic is B rows of 8 KiB, negligible.
- No prefill, no speculative decoding, no MoE, no paged KV yet, no L2 cache
  in front of HBM (weights are streamed, so an L2 would not help them; it
  could help the small KV-append writes).

## 10. Reproduce

```bash
source .venv/bin/activate
python -m pytest -q
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 4096 --out traces/llama3_8b_tp1_b1_s4096
./build/cpp/tw_expand traces/llama3_8b_tp1_b1_s4096.segs
python -m tokenwall gen --model configs/models/llama3_8b.yaml --batch 1 --seq 1024 --layers 0:1 --out traces/llama3_8b_layer0_tp1_b1_s1024
python -m tokenwall export-ramulator traces/llama3_8b_layer0_tp1_b1_s1024.segs --out traces/l0.txt --max-requests 1800000
python scripts/ramulator2_run_trace.py traces/l0.txt --label llama3_8b_layer0_prefix1800k
```
