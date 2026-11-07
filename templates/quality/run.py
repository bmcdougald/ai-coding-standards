from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonc import parse_jsonc
from maintenance import audit_node, checked_path, doctor, freshness, scan_receipt

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".quality",
    ".venv",
    ".tools",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}
COMPOSE = {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}


def ci_result(name: str, status: str, detail: str = "") -> None:
    report = os.environ.get("QUALITY_CI_REPORT")
    if not report:
        return
    if report != ".tools/ci/checks.jsonl":
        raise ValueError("CI results must use the managed report path")
    path = checked_path(ROOT, report)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps({"name": name, "status": status, "detail": detail}) + "\n")


def source_files() -> list[Path]:
    files = []
    for directory, folders, names in os.walk(ROOT):
        folders[:] = [
            name
            for name in folders
            if name not in EXCLUDED and not (Path(directory) / name).is_symlink()
        ]
        files.extend(Path(directory) / name for name in names)
    return files


def run(*command: str, directory: Path = ROOT, environment: dict[str, str] | None = None) -> None:
    executable = command[0]
    resolved = shutil.which(executable)
    if resolved is None:
        ci_result(Path(executable).name, "FAILED", "Required tool is missing")
        raise FileNotFoundError(f"Required tool missing: {executable}; run .quality/install.py")
    print("$ " + " ".join(command), flush=True)
    env = os.environ.copy()
    env.update(
        {
            "SEMGREP_SEND_METRICS": "off",
            "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "SEMGREP_SETTINGS_FILE": str(ROOT / ".tools/cache/semgrep/settings.yml"),
            "XDG_CACHE_HOME": str(ROOT / ".tools/cache"),
            "PYRIGHT_PYTHON_CACHE_DIR": str(ROOT / ".tools/cache/pyright"),
            "PRE_COMMIT_HOME": str(ROOT / ".tools/cache/pre-commit"),
        }
    )
    env.update(environment or {})
    name = " ".join(
        command[2:4] if command[1:2] == ("-m",) else (Path(executable).name, *command[1:2])
    )
    try:
        subprocess.run(  # noqa: S603 -- resolved local executable and argv; never shell interpolation
            [resolved, *command[1:]], cwd=directory, env=env, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        ci_result(name, "FAILED", "See job log")
        raise
    ci_result(name, "PASSED", str(directory.relative_to(ROOT)))


def docker_checks(files: list[Path], docker: dict) -> None:
    compose = [path for path in files if path.name in COMPOSE]
    builds = docker["builds"]
    images = docker["images"]
    if not compose and not builds and not images:
        ci_result("Docker runtime checks", "SKIPPED", "No Compose files or build/image targets")
        print(
            "Docker runtime checks: not applicable "
            "(no Compose files or configured build/image targets)."
        )
        return
    executable = shutil.which("docker")
    if executable is None:
        ci_result("Docker runtime checks", "SKIPPED", "Docker CLI is unavailable")
        print(
            "SKIPPED Docker runtime checks: skipped due to missing local docker dependencies "
            "(Docker CLI)."
        )
        return
    # Compose validation does not need a running daemon; build/image operations do.
    probe = subprocess.run(  # noqa: S603 -- resolved Docker executable, fixed probe arguments
        [executable, "compose", "version"], cwd=ROOT, capture_output=True, check=False
    )
    if probe.returncode and compose:
        ci_result("Compose validation", "SKIPPED", "Compose v2 is unavailable")
        print(
            "SKIPPED Docker Compose validation: skipped due to missing local docker dependencies "
            "(Compose v2)."
        )
    elif compose:
        for path in compose:
            run(
                "docker",
                "compose",
                "--project-directory",
                str(path.parent),
                "-f",
                str(path),
                "config",
                "-q",
            )
    if not builds and not images:
        ci_result("Docker build/image scans", "SKIPPED", "No explicit build/image targets")
        print("Docker builds/image scans: not configured; require explicit image/build targets.")
        return
    daemon = subprocess.run(  # noqa: S603 -- resolved Docker executable, fixed probe arguments
        [executable, "info"], cwd=ROOT, capture_output=True, check=False
    )
    if daemon.returncode:
        ci_result("Docker build/image scans", "SKIPPED", "Docker daemon is unavailable")
        print(
            "SKIPPED Docker builds/image scans: skipped due to missing local docker dependencies "
            "(accessible Docker daemon; check service status and permissions)."
        )
        return
    for build in builds:
        run("docker", "build", "-f", build["dockerfile"], "-t", build["image"], build["context"])
        images = [*images, build["image"]]
    config = parse_jsonc((ROOT / ".quality/config.json").read_text())
    for image in dict.fromkeys(images):
        run(
            str(ROOT / ".tools/bin/trivy"),
            "image",
            "--cache-dir",
            str(ROOT / ".tools/cache/trivy"),
            "--exit-code",
            "1",
            "--severity",
            ",".join(config["severity"]),
            image,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the repository's local quality tools.")
    parser.add_argument(
        "action",
        choices=[
            "check",
            "format",
            "format-check",
            "lint",
            "types",
            "schemas",
            "security",
            "secrets",
            "sast",
            "dependencies",
            "filesystem",
            "docker",
            "commit",
            "push",
            "doctor",
            "status",
            "freshness",
        ],
    )
    parser.add_argument(
        "--offline", action="store_true", help="Use local rules and cached databases/audits"
    )
    args = parser.parse_args()
    if args.offline and args.action not in {"security", "push"}:
        parser.error("--offline requires security or push")
    config = parse_jsonc((ROOT / ".quality/config.json").read_text())
    if args.action in {"doctor", "status"}:
        report = doctor(ROOT, config)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    if args.action == "freshness":
        print(json.dumps(freshness(ROOT), indent=2))
        return 0
    languages = set(config["languages"])
    python = "python" in languages
    node = bool(languages - {"python"}) or config.get("hook_manager") == "husky"
    node_projects = [ROOT / project for project in config.get("node_projects", ["."])]
    files = source_files()
    executable = str(ROOT / ".tools/venv/bin/python")
    npm_bin = ROOT / "node_modules/.bin"
    tools = ROOT / ".tools/bin"
    scanner_configs = {
        kind: [ROOT / name for name in names]
        for kind, names in config.get("scanner_configs", {}).items()
    }
    action = args.action
    check = action in {"check", "push"}
    format_check = (
        action == "format-check"
        or check
        or (
            action == "commit"
            and (
                config.get("hook_manager") != "pre-commit"
                or os.environ.get("QUALITY_UPDATE_CHECK") == "1"
            )
        )
    )
    lint = action in {"lint", "commit"} or check
    formatting = action in {"format", "format-check", "commit"} or check
    if formatting:
        if node:
            for directory in node_projects:
                local = directory / "node_modules/.bin/prettier"
                exclusions = [
                    "!" + child.relative_to(directory).as_posix() + "/**"
                    for child in node_projects
                    if child != directory and child.is_relative_to(directory)
                ]
                run(
                    str(local if local.is_file() else npm_bin / "prettier"),
                    "--check" if format_check else "--write",
                    ".",
                    *exclusions,
                    directory=directory,
                )
        if python:
            run(executable, "-m", "ruff", "format", *(["--check"] if format_check else []), ".")
    if lint:
        if node:
            for directory in node_projects:
                local = directory / "node_modules/.bin/eslint"
                executable_path = local if local.is_file() else npm_bin / "eslint"
                children = [
                    path
                    for path in node_projects
                    if path != directory and path.is_relative_to(directory)
                ]
                exclusions = [
                    argument
                    for child in children
                    for argument in (
                        "--ignore-pattern",
                        child.relative_to(directory).as_posix() + "/**",
                    )
                ]
                package_path = directory / "package.json"
                package = json.loads(package_path.read_text()) if package_path.exists() else {}
                legacy = "eslintConfig" in package or any(directory.glob(".eslintrc*"))
                flat = any(directory.glob("eslint.config.*"))
                run(
                    str(executable_path),
                    ".",
                    *exclusions,
                    directory=directory,
                    environment={"ESLINT_USE_FLAT_CONFIG": "false"} if legacy and not flat else {},
                )
        if python:
            run(executable, "-m", "ruff", "check", ".")
        for path in files:
            if (
                path.name == "Dockerfile"
                or path.name.startswith("Dockerfile.")
                or path.suffix == ".dockerfile"
            ):
                configs = [
                    name
                    for name in scanner_configs.get("hadolint", [])
                    if path.is_relative_to(name.parent)
                ]
                nearest = max(configs, key=lambda name: len(name.parts)) if configs else None
                run(
                    str(tools / "hadolint"),
                    *(["--config", str(nearest)] if nearest else []),
                    str(path),
                )
    if action == "types" or check:
        if node:
            if any(
                path.suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
                and path.name != "eslint.config.mjs"
                for path in files
            ):
                for name in config.get("typescript_configs", ["tsconfig.json"]):
                    path = ROOT / name
                    if not path.exists():
                        raise FileNotFoundError("Configured TypeScript project missing: " + name)
                    directory = path.parent
                    local = directory / "node_modules/.bin/tsc"
                    run(
                        str(local if local.is_file() else npm_bin / "tsc"),
                        "--noEmit",
                        "--project",
                        str(path),
                        directory=directory,
                    )
            else:
                ci_result("TypeScript", "SKIPPED", "No application source files")
                print("TypeScript: not applicable (no application source files yet).")
        if python:
            for project in config.get("python_projects", ["."]):
                interpreters = [ROOT / project / ".venv/bin/python", ROOT / ".venv/bin/python"]
                application_python = next(
                    (path for path in interpreters if path.exists()),
                    Path(sys.base_prefix) / "bin/python3",
                )
                if not application_python.exists():
                    raise FileNotFoundError("Application/base Python interpreter is missing")
                run(
                    executable,
                    "-m",
                    "pyright",
                    "--pythonpath",
                    str(application_python),
                    "--project",
                    str(ROOT / project),
                )
    if action == "schemas" or check or action == "commit":
        schemas = parse_jsonc((ROOT / ".quality/schemas.json").read_text())
        for item in schemas["checks"]:
            with tempfile.TemporaryDirectory(dir=ROOT / ".tools") as temporary:
                inputs = []
                for index, name in enumerate(item["files"]):
                    if Path(name).suffix in {".json", ".jsonc"}:
                        normalized = Path(temporary) / f"{index}.json"
                        normalized.write_text(json.dumps(parse_jsonc((ROOT / name).read_text())))
                        inputs.append(str(normalized))
                    else:
                        inputs.append(name)
                print("Validating schemas: " + ", ".join(item["files"]), flush=True)
                run(
                    str(ROOT / ".tools/venv/bin/check-jsonschema"),
                    "--schemafile",
                    item["schema"],
                    *inputs,
                )
    if action in {"commit", "security", "push", "secrets"}:
        configs = scanner_configs.get("gitleaks", [])
        root_configs = [path for path in configs if path.parent == ROOT]
        run(
            str(tools / "gitleaks"),
            "git" if action == "commit" else "dir",
            *(["--pre-commit", "--staged"] if action == "commit" else []),
            "--redact",
            *(["--config", str(root_configs[0])] if root_configs else []),
            ".",
        )
    if action in {"security", "push", "dependencies"}:
        if args.offline and not Path(config["semgrep_config"]).is_file():
            raise ValueError(
                "Offline Semgrep requires a reviewed local rule file, not registry rules"
            )
        if node:
            try:
                audit_node(ROOT, config, offline=args.offline)
            except (OSError, ValueError, subprocess.CalledProcessError):
                ci_result("Application npm dependency audit", "FAILED", "See job log")
                raise
            ci_result("Application npm dependency audit", "PASSED")
        else:
            ci_result("Application npm dependency audit", "SKIPPED", "No Node application profile")
    if action in {"security", "push", "secrets"}:
        for path in scanner_configs.get("gitleaks", []):
            if path.parent != ROOT:
                run(
                    str(tools / "gitleaks"),
                    "dir",
                    "--redact",
                    "--config",
                    str(path),
                    ".",
                    directory=path.parent,
                )
    if action in {"security", "push", "sast"}:
        run(
            str(ROOT / ".tools/venv/bin/semgrep"),
            "scan",
            "--config",
            config["semgrep_config"],
            "--metrics",
            "off",
            "--disable-version-check",
            "--error",
            ".",
        )
        for path in scanner_configs.get("semgrep", []):
            if path.name != ".semgrepignore":
                run(
                    str(ROOT / ".tools/venv/bin/semgrep"),
                    "scan",
                    "--config",
                    str(path),
                    "--metrics",
                    "off",
                    "--disable-version-check",
                    "--error",
                    ".",
                    directory=path.parent,
                )
    if action in {"security", "push", "filesystem"}:
        trivy_configs = scanner_configs.get("trivy", [])
        for scope in sorted({ROOT, *(path.parent for path in trivy_configs)}):
            options = [
                argument
                for path in trivy_configs
                if path.parent == scope
                for argument in (
                    "--ignorefile" if path.name.startswith(".trivyignore") else "--config",
                    str(path),
                )
            ]
            run(
                str(tools / "trivy"),
                "fs",
                "--cache-dir",
                str(ROOT / ".tools/cache/trivy"),
                "--scanners",
                "vuln,secret,misconfig",
                "--exit-code",
                "1",
                "--severity",
                ",".join(config["severity"]),
                "--skip-dirs",
                ".venv",
                "--skip-dirs",
                ".tools",
                "--skip-dirs",
                ".quality",
                "--skip-dirs",
                "node_modules",
                "--skip-files",
                "**/package-lock.json",
                "--skip-files",
                "**/npm-shrinkwrap.json",
                "--skip-files",
                "**/package.json",
                *(
                    ["--skip-db-update", "--skip-java-db-update", "--offline-scan"]
                    if args.offline
                    else []
                ),
                *options,
                ".",
                directory=scope,
            )
    if action in {"security", "push"}:
        scan_receipt(ROOT, config, args.offline)
    if action == "docker" or check:
        docker_checks(files, config["docker"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Quality check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
