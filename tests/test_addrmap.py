"""Mapping policies: bijective, in range, bit-exact with Ramulator's default, and with the locality
properties each policy claims."""
import numpy as np
import pytest

from tokenwall.addrmap import FIELDS, POLICY_NAMES, Geometry, make_policy, to_addr_vec

GEO16 = Geometry.from_hbm(stacks=1)
GEO32 = Geometry.from_hbm(stacks=2)
RNG = np.random.default_rng(7)


def random_addrs(geo: Geometry, n: int = 20000) -> np.ndarray:
    lines = RNG.integers(0, geo.capacity_bytes // geo.line_bytes, size=n, dtype=np.uint64)
    return lines * np.uint64(geo.line_bytes)


def ramulator_reference(addr: int, nch: int, interleave_bits: int) -> list[int]:
    """Literal transcription of Ramulator 2.1: CacheLineInterleave::apply + RoBaRaCoCh::apply for HBM3.
    Levels after Channel: PseudoChannel(1b), Sid(1b), BankGroup(2b), Bank(2b), Row(14b), Column(5b)."""
    tx_offset = 5
    if nch <= 1:
        ch, intra = 0, addr
    else:
        ch_shift = tx_offset + interleave_bits
        ch_width = nch.bit_length() - 1
        ch = (addr >> ch_shift) & ((1 << ch_width) - 1)
        low = addr & ((1 << ch_shift) - 1)
        high = addr >> (ch_shift + ch_width)
        intra = (high << ch_shift) | low
    a = intra >> tx_offset

    def slice_lower_bits(num_bits):
        nonlocal a
        lbits = a & ((1 << num_bits) - 1)
        a >>= num_bits
        return lbits

    col = slice_lower_bits(5)  # column first, adjusted for prefetch
    pc, sid, bg, bank, row = (slice_lower_bits(b) for b in (1, 1, 2, 2, 14))
    return [ch, pc, sid, bg, bank, row, col]


def test_geometry_from_preset():
    assert GEO16.channels == 16 and GEO16.total_bits == 29 and GEO16.capacity_bytes == 16 << 30
    assert GEO16.banks_total == 1024 and GEO32.total_bits == 30


@pytest.mark.parametrize("name", POLICY_NAMES)
@pytest.mark.parametrize("k", [0, 3, 5])
@pytest.mark.parametrize("geo", [GEO16, GEO32], ids=["16ch", "32ch"])
def test_bijection_and_ranges(name, k, geo):
    policy = make_policy(name, geo, k)
    addrs = random_addrs(geo)
    vec = policy.map(addrs, geo)
    for f in FIELDS:
        assert vec[f].max() < geo.count(f)
    assert np.array_equal(policy.unmap(vec, geo), addrs)
    # distinct addresses map to distinct vectors
    av = to_addr_vec(vec)
    assert len(np.unique(av, axis=0)) == len(np.unique(addrs))


@pytest.mark.parametrize("nch,k", [(1, 0), (16, 0), (16, 3), (32, 0), (32, 5)])
def test_ramulator_policy_is_bit_exact_with_ramulator_cpp(nch, k):
    geo = Geometry.from_hbm(stacks=max(1, nch // 16))
    if nch == 1:
        geo = Geometry(1, 2, 2, 4, 4, 16384, 32, 32)
    policy = make_policy("ramulator", geo, k)
    addrs = random_addrs(geo, 5000)
    ours = to_addr_vec(policy.map(addrs, geo)).tolist()
    ref = [ramulator_reference(int(a), nch, k) for a in addrs.tolist()]
    assert ours == ref


def test_ramulator_policy_keeps_a_row_in_one_bank_for_1KiB_per_channel():
    p = make_policy("ramulator", GEO16, 0)
    # consecutive lines of channel 0 are 16 lines apart in the flat space
    addrs = np.arange(32, dtype=np.uint64) * np.uint64(32 * 16)
    v = p.map(addrs, GEO16)
    bank = GEO16.flat_bank(v)
    assert len(set(bank.tolist())) == 1 and len(set(v["row"].tolist())) == 1
    assert v["column"].tolist() == list(range(32))


def test_bank_low_alternates_bank_groups_and_touches_all_banks_first():
    p = make_policy("bank_low", GEO16, 0)
    per_channel = np.arange(64, dtype=np.uint64) * np.uint64(32 * 16)  # 64 consecutive lines of channel 0
    v = p.map(per_channel, GEO16)
    assert v["channel"].max() == 0
    assert np.all(v["bank_group"][1:] != v["bank_group"][:-1]) or np.all(v["pseudo_channel"][1:] != v["pseudo_channel"][:-1])
    assert len(set(GEO16.flat_bank(v).tolist())) == 64  # 2 pc x 2 sid x 4 bg x 4 bank
    assert v["row"].max() == 0 and v["column"].max() == 0


def test_bank_high_keeps_one_bank_for_32MiB_per_channel():
    p = make_policy("bank_high", GEO16, 0)
    per_channel_lines = 2 * 16384 * 32  # pc x rows x lines_per_row before the bank bits move
    idx = np.array([0, 1, 31, 32, 1000, per_channel_lines - 1], dtype=np.uint64)
    v = p.map(idx * np.uint64(32 * 16), GEO16)
    assert len(set(GEO16.flat_bank(v).tolist())) == 2  # pc toggles, sid/bg/bank do not
    assert v["sid"].max() == 0 and v["bank_group"].max() == 0 and v["bank"].max() == 0
    v2 = p.map(np.array([per_channel_lines * 32 * 16], dtype=np.uint64), GEO16)
    assert v2["sid"][0] == 1


def test_xor_policy_spreads_power_of_two_stride():
    plain = make_policy("bank_low", GEO16, 0)
    hashed = make_policy("bank_low_xor", GEO16, 0)
    stride = np.uint64(32) << np.uint64(4 + 1 + 2 + 2 + 1 + 5)  # keeps everything but the row fixed
    addrs = np.arange(16, dtype=np.uint64) * stride
    assert len(set(GEO16.flat_bank(plain.map(addrs, GEO16)).tolist())) == 1
    assert len(set(GEO16.flat_bank(hashed.map(addrs, GEO16)).tolist())) == 16
    assert np.array_equal(hashed.unmap(hashed.map(addrs, GEO16), GEO16), addrs)


def test_interleave_moves_channel_period():
    for k in (0, 3, 5):
        p = make_policy("bank_low", GEO16, k)
        assert p.bit_offset("channel") == k
        two = p.map(np.array([0, 32 << k], dtype=np.uint64), GEO16)
        assert two["channel"].tolist() == [0, 1]
        if k:
            one = p.map(np.array([0, (32 << k) - 32], dtype=np.uint64), GEO16)
            assert one["channel"].tolist() == [0, 0]


def test_describe_lists_periods():
    text = make_policy("ramulator", GEO16, 0).describe(GEO16)
    assert "bank_group" in text and "changes every" in text and "1 KiB" in text


def test_address_beyond_capacity_is_refused():
    p = make_policy("ramulator", GEO16, 0)
    with pytest.raises(ValueError, match="stacks"):
        p.map(np.array([GEO16.capacity_bytes], dtype=np.uint64), GEO16)
    p.map(np.array([GEO16.capacity_bytes], dtype=np.uint64), GEO16, allow_alias=True)
