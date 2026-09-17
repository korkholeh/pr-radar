# PR Radar — специфікація для Claude Code

Локальний Django-інструмент, який через GitHub API збирає дані про пул-реквести команди, зберігає їх у SQLite і показує дашборди щодо:

1. **AI adoption і відповідності корпоративній AI-політиці**;
2. **якості та динаміки роботи** на рівні загальному, проєкту, репозиторію і людини.

**Аудиторія: лише тімліди та керівники** (один або кілька). Розробники, які створюють PR, **не є користувачами системи**: вони не мають акаунтів, не бачать дашбордів і не отримують сповіщень. Вони існують у системі лише як обʼєкти аналізу (Person). Інструмент використовується для контролю політики, розуміння динаміки проєктів і підготовки до 1:1 та ретро.

**Мови.** Основна мова застосунку — **англійська**; українська — повноцінний другий переклад з перемикачем (розділ 10.7). Уся супровідна документація, код, коментарі, docstrings, повідомлення комітів, логи й вивід management commands — **англійською**. Ця специфікація написана українською лише для автора: назви сторінок, KPI, аркушів тощо, наведені тут українською, є **українським перекладом**; англійські source-рядки агент формулює сам (коротко, у стилі GitHub/Linear) і фіксує пари в `.po`.

Документ написано для автономної реалізації по фазах (розділ 14). Якщо рішення не визначене явно, агент обирає варіант із розділу 15 «Рішення за замовчуванням» і фіксує його в `docs/DECISIONS.md`.

---

## 1. Scope

### У scope (v1)
- Збір даних з GitHub (GraphQL API v4 + REST там, де потрібно) для багатьох репозиторіїв з однієї або кількох організацій чи акаунтів.
- Інкрементальна синхронізація: management command і кнопка в UI.
- Групування репозиторіїв у проєкти.
- Зіставлення GitHub-логінів і git-email у «людей».
- Детекція AI-асистованих PR за явними маркерами та за полем у PR-шаблоні.
- Рушій правил AI-політики та журнал порушень.
- Реєстр метрик із поясненнями, розрахунок на льоту плюс денні rollup-и.
- Дашборди: загальний, проєкт, репозиторій, людина; режим «конкретний день» і режим «період».
- Таблиці (сортування, фільтри, експорт у CSV і XLSX) і графіки; звіт-експорт дашборду в XLSX.
- Усі сторінки доступні лише після логіну; ролі та дозволи.
- Метрика churn через локальні git-клони.

### Поза scope (v1)
- Webhooks, real-time.
- GitHub App (у v1 використовуємо PAT; архітектура не повинна цьому заважати).
- Стилістична (ML) детекція AI-коду.
- GitLab/Bitbucket.
- Будь-який функціонал для розробників: особисті кабінети, self-service, сповіщення авторам PR, коментарі чи статуси в GitHub від імені інструмента (інструмент **лише читає** GitHub).
- Хостинг: v1 працює локально, але код має бути готовий до переїзду (розділ 13).

---

## 2. Стек

| Шар | Вибір |
|---|---|
| Мова | Python 3.12+ |
| Пакети | `uv`, `pyproject.toml` |
| Фреймворк | Django 5.2 LTS |
| БД | SQLite (WAL mode, `busy_timeout`); без SQLite-специфічного SQL, щоб перейти на PostgreSQL без змін |
| HTTP | `httpx` (власний тонкий клієнт для GitHub, без PyGithub) |
| Фонові задачі | `huey` з `SqliteHuey` (окремий файл БД); усі задачі також доступні як management commands |
| UI | Django templates + HTMX + Alpine.js (опційно) |
| CSS | Tailwind (standalone CLI, зібраний CSS комітиться) або Pico.css, якщо Tailwind ускладнює збірку |
| Графіки | Chart.js, **вендорений у `static/`** (без CDN) |
| Таблиці | `django-tables2` + `django-filter` |
| Конфіг | `django-environ`, `.env` |
| Шифрування секретів | `cryptography` (Fernet / `MultiFernet` для ротації ключа) |
| Експорт | `XlsxWriter` (write-only, форматування, нативні Excel-графіки, `constant_memory` для великих обсягів); для тестів читання — `openpyxl` (dev-залежність) |
| i18n | Django i18n, `polib` (dev, перевірка перекладів) |
| Тести | `pytest`, `pytest-django`, `factory_boy`, `freezegun`, `respx` (моки httpx) |
| Якість | `ruff` (lint + format), `mypy` для `services/` і `metrics/` |
| Git | системний `git` через `subprocess` (для churn) |

Таймзона звітів: `REPORT_TIMEZONE=Europe/Kyiv` (налаштовується). У БД усе зберігається в UTC, а межі днів рахуються в `REPORT_TIMEZONE`.

Інтерфейс: англійська (за замовчуванням) і українська, стандартний Django i18n (див. 10.7).

---

## 3. Структура проєкту

```
pr_radar/
  config/                # settings (base/local/prod), urls, huey
  apps/
    accounts/            # логін, ролі, дозволи
    connections/         # GitHubConnection, шифрування токенів, перевірка підключень
    catalog/             # Organization, Repository, Project, Person, Identity
    github_sync/         # клієнт GitHub, sync-сервіси, SyncRun
    activity/            # PullRequest, Commit, Review, Comment, PRFile, CheckStatus
    ai_detection/        # детектори, правила, AISignal
    policy/              # AIPolicy, PolicyRule, PolicyViolation
    metrics/             # реєстр метрик, калькулятори, rollups
    churn/               # локальні клони, blame-аналіз
    dashboards/          # views, templates, charts, tables, exports
  static/  templates/
  tests/
  locale/uk/LC_MESSAGES/ # django.po, djangojs.po (комітяться; .mo збираються)
  docs/                  # англійською: README (в корені), SETUP.md, CONFIGURATION.md, METRICS.md,
                         # POLICY.md, GITHUB_CONNECTIONS.md, DECISIONS.md, PROGRESS.md, TRANSLATIONS.md
```

Бізнес-логіка живе в `services.py` або модулях сервісів, а не у views і models.

---

## 4. Модель даних

Нижче наведено обовʼязкові поля; допоміжні поля агент додає за потреби. Усі сутності з GitHub мають `github_id` (unique) і `raw` (JSONField, опційно, за прапорцем `STORE_RAW_PAYLOADS`).

### 4.1 catalog
- **Organization** — `login`, `type` (org/user), `github_id`, `is_active`.
- **Repository** — `organization`, `connection` (FK на GitHubConnection, PROTECT; через яке підключення синхронізується), `name`, `full_name` (unique), `github_id`, `default_branch`, `is_private`, `is_archived`, `is_active` (чи синхронізувати), `sync_since` (дата початку бекфілу), `last_synced_at`, `sync_cursor` (JSON), `merge_strategy_hint`.
- **Project** — `name`, `slug`, `description`, `repositories` (M2M), `is_active`, `color`.
  - Репозиторій може входити в кілька проєктів. **Загальний рівень завжди дедуплікує за репозиторієм.**
