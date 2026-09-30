"""Static locality analyzer against hand-computed answers."""
import numpy as np

from tokenwall.addrmap import Geometry, make_policy
from tokenwall.locality import LocalityAnalyzer

GEO = Geometry.from_hbm(stacks=1)


def addr_of(policy, **fields):
    vec = {f: np.array([fields.get(f, 0)], dtype=np.uint64)
           for f in ("channel", "pseudo_channel", "sid", "bank_group", "bank", "row", "column")}
    return int(policy.unmap(vec, GEO)[0])


def test_hits_follow_last_row_per_bank():
    p = make_policy("ramulator", GEO, 0)
    an = LocalityAnalyzer(GEO, p, window=4)
    seq = [
        addr_of(p, row=5, column=0),   # bank A row 5: first touch, miss
        addr_of(p, row=5, column=1),   # hit
        addr_of(p, row=5, column=2),   # hit
        addr_of(p, row=6, column=0),   # bank A row 6: switch
        addr_of(p, bank=1, row=6),     # bank B: first touch, miss
        addr_of(p, row=6, column=3),   # bank A still on row 6: hit
        addr_of(p, bank=1, row=6, column=1),  # bank B hit
        addr_of(p, row=5, column=0),   # bank A back to row 5: switch
    ]
    an.add(np.array(seq[:3], dtype=np.uint64), "w")  # state must carry across blocks
    an.add(np.array(seq[3:], dtype=np.uint64), "w")
    r = an.result()
    assert r["requests"] == 8 and r["row_switches"] == 4
    assert abs(r["ideal_row_hit_rate_pct"] - 50.0) < 1e-9
    assert r["per_class"]["w"]["requests"] == 8


def test_distinct_banks_per_window_and_pairs():
    p = make_policy("bank_low", GEO, 0)
    # 32 consecutive lines of channel 0 under bank_low: 32 different banks
    addrs = np.arange(32, dtype=np.uint64) * np.uint64(32 * 16)
    an = LocalityAnalyzer(GEO, p, window=32)
    an.add(addrs)
    r = an.result()
    assert r["avg_distinct_banks_per_window"] == 32
    assert r["channel_pairs_pct"]["same_pc_same_bg"] == 0.0
    assert r["channel_imbalance_max_over_mean"] == 16.0  # everything on channel 0

    q = make_policy("ramulator", GEO, 0)
    an2 = LocalityAnalyzer(GEO, q, window=32)
    an2.add(addrs)
    r2 = an2.result()
    assert r2["avg_distinct_banks_per_window"] == 1
    assert r2["channel_pairs_pct"]["same_pc_same_bg"] == 100.0
    assert abs(r2["ideal_row_hit_rate_pct"] - 100 * 31 / 32) < 1e-9
