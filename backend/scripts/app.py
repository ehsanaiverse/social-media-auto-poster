"""Interactive manual tester for the auto-poster.

Run from the backend folder (with the venv active):

    python scripts/app.py                    # sandbox: backend/sandbox
    python scripts/app.py --sandbox D:\\tmp\\autoposter-sandbox

Everything happens inside a sandbox folder with its own data, job database,
logs, and run lock, so your real ``data/`` and ``database/app.db`` are never
touched. Publishing is always simulated: "Run" and "Scan" call the real
``main(["run"])`` / ``main(["scan"])`` commands, which only use the Simulator.
"""

import argparse
import base64
import os
import shutil
import struct
import sys
import zlib
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from uuid import uuid4

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import BASE_DIR_VAR, DATA_DIR_VAR, Settings, load_settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.checkpoints import open_checkpointer, saved_thread_ids
from social_media_auto_poster.graph.state import WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, PublishResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db, open_app_db_read_only
from social_media_auto_poster.storage.jobs import DuplicateJobError, JobRecord, JobRepository

REPO_ROOT = Path(__file__).resolve().parents[1]  # backend/, the application's real base folder.
DEFAULT_SANDBOX = REPO_ROOT / "sandbox"
MARKER_NAME = ".autoposter-sandbox"  # Reset only ever deletes a folder that has this marker.
PLATFORM = Platform.LINKEDIN

# A real 1x1 JPEG (generated and decoded with Windows imaging). 634 bytes.
JPEG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8X"
    "GBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAAR"
    "CAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEG"
    "E1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEB"
    "AQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0"
    "tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDwyiiiv6lP5yP/2Q=="
)

INVALID_KINDS = {
    "gif": "unsupported image type (.gif)",
    "empty-caption": "valid image, empty caption",
    "renamed-text": "text file renamed to .jpg",
}

FORBIDDEN_STEM_CHARS = set('<>:"/\\|?*')


class SandboxError(Exception):
    """The sandbox location is unsafe or not a sandbox."""


# Sample files