- **Person** — `display_name`, `is_active`, `is_bot`, `exclude_from_metrics`, `team` (CharField, опційно), `role_hint` (dev/qa/design/other, опційно, для фільтрів), `notes` (text, приватні нотатки ліда).
  - Person не повʼязується з Django User: розробники не логіняться в систему.
- **Identity** — `person` (nullable, поки не зіставлено), `kind` (`github_login` / `git_email`), `value` (unique разом з kind), `github_id`, `first_seen_at`.
  - Автозіставлення: email виду `ID+login@users.noreply.github.com` → login; email, який GitHub повʼязав з автором коміту → login.
  - Нерозпізнані identity потрапляють у чергу «Зіставлення людей» в UI.
  - Боти (`[bot]`, `dependabot`, `renovate`, список у налаштуваннях) автоматично отримують `is_bot=True`.

### 4.2 activity
- **PullRequest** — `repository`, `number`, `github_id`, `author` (Identity), `title`, `body`, `state` (open/closed/merged), `is_draft`, `base_ref`, `head_ref`, `created_at`, `ready_for_review_at` (з timeline; якщо не було draft, дорівнює `created_at`), `first_commit_at`, `first_review_at`, `first_approval_at`, `merged_at`, `closed_at`, `merged_by`, `merge_commit_sha`, `merge_method` (merge/squash/rebase/unknown), `additions`, `deletions`, `changed_files`, `effective_additions`, `effective_deletions` (без виключених шляхів), `labels` (JSON), `review_rounds`, `commits_after_first_review`, `last_activity_at`, `updated_at_github`.
  - Похідні кешовані поля (перераховуються сервісами): `size_bucket`, `is_revert`, `reverts_pr` (FK self), `is_hotfix`, `has_test_changes`, `ai_status`, `ai_tools` (JSON), `ai_disclosure`, `is_rubber_stamp`, `is_self_merged`.
- **Commit** — `repository`, `sha` (unique у межах repo), `author_identity`, `committer_identity`, `authored_at`, `committed_at`, `message`, `additions`, `deletions`, `co_authors` (JSON: список name/email), `trailers` (JSON).
- **PullRequestCommit** — `pull_request`, `commit`, `position`.
- **PRFile** — `pull_request`, `path`, `status`, `additions`, `deletions`, `is_test`, `is_excluded`, `matched_sensitive_rule` (nullable).
- **Review** — `pull_request`, `reviewer` (Identity), `state` (APPROVED / CHANGES_REQUESTED / COMMENTED / DISMISSED), `submitted_at`, `body_length`, `comments_count`.
- **ReviewComment** — `pull_request`, `author`, `created_at`, `is_review_thread` (inline vs загальний). Текст не зберігається, окрім довжини (за замовчуванням).
- **CheckStatus** — `pull_request`, `commit_sha`, `is_first_ci_commit`, `rollup_state` (SUCCESS/FAILURE/ERROR/PENDING/NONE), `observed_at`.

### 4.3 ai_detection
- **DetectionRule** — `name`, `detector` (enum, див. 6.1), `pattern` (regex/glob/string), `tool` (claude_code / copilot / cursor / codex / devin / chatgpt / other), `confidence` (high/medium/low), `is_active`. Сідиться з `fixtures/detection_rules.yaml`, редагується в UI.
- **AISignal** — `pull_request`, `commit` (nullable), `rule`, `tool`, `confidence`, `evidence` (короткий фрагмент ≤200 символів), `detected_at`.

### 4.4 policy
- **AIPolicy** (singleton, версіонується) — `allowed_tools` (JSON), `require_disclosure` (bool), `require_human_approval` (bool), `min_human_approvals` (int), `require_tests_for_ai_prs` (bool), `ai_pr_max_effective_lines` (int, nullable), `effective_from`.
- **SensitivePathRule** — `project` (nullable = глобальне), `glob`, `ai_mode` (forbidden / needs_extra_review), `description`.
- **PolicyViolation** — `pull_request`, `rule_code` (enum), `severity` (high/medium/low), `details_params` (JSON — лише дані: шляхи, інструменти, кількості; **текст повідомлення не зберігається**, а рендериться на льоту перекладеним шаблоном за `rule_code`), `status` (open / acknowledged / waived / resolved), `resolved_by`, `resolution_comment`, `created_at`, `updated_at`. Унікальність: (`pull_request`, `rule_code`, `details_hash`).

### 4.5 churn
- **ChurnResult** — `pull_request`, `window_days`, `lines_at_merge`, `lines_surviving`, `churn_ratio`, `snapshot_sha`, `computed_at`, `status` (ok / unsupported_merge_method / too_large / error), `error`.

### 4.6 metrics
- **DailyRollup** — `date`, `scope_type` (global / project / repo / person), `scope_id` (nullable для global), `cohort` (all / ai / non_ai), `metric_key`, `value` (float), `sample_size`. Unique (`date`, `scope_type`, `scope_id`, `cohort`, `metric_key`).
  - У rollup зберігаються **лише адитивні метрики** (лічильники, суми). Медіани й перцентилі за період рахуються з сирих даних (див. 8.3).

### 4.7 connections
- **GitHubConnection** — `name` (унікальна, людська назва, напр. «Клієнт Atlas»), `kind` (`fine_grained_pat` / `classic_pat`; `github_app` — зарезервовано), `owner_login` (org/user, для якого видано токен; для classic PAT — порожньо), `token_encrypted` (BinaryField), `token_last4`, `token_login` (чий токен, з `/user`), `expires_at` (nullable; з заголовка `github-authentication-token-expiration` або введено вручну), `status` (`unverified` / `ok` / `degraded` / `invalid` / `expired`), `last_checked_at`, `last_check_result` (JSON з кодами перевірок і параметрами, без готових текстів — тексти й поради рендеряться мовою користувача), `rate_limit_remaining`, `rate_limit_reset_at`, `is_active`, `created_by`, `created_at`, `updated_at`.
  - Для майбутнього GitHub App закласти nullable поля `app_id`, `installation_id`, `private_key_encrypted` (без реалізації у v1).

### 4.8 github_sync
- **SyncRun** — `started_at`, `finished_at`, `trigger` (cli/ui/schedule), `status`, `repositories` (M2M), `stats` (JSON: prs/commits/reviews), `stats_by_connection` (JSON: репозиторії, помилки, rate limit для кожного підключення), `error_log` (text, з маскуванням токенів).

---

## 5. Синхронізація з GitHub

### 5.1 Підключення до GitHub

Репозиторії можуть належати різним організаціям (зокрема організаціям клієнтів), тому підключень може бути кілька. Кожне підключення — окремий запис `GitHubConnection`, яким керує admin в UI («Налаштування → Підключення GitHub»).

**Типи токенів:**
- **Fine-grained PAT (рекомендовано).** Видається на одного resource owner (організацію або акаунт), тому на кожну організацію потрібне окреме підключення. Права — лише читання: *Metadata*, *Contents*, *Pull requests*, *Checks*, *Commit statuses*; для discovery членів організації — *Members* (read, опційно). Організація може вимагати схвалення токена її адміністратором — UI показує це як статус `degraded` з поясненням.
- **Classic PAT (дозволено, не рекомендовано).** Бачить усі організації користувача, але scope `repo` дає також запис. При створенні UI показує попередження. Для організацій із SAML SSO токен треба окремо авторизувати для організації — перевірка підключення це виявляє (заголовок `X-GitHub-SSO`) і дає посилання на авторизацію.
- **GitHub App** — не у v1; клієнт GitHub абстраговано через протокол `GitHubAuth` (`get_headers()`, `get_git_credentials()`, `rate_limit_key`), щоб додати його без змін у sync.

