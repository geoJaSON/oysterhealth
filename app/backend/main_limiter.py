"""Slowapi limiter — extracted to its own module so route files can import the
shared instance without circular imports against `main.py`.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from settings import settings

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.REDIS_URL)
