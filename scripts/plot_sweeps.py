#!/usr/bin/env python
"""Phase 5 sensitivity plots from results/phase5/sweep_*.json.

Style follows the project's charting rules: one axis per panel, thin bars, hairline solid
gridlines, categorical colors assigned by entity (ramulator = blue, bank_low = orange,
bank_high = aqua, bank_low_xor = yellow) and never cycled, a legend for two or more series,
selective direct labels, text in ink tokens. Every caption names the provenance.

Usage: python scripts/plot_sweeps.py   -> results/phase5/plots/*.png
"""
from __future__ import annotations

import json
import pathlib
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
R = ROOT / "results" / "phase5"
P = R / "plots"

SERIES = {"ramulator": "#2a78d6", "bank_low": "#eb6834", "bank_high": "#1baf7a", "bank_low_xor": "#eda100"}
STACK = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
SURFACE, INK, INK2, MUTED, GRID, BASE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
PROVENANCE = "HBM3 per Ramulator 2.1 preset HBM3_6400Mbps. Tokenwall core, validated identical to Ramulator 2.1. One decode layer per run."
LABEL = {"ramulator": "ramulator (Ramulator default)", "bank_low": "bank_low", "bank_high": "bank_high", "bank_low_xor": "bank_low_xor"}


def load(name: str) -> list[dict]:
    path = R / f"sweep_{name}.json"
    return json.loads(path.read_text())["cases"] if path.exists() else []


def style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "semibold",
        "axes.labelsize": 10, "axes.edgecolor": BASE, "axes.linewidth": 0.8, "axes.facecolor": SURFACE,
        "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE, "text.color": INK, "axes.labelcolor": INK2,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
        "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 9,
    })


def clean(ax, left_label="% of HBM3 peak", ymax=100):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_ylabel(left_label)
    if ymax:
        ax.set_ylim(0, ymax)
        ax.yaxis.set_major_locator(MultipleLocator(20))
    ax.tick_params(length=0)


def caption(fig, title, subtitle, width=118):
    w_in = fig.get_size_inches()[0]
    fig.suptitle("\n".join(textwrap.wrap(title, int(7.2 * w_in))), x=0.01, y=0.995, ha="left", va="top", fontsize=13,
                 fontweight="semibold", color=INK)
    body = "\n".join(textwrap.wrap(subtitle, int(width * w_in / 9)) + textwrap.wrap(PROVENANCE, int(width * w_in / 9)))
    fig.text(0.01, 0.905, body, ha="left", va="top", fontsize=8.5, color=INK2)


def legend_above(ax, ncol):
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=ncol, borderaxespad=0, handlelength=1.2, columnspacing=1.2)


def fig_legend(fig, ax, y=0.80):
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, y), ncol=len(labels), frameon=False,
               handlelength=1.2, columnspacing=1.2)


def grouped_bars(ax, groups, series, values, width=0.18, fmt="{:.0f}", label_all=True):
    """groups: x labels; series: list of (key, color, legend); values[key][i]."""
    n = len(series)
    xs = range(len(groups))
    for j, (key, color, legend) in enumerate(series):
        offs = [x + (j - (n - 1) / 2) * (width + 0.02) for x in xs]
        vals = values[key]
        ax.bar(offs, vals, width=width, color=color, label=legend, linewidth=0)
        if label_all:
            for x, v in zip(offs, vals):
                if v is not None:
                    ax.text(x, v + 1.2, fmt.format(v), ha="center", va="bottom", fontsize=8, color=INK2)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(groups)


def fig_mapping_refresh():
    cases = load("mapping_refresh")
    if not cases:
        return
    refresh = ["none", "allbank", "perbank"]
    pols = ["ramulator", "bank_low", "bank_high", "bank_low_xor"]
    vals = {p: [next((c["pct_of_peak"] for c in cases if c["policy"] == p and c["refresh"] == r), None) for r in refresh]
            for p in pols}
    fig, ax = plt.subplots(figsize=(9, 5.6))
    fig.subplots_adjust(top=0.72, bottom=0.12)
    grouped_bars(ax, ["no refresh", "all-bank refresh", "per-bank refresh"], [(p, SERIES[p], LABEL[p]) for p in pols], vals)
    clean(ax)
    legend_above(ax, 4)
    caption(fig, "Address mapping decides most of the bandwidth; refresh policy decides the rest",
            "Llama 3 8B decode layer, batch 1, 4096 past positions, one stack (16 channels). Per-bank refresh uses "
            "Ramulator's rule that nothing is scheduled while a refresh request waits.")
    fig.savefig(P / "mapping_refresh.png", dpi=160)
    plt.close(fig)


