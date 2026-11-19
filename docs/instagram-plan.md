# Plan: multi-platform refactor and Instagram (simulated)

**Status:** plan only, 2026-11-19. Nothing here is built. Real Instagram publishing and the R2 media host are **future** work, blocked on the presigned-URL spike and on D48 being accepted (`docs/instagram-api-notes.md`, section 13). Simulated Instagram (steps A–F) is allowed by **D49**; real Instagram stays forbidden until D48 is accepted.

**Labels:** **[existing]** is how the code works today (checked against the source on 2026-11-19). **[proposed]** is what this plan would build. **[future]** is out of scope until the spike and D48.

**Goal:** Instagram plugs into the same workflow graph, storage, scheduling, CLI, API and dashboard as LinkedIn. No platform `if`/`else` in shared code, no second copy of any logic, and **LinkedIn behaves exactly as today** (same outputs, logs, exit codes and help texts until a step says otherwise).

---

## 1. Inventory: where LinkedIn is hard-coded today [existing]

**G** = generic already · **P** = needs to become platform-aware · **L** = truly LinkedIn-only (stays as it is, behind the adapter)

### Core package (`backend/src/social_media_auto_poster/`)

| File                                                                                 | Function / name                                                                                                                                                                                                                                                                                                                                                                                                                          | What it does                                                                                                                       |                                                                |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `models.py`                                                                        | `Platform`                                                                                                                                                                                                                                                                                                                                                                                                                             | Enum already has `INSTAGRAM` and `FACEBOOK`; the docstring says "only LinkedIn is processed"                                   | G (docstring only)                                             |
| `config.py`                                                                        | `Settings.platform_dir/incoming_dir/archive_dir/failed_dir`, `missing_platform_dirs`, `ensure_data_dirs`                                                                                                                                                                                                                                                                                                                           | Folders per platform; `ensure_data_dirs` creates all three platforms                                                             | G                                                              |
| `ingestion/scanner.py`                                                             | `scan_incoming(folder, platform)`                                                                                                                                                                                                                                                                                                                                                                                                      | Pairing; the platform is a parameter                                                                                               | G                                                              |
| `ingestion/stability.py`                                                           | `is_file_stable`                                                                                                                                                                                                                                                                                                                                                                                                                       | —                                                                                                                                 | G                                                              |
| `ingestion/validation.py`                                                          | `CAPTION_RULES = {Platform.LINKEDIN: linkedin_caption_problem}`                                                                                                                                                                                                                                                                                                                                                                        | Platform caption rule (D45); the image check (`EXPECTED_TYPE`: jpg/jpeg/png) is shared                                           | P: becomes a rules registry                                    |
| `ingestion/intake.py`                                                              | `add_post(..., platform=Platform.LINKEDIN)`                                                                                                                                                                                                                                                                                                                                                                                            | Upload intake: default platform, ".jpg, .jpeg or .png" text, `MAX_IMAGE_BYTES` 10 MB                                             | P (default stays LinkedIn)                                     |
| `graph/state.py`                                                                   | `JobState.platform`, `WorkflowContext.publisher`                                                                                                                                                                                                                                                                                                                                                                                     | One publisher per run, chosen by the caller                                                                                        | G                                                              |
| `graph/nodes.py`                                                                   | every node                                                                                                                                                                                                                                                                                                                                                                                                                               | Uses `candidate.platform` from state. `publish` calls `context.publisher.publish(content)`                                   | G. `publish` needs the optional reference hook (section 2.4) |
| `graph/workflow.py`, `graph/resume.py`, `graph/checkpoints.py`                 | —                                                                                                                                                                                                                                                                                                                                                                                                                                       | No platform logic                                                                                                                  | G                                                              |
| `storage/database.py`                                                              | migrations 1–2, `backup_before_migration`                                                                                                                                                                                                                                                                                                                                                                                             | `jobs` and `schedules` both have `platform`; unique indexes include it; the backup is generic (`.bak-v<N>`)                | G                                                              |
| `storage/jobs.py`                                                                  | `find_blocking_duplicate(platform=…)`                                                                                                                                                                                                                                                                                                                                                                                                 | Duplicate key per platform                                                                                                         | G                                                              |
| `storage/jobs.py`                                                                  | `has_jobs(mode=)`                                                                                                                                                                                                                                                                                                                                                                                                                      | "Any real job?", **across all platforms**                                                                                    | P: used by the archive check below                             |
| `storage/schedules.py`                                                             | `find_active`, `due(platform=…)`, `list_active(platform=None)`                                                                                                                                                                                                                                                                                                                                                                    | Per platform; `due` requires one platform                                                                                        | G (`due` gains `platform=None` = all)                      |
| `services/real_mode.py`                                                            | `posts_only_simulated`                                                                                                                                                                                                                                                                                                                                                                                                                 | Uses `candidate.platform`                                                                                                        | G                                                              |
| `services/real_mode.py`                                                            | `archive_without_history(settings, repo, platform)`                                                                                                                                                                                                                                                                                                                                                                                    | Takes a platform for the folder but calls `has_jobs(mode=REAL)` without one: a real LinkedIn job would silence Instagram's check | P                                                              |
| `services/archive.py`                                                              | `archive_post`                                                                                                                                                                                                                                                                                                                                                                                                                         | —                                                                                                                                 | G                                                              |
| `services/archive.py`                                                              | `write_error_report`                                                                                                                                                                                                                                                                                                                                                                                                                   | Text "Check LinkedIn before re-posting"                                                                                            | P (display name)                                               |
| `services/reconcile.py`                                                            | `reconcile_interrupted_jobs`, `_recover_real`                                                                                                                                                                                                                                                                                                                                                                                        | Real `PUBLISHING` → `NEEDS_REVIEW` for every platform (text says "check the platform")                                        | G now; P later for Instagram's remote check (section 7)        |
| `services/scheduling.py`                                                           | `check_schedulable`                                                                                                                                                                                                                                                                                                                                                                                                                    | Calls `platforms.linkedin.image_size.image_problem` for real schedules; message "cannot be posted to LinkedIn"                   | P                                                              |
| `services/scheduling.py`                                                           | `parse_publish_at`, `select_due`, `content_state`                                                                                                                                                                                                                                                                                                                                                                                  | —                                                                                                                                 | G                                                              |
| `platforms/base.py`                                                                | `Publisher` protocol                                                                                                                                                                                                                                                                                                                                                                                                                   | `publish(post, /) -> PublishResult`; raising means `NEEDS_REVIEW`                                                              | G                                                              |
| `platforms/simulator.py`                                                           | `Simulator`                                                                                                                                                                                                                                                                                                                                                                                                                            | Never inspects or sends anything                                                                                                   | G                                                              |
| `platforms/linkedin/caption.py`                                                    | `escape_caption`, `caption_length`, `caption_problem` (3,000 UTF-16 units)                                                                                                                                                                                                                                                                                                                                                         | —                                                                                                                                 | L                                                              |
| `platforms/linkedin/image_size.py`                                                 | `image_size` (PNG IHDR, JPEG SOF walk), `image_problem` (pixel limit)                                                                                                                                                                                                                                                                                                                                                                | The header reader is generic; the pixel limit is LinkedIn's                                                                        | split: reader → G, limit → L                                 |
| `platforms/linkedin/client.py`, `publisher.py`, `credentials.py`, `check.py` | HTTP client, publisher, `.env` token, `linkedin-check`                                                                                                                                                                                                                                                                                                                                                                               | —                                                                                                                                 | L                                                              |
| `operations/common.py`                                                             | `PLATFORM = Platform.LINKEDIN`, `REAL_CONFIRMATION = "publish to LinkedIn"`                                                                                                                                                                                                                                                                                                                                                          | The single constant most hard-coding flows through                                                                                 | P                                                              |
| `operations/linkedin.py`                                                           | `_real_publisher`, `_linkedin_client`, `linkedin_status`, `RealPublishingUnavailable`                                                                                                                                                                                                                                                                                                                                            | **Patch seams:** tests patch `linkedin_ops._real_publisher` (15×) and `_linkedin_client` (10×)                         | L (seams must stay)                                            |
| `operations/run.py`                                                                | `run_once` ("Run started: platform=%s" with `PLATFORM`), `_run_locked` (`missing_platform_dirs(…, PLATFORM)`, text "does not have its LinkedIn folders", `schedules.due(platform=PLATFORM)`), `_publisher_for` (`archive_without_history(…, PLATFORM)`, `linkedin_ops._real_publisher`), `_process` (`scan_incoming(…, PLATFORM)`), `_simulated_refusal_text` ("on LinkedIn"), `_footer` ("sent to LinkedIn") | The run path                                                                                                                       | P                                                              |
| `operations/scan.py`                                                               | `scan_preview` (`scan_incoming(folder, PLATFORM)`, `own_folder`), `_active_schedules` (`list_active(platform=PLATFORM)`)                                                                                                                                                                                                                                                                                                       | Read-only preview                                                                                                                  | P                                                              |
| `operations/schedules.py`                                                          | `schedule_post`/`unschedule_post`/`reconfirm_post` (`find_active(platform=PLATFORM)`), `_find_post` (`incoming_dir(PLATFORM)`), `_token_warnings` (`load_linkedin_credentials`)                                                                                                                                                                                                                                          | Schedules                                                                                                                          | P (`_token_warnings` → adapter)                             |
| `operations/schedules.py`                                                          | `list_schedules`, `find_schedule`                                                                                                                                                                                                                                                                                                                                                                                                    | All platforms already                                                                                                              | G                                                              |
| `operations/resolve.py`                                                            | `resolve_job`                                                                                                                                                                                                                                                                                                                                                                                                                          | Platform comes from the job record                                                                                                 | G (gains `--check` later)                                    |
| `cli/commands.py`                                                                  | `_scan` ("Scanning LinkedIn incoming"), `_run` (banner "REAL MODE: posts are published to LinkedIn", "Processing LinkedIn incoming"), schedule/reconfirm ("REAL: to be published to LinkedIn")                                                                                                                                                                                                                                       | Terminal texts                                                                                                                     | P (texts from the adapter)                                     |
| `cli/commands.py`                                                                  | `_linkedin_check`                                                                                                                                                                                                                                                                                                                                                                                                                      | —                                                                                                                                 | L                                                              |
| `cli/parser.py`                                                                    | descriptions and help ("LinkedIn only", "Publish to LinkedIn for real"), `linkedin-check`                                                                                                                                                                                                                                                                                                                                              | Help is byte-identical since D47                                                                                                   | P (help changes only in step E)                                |
| `cli/output.py`                                                                    | job and schedule printing                                                                                                                                                                                                                                                                                                                                                                                                                | Uses `job.platform` for folders                                                                                                  | G                                                              |
| `main.py`                                                                          | docstring, re-exports                                                                                                                                                                                                                                                                                                                                                                                                                    | —                                                                                                                                 | G                                                              |

