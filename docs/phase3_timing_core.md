# Phase 3 walkthrough: the timing core, and why it matched Ramulator on the first run

Written for study. Plain words and an example first, then the technical term.
Numbers are from `RESULTS.md` (Phase 3). HBM3 timings are Ramulator 2.1's
preset, not JEDEC values.

## 1. A desk, a book, and a notebook of promises

Plain version: a bank is a desk with room for one open book (the row buffer).
Opening a book takes time; so does putting it back; and once you open one you
must keep it open for a minimum time before putting it back. Reading a line
from an open book is quick, but two readers at desks in the same group of desks
must take turns at a slower pace than readers in different groups. The
librarian keeps, for every desk and for every group of desks, a notebook of
promises: "no new book at this desk before tick 142", "no reading at desk 3
before tick 63". A command is allowed only when every promise on its path has
expired, and issuing a command writes new promises.

Technical version: the device is a tree, channel > pseudo channel > SID >
bank group > bank. Every node keeps `ready[cmd]`, the earliest tick each
command may issue at that scope, plus a short history of when each command
last issued (for rolling windows like tFAW). `check_timing` walks from the
channel down to the addressed bank and fails if any node says "not yet".
`issue` walks the same path and raises `ready` for the commands each rule
constrains, descends into the addressed child (or all children for all-bank
commands), and touches sibling nodes for rules marked `sibling` (tCCD_R
across SIDs). The bank also keeps the state machine: closed, or open to row
R. From that state the device answers "what must happen next for this
request": ACT if closed, the read itself if open to the right row, PREpb if
open to the wrong row. That is the miss/hit/conflict distinction from the
quiz.

## 2. The rule table is Ramulator's, by construction

Plain version: instead of copying 51 rules out of a book by hand, we asked
Ramulator to print the exact table it uses at run time and loaded that.

Technical version: Ramulator's HBM3 class is a Python DSL. At run time it
resolves every `TimingConstraint` to integer entries (level, preceding
commands, following commands, latency in half-CK ticks, window, sibling,
shared-window group) and adjusts latencies for command lengths: an ACT
occupies the row bus for 1.5 CK, so a rule measured from the end of ACT gets
+2 ticks, and a 1 CK following command gets -1. `scripts/export_dram_spec.py`
re-derives that expansion with a name attached to each entry, checks it
entry by entry against Ramulator's own `to_config()` output, and writes
`configs/hbm3/hbm3_16gb_8hi_6400.spec`: 60 entries (51 rules, some split by
adjusted latency, plus 2 bus-occupancy rules). The C++ core loads this file.
Vendor overrides flow through the same script.

Why this and not hand-coded `if` statements per rule: two implementations of
51 rules invite drift, and a memory architect's real workflow is also a
timing table plus an engine. The "one constraint at a time" discipline lives
in the tests (section 4) and in the ablation switch (`--disable nFAW`).

## 3. The librarian: controller rules that matter

The controller mirrors Ramulator's HBM34 controller so that Phase 4 could
diff timing rather than scheduling:

- Per channel: a 32-entry read queue, a 32-entry write queue, an active
  buffer for requests whose ACT has issued, a priority queue for refresh, and
  a pending list of reads waiting their read latency (44 ticks).
- First-ready, first-come-first-served: among requests whose next command is
  legal right now, the oldest wins; a request whose row is already open
  beats an older request that still needs an ACT.
- Writes are drained in "write mode", entered when the write queue is more
  than 80% full or no reads are waiting, left when it falls under 20%.
- HBM3's split command bus: a column command (RD/WR) only on a rising edge
  (odd tick); one row command per tick; on a falling edge only a PREpb or
  PREab may issue, and not one that pairs badly with the row command just
  issued on the rising edge.
- All-bank refresh: every tREFI (3.9 us) a REFab request per pseudo channel
  enters the priority queue. Its prerequisite is PREab if any bank is open.
  While a priority request waits, no new read or write is scheduled; the
  active buffer still drains.

Two consequences the tests pin down: a read whose tRCD expires on an even
tick issues one tick later (ACT at 1, RD at 65, not 64), and a precharge may
use the falling edge the column bus cannot.

## 4. Tests: one per rule, with hand-derived ticks

`cpp/tests/test_timing.cpp` opens banks and asks "when is this command
first legal?":

| Rule | Scenario | Expected ticks | Also seen in Phase 0's Ramulator probe |
|---|---|---:|---|
| tRCD (read) | ACT then RD, same bank | 63 | yes |
| tRCD (write) | ACT then WR | 31 | |
| tRAS | ACT, RD, then PREpb | 92 | yes |
| tRP, tRC | PREpb then ACT; ACT to ACT | 50, 142 | yes |
| tCCD_L | RD then RD, same bank group | 8 | yes |
| tCCD_S | RD then RD, other bank group | 4 | yes |
| tCCD_R | RD then RD, other SID | 6 | yes |
| tRRD_S / tRRD_L | ACT then ACT, other / same group | 8 / 10 | |
| tFAW | four ACTs, then a fifth | 48 | yes |
| tWTR_S / tWTR_L | WR then RD, other / same group | 38 / 44 | |
| tRTW | RD then WR | 34 | |
| tPPD | PREpb then PREpb | 4 | |
| row bus | ACT then any row command | 3 | |
| column bus | RD then RD in the other pseudo channel | 2 | |
| tRFC | REFab then ACT | 1118 | yes |

