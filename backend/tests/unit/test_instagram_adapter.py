"""Instagram's adapter (step C): real Instagram is refused even when called directly, and Instagram stays unregistered."""

import re

import pytest

from social_media_auto_poster.config import load_settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations.common import RealPublishingUnavailable
from social_media_auto_poster.operations.platforms import ADAPTERS, adapter_for

D49_REFUSAL = re.escape("forbidden until D48 is accepted (D49)")


def test_only_linkedin_may_publish_for_real():
    assert linkedin_ops.ADAPTER.real_available is True
    assert instagram_ops.ADAPTER.real_available is False


def test_instagram_is_still_not_registered():
    assert ADAPTERS[Platform.INSTAGRAM] is instagram_ops.ADAPTER
    assert adapter_for(Platform.INSTAGRAM).real_available is False


def test_building_a_real_instagram_publisher_is_refused():
    with pytest.raises(RealPublishingUnavailable, match=D49_REFUSAL):
        instagram_ops.ADAPTER.real_publisher(load_settings())


def test_the_refusal_comes_before_any_setting_or_token_is_read(monkeypatch, tmp_path):
    # Even with a token and a user ID available, the refusal is the D49 one, not a credentials problem.
    monkeypatch.setenv(instagram_ops.TOKEN_VAR, "IG-REAL-LOOKING-TOKEN")
    monkeypatch.setenv(instagram_ops.USER_ID_VAR, "17841400000000001")
    with pytest.raises(RealPublishingUnavailable, match=D49_REFUSAL) as refused:
        instagram_ops._real_publisher(load_settings())
    assert "IG-REAL-LOOKING-TOKEN" not in str(refused.value)


def test_instagram_schedules_get_no_token_warnings():
    assert instagram_ops.ADAPTER.schedule_warnings(load_settings(), None, None) == ()
