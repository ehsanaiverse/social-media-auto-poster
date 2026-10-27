"""ASGI entry point: ``python -m app`` (run from the ``backend/`` directory)."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
