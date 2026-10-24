"""Platform-neutral contract implemented by platform-specific publishers."""

from typing import Protocol, TypeVar

PostT = TypeVar("PostT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


class Publisher(Protocol[PostT, ResultT]):
    """Publish a post and return an implementation-defined outcome."""

    def publish(self, post: PostT, /) -> ResultT:
        """Submit a post; platform-specific capabilities remain implementation-defined."""
        ...
