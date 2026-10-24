"""A safe development publisher that never sends content to a platform."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimulatedPublishResult:
    """Explicitly identifies a simulated result, not a confirmed publication."""

    simulated: bool = True
    message: str = "Simulated only; no content was sent to a platform."


class Simulator:
    """Return a simulation marker without inspecting or transmitting the post."""

    def publish(self, post: object, /) -> SimulatedPublishResult:
        del post
        return SimulatedPublishResult()
