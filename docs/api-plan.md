# API plan (FastAPI, for the React frontend)

**Status:** plan only, no code yet (2026-10-28). To be built by Mouzan. Binding rules for the project are in `CLAUDE.md` ("3. Web UI later", D32); decisions are in `docs/decisions.md`.

## Rules

1. Lives in `backend/src/social_media_auto_poster/api/`. FastAPI and Uvicorn are an optional extra, imported only by `api/`; the CLI must keep working without them.
2. **Reuses the backend; no duplicated logic, no second database or storage layer.** Job data only through `storage/` (`JobRepository`, `open_app_db_read_only`); posts only through the same graph (`graph.workflow.run_job`) and run path as the CLI. Job IDs are UUID strings.
3. **Binds to `127.0.0.1` only.** Never returns the LinkedIn token, `.env` values, or other secrets; error texts come from the backend, which never contains the token (D39, D40).
4. **Simulated by default.** Real publishing only with `real: true` **and** an explicit confirmation, behind every `run --real` safeguard (data folders exist, archive vs. history, token check, posts only simulated) (D38, D40, D41).
5. **Same single-run lock as the CLI** (`services/run_lock.single_run_lock`) for anything that changes state (run, resolve); a held lock → `409`.
6. **Uploads** accept only `.jpg`/`.jpeg`/`.png` (`ingestion.validation.EXPECTED_TYPE` plus the signature check), a size limit (open question 3), captions as UTF-8 up to `MAX_CAPTION_BYTES` (64 KB), and pass the same `validate_candidate` as the CLI before the post appears in `incoming/`.
7. **Browser safety:** CORS limited to the frontend's origin; state-changing calls need `Content-Type: application/json` (or, for the upload, a custom header), so a random web page open in the same browser cannot trigger a run (cross-site request forgery).

## Existing functions each CLI command uses

| Command | Functions (module: function) |
| --- | --- |
| `scan` | `config.load_settings`; `ingestion.scanner.scan_incoming`; `ingestion.stability.is_file_stable`; `ingestion.validation.validate_candidate`; `services.hashing.sha256_file`; `storage.database.open_app_db_read_only`; `storage.jobs.JobRepository.find_blocking_duplicate`, `.list_by_status`; with `--real`: `services.real_mode.posts_only_simulated`, `archive_without_history`; `config.data_dir_outside_base` |
| `run` | `config.load_settings`, `missing_platform_dirs`, `ensure_data_dirs`, `data_dir_outside_base`; `services.run_lock.single_run_lock`; `storage.database.open_app_db`; `graph.checkpoints.open_checkpointer`; `services.real_mode.archive_without_history`, `posts_only_simulated`; `platforms.linkedin.credentials.load_linkedin_credentials`; `platforms.linkedin.publisher.LinkedInPublisher.check_access`; `graph.resume.resume_interrupted_jobs`; `services.reconcile.reconcile_interrupted_jobs`; `ingestion.scanner.scan_incoming`; `graph.workflow.run_job` |
| `jobs` | `storage.database.open_app_db_read_only`; `JobRepository.list_recent`, `.find_by_id_prefix` |
| `resolve` | `single_run_lock`; `storage.database.open_app_db`; `JobRepository.find_by_id_prefix`, `.resolve_needs_review` |
| `linkedin-check` | `load_linkedin_credentials`; `LinkedInCredentials.state`, `.expires_on`, `.describe`; `platforms.linkedin.check.run_check` (with `client.LinkedInClient`) |

## Endpoints

