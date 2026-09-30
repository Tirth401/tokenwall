"""Shared helpers for the Ramulator 2.1 scripts in this directory."""
from __future__ import annotations

import pathlib
import time

import ramulator

ORG_PRESET = "HBM3_16Gb_8hi"
TIMING_PRESET = "HBM3_6400Mbps"


def hbm3_facts() -> dict:
    """Tick length, access size and channel peak from the preset (not typed by hand)."""
    probe = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET)
    org, t = probe.resolve()
    tck_ps = t["tCK_ps"]
    tx_bytes = type(probe).data_payload_bytes
    peak_channel_gbps = tx_bytes / (t["nBL"] * tck_ps * 1e-12) / 1e9 * org["pseudochannel"]
    return {"tick_ps": tck_ps / type(probe).tick_multiplier, "tx_bytes": tx_bytes,
            "peak_channel_GBps": peak_channel_gbps, "tCK_ps": tck_ps}


def make_refresh(kind: str):
    return {
        "allbank": ramulator.refresh_manager.AllBank,
        "perbank": ramulator.refresh_manager.HBM34PerBankRefresh,
        "none": ramulator.refresh_manager.NoRefresh,
    }[kind]()


def run_trace(trace_path: pathlib.Path, refresh: str = "allbank", verbose: bool = False, **dram_overrides):
    """One HBM3 channel: HBM34 controller, FRFCFS, open-row policy, RoBaRaCoCh mapping."""
    dram = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET, verbose=verbose, **dram_overrides)
    ctrl = ramulator.controller.HBM34(
        dram=dram,
        scheduler=ramulator.scheduler.FRFCFS(),
        refresh_manager=make_refresh(refresh),
        row_policy=ramulator.row_policy.Open(),
        addr_mapper=ramulator.addr_mapper.RoBaRaCoCh(),
    )
    mem = ramulator.memory_system.GenericDRAM(
        clock_ratio=1, controllers=[ctrl], channel_mapper=ramulator.channel_mapper.CacheLineInterleave())
    frontend = ramulator.frontend.LoadStoreTrace(clock_ratio=1, path=str(trace_path))
    sim = ramulator.Simulation(frontend, mem)
    t0 = time.time()
    sim.run()
    return sim.stats, sim.stats_yaml, time.time() - t0


def summarize(stats: dict, facts: dict, wall: float) -> dict:
    """Bandwidth over SERVED requests using the true 312.5 ps tick (see RESULTS.md quirks)."""
    c = stats["memory_system"]["controller"]
    tick_ps, tx = facts["tick_ps"], facts["tx_bytes"]
    accepted = c["num_read_reqs"] + c["num_write_reqs"]
    served = c["num_read_reqs_served"] + c["num_write_reqs_served"]
    seconds = c["cycles"] * tick_ps * 1e-12
    achieved = served * tx / seconds / 1e9
    hits, misses, conflicts = c["row_hits"], c["row_misses"], c["row_conflicts"]
    classified = hits + misses + conflicts
    return {
        "requests_accepted": accepted,
        "requests_served": served,
        "in_flight_at_end": accepted - served,
        "controller_ticks": c["cycles"],
        "sim_time_us": seconds * 1e6,
        "achieved_GBps": achieved,
        "pct_of_channel_peak": 100 * achieved / facts["peak_channel_GBps"],
        "row_hits": hits,
        "row_misses": misses,
        "row_conflicts": conflicts,
        "row_hit_rate_pct": (100 * hits / classified) if classified else None,
        "avg_read_latency_ticks": c["avg_read_latency"],
        "avg_read_latency_ns": c["avg_read_latency"] * tick_ps / 1000,
        "ramulator_total_throughput_MBps": c.get("total_throughput_MBps"),  # uses a 312 ps tick, ~0.16% high
        "wall_seconds": wall,
    }


def print_summary(title: str, row: dict) -> None:
    print(f"\n[{title}]")
    for k, v in row.items():
        print(f"  {k:32s} {v:.3f}" if isinstance(v, float) else f"  {k:32s} {v}")
