"""
Compatibility shim for Balatro mode to preserve existing callers.
Routes session lifecycle safely to skills.balatro package.
"""
from core.logger import log_system
from skills.balatro import start_session, stop_session

def start_balatro(iris, avatar):
    """Shim entrypoint mapping to session start."""
    log_system("[Balatro Shim] Received start_balatro request, routing to new session manager.")
    start_session(iris, avatar)


def stop_balatro():
    """Shim entrypoint mapping to session stop."""
    log_system("[Balatro Shim] Received stop_balatro request, routing to new session manager.")
    stop_session()
