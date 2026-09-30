#!/usr/bin/env python
"""Phase 2: validate the `ramulator` mapping policy bit for bit against Ramulator 2.1, then
compare the four mapping policies on real Llama 3 8B layer-0 traffic under Ramulator's timing.

Part A (bit-exactness): the same read-only request prefix is fed to Ramulator twice:
  1. flat addresses (LoadStoreTrace); Ramulator maps them with CacheLineInterleave + RoBaRaCoCh;
  2. pre-mapped by our `ramulator` policy (ReadWriteTrace with pass-through mappers).
  If the policy is bit-exact, every statistic (ticks, hits, misses, conflicts) is identical.
Part B (policies): the same prefix pre-mapped with each policy, 16 channels (one stack).

Read-only because Ramulator keys write coalescing on the flat address, which ReadWriteTrace
leaves unset (see PROGRESS.md, Phase 4 items).

Usage (repo root, venv active):
    python scripts/ramulator2_mapping_check.py [--requests 1800000] [--seq 1024]
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from r2util import hbm3_facts, print_summary, run_trace, summarize  # noqa: E402

from tokenwall.addrmap import POLICY_NAMES, Geometry, export_ramulator_mapped, make_policy  # noqa: E402
from tokenwall.hbm_config import HBMConfig  # noqa: E402
from tokenwall.model_config import ModelConfig  # noqa: E402
from tokenwall.segments import export_ramulator  # noqa: E402
from tokenwall.tracegen import RunConfig, generate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "phase2"
TRACES = ROOT / "traces"
TW_EXPAND = ROOT / "build" / "cpp" / "tw_expand"
COMPARE_KEYS = ("controller_ticks", "requests_served", "row_hits", "row_misses", "row_conflicts")


def export(segs: pathlib.Path, out: pathlib.Path, max_requests: int, policy: str | None = None,
           stacks: int = 1, interleave_log2: int = 0) -> str:
    """Read-only export via the C++ expander (fast) or Python (fallback). Both are tested identical."""
    if TW_EXPAND.exists():
        cmd = [str(TW_EXPAND), str(segs), "--max-requests", str(max_requests), "--reads-only", "--ramulator-out", str(out)]
        if policy:
            cmd += ["--map", policy, "--stacks", str(stacks), "--interleave-log2", str(interleave_log2)]
        subprocess.run(cmd, check=True, capture_output=True)
        return "tw_expand"
    from tokenwall.segments import SegmentTrace
    trace = SegmentTrace.read(segs)
    if policy:
        geo = Geometry.from_hbm(stacks=stacks)
        export_ramulator_mapped(trace, out, make_policy(policy, geo, interleave_log2), geo, max_requests, reads_only=True)
    else:
        export_ramulator(trace, out, max_requests=max_requests, reads_only=True)
    return "python"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=1_800_000)
    ap.add_argument("--seq", type=int, default=1024)
    ap.add_argument("--part", choices=["both", "a", "b"], default="both",
                    help="a: bit-exactness only, b: policy comparison only")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    facts = hbm3_facts()

    model = ModelConfig.from_yaml(ROOT / "configs" / "models" / "llama3_8b.yaml")
    run = RunConfig(batch=1, seq_len=args.seq, layers=(0, 1), include_embed=False, include_lm_head=False)
    trace, gstats = generate(model, run, HBMConfig.from_yaml())
    label = f"llama3_8b_layer0_b1_s{args.seq}_reads{args.requests}"
    segs = TRACES / f"{label}.segs"
    trace.write(segs)
    total_reads = trace.num_requests() - trace.num_writes()
    if args.requests > total_reads:
        args.requests = total_reads
        label = f"llama3_8b_layer0_b1_s{args.seq}_reads{args.requests}"
    print(f"layer 0 slice: {total_reads:,} reads; using the first {args.requests:,}")
    report = {
        "date": datetime.date.today().isoformat(),
        "command": "python " + " ".join(sys.argv),
        "trace": {"model": model.name, "run": gstats["run"], "prefix_requests": args.requests,
                  "layer_reads_total": total_reads, "reads_only": True, "exporter": "tw_expand" if TW_EXPAND.exists() else "python"},
        "bit_exactness": [],
        "policies": [],
    }

    print("=== Part A: `ramulator` policy vs Ramulator's own mapping (identical stats expected) ===")
    for channels, k in ((16, 0), (16, 3), (32, 0)) if args.part in ("both", "a") else ():
        stacks = channels // 16
        geo = Geometry.from_hbm(stacks=stacks)
        flat = TRACES / f"{label}_flat.txt"
        mapped = TRACES / f"{label}_ramulator_ch{channels}_k{k}.txt"
        export(segs, flat, args.requests)
        export(segs, mapped, args.requests, "ramulator", stacks, k)
        s_flat, _, w1 = run_trace(flat, channels=channels, interleave_bits=k)
        s_map, _, w2 = run_trace(mapped, channels=channels, premapped=True)
        a, b = summarize(s_flat, facts, w1), summarize(s_map, facts, w2)
        same = all(a[key] == b[key] for key in COMPARE_KEYS)
        print(f"  {channels:2d} channels, interleave {32 << k:4d} B: "
              f"ticks {a['controller_ticks']} vs {b['controller_ticks']}, hits {a['row_hits']} vs {b['row_hits']}, "
              f"misses {a['row_misses']} vs {b['row_misses']}, conflicts {a['row_conflicts']} vs {b['row_conflicts']} "
              f"-> {'IDENTICAL' if same else 'DIFFERENT'}")
        report["bit_exactness"].append({"channels": channels, "interleave_bits": k, "identical": same,
                                        "flat": a, "premapped": b})

    print("\n=== Part B: mapping policies on the same traffic, 16 channels, Ramulator timing ===")
    for name in POLICY_NAMES if args.part in ("both", "b") else ():
        mapped = TRACES / f"{label}_{name}_ch16.txt"
        t0 = time.time()
        export(segs, mapped, args.requests, name, 1, 0)
        s, _, wall = run_trace(mapped, channels=16, premapped=True)
        row = summarize(s, facts, wall)
        row["export_seconds"] = time.time() - t0 - wall
        print_summary(f"{name}, 16 channels", row)
        report["policies"].append({"policy": name, **row})

    print("\n=== summary ===")
    print(f"  {'policy':14s} {'GB/s':>8s} {'% of 819.2':>10s} {'row hit %':>10s} {'read lat ns':>12s}")
    for r in report["policies"]:
        print(f"  {r['policy']:14s} {r['achieved_GBps']:8.1f} {r['pct_of_peak']:10.1f} "
              f"{r['row_hit_rate_pct']:10.1f} {r['avg_read_latency_ns']:12.1f}")
    out = OUT / f"mapping_check_{label}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
