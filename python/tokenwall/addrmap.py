"""Address mapping: linear byte address -> (channel, pseudo channel, SID, bank group, bank, row, column).

Plain version: an address is a big binary number. A mapping policy says which
of its bits pick the channel, which pick the bank, and which pick the row. If
the bank bits sit low, consecutive addresses land in different banks (cards
dealt around the table). If they sit high, consecutive addresses pile onto one
bank (every card to one player).

Technical version: a Policy is an ordered list of (field, bits) pieces from the
least significant bit upward, applied to the 32-byte line index. A field may be
split into several pieces (column low bits below the channel bits, the rest
above). Optional XOR rules fold low row bits into bank bits to break
power-of-two strides. Field order in the output follows Ramulator 2.1's HBM3
address vector: channel, pseudo channel, SID, bank group, bank, row, column.

The `ramulator` policy reproduces Ramulator's CacheLineInterleave(interleave_bits)
+ RoBaRaCoCh bit for bit; tests/test_addrmap.py checks that against a literal
transcription of the C++, and scripts/ramulator2_mapping_check.py checks it
end to end. cpp/include/tokenwall/addrmap.h mirrors this file; the cross
expander test compares the two.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import yaml

from .hbm_config import DEFAULT_HBM3_YAML
from .segments import SegmentTrace, iter_filtered

FIELDS = ("channel", "pseudo_channel", "sid", "bank_group", "bank", "row", "column")
POLICY_NAMES = ("ramulator", "bank_low", "bank_high", "bank_low_xor")

POLICY_DOC = {
    "ramulator": "Ramulator 2.1 default for HBM3 (CacheLineInterleave + RoBaRaCoCh): channel bits lowest, then "
                 "column, pseudo channel, SID, bank group, bank, row. A stream sweeps one 1 KiB row of one bank "
                 "before moving to the next bank, so back-to-back reads share a bank group and pay tCCD_L.",
    "bank_low": "Bank group and bank bits right above the channel bits: consecutive accesses to a channel "
                "alternate bank groups (tCCD_S pace) and touch every bank before any row changes. Column and row "
                "bits are highest.",
    "bank_high": "Bank bits above the row bits: a stream walks all 16384 rows of one bank before touching "
                 "another bank, so every row switch is a same-bank miss with no parallelism to hide it. The "
                 "anti-pattern, kept to measure the cost.",
    "bank_low_xor": "bank_low plus XOR of the low row bits into the bank-group and bank bits, so power-of-two "
                    "strides that would pile onto one bank spread over 16 banks. GPU and CPU controllers hash "
                    "like this.",
}


def _log2(n: int, what: str) -> int:
    if n <= 0 or n & (n - 1):
        raise ValueError(f"{what}={n} must be a positive power of two")
    return n.bit_length() - 1


@dataclass(frozen=True)
class Geometry:
    channels: int
    pseudo_channels: int
    sids: int
    bank_groups: int
    banks: int
    rows: int
    lines_per_row: int  # 32 B accesses per row (1 KiB row -> 32)
    line_bytes: int  # 32

    @classmethod
    def from_hbm(cls, stacks: int = 1, path: str | pathlib.Path = DEFAULT_HBM3_YAML) -> "Geometry":
        org = yaml.safe_load(pathlib.Path(path).read_text())["organization"]
        return cls(
            channels=int(org["jedec_channels_per_stack"]) * stacks,
            pseudo_channels=int(org["pseudo_channels_per_channel"]),
            sids=int(org["sids"]),
            bank_groups=int(org["bank_groups"]),
            banks=int(org["banks_per_group"]),
            rows=int(org["rows_per_bank"]),
            lines_per_row=int(org["accesses_per_row"]),
            line_bytes=int(org["access_bytes"]),
        )

    def count(self, field: str) -> int:
        return {
            "channel": self.channels, "pseudo_channel": self.pseudo_channels, "sid": self.sids,
            "bank_group": self.bank_groups, "bank": self.banks, "row": self.rows, "column": self.lines_per_row,
        }[field]

    def bits(self, field: str) -> int:
        return _log2(self.count(field), field)

    @property
    def line_shift(self) -> int:
        return _log2(self.line_bytes, "line_bytes")

    @property
    def total_bits(self) -> int:
        return sum(self.bits(f) for f in FIELDS)

    @property
    def capacity_bytes(self) -> int:
        return (1 << self.total_bits) * self.line_bytes

    @property
    def banks_total(self) -> int:
        return self.channels * self.pseudo_channels * self.sids * self.bank_groups * self.banks

    def flat_bank(self, vec: dict[str, np.ndarray]) -> np.ndarray:
        """Unique bank id across the whole memory system, in Ramulator field order."""
        b = vec["channel"].astype(np.int64)
        for f in ("pseudo_channel", "sid", "bank_group", "bank"):
            b = b * self.count(f) + vec[f].astype(np.int64)
        return b


@dataclass(frozen=True)
class XorRule:
    target: str
    source: str
    source_shift: int


@dataclass(frozen=True)
class Policy:
    name: str
    layout: tuple[tuple[str, int], ...]  # (field, bits) from LSB to MSB of the line index
    xors: tuple[XorRule, ...] = ()
    interleave_log2: int = 0

    def validate(self, geo: Geometry) -> None:
        got = {f: 0 for f in FIELDS}
        for f, n in self.layout:
            if f not in got or n < 0:
                raise ValueError(f"{self.name}: bad layout piece {(f, n)}")
            got[f] += n
        for f in FIELDS:
            if got[f] != geo.bits(f):
                raise ValueError(f"{self.name}: field {f} has {got[f]} bits, geometry needs {geo.bits(f)}")
        targets = {r.target for r in self.xors}
        for r in self.xors:
            if r.source in targets or r.target not in FIELDS or r.source not in FIELDS:
                raise ValueError(f"{self.name}: invalid xor rule {r}")

    def bit_offset(self, field: str) -> int:
        """Position (in line-index bits) of the field's lowest piece."""
        pos = 0
        for f, n in self.layout:
            if f == field and n:
                return pos
            pos += n
        raise KeyError(field)

    def map(self, addr: Iterable[int] | np.ndarray, geo: Geometry, allow_alias: bool = False) -> dict[str, np.ndarray]:
        a = np.asarray(addr, dtype=np.uint64)
        if a.size and not allow_alias and int(a.max()) >= geo.capacity_bytes:
            raise ValueError(f"address {int(a.max())} beyond capacity {geo.capacity_bytes} B "
                             f"({geo.channels} channels); use more stacks")
        lines = a >> np.uint64(geo.line_shift)
        out = {f: np.zeros(a.shape, dtype=np.uint64) for f in FIELDS}
        consumed = {f: 0 for f in FIELDS}
        for f, n in self.layout:
            if n == 0:
                continue
            piece = lines & np.uint64((1 << n) - 1)
            out[f] |= piece << np.uint64(consumed[f])
            consumed[f] += n
            lines = lines >> np.uint64(n)
        if self.xors:
            raw = {r.source: out[r.source].copy() for r in self.xors}
            for r in self.xors:
                mask = np.uint64((1 << geo.bits(r.target)) - 1)
                out[r.target] ^= (raw[r.source] >> np.uint64(r.source_shift)) & mask
        return out

    def unmap(self, vec: dict[str, np.ndarray], geo: Geometry) -> np.ndarray:
        v = {f: np.asarray(vec[f], dtype=np.uint64) for f in FIELDS}
        for r in self.xors:  # sources are never targets, so xor is its own inverse
            mask = np.uint64((1 << geo.bits(r.target)) - 1)
            v[r.target] = v[r.target] ^ ((v[r.source] >> np.uint64(r.source_shift)) & mask)
        lines = np.zeros(v["row"].shape, dtype=np.uint64)
        pos = 0
        consumed = {f: 0 for f in FIELDS}
        for f, n in self.layout:
            if n == 0:
                continue
            piece = (v[f] >> np.uint64(consumed[f])) & np.uint64((1 << n) - 1)
            lines |= piece << np.uint64(pos)
            pos += n
            consumed[f] += n
        return lines << np.uint64(geo.line_shift)

    def describe(self, geo: Geometry) -> str:
        lines = [f"policy {self.name} (channel interleave {geo.line_bytes << self.interleave_log2} B), "
                 f"{geo.channels} channels, {geo.capacity_bytes / 2**30:.0f} GiB",
                 f"  {POLICY_DOC.get(self.name, '')}",
                 f"  {'field':15s} {'bits':>4s} {'line-index bits':>16s} {'changes every':>14s} {'wraps every':>14s}"]
        pos = 0
        pieces = []
        for f, n in self.layout:
            if n:
                pieces.append((f, n, pos))
            pos += n
        for f in FIELDS:
            own = [(n, p) for (g, n, p) in pieces if g == f]
            rng = ", ".join(f"[{p + n - 1}:{p}]" if n > 1 else f"[{p}]" for n, p in own)
            period = geo.line_bytes << own[0][1]
            span = period << geo.bits(f) if len(own) == 1 else None
            lines.append(f"  {f:15s} {geo.bits(f):4d} {rng:>16s} {_human(period):>14s} "
                         f"{(_human(span) if span else 'split'):>14s}")
        for r in self.xors:
            lines.append(f"  xor: {r.target} ^= row >> {r.source_shift}")
        return "\n".join(lines)


