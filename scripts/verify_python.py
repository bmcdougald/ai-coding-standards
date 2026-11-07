from __future__ import annotations

import subprocess
import sys


def run(*command: str) -> None:
    print(f"\n$ {' '.join(command)}")
    subprocess.run(  # noqa: S603 -- selected Python interpreter and fixed module argv
        command, check=True
    )


def main() -> int:
    try:
        run(sys.executable, "-m", "ruff", "format", "--check", ".")
        run(sys.executable, "-m", "ruff", "check", ".")
        run(sys.executable, "-m", "pyright", "--pythonpath", sys.executable)
        run(sys.executable, "-m", "pytest")
    except subprocess.CalledProcessError as error:
        return error.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