### API (`backend/app/`)

| File                                         | What                                                                           |                                       |
| -------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------- |
| `services/job_service.py`                  | `scan`, `add_post` use `PLATFORM`; `linkedin()` → `linkedin_status` | P (scan, add_post) / L (`linkedin`) |
| `models/schemas.py`                        | a **second copy** of `REAL_CONFIRMATION = "publish to LinkedIn"`       | P (take it from the adapter)          |
| `routers/run.py`, `routers/schedules.py` | check `confirm == REAL_CONFIRMATION`                                         | P                                     |
| `routers/linkedin.py`                      | `GET /api/linkedin/status`                                                   | L                                     |
| `routers/scan.py`, `routers/posts.py`    | docstrings say "LinkedIn incoming"                                             | P                                     |
| `main.py`                                  | `/api/health` returns `linkedin_version`                                   | L (kept)                              |

### Dashboard (`frontend/src/`)

| File                                                                                                    | What                                                                                                                             |                                       |
| ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `lib/platforms.tsx`                                                                                   | `PLATFORMS` with `live: true` only for LinkedIn                                                                              | P (Instagram "test only")             |
| `lib/postRules.ts`                                                                                    | copies of the backend rules: `MAX_CAPTION_CHARS = 3000`, LinkedIn message, `CONFIRM_PHRASE = 'publish to LinkedIn'`, JPG/PNG | P (per platform)                      |
| `components/IntakeStudio.tsx`                                                                         | "only LinkedIn can be chosen", "Fits LinkedIn's length limit"                                                                    | P                                     |
| `components/ScheduleFields.tsx`, `RunnerConsole.tsx`, `ResolveModal.tsx`, `JobDetailsModal.tsx` | "Posted to your LinkedIn", "Post N to LinkedIn", "look on LinkedIn", `urn:li:` post links                                      | P (texts and links per platform)      |
| `components/LinkedInSentinel.tsx`, `context/ApiContext.tsx` (`linkedInStatus`)                    | Token card                                                                                                                       | L (Instagram gets a "test only" card) |

### Tools and tests

| File                                               | What                                                                                                                                                |                                          |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| `scripts/app.py`                                 | sandbox tester; `_SandboxRealPublisher` (`urn:li:share:sandbox-…`)                                                                             | L (unchanged)                            |
| `scripts/register-schedule-task.ps1`             | runs `run --scheduled`                                                                                                                            | G (unchanged; see section 5)             |
| `tests/linkedin_fake.py`, `test_linkedin_*.py` | fake LinkedIn                                                                                                                                       | L                                        |
| `tests/unit/test_linkedin_checks.py`             | imports `image_size`/`image_problem` from `platforms.linkedin.image_size`                                                                     | L (that import path must keep working)   |
| Tests pinning LinkedIn text                        | e.g. "Run started: platform=LinkedIn mode=simulated", "Processing LinkedIn incoming:", "Simulation mode: nothing was sent to LinkedIn in this run." | must stay green unchanged through step A |

**Summary:** the graph, storage, scanner, archive and resume are generic already. The hard-coding is concentrated in `operations/common.PLATFORM` (used by run, scan, schedules and the API), the caption-rules dict, the LinkedIn pixel limit in `check_schedulable`, and user-facing texts. Two real gaps: `has_jobs` ignores the platform, and there are two copies of the confirmation phrase.

## 2. Platform adapter [proposed]

Two layers, because `ingestion/` must not import `operations/` (layering) and validation must work with no token, no network and no HTTP client.

### 2.1 `PostRules`: pure rules, in `platforms/rules.py`

Used by validation, intake, scheduling, scan and `GET /api/platforms`.

```python
@dataclass(frozen=True)
class PostRules:
    platform: Platform
    display_name: str                      # "LinkedIn": every user-facing text uses this
    extensions: tuple[str, ...]            # allowed image extensions (subset of the shared EXPECTED_TYPE)
    max_image_bytes: int | None            # None: only the shared intake cap
    caption_max_chars: int
    caption_max_hashtags: int | None
    caption_max_mentions: int | None
    caption_problems: Callable[[str], list[str]]          # every mode (D45)
    media_problems: Callable[[bytes], list[str]]          # every mode (Instagram: aspect, MPO, CMYK...)
    real_media_problems: Callable[[bytes], list[str]]     # real mode only (LinkedIn pixel limit, as today)

RULES: dict[Platform, PostRules]           # LinkedIn now; Instagram in step B. Facebook absent.
def rules_for(platform: Platform) -> PostRules   # raises UnsupportedPlatform
```

