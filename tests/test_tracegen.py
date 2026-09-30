"""Decode-step generator: byte totals follow the model shape exactly."""
import numpy as np
import pytest

from tokenwall.model_config import ModelConfig
from tokenwall.tracegen import KV_LAYOUTS, KV_ORDERS, CapacityError, RunConfig, generate

RB = 32


def addresses(trace) -> np.ndarray:
    return np.concatenate([a for a, _, _ in trace.iter_blocks()])


def test_bytes_match_closed_form(tiny_model, hbm):
    m, B, S = tiny_model, 3, 10
    trace, stats = generate(m, RunConfig(batch=B, seq_len=S, alignment=4096), hbm)
    dt, H, I, V, L, D = m.dtype_bytes, m.hidden_size, m.intermediate_size, m.vocab_size, m.num_hidden_layers, m.head_dim
    kv, kv_dim = m.num_key_value_heads, m.kv_dim
    weights_per_layer = dt * (H * H + 2 * H * kv_dim + H * H + 3 * H * I)
    b = stats["bytes"]
    assert b["weights"] == L * weights_per_layer + dt * V * H  # plus lm_head
    assert b["norm"] == dt * H * (2 * L + 1)
    assert b["embed"] == B * H * dt
    assert b["kv_read"] == L * 2 * B * kv * S * D * dt  # K and V, all past positions
    assert b["kv_write"] == L * 2 * B * kv * D * dt  # K and V, one appended position
    assert b["total"] == sum(b[k] for k in ("weights", "norm", "embed", "kv_read", "kv_write"))
    assert stats["requests"]["total"] * RB == b["total"]
    assert stats["requests"]["total"] == trace.num_requests()
    assert stats["requests"]["write"] == b["kv_write"] // RB


def test_gqa_knob_scales_kv_traffic_only(tiny_model, hbm):
    run = RunConfig(batch=2, seq_len=16, alignment=4096)
    _, gqa = generate(tiny_model, run, hbm)  # 2 KV heads
    _, mha = generate(tiny_model.with_overrides(num_key_value_heads=4), run, hbm)  # full MHA
    assert mha["bytes"]["kv_read"] == 2 * gqa["bytes"]["kv_read"]
    assert mha["bytes"]["kv_write"] == 2 * gqa["bytes"]["kv_write"]
    m, dt = tiny_model, tiny_model.dtype_bytes
    extra_kv_dim = (4 - 2) * m.head_dim
    assert mha["bytes"]["weights"] - gqa["bytes"]["weights"] == m.num_hidden_layers * 2 * m.hidden_size * extra_kv_dim * dt
    assert mha["bytes"]["norm"] == gqa["bytes"]["norm"]


@pytest.mark.parametrize("layout", KV_LAYOUTS)
def test_kv_order_changes_sequence_not_bytes_touched(tiny_model, hbm, layout):
    base = dict(batch=2, seq_len=9, alignment=4096, kv_layout=layout)
    t_head, s_head = generate(tiny_model, RunConfig(kv_order="head_outer", **base), hbm)
    t_pos, s_pos = generate(tiny_model, RunConfig(kv_order="position_outer", **base), hbm)
    assert s_head["bytes"] == s_pos["bytes"]
    a_head, a_pos = addresses(t_head), addresses(t_pos)
    assert np.array_equal(np.sort(a_head), np.sort(a_pos))  # same bytes touched
    assert not np.array_equal(a_head, a_pos)  # different order


def test_kv_layout_is_a_permutation_of_the_same_bytes(tiny_model, hbm):
    """A layout only reorders the cache. With the cache exactly full, both layouts touch the
    same byte set in a different order; with spare capacity the untouched tail differs."""
    base = dict(batch=2, seq_len=9, alignment=4096, kv_order="head_outer")
    t_h, s_h = generate(tiny_model, RunConfig(kv_layout="head_major", **base), hbm)
    t_p, s_p = generate(tiny_model, RunConfig(kv_layout="position_major", **base), hbm)
    assert s_h["bytes"] == s_p["bytes"]
    assert np.array_equal(np.sort(addresses(t_h)), np.sort(addresses(t_p)))  # cache exactly full
    assert not np.array_equal(addresses(t_h), addresses(t_p))  # but visited in a different order

    spare = dict(base, kv_capacity=16)
    t_h2, _ = generate(tiny_model, RunConfig(kv_layout="head_major", **spare), hbm)
    t_p2, _ = generate(tiny_model, RunConfig(kv_layout="position_major", **spare), hbm)
    assert not np.array_equal(np.sort(addresses(t_h2)), np.sort(addresses(t_p2)))


