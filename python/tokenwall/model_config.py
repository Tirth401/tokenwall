"""Model shape for the trace generator.

Numbers come from a published Hugging Face config.json, imported with
provenance by scripts/import_hf_config.py into configs/models/<name>.yaml.
Nothing here is typed from memory.
"""
from __future__ import annotations

import dataclasses
import pathlib
from dataclasses import dataclass

import yaml

DTYPE_BYTES = {"bfloat16": 2, "float16": 2, "float32": 4, "int8": 1, "float8_e4m3fn": 1}


@dataclass(frozen=True)
class ModelConfig:
    name: str
    num_hidden_layers: int
    hidden_size: int
    num_attention_heads: int
    num_key_value_heads: int
    head_dim: int
    intermediate_size: int
    vocab_size: int
    max_position_embeddings: int
    dtype_bytes: int
    tie_word_embeddings: bool = False
    source: str = ""

    def __post_init__(self) -> None:
        if self.num_attention_heads % self.num_key_value_heads:
            raise ValueError(
                f"{self.name}: num_attention_heads {self.num_attention_heads} must be a multiple of "
                f"num_key_value_heads {self.num_key_value_heads} (GQA shares one KV head across a group)"
            )
        if self.hidden_size != self.num_attention_heads * self.head_dim:
            raise ValueError(f"{self.name}: hidden_size must equal heads * head_dim")
        for f in ("num_hidden_layers", "hidden_size", "intermediate_size", "vocab_size", "dtype_bytes"):
            if getattr(self, f) <= 0:
                raise ValueError(f"{self.name}: {f} must be positive")

    @classmethod
    def from_yaml(cls, path: str | pathlib.Path) -> "ModelConfig":
        d = yaml.safe_load(pathlib.Path(path).read_text())
        f = d["fields"]
        return cls(name=d["name"], source=str(path), **f)

    def with_overrides(self, **kw) -> "ModelConfig":
        """Return a copy with fields replaced, e.g. num_key_value_heads=32 for full MHA."""
        return dataclasses.replace(self, **kw)

    # ---- element counts, unsharded ----
    @property
    def kv_dim(self) -> int:
        return self.num_key_value_heads * self.head_dim

    def per_layer_params(self) -> dict[str, int]:
        H, I, kv = self.hidden_size, self.intermediate_size, self.kv_dim
        return {
            "input_norm": H,
            "wq": H * H,
            "wk": H * kv,
            "wv": H * kv,
            "wo": H * H,
            "post_norm": H,
            "gate": H * I,
            "up": H * I,
            "down": I * H,
        }

    def total_params(self) -> int:
        H, V = self.hidden_size, self.vocab_size
        per_layer = sum(self.per_layer_params().values())
        lm_head = 0 if self.tie_word_embeddings else V * H
        return V * H + self.num_hidden_layers * per_layer + H + lm_head
