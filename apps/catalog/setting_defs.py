from dataclasses import dataclass

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class SettingDef:
    key: str
    value_type: str  # bool | int | float | str | list | dict
    default: object
    description: Promise | str
    group: str  # sync | metrics | ai | policy | churn | ui | export


SETTING_DEFS: tuple[SettingDef, ...] = (
    SettingDef("BACKFILL_DAYS", "int", 180, _("How many days of history a first sync pulls in."), "sync"),
    SettingDef(
        "DEFAULT_CONNECTION_KIND",
        "str",
        "fine_grained_pat",
        _("Connection kind preselected when creating a new GitHub connection."),
        "sync",
    ),
    SettingDef(
        "CONNECTION_CHECK_INTERVAL_HOURS",
        "int",
        24,
        _("How often a connection's health is re-checked."),
        "sync",
    ),
    SettingDef(
        "AI_COHORT_INCLUDE_SUSPECTED",
        "bool",
        False,
        _("Whether PRs with a merely suspected AI status count in the AI cohort."),
        "ai",
    ),
    SettingDef(
        "BOT_LOGIN_SUFFIXES", "list", ["[bot]"], _("Login suffixes that mark an account as a bot."), "ai"
    ),
    SettingDef(
        "BOT_LOGINS",
        "list",
        [
            "dependabot",
            "renovate",
            "github-actions",
            # AI agents and reviewers whose login carries no `[bot]` suffix, so `BOT_LOGIN_SUFFIXES`
            # never catches them. Each one below was observed in real synced data; do not add a
            # login here on the strength of a vendor's documentation alone, because a wrong entry
            # silently removes a real person from every metric.
            "copilot-pull-request-reviewer",
            "charliecreates",
            "charliehelps",
        ],
        _("Exact logins that mark an account as a bot."),
        "ai",
    ),
    SettingDef(
        "DISCLOSURE_SECTION_HEADINGS",
        "list",
        ["AI assistance"],
        _("PR template section headings that hold the AI disclosure."),
        "ai",
    ),
    SettingDef(
        "DISCLOSURE_LABELS_NONE", "list", ["None"], _("Disclosure values meaning no AI assistance."), "ai"
    ),
    SettingDef(
        "DISCLOSURE_LABELS_PARTIAL",
        "list",
        ["Partial"],
        _("Disclosure values meaning partial AI assistance."),
        "ai",
    ),
    SettingDef(
        "DISCLOSURE_LABELS_SUBSTANTIAL",
        "list",
        ["Substantial"],
        _("Disclosure values meaning substantial AI assistance."),
        "ai",
    ),
    SettingDef(
        "DISCLOSURE_TOOLS_LABELS",
        "list",
        ["AI tools used"],
        _("PR template section headings that list the AI tools used."),
        "ai",
    ),
    SettingDef(
        "DETECTION_DRY_RUN_PR_COUNT",
        "int",
        50,
        _("How many of the most recent PRs a detection rule dry run scans."),
        "ai",
    ),
    SettingDef(
        "DISCLOSURE_TOOL_ALIASES",
        "dict",
        {
            "claude_code": ["claude code", "claude"],
            "copilot": ["copilot", "github copilot"],
            "cursor": ["cursor"],
            "codex": ["codex"],
            "devin": ["devin"],
            "gemini": ["gemini"],
            "aider": ["aider"],
            "windsurf": ["windsurf"],
            "chatgpt": ["chatgpt", "gpt"],
        },
        _("Free text in the disclosed-tools line mapped onto a canonical Tool value."),
        "ai",
    ),
    SettingDef(
        "MIN_SAMPLE", "int", 5, _("Minimum sample size below which a metric is greyed out."), "metrics"
    ),
    SettingDef(
        "STALE_DAYS", "int", 5, _("Days of inactivity after which an open PR is considered stale."), "metrics"
    ),
    SettingDef(
        "WAITING_REVIEW_HOURS",
        "int",
        24,
        _("Hours after which a PR waiting for review is flagged."),
        "metrics",
    ),
    SettingDef("DURATION_MODE", "str", "calendar_hours", _("How PR durations are computed."), "metrics"),
    SettingDef(
        "PR_SIZE_BUCKETS",
        "dict",
        {"XS": 10, "S": 100, "M": 400, "L": 1000},
        _("Upper line-count boundary for each PR size bucket."),
        "metrics",
    ),
    SettingDef(
        "AI_SUSPECTED_MIN_STRUCTURAL_KINDS",
        "int",
        2,
        _(
            "How many distinct structural signal kinds a pull request needs before its AI status "
            "becomes 'suspected'. Counted over kinds, not rows: five commit bursts are one kind of "
            "evidence. No number of structural signals ever reaches 'explicit'."
        ),
        "ai",
    ),
    SettingDef(
        "AI_TOOLING_PATH_GLOBS",
        "list",
        [
            # Ordered by what this installation's synced data actually contains: `.agents/` and
            # `AGENTS.md` were found in real pull requests, none of the others anywhere. The rest
            # are kept because a repository that has one is unambiguously configured for an
            # agent — they simply have not been seen here yet.
            ".agents",
            ".claude",
            ".claude/skills",
            "AGENTS.md",
            "CLAUDE.md",
            ".mcp.json",
            ".github/copilot-instructions.md",
            ".github/agents",
            ".cursor",
            ".cursorrules",
            ".windsurfrules",
            ".codex",
            ".gemini",
            "GEMINI.md",
            ".aider.conf.yml",
            ".specstory",
        ],
        _(
            "Paths that mark a repository as configured for an AI agent. Matched against the "
            "repository's root tree, plus one level inside any directory a pattern names."
        ),
        "ai",
    ),
    SettingDef(
        "EXCLUDED_PATH_GLOBS",
        "list",
        [
            "*.lock",
            "*-lock.json",
            "*.min.js",
            "*.min.css",
            "**/migrations/**",
            "**/generated/**",
            "**/vendor/**",
        ],
        _("File path globs excluded from size and churn calculations."),
        "metrics",
    ),
    SettingDef(
        "TEST_PATH_GLOBS",
        "list",
        [
            "tests/**",
            "test_*.py",
            "*_test.py",
            "*.test.ts",
            "*.test.tsx",
            "*.spec.ts",
            "*.spec.tsx",
            "*Tests.swift",
            "**/__tests__/**",
        ],
        _("File path globs recognised as test files."),
        "metrics",
    ),
    SettingDef(
        "RUBBER_STAMP_MAX_MINUTES",
        "int",
        10,
        _("Maximum minutes between review request and approval to call a review a rubber stamp."),
        "metrics",
    ),
    SettingDef(
        "REVIEW_LOAD_TOP_N", "int", 2, _("How many top reviewers count toward review load."), "metrics"
    ),
    SettingDef(
        "FOLLOWUP_FIX_WINDOW_DAYS",
        "int",
        14,
        _("Days after merge in which a follow-up fix is attributed."),
        "metrics",
    ),
    SettingDef(
        "FOLLOWUP_FIX_FILE_OVERLAP",
        "float",
        0.5,
        _("Minimum file overlap ratio to attribute a follow-up fix to a PR."),
        "metrics",
    ),
    SettingDef(
        "METRICS_CACHE_TTL_SECONDS",
        "int",
        3600,
        _("How long a computed metrics result stays cached before it expires on its own."),
        "metrics",
    ),
    SettingDef(
        "DEFAULT_PERIOD_DAYS",
        "int",
        30,
        _("Default number of days a dashboard period covers when none is chosen."),
        "metrics",
    ),
    SettingDef(
        "NO_TESTS_MIN_LINES",
        "int",
        20,
        _("Minimum changed lines before a missing-tests violation applies."),
        "policy",
    ),
    SettingDef(
        "POLICY_DISABLED_RULES",
        "list",
        [],
        _("Rule codes switched off; an open violation for a disabled rule auto-resolves."),
        "policy",
    ),
    SettingDef(
        "POLICY_VIOLATION_PATHS_IN_PARAMS",
        "int",
        20,
        _("Maximum sensitive paths stored in a violation's details; the total is kept separately."),
        "policy",
    ),
    SettingDef(
        "VIOLATIONS_PAGE_SIZE",
        "int",
        50,
        _("Rows per page on the policy violations console."),
        "policy",
    ),
    SettingDef("CHURN_WINDOW_DAYS", "int", 21, _("Days after merge over which churn is measured."), "churn"),
    SettingDef(
        "CHURN_MAX_FILES", "int", 50, _("Maximum files in a PR before churn analysis is skipped."), "churn"
    ),
    SettingDef(
        "CHURN_MAX_WORKERS",
        "int",
        4,
        _("Maximum concurrent git workers a churn run uses."),
        "churn",
    ),
    SettingDef(
        "CHURN_GIT_TIMEOUT_SECONDS",
        "int",
        120,
        _("Timeout in seconds for a single git subprocess call during churn analysis."),
        "churn",
    ),
    SettingDef(
        "CHURN_REPO_TIME_BUDGET_SECONDS",
        "int",
        600,
        _("Maximum seconds a churn run spends on a single repository before deferring the rest."),
        "churn",
    ),
    SettingDef(
        "DIFF_ANALYSIS_REPOSITORIES",
        "list",
        [],
        _(
            "Repositories (owner/name) whose diffs are analysed for structural AI signals during "
            "the nightly churn run. Opt-in per repository because it reads the contents of every "
            'change; "*" opts in every repository. Empty means no diff analysis runs.'
        ),
        "churn",
    ),
    SettingDef(
        "DASHBOARD_TABLE_PAGE_SIZE",
        "int",
        25,
        _("Rows per page on a dashboard table (projects, repositories, people, recent PRs)."),
        "ui",
    ),
    SettingDef(
        "PR_FILES_DISPLAY_LIMIT",
        "int",
        300,
        _("Maximum files shown on the PR detail page before a '+N more' line."),
        "ui",
    ),
    SettingDef(
        "REVIEW_HEATMAP_TOP_N",
        "int",
        15,
        _("Authors/reviewers shown per axis on the reviews heat map; the rest fold into 'Other'."),
        "ui",
    ),
    SettingDef("DEFAULT_UI_LANGUAGE", "str", "en", _("Default UI language for a new user."), "ui"),
    SettingDef(
        "DETECT_BROWSER_LANGUAGE",
        "bool",
        False,
        _("Whether to derive the initial UI language from the browser."),
        "ui",
    ),
    SettingDef("DEFAULT_THEME", "str", "system", _("Default UI theme for a new user."), "ui"),
    SettingDef(
        "EXPORT_DURATION_UNIT",
        "str",
        "hours",
        _("Unit durations are rendered in for CSV/XLSX exports."),
        "export",
    ),
    SettingDef(
        "EXPORT_SYNC_MAX_ROWS", "int", 20000, _("Maximum rows a synchronous export may return."), "export"
    ),
    SettingDef(
        "EXPORT_RETENTION_DAYS",
        "int",
        7,
        _("Days a background export file is kept before cleanup deletes it."),
        "export",
    ),
    SettingDef(
        "SYNC_OVERLAP_MINUTES",
        "int",
        60,
        _("Minutes a sync re-reads before the last watermark, to absorb late-arriving updates."),
        "sync",
    ),
    SettingDef("SYNC_PR_PAGE_SIZE", "int", 50, _("Pull requests fetched per GraphQL page."), "sync"),
    SettingDef(
        "SYNC_NESTED_PAGE_SIZE",
        "int",
        100,
        _("Items fetched per page for a pull request's nested connections."),
        "sync",
    ),
    SettingDef(
        "RATE_LIMIT_MIN_REMAINING",
        "int",
        200,
        _("Primary rate-limit remaining below which the client waits for reset."),
        "sync",
    ),
    SettingDef(
        "SYNC_MAX_RETRIES", "int", 5, _("Maximum retry attempts for a transient request failure."), "sync"
    ),
    SettingDef(
        "SYNC_RETRY_MAX_SECONDS", "int", 60, _("Maximum backoff delay between retry attempts."), "sync"
    ),
    SettingDef(
        "SYNC_LOCK_STALE_MINUTES",
        "int",
        360,
        _("Minutes after which an unreleased sync lock is considered stale and stolen."),
        "sync",
    ),
    SettingDef(
        "TOKEN_EXPIRY_WARNING_DAYS",
        "int",
        14,
        _("Days before token expiry at which the admin banner starts warning."),
        "sync",
    ),
    SettingDef(
        "CONNECTION_RECHECK_MIN_MINUTES",
        "int",
        60,
        _("Minimum minutes between two non-forced verifications of the same connection."),
        "sync",
    ),
)
