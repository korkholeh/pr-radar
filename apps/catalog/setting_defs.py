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
        ["dependabot", "renovate", "github-actions"],
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
