"""Static locality analysis: what a mapping does to a request stream, before any timing.

Plain version: walk the stream with a notebook per bank that remembers which
row is open. A request to the same row as that bank's last request is a hit;
anything else is a row switch. Also count how many different banks appear in
every run of 32 consecutive requests (how much parallel work the memory sees)
and, per channel, whether back-to-back requests share a bank group (tCCD_L
pace) or not (tCCD_S pace).

Technical version: this is the open-row-policy hit rate with an infinitely
patient scheduler, an upper bound on what a real controller achieves, computed
exactly and vectorised per block (stable argsort by bank, compare with the
previous access to that bank). Numbers here are properties of the address
stream under a mapping, not simulated bandwidth.
"""
from __future__ import annotations

import numpy as np

from .addrmap import Geometry, Policy
from .segments import SegmentTrace, iter_filtered


class LocalityAnalyzer:
    PAIR_KEYS = ("same_pc_same_bg", "same_pc_diff_bg", "diff_pc")

    def __init__(self, geo: Geometry, policy: Policy, window: int = 32):
        self.geo, self.policy, self.window = geo, policy, window
        self.last_row = np.full(geo.banks_total, -1, dtype=np.int64)
        self.requests = 0
        self.hits = 0
        self.windows = 0
        self.distinct_sum = 0
        self.pairs = {k: 0 for k in self.PAIR_KEYS}
        self.channel_hist = np.zeros(geo.channels, dtype=np.int64)
        self.per_class: dict[str, list[int]] = {}

    def add(self, addrs: np.ndarray, cls: str = "all") -> None:
        n = len(addrs)
        if n == 0:
            return
        vec = self.policy.map(addrs, self.geo)
        bank = self.geo.flat_bank(vec)
        row = vec["row"].astype(np.int64)

        # ideal open-row hits: compare with the previous access to the same bank
        order = np.argsort(bank, kind="stable")
        b, r = bank[order], row[order]
        first = np.ones(n, dtype=bool)
        first[1:] = b[1:] != b[:-1]
        prev = np.empty(n, dtype=np.int64)
        prev[1:] = r[:-1]
        prev[first] = self.last_row[b[first]]
        hits = int(np.count_nonzero(prev == r))
        last = np.ones(n, dtype=bool)
        last[:-1] = b[1:] != b[:-1]
        self.last_row[b[last]] = r[last]

        # bank parallelism: distinct banks per window of consecutive requests
        w = self.window
        m = n // w
        if m:
            bw = np.sort(bank[: m * w].reshape(m, w), axis=1)
            self.distinct_sum += int(((bw[:, 1:] != bw[:, :-1]).sum(axis=1) + 1).sum())
            self.windows += m

        # per-channel back-to-back pairs (stable sort by channel keeps arrival order)
        ch = vec["channel"].astype(np.int64)
        oc = np.argsort(ch, kind="stable")
        c, pc, bg = ch[oc], vec["pseudo_channel"][oc], vec["bank_group"][oc]
        same_ch = c[1:] == c[:-1]
        same_pc = pc[1:] == pc[:-1]
        same_bg = bg[1:] == bg[:-1]
        self.pairs["same_pc_same_bg"] += int(np.count_nonzero(same_ch & same_pc & same_bg))
        self.pairs["same_pc_diff_bg"] += int(np.count_nonzero(same_ch & same_pc & ~same_bg))
        self.pairs["diff_pc"] += int(np.count_nonzero(same_ch & ~same_pc))
        self.channel_hist += np.bincount(ch, minlength=self.geo.channels)

        self.requests += n
        self.hits += hits
        acc = self.per_class.setdefault(cls, [0, 0])
        acc[0] += n
        acc[1] += hits

    def result(self) -> dict:
        pairs_total = sum(self.pairs.values()) or 1
        mean_ch = self.channel_hist.mean() if self.requests else 0
        return {
            "policy": self.policy.name,
            "channel_interleave_bytes": self.geo.line_bytes << self.policy.interleave_log2,
            "channels": self.geo.channels,
            "requests": self.requests,
            "ideal_row_hit_rate_pct": 100 * self.hits / self.requests if self.requests else None,
            "row_switches": self.requests - self.hits,
            "avg_distinct_banks_per_window": self.distinct_sum / self.windows if self.windows else None,
            "window": self.window,
            "banks_total": self.geo.banks_total,
            "channel_pairs_pct": {k: 100 * v / pairs_total for k, v in self.pairs.items()},
            "channel_imbalance_max_over_mean": float(self.channel_hist.max() / mean_ch) if self.requests else None,
            "per_class": {k: {"requests": v[0], "ideal_row_hit_rate_pct": 100 * v[1] / v[0] if v[0] else None}
                          for k, v in self.per_class.items()},
        }


def request_class(trace: SegmentTrace, tensor_id: int, write: bool) -> str:
    kind = trace.tensors[tensor_id].kind
    if kind == "kv":
        return "kv_write" if write else "kv_read"
    return "weights" if kind == "weight" else "other"


def analyze_trace(trace: SegmentTrace, geo: Geometry, policy: Policy, max_requests: int | None = None,
                  block: int = 1 << 21, window: int = 32) -> dict:
    """Run the analyzer over a trace in exact stream order, merging consecutive blocks of the
    same request class up to `block` requests so numpy works on large arrays."""
    an = LocalityAnalyzer(geo, policy, window)
    buf: list[np.ndarray] = []
    buf_cls: str | None = None
    buf_n = 0

    def flush() -> None:
        nonlocal buf, buf_n
        if buf:
            an.add(np.concatenate(buf) if len(buf) > 1 else buf[0], buf_cls or "all")
        buf, buf_n = [], 0

    for addrs, write, tid in iter_filtered(trace, max_requests):
        cls = request_class(trace, tid, write)
        if cls != buf_cls or buf_n >= block:
            flush()
            buf_cls = cls
        buf.append(addrs)
        buf_n += len(addrs)
    flush()
    return an.result()
