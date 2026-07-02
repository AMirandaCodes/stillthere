"""
Central rate-limiter instance.
Imported by both main.py (to attach to the app) and route modules (to apply decorators).
Defining it here avoids circular imports between main.py and route files.
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# RATE_LIMITS_ENABLED=false disables rate limiting (set by conftest.py before app imports).
_enabled = os.getenv("RATE_LIMITS_ENABLED", "true").lower() != "false"

limiter = Limiter(key_func=get_remote_address, enabled=_enabled)