def fig_batch_seq():
    b, s = load("batch"), load("seq")
    if not b and not s:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    fig.subplots_adjust(top=0.66, bottom=0.18, wspace=0.25)
    for ax, cases, key, xlabel, title in ((axes[0], b, "batch", "batch size (sequences per step)", "Batch sweep, 4096 past positions, 2 stacks"),
                                          (axes[1], s, "seq", "past positions per sequence", "Sequence sweep, batch 32, 4 stacks")):
        for p in ("ramulator", "bank_low"):
            pts = sorted(((c[key], c["pct_of_peak"], 100 * c["gen"]["bytes"]["kv_read"] / c["gen"]["bytes"]["total"])
                          for c in cases if c["policy"] == p), key=lambda t: t[0])
            if not pts:
                continue
            xs = [t[0] for t in pts]
            ys = [t[1] for t in pts]
            ax.plot(xs, ys, color=SERIES[p], linewidth=2, marker="o", markersize=8, markeredgecolor=SURFACE,
                    markeredgewidth=2, label=LABEL[p], solid_capstyle="round")
            ax.text(xs[-1], ys[-1] + 3, f"{ys[-1]:.1f}%", ha="right", va="bottom", fontsize=8.5, color=INK2)
        ax.set_xscale("log", base=2)
        if cases:
            pts = sorted(((c[key], 100 * c["gen"]["bytes"]["kv_read"] / c["gen"]["bytes"]["total"]) for c in cases if c["policy"] == "bank_low"))
            ax.set_xticks([t[0] for t in pts])
            ax.set_xticklabels([f"{t[0]}\n{t[1]:.0f}% KV" for t in pts])
            ax.set_xticks([], minor=True)
        ax.set_xlabel(xlabel)
        ax.set_title(title, loc="left")
        clean(ax)
    fig_legend(fig, axes[0], y=0.79)
    caption(fig, "Batch and sequence length move the KV share of traffic, not the fraction of peak",
            "Llama 3 8B decode layer. Tick labels give the share of bytes that are KV-cache reads.")
    fig.savefig(P / "batch_seq.png", dpi=160)
    plt.close(fig)


def fig_kv_layout():
    cases = load("kv_layout")
    if not cases:
        return
    combos = [("head_major", "head_outer"), ("head_major", "position_outer"), ("position_major", "head_outer"),
              ("position_major", "position_outer")]
    names = ["head-major\nhead walk\n(matched)", "head-major\nposition walk\n(mismatched)",
             "position-major\nhead walk\n(mismatched)", "position-major\nposition walk\n(matched)"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.6))
    fig.subplots_adjust(top=0.68, bottom=0.2, wspace=0.22)
    pols = ["ramulator", "bank_low"]
    pct = {p: [next((c["pct_of_peak"] for c in cases if c["policy"] == p and c["layout"] == l and c["order"] == o), None)
               for l, o in combos] for p in pols}
    kvhit = {}
    for p in pols:
        row = []
        for l, o in combos:
            c = next((c for c in cases if c["policy"] == p and c["layout"] == l and c["order"] == o), None)
            k = c["result"]["classes"]["kv_read"] if c else None
            row.append(100 * k["row_hits"] / k["served"] if k and k["served"] else None)
        kvhit[p] = row
    grouped_bars(axes[0], names, [(p, SERIES[p], LABEL[p]) for p in pols], pct, width=0.3, fmt="{:.1f}")
    clean(axes[0])
    axes[0].set_title("% of peak", loc="left")
    grouped_bars(axes[1], names, [(p, SERIES[p], LABEL[p]) for p in pols], kvhit, width=0.3, fmt="{:.1f}")
    clean(axes[1], left_label="KV-read row-hit rate (%)")
    axes[1].set_title("KV-read row hits", loc="left")
    fig_legend(fig, axes[0], y=0.80)
    caption(fig, "The KV-cache layout must match the attention kernel's walk order, under either mapping",
            "Llama 3 8B decode layer, batch 32 (55% of bytes are KV reads), 4096 past positions, 2 stacks, all-bank refresh. "
            "A mismatch costs 2 to 5x even with bank_low; the row-hit rate alone does not show it.")
    fig.savefig(P / "kv_layout.png", dpi=160)
    plt.close(fig)


