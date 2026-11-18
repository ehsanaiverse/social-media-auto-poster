"""One module per group of endpoints. ``app/main.py`` includes them all."""

from . import jobs, linkedin, posts, run, scan

__all__ = ["jobs", "linkedin", "posts", "run", "scan"]
