"""A safe development publisher that never sends content to a platform."""

from social_media_auto_poster.models import PublishResult

SIMULATED_MESSAGE = "Simulated only; no content was sent to a platform."


class Simulator:
    """Return a simulated result without inspecting or transmitting the post."""

    def publish(self, post: object, /) -> PublishResult:
        del post
        return PublishResult(simulated=True, message=SIMULATED_MESSAGE)
