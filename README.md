# Social Media Auto-Poster

A local, folder-driven Python application scaffold for preparing and publishing social media content. The project is being built incrementally, starting with LinkedIn and adding other platforms only after their integrations are separately planned and validated.

## MVP goals

- Discover content placed in platform-specific incoming folders.
- Pair image files with UTF-8 caption files and validate them before processing.
- Track post state and processing history so interrupted work can be recovered safely.
- Publish through official platform APIs, archive only confirmed successes, and retain failures with useful diagnostics.
- Prevent duplicate processing and avoid blindly retrying requests whose publishing outcome is ambiguous.

## Non-goals for this scaffold

There is no working folder watcher, content validator, database/repository, scheduler, OAuth flow, or live publishing integration yet. The simulator does not contact a platform and its result is not confirmation of publication. This scaffold does not claim API access is configured.

## Current status

**Scaffold only.** The package and content-folder layout, dependency manifest, environment-variable template, publisher contract, and safe simulation publisher are present. Application workflows and automated tests have not yet been implemented.

Platform order:

1. LinkedIn — first integration milestone; not implemented.
2. Facebook — planned, not implemented.
3. Instagram — planned, not implemented.

Facebook and Instagram are placeholders for future work, not supported integrations.

## Project layout

```text
data/                              Local platform content; ignored by Git
  LinkedIn/{incoming,archive,failed}/
  Facebook/{incoming,archive,failed}/
  Instagram/{incoming,archive,failed}/
src/social_media_auto_poster/
  core/                            Configuration, logging, and shared errors
  ingestion/                       Folder monitoring, stability, pairing, validation
  models/                          Shared post data types
  storage/                         SQLite setup and post repository
  publishing/                      Platform-neutral publisher contract and simulator
  platforms/linkedin/              Reserved for LinkedIn auth, client, and publisher
  platforms/facebook/              Reserved for a future Facebook integration
  platforms/instagram/             Reserved for a future Instagram integration
  workflow/                        Processing and archive orchestration
  scheduling/                      Reserved for scheduling and recovery coordination
tests/                             Unit, integration, and fixture locations
scripts/                           Developer and maintenance scripts
docs/                              Design and operational documentation
logs/                              Local application logs; ignored by Git
```

The folders for future components are intentionally lightweight. Their implementation modules will be added with the corresponding milestones rather than filled with nonfunctional stubs. `publishing/base.py` defines a generic contract without assuming platform-specific media, permissions, or API behavior. `publishing/simulator.py` is a development-only fake and never sends content.

## Content naming convention

The initial convention is one image and one UTF-8 text caption with the same filename stem, for example:

```text
data/LinkedIn/incoming/product-launch.jpg
data/LinkedIn/incoming/product-launch.txt
```

The planned initial image extensions are `.jpg`, `.jpeg`, and `.png`. Pairing, extension validation, and file-copy stability checks are not implemented yet. This convention is provisional; media support will be reviewed against each platform's official API before implementation.

## Windows setup

Use Python 3.11 or newer. From PowerShell in the project root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Verify the environment:

```powershell
python --version
python -m pip check
```

There is no runnable application entry point yet. Once `main.py` is implemented, the intended invocation is:

```powershell
python -m social_media_auto_poster.main
```

## Configuration and security

Copy `.env.example` to `.env` when local configuration is introduced. `.env` is ignored by Git; `.env.example` contains only blank placeholders. Do not commit credentials, access tokens, local databases, content, or logs. OAuth and API access are not configured by this scaffold. Future integrations must use official APIs and comply with their permissions and policies.

SQLite is planned for durable post state and restart recovery, but no database has been created. A publishing request with an unknown outcome must not be retried automatically without a platform-safe way to establish whether it succeeded. Content should be archived only after success is confirmed.

## Development milestones

1. Define content validation, file stability checks, and safe pairing behavior.
2. Implement SQLite state tracking, recovery, and duplicate prevention.
3. Implement and validate LinkedIn authorization and publishing using official APIs; make uncertain outcomes explicit.
4. Add scheduling, retry/recovery policy, and archive/failure handling.
5. Plan and implement Facebook, then Instagram, as separate platform integrations.

No live publishing, OAuth, or file-processing feature should be considered available until implemented and covered by appropriate tests.
