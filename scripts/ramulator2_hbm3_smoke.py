#!/usr/bin/env python
"""Phase 0 smoke run: Ramulator 2.1's HBM3 preset on two synthetic read traces.

Purpose: prove the toolchain works end to end and give a first feel for how
much row locality matters. The traffic is synthetic (sequential and random
reads), so these bandwidth figures are toolchain checks, not project results.

Usage (repo root, venv active):
    python scripts/ramulator2_hbm3_smoke.py [--requests 20000] [--refresh allbank|perbank|none]
Writes results/phase0/smoke_*.stats.yaml and results/phase0/smoke_summary_<refresh>.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import random
import sys
import time

import ramulator

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "phase0"
ORG_PRESET = "HBM3_16Gb_8hi"
TIMING_PRESET = "HBM3_6400Mbps"


def write_trace(path: pathlib.Path, pattern: str, n: int, tx_bytes: int, seed: int = 1) -> None:
    rng = random.Random(seed)
    span_lines = (1 << 30) // tx_bytes  # random reads land anywhere in 1 GiB
    with open(path, "w") as f:
        for i in range(n):
            addr = i * tx_bytes if pattern == "sequential" else rng.randrange(span_lines) * tx_bytes
            f.write(f"LD {addr}\n")


def make_refresh(kind: str):
    return {
        "allbank": ramulator.refresh_manager.AllBank,
        "perbank": ramulator.refresh_manager.HBM34PerBankRefresh,
        "none": ramulator.refresh_manager.NoRefresh,
    }[kind]()


def run_one(trace_path: pathlib.Path, refresh: str, verbose: bool):
    dram = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET, verbose=verbose)
    ctrl = ramulator.controller.HBM34(
        dram=dram,
        scheduler=ramulator.scheduler.FRFCFS(),
        refresh_manager=make_refresh(refresh),
        row_policy=ramulator.row_policy.Open(),
        addr_mapper=ramulator.addr_mapper.RoBaRaCoCh(),
    )
    mem = ramulator.memory_system.GenericDRAM(
        clock_ratio=1,
        controllers=[ctrl],
        channel_mapper=ramulator.channel_mapper.CacheLineInterleave(),
    )
    frontend = ramulator.frontend.LoadStoreTrace(clock_ratio=1, path=str(trace_path))
    sim = ramulator.Simulation(frontend, mem)
    t0 = time.time()
    sim.run()
    wall = time.time() - t0
    return sim.stats, sim.stats_yaml, wall


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=20000)
    ap.add_argument("--refresh", default="allbank", choices=["allbank", "perbank", "none"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    probe = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET)
    org, t = probe.resolve()
    tck_ps = t["tCK_ps"]
    tick_ps = tck_ps / type(probe).tick_multiplier
    tx_bytes = type(probe).data_payload_bytes
    peak_channel_gbps = tx_bytes / (t["nBL"] * tck_ps * 1e-12) / 1e9 * org["pseudochannel"]

    summary = {
        "date": datetime.date.today().isoformat(),
        "command": "python " + " ".join(sys.argv),
        "org_preset": ORG_PRESET,
        "timing_preset": TIMING_PRESET,
        "controller": "HBM34 + FRFCFS + Open row policy + RoBaRaCoCh",
        "refresh": args.refresh,
        "requests_per_run": args.requests,
        "access_bytes": tx_bytes,
        "tick_ps": tick_ps,
        "peak_channel_GBps": peak_channel_gbps,
        "runs": {},
    }

    first = True
    for pattern in ("sequential", "random"):
        trace = OUT / f"smoke_{pattern}.trace"
        write_trace(trace, pattern, args.requests, tx_bytes)
        stats, stats_yaml, wall = run_one(trace, args.refresh, verbose=first)
        first = False
        c = stats["memory_system"]["controller"]
        cycles = c["cycles"]
        accepted = c["num_read_reqs"] + c["num_write_reqs"]
        # Ramulator stops when the frontend has SENT its last request, so the
        # requests still queued at that moment were never served. Count only
        # served requests as bytes moved.
        served = c["num_read_reqs_served"] + c["num_write_reqs_served"]
        bytes_moved = served * tx_bytes
        seconds = cycles * tick_ps * 1e-12  # true tick = tCK / 2 = 312.5 ps
        achieved = bytes_moved / seconds / 1e9
        hits, misses, conflicts = c["row_hits"], c["row_misses"], c["row_conflicts"]
        classified = hits + misses + conflicts
        row = {
            "requests_accepted": accepted,
            "requests_served": served,
            "in_flight_at_end": accepted - served,
            "controller_ticks": cycles,
            "sim_time_us": seconds * 1e6,
            "achieved_GBps": achieved,
            "pct_of_channel_peak": 100 * achieved / peak_channel_gbps,
            "row_hits": hits,
            "row_misses": misses,
            "row_conflicts": conflicts,
            "row_hit_rate_pct": (100 * hits / classified) if classified else None,
            "avg_read_latency_ticks": c["avg_read_latency"],
            "avg_read_latency_ns": c["avg_read_latency"] * tick_ps / 1000,
            # Ramulator's own stat uses an integer tick of 312 ps (625 // 2), so it
            # reads ~0.16% high relative to the true 312.5 ps tick.
            "ramulator_total_throughput_MBps": c.get("total_throughput_MBps"),
            "wall_seconds": wall,
        }
        summary["runs"][pattern] = row
        (OUT / f"smoke_{pattern}_{args.refresh}.stats.yaml").write_text(stats_yaml)
        print(f"\n[{pattern} reads, refresh={args.refresh}]")
        for k, v in row.items():
            print(f"  {k:32s} {v:.3f}" if isinstance(v, float) else f"  {k:32s} {v}")

    out = OUT / f"smoke_summary_{args.refresh}.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nchannel peak {peak_channel_gbps:.1f} GB/s; wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