- `validate_candidate` keeps the shared checks (extension, signature, empty file, UTF-8, empty caption, 64 KB cap) and then adds `rules.caption_problems` and `rules.media_problems`. `CAPTION_RULES` disappears; tests only use `validate_candidate`.
- `real_media_problems` exists only to keep LinkedIn exactly as it is: its pixel limit is checked on the real path only (publisher and `check_schedulable` for real schedules). Making it apply in every mode would be a behaviour change and needs its own decision (section 9).
- The numbers are data, so the API can serve them to the dashboard (section 6.5).

### 2.2 `PlatformAdapter`: live parts, in `operations/platforms.py`

```python
class PlatformAdapter(Protocol):
    rules: PostRules
    confirmation_phrase: str               # "publish to LinkedIn"; the API and dashboard compare with it
    real_available: bool                   # Instagram: False until D48 is accepted

    def real_publisher(self, settings: Settings) -> Publisher: ...
        # Raises RealPublishingUnavailable (token missing, expired, rejected): the D40 check, before anything changes.
    def schedule_warnings(self, settings: Settings, publish_at: datetime, now: datetime) -> tuple[str, ...]: ...
        # Reads backend/.env only, no network (today: operations/schedules._token_warnings).
    def remote_checker(self, settings: Settings) -> RemoteChecker | None: ...
        # None: the platform offers no safe check (LinkedIn, D36). Instagram: container status (future, real).

ADAPTERS: dict[Platform, PlatformAdapter]
def adapter_for(platform: Platform) -> PlatformAdapter    # raises UnsupportedPlatform (Facebook)
def supported_platforms() -> tuple[Platform, ...]          # LinkedIn first, so it stays the default
```

`LinkedInAdapter` lives **in `operations/linkedin.py`**. Its `real_publisher` calls the module-level `_real_publisher(settings)`, looked up in that module at call time. The 15 test patches on `linkedin_ops._real_publisher` therefore keep reaching it, and `test_patch_targets.py` passes unchanged (the module both defines and uses the name).

### 2.3 Why each member exists, and what was dropped

| Member                       | Needed because                                                                                                                       | Used by                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- |
| `rules`                    | validation, intake, scheduling and the dashboard need the same numbers and checks                                                    | ingestion, scheduling, API                   |
| `display_name` (in rules)  | today's texts say "LinkedIn"; generating them from one field keeps LinkedIn's wording byte-identical                                 | CLI, run footers, error report, API messages |
| `confirmation_phrase`      | the API and dashboard refuse real runs without it (D43); today it is copied twice                                                    | API routers, dashboard                       |
| `real_available`           | Instagram is registered for simulated use while real is blocked; a real Instagram run is refused with exit 1 before anything changes | `_publisher_for`, API, dashboard           |
| `real_publisher(settings)` | real runs need a platform publisher with the token check first (D40)                                                                 | `operations/run._publisher_for`            |
| `schedule_warnings(...)`   | a real schedule warns about token expiry (D46); token files are per platform                                                         | `operations/schedules`                     |
| `remote_checker(settings)` | Instagram can check a lost publish safely; LinkedIn cannot (returns `None`, behaviour unchanged)                                   | reconcile, `resolve --check`               |

**Dropped** from the suggested list:

- **`data folder`**: already `Settings.incoming_dir(platform)`, derived from the enum value.
- **`validate(post)` as a method**: the platform part is `rules.caption_problems` and `rules.media_problems`, which stay pure functions so validation does not need an adapter.
- **`publish(post, job, mode)`**: the mode is not the adapter's business. Simulated mode stays the shared `Simulator` for every platform ("nothing sent", identical labelling, D4), and real mode is `real_publisher(settings).publish(content)` with the existing `Publisher` protocol.
- **`check_remote(job)` directly on the adapter**: it needs a token and a client, so the adapter builds a `RemoteChecker` only when asked.
- **`status`/`check` for a `linkedin-check`-style command**: not needed until real Instagram exists.

### 2.4 Pre-publish reference hook (Instagram only)

The `Publisher` protocol stays exactly as it is. Fifteen implementations exist (`Simulator`, `LinkedInPublisher`, `_NeverPublish`, `_NothingToPublish`, the sandbox publisher and 10 test fakes), and none of them change.

```python
class ReferenceRecorder(Protocol):
    def __call__(self, *, remote_ref: str | None = None, media_host_key: str | None = None) -> None: ...
        # Commits to the job row (synchronous=FULL) before returning. Raises on failure.

class RecordingPublisher(Publisher, Protocol):
    records_references: Literal[True]
    def publish(self, post: PostContent, /, *, record: ReferenceRecorder) -> PublishResult: ...
```

`nodes.publish` passes `record=` only if `getattr(publisher, "records_references", False)` is true. The recorder calls `JobRepository.update(job_id, remote_ref=…/media_host_key=…)`.

Inside the Instagram publisher, if the recorder raises, `media_publish` has **not** been called, so the publisher returns a definite `FAILED` ("could not record the container ID; nothing was published"), never an exception, which would wrongly become `NEEDS_REVIEW`.

### 2.5 `RemoteChecker`

```python
class RemoteOutcome:   # one of:
    PUBLISHED(post_id)   # the platform confirms the post exists, and its ID is known
    NOT_PUBLISHED(reason)   # provably never published (container EXPIRED or ERROR)
    UNKNOWN(reason)      # cannot tell (FINISHED, IN_PROGRESS, unreadable, PUBLISHED without an ID)

class RemoteChecker(Protocol):
    def check(self, job: JobRecord) -> RemoteOutcome: ...   # read-only; never publishes
```

## 3. Media host [proposed; the real implementation is future]

`platforms/media_host.py`:

```python
class MediaHost(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...
    def signed_url(self, key: str, *, expires_in: timedelta) -> str: ...   # a bearer secret: never logged or stored
    def delete(self, key: str) -> None: ...                                # an absent key is fine
    def sweep(self, *, prefix: str, older_than: datetime) -> tuple[str, ...]: ...   # deletes leftovers, returns their keys

def new_media_key(prefix: str = "ig-tmp/") -> str:   # f"{prefix}{uuid4().hex}.jpg": never the post name
```

- **Order inside the Instagram publisher:** `new_media_key()` → `record(media_host_key=key)` (committed) → `put` → `signed_url` → create the container → `record(remote_ref=container_id)` → poll the status → `media_publish` once → `delete(key)` (always, best effort) → `record(media_host_key=None)` once deleted. Because the key is committed before `put`, a crash can only leave objects whose keys the database knows.
- **Sweep:** at the start of a real Instagram run (after the token check), delete the object of every job that is not `PUBLISHING` and still has a `media_host_key`, then clear the column. A prefix sweep of objects older than 24 hours is the backstop, along with the bucket lifecycle rule (future).
- **`tests/media_host_fake.py`** [proposed]: `InMemoryMediaHost` holds objects in a dict, records every call, can be told to fail on any call, and returns `https://media.invalid/<key>?sig=…` URLs, so a test can check that the URL never appears in a log, the database, a checkpoint or an error report. Like `tests/linkedin_fake.py`, it lives in the tests only.
- **[future]** `R2MediaHost` (SigV4 by hand with `httpx`, or a new dependency: separate decision), its `.env` settings, and an upload test in `instagram-check`.

## 4. Storage: schema v3 [proposed]

Migration 3, appended to `MIGRATIONS` (never editing 1 or 2):

```sql
ALTER TABLE jobs ADD COLUMN remote_ref     TEXT;   -- the platform's pre-publish reference (Instagram: container ID)
ALTER TABLE jobs ADD COLUMN media_host_key TEXT;   -- the media host object to delete; NULL once deleted
CREATE INDEX jobs_with_media ON jobs (media_host_key) WHERE media_host_key IS NOT NULL;
```

