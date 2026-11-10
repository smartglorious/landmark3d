"""Versioned contract types for the Detail Engine (docs/DETAIL_ENGINE_V2_PLAN.md, section 6.3 and 8.1).

Pure data and validation. No file writes, no AI, no project state, no landmark names.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

PROVENANCE_KINDS = ("observed", "user_corrected", "symmetry", "style_inferred", "source_data", "default")
DEFAULT_CONFIDENCE_CAP = 0.5          # plan 8.2: a default may not receive a confidence above 0.5


def _finite(values) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class Bounds:
    lo: tuple
    hi: tuple

    def problems(self) -> list[str]:
        out = []
        if len(self.lo) != 3 or len(self.hi) != 3 or not _finite(self.lo + self.hi):
            out.append("bounds must be two finite 3-vectors")
        elif any(a > b for a, b in zip(self.lo, self.hi)):
            out.append("bounds lower corner is above the upper corner")
        return out


@dataclass(frozen=True)
class Provenance:
    kind: str
    confidence: float
    source: str | None = None         # the observation, rule or source record that supports it

    def problems(self) -> list[str]:
        out = []
        if self.kind not in PROVENANCE_KINDS:
            out.append(f"unknown provenance {self.kind!r}")
        if not isinstance(self.confidence, (int, float)) or not 0.0 <= self.confidence <= 1.0:
            out.append("confidence must be in [0, 1]")
            return out
        if self.kind == "default" and self.confidence > DEFAULT_CONFIDENCE_CAP:
            out.append(f"a default may not have confidence above {DEFAULT_CONFIDENCE_CAP}")
        if self.kind != "default" and not self.source:
            out.append(f"{self.kind} needs the source or rule that supports it")
        return out


@dataclass
class GeneratedComponent:
    """What every generator returns. Accounting is checked against the mesh itself."""
    mesh: object                       # geom.Mesh
    triangle_count: int
    material_slots: set = field(default_factory=set)
    bounds: Bounds | None = None
    anchors: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def problems(self) -> list[str]:
        out = []
        actual = sum(len(f) - 2 for f in self.mesh.faces)
        if self.triangle_count != actual:
            out.append(f"triangle_count {self.triangle_count} does not match the mesh ({actual})")
        if self.triangle_count < 0:
            out.append("triangle_count must not be negative")
        if self.bounds is not None:
            out += self.bounds.problems()
        if any(not _finite(vert) for vert in self.mesh.verts):
            out.append("mesh has non-finite vertices")
        return out


@dataclass
class DetailCandidate:
    id: str
    component_ids: list
    current_lod: int
    target_lod: int
    incremental_triangles: int
    incremental_bytes_estimate: int = 0
    incremental_materials: set = field(default_factory=set)
    utility: float = 0.0
    reasons: dict = field(default_factory=dict)

    def problems(self) -> list[str]:
        out = []
        if not self.id:
            out.append("candidate needs an id")
        if not (isinstance(self.current_lod, int) and isinstance(self.target_lod, int)
                and 0 <= self.current_lod < self.target_lod):
            out.append("target_lod must be above current_lod, both non-negative integers")
        if self.incremental_triangles < 0 or self.incremental_bytes_estimate < 0:
            out.append("incremental cost must not be negative")
        if not self.component_ids:
            out.append("candidate needs at least one component")
        return out