**Зберігання токенів:**
- Токен шифрується Fernet перед записом у БД. Ключ — лише в `.env` (`FIELD_ENCRYPTION_KEYS`, список через кому; перший — для шифрування, решта — для розшифрування при ротації, `MultiFernet`). Без ключа файл SQLite не розкриває токенів.
- Команда `manage.py rotate_encryption_key` перешифровує всі токени першим ключем.
- Розшифрований токен існує лише в памʼяті під час запиту; ніколи не пишеться в логи, винятки, `SyncRun.error_log`, audit, експорт, Django admin чи шаблони. Логер має фільтр, що маскує `ghp_…`, `github_pat_…`, `gho_…`.
- У UI після збереження показується лише `…last4`. Замінити токен можна, переглянути — ні.
- Django admin для `GitHubConnection` — без поля токена (лише службові поля, read-only).

**Створення підключення (UI):**
1. Форма: назва, тип, власник (для fine-grained), токен.
2. Перед збереженням — автоматична перевірка (нижче). Зберегти з помилкою можна лише явно («Зберегти як неперевірене»).
3. Після успіху — перехід до discovery репозиторіїв цього підключення.

**Перевірка підключення** (кнопка «Перевірити» + автоматично щодня і після кожної помилки 401/403):
- `GET /user` → `token_login`; заголовок терміну дії → `expires_at`.
- Перелік видимих репозиторіїв власника (кількість + перші N назв).
- Пробні read-запити на одному репозиторії: PR, commits, check runs, contents — результат по кожному праву (✓ / ✗ / недоступно).
- Для classic PAT — наявні scopes (`X-OAuth-Scopes`), попередження, якщо є права на запис.
- Потреба SSO-авторизації для організації.
- Rate limit: залишок і час скидання.
- Результат зберігається в `last_check_result` і показується таблицею з людськими поясненнями, що робити при кожній проблемі.

**Статуси і сповіщення в UI:**
- `ok` — усе працює; `degraded` — частина прав чи репозиторіїв недоступна; `invalid` — 401; `expired` — минув термін.
- Банер на всіх сторінках (для admin), якщо є підключення зі статусом `invalid`/`expired` або з терміном дії < 14 днів.
- На сторінках репозиторіїв і в журналі синхронізації видно, які репозиторії не оновлюються через проблемне підключення.

**Сумісність зі швидким стартом:** якщо в `.env` задано `GITHUB_TOKEN` і підключень ще немає, при першому запуску (`manage.py bootstrap_connection` або автоматично в міграції даних / на старті) створюється підключення «Default (.env)». Далі джерело істини — БД; змінна з `.env` більше не читається, про що пише попередження в логах, якщо вона лишилась.

### 5.2 Discovery
- Сторінка «Репозиторії → Додати»: вибрати підключення → отримати список доступних через нього репозиторіїв (з групуванням за власником), позначити потрібні чекбоксами, задати `sync_since` (за замовчуванням 180 днів тому) і, опційно, одразу додати в проєкт.
- Архівні репозиторії за замовчуванням не пропонуються.
- Репозиторій, уже доданий через інше підключення, позначається; його можна перепривʼязати до іншого підключення (дія в UI з підтвердженням).
- Якщо підключення видаляється, спершу треба перепривʼязати або деактивувати його репозиторії (PROTECT); історичні дані не видаляються.

### 5.3 Алгоритм
Для кожного активного репозиторію:
1. GraphQL `repository.pullRequests(orderBy: UPDATED_AT DESC)` сторінками по 50; зупинка, коли `updatedAt < last_synced_at - SYNC_OVERLAP` (за замовчуванням 1 год) або `< sync_since`.
2. Для кожного PR: деталі, `timelineItems` (READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT, MERGED_EVENT), reviews, review threads/comments (лише метадані), commits (з трейлерами, авторами, `statusCheckRollup`), files. Вкладені сторінки обовʼязково допагінувати, якщо їх більше за 100.
3. Upsert-и ідемпотентні (`update_or_create` за `github_id`/`sha`), в одній транзакції на PR.
4. Після запису PR викликати post-processing: identity resolution → похідні поля → AI detection → policy evaluation → позначити дати для перерахунку rollup-ів.
5. Оновити `last_synced_at` лише після успішного завершення репозиторію.

### 5.4 Rate limit і надійність
- Запитувати `rateLimit { remaining resetAt cost }` у кожному GraphQL-запиті. Якщо `remaining < 200`, чекати до `resetAt` і логувати це в SyncRun.
- Ретраї з експоненційною затримкою на 502/503/secondary rate limit (з урахуванням `Retry-After`).
- Помилка одного PR не зупиняє репозиторій, а помилка одного репозиторію не зупиняє run. Усе фіксується в `error_log`.
- Блокування: одночасно може працювати лише один sync (lock у БД).
- Rate limit ведеться **окремо для кожного підключення**; якщо одне підключення чекає скидання ліміту, репозиторії інших підключень синхронізуються далі.
- 401 → підключення отримує статус `invalid`, його репозиторії пропускаються до кінця run; 403 з ознакою SSO → статус `degraded` з поясненням.
- Невдалі повторні перевірки не спамлять: не частіше разу на годину на підключення.

### 5.5 Команди
```
manage.py sync [--repo owner/name ...] [--project slug] [--since YYYY-MM-DD] [--full]
manage.py recompute [--from DATE] [--to DATE]    # похідні поля, AI, політика, rollups
manage.py compute_churn [--repo ...]
manage.py seed_demo                              # фейкові дані для розробки UI
```
В UI сторінка «Синхронізація» містить кнопку «Синхронізувати зараз» (ставить задачу в huey), список SyncRun, статус і прогрес через HTMX polling. Для локального розкладу `docs/SETUP.md` описує launchd/cron (`sync` щогодини, `compute_churn` щоночі).

---

## 6. Детекція AI

### 6.1 Детектори
| detector | Джерело | Приклад |
|---|---|---|
| `commit_trailer` | трейлери комітів | `Co-Authored-By: Claude <noreply@anthropic.com>` |
| `commit_author` | email/логін автора коміту | `noreply@anthropic.com`, `copilot-swe-agent[bot]` |
| `pr_author` | автор PR | `devin-ai-integration[bot]` |
| `pr_body_footer` | текст опису PR | `Generated with Claude Code` |
| `html_comment` | HTML-коментар в описі | `<!-- ai-generated -->` |
| `label` | лейбли PR | `ai-generated`, `codex` |
| `branch_pattern` | `head_ref` | `^(codex|cursor|claude|copilot)/` |
| `commit_message` | текст коміту | маркери інструментів |

