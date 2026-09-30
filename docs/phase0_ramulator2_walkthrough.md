# Phase 0 walkthrough: where HBM3 timing lives in Ramulator 2.1

Written for study. Every number below was produced by Ramulator 2.1 at commit
`72427a1b` on 2026-09-30; the commands are in the last section. Terms are
defined the first time they appear.

## 0. Why this matters for Tokenwall

Our C++ timing core (Phase 3) must enforce the same rules with the same numbers
as Ramulator, or the Phase 4 validation means nothing. So Phase 0 does two
things: take the numbers from Ramulator's HBM3 definition rather than from
memory, and understand how Ramulator applies them.

## 1. The shape of Ramulator 2.1

- It is a C++ shared library (`libramulator.dylib`) with Python bindings. There
  is no standalone binary any more. A "config file" is a Python script that
  builds a few objects and calls `sim.run()`.
- Each DRAM standard is a Python class. HBM3 is
  `external/ramulator2/python/ramulator/dram/hbm3.py`. A code generator turns
  that class into C++ (`src/ramulator/dram/impl/HBM3.cpp`, stamped
  AUTO-GENERATED). The Python file is the source of truth; never edit the C++.
- Build: CMake fetches `yaml-cpp`, `fmt`, `nanobind`, compiles the library and
  two Python extension modules, then `pip install -e` makes `import ramulator`
  work. On Apple Clang 21 the build needs the two-hunk patch in
  `patches/ramulator2/` (a missing `template` keyword, and `fmt` 10.2.1 to
  11.2.0). Neither hunk touches simulation behaviour.

## 2. The configuration, block by block

This is `scripts/ramulator2_hbm3_smoke.py` reduced to its four blocks.

```python
frontend = ramulator.frontend.LoadStoreTrace(clock_ratio=1, path="trace.txt")
```
The **frontend** produces memory requests. `LoadStoreTrace` replays a text file
of `LD <addr>` / `ST <addr>` lines, one request per tick, with no gaps: a
firehose. That is what we want when measuring how much bandwidth the memory
can deliver. Two things to remember: it loads the whole file into RAM, and the
simulation stops when the *last request is sent*, not when it completes (32
requests were still queued at the end of our sequential run).

```python
dram = ramulator.dram.HBM3(org_preset="HBM3_16Gb_8hi", timing_preset="HBM3_6400Mbps")
```
The **DRAM device**. Two presets: an *organisation* (how many pseudo channels,
banks, rows, columns, and the capacity) and a *timing* speed bin (all the
nanosecond rules, expressed in clock cycles). Any preset value can be
overridden by keyword, e.g. `nFAW=30`. `verbose=True` prints the final resolved
timings, which is the printout captured in `results/phase0/smoke_allbank.log`.

```python
ctrl = ramulator.controller.HBM34(
    dram=dram,
    scheduler=ramulator.scheduler.FRFCFS(),
    refresh_manager=ramulator.refresh_manager.AllBank(),
    row_policy=ramulator.row_policy.Open(),
    addr_mapper=ramulator.addr_mapper.RoBaRaCoCh(),
)
```
The **memory controller**: the logic that turns requests into DRAM commands.
`HBM34` is the HBM3/HBM4 controller; it models HBM3's split command bus (row
commands like ACT and PRE travel on different wires than column commands like
RD and WR, and row commands take half or one-and-a-half clocks). Its parts:

- **Scheduler `FRFCFS`**: First-Ready, First-Come-First-Served. Among queued
  requests, prefer one whose next command can issue right now (typically a
  row hit); break ties by age. This is the standard baseline and the one our
  Phase 3 core starts with.
- **Refresh manager `AllBank`**: every tREFI (3.9 us) issue one all-bank
  refresh (REFab) to each pseudo channel, which blocks it for tRFC (350 ns).
  Alternatives: `HBM34PerBankRefresh` (refresh one bank at a time, REFpb, so
  other banks keep working) and `NoRefresh`. Phase 5 sweeps these.
- **Row policy `Open`**: after a read, leave the row open hoping the next
  request hits it. Alternative `ClosedCap` closes rows eagerly.
- **Address mapper `RoBaRaCoCh`**: which address bits pick which pseudo
  channel, bank group, bank, row and column. Section 6 has the exact bit
  layout. Phase 2 replaces this with our own policies.

Defaults you do not see: a 32-entry read queue and a 32-entry write queue.
The controller can only look 32 requests ahead, which matters in section 7.

