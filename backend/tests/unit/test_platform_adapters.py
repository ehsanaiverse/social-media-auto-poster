"""The platform registry (operations/platforms.py): lookup, and that it reaches LinkedIn's module-level functions."""

from datetime import UTC, datetime

import pytest

from social_media_auto_poster.config import load_settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations import common
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations.platforms import ADAPTERS, UnsupportedPlatform, adapter_for


def test_only_linkedin_is_registered():
    assert set(ADAPTERS) == {Platform.LINKEDIN}
    assert adapter_for(Platform.LINKEDIN) is linkedin_ops.ADAPTER
    assert adapter_for(Platform.LINKEDIN).platform is Platform.LINKEDIN


@pytest.mark.parametrize("platform", [Platform.INSTAGRAM, Platform.FACEBOOK])
def test_platforms_without_an_integration_have_no_adapter(platform):
    with pytest.raises(UnsupportedPlatform, match=f"{platform} is not supported"):
        adapter_for(platform)


def test_the_real_publisher_comes_from_the_module_level_function(monkeypatch):
    # The seam every real-mode test uses: replacing linkedin_ops._real_publisher must reach the registry.
    built = object()
    calls = []
    monkeypatch.setattr(linkedin_ops, "_real_publisher", lambda settings: calls.append(settings) or built)
    settings = load_settings()

    assert adapter_for(Platform.LINKEDIN).real_publisher(settings) is built
    assert calls == [settings]


def test_without_a_token_the_real_publisher_is_refused_with_the_generic_error():
    # conftest removes the LinkedIn settings, so no test can see a real token.
    with pytest.raises(common.RealPublishingUnavailable):
        adapter_for(Platform.LINKEDIN).real_publisher(load_settings())
    assert linkedin_ops.RealPublishingUnavailable is common.RealPublishingUnavailable


def test_schedule_warnings_are_linkedins_token_warnings(monkeypatch):
    calls = []
    monkeypatch.setattr(linkedin_ops, "_token_warnings", lambda *args: calls.append(args) or ("warned",))
    settings = load_settings()
    publish_at = now = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)

    assert adapter_for(Platform.LINKEDIN).schedule_warnings(settings, publish_at, now) == ("warned",)
    assert calls == [(settings, publish_at, now)]


def test_without_a_token_a_real_schedule_gets_the_missing_token_warning():
    now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    [warning] = adapter_for(Platform.LINKEDIN).schedule_warnings(load_settings(), now, now)

    assert "LINKEDIN_ACCESS_TOKEN" in warning
    assert warning.endswith("so this post will not be published until it is set.")
