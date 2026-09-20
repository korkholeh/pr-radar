import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

# Cheapest useful guard against catastrophic backtracking (spec/RISKS row 5's "wrong in both
# directions" also covers "never finishes"): a quantified group nested directly inside another
# quantifier, e.g. `(a+)+` or `(a*)*`, is the classic ReDoS shape. This is a heuristic, not a
# parser — it does not catch every pathological pattern (deeper nesting, alternation-based blowup)
# — but it rejects the shape an admin is most likely to type by accident, at the point of entry,
# before the pattern ever reaches `re.search` on a PR body. A durable fix would run matching under
# a timeout (e.g. the `regex` module's `timeout=`); deferred as this phase makes no live regex
# call outside the request/task thread that already has to finish quickly.
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*]")


class Detector(models.TextChoices):
    COMMIT_TRAILER = "commit_trailer", _("Commit trailer")
    COMMIT_AUTHOR = "commit_author", _("Commit author")
    PR_AUTHOR = "pr_author", _("PR author")
    PR_BODY_FOOTER = "pr_body_footer", _("PR body footer")
    HTML_COMMENT = "html_comment", _("HTML comment")
    LABEL = "label", _("Label")
    BRANCH_PATTERN = "branch_pattern", _("Branch pattern")
    COMMIT_MESSAGE = "commit_message", _("Commit message")
    FILE_PATH = "file_path", _("File path")
    PR_TITLE = "pr_title", _("PR title")
    REVIEWER_IDENTITY = "reviewer_identity", _("Reviewer identity")
    MERGED_BY_IDENTITY = "merged_by_identity", _("Merged-by identity")


class Tool(models.TextChoices):
    CLAUDE_CODE = "claude_code", _("Claude Code")
    COPILOT = "copilot", _("Copilot")
    CURSOR = "cursor", _("Cursor")
    CODEX = "codex", _("Codex")
    DEVIN = "devin", _("Devin")
    GEMINI = "gemini", _("Gemini")
    AIDER = "aider", _("Aider")
    WINDSURF = "windsurf", _("Windsurf")
    CHATGPT = "chatgpt", _("ChatGPT")
    CHARLIE = "charlie", _("Charlie")
    OTHER = "other", _("Other")


class Confidence(models.TextChoices):
    HIGH = "high", _("High")
    MEDIUM = "medium", _("Medium")
    LOW = "low", _("Low")