`cpp/tests/test_controller.cpp` runs whole timelines: a single read (ACT 1,
RD 65, departs 109), four hits spaced by tCCD_L, a conflict (PREpb at 93, ACT
143, RD 207), two bank groups overlapping their ACTs, a write followed by a
hit that waits tWTR_L, a refresh that closes rows (PREab 1400, REFab 1453 and
1455, next ACT 2571), and sixteen independent channels.

## 5. Validation: identical to Ramulator

`scripts/tokenwall_vs_ramulator.py` sends the first 1.8 M reads of Llama 3
8B layer 0 through both simulators with the same mapping, 16 channels, the
same frontend rate and the same refresh setting.

| Case | Ticks (both) | Served | Hits | Misses | Conflicts | GB/s (both) | Difference |
|---|---:|---:|---:|---:|---:|---:|---|
| `ramulator`, no refresh | 567,661 | 1,799,488 | 1,743,200 | 1,024 | 55,280 | 324.6 | none |
| `ramulator`, all-bank refresh | 534,313 | 1,799,488 | 1,742,063 | 43,809 | 13,632 | 344.9 | none |
| `bank_low`, no refresh | 239,251 | 1,799,488 | 1,742,800 | 1,024 | 55,664 | 770.2 | none |
| `bank_low`, all-bank refresh | 267,947 | 1,799,488 | 1,722,704 | 21,632 | 55,152 | 687.7 | none |

Average read latency also matched to three decimals. Tokenwall took 1.3 to
3.0 s per case against Ramulator's 2.3 to 4.5 s.

What this does and does not mean. The core deliberately reuses Ramulator's
resolved rule table and mirrors its algorithms, so agreement shows the
implementation is faithful, not that Ramulator is right about HBM3 silicon.
The timings are still Ramulator's preset with its 13 estimates. The value is
a core we fully control, that is faster, and that reports what Ramulator
does not (next section).

## 6. Where the bandwidth goes

Every rising edge is a column-command slot for each pseudo channel. Tokenwall
labels each slot: `data` if a burst is on that pseudo channel's wires,
otherwise the command and rule blocking the oldest request to that pseudo
channel, or `empty`, or `arbitration` when it was ready but another request
took the bus. Same traffic as above, 16 channels, reads only:

| Mapping, refresh | data | tCCD_L (RD) | tRCD (RD) | tRP (ACT) | column bus | tRFC (ACT) | tCCD_R |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ramulator`, none | 39.6% | 34.7% | 19.5% | 2.2% | 4.0% | | |
| `ramulator`, all-bank | 42.1% | 32.6% | 6.4% | 0.6% | 8.7% | 8.5% | |
| `bank_low`, none | 94.0% | | 3.2% | | 1.4% | | 1.4% |
| `bank_low`, all-bank | 84.0% | | 4.0% | | 1.2% | 8.5% | 1.2% |

Plain reading: with Ramulator's default mapping the wires carry data two
slots in five, and the single largest reason is tCCD_L, the same-bank-group
spacing rule, exactly the Phase 0 prediction. With `bank_low` and no refresh
the wires carry data 94% of the time; the remainder is row opens (tRCD).
All-bank refresh costs 8.5 points, which is tRFC / tREFI = 350 / 3900 = 9%
less the slots that were idle anyway.

The surprise: under the `ramulator` mapping, refresh made the run 5.9%
*shorter*. Its PREab closes all rows at once, so the next visit to each bank
is a miss (one ACT) instead of a conflict (PREpb, wait tRP, ACT); conflicts
fell from 55,280 to 13,632 and the tRCD/tRP share from 21.6% to 7.0%. For
streaming traffic under an open-row policy, a batched precharge can be worth
more than tRFC costs. This is analysis built on the attribution numbers;
Phase 5 can test it with the refresh-policy sweep.

## 7. Limits to say out loud

- Reads only in the Ramulator comparison; Tokenwall itself handles writes
  and write mode, and the first full-step runs include them (RESULTS).
- Per-bank refresh and read/write-with-auto-precharge commands are in the
  rule table but not issued by the controller yet.
- Attribution names the rule blocking the *oldest* request to a pseudo
  channel; a younger request may have been blocked by something else.
- The frontend issues at most one request per channel per tick and retries
  a full queue head-first, like Ramulator's trace frontend; a GPU's memory
  pipeline is deeper and reorders more.

## 8. Reproduce

```bash
cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure
source .venv/bin/activate
python scripts/export_dram_spec.py
python scripts/tokenwall_vs_ramulator.py --requests 1800000
./build/cpp/tokenwall sim --segs traces/llama3_8b_tp1_b1_s4096.segs --policy bank_low --stacks 1 --refresh allbank --drain
./build/cpp/tokenwall sim --segs traces/llama3_8b_tp1_b1_s4096.segs --policy bank_low --stacks 1 --refresh none --disable nFAW --max-requests 2000000
```
