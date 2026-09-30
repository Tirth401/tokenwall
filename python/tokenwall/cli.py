"""Command line: python -m tokenwall <gen|export-ramulator|hash|stats> ..."""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys

from .addrmap import POLICY_NAMES, Geometry, export_ramulator_mapped, make_policy, to_addr_vec
from .hbm_config import DEFAULT_HBM3_YAML, HBMConfig
from .locality import analyze_trace
from .model_config import ModelConfig
from .segments import SegmentTrace, export_ramulator, stream_hash
from .tracegen import KV_LAYOUTS, KV_ORDERS, RunConfig, generate


def _geo_policy(args: argparse.Namespace):
    geo = Geometry.from_hbm(stacks=args.stacks, path=args.hbm)
    k = (args.interleave_bytes // geo.line_bytes).bit_length() - 1
    if geo.line_bytes << k != args.interleave_bytes:
        raise SystemExit(f"--interleave-bytes must be {geo.line_bytes} times a power of two")
    return geo, make_policy(args.policy, geo, k)


def _add_map_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--policy", choices=POLICY_NAMES, default="ramulator")
    p.add_argument("--stacks", type=int, default=1, help="HBM3 stacks (16 channels each)")
    p.add_argument("--interleave-bytes", type=int, default=32, help="bytes per channel before the channel bits change")
    p.add_argument("--hbm", default=str(DEFAULT_HBM3_YAML))


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.2f} GiB"


def cmd_gen(args: argparse.Namespace) -> int:
    model = ModelConfig.from_yaml(args.model)
    if args.n_kv_heads is not None:
        model = model.with_overrides(num_key_value_heads=args.n_kv_heads)
    if args.dtype_bytes is not None:
        model = model.with_overrides(dtype_bytes=args.dtype_bytes)
    hbm = HBMConfig.from_yaml(args.hbm)
    layers = None
    if args.layers:
        a, b = args.layers.split(":")
        layers = (int(a), int(b))
    run = RunConfig(
        batch=args.batch, seq_len=args.seq, tp=args.tp, stacks=args.stacks,
        kv_layout=args.kv_layout, kv_order=args.kv_order, kv_capacity=args.kv_capacity,
        request_bytes=args.request_bytes, alignment=args.alignment, small_alignment=args.small_alignment,
        weight_streams=args.weight_streams,
        chunk_bytes=args.chunk_bytes, kv_chunk_bytes=args.kv_chunk_bytes, layers=layers,
        include_embed=(layers is None) if args.head_tail is None else args.head_tail,
        include_lm_head=(layers is None) if args.head_tail is None else args.head_tail,
        seed=args.seed,
    )
    trace, stats = generate(model, run, hbm)
    stats["generated_on"] = datetime.date.today().isoformat()
    stats["command"] = "python -m tokenwall " + " ".join(sys.argv[1:])
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    trace.write(out.with_suffix(".segs"))
    out.with_suffix(".meta.json").write_text(json.dumps(stats, indent=2))

    b = stats["bytes"]
    print(f"model {model.name}: {model.num_hidden_layers} layers, hidden {model.hidden_size}, "
          f"{model.num_attention_heads} heads / {model.num_key_value_heads} KV heads, dtype {model.dtype_bytes} B")
    print(f"run: batch {run.batch}, past positions {run.seq_len}, tp {run.tp} (this shard: "
          f"{stats['shard']['heads']} heads / {stats['shard']['kv_heads']} KV heads), "
          f"kv {run.kv_layout}/{run.kv_order}, {run.stacks} stack(s), layers {stats['shard']['layers_emitted']}")
    print(f"footprint {_fmt_bytes(stats['footprint_bytes'])} of {_fmt_bytes(stats['capacity_bytes'])} "
          f"({100 * stats['capacity_utilization']:.1f}%)")
    print("bytes per decode step:")
    for k in ("weights", "norm", "embed", "kv_read", "kv_write", "total"):
        share = 100 * b[k] / b["total"] if b["total"] else 0
        print(f"  {k:10s} {_fmt_bytes(b[k]):>12s}  {share:5.1f}%")
    r = stats["requests"]
    print(f"requests: {r['total']:,} ({r['read']:,} reads, {r['write']:,} writes) of {run.request_bytes} B; "
          f"{stats['segments']} segments in {stats['groups']} groups")
    d = stats["derived"]
    print(f"floor at {d['peak_GBps_ramulator_preset']:.1f} GB/s peak (Ramulator 2.1 preset): "
          f"{d['time_at_peak_ms']:.3f} ms per token")
    print(f"wrote {out.with_suffix('.segs')} and {out.with_suffix('.meta.json')}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    trace = SegmentTrace.read(args.segs)
    if args.policy is None:
        n = export_ramulator(trace, args.out, max_requests=args.max_requests, reads_only=args.reads_only)
        fmt = "LoadStoreTrace (LD/ST <addr>)"
    else:
        geo, policy = _geo_policy(args)
        n = export_ramulator_mapped(trace, args.out, policy, geo, max_requests=args.max_requests,
                                    reads_only=args.reads_only)
        fmt = f"ReadWriteTrace (R/W ch,pc,sid,bg,bank,row,col) via {policy.name}"
    print(f"wrote {n:,} requests to {args.out} as {fmt}")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    geo, policy = _geo_policy(args)
    print(policy.describe(geo))
    if args.addr:
        import numpy as np
        addrs = np.array([int(a, 0) for a in args.addr], dtype=np.uint64)
        vec = policy.map(addrs, geo)
        for a, row in zip(addrs.tolist(), to_addr_vec(vec).tolist()):
            print(f"  {a:#x} -> ch {row[0]} pc {row[1]} sid {row[2]} bg {row[3]} bank {row[4]} row {row[5]} col {row[6]}")
    return 0


def cmd_locality(args: argparse.Namespace) -> int:
    geo, policy = _geo_policy(args)
    trace = SegmentTrace.read(args.segs)
    res = analyze_trace(trace, geo, policy, max_requests=args.max_requests, window=args.window)
    res.update({"segs": args.segs, "date": datetime.date.today().isoformat(),
                "command": "python -m tokenwall " + " ".join(sys.argv[1:])})
    print(f"[{policy.name}, interleave {res['channel_interleave_bytes']} B, {geo.channels} channels] "
          f"{res['requests']:,} requests")
    print(f"  ideal row hit rate            {res['ideal_row_hit_rate_pct']:.2f}%   (row switches {res['row_switches']:,})")
    print(f"  distinct banks per {args.window:>3d} reqs   {res['avg_distinct_banks_per_window']:.2f} of {res['banks_total']}")
    pp = res["channel_pairs_pct"]
    print(f"  per-channel back-to-back      same PC same BG {pp['same_pc_same_bg']:.1f}%  same PC other BG "
          f"{pp['same_pc_diff_bg']:.1f}%  other PC {pp['diff_pc']:.1f}%")
    print(f"  channel imbalance max/mean    {res['channel_imbalance_max_over_mean']:.3f}")
    for k, v in res["per_class"].items():
        hr = v["ideal_row_hit_rate_pct"]
        print(f"  {k:10s} {v['requests']:>14,}  hit {hr:6.2f}%" if hr is not None else f"  {k:10s} {v['requests']:>14,}")
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2))
        print(f"wrote {out}")
    return 0


