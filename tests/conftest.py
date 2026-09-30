import pathlib

import pytest

from tokenwall.hbm_config import HBMConfig
from tokenwall.model_config import ModelConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def hbm() -> HBMConfig:
    return HBMConfig.from_yaml()


@pytest.fixture
def tiny_model() -> ModelConfig:
    # Small enough to expand and hash in Python in well under a second.
    return ModelConfig(
        name="tiny", num_hidden_layers=2, hidden_size=64, num_attention_heads=4,
        num_key_value_heads=2, head_dim=16, intermediate_size=128, vocab_size=64,
        max_position_embeddings=128, dtype_bytes=2,
    )
