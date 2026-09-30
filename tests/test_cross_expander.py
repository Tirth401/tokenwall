"""Python and C++ expand the same .segs to the same request stream (hash and line-by-line).

A silent drift between the two expanders would poison every downstream number,
so this stays in the suite and fails loudly if the C++ tool is not built.
"""
import itertools
import pathlib
import subprocess

import pytest

from tokenwall.model_config import ModelConfig
from tokenwall.segments import export_ramulator, stream_hash
from tokenwall.tracegen import KV_LAYOUTS, KV_ORDERS, RunConfig, generate

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "build" / "cpp" / "tw_expand"


def tw_expand() -> pathlib.Path:
    if not BIN.exists():
        subprocess.run(["cmake", "--build", str(ROOT / "build"), "--target", "tw_expand"], capture_output=True)
    if not BIN.exists():
        pytest.fail("build/cpp/tw_expand missing; run: cmake -S . -B build && cmake --build build")
    return BIN


def run_cpp(segs: pathlib.Path, max_requests: int | None = None, ramulator_out: pathlib.Path | None = None) -> dict:
    cmd = [str(tw_expand()), str(segs)]
    if max_requests is not None:
        cmd += ["--max-requests", str(max_requests)]
    if ramulator_out is not None:
        cmd += ["--ramulator-out", str(ramulator_out)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    kv = dict(tok.split("=", 1) for tok in out.split())
    return {"requests": int(kv["requests"]), "writes": int(kv["writes"]), "hash": int(kv["hash"], 16)}


KNOBS = [(1, 4096, 0), (3, 128, 64), (2, 32, 32)]  # (weight_streams, chunk_bytes, kv_chunk_bytes)


@pytest.mark.parametrize("layout,order,knobs", list(itertools.product(KV_LAYOUTS, KV_ORDERS, KNOBS)))
def test_tiny_model_streams_identical(tmp_path, tiny_model, hbm, layout, order, knobs):
    streams, chunk, kv_chunk = knobs
    run = RunConfig(batch=2, seq_len=7, alignment=4096, kv_layout=layout, kv_order=order,
                    weight_streams=streams, chunk_bytes=chunk, kv_chunk_bytes=kv_chunk)
    trace, stats = generate(tiny_model, run, hbm)
    segs = tmp_path / "t.segs"
    trace.write(segs)

    py_hash, py_n, py_w = stream_hash(trace)
    cpp = run_cpp(segs, ramulator_out=tmp_path / "cpp.txt")
    assert cpp["requests"] == py_n == stats["requests"]["total"]
    assert cpp["writes"] == py_w
    assert cpp["hash"] == py_hash

    export_ramulator(trace, tmp_path / "py.txt")
    assert (tmp_path / "py.txt").read_text() == (tmp_path / "cpp.txt").read_text()


def test_llama3_8b_layer_slice_prefix_identical(tmp_path, hbm):
    yaml_path = ROOT / "configs" / "models" / "llama3_8b.yaml"
    if not yaml_path.exists():
        pytest.fail("configs/models/llama3_8b.yaml missing; run scripts/import_hf_config.py")
    model = ModelConfig.from_yaml(yaml_path)
    run = RunConfig(batch=2, seq_len=256, layers=(0, 1), include_embed=False, include_lm_head=False,
                    weight_streams=4, chunk_bytes=8192, kv_chunk_bytes=1024)
    trace, stats = generate(model, run, hbm)
    segs = tmp_path / "l8b.segs"
    trace.write(segs)

    n = 300_000
    py_hash, py_n, _ = stream_hash(trace, max_requests=n)
    cpp_prefix = run_cpp(segs, max_requests=n)
    assert (cpp_prefix["requests"], cpp_prefix["hash"]) == (py_n, py_hash)

    cpp_full = run_cpp(segs)  # C++ expands the whole layer in well under a second
    assert cpp_full["requests"] == stats["requests"]["total"]
    assert cpp_full["writes"] == stats["requests"]["write"]
