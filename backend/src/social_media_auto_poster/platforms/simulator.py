"""A safe development publisher that never sends content to a platform."""

from social_media_auto_poster.models import PublishResult


class Simulator:
    """Return a simulated result without inspecting or transmitting the post."""

    def publish(self, post: object, /) -> PublishResult:
        del post
        return PublishResult(success=True, simulated=True)
