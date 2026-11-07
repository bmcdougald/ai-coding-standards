from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[1]
PROVIDERS = {
    "github": (".github/workflows/quality.yml", "github-quality.yml"),
    "gitlab": (".gitlab-ci.yml", "gitlab-quality.yml"),
    "jenkins": ("Jenkinsfile", "Jenkinsfile"),
}
EXCLUDED = {".git", ".quality", ".tools", ".venv", "node_modules", "vendor", "build", "dist"}


def safe_path(target: Path, name: str) -> Path:
    path = target / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(target)
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError("Unsafe CI project path: " + name)
    return path


def load_runner(source: Path):
    spec = importlib.util.spec_from_file_location("quality_ci", source / "templates/quality/ci.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Missing CI runner")
    for name in ("jsonc", "maintenance"):
        helper_spec = importlib.util.spec_from_file_location(
            name, source / "templates/quality" / (name + ".py")
        )
        if helper_spec is None or helper_spec.loader is None:
            raise RuntimeError("Missing quality helper: " + name)
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        sys.modules[name] = helper
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover(target: Path) -> dict[str, Any]:
    config: dict[str, Any] = {"schema": 1, "setup": [], "tests": [], "timeout_seconds": 1800}
    paths = []
    for directory, folders, names in os.walk(target):
        folders[:] = [
            name
            for name in folders
            if name not in EXCLUDED and not (Path(directory) / name).is_symlink()
        ]
        paths.extend(
            Path(directory) / name for name in names if not (Path(directory) / name).is_symlink()
        )

    def command(group: str, name: str, directory: Path, argv: list[str]) -> None:
        config[group].append(
            {"name": name, "cwd": directory.relative_to(target).as_posix(), "argv": argv}
        )

    for manifest in sorted(path for path in paths if path.name == "package.json"):
        package = json.loads(manifest.read_text())
        if not isinstance(package, dict):
            raise ValueError("CI package manifest must be an object: " + str(manifest))
        scripts = package.get("scripts", {})
        if not isinstance(scripts, dict) or any(
            not isinstance(value, str) for value in scripts.values()
        ):
            raise ValueError("CI package scripts must contain string commands: " + str(manifest))
        if any(
            not isinstance(package.get(field, {}), dict)
            for field in ("dependencies", "devDependencies")
        ):
            raise ValueError("CI package dependencies must be objects: " + str(manifest))
        directory = manifest.parent
        dedicated = [
            name
            for name in ("test:unit", "test:integration", "test:e2e", "test:playwright")
            if name in scripts
        ]
        selected = (["test:ci"] if "test:ci" in scripts else []) + dedicated
        if not selected and "test" in scripts:
            selected = ["test"]
        if selected:
            command("setup", "Rebuild application npm dependencies", directory, ["npm", "rebuild"])
        for name in selected:
            script = scripts[name]
            if (
                not isinstance(script, str)
                or ".quality/" in script
                or "no test specified" in script
            ):
                continue
            command("tests", "npm " + name, directory, ["npm", "run", name])
        playwright = any(
            path.parent == directory and path.name.startswith("playwright.config.")
            for path in paths
        )
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        if playwright and "@playwright/test" in dependencies:
            command(
                "setup",
                "Install Playwright browsers/system dependencies",
                directory,
                ["npm", "exec", "--no", "--", "playwright", "install", "--with-deps"],
            )
            if not any("playwright" in scripts.get(name, "") for name in selected):
                command(
                    "tests",
                    "Playwright",
                    directory,
                    ["npm", "exec", "--no", "--", "playwright", "test"],
                )
        if playwright and not selected:
            command("setup", "Rebuild application npm dependencies", directory, ["npm", "rebuild"])

    python_roots = {
        path.parent
        for path in paths
        if path.name in {"pyproject.toml", "pytest.ini", "setup.cfg", "requirements.txt"}
    }
    if not python_roots and any(path.suffix == ".py" for path in paths):
        python_roots.add(target)
    for directory in sorted(python_roots):
        scoped_paths = [
            path
            for path in paths
            if path.is_relative_to(directory)
            and not any(
                path.is_relative_to(child)
                for child in python_roots
                if child != directory and child.is_relative_to(directory)
            )
        ]
        tests = [
            path
            for path in scoped_paths
            if (path.name.startswith("test_") or path.name.endswith("_test.py"))
            and path.suffix == ".py"
        ]
        if not tests:
            continue
        pyproject = directory / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text()) if pyproject in paths else {}
        pytest = (
            (directory / "pytest.ini") in paths
            or "pytest" in project.get("tool", {})
            or any(
                "import pytest" in path.read_text() or "from pytest import" in path.read_text()
                for path in tests
            )
        )
        unittest = all("unittest" in path.read_text() for path in tests)
        if not pytest and not unittest:
            print(
                "CI: test framework is ambiguous in " + str(directory) + "; add commands manually.",
                file=sys.stderr,
            )
            continue
        command(
            "setup",
            "Create application Python environment",
            directory,
            ["{base_python}", "-m", "venv", ".venv"],
        )
        requirements = [
            name
            for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt")
            if (directory / name) in paths
        ]
        python_dependencies: list[str] = []
        if requirements:
            python_dependencies = [
                line
                for name in requirements
                for line in (directory / name).read_text().splitlines()
            ]
            command(
                "setup",
                "Install application Python requirements",
                directory,
                [
                    "{python}",
                    "-m",
                    "pip",
                    "install",
                    *[arg for name in requirements for arg in ("-r", name)],
                ],
            )
        elif project.get("project") or (directory / "setup.py") in paths:
            extras = project.get("project", {}).get("optional-dependencies", {})
            extra = next((name for name in ("test", "tests", "dev") if name in extras), None)
            python_dependencies = [
                *project.get("project", {}).get("dependencies", []),
                *extras.get(extra, []),
            ]
            command(
                "setup",
                "Install application Python package",
                directory,
                ["{python}", "-m", "pip", "install", f".[{extra}]" if extra else "."],
            )
        if pytest:
            playwright = any(
                re.match(
                    r"^\s*(?:pytest[-_]playwright|playwright)(?=\s|[<>=!~;\[]|$)",
                    value,
                    re.IGNORECASE,
                )
                for value in python_dependencies
            )
            for path in [*tests, *(path for path in scoped_paths if path.name == "conftest.py")]:
                try:
                    tree = ast.parse(path.read_text(), filename=str(path))
                except SyntaxError as error:
                    raise ValueError("Invalid Python test/fixture: " + str(path)) from error
                if any(
                    isinstance(node, ast.Import)
                    and any(alias.name.split(".")[0] == "playwright" for alias in node.names)
                    or isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and node.module.split(".")[0] == "playwright"
                    for node in ast.walk(tree)
                ):
                    playwright = True
            if playwright:
                command(
                    "setup",
                    "Install Python Playwright browsers/system dependencies",
                    directory,
                    ["{python}", "-m", "playwright", "install", "--with-deps"],
                )
            command("tests", "pytest", directory, ["{python}", "-m", "pytest"])
        else:
            start = "tests" if (directory / "tests").is_dir() else "."
            command(
                "tests",
                "unittest",
                directory,
                ["{python}", "-m", "unittest", "discover", "-s", start],
            )
    return config