- **Nullable, no CHECK.** LinkedIn rows always have `NULL` in both. Neither column is a secret: the token and the presigned URL are never stored.
- **Migration from v2:** `backup_before_migration` already copies `app.db` to `app.db.bak-v2` (or a timestamped name). It is generic, so only a test is added. Older code refuses a v3 database (`SchemaTooNewError`); to go back, restore `app.db.bak-v2`. `open_app_db_read_only` (`scan`, `jobs`, `schedules`) reads v2 or v3 without upgrading. Code must treat a missing column (a v2 database opened read-only) as `NULL`.
- **`JobRecord`** gains `remote_ref` and `media_host_key` (default `None`). `JobRepository.update` accepts them.
- **`has_jobs(mode=, platform=None)`**; `archive_without_history` passes its platform. LinkedIn behaviour is identical as long as no other platform has real jobs, which is true until real Instagram exists.
- **The duplicate key stays per platform** and needs no change: `jobs_one_live_job_per_content ON jobs (platform, mode, media_sha256, caption_sha256)`. The same image and caption can be one live LinkedIn job and one live Instagram job. Schedules stay one active per `(platform, stem_key)`, so the same post name can be scheduled on both platforms, and `find_active_by_content` is per platform.
- **Graph state:** no new fields. The container ID goes to the database through the recorder, not into state. Reconciliation reads the database (CLAUDE.md: "never read business data from checkpoints").

## 5. Scheduling, CLI and API [proposed]

### 5.1 CLI: one option, `--platform`

`--platform {linkedin,instagram}` (case-insensitive, default `linkedin`) on `scan`, `run`, `schedule`, `unschedule` and `reconfirm`. Without it, every command does exactly what it does today. There are no new Instagram commands.

