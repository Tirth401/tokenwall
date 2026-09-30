"""The HBM3 facts the generator needs, read from the extracted Ramulator 2.1 YAML."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_HBM3_YAML = REPO_ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.yaml"


@dataclass(frozen=True)
class HBMConfig:
    access_bytes: int
    channels_per_stack: int
    stack_capacity_bytes: int
    peak_stack_GBps: float
    source: str

    @classmethod
    def from_yaml(cls, path: str | pathlib.Path = DEFAULT_HBM3_YAML) -> "HBMConfig":
        d = yaml.safe_load(pathlib.Path(path).read_text())
        org = d["organization"]
        return cls(
            access_bytes=int(org["access_bytes"]),
            channels_per_stack=int(org["jedec_channels_per_stack"]),
            stack_capacity_bytes=int(round(org["stack_capacity_GB"] * (1 << 30))),
            peak_stack_GBps=float(d["peak_bandwidth"]["per_stack_GBps"]),
            source=str(path),
        )
