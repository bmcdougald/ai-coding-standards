from __future__ import annotations

import subprocess
from pathlib import Path

COMPOSE_FILES = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)


def main() -> int:
    if not any(Path(name).exists() for name in COMPOSE_FILES):
        print("No Docker Compose file found; skipping Compose validation.")
        return 0

    result = subprocess.run(["docker", "compose", "config"], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