```python
mem = ramulator.memory_system.GenericDRAM(clock_ratio=1, controllers=[ctrl],
                                          channel_mapper=ramulator.channel_mapper.CacheLineInterleave())
```
The **memory system** is a list of controllers. One controller is one HBM3
channel. A real HBM3 stack has 16 channels; to model a stack you pass 16
controllers and the channel mapper picks a channel from address bits (it
requires a power-of-two count).

## 3. HBM3 organisation, preset `HBM3_16Gb_8hi`

An HBM3 stack is 8 DRAM dies stacked on a logic die, connected by thousands of
vertical wires. The interface is split into 16 independent **channels** of 64
data bits each. Inside a channel, Ramulator's hierarchy is:

| Level | Count | What it is |
|---|---:|---|
| Channel | 1 per controller (16 per stack) | An independent 64-bit interface with its own command wires. |
| PseudoChannel | 2 | Each 64-bit channel is run as two 32-bit halves that share command wires but have separate data wires and independent bank state. Most channel-wide timing rules apply per pseudo channel. |
| Sid (stack ID) | 2 | Which of two dies serves this channel. Behaves like a rank: a second, independent set of banks behind the same wires. |
| BankGroup | 4 | Banks that share internal circuitry. Two column commands to the same group must be spaced further apart (tCCD_L) than to different groups (tCCD_S). |
| Bank | 4 per group | An independent memory array with exactly one **row buffer**: a set of sense amplifiers that holds one open row. 32 banks per pseudo channel, 64 per channel. |
| Row | 16384 per bank | One row is 1 KB per pseudo channel (256 columns x 32 bits). Opening a row (ACT) copies it into the row buffer; that is the slow step. |
| Column | 256 per row | A 32-bit word. One access moves a burst of 8 columns (BL8): 8 x 32 bits = **32 bytes**. So an open row serves 32 accesses. |

Capacity: 16 Gb per die, 8 dies, 8 Gb (1 GB) per channel, **16 GB per stack**.

The 32-byte access size is the first surprise of the project: an HBM3 access is
half a CPU cache line. That is exactly why GPU L2 caches use 32-byte sectors.
It is an open question for Phase 1 whether Tokenwall generates 32 B requests
natively or 64 B requests split in two.

## 4. Clock and peak bandwidth

The speed bin is 6400 Mb/s per data pin. The clock (CK) is 1.6 GHz, so one
cycle is tCK = 0.625 ns, and data moves at 4 beats per CK. A 32-byte access is
8 beats = 2 CK = 1.25 ns. Peak bandwidth is therefore:

| Scope | Peak |
|---|---:|
| per pseudo channel: 32 B / 1.25 ns | 25.6 GB/s |
| per channel (2 pseudo channels) | 51.2 GB/s |
| per 16-channel stack | 819.2 GB/s |

The last figure is the well-known HBM3 headline number, so the arithmetic
checks out against the outside world.

Ramulator simulates HBM3 in **half-CK ticks** (312.5 ps) because on the HBM3
command bus an ACT occupies 1.5 CK and a PRE or REF 0.5 CK. All Ramulator
"cycles" for HBM3 are ticks; divide by 2 for CK. Internally it stores the tick
as the integer 312 ps, so its own ns and MB/s statistics read 0.16% high; we
convert ticks with 312.5 ps ourselves.

## 5. The timing parameters

All values from `configs/hbm3/hbm3_16gb_8hi_6400.yaml`. "Source" says where the
number comes from: a speed-bin value, an estimate by Ramulator's authors, or a
formula in `hbm3.py`.

