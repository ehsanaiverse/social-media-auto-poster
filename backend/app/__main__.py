"""Start the local API: ``python -m app``, from the repository root.

Run it from the repository root so it uses the same data folder, job database,
and run lock as the CLI. It listens on 127.0.0.1 only.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8000)
