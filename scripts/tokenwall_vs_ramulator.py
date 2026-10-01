#!/usr/bin/env python
"""Phase 3/4: run the same request stream through Tokenwall's C++ core and Ramulator 2.1 and compare.

Same traffic, mapping, channel count, frontend rate and refresh setting on both sides. Reports
ticks, hits, misses, conflicts, bandwidth and latency from each, and the differences. Both stop
when the frontend has sent its last request, so counts are comparable. With --cmd-trace both
record every DRAM command and scripts/cmd_trace_diff.py reports the first divergence.

Trace presets (one layer each, generated on the fly):
  layer0_b1   Llama 3 8B, batch 1, 1024 past positions, 1 stack (16 channels)      13.8 M requests
  layer0_b32  Llama 3 8B, batch 32, 4096 past positions, 2 stacks (32 channels)    30.4 M requests
  70b_layer0  Llama 3 70B TP=8 shard, batch 1, 4096 past positions, 2 stacks        6.7 M requests

Usage (repo root, venv active, build/ present):
    python scripts/tokenwall_vs_ramulator.py --trace layer0_b1 --requests 2000000 \
        --policies ramulator,bank_low --refresh none,allbank,perbank --interleave 0,3 [--reads-only] [--cmd-trace]
        [--disable nPPD]   # Tokenwall only: inject a known deviation to exercise the diff tool
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
from cmd_trace_diff import compare, load_ramulator, load_tokenwall  # noqa: E402
from r2util import hbm3_facts, run_trace, summarize  # noqa: E402

from tokenwall.addrmap import POLICY_NAMES  # noqa: E402
from tokenwall.hbm_config import HBMConfig  # noqa: E402
from tokenwall.model_config import ModelConfig  # noqa: E402
from tokenwall.tracegen import RunConfig, generate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "phase4"
TRACES = ROOT / "traces"
TW = ROOT / "build" / "cpp" / "tokenwall"
TW_EXPAND = ROOT / "build" / "cpp" / "tw_expand"
SPEC = ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.spec"
INT_KEYS = ("controller_ticks", "requests_served", "row_hits", "row_misses", "row_conflicts")
KEYS = INT_KEYS + ("achieved_GBps", "avg_read_latency_ticks")

PRESETS = {
    "layer0_b1": dict(model="llama3_8b", run=dict(batch=1, seq_len=1024, tp=1, stacks=1)),
    "layer0_b32": dict(model="llama3_8b", run=dict(batch=32, seq_len=4096, tp=1, stacks=2)),
    "70b_layer0": dict(model="llama3_70b", run=dict(batch=1, seq_len=4096, tp=8, stacks=2)),
}


def run_tokenwall(segs, policy, stacks, channels, k, requests, reads_only, refresh, json_path, disable, cmd_trace):
    cmd = [str(TW), "sim", "--segs", str(segs), "--spec", str(SPEC), "--policy", policy, "--stacks", str(stacks),
           "--channels", str(channels), "--interleave-log2", str(k), "--max-requests", str(requests),
           "--refresh", refresh, "--json", str(json_path), "--label", f"tokenwall {policy} k{k} {refresh}"]
    if reads_only:
        cmd.append("--reads-only")
    if disable:
        cmd += ["--disable", disable]
    if cmd_trace:
        cmd += ["--cmd-trace", str(cmd_trace)]
    t0 = time.time()
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    res = json.loads(json_path.read_text())["result"]
    res["wall_seconds_total"] = time.time() - t0
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", choices=sorted(PRESETS), default="layer0_b1")
    ap.add_argument("--requests", type=int, default=2_000_000, help="trace requests (writes count unless --reads-only)")
    ap.add_argument("--policies", default="ramulator,bank_low", help="comma list or 'all'")
    ap.add_argument("--refresh", default="none,allbank", help="comma list of none,allbank,perbank")
    ap.add_argument("--interleave", default="0", help="comma list of log2(lines per channel), e.g. 0,3,5")
    ap.add_argument("--channels", type=int, default=None, help="default 16 x stacks of the preset")
    ap.add_argument("--reads-only", action="store_true")
    ap.add_argument("--cmd-trace", action="store_true", help="record and diff every DRAM command")
    ap.add_argument("--disable", default=None, help="Tokenwall-only constraint substrings to disable (bug injection)")
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    facts = hbm3_facts()

    preset = PRESETS[args.trace]
    model = ModelConfig.from_yaml(ROOT / "configs" / "models" / f"{preset['model']}.yaml")
    stacks = preset["run"]["stacks"]
    run = RunConfig(layers=(0, 1), include_embed=False, include_lm_head=False, **preset["run"])
    trace, gstats = generate(model, run, HBMConfig.from_yaml())
    channels = args.channels or 16 * stacks
    label = f"{args.trace}_req{args.requests}{'_reads' if args.reads_only else ''}{('_' + args.label) if args.label else ''}"
    segs = TRACES / f"val_{args.trace}.segs"
    trace.write(segs)
    total = trace.num_requests()
    print(f"trace {args.trace}: {total:,} requests ({trace.num_writes():,} writes); using the first "
          f"{min(args.requests, total):,}; {channels} channels; stacks {stacks}")

    policies = list(POLICY_NAMES) if args.policies == "all" else args.policies.split(",")
    report = {"date": datetime.date.today().isoformat(), "command": "python " + " ".join(sys.argv),
              "trace": args.trace, "run": gstats["run"], "channels": channels, "requests": args.requests,
              "reads_only": args.reads_only, "disable": args.disable, "cases": []}
    for policy in policies:
        for k in (int(x) for x in args.interleave.split(",")):
            for refresh in args.refresh.split(","):
                case_id = f"{label}_{policy}_k{k}_{refresh}"
                exp = [str(TW_EXPAND), str(segs), "--max-requests", str(args.requests), "--ramulator-out"]
                if policy == "ramulator":
                    txt = TRACES / f"val_{args.trace}_flat.txt"
                    subprocess.run(exp + [str(txt)] + (["--reads-only"] if args.reads_only else []), check=True,
                                   capture_output=True)
                    r2_trace = TRACES / f"cmdtrace_r2_{case_id}" if args.cmd_trace else None
                    stats, _, wall = run_trace(txt, refresh, channels=channels, interleave_bits=k, cmd_trace=r2_trace)
                else:
                    txt = TRACES / f"val_{args.trace}_{policy}_k{k}_ch{channels}.txt"
                    subprocess.run(exp + [str(txt), "--map", policy, "--stacks", str(stacks), "--channels", str(channels),
                                          "--interleave-log2", str(k)]
                                   + (["--reads-only"] if args.reads_only else []), check=True, capture_output=True)
                    r2_trace = TRACES / f"cmdtrace_r2_{case_id}" if args.cmd_trace else None
                    stats, _, wall = run_trace(txt, refresh, channels=channels, premapped=True, cmd_trace=r2_trace)
                r2 = summarize(stats, facts, wall)
                tw_trace = TRACES / f"cmdtrace_tw_{case_id}.csv" if args.cmd_trace else None
                tw = run_tokenwall(segs, policy, stacks, channels, k, args.requests, args.reads_only, refresh,
                                   OUT / f"tokenwall_{case_id}.json", args.disable, tw_trace)
                case = {"policy": policy, "interleave_log2": k, "refresh": refresh, "ramulator": r2, "tokenwall": tw,
                        "identical_ints": all(r2[key] == tw[key] for key in INT_KEYS), "diff": {}}
                print(f"\n=== {args.trace}: {policy}, interleave {32 << k} B, refresh={refresh}, {channels} ch, "
                      f"{min(args.requests, total):,} requests{' (reads only)' if args.reads_only else ''} ===")
                print(f"  {'metric':24s} {'ramulator':>16s} {'tokenwall':>16s} {'diff':>12s} {'rel %':>8s}")
                for key in KEYS:
                    a, b = r2[key], tw[key]
                    d = b - a
                    rel = 100 * d / a if a else 0
                    case["diff"][key] = {"abs": d, "rel_pct": rel}
                    fa = f"{a:16.3f}" if isinstance(a, float) else f"{a:16d}"
                    fb = f"{b:16.3f}" if isinstance(b, float) else f"{b:16d}"
                    print(f"  {key:24s} {fa} {fb} {d:12.3f} {rel:8.3f}")
                print(f"  integer statistics identical: {case['identical_ints']}; wall ramulator {r2['wall_seconds']:.1f}s, "
                      f"tokenwall {tw['wall_seconds']:.1f}s")
                if args.cmd_trace:
                    res = compare(load_ramulator(str(r2_trace)), load_tokenwall(str(tw_trace)))
                    case["cmd_trace"] = {k2: v for k2, v in res.items() if k2 != "report"}
                    if res["identical"]:
                        print(f"  command traces identical: {res['commands']:,} commands over {res['channels']} channels")
                    else:
                        print("  command traces DIFFER:\n" + "\n".join("    " + ln for ln in res["report"].splitlines()))
                        case["cmd_trace"]["report"] = res["report"]
                report["cases"].append(case)

    print("\n=== summary ===")
    print(f"  {'policy':13s} {'il':>5s} {'refresh':8s} {'ticks R2':>10s} {'ticks TW':>10s} {'GB/s R2':>8s} {'GB/s TW':>8s} identical")
    for c in report["cases"]:
        print(f"  {c['policy']:13s} {32 << c['interleave_log2']:4d}B {c['refresh']:8s} {c['ramulator']['controller_ticks']:10d} "
              f"{c['tokenwall']['controller_ticks']:10d} {c['ramulator']['achieved_GBps']:8.1f} {c['tokenwall']['achieved_GBps']:8.1f} "
              f"{c['identical_ints']}")
    out = OUT / f"validation_{label}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
