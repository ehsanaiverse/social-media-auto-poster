"""Fix 4 (D50): a real run refuses when the publisher and its adapter disagree about recording a reference.

Crash recovery trusts the adapter's ``records_references`` (a real job without a reference is FAILED and may be
posted again). A publisher that publishes without recording one, while its adapter says it records one, could
then post twice after a crash, so a disagreement in either direction stops the run before anything is sent.
"""

from pathlib import Path

import pytest

from social_media_auto_poster.config import Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.models import Platform, PublishMode, PublishResult
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations.run import run_once

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()


class FakePublisher:
    def __init__(self, *, records_references: bool | None = None):
        if records_references is not None:
            self.records_references = records_references
        self.published: list[str] = []
        self.closed = False

    def publish(self, post, /, **kwargs):
        self.published.append(post.candidate.stem)
        return PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:1")

    def close(self):
        self.closed = True


@pytest.fixture
def settings() -> Settings:
    settings = load_settings()
    ensure_data_dirs(settings)
    return settings


def _post(settings: Settings) -> None:
    incoming = settings.incoming_dir(Platform.LINKEDIN)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text("Launch day", encoding="utf-8")


def test_a_publisher_that_records_references_for_an_adapter_that_does_not_is_refused(settings, monkeypatch):
    fake = FakePublisher(records_references=True)
    monkeypatch.setattr(linkedin_ops, "_real_publisher", lambda settings: fake)
    _post(settings)

    report = run_once(settings, PublishMode.REAL, interval_s=0)

    assert report.exit_code == 1 and report.refusal.kind == "reference_mismatch"
    assert "disagree about recording a reference" in report.refusal.text
    assert fake.published == [] and fake.closed is True


def test_an_adapter_that_records_references_with_a_publisher_that_does_not_is_refused(settings, monkeypatch):
    fake = FakePublisher()  # No records_references attribute: it never records one.
    monkeypatch.setattr(linkedin_ops, "_real_publisher", lambda settings: fake)
    monkeypatch.setattr(linkedin_ops.LinkedInAdapter, "records_references", True)
    _post(settings)

    report = run_once(settings, PublishMode.REAL, interval_s=0)

    assert report.exit_code == 1 and report.refusal.kind == "reference_mismatch"
    assert fake.published == [] and fake.closed is True


def test_agreeing_flags_publish_as_before(settings, monkeypatch):
    fake = FakePublisher(records_references=False)
    monkeypatch.setattr(linkedin_ops, "_real_publisher", lambda settings: fake)
    _post(settings)

    report = run_once(settings, PublishMode.REAL, interval_s=0)

    assert report.refusal is None and fake.published == ["launch"]
