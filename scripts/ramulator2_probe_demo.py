#!/usr/bin/env python
"""Ask Ramulator 2.1's HBM3 device model directly when commands become legal.

Uses Ramulator's own device-under-test harness (tests/device_timings), so every
number printed is produced by Ramulator, not typed by us. Ticks are half-CK
because Ramulator models HBM3 row commands at half-cycle granularity.

Usage (repo root, venv active):
    python scripts/ramulator2_probe_demo.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "external" / "ramulator2"))  # for tests.device_timings.harness

import ramulator  # noqa: E402
from tests.device_timings.harness import DeviceUnderTest  # noqa: E402

dram = ramulator.dram.HBM3(org_preset="HBM3_16Gb_8hi", timing_preset="HBM3_6400Mbps")
dut = DeviceUnderTest(dram)
T = dut.timings  # name -> ticks
mult = dut.tick_multiplier
ns_per_tick = dut.time_unit_ns


def show(label: str, ticks: int) -> None:
    print(f"  {label:60s} {ticks:5d} ticks = {ticks / mult:5.1f} CK = {ticks * ns_per_tick:8.3f} ns")


def vec(pc=0, sid=0, bg=0, bank=0, row=0):
    return dut.addr_vec(PseudoChannel=pc, Sid=sid, BankGroup=bg, Bank=bank, Row=row, Column=0)


print(f"tick = {ns_per_tick} ns ({mult} ticks per CK); device reports {len(T)} timing values\n")

print("A. State first, timing second")
p = dut.probe("RD", vec(), clk=0)
print(f"  RD on a closed bank at clk 0 -> preq={p.preq}, timing_OK={p.timing_OK}, ready={p.ready}")

print("\nB. One bank, one row: the activate -> read -> precharge -> activate cycle (pseudo channel 0)")
dut.issue("ACT", vec(), clk=0)
t_rd = dut.get_first_ready_clk("RD", vec())
show("ACT -> first legal RD               (tRCDRD)", t_rd)
dut.issue("RD", vec(), clk=t_rd)
t_pre = dut.get_first_ready_clk("PREpb", vec(), start=t_rd)
show("ACT -> first legal PREpb            (tRAS, since RD+tRTP is shorter)", t_pre)
dut.issue("PREpb", vec(), clk=t_pre)
t_act2 = dut.get_first_ready_clk("ACT", vec(row=1), start=t_pre)
show("PREpb -> next ACT in same bank      (tRP)", t_act2 - t_pre)
show("ACT -> ACT same bank, total         (tRC = tRAS + tRP)", t_act2)

print("\nC. Back-to-back reads: how close can two RDs be? (pseudo channel 1, all banks pre-opened)")
opened = [vec(pc=1, bg=0, bank=0), vec(pc=1, bg=0, bank=1), vec(pc=1, bg=1, bank=0), vec(pc=1, sid=1, bg=0, bank=0)]
clk = 0
for a in opened:
    clk = dut.get_first_ready_clk("ACT", a, start=clk)
    dut.issue("ACT", a, clk=clk)
base = 400  # far enough that every tRCD has expired
dut.issue("RD", opened[0], clk=base)
show("RD -> RD, same bank                 (tCCD_L, bank-group local)", dut.get_first_ready_clk("RD", opened[0], start=base) - base)
show("RD -> RD, other bank in same group  (tCCD_L)", dut.get_first_ready_clk("RD", opened[1], start=base) - base)
show("RD -> RD, other bank group          (tCCD_S)", dut.get_first_ready_clk("RD", opened[2], start=base) - base)
show("RD -> RD, other SID (other die)     (tCCD_R)", dut.get_first_ready_clk("RD", opened[3], start=base) - base)
show("data burst length itself            (nBL)", T["nBL"])

print("\nD. The four-activate window (fresh device, pseudo channel 0)")
dut2 = DeviceUnderTest(ramulator.dram.HBM3(org_preset="HBM3_16Gb_8hi", timing_preset="HBM3_6400Mbps"))
acts = [dut2.addr_vec(PseudoChannel=0, Sid=0, BankGroup=bg, Bank=0, Row=0, Column=0) for bg in range(4)]
fifth = dut2.addr_vec(PseudoChannel=0, Sid=1, BankGroup=0, Bank=0, Row=0, Column=0)
clk, times = 0, []
for a in acts:
    clk = dut2.get_first_ready_clk("ACT", a, start=clk)
    dut2.issue("ACT", a, clk=clk)
    times.append(clk)
print(f"  four ACTs to different bank groups issued at ticks {times} (spacing = tRRD_S)")
t5 = dut2.get_first_ready_clk("ACT", fifth, start=clk)
show("first ACT -> fifth ACT              (tFAW window of 4)", t5 - times[0])

print("\nE. Refresh: how long an all-bank refresh blocks a pseudo channel (fresh device)")
dut3 = DeviceUnderTest(ramulator.dram.HBM3(org_preset="HBM3_16Gb_8hi", timing_preset="HBM3_6400Mbps"))
refab = dut3.addr_vec(PseudoChannel=0, Sid=dut3.ALL, BankGroup=dut3.ALL, Bank=dut3.ALL, Row=dut3.ALL, Column=dut3.ALL)
dut3.issue("REFab", refab, clk=0)
show("REFab -> next ACT                   (tRFC)", dut3.get_first_ready_clk("ACT", dut3.addr_vec(PseudoChannel=0, Sid=0, BankGroup=0, Bank=0, Row=0, Column=0)))
show("refresh interval                    (tREFI)", T["nREFI"])
print(f"  refresh duty: tRFC / tREFI = {T['nRFC'] / T['nREFI'] * 100:.1f}% of time a pseudo channel is unavailable")
