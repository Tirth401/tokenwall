"""The segment format expands to exactly the sequence a naive loop would produce."""
import re

import numpy as np
import pytest

from tokenwall.segments import Group, Segment, SegmentTrace, TensorInfo, export_ramulator, stream_hash

RB = 32


def naive_group(g: Group, rb: int) -> list[tuple[int, bool]]:
    """Reference expansion in plain Python, written independently of the numpy path."""

    def seg_addrs(s: Segment) -> list[int]:
        out = []
        for o in range(s.outer_runs):
            for r in range(s.runs):
                for off in range(0, s.run_bytes, rb):
                    out.append(s.base + o * s.outer_stride + r * s.stride + off)
        return out

    if g.chunk_bytes == 0:
        return [(a, s.write) for s in g.segments for a in seg_addrs(s)]
    per = max(1, g.chunk_bytes // rb)
    lists = [seg_addrs(s) for s in g.segments]
    pos, out = [0] * len(lists), []
    while any(p < len(lst) for p, lst in zip(pos, lists)):
        for k, s in enumerate(g.segments):
            nxt = lists[k][pos[k]:pos[k] + per]
            out += [(a, s.write) for a in nxt]
            pos[k] += len(nxt)
    return out


def sample_trace() -> SegmentTrace:
    return SegmentTrace(
        request_bytes=RB,
        tensors=[TensorInfo(0, "weight", -1, 0, 16384, "w")],
        groups=[
            Group(0, [Segment(False, 0, 64, runs=2, stride=256, outer_runs=2, outer_stride=1024)]),
            Group(64, [Segment(False, 4096, 96), Segment(True, 8192, 128)]),
        ],
    )


def test_two_level_striding_matches_naive():
    t = sample_trace()
    got = list(t.iter_requests())
    want = naive_group(t.groups[0], RB) + naive_group(t.groups[1], RB)
    assert got == want
    assert [a for a, _ in got[:8]] == [0, 32, 256, 288, 1024, 1056, 1280, 1312]
    assert [a for a, _ in got[8:]] == [4096, 4128, 8192, 8224, 4160, 8256, 8288]


@pytest.mark.parametrize("chunk", [32, 64, 96, 4096])
def test_round_robin_matches_naive_for_any_chunk(chunk):
    g = Group(chunk, [Segment(False, 0, 32 * 5), Segment(True, 4096, 32 * 3), Segment(False, 8192, 32 * 7)])
    t = SegmentTrace(RB, groups=[g])
    assert list(t.iter_requests()) == naive_group(g, RB)


def test_block_boundaries_do_not_change_sequence():
    t = sample_trace()
    full = list(t.iter_requests())
    small_blocks = [(a, w) for addrs, w, _ in t.iter_blocks(block=3) for a in addrs.tolist()]
    assert small_blocks == full


def test_roundtrip(tmp_path):
    t = sample_trace()
    p = tmp_path / "t.segs"
    t.write(p)
    back = SegmentTrace.read(p)
    assert back.request_bytes == t.request_bytes
    assert back.groups == t.groups
    assert back.tensors == t.tensors


def test_validate_rejects_misaligned():
    with pytest.raises(ValueError):
        SegmentTrace(RB, groups=[Group(0, [Segment(False, 16, 64)])]).validate()
    with pytest.raises(ValueError):
        SegmentTrace(RB, groups=[Group(0, [Segment(False, 0, 48)])]).validate()


def test_hash_deterministic_and_order_sensitive():
    t = sample_trace()
    h1, n1, w1 = stream_hash(t)
    h2, n2, w2 = stream_hash(t)
    assert (h1, n1, w1) == (h2, n2, w2)
    assert n1 == t.num_requests() == 15 and w1 == 4
    swapped = SegmentTrace(RB, groups=[t.groups[1], t.groups[0]])
    assert stream_hash(swapped)[0] != h1


def test_hash_prefix_matches_truncated_full():
    t = sample_trace()
    n = 5
    h_prefix, n_prefix, _ = stream_hash(t, max_requests=n)
    prefix = SegmentTrace(RB, groups=[Group(0, [Segment(False, 0, 64, runs=2, stride=256, outer_runs=2, outer_stride=1024)])])
    # first 5 requests of group 0 only
    full = list(prefix.iter_requests())[:n]
    from tokenwall.segments import FNV_OFFSET, FNV_PRIME, MASK64
    h = FNV_OFFSET
    for a, w in full:
        h = ((h ^ ((a << 1) | int(w))) * FNV_PRIME) & MASK64
    assert (h_prefix, n_prefix) == (h, n)


def test_export_ramulator_format(tmp_path):
    t = sample_trace()
    p = tmp_path / "trace.txt"
    n = export_ramulator(t, p)
    lines = p.read_text().splitlines()
    assert n == len(lines) == 15
    assert all(re.fullmatch(r"(LD|ST) \d+", ln) for ln in lines)
    assert lines[0] == "LD 0" and lines[10] == "ST 8192"
