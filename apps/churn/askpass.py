#!/usr/bin/env python3
"""GIT_ASKPASS helper (ADR 0004). git runs this with the prompt as argv[1] and reads one line of
stdout. The token reaches it only through the process environment -- never argv, never a file,
never a URL -- so it is the only thing besides gitcmd's env dict that ever sees it."""

import os
import sys


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    key = "PR_RADAR_GIT_USERNAME" if prompt.lower().startswith("username") else "PR_RADAR_GIT_TOKEN"
    sys.stdout.write(os.environ.get(key, ""))


if __name__ == "__main__":
    main()
