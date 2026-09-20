"""The settings and compiled globs the policy evaluators read (phase 12, stage 7).

Loaded once per run and handed to every evaluation, for the same reason `DisclosureConfig` exists:
`evaluate_pull_requests` walks thousands of pull requests, and reading a dozen `AppSetting` rows
and recompiling a dozen glob lists per pull request would dominate the work.

Everything here is operator-owned data, not code. A client's PR template will not use PR Radar's
headings, their tracker is not ours, and their repository layout is their own — so the headings, the
task-link patterns and every path glob are settings, and the defaults are only a starting point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from apps.catalog.globs import compile_globs
from apps.catalog.services import get_list


@dataclass(frozen=True)
class PolicyConfig:
    risk_headings: tuple[str, ...] = ()
    verification_headings: tuple[str, ...] = ()
    task_headings: tuple[str, ...] = ()
    plan_headings: tuple[str, ...] = ()
    scope_headings: tuple[str, ...] = ()
    task_link_patterns: tuple[re.Pattern[str], ...] = ()
    skip_ci_markers: tuple[str, ...] = ()
    manifest_globs: tuple[Any, ...] = ()
    migration_globs: tuple[Any, ...] = ()
    agent_config_globs: tuple[Any, ...] = ()
    secret_artifact_globs: tuple[Any, ...] = ()
    secret_artifact_exception_globs: tuple[Any, ...] = field(default=())


def _patterns(raw: list[str]) -> tuple[re.Pattern[str], ...]:
    """Compiles the task-link patterns, skipping (and reporting) one that does not compile rather
    than failing the whole run: a typo in one setting must not stop every other check."""
    compiled = []
    for pattern in raw:
        try:
            compiled.append(re.compile(str(pattern), re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
    return tuple(compiled)


def load_policy_config() -> PolicyConfig:
    return PolicyConfig(
        risk_headings=tuple(get_list("POLICY_RISK_SECTION_HEADINGS")),
        verification_headings=tuple(get_list("POLICY_VERIFICATION_SECTION_HEADINGS")),
        task_headings=tuple(get_list("POLICY_TASK_SECTION_HEADINGS")),
        plan_headings=tuple(get_list("POLICY_PLAN_SECTION_HEADINGS")),
        scope_headings=tuple(get_list("POLICY_SCOPE_SECTION_HEADINGS")),
        task_link_patterns=_patterns(get_list("POLICY_TASK_LINK_PATTERNS")),
        skip_ci_markers=tuple(str(marker) for marker in get_list("POLICY_SKIP_CI_MARKERS")),
        manifest_globs=tuple(compile_globs(get_list("POLICY_MANIFEST_PATH_GLOBS"))),
        migration_globs=tuple(compile_globs(get_list("POLICY_MIGRATION_PATH_GLOBS"))),
        agent_config_globs=tuple(compile_globs(get_list("POLICY_AGENT_CONFIG_PATH_GLOBS"))),
        secret_artifact_globs=tuple(compile_globs(get_list("POLICY_SECRET_ARTIFACT_PATH_GLOBS"))),
        secret_artifact_exception_globs=tuple(
            compile_globs(get_list("POLICY_SECRET_ARTIFACT_EXCEPTION_GLOBS"))
        ),
    )
