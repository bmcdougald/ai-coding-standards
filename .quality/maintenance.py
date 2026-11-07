from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def checked_path(root: Path, name: str) -> Path:
    path = root / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(root)
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError("Unsafe project path: " + name)
    return path


def application_nodes(packages: dict[str, Any], owned: dict[str, Any]) -> set[str]:
    """Walk application roots, retaining shared transitive packages even when also used by tools."""
    roots = [name for name in packages if "node_modules" not in Path(name).parts]
    pending: list[tuple[str, str, bool]] = []
    reachable: set[str] = set()

    def edges(location: str, fields: tuple[str, ...]) -> None:
        package = packages[location]
        owner = owned.get((Path(location) / "package.json").as_posix(), {})
        for field in fields:
            for name in package.get(field, {}):
                if location in roots and name in owner.get(field, {}):
                    continue
                pending.append(
                    (
                        location,
                        name,
                        field == "optionalDependencies"
                        or (
                            field == "peerDependencies"
                            and package.get("peerDependenciesMeta", {})
                            .get(name, {})
                            .get("optional", False)
                        ),
                    )
                )

    for location in roots:
        edges(
            location,
            ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"),
        )
    while pending:
        location, name, optional = pending.pop()
        directory = Path(location)
        candidates = []
        while True:
            candidates.append((directory / "node_modules" / name).as_posix())
            if directory == Path("."):
                break
            directory = directory.parent
        resolved = next((candidate for candidate in candidates if candidate in packages), None)
        if resolved is None:
            if optional:
                continue
            raise ValueError(
                "Lockfile dependency cannot be resolved: " + name + " from " + location
            )
        if packages[resolved].get("link"):
            resolved = packages[resolved]["resolved"]
            if resolved not in packages:
                raise ValueError("Missing workspace link target: " + resolved)
        if resolved not in reachable:
            reachable.add(resolved)
            edges(resolved, ("dependencies", "optionalDependencies", "peerDependencies"))
    return reachable


def audit_node(root: Path, config: dict[str, Any], *, offline: bool = False) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise FileNotFoundError("npm is required for application dependency audits")
    baseline = json.loads(checked_path(root, ".quality/baseline.json").read_text())
    owners = baseline["node"]
    scanned: set[Path] = set()
    covered: set[Path] = set()
    failures = []
    for project in config.get("node_projects", []):
        directory = checked_path(root, project)
        if directory in covered:
            continue
        locks = [directory / name for name in ("npm-shrinkwrap.json", "package-lock.json")]
        lock = next((path for path in locks if path.exists()), None)
        if lock is None:
            if (directory / "package.json").exists():
                raise FileNotFoundError("Dependency audit needs an npm lock: " + str(directory))
            continue
        lock = checked_path(root, lock.relative_to(root).as_posix())
        if lock in scanned:
            continue
        scanned.add(lock)
        packages = json.loads(lock.read_text()).get("packages")
        if not isinstance(packages, dict):
            raise ValueError("npm lockfile v2/v3 with packages is required: " + str(lock))
        covered.update(
            directory / name for name in packages if "node_modules" not in Path(name).parts
        )
        local_owners = {
            Path(name).relative_to(directory.relative_to(root)).as_posix(): values
            for name, values in owners.items()
            if Path(name).is_relative_to(directory.relative_to(root))
        }
        for location in packages:
            if "node_modules" in Path(location).parts:
                continue
            name = (directory.relative_to(root) / location / "package.json").as_posix()
            manifest = json.loads(checked_path(root, name).read_text())
            locked = packages[location]
            if any(
                manifest.get(field) != locked.get(field)
                for field in (
                    "dependencies",
                    "devDependencies",
                    "optionalDependencies",
                    "peerDependencies",
                    "peerDependenciesMeta",
                    "workspaces",
                )
            ):
                raise ValueError("Manifest/lock drift must be resolved before auditing: " + name)
        for name, fields in local_owners.items():
            manifest = json.loads(
                checked_path(root, (directory.relative_to(root) / name).as_posix()).read_text()
            )
            location = Path(name).parent.as_posix()
            locked = packages.get("" if location == "." else location)
            if not isinstance(locked, dict) or any(
                manifest.get(field) != locked.get(field)
                for field in (
                    "dependencies",
                    "devDependencies",
                    "optionalDependencies",
                    "peerDependencies",
                    "peerDependenciesMeta",
                    "workspaces",
                )
            ):
                raise ValueError("Manifest/lock drift must be resolved before auditing: " + name)
            for field, values in fields.items():
                for tool, expected in values.items():
                    if manifest.get(field, {}).get(tool) != expected:
                        raise ValueError(
                            "Tooling ownership drift must be resolved before auditing: " + tool
                        )
        reachable = application_nodes(packages, local_owners)
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        cache = checked_path(root, ".tools/cache/npm-audits/" + digest + ".json")
        if offline:
            if not cache.exists():
                raise ValueError("Offline npm audit needs a cached result for the current lock")
            record = json.loads(cache.read_text())
            age = datetime.datetime.now(datetime.UTC).timestamp() - record["timestamp"]
            if age > 86400 or age < 0:
                raise ValueError("Offline npm audit cache is stale (maximum age: 24 hours)")
            report = record["report"]
            print("OFFLINE npm audit: cached advisory results, not a current registry check.")
        else:
            result = subprocess.run(  # noqa: S603 -- resolved npm, fixed audit argv; no shell
                [npm, "audit", "--json", "--ignore-scripts"],
                cwd=directory,
                text=True,
                capture_output=True,
                check=False,
            )
            report = json.loads(result.stdout)
            if (
                result.returncode not in (0, 1)
                or "error" in report
                or "vulnerabilities" not in report
            ):
                raise ValueError("npm advisory audit failed: " + result.stdout + result.stderr)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(
                    {"timestamp": datetime.datetime.now(datetime.UTC).timestamp(), "report": report}
                )
                + "\n"
            )
        for name, finding in report["vulnerabilities"].items():
            nodes = finding.get("nodes")
            if not isinstance(nodes, list):
                raise ValueError("npm audit finding has no dependency locations: " + name)
            if reachable.intersection(nodes):
                print(json.dumps({"application_dependency": name, **finding}, indent=2))
                if finding["severity"].upper() in config["severity"]:
                    failures.append(name)
        print(
            "Application npm audit includes development dependencies; suite-only nodes excluded: "
            + str(directory)
        )
    if failures:
        raise ValueError(
            "Application dependency vulnerabilities: " + ", ".join(sorted(set(failures)))
        )