Початковий набір правил — у `fixtures/detection_rules.yaml`. Агент заповнює його для Claude Code, Copilot, Cursor, Codex, Devin, Gemini, Aider, Windsurf, позначаючи кожне правило коментарем про джерело. Спірні правила отримують `confidence=low`.

### 6.2 Disclosure з PR-шаблону
Інструмент постачає рекомендований англомовний `docs/pull_request_template.md`:
```markdown
### AI assistance
- [ ] None
- [ ] Partial (autocomplete, snippets, review)
- [ ] Substantial (agent wrote most of the code)

AI tools used: <!-- e.g. Claude Code, Copilot -->
```
Парсер (толерантний до регістру, `[x]`/`[X]`) визначає `ai_disclosure`: `none` / `partial` / `substantial` / `missing` / `ambiguous` (кілька позначок), а також `disclosed_tools`. Мітки чекбоксів і заголовок секції — налаштовувані синоніми (`AppSetting`), за замовчуванням англійські; можна додати українські, якщо команда використовує локалізований шаблон.

### 6.3 Підсумковий `ai_status` PR
- `ai_explicit` — є сигнал з `confidence=high`;
- `ai_disclosed` — disclosure `partial` або `substantial`, а high-сигналів немає;
- `ai_suspected` — лише medium/low сигнали;
- `no_ai` — disclosure `none` і сигналів немає;
- `unknown` — disclosure `missing` і сигналів немає.

**Когорта AI** для метрик = `ai_explicit` ∪ `ai_disclosed` (налаштовується: чи включати `ai_suspected`).

---

## 7. Рушій AI-політики

Правила (`rule_code`), кожне можна вимкнути в налаштуваннях:

| code | Умова | severity |
|---|---|---|
| `DISCLOSURE_MISSING` | `require_disclosure` і `ai_disclosure ∈ {missing, ambiguous}` | medium |
| `DISCLOSURE_MISMATCH` | disclosure = `none`, але є high-сигнал | high |
| `TOOL_NOT_ALLOWED` | виявлений або задекларований tool не входить у `allowed_tools` | high |
| `SENSITIVE_PATH_FORBIDDEN` | AI-PR змінює файл, що підпадає під `ai_mode=forbidden` | high |
| `SENSITIVE_PATH_REVIEW` | AI-PR змінює `needs_extra_review`-шлях і має менше ніж `min_human_approvals + 1` апрувів | medium |
| `NO_HUMAN_APPROVAL` | AI-PR змерджено без потрібної кількості апрувів від людей (не автор і не бот) | high |
| `SELF_MERGE` | AI-PR змерджено автором без апрувів інших | high |
| `NO_TESTS` | AI-PR змінює не-тестовий код понад поріг (за замовчуванням 20 рядків) без змін у тестах | low |
| `AI_PR_TOO_LARGE` | `effective_lines > ai_pr_max_effective_lines` | low |

- Оцінювання ідемпотентне: повторний прогін не дублює порушення; якщо умова зникла, `open` порушення переходить у `resolved` автоматично (з позначкою «auto»).
- Правила, що залежать від мерджу, перевіряються лише для змердженних PR.
- Політика має `effective_from`: PR, створені раніше, не оцінюються.
- Статуси `acknowledged`/`waived` виставляє будь-який користувач (лід), коментар обовʼязковий; фіксується в AuditEntry.

---

## 8. Метрики

### 8.1 Реєстр
Кожна метрика — обʼєкт у `metrics/registry.py`:
```python
MetricDef(
    key="lead_time_p50",
    title=_("Lead time (median)"),
    description=_("From ready for review to merge."),
    unit="duration",
    direction="lower_is_better",
    kind="distribution",  # counter | ratio | distribution | state
    levels={"global", "project", "repo", "person"},
    supports_cohorts=True,
    calculator=...,
)
```
`title` і `description` — lazy-переклади. `docs/METRICS.md` (англійською) генерується з реєстру командою `manage.py metrics_doc`; CI-тест перевіряє, що файл актуальний. В UI біля кожної метрики є іконка ⓘ з `description` і формулою.

### 8.2 Перелік (v1)

**Adoption і політика**
- `ai_pr_share` — частка змердженних PR у когорті AI.
- `ai_pr_count`, `ai_status_breakdown` — розподіл за `ai_status`.
- `ai_tool_breakdown` — розподіл PR за інструментами.
- `disclosure_rate` — частка PR з валідним disclosure (не missing/ambiguous).
- `disclosure_mismatch_count`.
- `violations_open`, `violations_new`, `violations_by_rule`.
- `ai_active_people` — кількість людей з ≥1 AI-PR за період.

**Динаміка (flow)**
- `prs_opened`, `prs_merged` (throughput), `prs_closed_unmerged`.
- `lead_time_p50/p90` — `ready_for_review_at → merged_at`.
- `cycle_time_p50` — `first_commit_at → merged_at`.
- `time_to_first_review_p50/p90` — `ready_for_review_at → first_review_at` (перший review або review comment від не-автора і не-бота).
- `pr_size_p50`, `pr_size_buckets` — `effective_additions + effective_deletions`; бакети XS <10, S <100, M <400, L <1000, XL ≥1000.
- `open_prs` (state на кінець дня), `stale_prs` (open, не draft, без активності > `STALE_DAYS`=5), `waiting_review_24h` (ready, без жодного ревʼю > 24 год).
- `wip_per_person` — відкриті не-draft PR на автора.
- `reviews_given`, `review_load_share` — частка ревʼю, що припадає на топ-2 ревʼюерів (ризик вузького місця).
- `reviewer_response_p50` (для людини) — від запиту на ревʼю до її ревʼю.

**Якість**
- `review_rounds_avg` — кількість CHANGES_REQUESTED + 1.
- `rework_rate` — частка PR з комітами після першого ревʼю.
- `ci_first_pass_rate` — частка PR, де перший коміт із CI мав `SUCCESS`.
- `revert_rate` — частка змердженних PR, які пізніше були ревертнуті (визначення за `Revert "…"` / `This reverts commit` / `Reverts owner/repo#N`).
- `followup_fix_rate` *(евристика, так і підписати в UI)* — змерджений PR, за яким протягом 14 днів іде PR з hotfix/fix-патерном, що перетинається з ним ≥50% файлів.
- `churn_21d` — див. розділ 9.
- `test_change_ratio` — частка PR із змінами в тестах; `test_lines_ratio`.
- `review_comments_per_100_lines`.
- `rubber_stamp_rate` — PR розміру ≥L, апрувнутий <10 хв після ready, без жодного коментаря.
- `self_merge_rate`.

Кожна метрика з `supports_cohorts=True` показується для **all / AI / non-AI** поруч.

