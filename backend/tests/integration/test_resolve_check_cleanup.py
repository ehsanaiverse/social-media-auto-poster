"""E1: after ``resolve --check`` finds a post PUBLISHED, its old failed/ folder no longer looks failed.

The folder is removed only when it holds nothing but what the app wrote there for this job (its marker and
error report). Anything else, such as a file the user put there, leaves the folder untouched, and the output
says so.
"""

from pathlib import Path

from instagram_fake import MEDIA_ID, json_response
from test_resolve_check import (  # noqa: F401  (pytest fixtures)
    check,
    checker_graph,
    faked,
    host,
    needs_review_job,
    settings,
    status,
    stored,
)
from social_media_auto_poster.models import JobStatus


def _published(faked):
    faked.answers["status"] = status("PUBLISHED")
    faked.answers["media"] = json_response(200, {"data": [{"id": MEDIA_ID, "media_type": "IMAGE",
                                                           "timestamp": "2099-01-01T00:00:00+0000"}]})


def test_the_old_failed_folder_is_removed_after_published(settings, host, faked, capsys):
    job_id = needs_review_job(settings, host)
    failed_folder = Path(stored(settings, job_id).destination)
    _published(faked)

    assert check(job_id) == 0

    assert not failed_folder.exists()
    assert stored(settings, job_id).status is JobStatus.PUBLISHED
    assert f"the old folder {failed_folder.name} in failed/ was removed" in capsys.readouterr().out


def test_an_old_failed_folder_with_other_files_is_left_as_it_is(settings, host, faked, capsys):
    job_id = needs_review_job(settings, host)
    failed_folder = Path(stored(settings, job_id).destination)
    (failed_folder / "my-notes.txt").write_text("kept by the user", encoding="utf-8")
    _published(faked)

    assert check(job_id) == 0

    assert (failed_folder / "my-notes.txt").read_text(encoding="utf-8") == "kept by the user"
    assert f"the old folder {failed_folder.name} in failed/ holds other files; it was left as it is" in \
        capsys.readouterr().out


def test_a_failed_answer_keeps_the_failed_folder(settings, host, faked):
    job_id = needs_review_job(settings, host)
    failed_folder = Path(stored(settings, job_id).destination)
    faked.answers["status"] = status("EXPIRED")

    assert check(job_id) == 0

    assert (failed_folder / "launch.jpg").is_file() and (failed_folder / "error-report.txt").is_file()
