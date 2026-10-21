"""Entry point: run the FastAPI server.

Usage:
    python main.py
    # or
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn

from app.main import app, create_app
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)
