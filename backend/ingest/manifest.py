"""Load and validate a manual set's manifest.yaml (and optional overrides.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROLES = {"manual", "engineering_reference", "cad"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class DocSpec:
    id: str
    file: str
    role: str
    title: str | None = None
    applies_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlwaysInclude:
    doc: str
    section_prefix: str


@dataclass(frozen=True)
class Manifest:
    id: str
    set_dir: Path
    equipment: dict
    documents: tuple[DocSpec, ...]
    always_include_sections: tuple[AlwaysInclude, ...] = ()
    overrides: dict = field(default_factory=dict)

    def path(self, doc: DocSpec) -> Path:
        return self.set_dir / doc.file

    def figure_override(self, figure_id: str) -> dict:
        return (self.overrides.get("figures") or {}).get(figure_id, {})


def load_manifest(set_dir: Path) -> Manifest:
    set_dir = Path(set_dir)
    raw = yaml.safe_load((set_dir / "manifest.yaml").read_text())
    docs: list[DocSpec] = []
    seen: set[str] = set()
    for d in raw.get("documents", []):
        spec = DocSpec(id=d["id"], file=d["file"], role=d["role"], title=d.get("title"),
                       applies_to=tuple(d.get("applies_to", [])))
        if spec.role not in ROLES:
            raise ManifestError(f"{spec.id}: unknown role {spec.role!r}")
        if spec.id in seen:
            raise ManifestError(f"duplicate document id {spec.id!r}")
        if not (set_dir / spec.file).is_file():
            raise ManifestError(f"{spec.id}: file not found: {spec.file}")
        seen.add(spec.id)
        docs.append(spec)
    always = tuple(AlwaysInclude(a["doc"], a["section_prefix"]) for a in raw.get("always_include_sections", []))
    for a in always:
        if a.doc not in seen:
            raise ManifestError(f"always_include_sections references unknown doc {a.doc!r}")
    ov_path = set_dir / "overrides.yaml"
    overrides = (yaml.safe_load(ov_path.read_text()) or {}) if ov_path.is_file() else {}
    return Manifest(id=raw["id"], set_dir=set_dir, equipment=raw.get("equipment", {}),
                    documents=tuple(docs), always_include_sections=always, overrides=overrides)
