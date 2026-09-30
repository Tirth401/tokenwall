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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from r2util import ORG_PRESET, TIMING_PRESET, hbm3_facts, print_summary, run_trace, summarize  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "phase0"


def write_trace(path: pathlib.Path, pattern: str, n: int, tx_bytes: int, seed: int = 1) -> None:
    rng = random.Random(seed)
    span_lines = (1 << 30) // tx_bytes  # random reads land anywhere in 1 GiB
    with open(path, "w") as f:
        for i in range(n):
            addr = i * tx_bytes if pattern == "sequential" else rng.randrange(span_lines) * tx_bytes
            f.write(f"LD {addr}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=20000)
    ap.add_argument("--refresh", default="allbank", choices=["allbank", "perbank", "none"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    facts = hbm3_facts()

    summary = {
        "date": datetime.date.today().isoformat(),
        "command": "python " + " ".join(sys.argv),
        "org_preset": ORG_PRESET,
        "timing_preset": TIMING_PRESET,
        "controller": "HBM34 + FRFCFS + Open row policy + RoBaRaCoCh",
        "refresh": args.refresh,
        "requests_per_run": args.requests,
        "access_bytes": facts["tx_bytes"],
        "tick_ps": facts["tick_ps"],
        "peak_channel_GBps": facts["peak_channel_GBps"],
        "runs": {},
    }
    first = True
    for pattern in ("sequential", "random"):
        trace = OUT / f"smoke_{pattern}.trace"
        write_trace(trace, pattern, args.requests, facts["tx_bytes"])
        stats, stats_yaml, wall = run_trace(trace, args.refresh, verbose=first)
        first = False
        row = summarize(stats, facts, wall)
        summary["runs"][pattern] = row
        (OUT / f"smoke_{pattern}_{args.refresh}.stats.yaml").write_text(stats_yaml)
        print_summary(f"{pattern} reads, refresh={args.refresh}", row)

    out = OUT / f"smoke_summary_{args.refresh}.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nchannel peak {facts['peak_channel_GBps']:.1f} GB/s; wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
