"""Turn a model shape into the request stream of one decode step.

Decode-step traffic per GPU (one tensor-parallel shard), in issue order:
  embedding rows for the batch,
  per layer: input norm, Wq, Wk, Wv, KV-cache reads for all past positions,
             KV append writes, Wo, post-attention norm, gate, up, down,
  final norm, lm_head.
Weights are read once per step regardless of batch size (that is what batching
buys). KV-cache traffic scales with batch * past positions * KV heads.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .hbm_config import HBMConfig
from .layout import AddressSpace, Tensor
from .model_config import ModelConfig
from .segments import Group, Segment, SegmentTrace, TensorInfo

KV_LAYOUTS = ("head_major", "position_major")
KV_ORDERS = ("head_outer", "position_outer")


class CapacityError(ValueError):
    pass


@dataclass(frozen=True)
class RunConfig:
    batch: int = 1
    seq_len: int = 1024  # past positions already in the KV cache
    tp: int = 1  # tensor-parallel degree; we simulate one shard (one GPU)
    stacks: int = 1  # HBM3 stacks on this GPU
    kv_layout: str = "head_major"  # [batch][kv_head][position][head_dim]  (HF style)
    kv_order: str = "head_outer"  # attention kernel walks one KV head over all positions
    kv_capacity: int | None = None  # positions allocated per sequence; default seq_len + 1
    request_bytes: int = 32
    alignment: int = 2 << 20  # 2 MiB, like a CUDA allocator's large pages
    small_alignment: int = 512  # packing boundary for tensors smaller than `alignment`
    weight_streams: int = 1  # split each weight sweep into N interleaved streams
    chunk_bytes: int = 4096  # round-robin granularity for weight streams
    kv_chunk_bytes: int = 0  # 0: heads read one after another; >0: interleave heads
    layers: tuple[int, int] | None = None  # slice [a, b) of layers, for validation runs
    include_embed: bool = True
    include_lm_head: bool = True
    seed: int = 1


def _token_id(b: int, vocab_shard: int, seed: int) -> int:
    # Placeholder for data-dependent token ids; embedding traffic is B rows, negligible.
    return (b * 7919 + 104729 + seed * 31) % vocab_shard


def _validate(model: ModelConfig, run: RunConfig, hbm: HBMConfig) -> None:
    rb = run.request_bytes
    if rb <= 0 or rb & (rb - 1) or rb % hbm.access_bytes:
        raise ValueError(f"request_bytes must be a power-of-two multiple of the HBM access size {hbm.access_bytes}")
    if run.batch <= 0 or run.seq_len < 0 or run.tp <= 0 or run.stacks <= 0:
        raise ValueError("batch, tp and stacks must be positive; seq_len non-negative")
    if run.kv_layout not in KV_LAYOUTS or run.kv_order not in KV_ORDERS:
        raise ValueError(f"kv_layout in {KV_LAYOUTS}, kv_order in {KV_ORDERS}")
    for name, v in (("num_attention_heads", model.num_attention_heads),
                    ("num_key_value_heads", model.num_key_value_heads),
                    ("intermediate_size", model.intermediate_size),
                    ("vocab_size", model.vocab_size)):
        if v % run.tp:
            raise ValueError(f"{name}={v} is not divisible by tp={run.tp}")
    dt = model.dtype_bytes
    if (model.hidden_size * dt) % rb or (model.head_dim * dt) % rb:
        raise ValueError("hidden and head vectors must be whole requests")
    if run.alignment % rb or run.small_alignment % rb:
        raise ValueError("alignments must be multiples of request_bytes")
    if run.weight_streams <= 0 or run.chunk_bytes % rb or run.kv_chunk_bytes % rb:
        raise ValueError("weight_streams positive; chunk sizes multiples of request_bytes")
    if run.layers is not None:
        a, b = run.layers
        if not (0 <= a < b <= model.num_hidden_layers):
            raise ValueError(f"layers slice {run.layers} outside [0, {model.num_hidden_layers})")


def generate(model: ModelConfig, run: RunConfig, hbm: HBMConfig) -> tuple[SegmentTrace, dict]:
    """Return (segment trace, stats dict) for one decode step of one shard."""
    _validate(model, run, hbm)
    rb, dt = run.request_bytes, model.dtype_bytes
    L, H, D = model.num_hidden_layers, model.hidden_size, model.head_dim
    heads_l = model.num_attention_heads // run.tp
    kv_l = model.num_key_value_heads // run.tp
    q_dim, kv_dim = heads_l * D, kv_l * D
    I_l = model.intermediate_size // run.tp
    V_l = model.vocab_size // run.tp
    B, S = run.batch, run.seq_len
    S_cap = run.kv_capacity if run.kv_capacity is not None else S + 1
    if S_cap < S + 1:
        raise ValueError("kv_capacity must be at least seq_len + 1 (room for the appended position)")
    vec = D * dt  # bytes of one head vector for one position

    # ---- placement: weights in load order, then KV caches (allocated at serving start) ----
    space = AddressSpace(run.alignment, run.small_alignment)
    embed = space.alloc("embed_tokens", "embed", -1, V_l * H * dt)
    layer_w: list[dict[str, Tensor]] = []
    for l in range(L):
        layer_w.append({
            "input_norm": space.alloc(f"layers.{l}.input_norm", "norm", l, H * dt),
            "wq": space.alloc(f"layers.{l}.wq", "weight", l, H * q_dim * dt),
            "wk": space.alloc(f"layers.{l}.wk", "weight", l, H * kv_dim * dt),
            "wv": space.alloc(f"layers.{l}.wv", "weight", l, H * kv_dim * dt),
            "wo": space.alloc(f"layers.{l}.wo", "weight", l, q_dim * H * dt),
            "post_norm": space.alloc(f"layers.{l}.post_norm", "norm", l, H * dt),
            "gate": space.alloc(f"layers.{l}.gate", "weight", l, H * I_l * dt),
            "up": space.alloc(f"layers.{l}.up", "weight", l, H * I_l * dt),
            "down": space.alloc(f"layers.{l}.down", "weight", l, I_l * H * dt),
        })
    final_norm = space.alloc("final_norm", "norm", -1, H * dt)
    lm_head = embed if model.tie_word_embeddings else space.alloc("lm_head", "weight", -1, V_l * H * dt)
    kv_t: list[tuple[Tensor, Tensor]] = []
    for l in range(L):
        kbytes = B * kv_l * S_cap * vec
        kv_t.append((space.alloc(f"layers.{l}.k_cache", "kv", l, kbytes),
                     space.alloc(f"layers.{l}.v_cache", "kv", l, kbytes)))

    footprint = space.footprint
    capacity = run.stacks * hbm.stack_capacity_bytes
    if footprint > capacity:
        need = -(-footprint // hbm.stack_capacity_bytes)
        raise CapacityError(
            f"footprint {footprint / 2**30:.2f} GiB exceeds {run.stacks} stack(s) = {capacity / 2**30:.2f} GiB; "
            f"needs stacks={need} (or a larger tp)"
        )

    # ---- issue order ----
    groups: list[Group] = []

    def sweep(t: Tensor) -> None:
        n_req = t.nbytes // rb
        if run.weight_streams <= 1 or n_req < run.weight_streams:
            groups.append(Group(0, [Segment(False, t.base, t.nbytes, tensor_id=t.id)]))
            return
        per = n_req // run.weight_streams
        segs, start = [], 0
        for k in range(run.weight_streams):
            cnt = per if k < run.weight_streams - 1 else n_req - start
            segs.append(Segment(False, t.base + start * rb, cnt * rb, tensor_id=t.id))
            start += cnt
        groups.append(Group(run.chunk_bytes, segs))

    def kv_addr(t: Tensor, b: int, h: int, s: int) -> int:
        if run.kv_layout == "head_major":
            return t.base + ((b * kv_l + h) * S_cap + s) * vec
        return t.base + ((b * S_cap + s) * kv_l + h) * vec  # position_major

    def kv_reads(t: Tensor) -> list[Segment]:
        if S == 0:
            return []
        out: list[Segment] = []
        for b in range(B):
            if run.kv_layout == "head_major" and run.kv_order == "head_outer":
                out += [Segment(False, kv_addr(t, b, h, 0), S * vec, tensor_id=t.id) for h in range(kv_l)]
            elif run.kv_layout == "head_major":  # position_outer over a head-major layout: strided
                out.append(Segment(False, kv_addr(t, b, 0, 0), vec, runs=kv_l, stride=S_cap * vec,
                                   outer_runs=S, outer_stride=vec, tensor_id=t.id))
            elif run.kv_order == "position_outer":  # position_major: one contiguous block per sequence
                out.append(Segment(False, kv_addr(t, b, 0, 0), S * kv_l * vec, tensor_id=t.id))
            else:  # position_major layout walked head by head: stride between positions
                out += [Segment(False, kv_addr(t, b, h, 0), vec, runs=S, stride=kv_l * vec, tensor_id=t.id)
                        for h in range(kv_l)]
        return out

    def kv_writes(t: Tensor) -> list[Segment]:
        out: list[Segment] = []
        for b in range(B):
            if run.kv_layout == "head_major":
                out.append(Segment(True, kv_addr(t, b, 0, S), vec, runs=kv_l, stride=S_cap * vec, tensor_id=t.id))
            else:
                out.append(Segment(True, kv_addr(t, b, 0, S), kv_l * vec, tensor_id=t.id))
        return out

    a, z = run.layers if run.layers is not None else (0, L)
    if run.include_embed:
        groups.append(Group(0, [Segment(False, embed.base + _token_id(b, V_l, run.seed) * H * dt, H * dt,
                                        tensor_id=embed.id) for b in range(B)]))
    for l in range(a, z):
        w, (k, v) = layer_w[l], kv_t[l]
        sweep(w["input_norm"]); sweep(w["wq"]); sweep(w["wk"]); sweep(w["wv"])
        reads = kv_reads(k) + kv_reads(v)
        if reads:
            groups.append(Group(run.kv_chunk_bytes, reads))
        groups.append(Group(0, kv_writes(k) + kv_writes(v)))
        sweep(w["wo"]); sweep(w["post_norm"]); sweep(w["gate"]); sweep(w["up"]); sweep(w["down"])
    if run.include_lm_head:
        sweep(final_norm)
        sweep(lm_head)

    trace = SegmentTrace(
        request_bytes=rb,
        groups=groups,
        tensors=[TensorInfo(t.id, t.kind, t.layer, t.base, t.nbytes, t.name) for t in space.tensors],
    )
    trace.validate()

    # ---- accounting ----
    kind_of = {t.id: t.kind for t in space.tensors}
    by_class = {"weights": 0, "norm": 0, "embed": 0, "kv_read": 0, "kv_write": 0}
    for g in groups:
        for s in g.segments:
            kind = kind_of[s.tensor_id]
            if kind == "kv":
                by_class["kv_write" if s.write else "kv_read"] += s.nbytes()
            elif kind == "weight":
                by_class["weights"] += s.nbytes()
            else:
                by_class[kind] += s.nbytes()
    total_read = sum(v for k, v in by_class.items() if k != "kv_write")
    total = total_read + by_class["kv_write"]
    peak_GBps = run.stacks * hbm.peak_stack_GBps
    stats = {
        "model": model.name,
        "model_source": model.source,
        "run": {**dataclasses.asdict(run), "layers": list(run.layers) if run.layers else None},
        "shard": {"heads": heads_l, "kv_heads": kv_l, "intermediate": I_l, "vocab": V_l,
                  "kv_capacity_positions": S_cap, "layers_emitted": z - a},
        "footprint_bytes": footprint,
        "capacity_bytes": capacity,
        "capacity_utilization": footprint / capacity,
        "bytes": {**by_class, "total_read": total_read, "total_write": by_class["kv_write"], "total": total},
        "requests": {"total": trace.num_requests(), "write": trace.num_writes(),
                     "read": trace.num_requests() - trace.num_writes()},
        "segments": sum(len(g.segments) for g in groups),
        "groups": len(groups),
        "derived": {
            "peak_GBps_ramulator_preset": peak_GBps,
            "time_at_peak_ms": total / (peak_GBps * 1e9) * 1e3,
            "note": "time_at_peak is a floor: every byte at 100% of HBM3 peak per Ramulator 2.1's preset",
        },
        "hbm_source": hbm.source,
    }
    return trace, stats
