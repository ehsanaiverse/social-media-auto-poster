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