### 8.3 Розрахунок
- `metrics/service.py`: `compute(metric_keys, scope, date_from, date_to, cohort, granularity)` повертає значення за період, значення за попередній період такої ж довжини, дельту і часовий ряд.
- Лічильники читаються з `DailyRollup`; розподіли й частки рахуються з сирих таблиць queryset-ами (агрегація в Python допустима, медіани рахувати через `statistics`).
- Кешування результатів у Django cache (FileBased/LocMem локально) з ключем, що включає `last_data_version` (інкрементується після кожного sync/recompute).
- **Атрибуція подій до дня**: opened — `created_at`, merged — `merged_at`, review — `submitted_at` (у `REPORT_TIMEZONE`). Distribution-метрики атрибутуються до дня мерджу.
- **State-метрики на дату D** відновлюються з дат (відкритий на кінець D = `created_at ≤ D` і не закритий до кінця D). Для `stale` береться остання активність ≤ D.
- Виключення: PR від ботів і `exclude_from_metrics` людей не входять у метрики (окремий лічильник «ботові PR»); шляхи з `EXCLUDED_PATH_GLOBS` (lock-файли, `*.min.js`, згенеровані файли, опційно міграції) не входять у розмір.
- Метрики людини рахуються за PR, де вона автор, а review-метрики — де вона ревʼюер.

---

## 9. Churn

Визначення: частка рядків PR, які не дожили до `merged_at + CHURN_WINDOW_DAYS` (21) на default branch.

Алгоритм (`churn/service.py`):
1. Підтримувати bare-клон кожного репозиторію в `DATA_DIR/repos/<owner>/<name>.git` (`git fetch` перед розрахунком). Автентифікація git — через `GIT_ASKPASS`-хелпер, що отримує токен підключення репозиторію зі змінної середовища процесу; токен ніколи не записується в URL remote, `.git/config` чи credential store. Для fine-grained PAT потрібне право *Contents: read*; якщо його немає, churn для репозиторію отримує статус `error` з поясненням.
2. Розраховувати лише змерджені PR, для яких вікно вже минуло і `ChurnResult` відсутній.
3. Набір «PR-комітів»: для squash — `merge_commit_sha`; для merge commit — SHA комітів PR. Rebase позначати `unsupported_merge_method` у v1.
4. `lines_at_merge` = кількість рядків у файлах PR (без виключених), атрибутованих `git blame --line-porcelain` набору PR-комітів на снапшоті `merge_commit_sha`.
5. `snapshot_sha` = останній коміт default branch з `committed_at ≤ merged_at + window`. `lines_surviving` = те саме на цьому снапшоті (з `-M -C` для переміщень, з урахуванням перейменувань файлів).
6. `churn_ratio = 1 - lines_surviving / lines_at_merge`; якщо `lines_at_merge = 0`, результат пропускається.
7. Ліміти: не більше `CHURN_MAX_FILES` (50) файлів на PR, інакше `too_large`. Паралелізм обмежений.

---

## 10. UI і дашборди

### 10.1 Загальне
- **Усі URL вимагають логіну** (`LoginRequiredMiddleware`, Django 5.1+). Виняток — login/logout і password reset.
- Верхня навігація: Огляд · Проєкти · Репозиторії · Люди · PR · Політика · Ревʼю · Налаштування (лише для адмінів).
- **Глобальна панель фільтрів** (стан у query string, щоб посилання можна було поділити):
  - режим: **День** (date picker, стрілки ←/→) або **Період** (пресети: 7д, 30д, 90д, цей місяць, минулий місяць, квартал, довільний);
  - гранулярність для графіків: день / тиждень / місяць (автовибір за довжиною періоду);
  - когорта: всі / AI / не-AI / порівняння;
  - проєкт і репозиторій (multi-select) — там, де доречно.
- Банер «Дані актуальні станом на …» з часом останнього успішного sync.

### 10.2 Читабельність (обовʼязкові вимоги)
- KPI-картки: велике значення, одиниця, дельта до попереднього періоду зі стрілкою; колір залежить від `direction` (зелений = краще, червоний = гірше, сірий = без змін або <5%); під карткою міні-спарклайн.
- Тривалості людською мовою: «3 год 20 хв», «2 д 4 год». Частки у відсотках з одним знаком після коми; великі числа з пробілами.
- Порожні стани з поясненням («Немає змердженних PR за період»). Метрики з вибіркою < `MIN_SAMPLE` (5) показуються сірим з позначкою «мала вибірка».
- Таблиці: сортування за будь-якою колонкою, sticky header, пагінація, пошук, експорт поточного вигляду в CSV і XLSX (див. 10.6), вирівнювання чисел праворуч, бейджі статусів (AI tool, severity).
- Графіки: підписані осі, легенда, тултіпи з точними значеннями; AI/не-AI мають однакові кольори всюди; палітра доступна для дальтоніків; коректний вигляд у світлій і темній темах (див. 10.5).
- Адаптивність: дашборди читаються на ноутбуці та планшеті; на телефоні допустимо колонками.

### 10.3 Сторінки

**Огляд (загальний рівень) / Проєкт / Репозиторій** — однаковий шаблон з різним scope:
- KPI-ряд 1 (adoption): частка AI-PR, disclosure rate, відкриті порушення, AI-активні люди.
- KPI-ряд 2 (flow): змерджено PR, lead time p50, time to first review p50, відкриті/застарілі PR.
- KPI-ряд 3 (якість): rework rate, CI first-pass, revert rate, churn 21д — кожен з порівнянням AI проти не-AI.
- Графіки:
  1. Throughput за інтервалами, stacked AI / не-AI.
  2. Частка AI-PR і disclosure rate (лінії).
  3. Lead time і time to first review (p50 і p90).
  4. Розподіл розміру PR (stacked за бакетами).
  5. Churn і rework: AI проти не-AI (згруповані стовпці).
  6. Нові порушення за правилами (stacked).
- Таблиці:
  - на загальному рівні — **проєкти** (рядок = проєкт, колонки = ключові метрики з дельтами);
  - на рівні проєкту — **репозиторії** і **люди**;
  - на рівні репозиторію — **люди** і останні PR.

**Режим «День»** (для будь-якого scope):
- KPI: відкрито, змерджено, закрито без мерджу, ревʼю, AI-PR змерджено, нові порушення.
- Стан на кінець дня: відкриті, чекають ревʼю > 24 год, застарілі.
- Списки PR, відкритих і змердженних цього дня (з бейджами AI і порушень).
- Таблиця активності людей за день: відкрив, змерджив, відревʼюїв, коментарів.

**Люди** (доступно всім користувачам — лідам, з урахуванням обмеження за проєктами, див. 11):
- Таблиця людей: PR, частка AI, disclosure rate, порушення, lead time p50, розмір p50, rework, ревʼю дано, час відповіді на ревʼю, churn. За замовчуванням сортування за іменем, **не** рейтинг.
- Сторінка людини — робочий інструмент ліда для 1:1: ті самі KPI + порівняння з медіаною проєкту/команди та з попереднім періодом, графіки динаміки, інструменти AI, список PR і порушень, навантаження на ревʼю, приватні нотатки ліда (`Person.notes`).

**PR**:
- Список з фільтрами: scope, автор, стан, AI status, tool, розмір, наявність порушень, дати.
- Детальна сторінка: посилання на GitHub, таймлайн (commit → open → ready → reviews → merge), метрики PR, AI-сигнали з evidence, disclosure, файли (з позначками test/excluded/sensitive), порушення з діями, churn.

**Політика**:
- KPI дотримання, графік порушень, таблиця порушень з фільтрами і масовими діями (acknowledge/waive з коментарем), розподіл інструментів, список PR з `DISCLOSURE_MISMATCH`.

