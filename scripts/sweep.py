#!/usr/bin/env python
"""Phase 5 sweeps: run Tokenwall (validated identical to Ramulator 2.1) over knobs, one layer per run.

Why one layer: Phase 4 showed the per-layer percentage of peak matches the full decode step
within 0.1 point (42.1 vs 42.0, 83.8 vs 83.7), because a step is 32 near-identical layers.
Every run is drained (every request served, writes included). Full-step runs anchor absolute
token times separately (scripts in RESULTS.md).

Usage (repo root, venv active, build/ present):
    python scripts/sweep.py --sweeps all --workers 6
    python scripts/sweep.py --sweeps batch,seq
Outputs results/phase5/sweep_<name>.json and results/phase5/sweeps.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from tokenwall.addrmap import POLICY_NAMES
from tokenwall.hbm_config import HBMConfig
from tokenwall.model_config import ModelConfig
from tokenwall.tracegen import RunConfig, generate

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "phase5"
TRACES = ROOT / "traces"
TW = ROOT / "build" / "cpp" / "tokenwall"
SPEC = ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.spec"
HBM = HBMConfig.from_yaml()
MODELS: dict[str, ModelConfig] = {}


def model(name: str, n_kv: int | None = None) -> ModelConfig:
    key = f"{name}:{n_kv}"
    if key not in MODELS:
        m = ModelConfig.from_yaml(ROOT / "configs" / "models" / f"{name}.yaml")
        MODELS[key] = m.with_overrides(num_key_value_heads=n_kv) if n_kv else m
    return MODELS[key]


def trace_for(tag: str, m: ModelConfig, **run_kw) -> tuple[pathlib.Path, dict]:
    """Generate (or reuse) a one-layer slice; returns the .segs path and the generator stats."""
    path = TRACES / f"sweep_{tag}.segs"
    meta = path.with_suffix(".meta.json")
    run = RunConfig(layers=(0, 1), include_embed=False, include_lm_head=False, **run_kw)
    trace, stats = generate(m, run, HBM)
    if not path.exists():
        trace.write(path)
    meta.write_text(json.dumps(stats, indent=1))
    return path, stats


def case(sweep: str, name: str, segs: pathlib.Path, gen: dict, policy: str, refresh: str, stacks: int,
         disable: str | None = None, nonblocking: bool = False, **extra) -> dict:
    return {"sweep": sweep, "name": name, "segs": str(segs), "policy": policy, "refresh": refresh, "stacks": stacks,
            "disable": disable, "nonblocking": nonblocking, "gen": gen, **extra}


def build_cases(which: set[str]) -> list[dict]:
    cases: list[dict] = []
    m8 = model("llama3_8b")
    if "mapping_refresh" in which:
        segs, gen = trace_for("8b_b1_s4096", m8, batch=1, seq_len=4096, stacks=1)
        for policy in POLICY_NAMES:
            for refresh in ("none", "allbank", "perbank"):
                cases.append(case("mapping_refresh", f"{policy}/{refresh}", segs, gen, policy, refresh, 1))
    if "batch" in which:
        for b in (1, 4, 16, 32):
            segs, gen = trace_for(f"8b_b{b}_s4096", m8, batch=b, seq_len=4096, stacks=2)
            for policy in ("ramulator", "bank_low"):
                cases.append(case("batch", f"{policy}/b{b}", segs, gen, policy, "allbank", 2, batch=b))
    if "seq" in which:
        for s in (512, 1024, 2048, 4096, 8192):
            segs, gen = trace_for(f"8b_b32_s{s}", m8, batch=32, seq_len=s, stacks=4)
            for policy in ("ramulator", "bank_low"):
                cases.append(case("seq", f"{policy}/s{s}", segs, gen, policy, "allbank", 4, seq=s))
    if "kv_layout" in which:
        for layout in ("head_major", "position_major"):
            for order in ("head_outer", "position_outer"):
                segs, gen = trace_for(f"8b_b32_s4096_{layout}_{order}", m8, batch=32, seq_len=4096, stacks=2,
                                      kv_layout=layout, kv_order=order)
                for policy in ("ramulator", "bank_low"):
                    cases.append(case("kv_layout", f"{policy}/{layout}/{order}", segs, gen, policy, "allbank", 2,
                                      layout=layout, order=order))
    if "gqa" in which:
        for kv in (8, 32):
            segs, gen = trace_for(f"8b_kv{kv}_b1_s4096", model("llama3_8b", kv), batch=1, seq_len=4096, stacks=2)
            for policy in ("ramulator", "bank_low"):
                cases.append(case("gqa", f"{policy}/kv{kv}", segs, gen, policy, "allbank", 2, kv_heads=kv))
    if "70b" in which:
        m70 = model("llama3_70b")
        for b in (1, 8):
            segs, gen = trace_for(f"70b_tp8_b{b}_s4096", m70, batch=b, seq_len=4096, tp=8, stacks=2)
            for stacks in (2, 4):  # an H100 has 5; the bit-slice mapping needs a power of two
                for policy in ("ramulator", "bank_low"):
                    cases.append(case("70b", f"{policy}/b{b}/stacks{stacks}", segs, gen, policy, "allbank", stacks,
                                      batch=b))
    if "ablation" in which:
        segs, gen = trace_for("8b_b1_s4096", m8, batch=1, seq_len=4096, stacks=1)
        rules = [None, "nFAW", "nCCDL", "nCCDR", "nRRDS", "nRRDL", "nWTR", "nRTW", "nPPD", "bus(ACT)", "nRCDRD",
                 "nRP", "nRAS"]
        for policy in ("ramulator", "bank_low"):
            for rule in rules:
                cases.append(case("ablation", f"{policy}/{rule or 'baseline'}", segs, gen, policy, "allbank", 1,
                                  disable=rule, rule=rule or "baseline"))
    if "controller" in which:
        segs, gen = trace_for("8b_b1_s4096", m8, batch=1, seq_len=4096, stacks=1)
        for policy in ("ramulator", "bank_low"):
            for refresh in ("allbank", "perbank"):
                for nb in (False, True):
                    cases.append(case("controller", f"{policy}/{refresh}/{'nonblocking' if nb else 'blocking'}", segs,
                                      gen, policy, refresh, 1, nonblocking=nb))
    return cases


def run_case(c: dict, idx: int) -> dict:
    out = OUT / "runs" / f"{c['sweep']}_{idx:03d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(TW), "sim", "--segs", c["segs"], "--spec", str(SPEC), "--policy", c["policy"], "--stacks",
           str(c["stacks"]), "--refresh", c["refresh"], "--drain", "--json", str(out), "--label", c["name"]]
    if c["disable"]:
        cmd += ["--disable", c["disable"]]
    if c["nonblocking"]:
        cmd.append("--refresh-nonblocking")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {**c, "error": proc.stderr[-500:], "wall": time.time() - t0}
    res = json.loads(out.read_text())["result"]
    slots = res["slots"] or 1
    top = sorted(res["slot_reasons"].items(), key=lambda kv: -kv[1])
    return {**c, "result": res, "wall": time.time() - t0,
            "pct_of_peak": res["pct_of_peak"], "achieved_GBps": res["achieved_GBps"],
            "row_hit_rate_pct": res["row_hit_rate_pct"], "sim_time_us": res["sim_time_us"],
            "avg_read_latency_ns": res["avg_read_latency_ns"],
            "attribution": {k: 100 * v / slots for k, v in top}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", default="all")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    all_names = ["mapping_refresh", "batch", "seq", "kv_layout", "gqa", "70b", "ablation", "controller"]
    which = set(all_names) if args.sweeps == "all" else set(args.sweeps.split(","))
    OUT.mkdir(parents=True, exist_ok=True)
    cases = build_cases(which)
    print(f"{len(cases)} runs, {args.workers} workers", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(lambda ic: run_case(ic[1], ic[0]), enumerate(cases)))
    for r in results:
        if "error" in r:
            print(f"  FAILED {r['sweep']} {r['name']}: {r['error']}", flush=True)
        else:
            print(f"  {r['sweep']:16s} {r['name']:44s} {r['pct_of_peak']:6.1f}% of peak  {r['achieved_GBps']:8.1f} GB/s  "
                  f"hits {r['row_hit_rate_pct']:5.1f}%  {r['wall']:5.0f}s", flush=True)
    by_sweep: dict[str, list] = {}
    for r in results:
        by_sweep.setdefault(r["sweep"], []).append(r)
    stamp = {"date": datetime.date.today().isoformat(), "command": "python " + " ".join(sys.argv),
             "provenance": "HBM3 per Ramulator 2.1 preset HBM3_6400Mbps; Tokenwall core validated identical to Ramulator 2.1"}
    for name, rows in by_sweep.items():
        (OUT / f"sweep_{name}.json").write_text(json.dumps({**stamp, "cases": rows}, indent=1))
    with open(OUT / "sweeps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sweep", "name", "policy", "refresh", "stacks", "disable", "nonblocking", "pct_of_peak",
                    "achieved_GBps", "row_hit_rate_pct", "sim_time_us", "avg_read_latency_ns", "bytes_per_step",
                    "kv_read_share_pct", "requests", "wall_s"])
        for r in results:
            if "error" in r:
                continue
            g = r["gen"]["bytes"]
            w.writerow([r["sweep"], r["name"], r["policy"], r["refresh"], r["stacks"], r["disable"] or "",
                        r["nonblocking"], f"{r['pct_of_peak']:.3f}", f"{r['achieved_GBps']:.2f}",
                        f"{r['row_hit_rate_pct']:.3f}", f"{r['sim_time_us']:.1f}", f"{r['avg_read_latency_ns']:.2f}",
                        g["total"], f"{100 * g['kv_read'] / g['total']:.2f}", r["gen"]["requests"]["total"], f"{r['wall']:.0f}"])
    print(f"done in {time.time() - t0:.0f}s; wrote {OUT / 'sweeps.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