def cmd_hash(args: argparse.Namespace) -> int:
    trace = SegmentTrace.read(args.segs)
    h, n, w = stream_hash(trace, max_requests=args.max_requests)
    print(f"requests={n} reads={n - w} writes={w} bytes={n * trace.request_bytes} hash=0x{h:016x}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    print(json.dumps(json.loads(pathlib.Path(args.meta).read_text()), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tokenwall")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="generate a decode-step segment trace")
    g.add_argument("--model", required=True, help="configs/models/<name>.yaml")
    g.add_argument("--hbm", default=str(DEFAULT_HBM3_YAML))
    g.add_argument("--batch", type=int, default=1)
    g.add_argument("--seq", type=int, default=1024, help="past positions in the KV cache")
    g.add_argument("--tp", type=int, default=1, help="tensor-parallel degree; one shard is simulated")
    g.add_argument("--stacks", type=int, default=1, help="HBM3 stacks on this shard's GPU")
    g.add_argument("--kv-layout", choices=KV_LAYOUTS, default="head_major")
    g.add_argument("--kv-order", choices=KV_ORDERS, default="head_outer")
    g.add_argument("--kv-capacity", type=int, default=None)
    g.add_argument("--request-bytes", type=int, default=32)
    g.add_argument("--alignment", type=int, default=2 << 20, help="placement boundary for large tensors")
    g.add_argument("--small-alignment", type=int, default=512, help="packing boundary for small tensors")
    g.add_argument("--weight-streams", type=int, default=1)
    g.add_argument("--chunk-bytes", type=int, default=4096)
    g.add_argument("--kv-chunk-bytes", type=int, default=0)
    g.add_argument("--layers", default=None, help="slice a:b of layers (implies no embed/lm_head)")
    g.add_argument("--head-tail", dest="head_tail", action=argparse.BooleanOptionalAction, default=None,
                   help="force include/exclude embedding and lm_head")
    g.add_argument("--n-kv-heads", type=int, default=None, help="override KV heads (32 on Llama 3 8B = full MHA)")
    g.add_argument("--dtype-bytes", type=int, default=None)
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--out", required=True, help="output path stem (writes .segs and .meta.json)")
    g.set_defaults(fn=cmd_gen)

    e = sub.add_parser("export-ramulator", help="expand a .segs to Ramulator LD/ST text (or R/W addr_vec with --policy)")
    e.add_argument("segs")
    e.add_argument("--out", required=True)
    e.add_argument("--max-requests", type=int, default=None)
    e.add_argument("--reads-only", action="store_true", help="drop writes (Ramulator's ReadWriteTrace cannot coalesce them)")
    e.add_argument("--policy", choices=POLICY_NAMES, default=None, help="pre-map with this policy (ReadWriteTrace format)")
    e.add_argument("--stacks", type=int, default=1)
    e.add_argument("--interleave-bytes", type=int, default=32)
    e.add_argument("--hbm", default=str(DEFAULT_HBM3_YAML))
    e.set_defaults(fn=cmd_export)

    m = sub.add_parser("map", help="describe a mapping policy, optionally map addresses")
    _add_map_args(m)
    m.add_argument("--addr", nargs="*", default=None, help="addresses to map (decimal or 0x hex)")
    m.set_defaults(fn=cmd_map)

    lo = sub.add_parser("locality", help="static locality analysis of a .segs under a mapping policy")
    lo.add_argument("segs")
    _add_map_args(lo)
    lo.add_argument("--max-requests", type=int, default=None)
    lo.add_argument("--window", type=int, default=32)
    lo.add_argument("--out", default=None, help="write JSON here")
    lo.set_defaults(fn=cmd_locality)

    h = sub.add_parser("hash", help="stream hash of a .segs (matches cpp tw_expand)")
    h.add_argument("segs")
    h.add_argument("--max-requests", type=int, default=None)
    h.set_defaults(fn=cmd_hash)

    s = sub.add_parser("stats", help="pretty-print a .meta.json")
    s.add_argument("meta")
    s.set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    return args.fn(args)
