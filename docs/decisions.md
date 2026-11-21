# Decision records

Significant decisions, newest last. Each entry: what was decided, why, and what it means for later work.
`CLAUDE.md` holds the binding rules; this file explains how they came about.

---

## D1. LangGraph as a deterministic workflow engine (Milestone 0)

- **Decision:** Each post's workflow is an explicit LangGraph `StateGraph` with typed state, named nodes, and conditional edges. No LLMs, prompts, or agents.
- **Why:** Explicit stages and routing, with built-in checkpointing (M3) for resume after a crash.
- **Consequences:** Nodes stay thin and call plain Python services; `langchain` is not a dependency.

## D2. SQLite first, two separate stores (Milestone 0)

- **Decision:** `database/app.db` for business records (M2) and `database/langgraph_checkpoints.db` for graph state (M3), never mixed. PostgreSQL only with a concrete need.
- **Why:** Zero setup on Windows; keeping the stores separate lets either be replaced independently.
- **Consequences:** All app-DB access goes through `storage/`.

## D3. Python 3.14 in `.vevn` (2026-10-25, Slice 2)

- **Decision:** Develop on Python 3.14 in a venv named `.vevn`; `requires-python` stays `>=3.12`.
- **Why:** Developer's choice. All tests pass on 3.14.
- **Consequences:** `langgraph` officially lists support only through 3.13. Re-check 3.14 compatibility whenever a dependency is added, especially `langgraph-checkpoint-sqlite` (M3). If something breaks, fall back to 3.13.

## D4. `PublishResult` is a definite outcome (2026-10-25, Slice 2)

- **Decision:** Fields `success`, `simulated`, `platform_post_id`, `error`, with these rules checked at construction: simulated → no post ID; success → no error; failure → non-empty error; real success → non-blank post ID. `status` maps to `SIMULATED`, `PUBLISHED`, or `FAILED`.
- **Why:** The graph needs a definite failure value to route on, and keeping "unknown" out of the type means it can never be mistaken for success.
- **Consequences:** An ambiguous outcome is never a `PublishResult`; the publisher raises and the graph sets `NEEDS_REVIEW`.

## D5. Frozen models hold tuples, not lists (2026-10-25, Slices 2–3)

- **Decision:** `ScanResult.candidates/pending/ambiguous` and `ValidationResult.errors` are tuples; passing a list raises `TypeError`.
- **Why:** `frozen=True` only blocks reassignment; a list inside could still be changed.
- **Consequences:** This changed the original `list` spec. Graph state uses lists instead, because it is serialized.

## D6. Pairing rules (2026-10-25, Slice 3)

- **Decision:** Stems and extensions match case-insensitively. Any non-`.txt` file counts as media, so an unsupported type still pairs and then fails validation. More than one media file or caption for one stem is **ambiguous**: reported in groups, one warning per stem, files left untouched. System, lock, and partial-download files are ignored.
- **Why:** Matches how Windows users see files. Ensures an unsupported image fails visibly instead of waiting forever. Ambiguity cannot be resolved safely by guessing.
- **Consequences:** A user must fix ambiguous stems by hand.

## D7. File stability check (2026-10-25, Slice 3)

- **Decision:** Size and `st_mtime_ns` must be unchanged across an interval, and the file must open for reading. Any `OSError` means "not stable yet". The graph checks both files.
- **Why:** A Windows copy may set the final size first, but usually holds a lock until done. Integer nanoseconds avoid float rounding.
- **Consequences:** Copy tools that write without locking can still slip through; the M2 content hash adds a second safeguard.

## D8. A read-only `scan` command before `run` (2026-10-25, after Slice 3)

- **Decision:** `main.py` arrived early with only `scan`, which never moves, publishes, or creates folders.
- **Why:** Manual testing from the terminal before the workflow existed.
- **Consequences:** `scan` stays as a dry-run tool alongside `run`.

## D9. File-signature check, not Pillow (2026-10-25, Slice 4)

- **Decision:** Validate images by extension plus the first bytes (JPEG `FF D8 FF`, PNG 8-byte header), and the detected type must match the extension. Captions: strict UTF-8 (BOM allowed), not empty or whitespace-only, reads capped at 64 KB. A missing file reports only "not found".
- **Why:** No dependency, and it catches renamed and mislabeled files. The 64 KB cap is a memory safety guard, **not** LinkedIn's limit.
- **Consequences:** Corrupt image bodies and dimensions are not checked. Revisit Pillow in M4, once LinkedIn's real image and caption limits are verified in its official docs.

## D10. Moving posts: per-post folders and atomic, same-drive moves (2026-10-25, Slice 4)

- **Decision:** `archive_post` moves both files into `dest/<UTC timestamp>_<stem>/` (with `_2`... on a clash) and returns the folder. It uses `os.replace` only, refusing a different drive instead of copying. If the caption move fails, the image is moved back. If that rollback also fails, it logs CRITICAL and raises `ArchiveError` naming both file locations.
- **Why:** A post's files stay together, old posts are never overwritten, and a half-moved post is either impossible or loudly reported.
- **Consequences:** The data folder must be on one drive. Milestone 2 will record the returned folder.

## D11. Graph shape (2026-10-25, Slice 5)

- **Decision:** `check_stability → validate → mark_publishing → publish → record_result → archive` (archive only on a real `PUBLISHED`). Failure paths go through `move_to_failed → record_result`. Every path ends at `record_result`, and a real publish is recorded **before** any file move.
- **Why:** In M2 `record_result` writes the database, and a real publish must be on record before files move. `mark_publishing` is its own node so the checkpointer (M3) saves `PUBLISHING` before the call, so a crash mid-publish can be detected.
- **Consequences:** `duplicate_check` (M2) and `prepare` (M4) are added when they have real work. Any exception from `publish` → `NEEDS_REVIEW`, never retried. If archiving fails after `PUBLISHED`, the status stays `PUBLISHED`. `FAILED` and `NEEDS_REVIEW` posts move to `failed/` with `error-report.txt`; if writing the report fails, that is logged and the outcome is unchanged.

## D12. Run context and job IDs (2026-10-25, Slice 5)

- **Decision:** Live dependencies (publisher, settings, interval) are passed with `invoke(..., context=...)` (`context_schema`, verified on langgraph 1.2.17), not in state. The M1 job ID is a random UUID, also used as the graph `thread_id`.
- **Why:** State must stay serializable for checkpoints.
- **Consequences:** M2 replaces the UUID with the database job ID.

## D13. LangSmith tracing stays off (2026-10-25, Slice 5)

- **Decision:** `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` must be unset or false; a test enforces it.
- **Why:** Tracing would send state (paths, captions) to an external service. `langsmith` is installed in the venv via `requirements.txt`.

## D14. An exception before any request is sent is `FAILED` (2026-10-25, recorded for Milestone 4)

- **Decision:** In the LinkedIn adapter, an error before any request reaches LinkedIn (missing credentials, unreadable file, request not built) is a definite `FAILED`. Only errors after a request may have been sent are `NEEDS_REVIEW`.
- **Why:** Nothing was sent, so nothing can be live. `NEEDS_REVIEW` should be reserved for real uncertainty.
- **Consequences:** The M1 graph maps *every* `publish` exception to `NEEDS_REVIEW`, so the M4 adapter must report not-sent errors as a failed `PublishResult` (or a dedicated not-sent error that the graph maps to `FAILED`).

## D15. The `run` command (2026-10-25, Slice 6)

- **Decision:** `run` is one-shot, always uses the `Simulator`, and has no `--dir`. It creates the data folders, processes jobs one at a time, and keeps going after an unexpected error in one job. Exit codes: 0 ok, 1 setup error, 2 usage error, 3 some job `FAILED`/`NEEDS_REVIEW`/`ERROR`, 4 locked.
- **Why:** There is no real publisher to choose yet. Incoming, failed, and archive must come from the same settings. The exit codes let Task Scheduler or scripts notice problems.
- **Consequences:** Continuous folder watching (`watchdog`) is future work. Simulated posts are simulated again on every run until M2 adds duplicate detection.

## D16. Single-run lock (2026-10-25, Slice 6)

- **Decision:** `run` takes a non-blocking OS-level lock (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) on `<base>/autoposter.lock` before any work. If it is held, `run` exits 4. `scan` does not take the lock.
- **Why:** Two runs at once could process the same post twice. The OS releases the lock when the process exits or crashes, so there are no stale locks; a test confirms this with a killed process.

## D17. Logging (2026-10-25, Slice 6)

- **Decision:** `logs/autoposter.log` uses a `RotatingFileHandler` (1 MB × 5), UTC timestamps, and INFO and above. The terminal shows WARNING and above (INFO with `--verbose`). Caption text is never logged. Handlers are closed after each command.
- **Why:** Bounded disk use; timestamps that match archive folder names and error reports; captions may be private content. Closing handlers stops Windows from keeping the log file locked.

---

# Milestone 2

Commits: 2.1 `5237e69`, 2.2 `24c73d7`, dev tools `04ce5cb`, 2.3 `d30dc71`, 2.4 `9c0e4be`. D28–D30 (M2 follow-up) are implemented and tested but not yet committed.

## D18. The job UUID is the database primary key (2026-10-26, M2 plan)

