"""A flat address space in which every tensor gets a base address."""
from __future__ import annotations

from dataclasses import dataclass


def align_up(x: int, a: int) -> int:
    return (x + a - 1) // a * a


@dataclass(frozen=True)
class Tensor:
    id: int
    name: str
    kind: str  # weight | norm | embed | kv
    layer: int  # -1 when not part of a layer
    base: int
    nbytes: int

    @property
    def end(self) -> int:
        return self.base + self.nbytes


class AddressSpace:
    """Bump allocator. Tensors are placed in allocation order.

    Mirrors a caching allocator: big tensors start on a large-page boundary
    (2 MiB by default), small ones (norm weights) pack tightly on a 512 B one.
    """

    def __init__(self, alignment: int, small_alignment: int = 512):
        for a in (alignment, small_alignment):
            if a <= 0 or a & (a - 1):
                raise ValueError("alignments must be positive powers of two")
        if small_alignment > alignment:
            raise ValueError("small_alignment must not exceed alignment")
        self.alignment = alignment  # tensors at least this big are placed on this boundary
        self.small_alignment = small_alignment  # smaller tensors (norms) pack on this boundary
        self._cursor = 0
        self.tensors: list[Tensor] = []

    def alloc(self, name: str, kind: str, layer: int, nbytes: int) -> Tensor:
        if nbytes <= 0:
            raise ValueError(f"{name}: tensor must have positive size")
        base = align_up(self._cursor, self.alignment if nbytes >= self.alignment else self.small_alignment)
        t = Tensor(len(self.tensors), name, kind, layer, base, nbytes)
        self.tensors.append(t)
        self._cursor = base + nbytes
        return t

    @property
    def footprint(self) -> int:
        return align_up(self._cursor, self.alignment)
