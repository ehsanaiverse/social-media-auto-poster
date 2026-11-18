"""The operations the command line and the local API both call: scan, run, resolve, schedules, LinkedIn status.

They return data; the CLI (``main.py``) prints it and the API (``backend/app``) serializes it, so both share
one implementation. Lower-level pieces they use live in ``services/``, ``storage/``, ``ingestion/`` and ``graph/``.
"""