| Command                                     | `--platform`                                                                | Notes                                                                                                                                                                                                               |
| ------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scan`, `run`                           | default `linkedin`                                                          | `run --real --platform instagram` → exit 1 "real Instagram publishing is not available yet; nothing was published", **before anything changes** (no lock side effects beyond "Run started"/"Run finished") |
| `run --scheduled`                         | **default: all supported platforms**; `--platform X` limits it to one | see 5.2                                                                                                                                                                                                             |
| `schedule`, `unschedule`, `reconfirm` | default `linkedin`                                                          | the post is looked up in that platform's `incoming/`; `--real` with Instagram is refused (exit 1) while `real_available` is false                                                                             |
| `schedules`, `jobs`                     | optional filter, default all                                                  | a `PLATFORM` column is printed only when a listed row is not LinkedIn, so today's output stays identical                                                                                                          |
| `resolve <id>`                            | none (the job knows its platform)                                             | **new `--check`**: asks the platform's `RemoteChecker` (read-only, under the run lock). LinkedIn: refused, "LinkedIn offers no way to check; use --published or --failed"                                 |
| `linkedin-check`                          | —                                                                            | unchanged. **[future]** `instagram-check` only once real Instagram exists                                                                                                                                   |

The Task Scheduler script needs no change: `run --real --scheduled` now covers every platform with **real** schedules, and today there are only LinkedIn ones.

### 5.2 `run --scheduled`: still at most one post per run, across platforms

- `schedules.due(platform=None, mode, now)` returns the due schedules of every platform; `select_due` already sorts them oldest `publish_at` first and keeps at most `MAX_SCHEDULED_PER_RUN = 1`. So one run publishes at most one post on one platform, and the 15-minute spacing holds across platforms.
- The publisher (and its token check, data-folder check and archive-history check) is built **only for the platform of the selected schedule**. Nothing due → `_NothingToPublish`, no token check, no network, as today.
- `MISSED` marking covers every platform. As in D46, setup checks run **before** anything changes, so a refused run leaves every schedule as it was.
- **Decided (D49):** a platform whose setup is refused (expired token, missing data folders, archive without history) is **skipped** for that run, with the reason logged and shown. Its schedules are not changed: they stay due and become `MISSED` normally after 6 hours. The oldest due schedule of the remaining platforms is published (still at most one). Still open: that run's exit code (1 or 3, never 0).

### 5.3 API (FastAPI)

| Endpoint                                  | Change                                                                                                                                                                                                                                                                     |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/platforms` **(new)**    | `[{id, name, real_available, confirmation_phrase, rules: {extensions, max_image_bytes, caption_max_chars, caption_max_hashtags, caption_max_mentions, aspect: {min: "4:5", max: "1.91:1"} or null}}]`. The dashboard takes the numbers from here instead of copying them |
| `GET /api/scan`                         | `?platform=` (default LinkedIn); the response already has `platform`                                                                                                                                                                                                   |
| `POST /api/posts`                       | form field `platforms` (one or more; default LinkedIn). Validates against **every** chosen platform first, then writes to each `incoming/`; the result is per platform (section 6.4)                                                                             |
| `POST /api/posts/check` **(new)** | same form, **writes nothing**: returns each platform's validation errors from the backend's own rules (the dashboard's source of truth)                                                                                                                              |
| `POST /api/run`                         | body `platform` (default LinkedIn). Real needs `real: true` and `confirm` equal to **that platform's** phrase; Instagram real → 400 while unavailable                                                                                                         |
| `POST /api/schedules`, `/reconfirm`   | body `platform` (default LinkedIn), same real rules                                                                                                                                                                                                                      |
| `POST /api/jobs/{id}/resolve`           | `{"check": true}` as a third option next to `published_post_id` and `failed`                                                                                                                                                                                         |
| `GET /api/linkedin/status`              | unchanged. **[future]** `GET /api/instagram/status`                                                                                                                                                                                                                |

`app/models/schemas.py` loses its copy of `REAL_CONFIRMATION`; the routers ask `adapter_for(platform).confirmation_phrase`. All existing requests without `platform` behave exactly as now, and API tests keep passing unchanged.

## 6. Instagram rules, in simulated mode [proposed]

All in `platforms/instagram/rules.py`, registered in `RULES`, standard library only. They are checked in validation for **every** mode (as D45 does for captions), so `scan`, simulated runs, uploads and schedules all refuse the same posts. Numbers come from `docs/instagram-api-notes.md` sections 5–6; the uncertain ones are set conservatively.

### 6.1 Image

| Rule                                         | Check                                                                                                                                                                            | Message (example)                                                                        |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| JPEG only, no conversion                     | `extensions = (".jpg", ".jpeg")`; the shared signature check confirms the content is JPEG. PNG is rejected, never converted, so the hashed bytes are the published bytes (D33) | "Instagram accepts JPEG pictures only; export it as .jpg (pictures are never converted)" |
| Size                                         | `stat().st_size <= 8_000_000` (the smaller reading of "8 MB" / "8 MiB"), checked before reading                                                                                | "the picture is 9.1 MB; Instagram accepts at most 8 MB"                                  |
| Not MPO                                      | reject a JPEG with an APP2 segment whose identifier is `MPF\0` (multi-picture)                                                                                                 | "this JPEG holds several pictures (MPO); Instagram does not accept it"                   |
| Not CMYK (conservative, open)                | SOF component count must be 1 or 3                                                                                                                                               | "the picture uses CMYK colours; save it as RGB"                                          |
| Aspect ratio 4:5 to 1.91:1, inclusive (open) | integer maths, no floats: `5*w >= 4*h` and `100*w <= 191*h`, using width and height from the SOF segment                                                                     | "the picture is 1000 x 2000 (1:2); Instagram accepts 4:5 (tall) to 1.91:1 (wide)"        |
| Dimensions readable                          | an unreadable or truncated header is refused, never guessed (as D37)                                                                                                             | "the picture's size could not be read (...)"                                             |

- **Header reader:** `platforms/image_header.py` takes over the PNG and JPEG reader from `platforms/linkedin/image_size.py` (moved, not rewritten). `linkedin/image_size.py` keeps `image_problem` and `MAX_PIXELS` and re-exports `image_size`, so `test_linkedin_checks.py` is unchanged. The JPEG walk is extended to collect, on the way to SOF: the APP2 `MPF` flag, the APP1 EXIF orientation, and the SOF component count.
- **EXIF orientation:** parse APP1 `Exif\0\0` → TIFF header (`II` or `MM`) → IFD0 → tag `0x0112` (SHORT). Values 5–8 mean the picture is shown rotated 90°, so width and height are swapped for display. Whether Instagram applies the orientation is not documented, so the check is conservative: if the orientation is 5–8, **both** readings (stored and rotated) must be within 4:5–1.91:1, otherwise the picture is refused ("the picture has rotation information (EXIF orientation); re-save it upright"). Orientation 1–4, a missing tag, or an unreadable EXIF block → the stored dimensions are used; an unreadable EXIF block is not an error by itself.
- Validation reads the whole file (at most 8,000,000 bytes after the size check) for Instagram. LinkedIn keeps reading 8 bytes, as today.

### 6.2 Caption

| Rule                | Counting (conservative)                                                                                                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ≤ 2,200 characters | refuse if **either** the code-point count (`len(text)`) **or** the UTF-16 count (`len(text.encode("utf-16-le")) // 2`) is over 2,200. An emoji counts as 2 in UTF-16 |
| ≤ 30 hashtags      | count every `#` immediately followed by a Unicode word character (`#\w`), wherever it appears (so `C#1` counts and `C#` does not). Overcounting is the safe direction        |
| ≤ 20 mentions      | count every `@` immediately followed by `[A-Za-z0-9._]`, wherever it appears. An e-mail address counts as a mention (safe direction; the message says so)                        |

The caption is published as written: Instagram needs no escaping, so there is no `escape_caption` counterpart.

### 6.3 Where the rules run

- `validate_candidate` → `scan`, every run, intake (uploads), `check_schedulable` (schedule and reconfirm).
- The real Instagram publisher checks them again on the exact `PostContent` bytes before uploading anything (as `LinkedInPublisher` does) **[future]**.
- `check_schedulable` uses `rules.real_media_problems` for a real schedule instead of the LinkedIn import, and its message names `rules.display_name`.

### 6.4 One post to several platforms

One job per platform, with independent outcomes: separate `incoming/` folders, hashes and duplicate checks, and an optional schedule per platform. The API validates against every chosen platform **before** writing anything. A PNG is fine for LinkedIn and refused for Instagram; the response names the platform that refuses. The writes are per folder, so if the second write fails the first post stays and the response says so (the same limitation as "add, then schedule" in D46).

### 6.5 Dashboard use of the same rules

- **Numbers:** from `GET /api/platforms`.
- **Instant feedback:** `frontend/src/lib/postRules.ts` mirrors the counting rules (code points and UTF-16, `#\w`, `@[A-Za-z0-9._]`) and a small TypeScript JPEG header reader for aspect ratio, orientation and MPO. Browser image decoding is not used: it applies EXIF orientation in its own way.
- **One set of test cases for both:** `backend/tests/fixtures/post_rules_cases.json` (captions with expected counts, header byte strings with expected dimensions and verdicts) is read by pytest and by Vitest, so the two implementations cannot drift apart.
- **Final word:** the backend always decides, at `POST /api/posts/check` and again at `POST /api/posts`.

## 7. Lost-response mapping for Instagram (with fakes) [proposed]

The mapping is built and tested now with fake Graph API answers and the in-memory media host. **No real run can reach it** while `real_available` is false. Rules: nothing is ever retried automatically, and `media_publish` is called at most once per job.

**In the publisher** (`platforms/instagram/publisher.py`, using a `GraphClient` protocol):

| Situation                                                                                                                                                                                              | Result                                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Any local check fails, the recorder fails before `put`, `put` fails, container creation fails (any error, including timeouts), container `ERROR`/`EXPIRED`/still `IN_PROGRESS` after polling | `FAILED` (`PublishResult`): no post can exist                                                                                                                                                   |
| Recording the container ID fails                                                                                                                                                                       | `FAILED`: `media_publish` was not called                                                                                                                                                        |
| `media_publish`: `ConnectError`/`ConnectTimeout`                                                                                                                                                 | `FAILED`: never sent                                                                                                                                                                              |
| `media_publish`: `200` with `id`                                                                                                                                                                 | `PUBLISHED` with that ID. A `config_issue` is kept in the errors ("published without its caption"); still `PUBLISHED`, never retried                                                          |
| `media_publish`: a documented refusal (codes `9`/`2207042`, `9007`, `25`, `4`/`2207051`, `190`, throttling)                                                                            | one read-only status check: `PUBLISHED` → see the next row; otherwise `FAILED`                                                                                                                 |
| Container status `PUBLISHED` but no media ID                                                                                                                                                         | list recent media: exactly one new `IMAGE` → `PUBLISHED` with its ID; otherwise raise `PublishOutcomeUnknown` → `NEEDS_REVIEW`                                                            |
| anything else (read timeout, dropped connection, 5xx, `200` without `id`, undocumented answers): **a lost publish**                                                                          | one read-only status check: `PUBLISHED` → as above; `ERROR`/`EXPIRED` → `FAILED`; **`FINISHED`, `IN_PROGRESS`, or unreadable → `PublishOutcomeUnknown` → `NEEDS_REVIEW`** |

**After a crash (reconciliation, `PUBLISHING` row, real Instagram):**

| Stored                                                                 | Result                                                                                                                                                                                                                                       |
| ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| no `remote_ref`                                                      | **`FAILED`**: "interrupted before the container ID was recorded; media_publish was never called". This uses `settle_crashed_before_publish`'s rule, so the same run may process it again; the orphan container expires unpublished |
| `remote_ref` set, real run on Instagram (checker available)          | `RemoteChecker.check`: `PUBLISHED(id)` → `PUBLISHED`, then archive the files (best effort, as the `archive` node); `NOT_PUBLISHED` → `FAILED`; `UNKNOWN` → `NEEDS_REVIEW` (files to `failed/`, as today)                |
| `remote_ref` set, any other run (simulated, LinkedIn, or no checker) | `NEEDS_REVIEW`, exactly today's rule: no network call from a run that does not need one                                                                                                                                                    |

**Settled later by `resolve`:**

- `resolve <id> --check` → `PUBLISHED(id)` sets `PUBLISHED`; `NOT_PUBLISHED` (e.g. the container `EXPIRED` after 24 hours) sets `FAILED` ("never published", retryable); `UNKNOWN` → refused, "still unknown; try later, or use --published/--failed".
- `--published`/`--failed` are unchanged. A resolve note records which way was used.

**Simulated mode** never reaches any of this: `run --platform instagram` (simulated) uses the shared `Simulator` and ends `SIMULATED`, with the footer "nothing was sent to Instagram in this run".

## 8. Implementation order [proposed]

Each step is one or more commits; each leaves the **full** suite green (pytest; plus `npm test`, `typecheck` and `build` for frontend steps). After each step: update `CLAUDE.md` and the decision records as needed, and give the Suggested commit block.

### Step A: refactor only (LinkedIn behind the adapter, zero behaviour change)

- **A1 Rules registry.** New `platforms/rules.py` (`PostRules`, `RULES` with LinkedIn only) and `platforms/image_header.py` (reader moved from `linkedin/image_size.py`, which re-exports it). `validate_candidate` looks the platform up with `RULES.get` (a platform without rules gets the shared checks only, as before); `check_schedulable` uses `rules.real_media_problems` and `display_name`. **Done (`eb3613f`).** **Moved from A1 to step B** (not used yet): the all-modes image check `media_problems` (it would make validation read the whole image instead of 8 bytes, only Instagram needs it) and the strict lookup `rules_for` that raises for an unknown platform (validation must keep accepting platforms without rules until then).
- **A2 Adapter registry.** `operations/platforms.py` (`PlatformAdapter`, `ADAPTERS`, `adapter_for`); `LinkedInAdapter` in `operations/linkedin.py`, delegating to the module-level `_real_publisher` and to `_token_warnings` (moved there from `operations/schedules.py`). `_publisher_for` calls `adapter_for(PLATFORM).real_publisher(settings)` (its `platform` parameter comes with A3). `RealPublishingUnavailable` moved to `operations/common.py` so the run path needs no LinkedIn import (`linkedin_ops.RealPublishingUnavailable` is the same class). The adapter has only the members used now: `platform`, `real_publisher`, `schedule_warnings`; `confirmation_phrase` comes with A3, `real_available` with step C, `remote_checker` with step D. **Done (A2).**
- **A3 Platform parameter.** `run_once`, `scan_preview`, `schedule_post`/`unschedule_post`/`reconfirm_post` and `JobService` take `platform: Platform = Platform.LINKEDIN`. `PLATFORM` is renamed `DEFAULT_PLATFORM` (kept for the API's defaults). Texts (footer, banner, refusals, error report) are built from `display_name`. `REAL_CONFIRMATION` comes from the adapter, and `app/models/schemas.py` imports it instead of copying it (single source of truth, D49; a test checks that the API and the CLI use the same phrase).
- **A3 is split.** **A3a (done):** `platform: Platform = PLATFORM` (LinkedIn) on `run_once` (and `_run_locked`, `_publisher_for`, `_process`, `_hold_scheduled`), `scan_preview` (and `_active_schedules`), `schedule_post`, `unschedule_post`, `reconfirm_post` (and `_find_post`). Each public operation calls `adapter_for(platform)` first, so Instagram and Facebook are refused with `UnsupportedPlatform` before the lock, the data folders or the job database are touched; no CLI option yet (it would change the help text; it comes with step E). **A3b (done):** optional `platform` on `GET /api/scan`, `POST /api/posts`, `POST /api/run` and `POST /api/schedules` (any case; default LinkedIn), parsed first by `app/dependencies.py: parse_platform`, so Instagram, Facebook or an unknown name is a 400 before any lock, folder or database write; cancel and re-confirm use the schedule's own platform. User-facing texts (run footers and refusals, the error report, the CLI's scan/run/schedule lines) come from `platforms/rules.py: display_name`. `REAL_CONFIRMATION` exists once, in `operations/common.py`, imported by `app/models/schemas.py`; there is no Instagram phrase (D49).
- **A4 `has_jobs` per platform (D49, moved here from step D). Done.** `JobRepository.has_jobs(mode=, platform=)` filters by platform and mode; `archive_without_history` (the only production caller) passes its platform, and a test checks that every call in `src/` and `app/` passes `platform=`. The `platform` default is LinkedIn only so the older `test_real_mode.py::test_has_jobs_is_per_mode` (LinkedIn rows) keeps working unedited. Storage tests with rows inserted directly: real history counts per platform both ways; simulated history never counts as real; D38's archive check is per platform.
- **Step A is done** (`eb3613f` A1, `a86d6ef` A2, `e27f2af` A3a, `a650977` A3b, then A4); LinkedIn behaves exactly as before, and every test that existed before step A passes unedited. **Deferred to later steps:**
  - `PostRules.media_problems` (all-modes image check) and the strict `rules_for` lookup → step B.
  - `real_available` on the adapter (an Instagram adapter refusing real mode in code) → step C.
  - `remote_checker` on the adapter, `resolve --check`, the reference recorder, schema v3 → steps C and D.
  - `run --scheduled` across platforms, with a refused platform skipped (D49), and that run's exit code → step D.
  - The `--platform` CLI option (changes the help text), the platform column in `jobs`/`schedules`, `GET /api/platforms`, `POST /api/posts/check` → step E.
  - The dashboard's own copy of the real-mode phrase (`frontend/src/lib/postRules.ts`) → step F.
  - A per-platform confirmation phrase (`confirmation_phrase` on the adapter): only with real Instagram (draft D48).
  - `CLAUDE.md` still names `CAPTION_RULES` (replaced by `platforms/rules.py` in A1): correct it together with the next update of `CLAUDE.md`.
- **Files:** `platforms/rules.py` (new), `platforms/image_header.py` (new), `platforms/linkedin/image_size.py`, `ingestion/validation.py`, `services/scheduling.py`, `services/archive.py`, `operations/{platforms (new), linkedin, common, run, scan, schedules}.py`, `cli/commands.py`, `app/services/job_service.py`, `app/models/schemas.py`, `app/routers/{run,schedules}.py`.
- **Tests:** all ~872 pass **unchanged**. New: `test_platform_registry.py` (LinkedIn registered; Facebook raises `UnsupportedPlatform`; LinkedIn rules equal today's constants: 3,000, jpg/jpeg/png, pixel limit real-only). `test_patch_targets.py` needs no edit if `_real_publisher` stays defined and used in `operations/linkedin.py`: confirm it is run and still finds ≥ 60 patches, and add to its "made-up modules" a case for an adapter method that calls a module-level function (accepted).
- **Proof of zero change:** before and after, compare `--help` of every command, the output of the sandbox tester's scripted run, and `git grep` for the old LinkedIn strings.
- **Risks:**
  - Double-post: none (no flow change), but `_publisher_for` is the gate for real mode. The tests `test_a_plain_run_never_asks_for_the_real_publisher` and `test_env_can_never_switch_*` must stay green untouched.
  - Patch seams: a patch could silently stop reaching the code; the guard catches it.
  - Labelling: footer texts are now generated; the existing tests pin them.

- [X] Step B: Instagram rules (validation in every mode; Instagram not runnable yet)

- **Done (2026-11-19).**

  - `platforms/image_header.py: jpeg_info` reads the EXIF orientation, the MPF flag and the colour components; `image_size` is unchanged.
  - `platforms/instagram/rules.py` holds the caption and picture rules, with their source IDs.
  - `PostRules` gains `media_problems` and `media_read_limit`: validation reads the whole image (up to 8,000,001 bytes) only for a platform that defines them, so LinkedIn still reads 8 bytes.
  - `UnsupportedPlatform` moved to `platforms/rules.py` (re-exported by `operations/platforms.py`), and `check_schedulable` uses `rules_for`.
  - Instagram has rules but **no adapter**, so it is still unreachable.
  - One existing assertion changed, with the developer's approval: `test_platform_rules.py::test_only_linkedin_has_rules` now expects exactly `{LinkedIn, Instagram}`.
- **Files:** `platforms/instagram/__init__.py`, `platforms/instagram/rules.py` (new), `platforms/image_header.py` (EXIF orientation, MPF, components), `platforms/rules.py` (register Instagram), `tests/fixtures/post_rules_cases.json` (new).
- **Also here (moved from A1):** `PostRules.media_problems` (the all-modes image check; validation reads the whole image only for platforms that define it, so LinkedIn keeps reading 8 bytes) and `rules_for(platform)`, which raises `UnsupportedPlatform` for a platform without rules, where a caller must not silently skip the platform's checks.
- **Tests:** `tests/unit/test_instagram_rules.py`:

  - real JPEG headers written by Windows' encoder (as D37): baseline, progressive, EXIF before SOF, orientation 6 portrait and landscape, MPO, CMYK, truncated;
  - aspect-ratio boundaries (exactly 4:5 and exactly 1.91:1, one pixel either side);
  - 8,000,000 vs 8,000,001 bytes;
  - captions at 2,200/2,201 code points; 1,101 emoji (2,202 UTF-16 units → refused);
  - 30/31 hashtags; `C#`/`C#1`; 20/21 mentions; e-mail addresses.

  LinkedIn validation tests unchanged.
- **Risks:** none for publishing (nothing runs Instagram yet). A false rejection is the safe failure.

### Step C: Instagram adapter and publisher against fakes (no network, not reachable by real runs)

- [X] **Done (2026-11-19).**
  - `platforms/instagram/{client,publisher,remote}.py`, `platforms/media_host.py`, plus `ReferenceRecorder`, `RemoteOutcome` and `PublishOutcomeUnknown` in `platforms/base.py`.
  - `operations/instagram.py`: `InstagramAdapter` with `real_available = False`, **not registered**. Its real publisher is refused with the D49 message before anything is read.
  - The client refuses any transport that is not a test `MockTransport`.
  - Tested with `tests/instagram_fake.py` and `tests/media_host_fake.py`.
  - **Decided while building** (developer's instructions for step C):
    - the picture is **kept** after NEEDS_REVIEW and deleted after PUBLISHED or FAILED;
    - an uncertain `media_publish` is NEEDS_REVIEW without a status check in the publisher; the remote checker settles it later;
    - a documented refusal gets one read-only status check;
    - the token goes only in the `Authorization` header (S12's examples).
  - **Left for step D:** `remote_checker` on the adapter, wiring the recorder to the job row (schema v3), and `resolve --check`.

- **Files:**
  - `platforms/media_host.py` (`MediaHost`, `new_media_key`);
  - `platforms/base.py` (`RecordingPublisher`, `ReferenceRecorder`, `RemoteOutcome`, `RemoteChecker`);
  - `platforms/instagram/graph.py` (`GraphClient` protocol only: create container, read status, publish, list media; the httpx client is future);
  - `platforms/instagram/publisher.py` (the section 7 state machine), `platforms/instagram/remote.py` (`InstagramRemoteChecker`);
  - `operations/instagram.py` (`InstagramAdapter`: `real_available = False`, `real_publisher` raises `RealPublishingUnavailable("real Instagram publishing is not available yet")`, `confirmation_phrase = "publish to Instagram"`);
  - `tests/instagram_fake.py`, `tests/media_host_fake.py`.
- **Tests:** `tests/unit/test_instagram_publisher.py`:
  - one test per row of the section 7 tables;
  - `media_publish` called **exactly once** in every case;
  - the recorder is called with the key before `put` and with the container ID before `media_publish`;
  - recorder failure → `FAILED` with no publish call;
  - the object is deleted in every outcome;
  - the presigned URL and a fake token never appear in errors or reprs;
  - error texts keep codes, never messages (D39 style).
- **Risks:**
  - Double-post: the "exactly once" assertion is the guard.
  - Secrecy: URL and token scans.
  - Labelling: none (not wired to runs).

### Step D: schema v3 and wiring (simulated Instagram runs end to end)

Split into D1 (schema v3, migration, the recorder writing the container ID), D2 (register the Instagram adapter, simulated only; `run --scheduled` across platforms, refused platforms skipped, exit code 3 when one was), D3 (`resolve --check` with the remote checker).

- [X] **D3 done (2026-11-19).**

  - `resolve <job> --check` (`operations/resolve.py: check_job`): read-only, under the run lock, for real NEEDS_REVIEW jobs of a platform whose adapter has a `remote_checker`.
  - PUBLISHED → PUBLISHED + archive (no post ID, with a note, if the post cannot be identified); FAILED → FAILED in `failed/` with the report rewritten; uncertain → unchanged, exit 3.
  - The stored picture is deleted and its key cleared on PUBLISHED or FAILED; a failed delete keeps the key.
  - LinkedIn: refused (no checker). Real Instagram: refused (D49, `_remote_checker`/`_media_host`).
  - The API's `{"check": true}` is left for step E (section 5.3).
  - When PUBLISHED moves the files out of `failed/`, the old folder with its error report and marker stays.
- [X] **D2 done (2026-11-19).**

  - Instagram is registered (`real_available = False`), so simulated Instagram works through `run_once`, `scan_preview`, the schedule operations and the API (`platform`). Facebook is still refused.
  - `real_unavailable_text` refuses real Instagram in `_run_locked` (before anything changes), `_publisher_for`, `schedule_post`/`reconfirm_post` (before the lock) and the API (400).
  - `run_once(scheduled=True)` without a platform covers every registered platform: the oldest due schedule of any platform, at most one per run. Per-platform setup (data folders, archive history, real availability, token) is checked oldest first; a refused platform is skipped, with its schedules unchanged, logged as `Platform skipped by the scheduled run: platform=… reason=…` and shown as `SKIPPED`.
  - Exit codes as recorded in D49: 3 if a platform was skipped, 1 if every platform with something to publish was refused (unchanged D46 behaviour), 0 otherwise.
  - The "Run started" line of a scheduled run now reads `platform=LinkedIn,Instagram`; the only LinkedIn-visible change, because scheduled runs are no longer LinkedIn-only.
  - Tests edited with the developer's approval: the Instagram cases of the A2/A3a/A3b refusal tests, four Instagram-registration assertions, and the two Facebook/unknown-name message strings.
- [X] **D1 done (2026-11-19).**

  - Migration 3 adds `jobs.remote_ref` and `jobs.media_host_key`, backed up first as `app.db.bak-v2`.
  - `JobRepository.record_remote_ref` commits the reference in its own transaction, and only for a `PUBLISHING` job without one.
  - The `publish` node passes the recorder to a publisher that `records_references`.
  - Reconciliation takes `reference_platforms` (adapters with `records_references`; none until D2): a real job without a reference → `FAILED`, with one → `NEEDS_REVIEW`.
  - A job whose next step is `publish` is still never resumed.
  - `media_host_key` is in the schema but not written yet (picture cleanup comes later).
  - Four assertions in `tests/unit/test_database.py` were changed with the developer's approval: three schema-version checks `2 → 3`, and the `all_jobs` helper now selects the 14 original columns.

- **Files:** `storage/database.py` (migration 3), `storage/jobs.py` (`remote_ref`, `media_host_key`), `graph/nodes.py` (`publish` passes `record=` to recording publishers), `services/reconcile.py` (the Instagram rows of section 7; LinkedIn path unchanged), `operations/resolve.py` (`check`), `operations/run.py` (remote checker for real runs of that platform; `due(platform=None)` for scheduled runs), `operations/scan.py`, `operations/schedules.py`.
- **Tests:**
  - migration v2 → v3 with `app.db.bak-v2`; reading a v2 database read-only;
  - simulated Instagram end to end (`SIMULATED`, files stay, footer says Instagram);
  - the same files in both platforms → two independent jobs, each duplicate-checked on its own platform;
  - a crash with a fake recording publisher: `PUBLISHING` + no `remote_ref` → `FAILED`; with `remote_ref` and a fake checker → `PUBLISHED`/`FAILED`/`NEEDS_REVIEW`; in a simulated run → `NEEDS_REVIEW` without any checker call;
  - `resolve --check` per outcome, and refused for LinkedIn;
  - `run --scheduled` with due schedules on both platforms → exactly one published, the oldest; a refused platform is skipped (D49): its schedules unchanged and still due, the oldest due schedule of the other platform published, the skip logged and shown, and the exit code not 0; a skipped schedule more than 6 hours late becomes `MISSED` in a later run;
  - `run --real --platform instagram` → exit 1, nothing changed;
  - every existing LinkedIn test unchanged.
- **Risks:**
  - Double-post: the reconciliation "no `remote_ref` → FAILED → processed again" rule is safe only because `media_publish` is called strictly after the committed recorder. A test kills the process between the recorder and `media_publish` (pattern of `test_a_killed_process_is_resumed_by_the_next_run`).
  - Recovery: a v3 database is refused by older code; the backup and the restore note go in the README.
  - Labelling: footers per platform.

### Step E: CLI and API

Split into E1 (CLI) and E2 (API: `GET /api/platforms`, `POST /api/posts/check`, the resolve check).

- [X] **E1 done (2026-11-19).**
  - `--platform {linkedin,instagram}` (any case) on scan, run, jobs, schedule, unschedule, schedules, reconfirm and resolve.
    - Without it, every command behaves and prints exactly as before.
    - `run --scheduled` without it still covers every platform.
    - `jobs` and `schedules` list every platform unless filtered (a LinkedIn default would hide Instagram rows).
  - A `PLATFORM` column appears only when a listed row is not LinkedIn, because `test_history_commands.py` pins the existing layout.
  - `--platform facebook` → exit 1. `--real` with Instagram → the D49 message, exit 1, before any lock, log or database. An unknown name → exit 2.
  - `resolve --platform X` refuses a job of another platform.
  - After `resolve --check` → PUBLISHED, the old `failed/` folder is removed if it holds only this job's marker and error report; otherwise it is left and the output says so.
  - The lock exit code was already 4 everywhere.
  - `instagram-check` is not added: future work, once real Instagram exists.
- [x] **E2 done (2026-11-19).**
  - `GET /api/platforms`: `id`, `name`, `real_available`, `confirmation_phrase` (null while real is off), and `rules` built from the backend's own constants.
  - `POST /api/posts/check` (form: `caption`, `image`, optional `name`, `platform`): `ingestion/validation.py: validate_content` runs the same checks as a file in `incoming/`, on the bytes in memory, and writes nothing. A caption that cannot be encoded (a lone surrogate) → 400 "The caption is not valid text."
  - `POST /api/jobs/{id}/resolve` with `{"check": true}`: the same `check_job` as the CLI, read-only. Its response includes `changed` (false when still uncertain). Real Instagram checking is refused → 400 (D49). Combined with another answer → 400.
  - Every existing request is unchanged. The dashboard still uses its own copy of the rules; switching it to `/api/platforms` is step F.

- **Files:** `cli/parser.py` (`--platform`, `resolve --check`, help texts), `cli/commands.py`, `cli/output.py` (platform column only when needed), `app/routers/{scan,posts,run,schedules,jobs}.py`, `app/routers/platforms.py` (new), `app/services/job_service.py`, README (commands, multi-platform notes).
- **Tests:**
  - CLI: `--platform` parsing; defaults identical (a golden comparison of `scan`/`run`/`schedules` output without `--platform`); `run --scheduled` across platforms; `--real --platform instagram` → exit 1 with the message.
  - API: every existing test unchanged; `platform` defaults; `/api/platforms`; `/api/posts/check` writes nothing (folder listing before/after); multi-platform `POST /api/posts` with PNG → refused for Instagram only; a real Instagram run or schedule → 400; the confirmation phrase per platform.
- **Risks:**
  - The help text changes (intended; D47's "byte-identical" held until here).
  - Real mode only by flag and phrase: extend `test_env_can_never_switch_*` with `AUTOPOSTER_PLATFORM=instagram` in `.env`, which must have no effect.

### Step F: dashboard

- **Files:** `frontend/src/lib/platforms.tsx` (live/test flags from the API), `lib/postRules.ts` (per-platform rules, JPEG header reader, counting), `services/apiService.ts`, `types/api.ts`, `components/IntakeStudio.tsx` (platform checkboxes, per-platform checklist), `ScanQueueStation.tsx` (queue per platform), `ScheduleModal.tsx`/`ScheduleFields.tsx` (platform; Live disabled for Instagram), `RunnerConsole.tsx` (per platform), `JobDetailsModal.tsx`/`ResolveModal.tsx` (platform texts; "Check now" for Instagram), `LinkedInSentinel.tsx` (Instagram card "test only").
- **Tests:** Vitest for each component change; `postRules.test.ts` reads the shared `post_rules_cases.json`; Instagram never offers Live; no "ready" count mixes platforms incorrectly; `npm test`, `typecheck`, `build`; `npm audit` reported. A manual browser walkthrough by the developer before the commit (as in M5 commit 8).
- **Risks:** labelling ("Test" vs "Live" per platform; Instagram always Test); a ready count that includes another platform's posts.

- [x] **F done (2026-11-19).** One platform choice, shared by New post ("Post to") and the pickers in Queue, Scheduled and History, shown only when `GET /api/platforms` offers more than one platform.
  - Rules and the confirmation phrase come from `GET /api/platforms`; `lib/postRules.ts` keeps only LinkedIn's values as a fallback until the API answers.
  - While a post is written, `POST /api/posts/check` gives the backend's verdict; its errors block adding the post.
  - Instagram is test only: no Live schedule, and Publish runs a test with no phrase.
  - The API gained `?platform=` on `GET /api/jobs` and `GET /api/schedules` (without it, every platform, as before). LinkedIn requests are sent exactly as before.
  - Not done: the per-platform card in Accounts, and one post sent to several platforms at once (question 7).

### Future (blocked on the spike and D48 acceptance)

1. **Spike:** a presigned R2 or S3 URL accepted by Meta? Fetched at container creation or at publish? Visibility of posts from a Development-mode app.
2. Accept D48 (possibly amended by the spike); choose the media host and how SigV4 is done.
3. `platforms/instagram/client.py` (httpx, `retries=0`, token only in `Authorization`, only to `graph.instagram.com`), `credentials.py` (`INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_TOKEN_CREATED`), `R2MediaHost`, media sweep.
4. `instagram-check` (`--upload-test`: creates a container, never publishes); `GET /api/instagram/status`; the dashboard card.
5. `real_available = True`; a first supervised real post; a milestone review document as for M4.

## 9. Open questions for you

Questions 1, 2, 4 and 6 are **DECIDED (D49)**; the rest are answered when their step is built.

1. **DECIDED (D49).** ~~**Scope rule.**~~ Instagram may be implemented in simulated mode only; real Instagram is forbidden until D48 is accepted. `CLAUDE.md` is updated.
2. **DECIDED (D49).** ~~**The publisher with fakes (step C) before the spike.**~~ Built against fakes now (step C); updated if the spike shows different behaviour.
3. **LinkedIn's pixel limit:** keep it real-only (exactly as today, the plan's default), or move it into validation for every mode like the caption rule (D45)? The latter is a small LinkedIn behaviour change.
4. **DECIDED (D49).** ~~**Scheduled runs across platforms.**~~ A platform whose setup is refused is **skipped** for that run (logged with the reason); the other platforms continue, still at most one post per run; the skipped schedules stay due and become `MISSED` normally after 6 hours. Still open: that run's exit code (1 or 3, never 0).
5. **Wording of a cross-platform scheduled run** with nothing due: today "Simulation mode: nothing was sent to LinkedIn in this run." Keep it while only LinkedIn has schedules, or always say "to any platform" (one scheduled-run test changes)?
6. **DECIDED (D49).** ~~**Uncertain Instagram rules.**~~ The cautious defaults are accepted: inclusive aspect-ratio limits, CMYK rejected, rejected when EXIF orientation makes the two readings disagree, e-mail addresses count as mentions.
7. **One post to several platforms:** a dashboard feature only (checkboxes writing to each `incoming/`), or should the CLI also support it (for example a drop folder shared by platforms)? The plan keeps per-platform folders.
8. **Confirmation phrase** for Instagram: `publish to Instagram` (one phrase per platform), or one phrase for all real posts?
9. **`jobs`/`schedules` output:** add a platform column only when a non-LinkedIn row is shown (keeps today's output identical), or always?
10. **The sandbox tester (`scripts/app.py`):** add an Instagram option now, or leave it LinkedIn-only until real Instagram?
