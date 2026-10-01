#!/usr/bin/env python
"""Find the first command where Tokenwall and Ramulator 2.1 diverge, per channel, with bank context.

Plain version: two librarians kept a log of every action with its tick. Walk both logs
side by side and stop at the first line that differs; then show what each librarian had
done at that desk just before, and what state they believed the desk was in.

Inputs:
  Ramulator: the prefix given to CmdTraceRecorder(path=prefix); files <prefix>.ch<N> with
             header clock,command,Channel,PseudoChannel,Sid,BankGroup,Bank,Row,Column,type,source
  Tokenwall: `tokenwall sim --cmd-trace file.csv`, header
             clock,command,Channel,PseudoChannel,Sid,BankGroup,Bank,Row,Column,type

Usage:
    python scripts/cmd_trace_diff.py <ramulator_prefix> <tokenwall.csv> [--context 8]
Exit code 0 when identical, 1 when they diverge.
"""
from __future__ import annotations

import argparse
import csv
import glob
import pathlib
import sys
from collections import defaultdict

FIELDS = ("clock", "command", "PseudoChannel", "Sid", "BankGroup", "Bank", "Row", "Column", "type")


def _rows(path: pathlib.Path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield (int(row["clock"]), row["command"], int(row["PseudoChannel"]), int(row["Sid"]),
                   int(row["BankGroup"]), int(row["Bank"]), int(row["Row"]), int(row["Column"]), int(row["type"])), \
                  int(row["Channel"])


def load_ramulator(prefix: str) -> dict[int, list[tuple]]:
    out: dict[int, list[tuple]] = {}
    files = sorted(glob.glob(prefix + ".ch*"), key=lambda p: int(p.rsplit(".ch", 1)[1]))
    if not files:
        raise SystemExit(f"no files matching {prefix}.ch*")
    for p in files:
        ch = int(p.rsplit(".ch", 1)[1])
        out[ch] = [rec for rec, _ in _rows(pathlib.Path(p))]
    return out


def load_tokenwall(path: str) -> dict[int, list[tuple]]:
    out: dict[int, list[tuple]] = defaultdict(list)
    for rec, ch in _rows(pathlib.Path(path)):
        out[ch].append(rec)
    return dict(out)


def bank_key(rec: tuple) -> tuple:
    return rec[2], rec[3], rec[4], rec[5]  # pc, sid, bg, bank


def bank_state(history: list[tuple]) -> dict:
    """Reconstruct what the bank looked like after these commands: open row and last ticks."""
    state = {"open_row": None, "last": {}}
    for rec in history:
        clock, cmd = rec[0], rec[1]
        state["last"][cmd] = clock
        if cmd == "ACT":
            state["open_row"] = rec[6]
        elif cmd in ("PREpb", "PREab", "RDA", "WRA", "REFab", "REFpb"):
            if cmd in ("PREpb", "PREab", "RDA", "WRA"):
                state["open_row"] = None
    return state


def fmt(rec: tuple) -> str:
    clock, cmd, pc, sid, bg, bank, row, col, typ = rec
    return f"tick {clock:>9} {cmd:6s} pc {pc} sid {sid} bg {bg} bank {bank} row {row:>6} col {col:>3} type {typ}"


def compare(a: dict[int, list[tuple]], b: dict[int, list[tuple]], context: int = 8, a_name="ramulator",
            b_name="tokenwall") -> dict:
    channels = sorted(set(a) | set(b))
    total = 0
    for ch in channels:
        la, lb = a.get(ch, []), b.get(ch, [])
        n = min(len(la), len(lb))
        for i in range(n):
            if la[i] != lb[i]:
                return _divergence(ch, i, la, lb, context, a_name, b_name)
        if len(la) != len(lb):
            return _divergence(ch, n, la, lb, context, a_name, b_name)
        total += n
    return {"identical": True, "channels": len(channels), "commands": total}


def _divergence(ch, i, la, lb, context, a_name, b_name) -> dict:
    ra = la[i] if i < len(la) else None
    rb = lb[i] if i < len(lb) else None
    lines = [f"first divergence: channel {ch}, command index {i} (of {len(la)} / {len(lb)})",
             f"  {a_name:10s} {fmt(ra) if ra else '<no more commands>'}",
             f"  {b_name:10s} {fmt(rb) if rb else '<no more commands>'}"]
    keys = {bank_key(r) for r in (ra, rb) if r is not None and r[1] not in ('PREab', 'REFab')}
    for key in sorted(keys):
        pc, sid, bg, bank = key
        for name, lst in ((a_name, la), (b_name, lb)):
            hist = [r for r in lst[:i] if bank_key(r) == key or (r[2] == pc and r[1] in ('PREab', 'REFab'))]
            st = bank_state(hist)
            lines.append(f"  bank pc{pc}/sid{sid}/bg{bg}/bank{bank} in {name}: open_row={st['open_row']} "
                         f"last={{{', '.join(f'{k}:{v}' for k, v in sorted(st['last'].items(), key=lambda kv: kv[1]))}}}")
            for r in hist[-context:]:
                lines.append(f"      {fmt(r)}")
    return {"identical": False, "channel": ch, "index": i, "ramulator": ra, "tokenwall": rb, "report": "\n".join(lines)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ramulator_prefix")
    ap.add_argument("tokenwall_csv")
    ap.add_argument("--context", type=int, default=8)
    args = ap.parse_args()
    res = compare(load_ramulator(args.ramulator_prefix), load_tokenwall(args.tokenwall_csv), args.context)
    if res["identical"]:
        print(f"identical: {res['commands']:,} commands over {res['channels']} channels")
        return 0
    print(res["report"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
