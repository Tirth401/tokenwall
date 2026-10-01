"""End-to-end: the C++ simulator runs a generated trace and its counts match the generator."""
import json
import pathlib
import subprocess

import pytest

from tokenwall.tracegen import RunConfig, generate

ROOT = pathlib.Path(__file__).resolve().parents[1]
TW = ROOT / "build" / "cpp" / "tokenwall"
SPEC = ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.spec"


def run_sim(segs, out, *extra):
    if not TW.exists():
        pytest.fail("build/cpp/tokenwall missing; run: cmake -S . -B build && cmake --build build")
    subprocess.run([str(TW), "sim", "--segs", str(segs), "--spec", str(SPEC), "--policy", "bank_low", "--stacks", "1",
                    "--refresh", "none", "--drain", "--json", str(out), *extra], check=True, capture_output=True)
    return json.loads(out.read_text())["result"]


def test_sim_serves_every_request_and_classifies_rows(tmp_path, tiny_model, hbm):
    trace, stats = generate(tiny_model, RunConfig(batch=2, seq_len=8, alignment=4096), hbm)
    segs = tmp_path / "t.segs"
    trace.write(segs)
    res = run_sim(segs, tmp_path / "out.json")
    assert res["requests_served"] == stats["requests"]["total"]
    assert res["in_flight_at_end"] == 0
    assert res["row_hits"] + res["row_misses"] + res["row_conflicts"] == res["requests_served"]
    assert res["commands"]["RD"] == stats["requests"]["read"]
    assert res["commands"]["WR"] == stats["requests"]["write"]
    assert res["classes"]["kv_write"]["served"] == stats["requests"]["write"]


def test_64_byte_requests_become_two_accesses(tmp_path, hbm):
    from tokenwall.model_config import ModelConfig
    # head vectors must be whole requests: 64 B requests need head_dim * 2 B >= 64
    model = ModelConfig(name="tiny64", num_hidden_layers=2, hidden_size=128, num_attention_heads=4,
                        num_key_value_heads=2, head_dim=32, intermediate_size=128, vocab_size=64,
                        max_position_embeddings=128, dtype_bytes=2)
    base = dict(batch=1, seq_len=4, alignment=4096)
    t32, s32 = generate(model, RunConfig(request_bytes=32, **base), hbm)
    t64, s64 = generate(model, RunConfig(request_bytes=64, **base), hbm)
    assert s64["requests"]["total"] * 2 == s32["requests"]["total"]
    t64.write(tmp_path / "t64.segs")
    res = run_sim(tmp_path / "t64.segs", tmp_path / "out64.json")
    assert res["requests_served"] == s32["requests"]["total"]  # split into 32 B accesses
