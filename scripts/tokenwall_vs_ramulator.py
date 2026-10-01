#!/usr/bin/env python
"""Phase 3/4: run the same request stream through Tokenwall's C++ core and Ramulator 2.1 and compare.

Same traffic (first N reads of Llama 3 8B layer 0), same mapping, same number of channels,
same frontend rate, same refresh setting. Reports both simulators' ticks, hits, misses,
conflicts and bandwidth, and the differences. Ramulator stops when the frontend has sent its
last request; Tokenwall does the same unless --drain is given, so counts are comparable.

Usage (repo root, venv active, build/ present):
    python scripts/tokenwall_vs_ramulator.py [--requests 1800000] [--seq 1024] [--policies ramulator,bank_low]
                                             [--refresh none,allbank] [--channels 16]
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
from r2util import hbm3_facts, run_trace, summarize  # noqa: E402

from tokenwall.hbm_config import HBMConfig  # noqa: E402
from tokenwall.model_config import ModelConfig  # noqa: E402
from tokenwall.tracegen import RunConfig, generate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "phase3"
TRACES = ROOT / "traces"
TW = ROOT / "build" / "cpp" / "tokenwall"
TW_EXPAND = ROOT / "build" / "cpp" / "tw_expand"
SPEC = ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.spec"
KEYS = ("controller_ticks", "requests_served", "row_hits", "row_misses", "row_conflicts", "achieved_GBps",
        "avg_read_latency_ticks")


def run_tokenwall(segs, policy, channels, requests, refresh, json_path):
    cmd = [str(TW), "sim", "--segs", str(segs), "--spec", str(SPEC), "--policy", policy, "--stacks",
           str(max(1, channels // 16)), "--channels", str(channels), "--max-requests", str(requests), "--reads-only",
           "--refresh", refresh, "--json", str(json_path), "--label", f"tokenwall {policy} {refresh}"]
    t0 = time.time()
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    res = json.loads(json_path.read_text())["result"]
    res["wall_seconds_total"] = time.time() - t0
    return res, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=1_800_000)
    ap.add_argument("--seq", type=int, default=1024)
    ap.add_argument("--policies", default="ramulator,bank_low")
    ap.add_argument("--refresh", default="none,allbank")
    ap.add_argument("--channels", type=int, default=16)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    facts = hbm3_facts()

    model = ModelConfig.from_yaml(ROOT / "configs" / "models" / "llama3_8b.yaml")
    run = RunConfig(batch=1, seq_len=args.seq, layers=(0, 1), include_embed=False, include_lm_head=False)
    trace, _ = generate(model, run, HBMConfig.from_yaml())
    label = f"llama3_8b_layer0_b1_s{args.seq}_reads{args.requests}"
    segs = TRACES / f"{label}.segs"
    trace.write(segs)

    report = {"date": datetime.date.today().isoformat(), "command": "python " + " ".join(sys.argv),
              "channels": args.channels, "requests": args.requests, "cases": []}
    for policy in args.policies.split(","):
        for refresh in args.refresh.split(","):
            # Ramulator side: flat trace for its own mapping, pre-mapped otherwise
            if policy == "ramulator":
                txt = TRACES / f"{label}_flat.txt"
                subprocess.run([str(TW_EXPAND), str(segs), "--max-requests", str(args.requests), "--reads-only",
                                "--ramulator-out", str(txt)], check=True, capture_output=True)
                stats, _, wall = run_trace(txt, refresh, channels=args.channels)
            else:
                txt = TRACES / f"{label}_{policy}_ch{args.channels}.txt"
                subprocess.run([str(TW_EXPAND), str(segs), "--max-requests", str(args.requests), "--reads-only",
                                "--map", policy, "--stacks", str(max(1, args.channels // 16)),
                                "--ramulator-out", str(txt)], check=True, capture_output=True)
                stats, _, wall = run_trace(txt, refresh, channels=args.channels, premapped=True)
            r2 = summarize(stats, facts, wall)
            tw, tw_text = run_tokenwall(segs, policy, args.channels, args.requests,
                                        refresh, OUT / f"tokenwall_{label}_{policy}_{refresh}.json")
            case = {"policy": policy, "refresh": refresh, "ramulator": r2, "tokenwall": tw, "diff": {}}
            print(f"\n=== {policy}, refresh={refresh}, {args.channels} channels, {args.requests:,} reads ===")
            print(f"  {'metric':24s} {'ramulator':>16s} {'tokenwall':>16s} {'diff':>12s} {'rel %':>8s}")
            for k in KEYS:
                a, b = r2[k], tw[k]
                d = b - a
                rel = 100 * d / a if a else 0
                case["diff"][k] = {"abs": d, "rel_pct": rel}
                fa = f"{a:16.3f}" if isinstance(a, float) else f"{a:16d}"
                fb = f"{b:16.3f}" if isinstance(b, float) else f"{b:16d}"
                print(f"  {k:24s} {fa} {fb} {d:12.3f} {rel:8.3f}")
            print(f"  wall: ramulator {r2['wall_seconds']:.1f}s, tokenwall {tw['wall_seconds']:.1f}s")
            print("  tokenwall slot attribution (top):")
            slots = tw["slots"] or 1
            for k, v in sorted(tw["slot_reasons"].items(), key=lambda kv: -kv[1])[:8]:
                print(f"    {k:44s} {100 * v / slots:6.2f}%")
            report["cases"].append(case)
    out = OUT / f"validation_{label}_ch{args.channels}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
