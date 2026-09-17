"""huey is configured entirely through the HUEY setting in config/settings/*.py
(huey.contrib.djhuey reads it); this module exists as the named seam the
project's docs and PROFILE.md point at for anything that needs the instance
directly instead of the @db_task decorator."""

from huey.contrib.djhuey import HUEY as huey  # noqa: F401
