# Phase 2 walkthrough: which address bits pick the bank, and why that doubled bandwidth

Written for study. Plain words and an example first, then the technical term.
Every number is from `RESULTS.md` (Phase 2) and its raw files under
`results/phase2/`. HBM3 timings are Ramulator 2.1's preset, not JEDEC values.

## 1. An address is a phone number

Plain version: a phone number's digits mean different things. Some pick the
country, some the area, the rest the subscriber. A memory address is the same:
some of its bits say which channel to use, some which bank, some which row,
some which 32-byte slot in the row. The memory controller does not choose where
data lives; it chooses how to *read the number*, and that choice is the
address mapping policy.

Technical version: drop the low 5 bits of a byte address (one 32 B access) to
get a line index. One HBM3 stack has 29 line-index bits to assign: 4 channel,
1 pseudo channel, 1 SID, 2 bank group, 2 bank, 14 row, 5 column. A policy in
`python/tokenwall/addrmap.py` is an ordered list of (field, bits) pieces from
the least significant bit upward; `map` slices, `unmap` reassembles, and a
field may be split into two pieces (column bits below and above the channel
bits, which is what a channel interleave granularity means).

## 2. Dealing cards: the four policies

Plain version: think of 32 consecutive 32-byte lines as 32 cards. The policy
decides who gets them. `ramulator` deals all 32 to one player (one bank, one
row) before moving on. `bank_low` deals one card to each of 32 players.
`bank_high` deals one player the whole deck and then the next deck too.

`python -m tokenwall map --policy X --stacks 1` prints the layout. The two
that matter most, 16 channels:

```
ramulator     bit order (low to high): channel[3:0] column[8:4] pc[9] sid[10] bg[12:11] bank[14:13] row[28:15]
              bank group changes every 64 KiB, bank every 256 KiB, row every 1 MiB
bank_low      bit order (low to high): channel[3:0] pc[4] bg[6:5] bank[8:7] sid[9] column[14:10] row[28:15]
              pseudo channel changes every 512 B, bank group every 1 KiB, row every 1 MiB
```

"Changes every N bytes" is over the whole 16-channel address space; inside one
channel divide by 16. So under `ramulator` a channel sees 32 consecutive
requests to one bank and one row before anything changes; under `bank_low` it
sees the pseudo channel flip on every request and the bank group on every
second one.

| Policy | One sentence | Kept because |
|---|---|---|
| `ramulator` | Ramulator 2.1's default, reproduced bit for bit | the validation baseline |
| `bank_low` | bank bits right above the channel bits | the spec's "bank bits low" |
| `bank_high` | bank bits above the row bits | the spec's "bank bits high"; the anti-pattern |
| `bank_low_xor` | `bank_low` with row bits XORed into bank bits | what GPU and CPU controllers do to break strides |

## 3. Why hit rate cannot rank them (this was the quiz)

Plain version: in the library, every policy kept the right book open at the
right page 96.9% of the time. The difference is that under `ramulator` one
reader takes 32 consecutive turns at one desk while the other desks stand
idle, and every turn at the same desk must be spaced further apart.

Technical version: after a read command, the next read to the *same bank
group* must wait tCCD_L = 4 CK, but the data burst only occupies the bus for
nBL = 2 CK. A stream that stays in one bank group can use at most half the
data wires. A read to a *different* bank group may follow after tCCD_S = 2 CK,
exactly the burst length, so alternating bank groups keeps the wires full.
Static analysis of the full Llama 3 8B step (486 M requests) shows the two
policies differ only in that respect:

| Policy | Ideal open-row hit rate | Back-to-back requests in a channel that share a bank group | Distinct banks per 32 requests |
|---|---:|---:|---:|
| `ramulator` | 96.87% | 96.9% | 16.0 |
| `bank_low` | 96.87% | 0.0% | 32.0 |
| `bank_high` | 96.87% | 96.9% | 16.0 |
| `bank_low_xor` | 96.87% | 0.0% | 32.0 |

The hit rate is identical to two decimals. The pairing column predicted the
bandwidth ordering before Ramulator ran.

## 4. What Ramulator measured (the headline)

Setup: all 13,763,072 reads of Llama 3 8B layer 0 (batch 1, 1024 past
positions), pre-mapped by each policy, one HBM3 stack = 16 channels, Ramulator
2.1 timing with its HBM34 controller, FR-FCFS, open-row policy, all-bank
refresh, 32-entry queues. Peak is 819.2 GB/s.

| Policy | Achieved | % of peak | Row hits | Avg read latency |
|---|---:|---:|---:|---:|
| `ramulator` | 344.5 GB/s | 42.1% | 96.8% | 61.9 ns |
| `bank_low` | 686.5 GB/s | 83.8% | 95.7% | 38.6 ns |
| `bank_high` | 246.4 GB/s | 30.1% | 96.8% | 76.2 ns |
| `bank_low_xor` | 685.8 GB/s | 83.7% | 95.7% | 38.6 ns |

The same four numbers on the first 1.8 M reads (13% of the layer) were within
2 GB/s, 0.2% of peak, so the result is stable across the layer. Reading it:

