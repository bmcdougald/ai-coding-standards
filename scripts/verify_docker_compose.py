from __future__ import annotations

import subprocess
import sys
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

    result = subprocess.run(  # noqa: S603 -- selected interpreter and fixed local runner argv
        [sys.executable, str(Path(__file__).resolve().parents[1] / ".quality/run.py"), "docker"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