- **Decision:** Keep the random UUID job ID and use it as `jobs.job_id` (TEXT primary key). It remains the graph `thread_id`.
- **Why:** One ID everywhere (logs, error reports, graph thread, database), and it works the same if PostgreSQL ever replaces SQLite.
- **Consequences:** This replaces the earlier plan to switch to a database-generated ID in M2.

## D19. Duplicates create no row and stay in `incoming/` (2026-10-26, M2 plan)

- **Decision:** When a post's content matches a live job, no new job row is written. The files are left untouched, and the post is reported as a duplicate of the existing job on every run.
- **Why:** Simulated posts stay in `incoming/` by design. Moving duplicates to `failed/` would catch them, and writing a row per run would grow the table without limit under a scheduled run.
- **Consequences:** The user removes duplicate files by hand.

## D20. Duplicate detection is per mode (2026-10-26, M2 plan)

- **Decision:** Each job records `mode` (`simulated` or `real`). Duplicate checks and the unique index use platform + mode + both hashes.
- **Why:** Otherwise every post simulated during development would count as a duplicate in M4 and would never be published for real.
- **Consequences:** The database also rejects `PUBLISHED` for a simulated job and `SIMULATED` for a real one.

## D21. `FAILED` never blocks a retry (2026-10-26, M2 plan)

- **Decision:** Only `PUBLISHING`, `PUBLISHED`, `NEEDS_REVIEW`, and `SIMULATED` block new jobs with the same content.
- **Why:** A user who fixes a failed post, or moves it back unchanged after a temporary failure, expects it to be processed again.
- **Consequences:** Several `FAILED` rows can exist for the same content, giving a history of attempts.

## D22. `jobs` history command (2026-10-26, M2 plan)

- **Decision:** Add a read-only `jobs` command in Slice 2.4 to list recent jobs from the database.
- **Why:** Lets the user see what happened without opening SQLite.

## D23. Storage foundation details (2026-10-26, Slice 2.1, `5237e69`)

- **Decision:** Python's stdlib `sqlite3` (no new dependency). `database/app.db` sits at a fixed path under the base folder. Schema updates are append-only migrations tracked by `PRAGMA user_version`, each in its own transaction; a newer schema is refused. `synchronous=FULL`, with the default rollback journal (only one writer, because of the run lock). Explicit column lists, never `SELECT *`. Timestamps are UTC text (`%Y-%m-%dT%H:%M:%SZ`).
- **Why:** Simple, durable, and portable. The key safety rules (no simulated `PUBLISHED`, one live job per content) are enforced by SQLite as well as by code.

## D24. Database writes in the graph, and failing closed (2026-10-26, Slice 2.2, `24c73d7`)

- **Decision:** A new `check_duplicate` node hashes both files and looks up a live job with the same content. `mark_publishing` commits the job row as `PUBLISHING` before the publish call. `record_result` writes the outcome (creating a `FAILED` row first for validation failures). `archive` records the destination on a best-effort basis. A database error before publishing propagates: nothing is published and the files stay put. A database error in `record_result` after a real publish propagates and skips `archive`, so the `PUBLISHING` row keeps blocking a re-publish. A file that cannot be read while hashing is treated as "still copying" (no row, retried next run). If the database rejects the `mark_publishing` insert as a duplicate, the job is treated as a duplicate and the publisher is never called.
- **Why:** Nothing should ever be published without a durable record first, and a lost record must never lead to a second publish.
- **Consequences:** Duplicates are by content, not name: identical bytes under two names are one post. Startup reconciliation (Slice 2.3) resolves leftover `PUBLISHING` rows.

## D25. Developer tools: sandboxed tester and workflow notebook (2026-10-26, between Slices 2.2 and 2.3, `04ce5cb`)

- **Decision:** `scripts/app.py` is a stdlib-only interactive tester that runs the real `run` and `scan` commands with the **sandbox as the working (base) folder** and `AUTOPOSTER_DATA_DIR` pointing at the sandbox data. The sandbox check refuses the project folder and any overlap with the real data folder. Reset requires a `.autoposter-sandbox` marker. `notebooks/workflow.ipynb` visualises the graph and runs one sample post; it is committed with outputs cleared. A `notebook` optional extra holds `ipykernel` and `jupyter`. `open_app_db_read_only` and `JobRepository.list_recent` were added for the tester's job history and will be reused by the `jobs` command (Slice 2.4).
- **Why:** Setting `AUTOPOSTER_DATA_DIR` alone would still have written the sandbox's job database, logs, and lock under the real base folder. The marker prevents a mistyped `--sandbox` path from being wiped.
- **Consequences:** `draw_mermaid_png()` contacts mermaid.ink, which receives only the graph structure (node names and edges); the Mermaid text is always printed as an offline fallback.

## D26. Startup reconciliation of interrupted publishes (2026-10-26, Slice 2.3, `d30dc71`)

- **Decision:** At the start of `run` (after the lock and database open, before scanning), every `PUBLISHING` row is resolved. Simulated mode → `FAILED` ("nothing was sent"), files left in place, so the post is processed again in the same run. Real mode → `NEEDS_REVIEW`, status written first, then the files move to `failed/` with an error report, but only if both are still in `incoming/` and match the recorded hashes. A database error during reconciliation stops the run (exit 1). A recovered `NEEDS_REVIEW` makes the run exit 3. `scan` never reconciles. Retrying the archive move for a real `PUBLISHED` job is deferred to M4.
- **Why:** The single-run lock guarantees that any `PUBLISHING` row seen at startup belongs to a dead process, so no timeouts or guesses are needed. Real-mode outcomes are unknown and must never be retried automatically. Moving the files stops them from being reported as a confusing "duplicate" on every run.
- **Consequences:** `scripts/app.py` gained "Simulate a crash during publish" to demonstrate recovery by hand.

## D27. Read-only `jobs` command, and duplicates shown by `scan` (2026-10-26, Slice 2.4, `9c0e4be`)

- **Decision:** `jobs` lists recent jobs (`--last`, `--status`) or shows one job in full (`--id`, an exact ID prefix with no wildcards), using `open_app_db_read_only`. It never creates or changes the database and takes no lock. `scan` now opens the database read-only if it exists and marks each stable, valid candidate `new` or `DUPLICATE of job …`, using the same hashing, `find_blocking_duplicate` call, and `RUN_MODE` as `run`. It also notes interrupted `PUBLISHING` jobs without reconciling them. If the database is missing or unreadable, `scan` still works and shows `new`. The tester's history now calls `main(["jobs"])`.
- **Why:** Users can see what happened, and what `run` will do, without opening SQLite. Reusing the same check means `scan` can never disagree with `run`.
- **Consequences:** Tests for `main` run from an isolated temporary folder, because `scan` now reads the database under the current folder.

## D28. `resolve` command for NEEDS_REVIEW jobs (2026-10-26, M2 follow-up)

- **Decision:** `resolve <job_id> --published <post_id> | --failed` settles a `NEEDS_REVIEW` job after a human has checked the platform. It takes the run lock (exit 4 if held), never creates the database, and accepts an ID prefix of at least 6 characters (shorter = usage error, exit 2) only if it matches exactly one job. The repository UPDATE is conditional on `status = 'NEEDS_REVIEW'`, so no other row can ever change. `--published` requires a non-blank post ID and is refused for simulated jobs. A timestamped note is appended to the job's errors. Files are never moved; the output says where they are and what to do next. Tester menu 12 drives it.
- **Why:** `NEEDS_REVIEW` must never be retried automatically, but a human who has checked LinkedIn needs a safe, recorded way to unblock (`--failed`) or confirm (`--published`) the content.

## D29. Run logging: run_id, start/finish lines, skipped-job lines (2026-10-26, M2 follow-up)

- **Decision:** A per-run 8-hex `run_id` (a `ContextVar` read by a logging filter) appears on every log-file line, including lines from graph nodes; terminal lines stay short. "Run started" is logged right after taking the lock, and "Run finished" (outcome counts including `recovered`, exit code, duration) is logged on every path. `mode=` is on every job line (`mode` added to `JobState`). Duplicates and files still being copied log exactly one `Job skipped ... status=SKIPPED` line **without a job_id**, because a job_id is only logged if it is stored. The "Processing ..." header is printed before any work.
- **Why:** One run's lines can be found together; the log never references IDs that do not exist in the database; counts make scheduled runs auditable.

## D30. Safety hardening for sandbox and tests (2026-10-26, M2 follow-up)

- **Decision:** `/database/` is Git-ignored as a whole. The tester refuses the real `database/` and `logs/` folders and only adopts empty or already-marked folders (previously `--sandbox src` or `--sandbox database` would have been marked, and Reset would have deleted their contents). `tests/conftest.py` runs every test from its own temporary folder with `AUTOPOSTER_DATA_DIR` unset, and fails the session if the real `database/app.db` changed.
- **Why:** Found during the M2 status check: the earlier sandbox check guarded only the data folder.

## D31. PR #1 undone: `main` restored to the tested M2 code (2026-10-28, PR #3)