def png_1x1(rgb: tuple[int, int, int] = (10, 102, 194)) -> bytes:
    """A real 1x1 RGB PNG, built with the standard library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit, RGB.
    pixels = zlib.compress(b"\x00" + bytes(rgb))  # Filter byte 0, then one pixel.
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def check_stem(stem: str) -> str:
    """Return a cleaned stem, or raise ``ValueError`` if it cannot be a post name."""
    stem = stem.strip()
    if not stem:
        raise ValueError("The name must not be empty.")
    if FORBIDDEN_STEM_CHARS & set(stem):
        raise ValueError(f"The name must not contain any of {''.join(sorted(FORBIDDEN_STEM_CHARS))}.")
    if stem.startswith((".", "~$")):
        raise ValueError("Names starting with '.' or '~$' are ignored by the scanner.")
    return stem


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"{path.name} already exists in incoming/.")
    path.write_bytes(data)


def _write_all(files: dict[Path, bytes]) -> list[Path]:
    # Check every target first so a post is never half-written.
    for path in files:
        if path.exists():
            raise FileExistsError(f"{path.name} already exists in incoming/.")
    for path, data in files.items():
        _write_new(path, data)
    return list(files)


def add_valid_post(incoming: Path, stem: str, caption: str, image: str = "jpg") -> list[Path]:
    stem = check_stem(stem)
    media = (incoming / f"{stem}.jpg", JPEG_1X1) if image == "jpg" else (incoming / f"{stem}.png", png_1x1())
    return _write_all({media[0]: media[1], incoming / f"{stem}.txt": caption.encode("utf-8")})


def add_invalid_post(incoming: Path, stem: str, kind: str) -> list[Path]:
    stem = check_stem(stem)
    caption = incoming / f"{stem}.txt"
    if kind == "gif":
        return _write_all({incoming / f"{stem}.gif": b"GIF89a\x01\x00\x01\x00", caption: b"A gif post"})
    if kind == "empty-caption":
        return _write_all({incoming / f"{stem}.jpg": JPEG_1X1, caption: b"   \r\n"})
    if kind == "renamed-text":
        return _write_all({incoming / f"{stem}.jpg": b"This is text, not an image.", caption: b"Renamed text"})
    raise ValueError(f"Unknown invalid kind: {kind}")


def add_lone_image(incoming: Path, stem: str) -> list[Path]:
    return _write_all({incoming / f"{check_stem(stem)}.jpg": JPEG_1X1})


def add_lone_caption(incoming: Path, stem: str, caption: str = "A caption waiting for its image") -> list[Path]:
    return _write_all({incoming / f"{check_stem(stem)}.txt": caption.encode("utf-8")})


def add_ambiguous_post(incoming: Path, stem: str) -> list[Path]:
    stem = check_stem(stem)
    return _write_all({
        incoming / f"{stem}.jpg": JPEG_1X1,
        incoming / f"{stem}.png": png_1x1(),
        incoming / f"{stem}.txt": b"Which image is meant?",
    })


# Sandbox


def real_data_dir(repo_root: Path = REPO_ROOT) -> Path:
    """The data folder the real application is configured to use (from .env and the environment)."""
    return load_settings(base_dir=repo_root).data_dir


def check_sandbox(sandbox: Path, *, repo_root: Path, real_data: Path) -> Path:
    """Return the resolved sandbox path, or raise ``SandboxError`` if it could mix with real files.

    Refuses the project folder itself, and any folder that is, is inside, or
    contains the real data folder, the real job database folder, or the real
    logs folder. ``Sandbox.prepare`` additionally refuses non-empty folders that
    are not already sandboxes (for example ``src``).
    """
    sandbox, repo_root = sandbox.resolve(), repo_root.resolve()
    if sandbox == repo_root:
        raise SandboxError(f"The sandbox must not be the project folder itself: {sandbox}")
    protected = {
        "real data folder": real_data.resolve(),
        "real job database folder": (repo_root / "database").resolve(),
        "real logs folder": (repo_root / "logs").resolve(),
    }
    for label, path in protected.items():
        if sandbox == path:
            raise SandboxError(f"The sandbox is the {label}: {sandbox}")
        if sandbox.is_relative_to(path):
            raise SandboxError(f"The sandbox is inside the {label} {path}: {sandbox}")
        if path.is_relative_to(sandbox):
            raise SandboxError(f"The sandbox contains the {label} {path}: {sandbox}")
    return sandbox


class _SimulatedCrash(Exception):
    """Stops a job inside the tester, as if the process had died."""


def _killed_before_recording(state, runtime):
    raise _SimulatedCrash("crashed after publish, before the result was recorded")


class _SandboxRealPublisher:
    """Sandbox only: pretends a real publish succeeded. Sends nothing anywhere."""

    def publish(self, post: PostCandidate, /) -> PublishResult:
        return PublishResult(success=True, simulated=False, platform_post_id=f"urn:li:share:sandbox-{uuid4().hex[:8]}")


class Sandbox:
    """A self-contained base folder: data/, database/, logs/, and the run lock all live inside it."""

    def __init__(self, root: Path):
        self.root = root
        self.settings = Settings(base_dir=root, data_dir=root / "data")

    @property
    def incoming(self) -> Path:
        return self.settings.incoming_dir(PLATFORM)

    def prepare(self) -> None:
        """Create the sandbox. Only an empty folder (or one already marked as a sandbox) is ever adopted."""
        marker = self.root / MARKER_NAME
        if self.root.exists() and not marker.is_file() and any(self.root.iterdir()):
            raise SandboxError(
                f"{self.root} is not empty and is not a sandbox; refusing to use it "
                "(Reset would delete its contents). Choose an empty or new folder."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        marker.touch()
        self.incoming.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def active(self) -> Generator[None, None, None]:
        """Run app commands with the sandbox as base folder and data folder, then restore both."""
        sandbox_env = {BASE_DIR_VAR: str(self.root), DATA_DIR_VAR: str(self.settings.data_dir)}
        previous_env = {name: os.environ.get(name) for name in sandbox_env}
        os.environ.update(sandbox_env)
        try:
            yield
        finally:
            for name, value in previous_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def run(self) -> int:
        with self.active():
            return cli.main(["run", "--interval", "0"])

    def scan(self) -> int:
        with self.active():
            return cli.main(["scan", "--interval", "0"])

    def jobs(self) -> int:
        with self.active():
            return cli.main(["jobs"])

    def resolve(self, *args: str) -> int:
        with self.active():
            return cli.main(["resolve", *args])

    def needs_review(self) -> list[JobRecord]:
        """NEEDS_REVIEW jobs in the sandbox database, read-only."""
        try:
            conn = open_app_db_read_only(self.settings.app_db_path)
        except FileNotFoundError:
            return []
        try:
            return JobRepository(conn).list_recent(100, status=JobStatus.NEEDS_REVIEW)
        finally:
            conn.close()

    def folder_contents(self) -> dict[str, list[str]]:
        contents = {}
        for name in ("incoming", "failed", "archive"):
            folder = self.settings.platform_dir(PLATFORM) / name
            files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []
            contents[name] = [p.relative_to(folder).as_posix() for p in files if p.name != ".gitkeep"]
        return contents

    def history(self, limit: int = 20) -> list[JobRecord]:
        """Recent jobs from the sandbox database, read-only. Empty if no run has happened yet."""
        try:
            conn = open_app_db_read_only(self.settings.app_db_path)
        except FileNotFoundError:
            return []
        try:
            return JobRepository(conn).list_recent(limit)
        finally:
            conn.close()

    def simulate_crash(self, stem: str, mode: PublishMode) -> str:
        """Record the post ``stem`` (in incoming/) as PUBLISHING, as if a run died mid-publish.

        Returns the new job ID. The next Run's startup recovery resolves it.
        """
        post = self._complete_post(stem)
        media, caption = post.media_path, post.caption_path
        conn = open_app_db(self.settings.app_db_path)
        try:
            job_id = uuid4().hex
            JobRepository(conn).create(
                job_id=job_id,
                platform=PLATFORM,
                mode=mode,
                stem=stem,
                media_name=media.name,
                caption_name=caption.name,
                media_sha256=sha256_file(media),
                caption_sha256=sha256_file(caption),
                status=JobStatus.PUBLISHING,
            )
        finally:
            conn.close()
        return job_id

    def simulate_crash_after_publish(self, stem: str, mode: PublishMode) -> str:
        """Run the post ``stem`` through the real graph and "kill" it right after publish returned.

        Leaves a PUBLISHING row and a saved checkpoint whose next step is record_result,
        so the next Run resumes the job from its checkpoint. Real mode uses a sandbox-only
        fake publisher with a made-up post ID: nothing is sent anywhere. Returns the job ID.
        """
        post = self._complete_post(stem)
        if not validate_candidate(post).is_valid:
            raise ValueError(f"'{post.stem}' is not a valid post; add a valid one first.")
        conn = open_app_db(self.settings.app_db_path)
        try:
            repository = JobRepository(conn)
            if repository.find_blocking_duplicate(
                platform=PLATFORM, mode=mode,
                media_sha256=sha256_file(post.media_path), caption_sha256=sha256_file(post.caption_path),
            ):
                raise ValueError(f"'{post.stem}' is already recorded in {mode} mode; add a new post first.")
            publisher = Simulator() if mode is PublishMode.SIMULATED else _SandboxRealPublisher()
            with open_checkpointer(self.settings.checkpoint_db_path, app_db_path=self.settings.app_db_path) as saver:
                before = set(saved_thread_ids(saver))
                context = WorkflowContext(publisher, mode, repository, self.settings, 0, checkpointer=saver)
                with mock.patch.object(nodes, "record_result", _killed_before_recording):
                    try:
                        run_job(post, context)
                    except _SimulatedCrash:
                        pass
                [job_id] = set(saved_thread_ids(saver)) - before
        finally:
            conn.close()
        return job_id

    def _complete_post(self, stem: str) -> PostCandidate:
        stem = check_stem(stem)
        media = next(
            (self.incoming / f"{stem}{ext}" for ext in (".jpg", ".jpeg", ".png") if (self.incoming / f"{stem}{ext}").is_file()),
            None,
        )
        caption = self.incoming / f"{stem}.txt"
        if media is None or not caption.is_file():
            raise ValueError(f"No complete post named '{stem}' in incoming/ (needs an image and {stem}.txt).")
        return PostCandidate(PLATFORM, stem, media, caption)

    def reset(self) -> None:
        """Delete everything in the sandbox. Refuses unless the sandbox marker file is present."""
        if not (self.root / MARKER_NAME).is_file():
            raise SandboxError(f"{self.root} has no {MARKER_NAME} marker; refusing to delete anything.")
        for child in self.root.iterdir():
            if child.name == MARKER_NAME:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.prepare()


# Interactive menu


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or (default or "")


def _choose(prompt: str, options: dict[str, str]) -> str:
    keys = list(options)
    for number, key in enumerate(keys, 1):
        print(f"   {number}) {options[key]}")
    answer = _ask(prompt, "1")
    if not answer.isdigit() or not 1 <= int(answer) <= len(keys):
        raise ValueError("Please choose one of the listed numbers.")
    return keys[int(answer) - 1]


def _print_added(paths: list[Path]) -> None:
    print("Added to incoming/: " + ", ".join(p.name for p in paths))


def _print_folders(sandbox: Sandbox) -> None:
    for name, files in sandbox.folder_contents().items():
        print(f"\n{name}/ ({len(files)})")
        for file in files:
            print(f"  {file}")


def _menu_actions(sandbox: Sandbox) -> dict[str, tuple[str, Callable[[], object]]]:
    incoming = sandbox.incoming

    def valid():
        stem = _ask("Post name", "product-launch")
        caption = _ask("Caption text", "Our new product is live!")
        image = _choose("Image type", {"jpg": "JPEG", "png": "PNG"})
        _print_added(add_valid_post(incoming, stem, caption, image))

    def invalid():
        kind = _choose("Kind of invalid post", INVALID_KINDS)
        _print_added(add_invalid_post(incoming, _ask("Post name", f"bad-{kind}"), kind))

    def crash_after_publish():
        stem = _ask("Name of a complete, new post in incoming/", "product-launch")
        mode = _choose(
            "Mode of the interrupted job",
            {PublishMode.SIMULATED: "simulated (resumes as SIMULATED)",
             PublishMode.REAL: "real, with a fake sandbox publisher (resumes as PUBLISHED and is archived)"},
        )
        job_id = sandbox.simulate_crash_after_publish(stem, PublishMode(mode))
        print(f"Job {job_id[:8]} published, then 'crashed' before recording it. Choose 'Run' to see it resumed.")

    def crash():
        stem = _ask("Name of a complete post in incoming/", "product-launch")
        mode = _choose(
            "Mode of the interrupted publish",
            {PublishMode.SIMULATED: "simulated (recovers as FAILED, then runs again)",
             PublishMode.REAL: "real (recovers as NEEDS_REVIEW, moved to failed/)"},
        )
        job_id = sandbox.simulate_crash(stem, PublishMode(mode))
        print(f"Job {job_id[:8]} is now stuck as PUBLISHING. Choose 'Run' to see startup recovery.")

    def resolve():
        waiting = sandbox.needs_review()
        if not waiting:
            print("No NEEDS_REVIEW jobs. Use 'Simulate a crash during publish' with mode real, then 'Run'.")
            return
        print("NEEDS_REVIEW jobs:")
        for record in waiting:
            print(f"   {record.job_id[:8]}  {record.stem}  ({record.mode})")
        prefix = _ask("Job ID (first 6+ characters)", waiting[0].job_id[:8])
        outcome = _choose(
            "Resolve as",
            {"failed": "FAILED (it was not published; allows a retry)",
             "published": "PUBLISHED (it is live; keeps the content blocked)"},
        )
        if outcome == "failed":
            args = [prefix, "--failed"]
        else:
            args = [prefix, "--published", _ask("Post ID", "urn:li:share:demo")]
        print(f"\nexit code: {sandbox.resolve(*args)}")

    def reset():
        if _ask(f"Delete everything in {sandbox.root}? Type 'yes' to confirm") == "yes":
            sandbox.reset()
            print("Sandbox reset.")
        else:
            print("Not reset.")

    return {
        "1": ("Add valid post", valid),
        "2": ("Add invalid post", invalid),
        "3": ("Add lone image (pending)", lambda: _print_added(add_lone_image(incoming, _ask("Name", "lonely")))),
        "4": ("Add lone caption (pending)", lambda: _print_added(add_lone_caption(incoming, _ask("Name", "waiting")))),
        "5": ("Add ambiguous post", lambda: _print_added(add_ambiguous_post(incoming, _ask("Name", "banner")))),
        "6": ("Run (simulated)", lambda: print(f"\nexit code: {sandbox.run()}")),
        "7": ("Scan (read-only)", lambda: print(f"\nexit code: {sandbox.scan()}")),
        "8": ("Show incoming/failed/archive", lambda: _print_folders(sandbox)),
        "9": ("Show job history (jobs command)", lambda: print(f"\nexit code: {sandbox.jobs()}")),
        "10": ("Reset sandbox", reset),
        "11": ("Simulate a crash during publish", crash),
        "12": ("Resolve a NEEDS_REVIEW job", resolve),
        "13": ("Simulate a crash after publish (resume)", crash_after_publish),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive manual tester (sandbox, simulation only).")
    parser.add_argument("--sandbox", type=Path, default=DEFAULT_SANDBOX, help=f"Sandbox folder (default: {DEFAULT_SANDBOX})")
    args = parser.parse_args(argv)

    try:
        root = check_sandbox(args.sandbox, repo_root=REPO_ROOT, real_data=real_data_dir())
    except SandboxError as error:
        print(f"Refusing to start: {error}", file=sys.stderr)
        return 1
    sandbox = Sandbox(root)
    try:
        sandbox.prepare()
    except SandboxError as error:
        print(f"Refusing to start: {error}", file=sys.stderr)
        return 1
    print(f"Sandbox: {root}   (simulation only; your real data is never touched)")

    actions = _menu_actions(sandbox)
    while True:
        print()
        for key, (label, _) in actions.items():
            print(f" {key:>2}) {label}")
        print("  0) Exit")
        try:
            choice = _ask("Choose")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "0":
            return 0
        if choice not in actions:
            print("Unknown choice.")
            continue
        try:
            actions[choice][1]()
        except (ValueError, FileExistsError, SandboxError, SchemaTooNewError, DuplicateJobError) as error:
            print(f"Error: {error}")
        except SystemExit as exit_:  # An app command rejected its arguments (argparse); keep the menu open.
            print(f"exit code: {exit_.code}")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
