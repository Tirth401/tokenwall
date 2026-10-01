#!/usr/bin/env python
"""Export Ramulator 2.1's RESOLVED HBM3 runtime tables as a plain text file for the C++ core.

Why: Ramulator's Python DSL turns 51 symbolic timing rules into integer entries
(level, preceding commands, following commands, latency in half-CK ticks, window,
sibling, shared-window group) with the command-length adjustments applied. Loading
the identical table into Tokenwall's core means both simulators enforce the same
rules; Phase 4 differences are then about scheduling and timing logic, never about
a mistyped constant. Each entry also gets a human name (level:expression) so stall
attribution can say "RD blocked by BankGroup:nCCDL".

Usage (repo root, venv active):
    python scripts/export_dram_spec.py [--override nRCDRD=28 ...] [--out path]
Writes configs/hbm3/hbm3_16gb_8hi_6400.spec (same provenance as the YAML).
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import subprocess
import sys

import ramulator
from ramulator.dram.hbm3 import HBM3

ROOT = pathlib.Path(__file__).resolve().parents[1]
R2 = ROOT / "external" / "ramulator2"
ORG_PRESET = "HBM3_16Gb_8hi"
TIMING_PRESET = "HBM3_6400Mbps"


def named_entries(cls, timing_ticks: dict, cmd_cycles: dict, tick_mult: int) -> list[tuple]:
    """Replicate DRAMStandard.to_config()'s constraint expansion, keeping a name per entry."""
    level_idx = {name: i for i, name in enumerate(cls.levels)}
    cmd_idx = {c: i for i, c in enumerate(cls.commands)}
    entries = []
    # bus occupancy constraints come first, exactly as _generate_bus_constraints does
    buses = [cls.row_commands, cls.column_commands] if (cls.row_commands and cls.column_commands) else [cls.commands]
    for bus_cmds in buses:
        groups: dict[int, list[str]] = {}
        for cmd in bus_cmds:
            groups.setdefault(cmd_cycles.get(cmd, tick_mult), []).append(cmd)
        all_ids = sorted(cmd_idx[c] for c in bus_cmds)
        for ticks, cmds in groups.items():
            if ticks == 1:
                continue
            entries.append((0, [cmd_idx[c] for c in cmds], all_ids, ticks, 1, False, -1,
                            f"Channel:bus({'/'.join(cmds)})"))
    history_group = 0
    for tc in cls.timing_constraints:
        nominal = cls._eval_expr(tc.latency, timing_ticks)
        level = level_idx[tc.level]
        group = history_group if tc.shared_window else -1
        if tc.shared_window:
            history_group += 1
        lat_groups: dict[int, tuple[list, set]] = {}
        for p in tc.preceding:
            p_off = cmd_cycles.get(p, tick_mult) - 1
            for f in tc.following:
                f_off = cmd_cycles.get(f, tick_mult) - 1
                adjusted = nominal - f_off if tc.shared_window else nominal + p_off - f_off
                lst, fset = lat_groups.setdefault(adjusted, ([], set()))
                if cmd_idx[p] not in lst:
                    lst.append(cmd_idx[p])
                fset.add(cmd_idx[f])
        for latency, (p_ids, f_ids) in lat_groups.items():
            entries.append((level, p_ids, sorted(f_ids), latency, tc.window, tc.sibling, group,
                            f"{tc.level}:{tc.latency.replace(' ', '')}"))
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", action="append", default=[], metavar="KEY=CK")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    overrides = {}
    for item in args.override:
        k, _, v = item.partition("=")
        overrides[k] = int(v)
    if overrides and not args.out:
        raise SystemExit("overrides change the numbers: give --out so the file cannot be confused with the preset")

    dram = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET, **overrides)
    org_ck, t_ck = dram.resolve()
    cfg = dram.to_config()  # tick units, adjusted constraints: exactly what Ramulator's C++ loads
    mult = HBM3.tick_multiplier
    timing_ticks = {}
    for k, v in t_ck.items():
        timing_ticks[k] = v if k == "rate" else (v // mult if k == "tCK_ps" else int(v * mult))
    cmd_cycles = {c: int(x * mult) for c, x in HBM3.command_cycles.items()}

    named = named_entries(HBM3, timing_ticks, cmd_cycles, mult)
    raw = cfg["timing_constraints"]
    if len(named) != len(raw):
        raise SystemExit(f"constraint count mismatch: named {len(named)} vs to_config {len(raw)}")
    for i, (n, r) in enumerate(zip(named, raw)):
        level, p_ids, f_ids, latency = r[0], list(r[1]), list(r[2]), r[3]
        window = r[4] if len(r) > 4 else 1
        sibling = bool(r[5]) if len(r) > 5 else False
        group = r[6] if len(r) > 6 else -1
        if (n[0], n[1], n[2], n[3], n[4], n[5], n[6]) != (level, p_ids, f_ids, latency, window, sibling, group):
            raise SystemExit(f"constraint {i} differs from to_config: {n[:7]} vs {(level, p_ids, f_ids, latency, window, sibling, group)}")

    commit = subprocess.check_output(["git", "-C", str(R2), "rev-parse", "--short", "HEAD"], text=True).strip()
    lines = [
        "tokenwall-dramspec 1",
        f"# generated {datetime.date.today().isoformat()} by scripts/export_dram_spec.py from Ramulator 2.1 {commit}",
        f"# presets {ORG_PRESET} {TIMING_PRESET}; overrides {overrides or 'none'}; latencies in half-CK ticks",
        "standard HBM3",
        f"tick_ps_numerator {t_ck['tCK_ps']}",  # true tick = tCK_ps / tick_multiplier
        f"tick_multiplier {mult}",
        "levels " + " ".join(HBM3.levels),
        "counts " + " ".join(str(c) for c in cfg["org"]["count"]),
        "commands " + " ".join(HBM3.commands),
        "command_cycles " + " ".join(str(c) for c in cfg["command_cycles"]),
        "row_commands " + " ".join(HBM3.row_commands),
        "column_commands " + " ".join(HBM3.column_commands),
        f"read_latency {cfg['read_latency']}",
        f"tx_bytes {cfg['data_payload_bytes']}",
    ]
    for name, val in zip(HBM3.timing_params, cfg["timing"]):
        lines.append(f"timing {name} {val}")
    for i, e in enumerate(named):
        level, p_ids, f_ids, latency, window, sibling, group, name = e
        lines.append(f"constraint {i} {level} {latency} {window} {int(sibling)} {group} {name} P "
                     + " ".join(map(str, p_ids)) + " F " + " ".join(map(str, f_ids)))
    out = pathlib.Path(args.out) if args.out else ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.spec"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}: {len(named)} constraints, {len(cfg['timing'])} timings, read_latency {cfg['read_latency']} ticks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