**Ревʼю**:
- Навантаження за ревʼюерами (стовпці), теплова карта «автор → ревʼюер», review load share, PR, що чекають ревʼю.

**Налаштування** (роль admin):
- Підключення GitHub (створення, перевірка, заміна токена, деактивація, статуси й терміни дії — див. 5.1).
- Організації й репозиторії (discovery через вибране підключення, активація, `sync_since`, перепривʼязка до іншого підключення).
- Проєкти (CRUD, призначення репозиторіїв).
- Люди: зіставлення identity (черга нерозпізнаних, merge двох Person, позначки bot/exclude).
- AI-політика, sensitive paths, правила детекції (з кнопкою «перевірити правило на останніх N PR»).
- Виключені шляхи, тестові шляхи, пороги (`STALE_DAYS`, `MIN_SAMPLE`, розміри бакетів, churn window) — модель `AppSetting` з типізованими значеннями і дефолтами.
- Синхронізація: запуск, історія, помилки, rate limit.
- Користувачі та ролі (або посилання на Django admin).

Django admin підключений для всіх моделей (read-only для сирих GitHub-даних).

### 10.4 Графіки — технічно
- Views віддають JSON для графіків окремими HTMX/fetch-ендпоінтами (`/api/charts/<chart_key>?…`), а шаблон ініціалізує Chart.js.
- Один модуль `static/js/charts.js` зі спільними налаштуваннями (кольори, форматери тривалостей і відсотків).

---

### 10.5 Світла і темна теми
- Три режими: **Системна** (за замовчуванням, `prefers-color-scheme`), **Світла**, **Темна**. Перемикач у верхній навігації (іконка з випадним меню), поруч із перемикачем мови.
- Вибір зберігається **на сервері** для користувача (`UserPreference.theme`), щоб він був однаковим на різних пристроях, і дублюється в cookie (для сторінки логіну та миттєвого застосування).
- **Без мерехтіння при завантаженні:** атрибут `data-theme` виставляється на `<html>` на сервері. Для режиму «Системна» невеликий inline-скрипт у `<head>` визначає тему до рендеру CSS.
- **Кольори лише через дизайн-токени:** CSS custom properties (`--bg`, `--surface`, `--surface-2`, `--border`, `--text`, `--text-muted`, `--accent`, `--good`, `--bad`, `--neutral`, `--warning`, `--series-ai`, `--series-non-ai`, `--series-1..8`, `--grid`, `--tooltip-bg`), визначені для `[data-theme="light"]` і `[data-theme="dark"]`. Хардкод кольорів у шаблонах і JS заборонено (тест: grep на `#hex`/`rgb(` поза файлом токенів).
- Якщо використовується Tailwind: `darkMode: ['selector', '[data-theme="dark"]']`, кольори мапляться на ті самі токени.
- **Графіки:** `charts.js` читає кольори з `getComputedStyle` (серії, сітка, осі, легенда, тултіпи) і перемальовує всі графіки при зміні теми (подія `themechange` та слухач `matchMedia` для системного режиму). Кольори AI / не-AI і семантика «краще/гірше» однакові за змістом в обох темах, але підібрані під фон кожної.
- **Контраст** — WCAG 2.1 AA в обох темах (текст ≥ 4.5:1, KPI-числа та елементи графіків ≥ 3:1). Статуси не передаються лише кольором: стрілки ▲▼, іконки, текстові бейджі.
- Темна тема — не інверсія: фон не чисто чорний (приблизно `#0f1115`–`#161a20`), поверхні трохи світліші за фон, приглушені кольори, без чисто білого тексту на великих площах.
- Покривається все: таблиці (зебра, hover, sticky header), форми, бейджі, модалки, порожні стани, date picker, теплова карта ревʼю (окрема шкала для кожної теми), сторінки 403/404/500. Django admin використовує свою вбудовану підтримку тем.
- Друк і експорт графіків у PNG — завжди у світлій темі.

### 10.6 Експорт у XLSX

**Два типи експорту:**
1. **Експорт таблиці** — кнопка «Експорт ▾ → XLSX / CSV» біля кожної таблиці (проєкти, репозиторії, люди, PR, порушення, ревʼю, активність за день). Експортується **поточний вигляд з усіма рядками** (не лише сторінка пагінації), з урахуванням фільтрів, сортування і пошуку.
2. **Звіт дашборду** — кнопка «Завантажити звіт (XLSX)» на сторінках Огляд / Проєкт / Репозиторій / Людина / Політика, у режимах «День» і «Період». Багатоаркушний файл:
   - `Зведення` — scope, період, попередній період, KPI з значенням, значенням за попередній період, дельтою і когортами AI / не-AI;
   - `Динаміка` — часові ряди всіх графіків сторінки (рядок = інтервал) + нативні Excel-графіки для ключових рядів (throughput, частка AI, lead time);
   - аркуші таблиць сторінки (`Проєкти` / `Репозиторії` / `Люди`);
   - `PR` — усі PR scope за період з ключовими полями (repo, номер, посилання, автор, стан, дати, розмір, AI status, tools, disclosure, rounds, churn, кількість порушень);
   - `Порушення` — порушення за період;
   - `Метрики` — довідник: назва, опис, формула, одиниця, напрям (з реєстру метрик);
   - `Параметри` — фільтри, користувач, час генерації, час останнього sync, версія інструмента, налаштування когорти AI.

**Єдиний шар експорту:**
- Таблиці та звіти описують колонки один раз (`ExportColumn(key, title, type, width, number_format)`, типи: text, int, float, percent, duration, datetime, date, url, badge-list). CSV- і XLSX-рендерери використовують ці описи, django-tables2 — теж, щоб набір колонок в UI і файлі збігався.
- Код у `apps/dashboards/exports/`: `columns.py`, `xlsx.py`, `csv.py`, `reports.py`.

**Форматування XLSX (обовʼязково):**
- Жирний заголовок, freeze panes на першому рядку, autofilter, ширина колонок за вмістом (з обмеженням), перенесення довгого тексту лише в колонках-описах.
- Числа — справжні числа, не рядки. Відсотки з форматом `0.0%`. Тривалості — числом у годинах з форматом `0.0` і назвою колонки «…, год» (не текст «2 д 4 год»), щоб по них можна було рахувати.
- Дати й час — справжні Excel-дати в `REPORT_TIMEZONE` (Excel не підтримує таймзони; назва таймзони вказується на аркуші `Параметри`), формат `yyyy-mm-dd hh:mm`.
- Посилання на PR і репозиторії — клікабельні гіперпосилання.
- Порожні значення метрик (`None`) — порожня клітинка, не 0.
- Умовне форматування дельт (зелений/червоний за `direction`), поміркована кольорова схема, завжди світла незалежно від теми UI.
- Аркуші з назвами до 31 символу, без заборонених символів.

**Безпека і доступ:**
- Захист від formula injection: `strings_to_formulas=False`; у CSV значення, що починаються з `=`, `+`, `-`, `@`, префіксуються апострофом.
- Експорт проходить через той самий `scope_for_user`: користувач з обмеженням за проєктами не отримує чужих даних ні в таблицях, ні у звітах.
- Кожен експорт записується в AuditEntry (хто, що, фільтри, кількість рядків).
- Приватні нотатки ліда (`Person.notes`) у звіти **не включаються**.

