"""Loading `fixtures/sensitive_paths.yaml` into validated, DB-independent definitions (phase 12,
stage 7). The same shape as `apps/ai_detection/rules.py`: the loader never touches the database, and
`seed_sensitive_paths` decides how to reconcile a definition with a stored row."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from apps.catalog.globs import compile_globs
from apps.policy.models import SensitivePathRule

DEFAULT_SENSITIVE_PATHS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "sensitive_paths.yaml"
)


class SensitivePathDefinitionError(Exception):
    pass


@dataclass(frozen=True)
class SensitivePathDefinition:
    glob: str
    risk_level: str
    description: str


def load_sensitive_path_definitions(
    path: Path = DEFAULT_SENSITIVE_PATHS_PATH,
) -> list[SensitivePathDefinition]:
    raw = yaml.safe_load(path.read_text()) or {}
    entries = raw.get("rules")
    if not isinstance(entries, list):
        raise SensitivePathDefinitionError(f"{path}: expected a top-level 'rules' list.")

    definitions = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SensitivePathDefinitionError(f"{path}: every rule must be a mapping.")
        glob = str(entry.get("glob") or "").strip()
        risk_level = str(entry.get("risk_level") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not glob:
            raise SensitivePathDefinitionError(f"{path}: a rule has no glob.")
        if glob in seen:
            raise SensitivePathDefinitionError(f"{path}: duplicate glob {glob!r}.")
        if risk_level not in SensitivePathRule.RiskLevel.values:
            raise SensitivePathDefinitionError(
                f"{path}: rule {glob!r} has an unknown risk level {risk_level!r}."
            )
        if not compile_globs([glob]):
            raise SensitivePathDefinitionError(f"{path}: rule {glob!r} is not a usable glob.")
        if not description:
            raise SensitivePathDefinitionError(
                f"{path}: rule {glob!r} has no description. State why the path is risky — a lead "
                f"reading a violation needs to know."
            )
        seen.add(glob)
        definitions.append(SensitivePathDefinition(glob=glob, risk_level=risk_level, description=description))
    return definitions