| Name | CK | ns | Plain meaning | Source |
|---|---:|---:|---|---|
| tRCDRD | 31 | 19.4 | ACT to first RD: time to open a row before reading it | estimate |
| tRCDWR | 15 | 9.4 | ACT to first WR (writes can start earlier) | estimate |
| tRP | 26 | 16.3 | PRE to ACT: time to close a row before opening another | estimate |
| tRAS | 45 | 28.1 | ACT to PRE: a row must stay open at least this long | estimate |
| tRC | 71 | 44.4 | ACT to ACT in the same bank = tRAS + tRP: one full row cycle | derived |
| tCL | 20 | 12.5 | RD to data on the wires | estimate |
| tCWL | 10 | 6.3 | WR to data on the wires | estimate |
| nBL | 2 | 1.25 | Burst length: how long one 32 B transfer occupies the data wires | speed bin |
| tCCD_S | 2 | 1.25 | RD to RD, different bank groups: equals the burst, so the bus stays 100% busy | speed bin |
| tCCD_L | 4 | 2.5 | RD to RD, same bank group: twice the burst, so the bus is 50% busy | derived (max(4 CK, 2.5 ns)) |
| tCCD_R | 3 | 1.9 | RD to RD, other SID (other die) | estimate |
| tRRD_S | 4 | 2.5 | ACT to ACT, different bank groups | estimate |
| tRRD_L | 5 | 3.1 | ACT to ACT, same bank group | estimate |
| tFAW | 24 | 15.0 | Four-activate window: at most 4 ACTs per pseudo channel in any 15 ns (power limit) | estimate |
| tRTP | 9 | 5.6 | RD to PRE | estimate |
| tWR | 33 | 20.6 | write recovery: last write data to PRE | estimate |
| tWTR_S / tWTR_L | 7 / 10 | 4.4 / 6.3 | write to read turnaround, other / same bank group | estimate |
| tRTW | 17 | 10.6 | read to write turnaround | derived (JESD238 formula) |
| tPPD | 2 | 1.25 | PRE to PRE | speed bin |
| tRFC | 560 | 350 | all-bank refresh duration: the pseudo channel is dead this long | derived (JESD238 table) |
| tRFCpb | 320 | 200 | per-bank refresh duration | derived (JESD238 table) |
| tREFI | 6240 | 3900 | all-bank refresh interval | derived (3.9 us) |
| tREFIpb | 195 | 122 | per-bank refresh interval = tREFI / 32 banks | derived |
| tRREFD | 13 | 8.1 | REFpb to REFpb | derived (max(3 CK, 8 ns)) |

Refresh duty with all-bank refresh: tRFC / tREFI = 350 / 3900 = **9.0%** of
the time each pseudo channel cannot do anything.

**Honesty note.** Thirteen of the speed-bin values (tCL, tCWL, tFAW, tRAS,
tRCDRD, tRCDWR, tRP, tRRD_L, tRRD_S, tRTP, tWR, tWTR_L, tWTR_S) sit inside a
block that `hbm3.py` labels `=== Ramulator Guesstimate ===`. JEDEC's HBM3
standard (JESD238) leaves those to vendor datasheets, so Ramulator's authors
estimated them. Everything Tokenwall reports is "HBM3 per Ramulator 2.1's
`HBM3_6400Mbps` preset", never "HBM3 per JEDEC". A Micron architect will have
the real datasheet numbers; our framework accepts them as overrides.

## 6. How the rules are written and enforced

Every rule is one line in `hbm3.py`:

```python
TimingConstraint(level="Bank", preceding=["ACT"], following=["RD", "RDA"], latency="nRCDRD")
```

Read it as: *at the scope of one Bank, after an ACT, an RD (or RDA) to that
bank may not issue for nRCDRD cycles.* The `level` is the scope. Rules at
`PseudoChannel` level (bus occupancy, turnarounds, tFAW, refresh) affect every
bank in that pseudo channel; `Sid` rules cover a die; `BankGroup` rules cover
a group; `Bank` rules cover one bank. HBM3 has 51 such lines; they are all
listed at the bottom of `configs/hbm3/hbm3_16gb_8hi_6400.yaml` with their
resolved cycle counts. Our Phase 3 core needs an equivalent of each one.

Enforcement, as Ramulator does it: every node in the hierarchy (pseudo
channel, SID, bank group, bank) keeps, per command, *the earliest tick that
command may next issue here*. To test a command, walk from the channel down
to the target bank; if any node says "not yet", the command waits. When a
command issues, walk the same path and push those earliest-tick values out
according to the rules. Two refinements: `sibling=True` rules also update
peer nodes (tCCD_R across SIDs), and `window=4` rules keep the last four
issue times (tFAW: the fifth ACT waits on the first).

There is one subtlety our core must copy. JEDEC measures a timing from the
*end* of the preceding command, but Ramulator time-stamps commands at their
*first* tick. Since an ACT is 3 ticks long and an RD 2, Ramulator adds the
difference: measured ACT to RD is 63 ticks, not the nominal 62; ACT to PREpb
is 92, not 90; PREpb to ACT is 50, not 52. Section 8 shows these measured.

Two views of the same bank live side by side: the timing view above, and a
state machine that answers "what command does this request need next?". For a
read: bank closed, needs ACT; bank open to the right row, needs RD (a **row
hit**); bank open to the wrong row, needs PREpb first (a **row conflict**).
Ramulator never expands a request into a fixed script; it re-asks that
question every tick.