def _human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n} {unit}" if unit == "B" else f"{n:.0f} {unit}"
        n //= 1024
    return f"{n} GiB"


def make_policy(name: str, geo: Geometry, interleave_log2: int = 0) -> Policy:
    """Build one of the named policies for a geometry.

    interleave_log2: log2 of the number of 32 B lines that stay in one channel
    before the channel bits change (0 -> every line, 3 -> 256 B, 5 -> 1 KiB).
    """
    if name not in POLICY_NAMES:
        raise ValueError(f"unknown policy {name!r}; choose from {POLICY_NAMES}")
    c, col = geo.bits("channel"), geo.bits("column")
    if not 0 <= interleave_log2 <= col:
        raise ValueError(f"interleave_log2 must be in [0, {col}]")
    pc, sid, bg, ba, row = (geo.bits(f) for f in ("pseudo_channel", "sid", "bank_group", "bank", "row"))
    low = (("column", interleave_log2),)
    ch = (("channel", c),)
    rest = (("column", col - interleave_log2),)
    xors: tuple[XorRule, ...] = ()
    if name == "ramulator":
        layout = low + ch + rest + (("pseudo_channel", pc), ("sid", sid), ("bank_group", bg), ("bank", ba), ("row", row))
    elif name in ("bank_low", "bank_low_xor"):
        layout = low + ch + (("pseudo_channel", pc), ("bank_group", bg), ("bank", ba), ("sid", sid)) + rest + (("row", row),)
        if name == "bank_low_xor":
            xors = (XorRule("bank_group", "row", 0), XorRule("bank", "row", bg))
    else:  # bank_high
        layout = low + ch + rest + (("pseudo_channel", pc), ("row", row), ("sid", sid), ("bank_group", bg), ("bank", ba))
    policy = Policy(name, tuple((f, n) for f, n in layout if n > 0), xors, interleave_log2)
    policy.validate(geo)
    return policy


def to_addr_vec(vec: dict[str, np.ndarray]) -> np.ndarray:
    """(N, 7) array in Ramulator HBM3 order: channel, pc, sid, bank group, bank, row, column."""
    return np.stack([vec[f] for f in FIELDS], axis=1)


def export_ramulator_mapped(trace: SegmentTrace, path: str | pathlib.Path, policy: Policy, geo: Geometry,
                            max_requests: int | None = None, reads_only: bool = False) -> int:
    """Write Ramulator 2.1 ReadWriteTrace format: 'R ch,pc,sid,bg,bank,row,col' per line."""
    n = 0
    with open(path, "w") as f:
        for addrs, write, _ in iter_filtered(trace, max_requests, reads_only):
            av = to_addr_vec(policy.map(addrs, geo)).tolist()
            op = "W " if write else "R "
            f.write("\n".join(op + ",".join(map(str, row)) for row in av))
            f.write("\n")
            n += len(av)
    return n
