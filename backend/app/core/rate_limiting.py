"""
Central rate-limiter instance.
Imported by both main.py (to attach to the app) and route modules (to apply decorators).
Defining it here avoids circular imports between main.py and route files.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
