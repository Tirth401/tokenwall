#!/usr/bin/env python
"""Run a Ramulator 2.1 LoadStoreTrace text file (LD/ST <addr>) through the HBM3 configuration.

Usage (repo root, venv active):
    python scripts/ramulator2_run_trace.py trace.txt --label llama3_8b_layer0 [--refresh allbank] [--out results/x.json]
One channel only (Ramulator's HBM3 preset is one channel per controller);
multi-channel runs arrive with Phase 4.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from r2util import ORG_PRESET, TIMING_PRESET, hbm3_facts, print_summary, run_trace, summarize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--label", required=True)
    ap.add_argument("--refresh", default="allbank", choices=["allbank", "perbank", "none"])
    ap.add_argument("--channels", type=int, default=1, help="HBM3 channels (16 per stack)")
    ap.add_argument("--premapped", action="store_true", help="trace is R/W addr_vec (ReadWriteTrace)")
    ap.add_argument("--interleave-bits", type=int, default=0, help="Ramulator CacheLineInterleave bits (flat traces)")
    ap.add_argument("--out", default=None, help="write summary JSON here")
    args = ap.parse_args()

    facts = hbm3_facts()
    stats, stats_yaml, wall = run_trace(pathlib.Path(args.trace), args.refresh, channels=args.channels,
                                        premapped=args.premapped, interleave_bits=args.interleave_bits)
    row = summarize(stats, facts, wall)
    print_summary(f"{args.label}, refresh={args.refresh}", row)
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "date": datetime.date.today().isoformat(),
            "command": "python " + " ".join(sys.argv),
            "trace": args.trace, "label": args.label, "refresh": args.refresh,
            "org_preset": ORG_PRESET, "timing_preset": TIMING_PRESET,
            "controller": "HBM34 + FRFCFS + Open row policy", "channels": args.channels, "premapped": args.premapped,
            "interleave_bits": args.interleave_bits,
            "peak_channel_GBps": facts["peak_channel_GBps"], "tick_ps": facts["tick_ps"],
            "result": row,
        }, indent=2))
        out.with_suffix(".stats.yaml").write_text(stats_yaml)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