def fig_gqa_70b():
    g, m70 = load("gqa"), load("70b")
    if not g and not m70:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.4), gridspec_kw={"width_ratios": [1, 1.6]})
    fig.subplots_adjust(top=0.66, bottom=0.2, wspace=0.25)
    pols = ["ramulator", "bank_low"]
    if g:
        kvs = [8, 32]
        vals = {p: [next((c["pct_of_peak"] for c in g if c["policy"] == p and c["kv_heads"] == k), None) for k in kvs] for p in pols}
        gb = {k: next((c["gen"]["bytes"]["total"] / 2**30 for c in g if c["kv_heads"] == k), 0) for k in kvs}
        grouped_bars(axes[0], [f"8 KV heads (GQA)\n{gb[8]:.2f} GiB per layer", f"32 KV heads (MHA)\n{gb[32]:.2f} GiB per layer"],
                     [(p, SERIES[p], LABEL[p]) for p in pols], vals, width=0.3, fmt="{:.1f}")
        clean(axes[0])
        axes[0].set_title("GQA versus full MHA, 8B, batch 1", loc="left")
        fig_legend(fig, axes[0], y=0.80)
    if m70:
        combos = [(1, 2), (1, 4), (8, 2), (8, 4)]
        names = [f"batch {b}\n{s} stacks" for b, s in combos]
        vals = {p: [next((c["pct_of_peak"] for c in m70 if c["policy"] == p and c["batch"] == b and c["stacks"] == s), None)
                    for b, s in combos] for p in pols}
        grouped_bars(axes[1], names, [(p, SERIES[p], LABEL[p]) for p in pols], vals, width=0.3, fmt="{:.1f}")
        clean(axes[1])
        axes[1].set_title("Llama 3 70B, one tensor-parallel shard of 8", loc="left")
    caption(fig, "The fraction of peak is a property of the mapping, not of the model shape",
            "Left: KV-head count changes bytes per step, not efficiency. Right: 70B shard on 2 or 4 stacks "
            "(an H100 has 5; the bit-slice mapping needs a power of two). All-bank refresh.")
    fig.savefig(P / "gqa_70b.png", dpi=160)
    plt.close(fig)


def fig_ablation():
    cases = load("ablation")
    if not cases:
        return
    pols = ["ramulator", "bank_low"]
    base = {p: next(c["pct_of_peak"] for c in cases if c["policy"] == p and c["rule"] == "baseline") for p in pols}
    rules = sorted({c["rule"] for c in cases if c["rule"] != "baseline"},
                   key=lambda r: -max(next((c["pct_of_peak"] for c in cases if c["policy"] == p and c["rule"] == r), 0) - base[p] for p in pols))
    nice = {"nFAW": "tFAW", "nCCDL": "tCCD_L", "nCCDR": "tCCD_R", "nRRDS": "tRRD_S", "nRRDL": "tRRD_L", "nWTR": "tWTR_S/L",
            "nRTW": "tRTW", "nPPD": "tPPD", "bus(ACT)": "row-bus occupancy of ACT", "nRCDRD": "tRCD (read)", "nRP": "tRP", "nRAS": "tRAS"}
    fig, ax = plt.subplots(figsize=(9, 6.4))
    fig.subplots_adjust(top=0.76, left=0.26, bottom=0.1)
    h = 0.34
    for j, p in enumerate(pols):
        ys = [i + (j - 0.5) * (h + 0.04) for i in range(len(rules))]
        deltas = [next((c["pct_of_peak"] for c in cases if c["policy"] == p and c["rule"] == r), base[p]) - base[p] for r in rules]
        ax.barh(ys, deltas, height=h, color=SERIES[p], label=f"{LABEL[p]} (baseline {base[p]:.1f}%)", linewidth=0)
        for y, d in zip(ys, deltas):
            if abs(d) >= 0.05:
                ax.text(d + (0.3 if d >= 0 else -0.3), y, f"{d:+.1f}", va="center", ha="left" if d >= 0 else "right", fontsize=8, color=INK2)
    ax.set_yticks(range(len(rules)))
    ax.set_yticklabels([nice.get(r, r) for r in rules])
    ax.invert_yaxis()
    ax.axvline(0, color=BASE, linewidth=0.8)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    ax.set_xlabel("percentage points of peak recovered when the rule is removed (ablation)")
    ax.legend(loc="lower right")
    caption(fig, "What each timing rule costs: remove it and see what comes back",
            "Llama 3 8B decode layer, batch 1, one stack, all-bank refresh. Removing a rule is unphysical; the bar is that "
            "rule's share of the loss, not a design option. Gains do not add up: rules overlap.")
    fig.savefig(P / "ablation.png", dpi=160)
    plt.close(fig)