**Обсяги:**
- До `EXPORT_SYNC_MAX_ROWS` (за замовчуванням 20 000 рядків сумарно) файл генерується в запиті (`constant_memory`).
- Більші експорти ставляться в huey-задачу. Користувач бачить статус через HTMX polling, а готовий файл зберігається в `DATA_DIR/exports/` і доступний на сторінці «Мої експорти» (модель `ExportJob`: user, kind, params, language, status, file, rows, created_at, expires_at). Файли автоматично видаляються через 7 днів. Завантажити файл може лише його автор.
- Імена файлів: `pr-radar_<scope-slug>_<from>_<to>.xlsx` (ASCII, англійською, незалежно від мови UI), для режиму «День» — `…_<date>.xlsx`.

### 10.7 Мови інтерфейсу (English / Українська)
- `LANGUAGE_CODE = "en"`, `LANGUAGES = [("en", "English"), ("uk", "Українська")]`, `USE_I18N = True`, `LocaleMiddleware`, `LOCALE_PATHS`.
- **Мова за замовчуванням — англійська.** Автовизначення з `Accept-Language` вимкнено за замовчуванням (опція `AppSetting.detect_browser_language`).
- **Перемикач** у верхній навігації (поруч із перемикачем теми, `EN / UK`) і на сторінці логіну. Вибір зберігається в `UserPreference.language` і в cookie `django_language`; після логіну застосовується мова з профілю. URL без мовних префіксів (без `i18n_patterns`).
- **Усі рядки інтерфейсу перекладні:** шаблони (`{% translate %}`, `{% blocktranslate %}` з `count` для множини), Python (`gettext_lazy` у моделях, формах, choices, `verbose_name`, реєстрі метрик, правилах політики, статусах підключень, повідомленнях `messages`), JS (`JavaScriptCatalog` + `gettext`/`ngettext`/`interpolate` у `charts.js` і HTMX-фрагментах). Конкатенація перекладених фрагментів заборонена — лише повні речення з іменованими плейсхолдерами.
- **Множина:** завжди `ngettext` (в українській три форми: 1 PR / 2 PR-и / 5 PR-ів тощо — формулювання обрати так, щоб звучало природно).
- **Форматування за локаллю:** числа (`1,234.5` / `1 234,5`), відсотки, дати (Django formats + власні `formats/uk/formats.py` за потреби), назви днів і місяців у графіках і date picker, людські тривалості (`3h 20m` / `3 год 20 хв`) через спільний форматер у Python і JS.
- **Що не перекладається:** дані з GitHub (назви PR, репозиторіїв, логіни), назви проєктів і людей, введені користувачем, ключі метрик і коди правил, логи, AuditEntry (англійською, з кодами).
- **Тексти, згенеровані системою** (порушення, результати перевірок підключень, помилки sync в UI), зберігаються як код + параметри й рендеряться мовою поточного користувача.
- **Експорт:** назви аркушів, заголовки колонок, значення enum-ів і довідник метрик у XLSX/CSV — мовою користувача на момент експорту (для фонових експортів мова фіксується в `ExportJob.language`); мова вказується на аркуші параметрів. Імена файлів — завжди ASCII англійською. Формати чисел і дат у XLSX — нативні Excel-формати (локаль відображення визначає Excel).
- **Переклади:** `locale/uk/LC_MESSAGES/django.po` і `djangojs.po` комітяться; `.mo` збираються `compilemessages` (крок у `docs/SETUP.md` і в `make`/`just` задачі). Український переклад робиться **в тій самій фазі**, де зʼявляються нові рядки.
- **Контроль повноти (тести/CI):**
  - `makemessages --check` (або еквівалентний скрипт) — `.po` актуальні щодо коду;
  - через `polib`: в `uk` немає порожніх `msgstr`, `fuzzy`, розбіжностей плейсхолдерів (`%(name)s`, `{name}`) між msgid і msgstr;
  - smoke-тест рендерить усі сторінки в `en` і `uk`; в `uk`-рендері немає відомих англійських рядків із навігації/KPI (простий список-канарка).
- **Верстка** витримує довші українські рядки: кнопки й KPI-картки не ламаються, заголовки колонок переносяться.
- `docs/TRANSLATIONS.md` (англійською): як додати рядок, оновити й скомпілювати переклади, як додати нову мову.

## 11. Доступ і безпека

- Користувачі — лише ліди/керівники. Дві групи: `admin` (усе, включно з налаштуваннями, користувачами, токеном/синхронізацією) і `lead` (усі дашборди, сторінки людей, PR, керування порушеннями, запуск sync).
- Опційне обмеження за проєктами: `UserProjectAccess(user, project)`. Якщо записів для користувача немає — бачить усе. Якщо є — бачить лише ці проєкти, їхні репозиторії та людей, які мали активність у них; загальний рівень для нього = сума доступних проєктів. Фільтрація централізована в одному місці (`scope_for_user`), не в кожному view.
- Дозвіл `catalog.manage_settings` — лише admin.
- Створення першого адміна: `manage.py createsuperuser`; сторінки реєстрації немає.
- CSRF скрізь; секрети застосунку (`SECRET_KEY`, `FIELD_ENCRYPTION_KEYS`) лише з `.env`, GitHub-токени — лише в зашифрованому вигляді в БД (див. 5.1); керувати підключеннями може лише admin, створення/заміна/видалення підключення пишеться в AuditEntry (без токена); `DEBUG=False` у prod-налаштуваннях; security headers через Django settings.
- Тексти описів і коментарів з GitHub рендеряться з escaping; markdown не рендериться у v1.
- Audit: зміни налаштувань, політики та статусів порушень логуються (модель `AuditEntry`: хто, коли, що, було/стало).

---

## 12. Тестування

- Жодних живих запитів до GitHub у тестах: GraphQL-відповіді як JSON-фікстури в `tests/fixtures/github/`, мок через `respx`.
- Покриття обовʼязково:
  - парсер трейлерів і disclosure (включно з кривими шаблонами);
  - кожне правило детекції та кожне правило політики (позитивний і негативний кейси, ідемпотентність, auto-resolve);
  - кожна метрика на ручно порахованому наборі даних (`factory_boy`), включно з межами днів у `Europe/Kyiv` і переходом на літній час;
  - дедуплікація репозиторіїв у загальному рівні;
  - пагінація і rate-limit backoff у sync-клієнті; незалежний rate limit для кількох підключень;
  - підключення: шифрування/розшифрування, ротація ключа, перевірка (ok / degraded / invalid / expired / потрібна SSO) на мок-відповідях, 401 під час sync пропускає лише репозиторії цього підключення, bootstrap з `GITHUB_TOKEN`;
  - витік секретів: після sync з помилками та після експорту токен (і його фрагменти, окрім last4) не знаходиться в БД у відкритому вигляді, у логах, `SyncRun.error_log`, HTML сторінок і файлах експорту;
  - churn на тимчасовому git-репозиторії, створеному в тесті (squash і merge-кейси);
  - доступ: анонім отримує редірект на логін для **кожного** URL (параметризований тест по `urlpatterns`), lead з обмеженням за проєктами не бачить чужих проєктів, репозиторіїв, людей і PR ні в сторінках, ні в API графіків, ні в CSV/XLSX-експорті;
  - XLSX: файл відкривається `openpyxl`; перевірити аркуші, заголовки, типи клітинок (число/дата/відсоток), гіперпосилання, порожні клітинки для `None`, відсутність формул із даних (рядок `=1+1` лишається текстом), відсутність `Person.notes`; великий експорт іде у фонову задачу;
  - smoke-тест рендерингу всіх сторінок на `seed_demo`-даних **в обох темах**; тест на відсутність хардкод-кольорів;
  - збереження вибору теми для користувача.
