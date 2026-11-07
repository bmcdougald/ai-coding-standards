from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from jsonc import parse_jsonc

ROOT = Path(__file__).resolve().parents[1]


def workspace_members(directory: Path, projects: list[Path]) -> list[Path]:
    package = json.loads((directory / "package.json").read_text())
    patterns = package.get("workspaces", [])
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
        raise ValueError("npm workspaces must be an array of directory patterns")
    if any(any(character in pattern for character in "{}()") for pattern in patterns):
        raise ValueError(
            "Workspace brace/extglob patterns require manual integration; use *, ? or **"
        )

    def matches(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
        if not pattern:
            return not parts
        if pattern[0] == "**":
            return any(matches(parts[index:], pattern[1:]) for index in range(len(parts) + 1))
        return bool(
            parts and fnmatch.fnmatchcase(parts[0], pattern[0]) and matches(parts[1:], pattern[1:])
        )

    members: set[Path] = set()
    for pattern in patterns:
        exclude = pattern.startswith("!")
        segments = tuple(pattern.lstrip("!").removeprefix("./").rstrip("/").split("/"))
        found = {
            path
            for path in projects
            if path != directory
            and path.is_relative_to(directory)
            and matches(path.relative_to(directory).parts, segments)
        }
        members = members - found if exclude else members | found
    return sorted(members)


def npm_action(directory: Path, workspaces: list[Path] | None = None) -> str:
    lock_path = directory / "npm-shrinkwrap.json"
    if not lock_path.exists():
        lock_path = directory / "package-lock.json"
    if not lock_path.exists():
        return "install"
    package = json.loads((directory / "package.json").read_text())
    packages = json.loads(lock_path.read_text()).get("packages", {})
    locked = packages.get("")
    fields = (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
        "peerDependenciesMeta",
        "workspaces",
    )
    mismatch = not isinstance(locked, dict) or any(
        package.get(key) != locked.get(key) for key in fields
    )
    for path in workspaces or []:
        desired = json.loads((path / "package.json").read_text())
        member = packages.get(path.relative_to(directory).as_posix(), {})
        mismatch = mismatch or any(desired.get(key) != member.get(key) for key in fields)
    if mismatch:
        print(f"Updating npm lockfile to match amended package: {directory}", flush=True)
        return "install"
    return "ci"


def run(*command: str) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError("Required executable missing: " + command[0])
    print("$ " + " ".join(command), flush=True)
    env = os.environ.copy()
    env.update(
        {
            "PIP_CACHE_DIR": str(ROOT / ".tools/cache/pip"),
            "XDG_CACHE_HOME": str(ROOT / ".tools/cache"),
            "PRE_COMMIT_HOME": str(ROOT / ".tools/cache/pre-commit"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    subprocess.run(  # noqa: S603 -- resolved tool executable and separate argv; no shell
        [executable, *command[1:]], cwd=ROOT, env=env, check=True
    )


def install_binary(name: str, spec: dict[str, Any], system: str) -> None:
    asset, digest = spec["assets"][system]
    repositories = {
        "gitleaks": "gitleaks/gitleaks",
        "trivy": "aquasecurity/trivy",
        "hadolint": "hadolint/hadolint",
    }
    if spec["repository"] != repositories.get(name):
        raise ValueError("Invalid official release repository")
    if not re.fullmatch(r"\d+\.\d+\.\d+", spec["version"]):
        raise ValueError("Invalid release version")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", asset) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid release asset or SHA-256")
    destination = ROOT / ".tools/bin" / name
    receipt = destination.with_suffix(".sha256")
    if destination.exists() and receipt.exists():
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
        if receipt.read_text().strip() == f"{spec['version']} {digest} {actual}":
            return
    url = f"https://github.com/{spec['repository']}/releases/download/v{spec['version']}/{asset}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as temporary:
        archive = Path(temporary) / asset
        print("Downloading " + url, flush=True)
        # Only HTTPS is supported, including redirects to GitHub's release asset storage.
        opener = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.HTTPSHandler(),
            urllib.request.HTTPRedirectHandler(),
            urllib.request.HTTPDefaultErrorHandler(),
            urllib.request.HTTPErrorProcessor(),
        ):
            opener.add_handler(handler)
        with opener.open(url, timeout=120) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise ValueError("Checksum mismatch for " + asset)
        if asset.endswith(".tar.gz"):
            with tarfile.open(archive) as bundle:
                candidates = [m for m in bundle.getmembers() if m.name == name and m.isfile()]
                if len(candidates) != 1:
                    raise ValueError("Expected exactly one regular executable named " + name)
                content = bundle.extractfile(candidates[0])
                if content is None:
                    raise ValueError("Missing executable in " + asset)
                with content, (Path(temporary) / name).open("wb") as output:
                    shutil.copyfileobj(content, output)
                binary = Path(temporary) / name
        else:
            binary = archive
        binary.chmod(0o755)
        os.replace(binary, destination)
        receipt.write_text(
            spec["version"]
            + " "
            + digest
            + " "
            + hashlib.sha256(destination.read_bytes()).hexdigest()
            + "\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore project-local tools and hooks.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--update-tools", action="store_true", help="Resolve updated pins; keep hooks"
    )
    mode.add_argument("--restore-tools", action="store_true", help="Restore locks; keep hooks")
    args = parser.parse_args(argv)
    config = parse_jsonc((ROOT / ".quality/config.json").read_text())
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(platform.machine(), platform.machine())
    system = platform.system() + "-" + machine
    if any(system not in spec["assets"] for spec in config["binaries"].values()):
        raise ValueError(
            "Unsupported platform: " + system + "; supported: Linux/macOS x86_64/arm64"
        )
    if sys.version_info < (3, 11):  # noqa: UP036 -- explicit installer prerequisite diagnostic
        raise ValueError("Python 3.11+ is required")
    python = ROOT / ".tools/venv/bin/python"
    if not python.exists():
        run(sys.executable, "-m", "venv", str(ROOT / ".tools/venv"))
    # Freeze the resolved graph once; fresh clones restore it instead of resolving again.
    lock = ROOT / ".quality/locked/requirements.txt"
    if lock.exists() and not args.update_tools:
        locked = {line.lower().replace("_", "-") for line in lock.read_text().splitlines()}
        for line in (ROOT / ".quality/requirements.in").read_text().splitlines():
            if (
                line.strip()
                and not line.startswith("#")
                and line.lower().replace("_", "-") not in locked
            ):
                raise ValueError(
                    "Python direct pins and lock disagree; review and regenerate the lock"
                )
    run(
        str(python),
        "-m",
        "pip",
        "install",
        *(["--upgrade", "--upgrade-strategy", "only-if-needed"] if args.update_tools else []),
        "-r",
        str(lock) if lock.exists() and not args.update_tools else ".quality/requirements.in",
    )
    if not lock.exists() or args.update_tools:
        result = subprocess.run(  # noqa: S603 -- project interpreter and fixed pip arguments
            [str(python), "-m", "pip", "freeze", "--all"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(result.stdout)
    run(str(python), "-m", "pip", "check")
    if (ROOT / "package.json").exists():
        projects = [
            ROOT / project
            for project in config.get("node_projects", ["."])
            if (ROOT / project / "package.json").exists()
        ]
        installed: set[Path] = set()
        owned = (
            {
                ROOT / Path(name).parent
                for name in json.loads((ROOT / ".quality/baseline.json").read_text())["node"]
            }
            if args.update_tools
            else set(projects)
        )
        for directory in projects:
            if directory in installed:
                continue
            if (directory / "package.json").exists():
                workspaces = workspace_members(directory, projects)
                if args.update_tools and not owned.intersection({directory, *workspaces}):
                    continue
                run(
                    "npm",
                    "--prefix",
                    str(directory),
                    "install" if args.update_tools else npm_action(directory, workspaces),
                    *(["--ignore-scripts"] if args.update_tools or args.restore_tools else []),
                    "--cache",
                    str(ROOT / ".tools/cache/npm"),
                )
                installed.update([directory, *workspaces])
        if not (args.update_tools or args.restore_tools):
            run(
                str(ROOT / "node_modules/.bin/prettier"),
                "--write",
                ".quality/config.json",
                ".quality/extensions.schema.json",
                ".quality/schemas.json",
                ".vscode/settings.json",
                ".vscode/extensions.json",
                ".vscode/tasks.json",
                *([".quality/ci.json"] if (ROOT / ".quality/ci.json").exists() else []),
            )
    for name, spec in config["binaries"].items():
        install_binary(name, spec, system)
    if args.update_tools or args.restore_tools:
        print("Installed project-local tools; existing hooks and settings unchanged.")
        return 0
    manager = config.get(
        "hook_manager", "pre-commit" if "python" in config["languages"] else "husky"
    )
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("Git is required")
    if config.get("tooling_policy") == "replace":
        unset = subprocess.run(  # noqa: S603 -- resolved Git executable, fixed local config key
            [git, "-C", str(ROOT), "config", "--local", "--unset-all", "core.hooksPath"],
            check=False,
        )
        if unset.returncode not in {0, 5}:
            raise subprocess.CalledProcessError(unset.returncode, unset.args)
    if manager == "pre-commit":
        run(str(ROOT / ".tools/venv/bin/pre-commit"), "validate-config")
        run(
            str(ROOT / ".tools/venv/bin/pre-commit"),
            "install",
            "--hook-type",
            "pre-commit",
            "--hook-type",
            "pre-push",
            *(["--overwrite"] if config.get("tooling_policy") == "replace" else []),
        )
    elif manager == "husky":
        run(str(ROOT / "node_modules/.bin/husky"))
    elif manager != "existing":
        raise ValueError("Unsupported hook manager: " + manager)
    if manager in {"pre-commit", "husky"}:
        result = subprocess.run(  # noqa: S603 -- resolved Git executable and fixed argv
            [git, "-C", str(ROOT), "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            check=True,
        )
        directory = Path(result.stdout.strip())
        directory = directory if directory.is_absolute() else ROOT / directory
        for stage in ("pre-commit", "pre-push"):
            hook = directory / stage
            if not hook.resolve().is_relative_to(ROOT) or hook.is_symlink():
                raise ValueError("Refusing to update hooks outside the repository")
            text = hook.read_text()
            marker = "# project-local-tool-path"
            if marker not in text:
                first, separator, rest = text.partition("\n")
                hook.write_text(
                    first
                    + separator
                    + marker
                    + '\n. "$(git rev-parse --show-toplevel)/.quality/env.sh" || exit $?\n'
                    + rest
                )
    print("Installed project-local tools and Git hooks.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Tool installation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
