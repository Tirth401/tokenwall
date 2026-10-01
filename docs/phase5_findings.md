# Phase 5 findings: what each knob does, and what surprised us

Written for study. Plain words and an example first, then the technical term.
Every number is in `RESULTS.md` (Phase 5) with the command that produced it.
Timings are HBM3 per Ramulator 2.1's `HBM3_6400Mbps` preset (13 of them
Ramulator's estimates); the simulator is Tokenwall, validated identical to
Ramulator 2.1 in Phases 3 and 4. Figures are in `results/phase5/plots/`.

## 0. How the sweeps were run

One decode layer per run instead of the whole step, because Phase 4 showed the
per-layer fraction of peak matches the full step within 0.1 point (a step is
32 near-identical layers). That made 84 runs cost ten minutes. Every run is
drained (all requests served, writes included). Five full-step runs anchor the
absolute token times.

## 1. Address mapping is the first-order knob

Plain version: the address is a phone number and the mapping says which digits
pick the desk. Deal consecutive lines to different desks and the room works in
parallel; deal them to one desk and everyone waits.

| Mapping | No refresh | All-bank refresh | Per-bank (Ramulator's blocking controller) |
|---|---:|---:|---:|
| `ramulator` (default) | 39.5% | 42.0% | 28.9% |
| `bank_low` | 94.0% | 83.8% | 67.7% |
| `bank_high` | 33.5% | 30.1% | 31.3% |
| `bank_low_xor` | 94.0% | 83.7% | 67.8% |

Two to one between the default and `bank_low`, in every refresh mode. XOR
hashing changed nothing for the third phase running: decode traffic has no
power-of-two stride that aliases banks under `bank_low`. `bank_high`, the
anti-pattern, is only 10 points worse than the default, because the channel
bits stay lowest in every policy and 16 channels still work in parallel.

## 2. Batch, sequence length, GQA and model size do not move the fraction

Plain version: the librarian walks the shelves at the same efficiency whether
one story or thirty-two are being written; what changes is how many shelves
must be walked.

Batch 1 to 32 moves the KV-cache share of bytes from 4% to 55%, and 512 to
8192 past positions moves it to 71%, while the fraction of peak stays at 42%
and 84% to the first decimal. Eight versus thirty-two KV heads, and Llama 3
70B versus 8B on two or four stacks: the same two numbers. Under a matched KV
layout (next section) KV reads are long sequential runs exactly like weight
sweeps, so DRAM cannot tell them apart. Consequence: token time scales with
bytes per step, and the fraction is a property of the memory layout and the
address mapping, not of the model.

## 3. The surprise: KV layout must match the kernel's walk order, under either mapping

Plain version: filing notes by date and reading them by topic means flipping
through every page. Phase 2's static analysis said `bank_low` would not care.
It was wrong.

| Layout | Walk order | `ramulator` | `bank_low` | KV-read row hits (`bank_low`) |
|---|---|---:|---:|---:|
| head-major | by head (matched) | 41.9% | 83.7% | 95.7% |
| head-major | by position (mismatched) | 21.9% | 34.2% | 49.3% |
| position-major | by head (mismatched) | 16.5% | 17.9% | 95.1% |
| position-major | by position (matched) | 42.0% | 83.8% | 95.7% |

Look at the third row: 95.1% row hits and 17.9% of peak. The hit rate, which
Phase 2 measured, is fine; the bandwidth is a fifth. Technical version: under
position-major layout, one head's consecutive vectors sit 2 KiB apart, 64
lines. Under `bank_low` with 32 channels the low five line bits select the
channel, so a stride of 64 lines leaves the channel bits unchanged: one head's
whole stream lands on 8 of the 32 channels (the 8 consecutive lines of each
256 B vector). A quarter of the desks do all the work. The second row fails
differently: under head-major layout, one position's eight head vectors are
1 MiB apart, which under `bank_low` lands four of them in different rows of
the *same* bank, so the hit rate halves and conflicts pile up.

What Phase 2's static analysis lacked was a per-channel spread metric; its
"distinct banks per 32 requests" counted the four bank groups the stride
cycled through and missed that they were all on eight channels. Timing
simulation does not make that mistake. The actionable rule: the KV cache's
memory order must match the attention kernel's traversal order, and when it
does, either order is equally good. Hugging Face's head-major cache with a
flash-decoding style per-head walk is a matched pair.

## 4. Refresh is a controller question, not a DRAM question

Plain version: closing the whole room for 350 ns every 3.9 µs costs 10 points.
Cleaning one desk at a time should cost less, but Ramulator's librarian stops
serving everyone while waiting to clean a desk, so it cost 26 points. Keep
serving the other desks and the cost falls to 2 points.

| Refresh | Controller | `bank_low` | Longest refresh postponement |
|---|---|---:|---:|
| none | | 94.0% | |
| all-bank | blocking (Ramulator) | 83.8% | 0.06 µs |
| per-bank | blocking (Ramulator) | 67.7% | 0.06 µs |
| per-bank | non-blocking, bank reserved | 92.2% | 0.15 µs |

The attribution for the blocking per-bank case says why: 25% of all slots are
the whole channel waiting tRP between a refresh's PREpb and its REFpb while
nothing else is scheduled. The non-blocking variant keeps scheduling other
banks.

The honest part. The first non-blocking attempt measured 92.4% but the new
refresh-postponement statistic showed the longest wait was 567 µs, more than
a hundred tREFI, with only a sixth of the refreshes served inside the run.
Refresh was being starved by a two-tick race: after the refresh's precharge,
a queued read's ACT becomes legal at tRP minus the ACT's length (50 ticks)
and the REFpb at 52, so under load a read reopened the bank first, every
time. The fix is what real controllers do: forbid new row opens on a bank
with a pending refresh. With it, every refresh is served within 0.15 µs and
the gain holds at 92.2%. A result without the postponement number would have
been wrong, and it looked better.

Two smaller refresh facts. Under the default mapping, all-bank refresh still
makes the run faster (39.5% to 42.0%), the Phase 3 observation: its batched
precharge converts conflicts into cheaper misses. And per-bank refresh with
the non-blocking controller also beats all-bank under the default mapping
(42.7% versus 42.0%).

## 5. Ablation: which rules actually cost something

Plain version: take one rule away and see how much comes back. Unphysical, but
it ranks the rules.

| Rule removed | `ramulator` (base 42.0) | `bank_low` (base 83.8) |
|---|---:|---:|
| tCCD_L | +15.0 | +0.2 |
| tRCD (read) | +3.8 | +0.4 |
| tRP | +2.6 | +1.5 |
| tCCD_R | 0 | +2.4 |
| tFAW | 0 | +1.3 |
| tRRD_S, ACT bus occupancy | 0 | +0.1 |
| tRRD_L, tWTR, tRTW, tPPD, tRAS | 0 | 0 |

Under the default mapping tCCD_L alone is 15 points, and the three binding
rules together recover 21 of the 42-point gap to `bank_low`: the rest is
structural, one bank per pseudo channel doing all the work, which no single
rule's removal fixes. Under `bank_low` the leftovers are the cross-die rule
tCCD_R and the row cycle; tFAW, the HBM limit most often quoted, costs 1.3
points here. Write-turnaround rules cost nothing: decode is 99% reads.

## 6. Full decode step anchors

For Llama 3 8B at batch 1 on one stack the step takes 45.2 ms under the
default mapping with all-bank refresh, 22.7 ms under `bank_low`, 20.2 ms with
no refresh, and 28.0 ms under per-bank refresh with the blocking controller;
the floor is 19.0 ms. Per-bank refresh with the non-blocking, bank-reserving
controller takes 20.6 ms, 0.4 ms above running with no refresh at all, while
serving 5.4 million refresh commands with a longest postponement of 0.69 µs.

## 7. What can go on a resume, with qualifiers

- Mapping alone moves a Llama 3 8B decode step from 42% to 84% of HBM3 peak
  (45 ms to 23 ms per token on one stack), measured with a core validated
  identical to Ramulator 2.1.
- A mismatched KV-cache layout costs 2 to 5x under either mapping while the
  row-hit rate stays above 95%: bandwidth loss from channel parallelism that
  hit-rate metrics cannot see.
- Per-bank refresh costs 26 points under Ramulator's priority-first scheduling
  and 2 points once the controller keeps scheduling and reserves the target
  bank; the first attempt starved refresh and was caught by measuring
  postponement.
- The fraction of peak is invariant to batch (1 to 32), context (512 to
  8192), GQA versus MHA, and 8B versus 70B.

Qualifiers every time: HBM3 timings per Ramulator 2.1's preset; a trace-replay
frontend with one request per channel per tick; Ramulator's controller model
with 32-entry queues; one layer per sweep run.

## 8. Reproduce

```bash
source .venv/bin/activate
python scripts/sweep.py --sweeps all --workers 6          # about 10 minutes
python scripts/plot_sweeps.py
./build/cpp/tokenwall sim --segs traces/llama3_8b_tp1_b1_s4096.segs --policy bank_low --stacks 1 --refresh perbank --refresh-nonblocking --drain
```
