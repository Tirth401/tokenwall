#!/usr/bin/env python
"""Extract the HBM3 parameters Tokenwall uses from Ramulator 2.1, with provenance.

Nothing in the output is typed by hand. Every number comes from
external/ramulator2/python/ramulator/dram/hbm3.py through Ramulator's own
resolve() logic. Timing keys tagged "estimate" sit inside a block that file
labels "Ramulator Guesstimate": the JEDEC HBM3 standard leaves those values to
vendor datasheets, so Ramulator's authors estimated them.

Usage (repo root, venv active):
    python scripts/extract_hbm3_params.py
Writes configs/hbm3/hbm3_16gb_8hi_6400.yaml
"""
from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys

import yaml

import ramulator
from ramulator.dram.hbm3 import HBM3

ROOT = pathlib.Path(__file__).resolve().parents[1]
R2 = ROOT / "external" / "ramulator2"
ORG_PRESET = "HBM3_16Gb_8hi"
TIMING_PRESET = "HBM3_6400Mbps"
JEDEC_CHANNELS_PER_STACK = 16  # JESD238: 16 channels, each split into 2 pseudo channels

# How hbm3.py derives the parameters that are not in the speed-bin preset.
DERIVED_SOURCE = {
    "nRC": "derived: nRAS + nRP",
    "nCCDL": "derived: max(4 CK, 2.5 ns)",
    "nCCDR": "derived: Ramulator estimate for 2 SIDs (marked Guesstimate in hbm3.py)",
    "nRTW": "derived: JESD238 Tables 92/93 note 18 formula",
    "nRFC": "derived: JESD238 Table 93 lookup for (16 Gb die, 8-high, 8 Gb channel)",
    "nRFCpb": "derived: JESD238 Table 93, 16 Gb die",
    "nRFMab": "derived: equals nRFC",
    "nRFMpb": "derived: equals nRFCpb",
    "nRREFD": "derived: max(3 CK, 8 ns)",
    "nREFI": "derived: 3.9 us / tCK",
    "nREFIpb": "derived: tREFI / banks per pseudo channel",
}


def preset_estimate_keys(src_text: str) -> set[str]:
    """Timing keys inside the 'Ramulator Guesstimate' block of HBM3.timing_presets."""
    block = src_text.split("HBM3.timing_presets = {", 1)[1]
    keys, inside = set(), False
    for line in block.splitlines():
        if "Ramulator Guesstimate" in line:
            inside = True
            continue
        if inside and re.match(r"\s*#\s*=+\s*$", line):
            inside = False
            continue
        if inside:
            keys.update(re.findall(r'"(\w+)"\s*:', line))
    return keys


