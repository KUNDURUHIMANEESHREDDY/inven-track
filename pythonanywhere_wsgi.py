"""WSGI entrypoint for free PythonAnywhere hosting (persistent SQLite)."""
import os
from pathlib import Path

from a2wsgi import ASGIMiddleware
from backend.app import app, init_db

# ASGI lifespan is not triggered under the WSGI adapter.
init_db()
application = ASGIMiddleware(app)
