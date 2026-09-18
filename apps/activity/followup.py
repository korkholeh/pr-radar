"""The `followup_fix_rate` heuristic (spec §8.2): whether a merged PR was followed, within a
window, by a hotfix/fix PR touching a large-enough share of the same files. Materialised into
`PullRequest.has_followup_fix` at sync time so the metric's rollup batch stays a grouped `COUNT`
instead of a `PRFile`x`PRFile` self-join (RISKS row 10)."""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable

from django.db.models import QuerySet

from apps.activity.models import PRFile, PullRequest
from apps.catalog.services import get_float, get_int

FOLLOWUP_FIELD = "has_followup_fix"


def _non_excluded_paths(pull_request_id: int) -> set[str]:
    return set(
        PRFile.objects.filter(pull_request_id=pull_request_id, is_excluded=False).values_list(
            "path", flat=True
        )
    )


def _paths_by_pull_request(pull_request_ids: Iterable[int]) -> dict[int, set[str]]:
    """One grouped query in place of one `_non_excluded_paths()` call per PR -- the batched
    counterpart used by `update_followup_fixes`'s cascade over `originals`, where a per-PR query
    is quadratic in the repository's PR volume."""
    paths: dict[int, set[str]] = defaultdict(set)
    rows = PRFile.objects.filter(pull_request_id__in=list(pull_request_ids), is_excluded=False).values_list(
        "pull_request_id", "path"
    )
    for pull_request_id, path in rows:
        paths[pull_request_id].add(path)
    return paths


def compute_has_followup_fix(pr: PullRequest) -> bool:
    """True when a merged PR in the same repository, `is_hotfix`, merged in
    (pr.merged_at, pr.merged_at + FOLLOWUP_FIX_WINDOW_DAYS], shares at least
    FOLLOWUP_FIX_FILE_OVERLAP of `pr`'s non-excluded file paths."""
    if pr.state != PullRequest.State.MERGED or pr.merged_at is None:
        return False

    paths_a = _non_excluded_paths(pr.pk)
    if not paths_a:
        return False

    window = datetime.timedelta(days=get_int("FOLLOWUP_FIX_WINDOW_DAYS"))
    overlap_threshold = get_float("FOLLOWUP_FIX_FILE_OVERLAP")

    candidates = PullRequest.objects.filter(
        repository_id=pr.repository_id,
        state=PullRequest.State.MERGED,
        is_hotfix=True,
        merged_at__gt=pr.merged_at,
        merged_at__lte=pr.merged_at + window,
    ).exclude(pk=pr.pk)

    for candidate_id in candidates.values_list("pk", flat=True):
        paths_b = _non_excluded_paths(candidate_id)
        overlap = len(paths_a & paths_b) / len(paths_a)
        if overlap >= overlap_threshold:
            return True
    return False


def _recompute_and_save(pr: PullRequest) -> bool:
    new_value = compute_has_followup_fix(pr)
    if pr.has_followup_fix == new_value:
        return False
    pr.has_followup_fix = new_value
    pr.save(update_fields=["has_followup_fix"])
    return True


def update_followup_fixes(pull_request_id: int) -> set[int]:
    """Idempotently recomputes the flag for `pull_request_id` itself *and* for every PR in the
    same repository merged in [pr.merged_at - window, pr.merged_at) -- the PRs this one could
    newly be a follow-up fix for. Returns the ids whose flag actually changed.

    The cascade over `originals` is batched: one query for the originals, one for every merged
    `is_hotfix` PR that could be any of their follow-up fixes, and one grouped `PRFile` query for
    all of their paths -- instead of `compute_has_followup_fix()`'s one-query-per-candidate,
    called once per original (quadratic in the repository's PR volume for a busy sync)."""
    pr = PullRequest.objects.get(pk=pull_request_id)
    changed: set[int] = set()

    if _recompute_and_save(pr):
        changed.add(pr.pk)

    if pr.merged_at is None:
        return changed

    window = datetime.timedelta(days=get_int("FOLLOWUP_FIX_WINDOW_DAYS"))
    originals = list(
        PullRequest.objects.filter(
            repository_id=pr.repository_id,
            state=PullRequest.State.MERGED,
            merged_at__gte=pr.merged_at - window,
            merged_at__lt=pr.merged_at,
        ).exclude(pk=pr.pk)
    )
    if not originals:
        return changed

    earliest_merge = min(original.merged_at for original in originals)
    candidates = list(
        PullRequest.objects.filter(
            repository_id=pr.repository_id,
            state=PullRequest.State.MERGED,
            is_hotfix=True,
            merged_at__gt=earliest_merge,
            merged_at__lte=pr.merged_at + window,
        )
    )
    paths = _paths_by_pull_request(
        [original.pk for original in originals] + [candidate.pk for candidate in candidates]
    )
    overlap_threshold = get_float("FOLLOWUP_FIX_FILE_OVERLAP")

    for original in originals:
        paths_a = paths.get(original.pk, set())
        is_followup = False
        if paths_a:
            for candidate in candidates:
                if candidate.pk == original.pk:
                    continue
                if not (original.merged_at < candidate.merged_at <= original.merged_at + window):
                    continue
                paths_b = paths.get(candidate.pk, set())
                if len(paths_a & paths_b) / len(paths_a) >= overlap_threshold:
                    is_followup = True
                    break
        if original.has_followup_fix != is_followup:
            original.has_followup_fix = is_followup
            original.save(update_fields=["has_followup_fix"])
            changed.add(original.pk)

    return changed


def update_followup_fixes_for(queryset: QuerySet[PullRequest]) -> int:
    """`recompute`'s batch entry point; same function, no per-PR pipeline overhead."""
    count = 0
    for pr in queryset.iterator():
        _recompute_and_save(pr)
        count += 1
    return count