def plan(
    target: Path,
    providers: set[str],
    *,
    config_path: Path | None = None,
    preview: bool = False,
    source: Path | None = None,
) -> dict[str, str]:
    source = source or SOURCE
    if not providers or providers - PROVIDERS.keys():
        raise ValueError("CI providers must be github, gitlab, and/or jenkins")
    saved = safe_path(target, ".quality/ci.json")
    if config_path:
        config = json.loads(config_path.expanduser().read_text())
    elif saved.exists():
        config = json.loads(saved.read_text())
    else:
        config = discover(target)
    runner = load_runner(source)
    runner.validate_config(config, target)
    if preview:
        return {".quality/ci.json": json.dumps(config, indent=2) + "\n"}
    baseline_path = safe_path(target, ".quality/baseline.json")
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        if (
            not isinstance(baseline, dict)
            or baseline.get("schema") != 1
            or not isinstance(baseline.get("runtime"), dict)
        ):
            raise ValueError("Invalid tooling ownership record; reconcile before adding CI")
        runtime = safe_path(target, ".quality/run.py")
        expected = hashlib.sha256((source / "templates/quality/run.py").read_bytes()).hexdigest()
        if (
            baseline.get("runtime_api") != 2
            or baseline.get("runtime", {}).get(".quality/run.py") != expected
            or not runtime.is_file()
            or hashlib.sha256(runtime.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(
                "Update existing tooling with --update-tooling before adding CI; "
                "use --migrate-tooling if the existing runtime needs migration."
            )
    if not config_path and not saved.exists() and (config["setup"] or config["tests"]):
        print("Proposed application setup/tests (review noninteractive CI, services, and secrets):")
        print(json.dumps(config, indent=2))
        if not sys.stdin.isatty():
            raise ValueError(
                "CI commands need review. Use --ci-preview to save JSON, edit it, "
                "then pass --ci-config /path/to/reviewed.json."
            )
        if input("Accept these CI commands? [y/N]: ").strip().lower() != "y":
            raise ValueError("CI cancelled; edit the --ci-preview JSON and use --ci-config")
    files = {
        ".quality/ci.json": json.dumps(config, indent=2) + "\n",
        ".quality/ci.py": (source / "templates/quality/ci.py").read_text(),
    }
    for name in (".gitignore", ".prettierignore"):
        path = safe_path(target, name)
        if (
            name == ".prettierignore"
            and not path.exists()
            and not (target / "package.json").exists()
        ):
            continue
        text = path.read_text() if path.exists() else ""
        patterns = ["test-results/", "playwright-report/", "blob-report/"]
        missing = [pattern for pattern in patterns if pattern not in text.splitlines()]
        if missing:
            files[name] = text.rstrip() + "\n" + "\n".join(missing) + "\n"
    runtime = safe_path(target, ".quality/ci.py")
    if (
        runtime.exists()
        and runtime.read_text() != files[".quality/ci.py"]
        and (
            not baseline_path.exists()
            or json.loads(baseline_path.read_text()).get("runtime", {}).get(".quality/ci.py")
            != hashlib.sha256(runtime.read_bytes()).hexdigest()
        )
    ):
        raise ValueError("Locally modified CI runner; reconcile before configuring CI")
    for provider in sorted(providers):
        destination, template = PROVIDERS[provider]
        content = (source / "templates/quality" / template).read_text()
        files[".quality/ci-adapters/" + template] = content
        path = safe_path(target, destination)
        if path.exists() and path.read_text() != content:
            print(
                "Existing pipeline preserved: "
                + destination
                + ". Integrate the generated .quality/ci-adapters/"
                + template
                + " manually."
            )
        else:
            files[destination] = content
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        if baseline.get("runtime_api") != 2:
            raise ValueError("Migrate tooling to runtime API 2 before configuring application CI")
        baseline["runtime"][".quality/ci.py"] = hashlib.sha256(
            files[".quality/ci.py"].encode()
        ).hexdigest()
        files[".quality/baseline.json"] = json.dumps(baseline, indent=2) + "\n"
    for name in files:
        safe_path(target, name)
    return files


def write(target: Path, files: dict[str, str]) -> None:
    changed = [
        name
        for name, content in files.items()
        if safe_path(target, name).exists() and safe_path(target, name).read_text() != content
    ]
    for name in files:
        path = safe_path(target, name)
        if path.exists() and not path.is_file():
            raise ValueError("CI destination is not a file: " + name)
    if changed:
        backup = safe_path(target, ".tools/backups/ci-" + uuid.uuid4().hex)
        for name in changed:
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_path(target, name), destination)
        print("CI configuration backup: " + str(backup.relative_to(target)))
    for name, content in files.items():
        path = safe_path(target, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
    prettier = target / "node_modules/.bin/prettier"
    if prettier.is_file():
        subprocess.run(  # noqa: S603 -- local formatter and fixed managed JSON filenames
            [
                str(prettier),
                "--write",
                ".quality/ci.json",
                *(
                    [".quality/baseline.json"]
                    if (target / ".quality/baseline.json").exists()
                    else []
                ),
            ],
            cwd=target,
            check=True,
        )
