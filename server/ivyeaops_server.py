"""PyInstaller-friendly IvyeaOps backend launcher."""
from __future__ import annotations

import uvicorn

from app.core.config import settings
from app.main import app


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