- **Mapping alone doubles bandwidth at a constant hit rate.** Nothing else
  changed: same requests, same timings, same controller.
- **`bank_low` reaches 84% of peak.** All-bank refresh removes 9% (each pseudo
  channel is dead for 350 ns every 3.9 us), and row switches take most of the
  rest. Phase 3's stall breakdown will measure this instead of inferring it.
- **`bank_high` is bad but not catastrophic (30%).** Within a channel every row
  switch is a same-bank miss with nothing to overlap it, but 16 channels still
  work in parallel because the channel bits stay lowest in every policy.
- **`bank_low_xor` bought nothing.** Decode traffic is sequential sweeps and
  regular KV strides; none of them alias onto one bank under `bank_low`. The
  knob stays, and the null result is stated as such.
- **Latency halves** because the queues drain twice as fast.

The `ramulator` row matches the Phase 0 smoke on one channel (42.4%) and the
Phase 1 format check (42.0%): this policy makes every channel behave like the
one-channel case.

## 5. Proving our baseline is Ramulator's

We claim `ramulator` reproduces Ramulator's mapping bit for bit. Two checks:

1. `tests/test_addrmap.py` contains a literal transcription of Ramulator's C++
   (`CacheLineInterleave::apply` and `RoBaRaCoCh::apply` with its
   `slice_lower_bits` helper) and compares 5000 random addresses at 1, 16 and
   32 channels and 32 B, 256 B and 1 KiB interleave.
2. `scripts/ramulator2_mapping_check.py` feeds the same read-only traffic to
   Ramulator twice: flat addresses that Ramulator maps itself, and addresses
   we pre-mapped. At 16 channels with 32 B and 256 B interleave and at 32
   channels, every statistic is identical: ticks, requests served, hits,
   misses, conflicts. If a single bit were wrong somewhere, some request would
   land in a different bank and the counts would drift.

Check 2 uses Ramulator's `ReadWriteTrace` frontend with pass-through mappers.
That frontend leaves the flat address unset, and Ramulator keys write
coalescing on it, so the comparison is reads-only. Phase 4 patches this.

## 6. A subtlety worth an interview minute: refresh versus open rows

`bank_low` has a *lower* hit rate than `ramulator` (95.7% versus 96.8%) yet
twice the bandwidth. Where do the extra misses come from? Under `bank_low`
all 1024 banks hold an open row at once. An all-bank refresh must close every
row first, so each refresh costs up to 1024 row switches. The full-layer run
lasted 642 us, 164 refresh intervals, and the surplus of row switches over
`ramulator` (148,719) is 89% of 164 x 1024. This is analysis, not a measured
attribution; Phase 3 should label "row closed by refresh" explicitly, and
Phase 5 should test whether per-bank refresh recovers it.

## 7. KV layout under each mapping (static)

Plain version: filing notes by date instead of topic only hurts if the
librarian's shelving scheme punishes it.

Technical version: on a KV-heavy slice (batch 32, 4096 past positions, layer
0, 30.4 M requests, two stacks), the `position_major` layout walked head by
head drops KV-read ideal hits from 96.85% to 93.74% under `ramulator` and
leaves them at 96.85% under `bank_low`. The strided pattern (256 B every
2 KiB) crosses row boundaries under Ramulator's mapping and does not under
`bank_low`. A robust mapping makes the software layout question smaller;
Phase 5 measures it with timing.

## 8. What the static metrics can and cannot say

- Ideal open-row hit rate: same for every policy here, so useless for ranking
  mappings on streaming traffic. Good for spotting layout problems (section 7).
- Distinct banks per 32 requests: saturates at the channel count when there
  are 32 or more channels; it measures channel spread, not bank spread inside
  a channel.
- Per-channel back-to-back pair classes (same pseudo channel and bank group,
  same pseudo channel other group, other pseudo channel): the metric that
  discriminates, because it maps directly onto tCCD_L, tCCD_S and independent
  data buses.

## 9. Limits to say out loud

- The timing is Ramulator's. Tokenwall's own core arrives in Phase 3 and is
  validated against these very runs in Phase 4.
- Reads only, one layer, batch 1, one model. Batch 32 and 70B come in Phase 5.
- HBM3 timings per Ramulator 2.1's `HBM3_6400Mbps` preset; 13 of them are
  Ramulator's estimates.
- FR-FCFS with 32-entry queues per channel, no write traffic, all-bank
  refresh. A production GPU controller has deeper queues and its own hashing.
- Channel counts are powers of two, like Ramulator's channel mapper.

## 10. Reproduce

```bash
source .venv/bin/activate
python -m pytest -q                                                   # 92 tests
python -m tokenwall map --policy bank_low --stacks 1
python -m tokenwall locality traces/llama3_8b_tp1_b1_s4096.segs --policy ramulator --stacks 1
python scripts/ramulator2_mapping_check.py                            # bit-exactness + policies, 1.8 M-read prefix
python scripts/ramulator2_mapping_check.py --part b --requests 20000000   # policies on the whole layer (~2 min)
```
