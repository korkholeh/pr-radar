"""Named e2e personas. Usernames and env var names mirror
apps/accounts/management/commands/seed_e2e.py — the only place that creates these accounts."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    username: str
    password: str


def _persona(username: str, password_env: str, default_password: str) -> Persona:
    return Persona(username=username, password=os.environ.get(password_env, default_password))


USER_ADMIN = _persona("e2e-admin", "E2E_ADMIN_PASSWORD", "admin-password-change-me")
USER_LEAD = _persona("e2e-lead", "E2E_LEAD_PASSWORD", "lead-password-change-me")
