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


def run_trace(trace_path: pathlib.Path, refresh: str = "allbank", verbose: bool = False, channels: int = 1,
              premapped: bool = False, interleave_bits: int = 0, frontend_ratio: int | None = None,
              **dram_overrides):
    """HBM3 channels with HBM34 controllers, FRFCFS, open-row policy.

    premapped=False: LoadStoreTrace (LD/ST <addr>), Ramulator maps with
        CacheLineInterleave(interleave_bits) + RoBaRaCoCh.
    premapped=True: ReadWriteTrace (R/W ch,pc,sid,bg,bank,row,col) with pass-through
        mappers; the trace must be read-only (Ramulator keys write coalescing on the
        flat address, which this frontend leaves unset).
    frontend_ratio: frontend ticks per memory tick, i.e. the maximum requests issued per
        tick; defaults to `channels` (each channel's peak is half a request per tick).
    """
    dram = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET, verbose=verbose, **dram_overrides)
    ctrl = ramulator.controller.HBM34(
        dram=dram,
        scheduler=ramulator.scheduler.FRFCFS(),
        refresh_manager=make_refresh(refresh),
        row_policy=ramulator.row_policy.Open(),
        addr_mapper=(ramulator.addr_mapper.PassThroughAddrMapper() if premapped
                     else ramulator.addr_mapper.RoBaRaCoCh()),
    )
    mem = ramulator.memory_system.GenericDRAM(
        clock_ratio=1,
        controllers=[ctrl] * channels,
        channel_mapper=(ramulator.channel_mapper.PassThroughChannelMapper() if premapped
                        else ramulator.channel_mapper.CacheLineInterleave(interleave_bits=interleave_bits)),
    )
    ratio = frontend_ratio or channels
    fe_cls = ramulator.frontend.ReadWriteTrace if premapped else ramulator.frontend.LoadStoreTrace
    frontend = fe_cls(clock_ratio=ratio, path=str(trace_path))
    sim = ramulator.Simulation(frontend, mem)
    t0 = time.time()
    sim.run()
    return sim.stats, sim.stats_yaml, time.time() - t0


def summarize(stats: dict, facts: dict, wall: float) -> dict:
    """Bandwidth over SERVED requests using the true 312.5 ps tick (see RESULTS.md quirks).

    Works for one channel (controller stats is a dict) or many (a list of dicts):
    counts are summed, ticks are the maximum, latency is weighted by served reads.
    """
    c = stats["memory_system"]["controller"]
    ctrls = c if isinstance(c, list) else [c]
    channels = len(ctrls)
    tick_ps, tx = facts["tick_ps"], facts["tx_bytes"]

    def total(key):
        return sum(x[key] for x in ctrls)

    accepted = total("num_read_reqs") + total("num_write_reqs")
    served = total("num_read_reqs_served") + total("num_write_reqs_served")
    served_per_channel = [x["num_read_reqs_served"] + x["num_write_reqs_served"] for x in ctrls]
    cycles = max(x["cycles"] for x in ctrls)
    seconds = cycles * tick_ps * 1e-12
    achieved = served * tx / seconds / 1e9
    hits, misses, conflicts = total("row_hits"), total("row_misses"), total("row_conflicts")
    classified = hits + misses + conflicts
    reads_served = total("num_read_reqs_served") or 1
    lat = sum(x["avg_read_latency"] * x["num_read_reqs_served"] for x in ctrls) / reads_served
    peak = channels * facts["peak_channel_GBps"]
    return {
        "channels": channels,
        "requests_accepted": accepted,
        "requests_served": served,
        "in_flight_at_end": accepted - served,
        "served_per_channel_min_max": [min(served_per_channel), max(served_per_channel)],
        "controller_ticks": cycles,
        "sim_time_us": seconds * 1e6,
        "achieved_GBps": achieved,
        "peak_GBps": peak,
        "pct_of_peak": 100 * achieved / peak,
        "row_hits": hits,
        "row_misses": misses,
        "row_conflicts": conflicts,
        "row_hit_rate_pct": (100 * hits / classified) if classified else None,
        "avg_read_latency_ticks": lat,
        "avg_read_latency_ns": lat * tick_ps / 1000,
        "ramulator_total_throughput_MBps": sum((x.get("total_throughput_MBps") or 0) for x in ctrls),
        "wall_seconds": wall,
    }


def print_summary(title: str, row: dict) -> None:
    print(f"\n[{title}]")
    for k, v in row.items():
        print(f"  {k:32s} {v:.3f}" if isinstance(v, float) else f"  {k:32s} {v}")