def test_head_major_head_outer_kv_read_is_contiguous_per_head(tiny_model, hbm):
    trace, _ = generate(tiny_model, RunConfig(batch=1, seq_len=8, alignment=4096), hbm)
    kv_groups = [g for g in trace.groups if all(not s.write for s in g.segments)
                 and any(trace.tensors[s.tensor_id].kind == "kv" for s in g.segments)]
    assert kv_groups
    for s in kv_groups[0].segments:
        assert s.runs == 1 and s.outer_runs == 1  # one straight run per head


def test_alignment_no_overlap_within_capacity(tiny_model, hbm):
    for layout in KV_LAYOUTS:
        for order in KV_ORDERS:
            trace, stats = generate(
                tiny_model, RunConfig(batch=2, seq_len=5, alignment=4096, kv_layout=layout, kv_order=order,
                                      weight_streams=3, chunk_bytes=128, kv_chunk_bytes=64), hbm)
            ts = sorted(trace.tensors, key=lambda t: t.base)
            for a, b in zip(ts, ts[1:]):
                assert a.base + a.nbytes <= b.base, "tensors overlap"
            assert stats["footprint_bytes"] <= stats["capacity_bytes"]
            for addrs, _, tid in trace.iter_blocks():
                t = trace.tensors[tid]
                assert np.all(addrs % RB == 0)
                assert np.all(addrs >= t.base) and np.all(addrs + RB <= t.base + t.nbytes)


def test_layer_slice_reuses_full_run_addresses(tiny_model, hbm):
    full_t, full_s = generate(tiny_model, RunConfig(batch=1, seq_len=4, alignment=4096), hbm)
    slice_t, slice_s = generate(
        tiny_model, RunConfig(batch=1, seq_len=4, alignment=4096, layers=(1, 2), include_embed=False, include_lm_head=False), hbm)
    assert slice_t.tensors == full_t.tensors  # identical placement
    assert slice_s["bytes"]["embed"] == 0 and slice_s["shard"]["layers_emitted"] == 1
    assert set(addresses(slice_t).tolist()) <= set(addresses(full_t).tolist())


def test_tp_shard_halves_sharded_tensors(tiny_model, hbm):
    run1 = RunConfig(batch=2, seq_len=8, alignment=4096, tp=1)
    run2 = RunConfig(batch=2, seq_len=8, alignment=4096, tp=2)
    _, s1 = generate(tiny_model, run1, hbm)
    _, s2 = generate(tiny_model, run2, hbm)
    assert s2["bytes"]["weights"] * 2 == s1["bytes"]["weights"]
    assert s2["bytes"]["kv_read"] * 2 == s1["bytes"]["kv_read"]
    assert s2["bytes"]["norm"] == s1["bytes"]["norm"]  # replicated


def test_first_token_has_writes_but_no_kv_reads(tiny_model, hbm):
    _, s = generate(tiny_model, RunConfig(batch=1, seq_len=0, alignment=4096), hbm)
    assert s["bytes"]["kv_read"] == 0 and s["bytes"]["kv_write"] > 0


def test_capacity_error_names_the_fix(hbm):
    big = ModelConfig(name="big", num_hidden_layers=200, hidden_size=4096, num_attention_heads=32,
                      num_key_value_heads=8, head_dim=128, intermediate_size=14336, vocab_size=128256,
                      max_position_embeddings=8192, dtype_bytes=2)
    with pytest.raises(CapacityError, match="stacks="):
        generate(big, RunConfig(batch=1, seq_len=1), hbm)


def test_tp_must_divide_heads(tiny_model, hbm):
    with pytest.raises(ValueError, match="divisible"):
        generate(tiny_model, RunConfig(tp=3), hbm)
