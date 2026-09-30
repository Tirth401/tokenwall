"""Compact request-stream format shared by Python and C++.

A decode step for an 8B model is about 500 million 32-byte requests. Listing
them one per line is tens of gigabytes, so we store a recipe instead: a list
of *segments*, each "start at base, read run_bytes, repeat runs times with
this stride (and outer_runs times with an outer stride)". Segments are
collected into *groups*; a group either plays its segments one after another
(chunk_bytes == 0) or round-robins chunk_bytes from each segment in turn
(models many parallel streams hitting memory at once).

Both this module and cpp/include/tokenwall/segments.h expand the same file to
the same request sequence; tests/test_cross_expander.py hashes both streams
and asserts they match.

File format (whitespace separated, one record per line):
    tokenwall-segments 1
    request_bytes 32
    T <id> <kind> <layer> <base> <nbytes> <name>
    G <chunk_bytes>
    S <R|W> <base> <run_bytes> <runs> <stride> <outer_runs> <outer_stride> <tensor_id>
Every S line belongs to the most recent G line.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

FORMAT_VERSION = 1
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
MASK64 = (1 << 64) - 1


@dataclass(frozen=True)
class Segment:
    write: bool
    base: int
    run_bytes: int
    runs: int = 1
    stride: int = 0
    outer_runs: int = 1
    outer_stride: int = 0
    tensor_id: int = 0

    def num_requests(self, rb: int) -> int:
        return self.outer_runs * self.runs * (self.run_bytes // rb)

    def nbytes(self) -> int:
        return self.outer_runs * self.runs * self.run_bytes

    def addresses(self, rb: int, i0: int, i1: int) -> np.ndarray:
        """Addresses of request indices [i0, i1) within this segment."""
        per_run = self.run_bytes // rb
        per_outer = self.runs * per_run
        idx = np.arange(i0, i1, dtype=np.uint64)
        o = idx // np.uint64(per_outer)
        rem = idx % np.uint64(per_outer)
        r = rem // np.uint64(per_run)
        off = rem % np.uint64(per_run)
        return (
            np.uint64(self.base)
            + o * np.uint64(self.outer_stride)
            + r * np.uint64(self.stride)
            + off * np.uint64(rb)
        )

    def validate(self, rb: int) -> None:
        if self.run_bytes <= 0 or self.run_bytes % rb:
            raise ValueError(f"run_bytes {self.run_bytes} must be a positive multiple of {rb}")
        if self.runs <= 0 or self.outer_runs <= 0:
            raise ValueError("runs and outer_runs must be positive")
        for name in ("base", "stride", "outer_stride"):
            v = getattr(self, name)
            if v < 0 or v % rb:
                raise ValueError(f"{name} {v} must be a non-negative multiple of {rb}")


@dataclass
class Group:
    chunk_bytes: int
    segments: list[Segment] = field(default_factory=list)

    def num_requests(self, rb: int) -> int:
        return sum(s.num_requests(rb) for s in self.segments)


@dataclass
class TensorInfo:
    id: int
    kind: str
    layer: int
    base: int
    nbytes: int
    name: str


@dataclass
class SegmentTrace:
    request_bytes: int
    groups: list[Group] = field(default_factory=list)
    tensors: list[TensorInfo] = field(default_factory=list)

    # ---- sizes ----
    def num_requests(self) -> int:
        return sum(g.num_requests(self.request_bytes) for g in self.groups)

    def num_writes(self) -> int:
        rb = self.request_bytes
        return sum(s.num_requests(rb) for g in self.groups for s in g.segments if s.write)

    def validate(self) -> None:
        rb = self.request_bytes
        if rb <= 0 or rb & (rb - 1):
            raise ValueError("request_bytes must be a positive power of two")
        for g in self.groups:
            if g.chunk_bytes < 0:
                raise ValueError("chunk_bytes must be >= 0")
            for s in g.segments:
                s.validate(rb)

    # ---- file IO ----
    def write(self, path: str | pathlib.Path) -> None:
        self.validate()
        with open(path, "w") as f:
            f.write(f"tokenwall-segments {FORMAT_VERSION}\n")
            f.write(f"request_bytes {self.request_bytes}\n")
            for t in self.tensors:
                f.write(f"T {t.id} {t.kind} {t.layer} {t.base} {t.nbytes} {t.name}\n")
            for g in self.groups:
                f.write(f"G {g.chunk_bytes}\n")
                for s in g.segments:
                    f.write(
                        f"S {'W' if s.write else 'R'} {s.base} {s.run_bytes} {s.runs} {s.stride} "
                        f"{s.outer_runs} {s.outer_stride} {s.tensor_id}\n"
                    )

    @classmethod
    def read(cls, path: str | pathlib.Path) -> "SegmentTrace":
        trace: SegmentTrace | None = None
        with open(path) as f:
            head = f.readline().split()
            if len(head) != 2 or head[0] != "tokenwall-segments" or int(head[1]) != FORMAT_VERSION:
                raise ValueError(f"{path}: not a tokenwall-segments v{FORMAT_VERSION} file")
            rb_line = f.readline().split()
            if len(rb_line) != 2 or rb_line[0] != "request_bytes":
                raise ValueError(f"{path}: missing request_bytes line")
            trace = cls(request_bytes=int(rb_line[1]))
            for line in f:
                tok = line.split()
                if not tok or tok[0] == "#":
                    continue
                if tok[0] == "T":
                    trace.tensors.append(
                        TensorInfo(int(tok[1]), tok[2], int(tok[3]), int(tok[4]), int(tok[5]), tok[6])
                    )
                elif tok[0] == "G":
                    trace.groups.append(Group(int(tok[1])))
                elif tok[0] == "S":
                    if not trace.groups:
                        raise ValueError(f"{path}: S record before any G record")
                    trace.groups[-1].segments.append(
                        Segment(
                            write=(tok[1] == "W"),
                            base=int(tok[2]),
                            run_bytes=int(tok[3]),
                            runs=int(tok[4]),
                            stride=int(tok[5]),
                            outer_runs=int(tok[6]),
                            outer_stride=int(tok[7]),
                            tensor_id=int(tok[8]),
                        )
                    )
                else:
                    raise ValueError(f"{path}: unknown record {tok[0]!r}")
        trace.validate()
        return trace

    # ---- expansion ----
    def iter_blocks(
        self, block: int = 1 << 20, max_requests: int | None = None
    ) -> Iterator[tuple[np.ndarray, bool, int]]:
        """Yield (addresses, is_write, tensor_id) blocks in exact issue order."""
        rb = self.request_bytes
        budget = None if max_requests is None else max_requests
        for g in self.groups:
            for addrs, write, tid in _iter_group(g, rb, block):
                if budget is not None:
                    if budget <= 0:
                        return
                    if len(addrs) > budget:
                        addrs = addrs[:budget]
                    budget -= len(addrs)
                yield addrs, write, tid

    def iter_requests(self, max_requests: int | None = None) -> Iterator[tuple[int, bool]]:
        for addrs, write, _ in self.iter_blocks(max_requests=max_requests):
            for a in addrs.tolist():
                yield a, write


def _iter_group(g: Group, rb: int, block: int):
    if g.chunk_bytes == 0:
        for s in g.segments:
            n = s.num_requests(rb)
            for i0 in range(0, n, block):
                yield s.addresses(rb, i0, min(n, i0 + block)), s.write, s.tensor_id
        return
    per_chunk = max(1, g.chunk_bytes // rb)
    totals = [s.num_requests(rb) for s in g.segments]
    cursors = [0] * len(g.segments)
    remaining = sum(totals)
    while remaining:
        for k, s in enumerate(g.segments):
            if cursors[k] < totals[k]:
                i1 = min(totals[k], cursors[k] + per_chunk)
                yield s.addresses(rb, cursors[k], i1), s.write, s.tensor_id
                remaining -= i1 - cursors[k]
                cursors[k] = i1


def stream_hash(trace: SegmentTrace, max_requests: int | None = None) -> tuple[int, int, int]:
    """Order-sensitive FNV-1a style hash over words (addr << 1 | is_write).

    Returns (hash, requests, writes). Identical definition in
    cpp/include/tokenwall/segments.h (StreamHash).
    """
    h, n, w = FNV_OFFSET, 0, 0
    for addrs, write, _ in trace.iter_blocks(max_requests=max_requests):
        words = (addrs << np.uint64(1)) | np.uint64(1 if write else 0)
        for x in words.tolist():
            h = ((h ^ x) * FNV_PRIME) & MASK64
        n += len(words)
        if write:
            w += len(words)
    return h, n, w


def export_ramulator(trace: SegmentTrace, path: str | pathlib.Path, max_requests: int | None = None) -> int:
    """Write Ramulator 2.1 LoadStoreTrace format: one 'LD <addr>' or 'ST <addr>' per line."""
    n = 0
    with open(path, "w") as f:
        for addrs, write, _ in trace.iter_blocks(max_requests=max_requests):
            op = "ST " if write else "LD "
            f.write("\n".join(op + str(a) for a in addrs.tolist()))
            f.write("\n")
            n += len(addrs)
    return n