def fig_controller():
    cases = load("controller")
    if not cases:
        return
    combos = [("allbank", False), ("allbank", True), ("perbank", False), ("perbank", True)]
    names = ["all-bank\nblocking (Ramulator)", "all-bank\nnon-blocking,\nbanks reserved", "per-bank\nblocking (Ramulator)",
             "per-bank\nnon-blocking,\nbank reserved"]
    pols = ["ramulator", "bank_low"]
    vals = {p: [next((c["pct_of_peak"] for c in cases if c["policy"] == p and c["refresh"] == r and c["nonblocking"] == nb), None)
                for r, nb in combos] for p in pols}
    fig, ax = plt.subplots(figsize=(9, 5.8))
    fig.subplots_adjust(top=0.70, bottom=0.16)
    grouped_bars(ax, names, [(p, SERIES[p], LABEL[p]) for p in pols], vals, width=0.3, fmt="{:.1f}")
    clean(ax)
    legend_above(ax, 2)
    caption(fig, "Per-bank refresh is cheap once the controller keeps scheduling while a refresh waits",
            "Llama 3 8B decode layer, batch 1, one stack. 'Blocking' is Ramulator's HBM34 rule: no read or write is "
            "scheduled while a refresh request waits. 'Non-blocking' is a Tokenwall extension that keeps scheduling "
            "other banks and forbids new row opens on the bank(s) a pending refresh targets.")
    fig.savefig(P / "controller.png", dpi=160)
    plt.close(fig)


def fig_attribution():
    mr, ctrl = load("mapping_refresh"), load("controller")
    if not mr:
        return
    picks = []
    for p, r in (("ramulator", "allbank"), ("bank_low", "none"), ("bank_low", "allbank"), ("bank_low", "perbank")):
        c = next((c for c in mr if c["policy"] == p and c["refresh"] == r), None)
        if c:
            picks.append((f"{p}, {r.replace('allbank', 'all-bank').replace('perbank', 'per-bank')} refresh", c["attribution"]))
    c = next((c for c in ctrl if c["policy"] == "bank_low" and c["refresh"] == "perbank" and c["nonblocking"]), None)
    if c:
        picks.append(("bank_low, per-bank refresh, non-blocking controller", c["attribution"]))

    def bucket(attr):
        out = {"data": 0, "tCCD_L (same bank group)": 0, "tRCD (row open)": 0, "refresh (tRFC, tRFCpb, waits)": 0,
               "column bus": 0, "other": 0}
        for k, v in attr.items():
            if k == "data": out["data"] += v
            elif "nCCDL" in k: out["tCCD_L (same bank group)"] += v
            elif "nRCDRD" in k or "nRCDWR" in k: out["tRCD (row open)"] += v
            elif "nRFC" in k or k.startswith("refresh"): out["refresh (tRFC, tRFCpb, waits)"] += v
            elif "bus(RD" in k: out["column bus"] += v
            else: out["other"] += v
        return out

    rows = [(name, bucket(a)) for name, a in picks]
    keys = list(rows[0][1].keys())
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.subplots_adjust(top=0.74, left=0.34, bottom=0.14)
    ys = list(range(len(rows)))
    left = [0.0] * len(rows)
    for k, color in zip(keys, STACK):
        vals = [r[1][k] for r in rows]
        ax.barh(ys, vals, left=left, height=0.5, color=color, label=k, edgecolor=SURFACE, linewidth=1.5)
        for y, l, v in zip(ys, left, vals):
            if v >= 7:
                ax.text(l + v / 2, y, f"{v:.0f}%", ha="center", va="center", fontsize=8, color=SURFACE if k != "tCCD_L (same bank group)" else INK)
        left = [l + v for l, v in zip(left, vals)]
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of column-command slots per pseudo channel (%)")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)
    caption(fig, "Where the slots go: data on the wires versus the rule that blocked the oldest request",
            "Llama 3 8B decode layer, batch 1, one stack. 'data' is the achieved fraction of peak.")
    fig.savefig(P / "attribution.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    P.mkdir(parents=True, exist_ok=True)
    style()
    for f in (fig_mapping_refresh, fig_batch_seq, fig_kv_layout, fig_gqa_70b, fig_ablation, fig_controller, fig_attribution):
        f()
    print("plots:", sorted(p.name for p in P.glob("*.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
