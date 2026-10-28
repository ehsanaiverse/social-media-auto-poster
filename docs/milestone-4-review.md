# Milestone 4 review: real LinkedIn publishing

**Date:** 2026-10-28. **Status:** implemented, tested, and confirmed with one real post; awaiting the developer's sign-off.

Milestone 4 lets the auto-poster publish one image with its caption to the developer's own LinkedIn profile, through LinkedIn's official API, while keeping every safety rule from Milestones 1–3. Real publishing only happens when `--real` is typed on the command line.

## What was built

| Step | What it does | Commits |
| --- | --- | --- |
| 4.0 Research and design | Read LinkedIn's current official docs and recorded them with sources in `docs/linkedin-api-notes.md`; recorded the design (D36) and the image-size approach (D37) | `461e49a`, `13d3011` |
| 4.1 `--real` and safeguards | `run --real` / `scan --real`. A real run refuses, publishing nothing, when posts were only simulated so far (unless `--publish-simulated`), or when `archive/` holds posts the job database does not know about (D38) | `0335133`, `1992329`, `5a1167c` |
| 4.2 Exact bytes | The publish step reads both files once, checks them against the hashes from the duplicate check, and hands exactly those bytes to the publisher. A changed or missing file fails before anything is sent | `6a5938a`, `39eab7d` |
| 4.3 LinkedIn client | Caption escaping and the 3,000-character limit; image size read from the PNG/JPEG header (no Pillow); the HTTP client and the publisher with the agreed outcome rules; no retries; the token never written anywhere (D39) | `b9a3a9c`, `9c941a5`, `11c124e`, `a078101` |
| 4.4 Credentials | The token and its creation date from `backend\.env`; expiry warnings; a token check with LinkedIn before a real run changes anything; `linkedin-check` and its `--upload-test` (D40) | `7c55c30`, `7c1a40b` |
| Data folder safeguard | A warning when the data folder is outside `backend\`; a real run never creates the data folder and refuses if its LinkedIn folders are missing (D41) | `61b54b6` |

## How a real post works now

`run --real`, in order:

1. Checks the data folder has its LinkedIn `incoming`, `archive`, and `failed` folders (never creates them).
2. Refuses if `archive\` holds posts but the job database has no real job (it may have been replaced or lost).
3. Reads the token from `backend\.env`: missing or expired stops the run; within 7 days of expiry, a warning.
4. Checks the token with LinkedIn (`GET /v2/userinfo`, read-only). Steps 1–4 happen before anything changes.
5. Recovers interrupted work (checkpoints first, then the job database).
6. Scans `incoming\` and refuses if any post was only simulated so far (unless `--publish-simulated`).
7. For each post: stability, validation, duplicate check (by content), the job recorded as `PUBLISHING`, then the publish step:
   - reads the exact bytes and checks their hashes;
   - checks the caption (3,000 characters) and the image size (< 36,152,320 pixels) locally;
   - registers the image upload, uploads the bytes, and waits until LinkedIn has processed the image;
   - creates the post with `POST /rest/posts`, **once**.
8. Records the outcome, then archives the files only after a confirmed `PUBLISHED`.

| Answer to creating the post | Result |
| --- | --- |
| `201` with a post ID | `PUBLISHED`, archived |
| Connection never opened (`ConnectError`, `ConnectTimeout`) | `FAILED`: nothing was sent |
| 400, 401, 403, 404, 422, 429 | `FAILED`: LinkedIn refused it (401: renew the token) |
| Timeout or dropped connection after sending, 5xx, 409, `201` without a post ID, any other answer | `NEEDS_REVIEW`: the post may exist; never retried |
| Any problem before this call (file changed, caption too long, image too large, upload failed) | `FAILED`: no post can exist |

## Safety rules and how they are tested

| Rule | Tested by |
| --- | --- |
| Real mode only with `--real`, never from a setting | `test_real_mode_run.py` (a plain run never asks for the real publisher) |
| Each outcome rule above, one test per case | `test_linkedin_publisher.py` (fake LinkedIn through `httpx.MockTransport`) |
| `POST /rest/posts` sent exactly once, no retries or redirects | every outcome test counts the calls; the default transport is checked for `retries=0`; the next run after `NEEDS_REVIEW` sends nothing (`test_linkedin_run.py`) |
| The token never written anywhere | after each end-to-end scenario, every file the app wrote and all output are searched byte for byte; a crash after posting leaves a checkpoint that holds the post ID but not the token (`test_linkedin_run.py`) |
| The caption never logged; LinkedIn's error messages (which can echo it) never kept | `test_linkedin_publisher.py`, `test_linkedin_run.py` |
| Exactly the hashed bytes are published | `test_content.py`, `test_workflow.py` |
| Image size from real headers, including a JPEG whose size marker comes after EXIF, a progressive JPEG, and truncated headers | `test_linkedin_checks.py`, with images written by Windows' own encoder |
| No test can reach the network or see the real token | `tests/conftest.py` blocks httpx's real transport and removes the LinkedIn settings |

## Results

- **Tests:** 537 passed. The only warning is a third-party `DeprecationWarning` inside `langsmith`; with Python's development mode and `ResourceWarning` enabled there are no unclosed files or database connections.
- **`linkedin-check --upload-test` against the real LinkedIn:** token accepted (posts go to the developer's profile, `urn:li:person:d8Uo3LT0Cm`); a test image uploaded (never used in a post) and its processing status was readable (`AVAILABLE`). So the Images API is used; no fallback is needed.
- **First real post:** `scan --real` showed `first-post` as new and valid. `run --real` published it (exit code 0): job `41ac71e1`, status `PUBLISHED`, post ID `urn:li:share:7521116341425037312`, files archived to `LinkedIn\archive\20261028T075107Z_first-post`. The post appeared on the developer's LinkedIn profile with its image. The developer then deleted it on LinkedIn to repost it with an edited caption; job `41ac71e1` is deliberately kept as `PUBLISHED` in the history.
- **The token was not found** in any of 101 files checked after the real run: logs, both databases, the data and sandbox folders, and every file tracked by Git.
- **Found before posting:** `AUTOPOSTER_DATA_DIR` in `backend\.env` still pointed at the old repo-root `data` folder, so `scan` and `run` used it and `run --real` created empty folders there. The developer fixed the setting; the data folder safeguard (D41) now warns about this and stops a real run from creating folders. The stray root `data` folder holds no files.

## Known limitations

1. **Uncertain publishes cannot be checked automatically.** On a personal profile, LinkedIn only lets approved partners list a member's posts (`r_member_social`). A post whose outcome is unknown stays `NEEDS_REVIEW` until the developer looks at the profile and runs `resolve <job> --published <post ID>` or `resolve <job> --failed`.
2. **The token must be renewed by hand every 60 days.** The current token expires on **2026-12-27**: warnings start on 2026-12-20, and real runs are refused from 2026-12-27. Renew it in the Developer Portal's token generator, then update `LINKEDIN_ACCESS_TOKEN` and `LINKEDIN_TOKEN_CREATED` in `backend\.env`, and run `linkedin-check`.
3. **No login inside the app yet.** The token comes from the Developer Portal; `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `LINKEDIN_REDIRECT_URI` are unused. An in-app OAuth login (and whether LinkedIn accepts a localhost redirect) is future work.
4. **A definite refusal moves the post to `failed\`.** For example, a rate limit (429) or a refused post moves the files to `failed\` with an error report; move them back to `incoming\` once the cause is fixed. (A rejected or expired token is caught before anything changes.)
5. **The app does not see changes made on LinkedIn.** A post deleted on LinkedIn stays `PUBLISHED` in the job history, so identical content (same image and caption bytes) is reported as a duplicate. Edited content is a new post.
6. **A failed archive move after `PUBLISHED` is not retried.** The job stays `PUBLISHED` with the error recorded, and the files stay in `incoming\`, where later runs report them as a duplicate. Move them by hand.
7. **Scope:** one JPEG or PNG image per post, personal profile only, no video, multiple images, mentions, or company pages. Captions are limited to 3,000 characters, counted the way browsers count (an emoji counts as two).
8. **`Linkedin-Version` is `202610`.** LinkedIn supports each version for at least a year; update the constant in `platforms/linkedin/client.py` before October 2027, or as soon as LinkedIn announces its sunset, then run the tests and `linkedin-check`.
9. **Reconciliation's own move to `failed\`** (for a real job interrupted during the publish call) is not resumed after a crash; a crash in the middle of it can leave the two files split, which is reported for manual repair (D34).
10. **One-shot runs.** There is no scheduler or folder watcher yet (Milestone 5); waiting for LinkedIn to process an image can take up to about 30 seconds per post.

## Backups

The job database is the only record of what was published, and the only protection against posting the same content twice. Back up `backend\database\app.db` regularly (at least before each real run), and restore that file if it is ever lost. A real run refuses to start when `archive\` holds published posts but the database has no record of any real job. Details are in the root `README.md`.

## Next

After sign-off: Milestone 5 (reliability: scheduling or folder watching, monitoring, operational docs), or the planned FastAPI API and React frontend (D32), which must reuse the same run path and keep every rule above.
