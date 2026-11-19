"""Instagram's adapter (D48): real publishing is available, chosen only by --real or Instagram's own phrase."""

import pytest

from social_media_auto_poster.config import load_settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations.common import RealPublishingUnavailable
from social_media_auto_poster.operations.platforms import ADAPTERS, adapter_for, confirmation_phrase


def test_both_platforms_may_publish_for_real():
    assert linkedin_ops.ADAPTER.real_available is True
    assert instagram_ops.ADAPTER.real_available is True


def test_instagram_is_registered_with_its_own_phrase():
    assert ADAPTERS[Platform.INSTAGRAM] is instagram_ops.ADAPTER
    assert confirmation_phrase(Platform.INSTAGRAM) == "publish to Instagram"
    assert confirmation_phrase(Platform.LINKEDIN) == "publish to LinkedIn"
    assert adapter_for(Platform.INSTAGRAM).records_references is True


def test_without_credentials_no_real_publisher_is_built():
    with pytest.raises(RealPublishingUnavailable, match="INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set"):
        instagram_ops.ADAPTER.real_publisher(load_settings())


def test_the_user_id_must_be_numeric_and_the_token_is_never_shown(monkeypatch):
    monkeypatch.setenv(instagram_ops.TOKEN_VAR, "IG-REAL-LOOKING-TOKEN")
    monkeypatch.setenv(instagram_ops.USER_ID_VAR, "me")
    with pytest.raises(RealPublishingUnavailable, match="numeric ID") as refused:
        instagram_ops._real_publisher(load_settings())
    assert "IG-REAL-LOOKING-TOKEN" not in str(refused.value)


def test_instagram_schedules_get_no_token_warnings():
    assert instagram_ops.ADAPTER.schedule_warnings(load_settings(), None, None) == ()