Address bits for `RoBaRaCoCh` on this preset, in units of 32 B lines: bits
[4:0] column (32 lines = one 1 KB row), bit 5 pseudo channel, bit 6 SID, bits
[8:7] bank group, bits [10:9] bank, bits [24:11] row. So 32 consecutive lines
sit in one bank of one pseudo channel; the next 32 go to the other pseudo
channel; and the bank group changes only every 4 KB.

## 7. Reading the smoke run

20 000 32-byte reads per pattern, one channel (2 pseudo channels), all-bank
refresh, FRFCFS, open-row policy. Full output: `results/phase0/smoke_allbank.log`.

| Pattern | Achieved | % of 51.2 GB/s | Row hit rate | Avg read latency |
|---|---:|---:|---:|---:|
| sequential | 21.69 GB/s | 42.4% | 96.8% | 61.5 ns |
| random over 1 GiB | 15.00 GB/s | 29.3% | 0.0% | 101.9 ns |

Why does a 97%-hit sequential stream get only 42%? With this address map all
32 accesses of a row land in one bank, so each RD must wait tCCD_L = 2.5 ns for
the next RD to the same bank group while the data burst only takes 1.25 ns:
the data wires idle half the time. That caps a pseudo channel at 12.8 GB/s and
the channel at 25.6 GB/s = 50% of peak. The 9% refresh duty and about 640 row
switches explain most of the remaining gap to 42%. This is an analytical
reading of the rule table, not a measurement; Phase 2 tests it directly by
moving the bank-group bits down next to the column bits, which should let a
sequential stream approach 100% of the data-bus limit.

Why does random get 29% with zero hits? Every access is a full row cycle
(ACT, RD, PRE). Spread over 64 banks that would allow a lot of parallelism,
but tFAW limits each pseudo channel to 4 ACTs per 15 ns, hence 4 x 32 B / 15 ns
= 8.53 GB/s per pseudo channel and 17.07 GB/s per channel (33.3% of peak).
Measured 15.0 GB/s is 88% of that bound; taking 9% off for refresh predicts
15.5 GB/s. Again analytical, again something Phase 3's stall breakdown will
measure instead of infer.

The lesson for the whole project: **in DRAM, "sequential" is not enough; the
address mapping must spread consecutive accesses across bank groups and pseudo
channels, or timing rules leave the data wires idle.** That is why address
mapping is a phase of its own.

## 8. Asking the device model directly

`scripts/ramulator2_probe_demo.py` uses Ramulator's device-under-test harness
to ask "when is this command legal?" Output (`results/phase0/probe_demo.txt`):

```
A. RD on a closed bank at clk 0 -> preq=ACT, timing_OK=True, ready=False
B. ACT -> first legal RD    (tRCDRD)     63 ticks = 31.5 CK = 19.688 ns
   ACT -> first legal PREpb (tRAS)       92 ticks = 46.0 CK = 28.750 ns
   PREpb -> next ACT        (tRP)        50 ticks = 25.0 CK = 15.625 ns
   ACT -> ACT same bank     (tRC)       142 ticks = 71.0 CK = 44.375 ns
C. RD -> RD same bank group (tCCD_L)      8 ticks =  4.0 CK =  2.500 ns
   RD -> RD other bank group (tCCD_S)     4 ticks =  2.0 CK =  1.250 ns
   RD -> RD other SID       (tCCD_R)      6 ticks =  3.0 CK =  1.875 ns
D. four ACTs at ticks [0, 8, 16, 24]; fifth ACT at +48 ticks = 15.0 ns (tFAW)
E. REFab -> next ACT        (tRFC)     1118 ticks = 559.0 CK = 349.375 ns
```

Line A is the state-versus-timing distinction: timing is fine at tick 0, but
the bank is closed, so the prerequisite is ACT. Lines B show the +1/-2 tick
command-length adjustments from section 6. Lines C are the whole Phase 2
story in three numbers. This harness is also how Phase 3 unit tests will be
cross-checked one constraint at a time.

## 9. Reproduce

```bash
source .venv/bin/activate
python scripts/extract_hbm3_params.py
python scripts/ramulator2_hbm3_smoke.py --requests 20000 --refresh allbank
python scripts/ramulator2_probe_demo.py
cd external/ramulator2 && python -m pytest tests/device_timings/test_hbm3.py tests/controller_scheduling/HBMController -q
```
