"""LinkedIn's uncertain outcome is raised without the httpx exception chained to it.

The workflow logs an uncertain publish with ``logger.exception``, which prints the whole traceback, including
every chained exception and its message. httpx's own message is not ours to log: it can carry a URL or other
details of the request. So the traceback must show only our message, never httpx's.
"""

import logging

import httpx
import pytest

from linkedin_fake import TOKEN, FakeLinkedIn
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient
from social_media_auto_poster.platforms.linkedin.publisher import LinkedInPublisher, PublishOutcomeUnknown
from test_linkedin_publisher import make_post

HTTPX_TEXT = "timed out talking to https://api.linkedin.com/rest/posts?trace=httpx-internal-detail"


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError])
def test_an_uncertain_outcome_logged_with_its_traceback_shows_no_httpx_text(error, caplog):
    fake = FakeLinkedIn(post=error(HTTPX_TEXT))
    caplog.set_level(logging.DEBUG)

    with LinkedInClient(TOKEN, transport=httpx.MockTransport(fake)) as client:
        try:
            LinkedInPublisher(client, sleep=lambda seconds: None).publish(make_post())
        except PublishOutcomeUnknown as uncertain:
            message = str(uncertain)
            logging.getLogger("test").exception("Publish outcome unknown; marking NEEDS_REVIEW, not retrying")
        else:
            pytest.fail("expected an uncertain outcome")

    # Our message is unchanged (LinkedIn output byte for byte) and is in the log ...
    assert message == (f"the connection failed after the post was sent ({error.__name__}); "
                       "check LinkedIn before re-posting")
    assert message in caplog.text
    # ... but nothing of httpx's own exception: not its text, not the URL in it, not even a second traceback.
    assert HTTPX_TEXT not in caplog.text
    assert "httpx-internal-detail" not in caplog.text and "api.linkedin.com" not in caplog.text
    assert "The above exception was the direct cause" not in caplog.text
    assert "During handling of the above exception" not in caplog.text