- **Decision:** PR #1 (a teammate's move from `src/` to `backend/`, plus a FastAPI app in `backend/app/` and a React dashboard in `frontend/`) was merged as `2611952` and then undone. Commit `ba79243` (PR #3, merged as `dd0e236`) makes every tracked file identical to `5e14b18`, the last tested Milestone 2 state. History was not rewritten: no reset and no force-push. An earlier D31 that recorded the adoption of PR #1 existed only on the undone side; this entry replaces it.
- **Why:** The PR's API was written against the Milestone 1 core, brought a second storage layer (`storage/db.py`, `storage/repository.py`: integer IDs, a different `jobs` schema, no `mode`), and did not start. Rewiring it inside the merged layout was the wrong base; the tested code was the safer place to continue from.
- **Consequences:** Nothing is lost. Tags: `archive/pr1-merged-main` (`2611952`, the merged PR #1 code), `archive/api-rewire` (`3a8137d`, the unpublished rewire of the API onto the M2 core), `archive/main-before-pr1` (`5e14b18`, the restored state; the local branch `backup/main-before-pr1` points there too). Commit `2fc6f68` ("M2: add SQLite app DB storage and wire into run") left `main` with the restore; it stays reachable through `archive/pr1-merged-main` and `origin/mz-updation`.

## D32. `backend/` layout, base folder from the package, and a web UI later (2026-10-28)

- **Decision:**
  - **Web UI later.** The project will get a React frontend and a FastAPI API. This replaces the earlier agreement (project bootstrap and tech-stack discussion, October 2026) that the MVP would have no web UI. Neither is built yet. The API will live inside the Python package (`backend/src/social_media_auto_poster/api/`) and reuse the same functions as the CLI (`scan`, `run`, `jobs`, `resolve`): no duplicated logic and no second storage layer. FastAPI binds to `127.0.0.1` by default. The real-LinkedIn safety rules apply equally to the CLI and the UI. The React app will live in `frontend/` at the repo root.
  - **Layout.** The Python project moved into `backend/` with `git mv`: `backend/pyproject.toml`, `backend/src/social_media_auto_poster/`, `backend/tests/`, `backend/scripts/app.py`, `backend/.env.example`, and a new `backend/README.md` (the package's `readme`). `CLAUDE.md`, `README.md`, `.gitignore`, and `docs/` stay at the root. The venv `.vevn` stays at the root.
  - **Base folder from the package location.** `config.py` takes the default base folder from its own location (`backend/`), not the current working directory, so `backend/.env`, `backend/data/`, `backend/database/`, `backend/logs/`, and the lock file are the same whichever folder a command starts from. `.env` is read by its path in that folder. Without `backend/pyproject.toml` (not an editable install), loading settings fails with `ProjectDirNotFoundError`. `AUTOPOSTER_BASE_DIR`, read from the process environment only and never from `.env`, moves the base folder; the tests and the sandbox tester use it. `AUTOPOSTER_DATA_DIR` still moves only the content folder.
  - **Removed:** `notebooks/workflow.ipynb` with its test and the `notebook` extra (D25 describes it as it was); `requirements.txt` (not used by the install; `pyproject.toml` declares everything the code imports); placeholder `.gitkeep` files.
- **Why:** A separate `backend/` keeps the Python project apart from the planned frontend. Resolving the base folder from the package means a command started from the wrong folder can no longer create a second, empty `data/` or `database/`; a "run from backend/" guard was considered and rejected because it would only warn about a problem this removes.
- **Consequences:** The tests and the sandbox used to isolate themselves by changing the current folder, which no longer moves the base folder: `tests/conftest.py` now sets `AUTOPOSTER_BASE_DIR` to each test's temporary folder, every test that picks its own base folder sets it too, and the tester sets it to the sandbox while a command runs. Only editable installs are supported. Existing runtime files at the repo root (`data/`, `database/`, `logs/`, `.env`, `sandbox/`) must be moved into `backend/` by hand; `.gitignore` keeps the old root locations ignored until then. An `.env` with an absolute `AUTOPOSTER_DATA_DIR` must be updated to the new path.

## D33. Milestones 1 and 2 signed off; three requirements for Milestone 4 (2026-10-28, M2 review)

- **Decision:** Milestones 1 and 2 are complete. The M2 review (code checked against `CLAUDE.md`, 341 tests, and a scripted walkthrough of every M2 scenario in a sandbox) found no blocking issue. Three risks that only matter once real publishing exists are recorded as Milestone 4 requirements in `CLAUDE.md`:
  1. The first real run must not silently publish posts that were only simulated: refuse, or require explicit confirmation, while `incoming/` holds content that already has a `SIMULATED` job.
  2. Real mode must refuse to start with an empty or new job database while `archive/` holds published posts, and the docs must cover backing up and restoring `backend/database/app.db`.
  3. The real publisher must upload exactly the bytes that were hashed, or re-hash just before uploading and treat a mismatch as a definite `FAILED`.
- **Why:** (1) Simulated posts stay in `incoming/` and duplicates are checked per mode (D20), so switching to real mode would otherwise post every one of them. (2) The job database is the only record of what was posted; losing it removes all duplicate protection. (3) Files are hashed in `check_duplicate` and read again in `publish`; a file swapped in between would be posted under the recorded hashes.
- **Consequences:** None of this changes M2 behaviour; simulation cannot post anything. Milestone 4 must implement and test all three before the first real publish. Two small fixes from the review were made at the same time: the `Recovery.notes` docstring and the README's command list.

## D34. Milestone 3: checkpoints, startup resume, and moves that are safe to repeat (2026-10-28)

- **Decision:**
  - **Separate checkpoint store.** `langgraph-checkpoint-sqlite>=3.1.2,<4` (newest release, verified on Python 3.14.8 with langgraph 1.2.18; past CVE-2025-67644, and through `langgraph-checkpoint>=4.3.0` past CVE-2025-64439). `SqliteSaver` runs on our own connection to `database/langgraph_checkpoints.db` with `synchronous=FULL` (WAL is set by `SqliteSaver`). It refuses the app database's path; no business data is read from it.
  - **`durability="sync"`** for every graph run, first or resumed: each step is on disk before the next starts, so the "publish is next" checkpoint exists before `publish` is called. Verified on the installed version: the default (`"async"`) saves while the next step already runs; a crash inside a node makes a resume run that node again; a crash after it keeps its result.
  - **Resume policy at startup** (before reconciliation): next step before `publish` → not resumed, a `PUBLISHING` row becomes `FAILED` ("nothing was sent") in either mode; next step `publish` → never resumed, reconciliation decides; next step `record_result`/`move_to_failed`/`archive` → resumed; unknown or unreadable checkpoint (corrupt data, missing state fields, unknown or no next step) → not resumed, warning, thread deleted, reconciliation decides; a resume that raises keeps its thread and reconciliation skips the job.
  - **Threads are deleted** when a job finishes (its record is in the app database). **No `interrupt()`**: human review stays with `resolve`.
  - **Moves are safe to repeat.** The post folder name is fixed when the job starts (`JobState.folder_name`), and each new folder gets a `.autoposter-job` marker with the job ID before any file moves. A repeated move finds its folder by the marker, keeps files already there, moves the rest, and reports a file that is in neither place or cannot be moved for manual repair. A real `PUBLISHED` job is never changed by a failed move.
- **Why:** Before M3, a crash after a real publish succeeded but before its result was recorded could only become `NEEDS_REVIEW`, even though the post ID was known. Checkpoints keep that result, but LangGraph re-runs the step that was interrupted, so resuming at `publish` would be a blind retry; hence the policy above. Resumed steps can run twice, so moving files had to become safe to repeat. The developer required the idempotent moves and the treatment of unknown checkpoints.
- **Consequences:** A crash after publishing is finished by the next run (`RESUMED`), with no second publish. A crash before publishing no longer creates a `NEEDS_REVIEW` in real mode. The folder timestamp in `failed/` and `archive/` is now the job's start time, not the move time. The checkpoint store holds only interrupted jobs; an unreadable store stops `run` (exit 1). `durability` is passed only when a checkpointer is used, because langgraph 1.2.18 raises `AttributeError` for `"sync"` without one. Reconciliation's own move to `failed/` is not resumed (it happens after its status is written); a crash during it can still leave a split for manual repair.

## D35. LangSmith tracing stays off by default; when to revisit (2026-10-28)

- **Decision:** LangSmith tracing is OFF by default. Revisit when AI features are added (caption generation/rewriting, hashtag suggestions, per-platform caption variants) or when the FastAPI app is deployed for shared use. Then enable it with masking of paths, usernames and post IDs, keep it disabled in tests, and never make the app depend on it. Until then, use `autoposter.log` and the `jobs` command for debugging.
- **Why:** Tracing sends graph state to an external service. Today the graph is deterministic and has no LLM calls, so traces would add little that the run log (`run_id`, job ID, status on every line) and `jobs` do not already show, while exposing local paths and post details. AI features or a shared deployment would change that trade-off.
- **Consequences:** D13 and the test that fails when `LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` is on stay in force. A `LANGSMITH_API_KEY` in `backend/.env` does not turn tracing on, and `load_settings()` never copies `.env` into the environment. Enabling tracing later is a new, recorded decision that must add masking first; the "No LLMs" rule in `CLAUDE.md` still applies until AI features are agreed.

## D36. Milestone 4 design: personal profile, hand-made token, httpx, no retries (2026-10-28, M4 slice 4.0)

- **Decision:** Milestone 3 is signed off. Milestone 4 publishes to the developer's **personal profile** with the self-serve products "Share on LinkedIn" (`w_member_social`) and "Sign In with LinkedIn using OpenID Connect" (`openid profile`). The access token is **generated in the Developer Portal** and stored in `backend/.env` as `LINKEDIN_ACCESS_TOKEN`, with an optional `LINKEDIN_TOKEN_CREATED` date for warnings 7 days before the 60-day expiry and a clear failure after it. Real mode is chosen **only with `--real`**, never from `.env`. HTTP uses **`httpx`**, declared explicitly. Creating a post is **never retried** (no client or transport retries; a test proves one call). Outcomes: `ConnectError`/`ConnectTimeout` → `FAILED`; `ReadTimeout`, `WriteTimeout`, `RemoteProtocolError`, a dropped connection, 5xx, 409, or `201` without `x-restli-id` → `NEEDS_REVIEW`; other 4xx → `FAILED`; `201` with `x-restli-id` → `PUBLISHED`. Images go through the **Images API**; `linkedin-check` verifies first that image status can be read, with the Assets API's synchronous upload only as a fallback. Captions escape every reserved little-format character except `#word` hashtags (`@` stays plain text), and real mode rejects captions over 3,000 characters before any upload. `Linkedin-Version` is one constant (`202610`). The token never reaches logs, graph state, checkpoints, error reports, or the job database, and a test checks this. The facts behind these choices, with sources, are in `docs/linkedin-api-notes.md`.
- **Why:** Personal posting is self-serve; a company page needs the Community Management API and a page admin role. Automatic token refresh is only for "a limited set of partners", so a hand-made 60-day token is the simplest honest option for one user; a local login flow can come later. Requiring `--real` every time keeps real posting deliberate. The Posts API documents no idempotency key, so any repeat can create a second post; a request that never connected cannot have created one, while a timeout or error after sending might have. `w_member_social` is write-only for images on the versioned API, so whether processing status can be read must be tested before relying on the Images API; posting an unprocessed image creates an invisible post. Unescaped reserved characters change how LinkedIn reads the caption. The 3,000-character limit is documented for UGC posts; the Posts API names none but defines `FIELD_LENGTH_TOO_LONG`.
- **Consequences:** Uncertain publishes cannot be checked automatically (`r_member_social` is restricted to approved users), so they stay `NEEDS_REVIEW` until the developer checks LinkedIn and runs `resolve`. The token must be renewed by hand every 60 days. **Open:** the image pixel limit (< 36,152,320 pixels): read dimensions with Pillow (only with the developer's approval), read them from the PNG/JPEG headers with the standard library, or let LinkedIn reject the upload and record `FAILED`. How caption characters are counted for the 3,000 limit is settled with tests in slice 4.3. Slices: 4.1 real-mode switch and the D33 safeguards (fake publisher, no network); 4.2 exact bytes; 4.3 LinkedIn client with fake transports; 4.4 credentials and `linkedin-check`; 4.5 a supervised first real post, then the docs.

## D37. Image pixel limit read from the file header, without Pillow (2026-10-28, M4)

- **Decision:** In real mode, before any upload, the image's width and height are read from the file header with the standard library: the PNG `IHDR` chunk, or the JPEG start-of-frame (SOF) marker. An image with 36,152,320 pixels or more (LinkedIn's documented limit is "less than 36,152,320 pixels"), or one whose size cannot be read, is a definite `FAILED` with a clear message; nothing is sent. Implemented with the LinkedIn adapter's other pre-upload checks (slice 4.3).
- **Why:** The developer chose option B over adding Pillow (a large dependency only for two numbers) and over letting LinkedIn reject the upload (a later, less clear error). It extends the file-signature check of D9 in the same style.
- **Consequences:** No new dependency. Header parsing is tested with real PNG and JPEG files, including progressive JPEGs and truncated headers. A corrupt image body is still only detected by LinkedIn; that failure happens during the upload, before any post is created, so it is also `FAILED`. Pillow stays an option for later, only with the developer's approval.

## D38. How run --real refuses: the D33 safeguards in practice (2026-10-28, M4 slice 4.1)

- **Decision:** `run --real` and `scan --real` choose real mode; nothing else can. `run --real` checks, in this order, and stops with exit code 1 and publishes nothing when a check fails: (1) a real publisher exists (`main._real_publisher`, which returns `None` until slices 4.3-4.4); (2) `archive/` does not hold post folders while the job database has no real job or does not exist. Both run **before** startup recovery, so a refused run changes nothing. After scanning, (3) no post in `incoming/` has content that was only simulated (a `SIMULATED` job and no live real job); these are listed by name, and `--publish-simulated` confirms them for that run. `--publish-simulated` without `--real` is a usage error (exit 2). The archive check has **no override**. Footers now say what *this run* sent to LinkedIn, and real runs start with a `REAL MODE` banner.
- **Why:** Refusing the whole run (instead of skipping the simulated posts) keeps the first real run fully deliberate and never leaves posts silently waiting. Exit 1 matches "setup problem, nothing processed" and is what a scheduled task already treats as a failure. The archive check has no override because the only safe fixes are restoring the database or moving those folders out by hand. Checking before recovery keeps a refusal side-effect free. The footer said "nothing was sent to LinkedIn" even when a resumed real job had been published in an earlier run; "in this run" makes it exact.
- **Consequences:** Real runs are still impossible until the LinkedIn publisher exists; tests use a fake publisher put in place of `_real_publisher`. The "already simulated" check hashes every candidate once more in real runs, which costs little at this scale. An exact-bytes publish (slice 4.2) and the LinkedIn client (4.3) come next.

## D39. LinkedIn client details (2026-10-28, M4)

- **Decision:** (1) Caption length is counted in UTF-16 code units of the caption as written (escaping backslashes not counted), against 3,000. (2) After uploading, the publisher polls the image status until `AVAILABLE` (15 tries, 2 s apart); this read is the only repeated call. A 403 on it, `PROCESSING_FAILED`, or still processing after the wait is a definite `FAILED`. (3) The token is sent only in the `Authorization` header and only to `https://*.linkedin.com`; an upload URL anywhere else is refused before sending. (4) Error texts keep LinkedIn's HTTP status and error code, never its message. (5) `httpx`'s own request logging is lowered to WARNING. (6) Undocumented answers to `POST /rest/posts` (for example `200`) are treated as uncertain (`NEEDS_REVIEW`). (7) Steps before the post (member lookup, upload registration, upload, status check) are `FAILED` on any error, including timeouts, because no post can exist yet.
- **Why:** (1) UTF-16 is how browsers count, never less than the number of characters, so it errs on the safe side (an emoji counts as two); readers never see the backslashes. (2) LinkedIn warns that a post made before its image is processed is not visible, and reading a status twice is harmless. (3) The upload URL comes from LinkedIn's answer; checking it keeps a malformed or tampered answer from ever receiving the token. (4) A refusal such as `INVALID_VALUE_FOR_FIELD` can echo the submitted value, which may be the caption, and captions must never reach logs or reports. (5) Upload URLs carry signed upload credentials. (6) Anything not documented as a refusal might have created a post. (7) An image without a post is visible to no one.
- **Consequences:** Whether `w_member_social` can read image status is still unknown; `linkedin-check` (slice 4.4) must confirm it, and the Assets API fallback needs its own decision if it cannot. A definite `FAILED` from LinkedIn (for example an expired token or the rate limit) moves the post to `failed/` like any failure; the user moves it back after fixing the cause. Checking the token once before the first post of a run would stop such runs earlier; slice 4.4 adds that with the token-expiry checks.

## D40. Credentials, token check before real runs, linkedin-check results (2026-10-28, M4)

- **Decision:** `run --real` reads `LINKEDIN_ACCESS_TOKEN` and the optional `LINKEDIN_TOKEN_CREATED` from `backend/.env` (real environment variables win), warns from 7 days before the 60-day expiry, refuses on the expiry day and after, and checks the token once with `GET /v2/userinfo` **before anything changes**: a missing, expired, rejected, or unreachable token stops the run (exit 1, nothing published, no files moved). The local archive check runs before this network call. `linkedin-check` reports the API version, token expiry, and the member posts would be published as; `--upload-test` uploads a generated test image (never used in a post) and reads its processing status. Tests block the real network and remove the LinkedIn settings from their environment. Checked against the real LinkedIn on 2026-10-28: the token is accepted and image status is readable (`AVAILABLE`), so the **Images API stays** and the Assets API fallback is not needed.
- **Why:** Without the early token check, an expired token was only discovered at the first post, and that post then moved to `failed/` (D39); checking first keeps a refused run side-effect free. The token lasts 60 days and cannot be refreshed automatically, so the creation date is the only way to warn in time. Blocking the network in tests matters now that real credentials exist on the developer's machine.
- **Consequences:** Every real run makes one extra read-only call to LinkedIn. The token was confirmed absent from every file the application wrote and from every tracked file. Next: the supervised first real post (slice 4.5), started by the developer.

## D41. Data folder outside backend: warn, and never create it in a real run (2026-10-28, M4)

- **Decision:** When `AUTOPOSTER_DATA_DIR` resolves outside `backend/`, `scan` (except with `--dir`) and `run` print a warning with the resolved path, and `run` logs it. `run --real` checks first, before the archive and token checks, that the data folder already has its LinkedIn `incoming`, `archive`, and `failed` folders; if not, it lists the missing folders and exits 1 without creating anything or contacting LinkedIn. Simulated runs still create the folders.
- **Why:** Found before the first real post: `AUTOPOSTER_DATA_DIR` in `backend/.env` still pointed at the old repo-root data folder from before the `backend/` move, so `scan` and `run` silently used it and `run --real` created empty folders there. A data folder outside `backend/` is allowed but usually a mistake; a real run creating its own folders hides that mistake behind an empty, "successful" run.
- **Consequences:** A new setup starts with one simulated run (or creating the folders by hand). The first real post (2026-10-28, job `41ac71e1`, `urn:li:share:7521116341425037312`) was published, archived, and seen on the developer's profile; the developer then deleted it on LinkedIn and kept the job as `PUBLISHED` in the history, so identical content stays blocked while an edited caption is new content. The Milestone 4 review, with its known limitations, is in `docs/milestone-4-review.md`.

## D42. Local API built on the current core, from Mouzan Raza's FastAPI and React work (2026-10-29)

- **Decision:** The local API is in `backend/app/` and the React dashboard in `frontend/`. Both come from Mouzan Raza's branch. The API files keep his layout (`app/main.py`, `routers/`, `services/`, `models/`, `dependencies.py`), not the `src/social_media_auto_poster/api/` location of `docs/api-plan.md` rule 1. Each endpoint calls the same function the CLI calls. Mouzan's own storage code (`storage.db`, `storage.repository`, a direct call to `run_job`) was removed. The first commit adds his files unchanged, so the rewrite shows as a diff.
- **Why:** His app was written for the old layout and had its own database access and run path. Connecting it to the current core keeps one run path, one lock, one database. Moving the core functions out of `print`-only code was needed first: `scan_preview`, `run_once`, `resolve_job` and `linkedin_status` in `main.py` return data, and the CLI prints it, with the same output and exit codes. The `ingestion/intake.add_post` function is the upload path, and `CheckReport` now records the member and whether LinkedIn was reachable. All 537 existing tests passed unchanged after the refactor.
- **Consequences:** The `api` extra (`fastapi>=0.162.0`, `uvicorn>=0.54.0`, `python-multipart>=0.0.32`, all below the next major, checked on PyPI 2026-10-29) is optional; the CLI never imports them. The package is not installed with the `app` folder: `pyproject.toml` still packages `src/` only (a two-root setuptools layout broke the core package's editable install), so run the API from `backend/` with `python -m app`, and the tests import `app` through `tests/conftest.py`. `job_service.py` imports two private helpers from `main.py` (`_logging_to`, `_outside_warning`); making them public is a small follow-up. Credit: the FastAPI routes, the upload flow and the dashboard are Mouzan Raza's design and code; this decision keeps them.

## D43. Answers to the open questions in docs/api-plan.md (2026-10-29)

- **Decision:**
  1. **Runs are synchronous:** `POST /api/run` returns when the run has finished (a real post can take about 30 s). No background runs or polling endpoint yet.
  2. **Real runs need a typed phrase:** the body must have `"real": true` and `"confirm": "publish to LinkedIn"`. Without both, the run is simulated (or refused with 400 when `real` is true and the phrase is missing). `publish_simulated` without `real` is a 400.
  3. **Upload limits:** images up to 10 MB (`.jpg`, `.jpeg`, `.png`, checked by signature like the CLI); captions up to 64 KB, the same guard as validation. The 3,000-character limit is not added: LinkedIn's real limit is still to be verified in M4.
  4. **Name clashes:** a post whose name matches an existing one in incoming/ (case-insensitively) is refused with `409`; nothing is overwritten.
  5. **Image previews:** not built.
  6. **`linkedin-check --upload-test`:** CLI only; the API's status check never uploads.
  7. **Port and origins:** `127.0.0.1:8000`; CORS allows `localhost:5173` and `127.0.0.1:5173` only.
  8. **Refactors first:** done before the endpoints, as one commit (`9f0370b`).
- **Also decided:**
  - State-changing calls need `Content-Type: application/json` (otherwise `415`), and uploads need `X-AutoPoster: 1` (otherwise `403`).
  - Job paths (`destination`) are relative to the data folder; no absolute path, token or `.env` value is returned.
  - A duplicate is reported as `duplicate_of` with the stored job's ID, the same job the CLI names.
  - Status codes follow the plan, with 412 for a real-mode safeguard, 413 for size, and 422 for `--published` on a simulated job.
- **Consequences:** The LinkedIn token is only read by the core functions and is never in a response or log line; a test asserts this for each LinkedIn endpoint. Log handlers are attached per request, and concurrent requests share the root logger; the run lock keeps runs and resolves from overlapping, but the log lines of two concurrent reads could be mixed. The ambiguous-prefix response (409) is not covered by an API test: it needs two jobs whose IDs share the prefix, and random IDs make that unlikely.

## D44. Project roles recorded in the README (2026-10-29)

- **Decision:** The README has a "Project roles" section. Ehsan Ullah leads the project (architecture, safety rules, workflow, database, checkpoints, real publishing, CLI, tester, tests, review and integration). Mouzan Raza wrote the initial FastAPI prototype (`scan`, `run`, `jobs` routers) and the React dashboard, which Ehsan adapted and integrated (D42, D43).
- **Why:** The roles were not recorded in one place. The wording follows the existing docs (CLAUDE.md, D42, `api-plan.md`) and the git history: Mouzan's commits `6193262` (API) and `19d03a4` (dashboard, cherry-picked with his authorship).
- **Consequences:** Update the README section and this note if the roles change.

## D45. Milestone 4 signed off (2026-11-17)

- **Decision:** The developer signed off Milestone 4 (real LinkedIn publishing) on 2026-11-17. The sign-off criteria are met:
  - **Real posts published and recorded:** three real `PUBLISHED` jobs with stored post IDs (`41ac71e1`, `cc3802b8`, `7cb929ea`); see the sign-off section of `docs/milestone-4-review.md`.
  - **No double posting of the same content in real mode:** `test_content_already_published_for_real_is_a_duplicate_not_a_refusal`, `test_second_live_job_for_same_content_is_rejected`, `test_unique_index_covers_exactly_the_blocking_statuses`; a lost database is caught by `test_real_run_refuses_archive_folders_without_real_history`.
  - **Uncertain outcomes are `NEEDS_REVIEW`, sent once, never retried:** `test_uncertain_outcomes_raise_and_are_never_retried`, `test_an_uncertain_answer_needs_review_and_is_not_retried`, `test_crash_inside_publish_is_never_resumed`, `test_the_default_transport_has_retries_switched_off`.
  - **`resolve` settles `NEEDS_REVIEW`:** `test_published_sets_status_post_id_and_keeps_content_blocked`, `test_failed_sets_status_and_allows_a_retry`, `test_resolve_as_published_records_the_post_id`.
  - **The token is never written or returned:** `test_linkedin_run.py` (every written file and all output searched), `test_the_token_never_appears_in_results_errors_or_reprs`, the four `/api/linkedin/status` token tests, the four `/api/run` token tests, and the redaction unit tests in `test_linkedin_publisher.py`.
  - **Real mode only with `--real` or the dashboard's typed confirmation, never from `.env` or a setting:** `test_a_plain_run_never_asks_for_the_real_publisher`, `test_real_mode_cannot_start_without_the_explicit_flag_and_confirmation`, `test_env_can_never_switch_a_run_to_real_mode`, `test_env_can_never_switch_an_api_run_to_real_mode`.
  - **A crash after publishing is finished without re-posting:** `test_real_crash_after_publish_is_recorded_as_published_with_its_post_id`, `test_a_crash_after_posting_keeps_no_token_in_the_checkpoint`, `test_a_killed_process_is_resumed_by_the_next_run`.
  - **Also done before the sign-off:** the helpers `main._logging_to` and `main._outside_warning` are public (`logging_to`, `outside_warning`; `2dbae11`), which closes the follow-up named in D42 (D42's text is unchanged). Error texts built from LinkedIn's answers redact the access token, and LinkedIn error codes longer than 64 characters are dropped, not cut (`4053e8b`).
- **Why:** The review of 2026-10-28 (`docs/milestone-4-review.md`) was re-checked against the code, the job database and the tests on 2026-11-17. That check found one gap, now fixed and tested: a token made only of letters and digits that LinkedIn echoed in its error code would have been stored in job errors, error reports and the log, and returned by the API. It also found two untested claims, now tested: that `.env` can never select real mode, and that real-run API responses never contain the token.
- **Consequences:** Milestone 4 is complete; Milestone 5 is next. Known limitations carried forward:
  - A failed archive move after `PUBLISHED` is not retried automatically; the user moves the files.
  - `NEEDS_REVIEW` jobs are settled by hand with `resolve` after checking LinkedIn (`r_member_social` is not available).
  - The token is renewed by hand every 60 days; the current token expires on **2026-12-27** (warnings from 2026-12-20).
  - The 3,000-character caption limit is only checked on the real path, not at upload or in simulated runs, so a caption that is too long is found only at `run --real` (as a `FAILED` with nothing sent). Planned fix: check it in validation.

## D46. Scheduled posts, and how a scheduled real run is confirmed (2026-11-17, M5)

- **Decision:**
  - **Scheduling.** A post can be scheduled for a date and time with `schedule <post> --at "YYYY-MM-DD HH:MM" [--real]` (CLI) or from the dashboard; `unschedule <post>` cancels it and `schedules` lists them. API endpoints call the same functions. Schedules live in a `schedules` table in `app.db` (migration 2) with platform, post name, file names, both SHA-256 hashes, `publish_at` (UTC), mode, status (`SCHEDULED`, `DONE`, `INVALID`, `CANCELLED`, `MISSED`), the job ID once handed to a job, a reason, and timestamps. At most one `SCHEDULED` row per platform + post name.
  - **Time.** The `--at` time is local to `AUTOPOSTER_TIMEZONE` (default `Asia/Karachi`) and stored in UTC, using the `tzdata` package (new dependency: Windows has no IANA timezone database for `zoneinfo`). It must be at least 5 minutes ahead and at most 1 year ahead.
  - **Running.** Windows Task Scheduler runs `run --real --scheduled` every 15 minutes. A scheduled run handles only due, valid schedules of its mode, **at most one per run** (oldest `publish_at` first), and nothing else in `incoming/`. Every other run (`run`, `run --real`, API runs) skips every post with an active schedule, future or due. A scheduled run with nothing due makes no LinkedIn call.
  - **Real-mode rule (amends D36, D38, D43).** Real publishing still needs an explicit human act for each post. For a scheduled post, that act is creating the real schedule: `--real` on the `schedule` command, or `"real": true` with the typed phrase `publish to LinkedIn` in the API (refused with 400 without it). The confirmation is bound to the exact content (both hashes): any change makes the schedule `INVALID`, and it is never published. `run --real --scheduled` only executes confirmations already recorded, so it can run unattended. `--real` is still required on that command line, and nothing in `.env` or any setting can select real mode or create a schedule. Because it holds a per-post confirmation, a due real schedule is exempt from the "only simulated" refusal (D38); every other real-run safeguard (data folders, archive vs. history, token check) applies unchanged.
  - **Workflow.** Choosing what is due happens before the graph. Inside it, `check_duplicate` compares the fresh hashes with the schedule's (optional `JobState` fields, so older checkpoints still resume); a mismatch or missing file makes the schedule `INVALID` with no job row. `mark_publishing` sets the schedule `DONE` with the job ID in the same transaction as the `PUBLISHING` insert; a validation failure sets it `DONE` with the `FAILED` job; a duplicate sets it `INVALID`; a post still being copied stays `SCHEDULED`. From then on the job row governs, and resume, reconciliation and `resolve` are unchanged. `schedule` and `unschedule` take the run lock.
  - **Missed schedules.** A due schedule is published late while within **6 hours** of `publish_at`, and the log says how late. After that it becomes `MISSED`, never published, and the user reschedules.
  - **Backup before migrating.** Before migration 2 changes the schema of an existing database, `app.db` is copied next to it (SQLite's backup API) as `app.db.bak-v1`. An existing file is never overwritten and the migration is not refused: if `app.db.bak-v1` already exists, the copy gets a UTC timestamp instead, for example `app.db.bak-v1-20261117T123000Z`. The backup's path is logged. If the copy fails, nothing is migrated. A new database is not copied (it holds nothing yet).
  - **Changed content is never published (decided 2026-11-18; workflow part in commit 5a).** If a scheduled post's image or caption changes after it was scheduled, it is never published automatically: the confirmation, especially a real one, covered the old content only. A new graph node, `check_schedule`, runs **before** `validate` and compares the files' fresh hashes with the schedule's; `check_duplicate` compares again and `publish` checks once more. A difference or a missing file makes the schedule `INVALID` ("content changed after scheduling" / "file missing: <name>"): no job row, the publisher is never called, and the files stay in `incoming/`. The check comes before validation so that changed content which is now also invalid is still reported as changed, not turned into a `FAILED` job moved to `failed/`. Creating the `PUBLISHING` job (or a validation-failure `FAILED` job) and marking the schedule `DONE` are one transaction. The new `JobState` fields are optional, and resume now checks only the required fields, so checkpoints from before the change still resume. Crash safety: the hash check writes nothing (its step is never resumed); a crash before `INVALID` is written resumes `record_result`, which writes it once; the job insert and `DONE` are both written or neither; once `DONE`, the schedule is never selected again, so an interrupted publish is handled by reconciliation (`NEEDS_REVIEW` for real) and never repeated. The user finds out early and re-confirms with a `reconfirm` command (commit 5b).
  - **Workflow diagram.** The README shows the graph as a Mermaid block produced locally by `draw_mermaid()`; `docs/langgraph_workflow.png` (rendered through the mermaid.ink web service) is removed, and a test fails if the README block no longer matches the graph.
  - **Early warning and re-confirming (commit 5b).** `schedules` and `scan` hash the files of every active schedule and show `CHANGED – will not be published; re-confirm` or `FILE MISSING – will not be published; re-confirm` (a file that cannot be read now is shown as "could not read the files", not as changed). They only read: the schedule row is not changed, the database is not opened for writing, and no lock is taken. `scan` lists active schedules whose post is no longer a complete pair under `SCHEDULED, FILES MISSING`. `list_schedules` returns each schedule with this content state, for the API and dashboard later. `reconfirm <post> [--real] [--at "YYYY-MM-DD HH:MM"]` replaces the post's active schedule, changed or not (so it also changes mode or time), or else its latest schedule if that is `INVALID`, with a new schedule bound to the files as they are now. Every schedule-time check runs again; the old schedule is not counted as "the same content scheduled elsewhere". The time stays the old one unless `--at` is given; an old time less than 5 minutes ahead (or past) is refused with a request for `--at`. **Real is never carried over:** without `--real` the new schedule is simulated, and if the old one was real a warning says so. The old `SCHEDULED` schedule becomes `CANCELLED` ("re-confirmed as <new id8>") and the new one is created in **one transaction** (`ScheduleRepository.replace`): a crash or error leaves either the old schedule active and no new one, or the new one active and the old one cancelled, never both and never neither. An old `INVALID` schedule stays `INVALID`. It takes the run lock; exit codes are those of `schedule`.
  - **`run --scheduled` (commit 6).** Built as decided above: only due schedules of the run's mode, nothing else in `incoming/`, at most one per run (oldest `publish_at` first), up to 6 hours late published with the lateness in the output and log, later ones `MISSED` without using up the one slot, and no publisher (so no token check and no LinkedIn call) when nothing is to be published. Two details settled while building it: (1) the safeguards are checked **before** anything changes, including marking `MISSED`, so a refused run (expired token, archive without history, missing data folders) leaves every schedule exactly as it was; (2) a `MISSED` or `INVALID` schedule makes the run exit `3` (like a `FAILED` job), so an unattended run that skipped a post shows up in Task Scheduler's history. The post is built from the file names stored in the schedule rather than from a scan, so a renamed or deleted file goes through the workflow's tested "file missing" path. `--scheduled` with `--publish-simulated` is a usage error (exit 2): there is nothing to confirm, because every scheduled post was confirmed when it was scheduled.
  - **Schedules API (commit 7, 2026-11-18).** `GET/POST /api/schedules`, `POST /api/schedules/{id}/cancel` and `/reconfirm` call the same `operations/schedules.py` functions as the CLI. A real schedule or re-confirmation needs `real: true` and the typed phrase each time. The API works on schedule IDs and the operations on post names, so `unschedule_post` and `reconfirm_post` gained an optional `expected_schedule_id`, checked inside the run lock: if the post's schedule is no longer that one (for example a CLI `reconfirm` replaced it), nothing changes and the API answers 409 "refresh and try again". The CLI passes nothing and behaves as before. Messages name folders relative to the data folder. The scan response now says which posts are scheduled. The API still has no scheduled run.
  - **Dashboard (commit 8, 2026-11-18).** Scheduling in the dashboard, plus the issues the developer's read-only browser walkthrough found: a scheduled post was shown as "Ready – goes out the next time you publish" and counted in "Publish N ready", "Post N to LinkedIn" and "Ready to post", although a dashboard run skips it. Now the queue shows it as scheduled (with `CHANGED`/`FILE MISSING` warnings) and no ready count includes it. The new-post form adds the post and then schedules it; if the second step is refused, the post stays in the queue as an ordinary post and the page says so (the two steps are separate requests, so this cannot be one transaction). Times are sent exactly as typed and read in `AUTOPOSTER_TIMEZONE`, so a browser in another time zone cannot shift them. The 3,000-character caption limit now blocks in the form in every mode, as the backend does (D45). Found while testing: `ui/Modal` moved focus back to the dialog after every key typed in a field (its effect depended on the `onClose` prop, which callers create on each render); it now keeps `onClose` in a ref. Frontend tests were added (Vitest, Testing Library, jsdom; dev dependencies only).
- **Why:** Scheduling must work while nobody is at the PC without weakening "real only by a deliberate act". Moving the confirmation from run time to schedule time, and binding it to the content hashes, keeps it deliberate and specific to one post. Handling only scheduled posts keeps an unattended run from publishing anything else in `incoming/`; skipping scheduled posts in every other run gives each post one path. One post per run spaces posts by at least 15 minutes without holding the lock while waiting. `MISSED` is not `NEEDS_REVIEW` because nothing was sent. A schema change is the first one since M2, and the job database is the only protection against double posting, so a copy of the old database is kept automatically.
- **Consequences:** A schedule missed by more than 6 hours (for example, the PC was off) is not published. An expired token stops scheduled runs (exit 1, schedules unchanged) until it is renewed; schedules whose window passes meanwhile become `MISSED`. Older code refuses the migrated database (schema version 2); to go back, restore `app.db.bak-v1`. A headless failure is visible only in the log and Task Scheduler's history until monitoring is added (M5). The dashboard part is committed only after the developer's manual browser walkthrough. Planned commits: the schedules table and repository; time parsing and due selection; the `schedule`, `unschedule` and `schedules` commands; the workflow check; `run --scheduled`; the API; the dashboard; the Task Scheduler setup docs.

## D47. main.py split into operations/ and cli/; behaviour unchanged; patch-target guard added (2026-11-18)

- **Decision:** `main.py` (2,023 lines) was split, as a pure refactor, into `operations/` (what the command line and the local API both call: `common.py`, `clock.py`, `logs.py`, `scan.py`, `linkedin.py`, `resolve.py`, `schedules.py`, `run.py`) and `cli/` (`parser.py`, `commands.py`, `output.py`). `main.py` is now the entry point only (`main()`, the `InvalidTimezoneError` catch, and a short re-export block whose every line names the tests that still read it). The API (`backend/app`) imports from `operations/` directly. Code was moved by its exact source text; every moved module logs under the old logger name (`social_media_auto_poster.main`), so log lines are unchanged. `python -m social_media_auto_poster.main` and every help text and usage error are byte-identical (checked before and after). Done in six commits, each with the full suite green.
- **Test seams:** code reaches what tests replace through the module (`clock.now()`, `linkedin_ops._real_publisher(...)`), never by importing the name, so one patch reaches every caller. Test patches moved with the code; each commit first ran the suite without changing the tests to see every stale patch fail, and where a stale patch could still apply (a name still present on `main`), deliberate faults were injected to prove the patched test still catches its bug. Three "never opens the database for writing" checks now patch `storage.database.migrate`, which every write-open passes through from any module; one of them had silently stopped checking anything when the scan moved (found and fixed in the resolve/schedules commit).
- **Guard:** `tests/test_patch_targets.py` reads every `setattr(<our module>, "<name>", ...)` in `tests/` and fails if the patched module's own code never uses the name (a re-export), or if another module imported the name by value and so calls around the patch. It checks itself on made-up modules. Patches whose name is not a literal string cannot be checked.
- **Why:** One 2,000-line file mixed the work, the command line and the printing. The API had to import the run path, scans and schedules from a CLI module. Splitting it makes each part small enough to read, and keeps one implementation shared by the CLI and the API.
- **Consequences:** New code imports from `operations/` or `cli/`, not from `main`. A test that patches code must patch the module whose code looks the name up; the guard enforces it. The old private names (`_run_locked`, `_publisher_for`, and so on) are unchanged, only their module changed.

## D48. Instagram publishing: System User token on graph.facebook.com, presigned S3-compatible URL (Backblaze B2), container-status recovery (ACCEPTED, 2026-11-19, after the spike)

- **Spike (2026-11-19, done by hand, outside the app):**
  - The token from the App Dashboard's "API setup with Unified login" is a **System User token** (`EAA…`) that **never expires**. It works on `graph.facebook.com`, **not** on `graph.instagram.com`.
  - Its scopes were reduced to `instagram_basic` and `instagram_content_publish` (no ads scopes).
  - `content_publishing_limit`, creating a container, and `media_publish` all worked.
  - The post is **publicly visible** although the app is in Development mode.
  - `GET /{ig-user-id}/media` returns `caption` and `timestamp` (format `+0000`). The `caption` field is missing when the caption is empty.
  - With this token, `me` is **not** the Instagram account, so the Instagram user ID must be configured.
- **Decision:**
  - **API and account.** Publish one JPEG with its caption to the developer's own professional account through the Graph API at **`graph.facebook.com`**, with the System User token. This replaces the draft's Instagram Login on `graph.instagram.com`.
  - **Token.** `INSTAGRAM_ACCESS_TOKEN` and `INSTAGRAM_USER_ID` live in `backend/.env` only. The token has **no expiry and no refresh**: there is no created date and no 60-day warning. Before anything changes, a real run checks the token once with a read-only call on the configured user ID, as D40 does for LinkedIn; a missing or rejected token refuses the run and nothing is sent. Real mode stays `--real` only.
  - **Media host: S3-compatible storage, Backblaze B2 (private bucket).**
    - The bucket is private. Objects go under a random key `ig-tmp/<uuid4>.jpg`, holding exactly the hashed bytes.
    - Meta receives a **presigned GET URL valid 1 hour**. The object is deleted once the container is final or the job ends, with a 1-day lifecycle rule as the backstop.
    - Upload, delete and presign are signed with **hand-written SigV4 using `httpx`** (no new dependency), tested against **AWS's published SigV4 test vectors**.
    - Requests use path-style URLs (`https://<endpoint>/<bucket>/<key>`).
    - The settings `MEDIA_S3_ENDPOINT`, `MEDIA_S3_REGION`, `MEDIA_S3_ACCESS_KEY_ID`, `MEDIA_S3_SECRET_ACCESS_KEY` and `MEDIA_S3_BUCKET` live in `backend/.env` only. They are never logged, stored in the job database, graph state or checkpoints, written to error reports, or returned by the API.
  - **Validation:** as D49, in every mode.
  - **Publish order:**
    1. local checks;
    2. the token check on the configured user ID;
    3. `content_publishing_limit`;
    4. upload to the bucket (the object key committed first, D50);
    5. create the container, and poll its status until `FINISHED`;
    6. commit the container ID to the job row;
    7. send `media_publish` **once**, never retried.
  - **Recovery:** as in D50 and `resolve --check` (D3). A media ID is looked up with `GET /{ig-user-id}/media?fields=id,caption,timestamp`, which reads `+0000` timestamps and treats a missing `caption` as empty.
  - **Confirmation phrase per platform:** `publish to Instagram`, checked against the request's platform. One platform's phrase never enables real mode on another.
- **First real post (2026-11-19, job `7ada9327`, media `17998365165024359`):** Meta accepted the presigned B2 URL; the container was `PUBLISHED` after `media_publish`; the B2 object was deleted and the job's media key cleared. Still unverified: the error from a second `media_publish` (the code never sends one).
- **Still unverified, checked on the first supervised real run:**
  - whether Meta accepts a presigned B2 URL;
  - the container status after `media_publish` (expected `PUBLISHED`);
  - the error a second `media_publish` on the same container returns (the code never sends one).

  Until each is confirmed, the code treats it as uncertain: an unexpected answer is `FAILED` before `media_publish` and `NEEDS_REVIEW` after it.
- **Code that must change (the real-Instagram step, not done yet):**
  - `platforms/instagram/client.py`: base URL `graph.facebook.com` (and its host allow-list), with the Graph API version in one constant.
  - Credentials: the System User token with no expiry and no refresh, and `INSTAGRAM_USER_ID` required.
  - The adapter's confirmation phrase becomes `publish to Instagram`; the API and the dashboard take it from `GET /api/platforms`.
  - **Done in step R1 (2026-11-19):** the media host is wired into the real publisher, `instagram-check` exists, the client uses `graph.facebook.com`, each adapter has its own phrase, and the D49 guards are lifted. The host itself: `platforms/s3_media_host.py` and `platforms/sigv4.py`, tested against AWS's SigV4 test vectors with `httpx.MockTransport`; nothing uses it yet.
  - The **D49 guards** (`real_available = False`, the test-transport-only client, the refused remote checker and media host) are lifted **only in that step**, with its own tests and a first supervised real post. Until then D49 holds and real Instagram stays refused in code.
- **Why:**
  - The spike showed the Unified-login token works only on `graph.facebook.com`, and that a Development-mode app's post is public, which removes the draft's biggest open question.
  - A token that never expires avoids D40's 60-day renewal routine. It is a long-lived secret, so it stays in `.env` only and is revoked in Meta's Business Settings if it leaks.
  - B2 offers S3-compatible presigned URLs on a private bucket. Code written for the S3 API also works with any other S3-compatible store.
  - Hand-written SigV4 avoids a large dependency, and the published test vectors make it verifiable without the network.
- **Consequences:**
  - New `.env` settings: `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` and the `MEDIA_S3_*` settings (placeholders in `.env.example`).
  - A Backblaze account and a private bucket with a lifecycle rule for `ig-tmp/`, outside Meta.
  - Error texts keep codes only, never messages, because they can echo a URL.
  - `docs/instagram-api-notes.md` sections 3 and 4 describe the Instagram Login path; where they disagree with this decision, this decision wins.

## D49. Simulated Instagram allowed before D48 (2026-11-19)

- **Decision:**
  - **Scope.** Instagram may be implemented in **simulated mode only**, following `docs/instagram-plan.md` (steps A–F). This amends the `CLAUDE.md` rule that Instagram had folders only. Facebook still has folders only.
  - **Superseded by D48 (step R1, 2026-11-19):** the real-Instagram refusals below were lifted when D48 was built; the rest of D49 (scheduled runs across platforms, the validation defaults, step A) still holds.
  - **Still blocked until D48 is accepted:** real Instagram publishing, real media hosting (R2 or S3), and any new dependency. D48 is still a draft (`docs/instagram-api-notes.md`, section 13); it is not recorded here until it is accepted.
  - **Refused in code, and tested.** Any real Instagram run or schedule (`run --real --platform instagram`, `schedule`/`reconfirm --real --platform instagram`, and the API's real run and schedule endpoints with Instagram) is refused before anything changes: CLI exit 1, API 400. Tests prove each refusal and that nothing was written, uploaded or sent.
  - **The Instagram publisher is built against fakes before the spike** (plan step C): the outcome mapping of plan section 7 (no stored container ID → `FAILED`; container `PUBLISHED` → `PUBLISHED`; `FINISHED`, `IN_PROGRESS` or unreadable after a lost publish → `NEEDS_REVIEW`; `media_publish` called at most once and never retried) is tested with a fake Graph API and an in-memory media host. No real run can reach it.
  - **Scheduled runs across platforms.** In `run --scheduled`, a platform whose setup is refused (for example an expired or missing token, missing data folders, or `archive/` without history) is **skipped for that run**. The reason is logged and shown. The other platforms continue, and the run still publishes **at most one post**, the oldest due one among the platforms not skipped. A skipped platform's schedules are not changed: they stay `SCHEDULED` and due, and become `MISSED` in the normal way once they are more than 6 hours late. This replaces, for scheduled runs, D46's "a refused run leaves every schedule unchanged" with a per-platform version.
  - **Cautious Instagram validation defaults are accepted** (plan section 6): the aspect-ratio limits 4:5 and 1.91:1 are inclusive; CMYK JPEGs are rejected; a JPEG is rejected when its EXIF orientation (5–8) makes the stored and rotated readings disagree about the aspect ratio; e-mail addresses count as mentions. Also from the plan: JPEG only, never converted; at most 8,000,000 bytes; MPO rejected; a caption is refused if either its code points or its UTF-16 units exceed 2,200; at most 30 hashtags (`#` followed by a word character) and 20 mentions.
  - **In step A, with tests:** `JobRepository.has_jobs` becomes platform-aware, so the archive-without-history check (D38) of one platform is never silenced by another platform's real jobs. The real-mode confirmation phrase gets a **single source of truth** (the platform adapter), replacing the second copy in `app/models/schemas.py`; LinkedIn's phrase stays `publish to LinkedIn`.
- **Why:**
  - The multi-platform refactor and Instagram's rules, storage, scheduling and dashboard can be built and tested now without any account, token, bucket or dependency. Simulated mode sends nothing, and real Instagram stays impossible in code, not only by convention.
  - Building the publisher against fakes now puts the most safety-critical part (lost responses and crash recovery) under tests early. If the spike shows different behaviour, the fakes and the mapping are updated before anything is real.
  - Skipping only the refused platform keeps one platform's expired token from making the other platform's schedules `MISSED`.
  - The cautious validation defaults err towards refusing a picture or caption, which is the safe failure: nothing is sent, and the message says what to fix.
  - `has_jobs` across all platforms would weaken D38's protection once a second platform has real jobs. Two copies of the confirmation phrase could drift apart.
- **Consequences:**
  - `CLAUDE.md` now reads "Instagram: simulated only (D49). Real Instagram is forbidden until D48 is accepted."
  - A scheduled run can now publish on one platform while another is refused.
  - **Exit code (decided 2026-11-19, step D2):**
    - **3** when a platform was skipped and the run went on with another, so Task Scheduler's history shows it;
    - **1** when *every* platform with something to publish was refused: the whole run is refused as before (D46), and nothing changes, not even the missed schedules;
    - **0** otherwise, or 3 as before for a MISSED, INVALID or FAILED outcome;
    - **1** for real errors (database, checkpoint store, crash recovery).

    Exit 1 when everything is refused keeps a LinkedIn-only setup exactly as it was: its existing tests (expired token, archive without history, missing data folders) expect exit 1 with nothing changed. A skipped platform's schedules (due, waiting and missed) are not touched; they become MISSED in a later run that is not refused for that platform. The data-folder check for LinkedIn still runs first, as a check of the data folder as a whole (D41).
  - **Instagram registered for simulated use (step D2):**
    - a real Instagram run is refused before anything changes (`real_unavailable`, exit 1);
    - a real Instagram schedule or re-confirmation is refused before the lock (`ScheduleError`, exit 1);
    - the API refuses `"real": true` for Instagram with 400 before anything happens;
    - a scheduled run treats real Instagram as a refused platform and skips it.
  - The open questions left in `docs/instagram-plan.md` section 9 (3, 5, 7–10) are answered when their step is built.
  - Step A must still leave every existing test green unchanged. The `has_jobs` change keeps LinkedIn's behaviour identical while no other platform has real jobs, which is the case until D48.

## D50. Crash recovery for platforms that record a reference before publishing (2026-11-19, step D1 and its follow-up)

- **Decision:**
  - **The rule.** A real job of a platform that records references (`records_references`, Instagram), found after a crash with no reference, becomes FAILED (may be posted again), because publishing is only ever called after the reference is committed; with a reference it becomes NEEDS_REVIEW. LinkedIn unchanged.
  - **One source of truth for the flag.** Crash recovery reads `records_references` from the adapter, while the `publish` node decides from the publisher whether to pass the recorder. A real run therefore refuses to start (`reference_mismatch`, exit 1, nothing sent; 412 in the API) when the real publisher's flag and its adapter's flag disagree, in either direction.
  - **The picture is tracked too.** The publisher commits the media host key (`jobs.media_host_key`) through `record_media` before uploading the picture, and clears it after a successful delete. A NEEDS_REVIEW outcome or a failed delete keeps the key, so any picture left on the media host can be found from its job row. A key that cannot be recorded is a definite FAILED, and nothing is uploaded.
- **Why:**
  - A job without a recorded reference provably never reached `media_publish`. Treating it as NEEDS_REVIEW would block content that was never posted, and leave the user to settle something that has a definite answer.
  - With a reference, the post may exist, so the usual "never retry an uncertain publish" rule applies.
  - The rule is only safe if every publisher of such a platform really records before publishing. The mismatch refusal makes a drift between the two flags impossible to run with.
  - Untracked pictures would stay reachable through their URLs, and no cleanup could find them.
- **Consequences:**
  - This relaxes CLAUDE.md's "real → NEEDS_REVIEW, never retried" for one case, and CLAUDE.md says so.
  - The step C publisher's `publish` takes `record_media` as well as `record`, and `MediaHost.put` takes the key chosen by `new_media_key()`.
  - Cleaning up pictures left behind (a sweep of the job rows that still have a `media_host_key`) is still future work, together with the real media host (D48).

## D51. Instagram Reels (2026-11-20)

- **Decision:**
  - **Input & Kind by extension:** Instagram accepts `.mp4` and `.mov` paired with a same-name `.txt` caption in `incoming/` or through the dashboard. The media kind is determined by file extension. LinkedIn rejects video (`"video not supported on LinkedIn"`).
  - **Validation (stdlib only, no new dependency):**
    - `ftyp`: file must start with an `ftyp` box (ISO Base Media / QuickTime).
    - `moov` before `mdat`: metadata must precede media data (fast-start streaming).
    - Edit lists: forbidden (any `elst` or `edts` atom in track/moov is rejected).
    - Duration: 3 s–15 min (from `mvhd` timescale and duration, v0 and v1).
    - Dimensions: width ≤ 1,920 and aspect ratio 0.01–10 (from visual `tkhd`, v0 and v1).
    - Size: ≤ 300 MB — exactly 300,000,000 bytes (decimal MB, not 300 MiB = 314,572,800 bytes). The backend constant (`MAX_VIDEO_BYTES`), intake, API rules, and the dashboard all use this one value; user-facing text says "300 MB".
    - Codec, fps, and bitrate are left to Meta.
  - **Publishing (graph.facebook.com, no Backblaze B2):**
    - Publishing flow:
      1. Quota check via `content_publishing_limit`.
      2. `POST /{ig-user-id}/media` with `media_type=REELS`, `upload_type=resumable`, and caption.
      3. Commit container ID through `record(container_id)`.
      4. `POST` to returned URI on `rupload.facebook.com` with headers `Authorization: OAuth <token>`, `offset: 0`, `file_size`, and `Content-Length`. Stream chunks and compute SHA-256 while sending. `Content-Length` matters: without it httpx sends the body chunked (no length).
      5. If the streamed hash differs from the recorded job hash → `FAILED`, never publish.
      6. If upload errors (network or HTTP failure) → `FAILED`.
      7. Poll container status until `FINISHED` (bounded ~10 min, injected sleep).
      8. `POST /{ig-user-id}/media_publish` sent once, never retried.
      9. Archive: post files moved to `archive/` or `failed/`.
    - Same `NEEDS_REVIEW` / `resolve --check` / crash recovery rules as images (D50).
    - Memory: the video file is never loaded into memory in full; chunks are streamed and hashed on the wire.
  - **Dashboard:** Instagram New post accepts video; upload limit is 300 MB for video only (images keep 10 MB, D43), streamed directly to disk; Queue and History show a "Reel" badge.
- **Why:**
  - Meta supports direct resumable video uploads to `rupload.facebook.com`, removing the need for an external S3/B2 media host for Reels.
  - Hashing while chunk-streaming ensures byte integrity and avoids duplicate disk reads or memory bloat.
  - Checking container ID commitment and polling for FINISHED before calling `media_publish` guarantees the post is ready and crash recovery is consistent.
- **Consequences:**
  - Instagram supports both feed images (via S3/B2) and Reels (via direct resumable upload).
  - The first real Reel was published and verified live on Instagram.
  - LinkedIn rejects video in intake and validation (`"video not supported on LinkedIn"`), so `platforms/linkedin/` is unchanged and its publisher is never reached.
  - Memory consumption stays constant regardless of video size up to 300 MB.