| Method | Endpoint | Does | Reuses (exact function) | Request | Response | Safety notes |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/health` | Is the API up | `config.load_settings` | none | `{status, simulated_by_default: true, linkedin_version}` | No paths beyond the data folder, no secrets |
| POST | `/api/posts` | Add a post (image + caption) to LinkedIn `incoming/` | new `ingestion.intake.add_post` (refactor 1) → `validate_candidate`, `sha256_file`, `find_blocking_duplicate` | multipart: `name`, `image`, `caption` | `201 {name, files, duplicate_of?}` | Type, signature, size, UTF-8 checked first; written to ignored temp names, then moved in; never overwrites |
| GET | `/api/scan?real=false` | Read-only preview of `incoming/` | `scan_preview` (refactor 2) → `scan_incoming`, `is_file_stable`, `validate_candidate`, `find_blocking_duplicate`, `posts_only_simulated`, `archive_without_history` | query `real` | `{candidates: [{name, stable, valid, errors, duplicate_of, only_simulated}], pending, ambiguous, warnings}` | No lock, never moves or creates anything (like the CLI) |
| POST | `/api/run` | Process every complete post once | `run_once` (refactor 3) → the whole `run` path | JSON `{real: false, publish_simulated: false, confirm: null}` | `{exit_code, mode, resumed, recovered, jobs: [...], pending, ambiguous, refused?}` | Real needs `real: true` + confirmation (question 2) + all `run --real` safeguards; lock |
| GET | `/api/jobs?last=20&status=` | Job history | `open_app_db_read_only`, `JobRepository.list_recent` | query | `[{job_id, created_at, stem, mode, status, errors}]` | Read-only, never creates the database |
| GET | `/api/jobs/{id}` | One job in full (ID or a prefix of 6+) | `JobRepository.find_by_id_prefix` | path | `{…all job fields, hashes, post_id, destination}` | Read-only |
| POST | `/api/jobs/{id}/resolve` | Settle a `NEEDS_REVIEW` job | `resolve_job` (refactor 4) → `find_by_id_prefix`, `resolve_needs_review` | JSON `{published_post_id}` or `{failed: true}` | `{job_id, status, note, files_at}` | Lock; only `NEEDS_REVIEW`; `--published` refused for simulated jobs |
| GET | `/api/linkedin/status?check=false` | Token present, expiry date, days left; with `check=true` also asks LinkedIn | `load_linkedin_credentials`, `LinkedInCredentials.state`, `.expires_on`; `check=true`: `run_check` (refactor 5) | query `check` | `{token_set, state, expires_on, days_left, member?}` | Never the token. No upload test from the API (question 6) |

## Small refactors needed first (listed, not done)

1. **Adding a post:** no package function writes a post into `incoming/`; the sandbox tester's `scripts/app.py: check_stem` and file writing are not in the package. Add `ingestion/intake.py: add_post(settings, name, image_bytes, extension, caption_text)`, reusing `validate_candidate`, and move the name rule (`check_stem`) into the package so the tester uses it too.
2. **Scan:** `main._scan`, `_print_candidates`, `_duplicate_label`, `_stability_label` print text. Move the logic into `scan_preview(...) -> ScanPreview` (data); the CLI prints it.
3. **Run:** `main._run`, `_run_locked`, `_publisher_for`, `_real_publisher`, `_process`, `_describe`, `_footer`, and the `_print_*` helpers mix work, printing, and exit codes. Move the work into a `run_once(settings, mode, publish_simulated, interval) -> RunReport` (data, including refusal reasons as typed values instead of printed text); the CLI prints the report and maps it to exit codes. The logging setup (`_logging_to`, `_run_scope`) must be shared so API runs write the same "Run started/finished" lines. The archived branch `archive/api-rewire` (`5f19400`) did a first version of this split.
4. **Resolve:** `main._resolve`, `_resolve_job`, `_print_resolve_next_steps` print and return exit codes. Add `resolve_job(...) -> ResolveResult`, raising typed errors (no match, ambiguous, wrong status, simulated published, lock held).
5. **LinkedIn status:** `platforms/linkedin/check.run_check` returns display lines (`CheckReport.lines`); add structured fields (token state, member, image check) for the API.
6. **Jobs:** `main._jobs`/`_show_job` only format; the API can call `JobRepository` directly. The duplicate wording (`_describe_original`) moves next to the repository so both use it.

## Status codes

| Endpoint | Codes |
| --- | --- |
| `GET /api/health` | 200 |
| `POST /api/posts` | 201 added; 400 invalid (type, signature, empty or non-UTF-8 caption, bad name); 409 a post with that name exists; 413 file too large |
| `GET /api/scan` | 200; 404 incoming folder missing |
| `POST /api/run` | 200 completed (`exit_code` 0 or 3 in the body); 400 bad request (e.g. `publish_simulated` without `real`, missing confirmation); 409 run lock held; 412 a `run --real` safeguard refused (body names it: data folders, archive vs. history, token, posts only simulated); 500 setup error (database, checkpoint store) |
| `GET /api/jobs` | 200 (empty list if no database yet); 500 database unreadable |
| `GET /api/jobs/{id}` | 200; 400 prefix shorter than 6; 404 no match; 409 more than one match |
| `POST /api/jobs/{id}/resolve` | 200; 400 bad body or blank post ID; 404 no match or no database; 409 ambiguous prefix, not `NEEDS_REVIEW`, or run lock held; 422 `published` for a simulated job |
| `GET /api/linkedin/status` | 200 (state `missing`, `expired`, `expiring`, `valid`, `unknown` in the body); 502 `check=true` and LinkedIn unreachable |

## Open questions (for the developer and Mouzan)

1. **Run in the request, or in the background?** A run waits up to about 30 s per post for LinkedIn's image processing. Simplest: a synchronous `POST /api/run`. Alternative: start it and poll `GET /api/runs/{run_id}`.
2. **Confirming a real run:** a typed phrase in the body (for example `confirm: "publish to LinkedIn"`), or a preview from `GET /api/scan?real=true` whose ID `POST /api/run` must echo, so only what was previewed is published?
3. **Upload limits:** maximum image size (proposal: 10 MB)? Reject captions over 3,000 characters at upload, or only in real runs as now?
4. **Name clashes on upload:** refuse with 409 (proposed), or add a suffix?
5. **Image previews:** should the frontend show images from `incoming/`, `archive/`, `failed/`? If so, only by job ID or post name inside the data folder, never by path.
6. **`linkedin-check --upload-test` from the UI?** It uploads a test image to LinkedIn; proposal: CLI only.
7. **Port and origin:** API on `127.0.0.1:8000`, Vite dev server on `http://localhost:5173`?
8. **Order of work:** do the refactors (1–6 above) first, as their own small commits with tests, before any FastAPI code? Who does them?
