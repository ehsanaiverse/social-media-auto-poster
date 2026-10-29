"""Start the local API on 127.0.0.1:8000. Run from ``backend/``: ``python -m app``."""

import uvicorn

HOST = "127.0.0.1"  # Local only; the API is never reachable from the network.
PORT = 8000


def main() -> None:
    uvicorn.run("app.main:create_app", factory=True, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