def freshness(root: Path) -> dict[str, Any]:
    paths = {
        "last_scan": ".tools/cache/scan-receipt.json",
        "trivy_database": ".tools/cache/trivy/db/metadata.json",
    }
    result: dict[str, Any] = {}
    for name, relative in paths.items():
        path = checked_path(root, relative)
        result[name] = json.loads(path.read_text()) if path.exists() else {"status": "missing"}
    result["limitations"] = (
        "Pinned executables do not pin registry rules or advisory databases. "
        "A scan receipt records completion, not immutable rule content."
    )
    return result


def scan_receipt(root: Path, config: dict[str, Any], offline: bool) -> None:
    rules = config["semgrep_config"]
    local = Path(rules) if Path(rules).is_absolute() else root / rules
    path = checked_path(root, ".tools/cache/scan-receipt.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "completed_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "offline": offline,
                "semgrep_config": rules,
                "local_rules_sha256": hashlib.sha256(local.read_bytes()).hexdigest()
                if local.is_file()
                else None,
            },
            indent=2,
        )
        + "\n"
    )


def doctor(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    python = checked_path(root, ".tools/venv/bin") / "python"
    # Virtualenv's interpreter is normally a symlink; checking its parent is sufficient here.
    versions: dict[str, str] = {}
    if python.exists():
        result = subprocess.run(  # noqa: S603 -- project interpreter, fixed metadata query
            [
                str(python),
                "-c",
                "import importlib.metadata,json; print(json.dumps("
                "{d.metadata['Name'].lower().replace('_','-'):d.version "
                "for d in importlib.metadata.distributions()}))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        versions = json.loads(result.stdout)
    else:
        issues.append("Tooling environment is missing; run python3 .quality/install.py")
    baseline = json.loads(checked_path(root, ".quality/baseline.json").read_text())
    for name, expected in baseline["python"].items():
        if versions.get(name) != expected:
            issues.append(
                f"Python tool {name}: required {expected}, installed {versions.get(name)}"
            )
    for name, digest in baseline["runtime"].items():
        path = checked_path(root, name)
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            issues.append("Runtime drift: " + name)
    for name, fields in baseline["node"].items():
        package = json.loads(checked_path(root, name).read_text())
        directory = (root / name).parent
        candidates = [directory, *directory.parents]
        lock_path = next(
            (
                base / filename
                for base in candidates
                if base.is_relative_to(root)
                for filename in ("npm-shrinkwrap.json", "package-lock.json")
                if (base / filename).exists()
            ),
            None,
        )
        if lock_path is None:
            issues.append("npm lock missing: " + name)
        else:
            lock_packages = json.loads(lock_path.read_text()).get("packages", {})
            locked = lock_packages.get(
                directory.relative_to(lock_path.parent).as_posix()
                if directory != lock_path.parent
                else "",
                {},
            )
            if any(
                package.get(field) != locked.get(field)
                for field in (
                    "dependencies",
                    "devDependencies",
                    "optionalDependencies",
                    "peerDependencies",
                    "peerDependenciesMeta",
                    "workspaces",
                )
            ):
                issues.append("npm lock drift: " + name)
        for field, tools in fields.items():
            for tool, expected in tools.items():
                if package.get(field, {}).get(tool) != expected:
                    issues.append("Dependency ownership drift: " + name + ": " + tool)
                if field != "overrides":
                    directory = (root / name).parent
                    candidates = [directory, *directory.parents]
                    installed = next(
                        (
                            base / "node_modules" / tool / "package.json"
                            for base in candidates
                            if base.is_relative_to(root)
                            and (base / "node_modules" / tool / "package.json").exists()
                        ),
                        None,
                    )
                    actual = json.loads(installed.read_text())["version"] if installed else None
                    if actual != expected:
                        issues.append(f"Node tool {tool}: required {expected}, installed {actual}")
    for name, spec in config["binaries"].items():
        binary = checked_path(root, ".tools/bin/" + name)
        receipt = binary.with_suffix(".sha256")
        if not binary.exists() or not receipt.exists():
            issues.append("Binary missing: " + name)
        else:
            parts = receipt.read_text().split()
            if (
                len(parts) != 3
                or parts[0] != spec["version"]
                or parts[2] != hashlib.sha256(binary.read_bytes()).hexdigest()
            ):
                issues.append("Binary version/checksum drift: " + name)
    lock = checked_path(root, ".quality/locked/requirements.txt")
    if not lock.exists():
        issues.append("Python tooling lock missing")
    else:
        lines = set(lock.read_text().lower().replace("_", "-").splitlines())
        for name, expected in baseline["python"].items():
            if name + "==" + expected not in lines:
                issues.append("Python lock drift: " + name)
        for pin in checked_path(root, ".quality/requirements.in").read_text().splitlines():
            if (
                pin.strip()
                and not pin.startswith("#")
                and pin.lower().replace("_", "-") not in lines
            ):
                issues.append("Python direct pin/lock drift: " + pin)
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("Git is required")
    hooks = subprocess.run(  # noqa: S603 -- resolved Git, fixed query
        [git, "rev-parse", "--git-path", "hooks"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    directory = Path(hooks)
    directory = directory if directory.is_absolute() else root / directory
    for stage in ("pre-commit", "pre-push"):
        path = directory / stage
        if not path.is_file() or not os.access(path, os.X_OK):
            issues.append("Git hook missing or non-executable: " + stage)
        elif config["hook_manager"] == "pre-commit":
            text = path.read_text()
            if str(root / ".tools/venv/bin/python") not in text:
                issues.append("pre-commit dispatcher is not wired to isolated tools: " + stage)
        elif config["hook_manager"] == "husky":
            hook = root / ".husky" / stage
            if not hook.exists() or ".quality/run.py" not in hook.read_text():
                issues.append("Husky suite invocation missing: " + stage)
        elif ".quality/run.py" not in path.read_text():
            issues.append("Suite invocation missing from existing hook: " + stage)
    if checked_path(root, ".tools/update-pending.json").exists():
        issues.append("Interrupted update: run bootstrap.sh --recover-tooling")
    docker = shutil.which("docker")
    docker_status = "SKIPPED: missing Docker CLI"
    if docker:
        compose = subprocess.run(  # noqa: S603 -- resolved Docker, fixed probe
            [docker, "compose", "version"],
            capture_output=True,
            check=False,
        )
        daemon = subprocess.run(  # noqa: S603 -- resolved Docker, fixed probe
            [docker, "info"],
            capture_output=True,
            check=False,
        )
        docker_status = {
            "compose": "available" if compose.returncode == 0 else "SKIPPED: missing Compose",
            "daemon": "available" if daemon.returncode == 0 else "SKIPPED: inaccessible daemon",
        }
    return {
        "ok": not issues,
        "issues": issues,
        "python_versions": {name: versions.get(name) for name in baseline["python"]},
        "docker": docker_status,
        "freshness": freshness(root),
    }