def main() -> int:
    dram = ramulator.dram.HBM3(org_preset=ORG_PRESET, timing_preset=TIMING_PRESET)
    org, t = dram.resolve()  # CK units
    src = (R2 / "python" / "ramulator" / "dram" / "hbm3.py").read_text()
    estimate_keys = preset_estimate_keys(src)
    preset_keys = set(HBM3.timing_presets[TIMING_PRESET])
    tck_ps = t["tCK_ps"]

    def source_of(key: str) -> str:
        if key in estimate_keys:
            return "preset: Ramulator estimate (marked Guesstimate in hbm3.py)"
        if key in preset_keys:
            return "preset: speed-bin value in hbm3.py"
        return DERIVED_SOURCE.get(key, "derived")

    timings = {}
    for key in HBM3.timing_params:
        if key in ("rate", "tCK_ps"):
            continue
        timings[key] = {"ck": t[key], "ns": round(t[key] * tck_ps / 1000, 4), "source": source_of(key)}

    constraints = []
    for tc in HBM3.timing_constraints:
        entry = {
            "level": tc.level,
            "preceding": list(tc.preceding),
            "following": list(tc.following),
            "latency": tc.latency,
            "ck": HBM3._eval_expr(tc.latency, t),
        }
        if tc.window != 1:
            entry["window"] = tc.window
        if tc.sibling:
            entry["sibling"] = True
        if tc.shared_window:
            entry["shared_window"] = True
        constraints.append(entry)

    commit = subprocess.check_output(["git", "-C", str(R2), "rev-parse", "HEAD"], text=True).strip()
    access_bytes = HBM3.data_payload_bytes
    peak_pc_gbps = access_bytes / (t["nBL"] * tck_ps * 1e-12) / 1e9
    pcs = org["pseudochannel"]
    banks_per_pc = org["sid"] * org["bankgroup"] * org["bank"]

    doc = {
        "provenance": {
            "source_file": "external/ramulator2/python/ramulator/dram/hbm3.py",
            "ramulator2_commit": commit,
            "org_preset": ORG_PRESET,
            "timing_preset": TIMING_PRESET,
            "extracted_on": datetime.date.today().isoformat(),
            "command": "python scripts/extract_hbm3_params.py",
            "honesty_note": (
                "Keys whose source says 'estimate' are Ramulator's guesses, not JEDEC-published "
                "values. Treat the resulting bandwidth numbers as 'HBM3 per Ramulator 2.1 preset'."
            ),
        },
        "clock": {
            "data_rate_MTps": t["rate"],
            "tCK_ps": tck_ps,
            "ck_GHz": round(1e3 / tck_ps, 4),
            "beats_per_ck": round(t["rate"] * tck_ps / 1e6, 3),
        },
        "ramulator_tick": {
            "ticks_per_ck": HBM3.tick_multiplier,
            "tick_ps": tck_ps / HBM3.tick_multiplier,
            "note": "Ramulator simulates HBM3 in half-CK ticks because row commands occupy 0.5 or 1.5 CK on the command bus.",
        },
        "organization": {
            "pseudo_channels_per_channel": pcs,
            "sids": org["sid"],
            "bank_groups": org["bankgroup"],
            "banks_per_group": org["bank"],
            "banks_per_pseudo_channel": banks_per_pc,
            "rows_per_bank": org["row"],
            "columns_per_row": org["column"],
            "internal_prefetch": HBM3.internal_prefetch_size,
            "dq_bits_per_pseudo_channel": org["dq"],
            "channel_width_bits": org["channel_width"],
            "access_bytes": access_bytes,
            "row_bytes_per_pseudo_channel": org["column"] * org["dq"] // 8,
            "accesses_per_row": org["column"] // HBM3.internal_prefetch_size,
            "die_density_Mb": org["die_density"],
            "stack_height": org["stack_height"],
            "channel_density_Mb": org["channel_density"],
            "channel_capacity_GB": org["channel_density"] / 8 / 1024,
            "jedec_channels_per_stack": JEDEC_CHANNELS_PER_STACK,
            "stack_capacity_GB": org["channel_density"] / 8 / 1024 * JEDEC_CHANNELS_PER_STACK,
        },
        "read_latency_ck": t["nCL"] + t["nBL"],
        "timings_ck": timings,
        "peak_bandwidth": {
            "formula": "access_bytes / (nBL * tCK)",
            "per_pseudo_channel_GBps": round(peak_pc_gbps, 3),
            "per_channel_GBps": round(peak_pc_gbps * pcs, 3),
            "per_stack_GBps": round(peak_pc_gbps * pcs * JEDEC_CHANNELS_PER_STACK, 3),
        },
        "timing_constraints": constraints,
    }

    out = ROOT / "configs" / "hbm3" / "hbm3_16gb_8hi_6400.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# GENERATED by scripts/extract_hbm3_params.py from Ramulator 2.1. Do not edit by hand.\n"
        + yaml.safe_dump(doc, sort_keys=False, width=110)
    )
    print(f"wrote {out.relative_to(ROOT)}: {len(timings)} timings, {len(constraints)} constraints")
    print(f"estimate-tagged preset keys: {sorted(estimate_keys)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
