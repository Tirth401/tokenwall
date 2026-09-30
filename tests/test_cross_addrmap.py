"""Python and C++ mappers agree field for field on the same addresses."""
import pathlib
import subprocess

import pytest

from tokenwall.addrmap import POLICY_NAMES, Geometry, export_ramulator_mapped, make_policy
from tokenwall.tracegen import RunConfig, generate

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "build" / "cpp" / "tw_expand"


@pytest.mark.parametrize("name", POLICY_NAMES)
@pytest.mark.parametrize("stacks,k", [(1, 0), (1, 3), (2, 0), (2, 5)])
def test_mapped_exports_identical(tmp_path, tiny_model, hbm, name, stacks, k):
    if not BIN.exists():
        pytest.fail("build/cpp/tw_expand missing; run: cmake -S . -B build && cmake --build build")
    trace, _ = generate(tiny_model, RunConfig(batch=2, seq_len=5, alignment=4096, weight_streams=2,
                                              chunk_bytes=64, kv_chunk_bytes=32), hbm)
    segs = tmp_path / "t.segs"
    trace.write(segs)
    geo = Geometry.from_hbm(stacks=stacks)
    export_ramulator_mapped(trace, tmp_path / "py.txt", make_policy(name, geo, k), geo)
    subprocess.run([str(BIN), str(segs), "--map", name, "--stacks", str(stacks), "--interleave-log2", str(k),
                    "--ramulator-out", str(tmp_path / "cpp.txt")], check=True, capture_output=True)
    assert (tmp_path / "py.txt").read_text() == (tmp_path / "cpp.txt").read_text()


def test_llama3_8b_prefix_mapped_identical(tmp_path, hbm):
    yaml_path = ROOT / "configs" / "models" / "llama3_8b.yaml"
    if not yaml_path.exists() or not BIN.exists():
        pytest.fail("needs configs/models/llama3_8b.yaml and build/cpp/tw_expand")
    from tokenwall.model_config import ModelConfig
    model = ModelConfig.from_yaml(yaml_path)
    trace, _ = generate(model, RunConfig(batch=1, seq_len=64, layers=(3, 4), include_embed=False,
                                         include_lm_head=False), hbm)
    segs = tmp_path / "l.segs"
    trace.write(segs)
    geo = Geometry.from_hbm(stacks=1)
    n = 200_000
    export_ramulator_mapped(trace, tmp_path / "py.txt", make_policy("bank_low_xor", geo, 3), geo, max_requests=n)
    subprocess.run([str(BIN), str(segs), "--map", "bank_low_xor", "--stacks", "1", "--interleave-log2", "3",
                    "--max-requests", str(n), "--ramulator-out", str(tmp_path / "cpp.txt")],
                   check=True, capture_output=True)
    assert (tmp_path / "py.txt").read_text() == (tmp_path / "cpp.txt").read_text()
