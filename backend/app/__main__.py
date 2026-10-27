"""Start the local API: ``python -m app``.

It always uses the project's own data folder, job database, and run lock (the
same ones as the CLI), wherever it is started. It listens on 127.0.0.1 only.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8000)