class DetectionRule(models.Model):
    name = models.CharField(_("name"), max_length=200, unique=True)
    detector = models.CharField(_("detector"), max_length=20, choices=Detector.choices)
    pattern = models.CharField(_("pattern"), max_length=500)
    tool = models.CharField(_("tool"), max_length=20, choices=Tool.choices)
    confidence = models.CharField(_("confidence"), max_length=10, choices=Confidence.choices)
    is_active = models.BooleanField(_("is active"), default=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("detection rule")
        verbose_name_plural = _("detection rules")
        indexes = [models.Index(fields=["is_active", "detector"])]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValidationError(
                {"pattern": _("Not a valid regular expression: %(error)s") % {"error": exc}}
            ) from exc
        if _NESTED_QUANTIFIER_RE.search(self.pattern):
            raise ValidationError(
                {
                    "pattern": _(
                        "This pattern nests a repeated group inside another repetition "
                        "(e.g. (a+)+), which can hang on a normal PR body. Rewrite it "
                        "without the nested quantifier."
                    )
                }
            )


class SignalKind(models.TextChoices):
    """The structural heuristics (phase 12). Unlike a `Detector`, a kind is not a regex over text:
    it is a named piece of code that reads a pull request's own shape — how fast it arrived, how
    its commits are spaced, how many files it created. Each one is tuned by a `SignalRule.params`
    dict rather than by a pattern."""

    FAST_LARGE_PR = "fast_large_pr", _("Large change delivered very fast")
    COMMIT_BURST = "commit_burst", _("Burst of near-simultaneous substantial commits")
    SINGLE_LARGE_COMMIT = "single_large_commit", _("Whole change in one large commit")
    MASS_FILE_CREATION = "mass_file_creation", _("Many files created across many directories")
    UNUSED_NEW_DEPENDENCY = "unused_new_dependency", _("New dependency nothing in the diff uses")
    INSTANT_REVIEW_RESPONSE = "instant_review_response", _("Repeated commits moments after review comments")
    # Baseline kinds (phase 12, stage 5). These read an author's *own history* rather than one
    # pull request, so their truth changes when other pull requests arrive — which is why they are
    # recomputed nightly over a rolling window instead of during one PR's sync.
    THROUGHPUT_SHIFT = "throughput_shift", _("Throughput jumped against the author's own history")
    OFF_HOURS_VOLUME = "off_hours_volume", _("Volume moved outside the author's usual hours")
    TEST_RATIO_LOCKSTEP = "test_ratio_lockstep", _("Test-to-code ratio barely varies across pull requests")
    BODY_STYLE_SHIFT = "body_style_shift", _("Pull-request descriptions broke from the author's own style")


class SignalFamily(models.TextChoices):
    """Where a kind's inputs come from, which decides where it runs and what may delete its rows.

    The split is forced, not stylistic. A `BASELINE` kind's verdict changes when *other* pull
    requests arrive, so computing it during one PR's sync would freeze a stale answer. A `DIFF`
    kind needs file contents that are not in the database at all, and the only place those bytes
    exist locally is the churn clone.

    It also keeps the three writers from deleting each other's work: each reconciles only the
    signals of its own family, so a nightly baseline run cannot wipe the per-PR signals the sync
    just wrote, and vice versa.
    """

    PER_PR = "per_pr", _("Per pull request")
    BASELINE = "baseline", _("Author baseline")
    DIFF = "diff", _("Diff contents")


SIGNAL_KIND_FAMILY: dict[str, str] = {
    SignalKind.FAST_LARGE_PR: SignalFamily.PER_PR,
    SignalKind.COMMIT_BURST: SignalFamily.PER_PR,
    SignalKind.SINGLE_LARGE_COMMIT: SignalFamily.PER_PR,
    SignalKind.MASS_FILE_CREATION: SignalFamily.PER_PR,
    SignalKind.UNUSED_NEW_DEPENDENCY: SignalFamily.PER_PR,
    SignalKind.INSTANT_REVIEW_RESPONSE: SignalFamily.PER_PR,
    SignalKind.THROUGHPUT_SHIFT: SignalFamily.BASELINE,
    SignalKind.OFF_HOURS_VOLUME: SignalFamily.BASELINE,
    SignalKind.TEST_RATIO_LOCKSTEP: SignalFamily.BASELINE,
    SignalKind.BODY_STYLE_SHIFT: SignalFamily.BASELINE,
}


def kinds_in_family(family: str) -> frozenset[str]:
    return frozenset(kind for kind, value in SIGNAL_KIND_FAMILY.items() if value == family)


# Which `params` keys each kind understands, and the default every seeded rule starts from. A key
# outside this set is a typo or a leftover from an older version of the kind, and `clean()` rejects
# it: silently ignoring it would leave a lead believing they had tuned something.
SIGNAL_PARAM_DEFAULTS: dict[str, dict[str, object]] = {
    SignalKind.FAST_LARGE_PR: {"min_lines": 400, "min_files": 8, "max_hours": 2},
    SignalKind.COMMIT_BURST: {"min_commits": 5, "max_gap_seconds": 90, "min_lines_per_commit": 20},
    SignalKind.SINGLE_LARGE_COMMIT: {"min_lines": 300, "min_files": 5},
    SignalKind.MASS_FILE_CREATION: {"min_added_files": 10, "min_directories": 3},
    SignalKind.UNUSED_NEW_DEPENDENCY: {"manifests": ["pyproject.toml", "package.json", "requirements*.txt"]},
    SignalKind.INSTANT_REVIEW_RESPONSE: {"max_minutes": 5, "min_occurrences": 3},
    SignalKind.THROUGHPUT_SHIFT: {"window_weeks": 8, "ratio": 2.5, "sustained_weeks": 2},
    SignalKind.OFF_HOURS_VOLUME: {"window_weeks": 8, "percentile": 0.9, "min_share": 0.4},
    SignalKind.TEST_RATIO_LOCKSTEP: {"window_weeks": 8, "min_prs": 10, "max_variance": 0.15},
    SignalKind.BODY_STYLE_SHIFT: {"window_weeks": 8, "min_prs": 10, "length_ratio": 4},
}


class SignalRule(models.Model):
    """A tuned instance of a `SignalKind`, editable by an admin exactly like a `DetectionRule`.

    A structural rule can never be `high` confidence, and that is a database `CheckConstraint`
    rather than only a form validator: "only a tool-written artefact proves AI authorship" is the
    one property of this feature that must not be reachable by editing a row in the Django admin.
    Everything a heuristic produces is evidence a lead reads, never a verdict the tool reaches on
    its own.
    """

    name = models.CharField(_("name"), max_length=200, unique=True)
    kind = models.CharField(_("kind"), max_length=40, choices=SignalKind.choices)
    params = models.JSONField(_("parameters"), default=dict, blank=True)
    tool = models.CharField(_("tool"), max_length=20, choices=Tool.choices, default=Tool.OTHER)
    confidence = models.CharField(_("confidence"), max_length=10, choices=Confidence.choices)
    is_active = models.BooleanField(_("is active"), default=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("structural signal rule")
        verbose_name_plural = _("structural signal rules")
        indexes = [models.Index(fields=["is_active", "kind"])]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(confidence=Confidence.HIGH),
                name="signalrule_confidence_never_high",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.confidence == Confidence.HIGH:
            raise ValidationError(
                {
                    "confidence": _(
                        "A structural rule can never be high confidence. Only an artefact the tool "
                        "itself wrote proves AI authorship; a heuristic about a pull request's shape "
                        "is evidence for a human to read."
                    )
                }
            )
        defaults = SIGNAL_PARAM_DEFAULTS.get(self.kind)
        if defaults is None:
            raise ValidationError({"kind": _("Unknown signal kind.")})
        params = self.params or {}
        if not isinstance(params, dict):
            raise ValidationError({"params": _("Parameters must be a JSON object.")})
        unknown = sorted(set(params) - set(defaults))
        if unknown:
            raise ValidationError(
                {
                    "params": _("Unknown parameter(s) for this kind: %(names)s. Known: %(known)s.")
                    % {"names": ", ".join(unknown), "known": ", ".join(sorted(defaults))}
                }
            )
        for key, value in params.items():
            expected = defaults[key]
            if isinstance(expected, bool) or not isinstance(expected, (int, float)):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError({"params": _("Parameter %(name)s must be a number.") % {"name": key}})
            if value < 0:
                raise ValidationError({"params": _("Parameter %(name)s cannot be negative.") % {"name": key}})

    def effective_params(self) -> dict:
        """The stored parameters over the kind's defaults, so a rule that sets one threshold still
        gets the rest — and so a kind that grows a parameter does not silently read it as absent."""
        return {**SIGNAL_PARAM_DEFAULTS.get(self.kind, {}), **(self.params or {})}


class AISignal(models.Model):
    pull_request = models.ForeignKey(
        "activity.PullRequest",
        on_delete=models.CASCADE,
        related_name="ai_signals",
        verbose_name=_("pull request"),
    )
    commit = models.ForeignKey(
        "activity.Commit",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_signals",
        verbose_name=_("commit"),
    )
    # A signal comes from exactly one of the two rule families, enforced below by a constraint.
    rule = models.ForeignKey(
        DetectionRule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="signals",
        verbose_name=_("rule"),
    )
    signal_rule = models.ForeignKey(
        SignalRule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="signals",
        verbose_name=_("structural signal rule"),
    )
    tool = models.CharField(_("tool"), max_length=20, choices=Tool.choices)
    confidence = models.CharField(_("confidence"), max_length=10, choices=Confidence.choices)
    # A regex signal quotes the source data it matched, which needs no translation. A structural
    # signal has no such quote — its evidence is a generated sentence — so it is stored as a code
    # plus parameters and rendered in the reader's language (CLAUDE.md), exactly like a policy
    # violation. The two are mutually exclusive in practice: `evidence` is blank for a structural
    # signal, `evidence_code` is blank for a regex one.
    evidence = models.CharField(_("evidence"), max_length=200, blank=True)
    evidence_code = models.CharField(_("evidence code"), max_length=50, blank=True)
    evidence_params = models.JSONField(_("evidence parameters"), default=dict, blank=True)
    evidence_hash = models.CharField(_("evidence hash"), max_length=64)
    detected_at = models.DateTimeField(_("detected at"), auto_now_add=True)

    class Meta:
        verbose_name = _("AI signal")
        verbose_name_plural = _("AI signals")
        indexes = [models.Index(fields=["pull_request", "confidence"])]
        constraints = [
            # One uniqueness rule per family rather than one over both columns: SQLite treats
            # every NULL as distinct, so a single constraint spanning a nullable FK would never
            # fire for the family whose column is NULL, and re-running detection would duplicate
            # every structural signal.
            models.UniqueConstraint(
                fields=["pull_request", "rule", "evidence_hash"],
                condition=models.Q(rule__isnull=False),
                name="uniq_aisignal_pr_rule_evidence",
            ),
            models.UniqueConstraint(
                fields=["pull_request", "signal_rule", "evidence_hash"],
                condition=models.Q(signal_rule__isnull=False),
                name="uniq_aisignal_pr_signal_rule_evidence",
            ),
            models.CheckConstraint(
                condition=models.Q(rule__isnull=False, signal_rule__isnull=True)
                | models.Q(rule__isnull=True, signal_rule__isnull=False),
                name="aisignal_exactly_one_rule_family",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.rule or self.signal_rule} on {self.pull_request}"
