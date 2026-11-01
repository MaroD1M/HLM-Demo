"""WSGI entry point used by gunicorn."""

from app import create_app

application = create_app()