- `ruff check`, `ruff format --check`, `mypy` (для services/metrics) і `pytest` мають проходити після кожної фази.

---

## 13. Готовність до хостингу (не реалізовувати зараз, але не блокувати)

- Settings розділені на `base`/`local`/`prod`; `DATABASE_URL` через environ, щоб перехід на PostgreSQL був зміною env.
- Жодних raw SQL із SQLite-діалектом.
- `DATA_DIR` (клони, huey-БД, кеш) задається через env.
- Статика через `whitenoise`.
- `docs/SETUP.md` містить розділ «Future deployment» (Docker Compose: web + huey worker + postgres), без реалізації.

---

## 14. Фази реалізації

Кожна фаза: план → реалізація → тести → ревʼю → виправлення → коміт. **Правило для кожної фази:** нові рядки UI одразу мають український переклад, а зміни поведінки відображені в англомовній документації. Після фази оновити `docs/PROGRESS.md` (що зроблено, відхилення від специфікації, відкриті питання).

| # | Фаза | Критерії приймання |
|---|---|---|
| 0 | Каркас: uv, Django, settings, SQLite WAL, auth + LoginRequired, i18n (en/uk, LocaleMiddleware, перемикач мови, `UserPreference`, JS-каталог, форматер тривалостей), англомовний README, базовий layout на CSS-токенах (з `data-theme`), навігація, ruff/mypy/pytest, `.env.example`, `docs/SETUP.md` | `runserver` працює, усі URL закриті логіном, перемикання мови працює, тести на це і перевірка повноти перекладів зелені |
| 1 | Моделі catalog/activity/ai_detection/policy/metrics/churn/sync + міграції + Django admin + `AppSetting` з дефолтами | міграції з нуля проходять; admin відкривається |
| 2 | `GitHubConnection` (шифрування, форма, перевірка підключення, статуси, банер, bootstrap з `.env`, ротація ключа) + GitHub-клієнт (GraphQL, пагінація, rate limit на підключення, ретраї) + discovery репозиторіїв в UI через підключення + `sync` command + SyncRun + huey-задача і кнопка в UI | sync на фікстурах створює коректні PR/commits/reviews/files; повторний sync нічого не дублює; токен не зустрічається в БД у відкритому вигляді, логах і відповідях |
| 3 | Identity resolution, боти, UI зіставлення людей, похідні поля PR (розмір, бакети, test/excluded, revert, rubber stamp, self-merge, rounds) | тести похідних полів зелені |
| 4 | AI detection: правила з YAML, детектори, disclosure-парсер, `ai_status`, UI правил з перевіркою на останніх PR, рекомендований PR-шаблон | тести кожного детектора зелені |
| 5 | Рушій політики, порушення, auto-resolve, UI політики з діями, AuditEntry | тести кожного правила зелені |
| 6 | Реєстр метрик, калькулятори, DailyRollup, `recompute`, кеш, `metrics_doc` → `docs/METRICS.md` | тести всіх метрик зелені, включно з таймзоною |
| 7 | `seed_demo` + дизайн-токени і перемикач світлої/темної теми + дашборди Огляд/Проєкт/Репозиторій у режимах «Період» і «День», глобальні фільтри, KPI-картки, графіки, таблиці, шар експорту (CSV + XLSX для таблиць) | smoke-тести сторінок; ручна перевірка на demo-даних |
| 8 | Люди (список, сторінка, нотатки), список і детальна сторінка PR, сторінка Ревʼю, обмеження доступу за проєктами, XLSX-звіти дашбордів, фонові експорти (`ExportJob`, «Мої експорти») | тести доступу й експорту зелені |
| 9 | CI first-pass, follow-up fix евристика, churn (клони, blame, `compute_churn`, huey-розклад) | churn-тест на тимчасовому репо зелений |
| 10 | Полірування: порожні стани, мала вибірка, фінальна перевірка обох тем і контрасту, вичитка українського перекладу та верстки з довгими рядками, повна англомовна документація (`docs/*`), продуктивність (індекси, `select_related`, профілювання на ~50 репо / 20k PR у seed), документація | дашборд 90 днів на seed-даних рендериться < 1.5 с локально |

---

## 15. Рішення за замовчуванням (для автономної роботи)

- Бекфіл: 180 днів.
- Когорта AI: `ai_explicit` + `ai_disclosed` (без `ai_suspected`).
- Тривалості: календарні години (робочі години — майбутня опція, закласти в `MetricDef` параметр).
- Міграції Django і lock-файли виключені з розміру PR; тестові шляхи: `tests/`, `test_*.py`, `*_test.py`, `*.test.ts(x)`, `*.spec.ts(x)`, `*Tests.swift`, `__tests__/`.
- Бот-логіни: суфікс `[bot]`, `dependabot`, `renovate`, `github-actions`.
- Мова UI за замовчуванням: англійська; друга мова — українська; автовизначення мови браузера вимкнене.
- Документація, код, коментарі, коміти, логи: англійська.
- Тип підключення за замовчуванням у формі: fine-grained PAT.
- Перевірка підключень: щодня + після помилок 401/403.
- Тема за замовчуванням: «Системна».
- Експорт тривалостей — у годинах (десяткове число).
- Якщо GitHub не повертає потрібне поле, метрика для PR = `None` і не входить у вибірку (не 0).
- Якщо вибір не очевидний, обирати простіший варіант, записати в `docs/DECISIONS.md` і продовжувати.

---

## Clarifications (pre-run)

Answered by the author at intake on 2026-09-17. These outrank the run's own preferences.

- **CSS:** Tailwind standalone CLI, not Pico.css. Compiled CSS is committed; no node build step for the application itself. Section 2 left this open — it is now settled.
- **GitHub credentials:** no `GITHUB_TOKEN` is available locally and no live GitHub API call may be made at any point in the run. Every GitHub interaction is exercised against JSON fixtures under `tests/fixtures/github/`, mocked with `respx`, per section 12. The `.env` bootstrap path of section 5.1 is implemented and unit-tested, but never executed against the real API.
- **Scope:** all phases 0 through 10 of section 14, in order.
- **Test gate per phase:** `ruff check`, `ruff format --check`, `mypy` (for `services/` and `metrics/`), and `uv run pytest -q` must all pass before a phase is considered done.
- **Branching:** each phase merges into `main` after its gate passes.
