from __future__ import annotations

import argparse
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
LANGUAGES = {"python", "javascript", "typescript", "react"}
spec = importlib.util.spec_from_file_location(
    "quality_jsonc", SOURCE / "templates/quality/jsonc.py"
)
if spec is None or spec.loader is None:
    raise RuntimeError("Missing bundled JSONC parser")
jsonc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jsonc)
TOKEN = jsonc.TOKEN
parse_jsonc = jsonc.parse_jsonc
integration_spec = importlib.util.spec_from_file_location(
    "integrate_tooling", SOURCE / "scripts/integrate_tooling.py"
)
if integration_spec is None or integration_spec.loader is None:
    raise RuntimeError("Missing bundled tooling integration")
integration = importlib.util.module_from_spec(integration_spec)
integration_spec.loader.exec_module(integration)
EXCLUDED = {
    ".git",
    ".quality",
    ".tools",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".next",
    ".nuxt",
    ".tox",
    ".pytest_cache",
    ".ruff_cache",
}


def repository_paths(target: Path, git: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 -- resolved Git executable, fixed argv; no shell
        [git, "-C", str(target), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for name in sorted(set(result.stdout.rstrip("\0").split("\0")) - {""}):
        path = target / name
        if (
            not any(part in EXCLUDED for part in Path(name).parts)
            and path.is_file()
            and not any(parent.is_symlink() for parent in (path, *path.parents))
        ):
            paths.append(name)
    return paths


def inspect_repository(target: Path, git: str) -> tuple[dict[str, list[str]], bool]:
    extensions = {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
        ".tsx": "typescript",
    }
    evidence: dict[str, list[str]] = {}
    nonempty = False
    metadata = {"AGENTS.md", "README", "LICENSE", "COPYING", "QUALITY.md"}
    config_path = target / ".quality/config.json"
    saved = parse_jsonc(config_path.read_text()) if config_path.exists() else {}

    def record(language: str, name: str) -> None:
        evidence.setdefault(language, []).append(name)

    for name in repository_paths(target, git):
        path = target / name
        suffix = path.suffix.lower()
        requirements = path.name.startswith("requirements") and suffix in {".txt", ".in"}
        if (
            suffix in {".md", ".rst", ".txt"}
            and not requirements
            or path.name in metadata
            or path.name.startswith(".")
        ):
            continue
        nonempty = True
        if path.name == "package.json":
            try:
                package = json.loads(path.read_text())
                if not isinstance(package, dict):
                    raise ValueError("Expected an object")
                dependencies: dict[str, Any] = {}
                for field in (
                    "dependencies",
                    "devDependencies",
                    "peerDependencies",
                    "optionalDependencies",
                ):
                    values = package.get(field, {})
                    if not isinstance(values, dict):
                        raise ValueError(f"{field} must be an object")
                    dependencies.update(values)
            except (ValueError, UnicodeError) as error:
                raise ValueError(f"Cannot inspect {name}: {error}") from error
            # A package manifest proves Node tooling, not necessarily JavaScript application code.
            scope = path.parent.relative_to(target).as_posix()
            managed = (
                scope in saved.get("node_projects", ["."])
                and name in saved.get("managed_tooling", [name])
                and (
                    bool(set(saved.get("languages", [])) - {"python"})
                    or saved.get("hook_manager") == "husky"
                )
            )
            changed_typescript = "typescript" in dependencies and dependencies[
                "typescript"
            ] != saved.get("node", {}).get("typescript")
            if not managed or changed_typescript:
                if "typescript" in dependencies:
                    record("typescript", name)
                else:
                    record("javascript", name)
            if "react" in dependencies or "react-dom" in dependencies:
                record("react", name)
        elif path.name == "pyproject.toml":
            try:
                tomllib.loads(path.read_text())
            except (ValueError, UnicodeError) as error:
                raise ValueError(f"Cannot inspect {name}: {error}") from error
            record("python", name)
        elif requirements or path.name in {
            "setup.py",
            "setup.cfg",
            "Pipfile",
            "poetry.lock",
            "uv.lock",
        }:
            record("python", name)
        elif suffix in extensions:
            # Analyzer configs generated by this suite are not application-language evidence.
            if path.name.startswith(("eslint.config.", "prettier.config.")):
                continue
            record(extensions[suffix], name)
            if suffix in {".jsx", ".tsx"}:
                record("react", name)
    return evidence, nonempty


def inspect_tooling(target: Path, git: str) -> tuple[list[dict[str, Any]], Path]:
    inventory: list[dict[str, Any]] = []
    for name in repository_paths(target, git):
        path = target / name
        scope = path.parent.relative_to(target).as_posix()
        for tool, filenames in integration.CONFIGS.items():
            if path.name in filenames:
                inventory.append({"path": name, "scope": scope, "kind": tool, "configured": True})
        if path.name == "pyproject.toml":
            document = tomllib.loads(path.read_text())
            if not isinstance(document.get("tool", {}), dict):
                raise ValueError("Python tool configuration must be a table: " + name)
            inventory.append(
                {
                    "path": name,
                    "scope": scope,
                    "kind": "python",
                    "configured": any(
                        key in document.get("tool", {}) for key in ("ruff", "pyright")
                    ),
                }
            )
        elif path.name == "package.json":
            package = json.loads(path.read_text())
            if not isinstance(package, dict) or any(
                not isinstance(package.get(field, {}), dict)
                for field in ("dependencies", "devDependencies", "peerDependencies")
            ):
                raise ValueError("Package metadata and dependencies must be objects: " + name)
            dependencies = {
                key
                for field in ("dependencies", "devDependencies", "peerDependencies")
                for key in package.get(field, {})
            }
            inventory.append(
                {
                    "path": name,
                    "scope": scope,
                    "kind": "package",
                    "configured": bool(dependencies & integration.NODE_TOOLS)
                    or "eslintConfig" in package
                    or "prettier" in package,
                }
            )
        elif path.name.startswith("tsconfig") and path.suffix == ".json":
            parse_jsonc(path.read_text())
            inventory.append(
                {"path": name, "scope": scope, "kind": "typescript", "configured": True}
            )
        elif path.parent.name == ".husky" and path.name in {"pre-commit", "pre-push"}:
            inventory.append(
                {
                    "path": name,
                    "scope": path.parent.parent.relative_to(target).as_posix(),
                    "kind": "hook",
                    "configured": True,
                }
            )
    hooks = subprocess.run(  # noqa: S603 -- resolved Git executable and fixed argv
        [git, "-C", str(target), "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        check=True,
    )
    directory = Path(hooks.stdout.strip())
    directory = directory if directory.is_absolute() else target / directory
    # Husky's dispatch scripts are generated infrastructure; amend its source hooks instead.
    if directory.parts[-2:] != (".husky", "_"):
        for stage in ("pre-commit", "pre-push"):
            path = directory / stage
            if path.is_file():
                if not path.resolve().is_relative_to(target):
                    inventory.append(
                        {
                            "path": str(path),
                            "scope": ".",
                            "kind": "hook",
                            "configured": True,
                            "external": True,
                        }
                    )
                else:
                    inventory.append(
                        {
                            "path": path.relative_to(target).as_posix(),
                            "scope": ".",
                            "kind": "hook",
                            "configured": True,
                        }
                    )
    return inventory, directory


def choose_tooling_policy(
    target: Path,
    inventory: list[dict[str, Any]],
    requested: str | None,
) -> str:
    if requested:
        return requested
    configured = target / ".quality/config.json"
    if configured.exists():
        current = parse_jsonc(configured.read_text())
        saved_policy = current.get("tooling_policy", "amend")
        if not isinstance(saved_policy, str) or saved_policy not in {"leave", "amend", "replace"}:
            raise ValueError("Invalid saved tooling policy; select --existing-tools explicitly")
        managed = current.get("managed_tooling")
        if managed is not None and (
            not isinstance(managed, list) or any(not isinstance(name, str) for name in managed)
        ):
            raise ValueError("Saved managed_tooling must be a list of configuration paths")
        newly_configured = [
            entry
            for entry in inventory
            if entry["configured"]
            and managed is not None
            and entry["path"] not in managed
            and not (
                entry["kind"] == "hook"
                and "# project-local-tool-path" in (target / entry["path"]).read_text()
            )
        ]
        if not newly_configured:
            return saved_policy
        inventory = newly_configured
        print("New tooling configuration requires review:")
    existing = [entry for entry in inventory if entry["configured"]]
    if not existing:
        return "amend"
    print("Existing tooling/checks found:")
    for name in sorted({entry["path"] for entry in existing}):
        print("  " + name)
    if not sys.stdin.isatty():
        raise ValueError("Existing tooling requires --existing-tools leave, amend, or replace")
    print("1. Leave Existing Tooling & Checks in Place (no changes)")
    print("2. Amend Existing Tooling & Checks (preserve rules; add missing tools/checks)")
    print("3. Replace Existing Tooling & Checks (back up and standardize)")
    selection = input("Existing tooling policy [1/2/3]: ").strip().lower()
    policy = {"1": "leave", "2": "amend", "3": "replace"}.get(selection, selection)
    if policy not in {"leave", "amend", "replace"}:
        raise ValueError("Select leave, amend, or replace")
    return policy


def ensure_configuration_parsers(target: Path, arguments: list[str]) -> int | None:
    if importlib.util.find_spec("tomlkit") and importlib.util.find_spec("ruamel"):
        return None
    environment = target / ".tools/bootstrap"
    if any(path.is_symlink() for path in (environment, *environment.parents)):
        raise ValueError("Refusing to install configuration parsers through a symlink")
    environment.parent.mkdir(parents=True, exist_ok=True)
    python = environment / "bin/python"
    if not python.exists():
        subprocess.run(  # noqa: S603 -- selected interpreter and fixed venv arguments
            [sys.executable, "-m", "venv", str(environment)], check=True
        )
    print("Installing comment-preserving configuration parsers in .tools/bootstrap.", flush=True)
    subprocess.run(  # noqa: S603 -- local interpreter, pinned parser requirements, no shell
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-r",
            str(SOURCE / "scripts/requirements-bootstrap.txt"),
            "--cache-dir",
            str(target / ".tools/cache/bootstrap"),
        ],
        check=True,
    )
    result = subprocess.run(  # noqa: S603 -- local interpreter and explicit bootstrap argv
        [str(python), str(SOURCE / "scripts/bootstrap.py"), *arguments], check=False
    )
    return result.returncode


def select_languages(
    target: Path, git: str, requested: str | None
) -> tuple[set[str], bool, dict[str, list[str]]]:
    evidence, nonempty = inspect_repository(target, git)
    configured = target / ".quality/config.json"
    if requested is not None:
        languages = {name.strip().lower() for name in requested.split(",")}
    elif configured.exists():
        languages = set(parse_jsonc(configured.read_text())["languages"])
        new = set(evidence) - languages
        if new:
            raise ValueError(
                "New languages detected: "
                + ", ".join(sorted(new))
                + "; changing the configured profile requires a reviewed migration"
            )
        print("Using the existing configured language profile.")
    elif evidence:
        languages = set(evidence)
        print("Detected languages: " + ", ".join(sorted(languages)))
        for language, paths in evidence.items():
            print(f"  {language}: {', '.join(paths[:3])}")
    elif nonempty:
        raise ValueError(
            "Existing repository has no supported language evidence. "
            "Use --languages explicitly; unsupported languages are not configured automatically."
        )
    else:
        if not sys.stdin.isatty():
            raise ValueError(
                "Empty/metadata-only repository: --languages is required without a terminal"
            )
        languages = {
            name.strip().lower()
            for name in input("Languages (python,javascript,typescript,react): ").split(",")
        }
    if not languages or not languages <= LANGUAGES:
        raise ValueError("Select one or more supported languages: " + ", ".join(sorted(LANGUAGES)))
    return languages, nonempty, evidence


def merge_jsonc(text: str, additions: dict[str, Any], *, replace_values: bool = False) -> str:
    """Add missing settings without replacing existing values or JSONC comments."""
    tokens = [m for m in TOKEN.finditer(text) if not m.group().startswith(("//", "/*"))]
    existing = parse_jsonc(text)
    if not isinstance(existing, dict):
        raise ValueError("Expected a JSON object")
    edits: list[tuple[int, int, str]] = []

    def walk(index: int, wanted: Any, current: Any, location: str = "") -> int:
        if (
            wanted != {}
            and isinstance(wanted, (dict, list))
            and not isinstance(current, type(wanted))
        ):
            raise ValueError("Existing configuration has an incompatible value type: " + location)
        start = tokens[index].group()
        if start == "{":
            index += 1
            while tokens[index].group() != "}":
                key = json.loads(tokens[index].group())
                index += 2
                index = walk(
                    index,
                    wanted.get(key, {}) if isinstance(wanted, dict) else {},
                    current[key],
                    location + "." + key,
                )
                if tokens[index].group() == ",":
                    index += 1
            if isinstance(wanted, dict):
                missing = {k: v for k, v in wanted.items() if k not in current}
                if missing:
                    comma = "," if current and tokens[index - 1].group() != "," else ""
                    body = json.dumps(missing, indent=2)[1:-1].strip()
                    edits.append(
                        (tokens[index].start(), tokens[index].start(), comma + "\n" + body + "\n")
                    )
            return index + 1
        if start == "[":
            beginning = tokens[index].start()
            index += 1
            position = 0
            while tokens[index].group() != "]":
                index = walk(index, {}, current[position], f"{location}[{position}]")
                position += 1
                if tokens[index].group() == ",":
                    index += 1
            if isinstance(wanted, list):
                if replace_values:
                    if wanted != current:
                        edits.append((beginning, tokens[index].end(), json.dumps(wanted)))
                    return index + 1
                missing = [item for item in wanted if item not in current]
                if missing:
                    comma = "," if current and tokens[index - 1].group() != "," else ""
                    edits.append(
                        (
                            tokens[index].start(),
                            tokens[index].start(),
                            comma
                            + "\n"
                            + ",\n".join(json.dumps(item, indent=2) for item in missing)
                            + "\n",
                        )
                    )
            return index + 1
        if wanted != {} and wanted != current:
            if replace_values:
                edits.append((tokens[index].start(), tokens[index].end(), json.dumps(wanted)))
            else:
                print(
                    "Preserving existing setting with a different default: " + location.lstrip(".")
                )
        return index + 1

    walk(0, additions, existing)
    for offset, end, content in sorted(edits, reverse=True):
        text = text[:offset] + content + text[end:]
    return text


def safe_tool_path(target: Path, name: str) -> Path:
    path = target / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(target)
        or any(parent.is_symlink() for parent in (path, *path.parents))
    ):
        raise ValueError("Unsafe tooling path: " + name)
    return path


def tooling_baseline(
    target: Path, files: dict[str, str], config: dict[str, Any], *, adopt: bool = False
) -> dict[str, Any]:
    """Record only dependencies introduced by the suite, not retained application tooling."""
    packages: dict[str, Any] = {}
    for project in config.get("node_projects", []):
        name = (Path(project) / "package.json").as_posix()
        if name not in files:
            continue
        package = json.loads(files[name])
        path = safe_tool_path(target, name)
        original = json.loads(path.read_text()) if path.exists() and not adopt else {}
        owned: dict[str, Any] = {}
        for field in ("devDependencies", "dependencies"):
            values = {
                tool: version
                for tool, version in package.get(field, {}).items()
                if config["node"].get(tool) == version
                and (
                    adopt
                    or config.get("tooling_policy") == "replace"
                    or tool not in original.get("dependencies", {})
                    and tool not in original.get("devDependencies", {})
                )
            }
            if values:
                owned[field] = values
        overrides = {
            tool: value
            for tool, value in package.get("overrides", {}).items()
            if value
            == config.get(
                "node_overrides",
                json.loads((SOURCE / "templates/quality/versions.json").read_text())[
                    "node_overrides"
                ],
            ).get(tool)
            and (
                adopt
                or config.get("tooling_policy") == "replace"
                or tool not in original.get("overrides", {})
            )
        }
        if overrides:
            owned["overrides"] = overrides
        if owned:
            packages[name] = owned
    requirements = files[".quality/requirements.in"].splitlines()
    python = {
        name: version
        for name, version in config["python"].items()
        if name + "==" + version in requirements
    }
    return {
        "schema": 1,
        "runtime_api": config.get("runtime_api", 1),
        "source_digest": hashlib.sha256(
            (SOURCE / "templates/quality/versions.json").read_bytes()
        ).hexdigest(),
        "python": python,
        "node": packages,
        "binaries": config["binaries"],
        "runtime": {
            ".quality/" + name: hashlib.sha256(files[".quality/" + name].encode()).hexdigest()
            for name in ("install.py", "run.py", "jsonc.py")
        },
    }


def compatible_version(name: str, old: str, new: str) -> None:
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("Update requires exact reviewed versions: " + name)
    old_match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", old)
    new_match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", new)
    if old_match is None or new_match is None:
        raise ValueError("Update requires exact reviewed versions: " + name)
    previous = tuple(int(part or "0") for part in old_match.groups())
    candidate = tuple(int(part or "0") for part in new_match.groups())
    if candidate < previous or (
        candidate[0] != previous[0] or previous[0] == 0 and candidate[1] != previous[1]
    ):
        raise ValueError("Version change requires an explicit migration: " + name)


def plan_tooling_update(
    target: Path, *, adopt: bool = False
) -> tuple[dict[str, str], dict[str, Any]]:
    pending = safe_tool_path(target, ".tools/update-pending.json")
    if pending.exists():
        raise ValueError(
            "An interrupted update needs recovery before another update. "
            "Use its backup restore.json inventory, restore installed tools, then remove "
            "the pending marker: " + str(pending)
        )
    config = parse_jsonc(safe_tool_path(target, ".quality/config.json").read_text())
    latest = json.loads((SOURCE / "templates/quality/versions.json").read_text())
    baseline_path = safe_tool_path(target, ".quality/baseline.json")
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
    else:
        if not adopt:
            raise ValueError(
                "This project predates ownership tracking. Review --update-tooling "
                "--adopt-tooling --preview, then confirm adoption explicitly."
            )
        files = {
            ".quality/" + name: safe_tool_path(target, ".quality/" + name).read_text()
            for name in ("install.py", "run.py", "jsonc.py", "requirements.in")
        }
        for project in config.get("node_projects", []):
            name = (Path(project) / "package.json").as_posix()
            path = safe_tool_path(target, name)
            if path.exists():
                files[name] = path.read_text()
            elif set(config["languages"]) - {"python"} or config["hook_manager"] == "husky":
                raise ValueError("Recorded Node project is missing: " + name)
        baseline = tooling_baseline(target, files, config, adopt=True)
        legacy_runtime = {
            ".quality/install.py": (
                "15ae4a883ecbc8fa55ba92307cad0bce60b91bb3cf261807c49624fa192c5ca8"
            ),
            ".quality/run.py": "89c894b79e04f1456b2e940abe06bd275960b93fa84bc729fcf38486277cd60b",
        }
        for name, digest in baseline["runtime"].items():
            current = hashlib.sha256(
                (SOURCE / "templates/quality" / Path(name).name).read_bytes()
            ).hexdigest()
            if digest != current and digest != legacy_runtime.get(name):
                raise ValueError("Cannot adopt locally modified or unfamiliar runtime: " + name)
        print("Adopting exact baseline-matching dependencies; retained versions stay unowned.")
    if not isinstance(baseline, dict) or any(
        not isinstance(baseline.get(key), dict) for key in ("python", "node", "binaries", "runtime")
    ):
        raise ValueError("Invalid tooling ownership record")
    if (
        baseline.get("schema") != 1
        or baseline.get("runtime_api") != latest["runtime_api"]
        or config.get("runtime_api", 1) != baseline["runtime_api"]
    ):
        raise ValueError("Suite runtime change requires an explicit migration")
    if set(baseline["runtime"]) != {".quality/install.py", ".quality/run.py", ".quality/jsonc.py"}:
        raise ValueError("Invalid suite runtime ownership record")
    edits: dict[str, str] = {}
    requirements_path = safe_tool_path(target, ".quality/requirements.in")
    requirements = requirements_path.read_text()
    for name, old in baseline["python"].items():
        new = latest["python"].get(name)
        if new is None:
            raise ValueError("Removed Python tool requires migration: " + name)
        compatible_version(name, old, new)
        if (
            config["python"].get(name) != old
            or requirements.splitlines().count(name + "==" + old) != 1
        ):
            raise ValueError("Locally changed Python tooling: " + name)
        requirements = re.sub(
            r"(?m)^" + re.escape(name + "==" + old) + r"$", name + "==" + new, requirements
        )
        config["python"][name] = new
        baseline["python"][name] = new
    edits[".quality/requirements.in"] = requirements
    for name, owned in baseline["node"].items():
        if not isinstance(owned, dict) or set(owned) - {
            "dependencies",
            "devDependencies",
            "overrides",
        }:
            raise ValueError("Invalid Node tooling ownership record: " + name)
        path = safe_tool_path(target, name)
        text = path.read_text()
        package = json.loads(text)
        for field, values in owned.items():
            for tool, old in values.items():
                if package.get(field, {}).get(tool) != old:
                    raise ValueError("Locally changed suite dependency: " + name + ": " + tool)
                new = latest["node_overrides" if field == "overrides" else "node"].get(tool)
                if new is None:
                    raise ValueError("Removed Node tool requires migration: " + tool)
                if field == "overrides":
                    if (
                        not isinstance(old, dict)
                        or not isinstance(new, dict)
                        or old.keys() != new.keys()
                    ):
                        raise ValueError("Override structure change requires migration: " + tool)
                    for dependency in old:
                        compatible_version(
                            tool + "/" + dependency, old[dependency], new[dependency]
                        )
                else:
                    compatible_version(tool, old, new)
                package[field][tool] = new
                values[tool] = new
        edits[name] = merge_jsonc(
            text, {field: package[field] for field in owned}, replace_values=True
        )
    for name, old in baseline["binaries"].items():
        new = latest["binaries"].get(name)
        if new is None or new["repository"] != old["repository"]:
            raise ValueError("Binary source change requires migration: " + name)
        compatible_version(name, old["version"], new["version"])
        if config["binaries"].get(name) != old:
            raise ValueError("Locally changed binary configuration: " + name)
        config["binaries"][name] = new
        baseline["binaries"][name] = new
    for name, digest in baseline["runtime"].items():
        path = safe_tool_path(target, name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Locally modified suite runtime: " + name)
        content = (SOURCE / "templates/quality" / Path(name).name).read_text()
        edits[name] = content
        baseline["runtime"][name] = hashlib.sha256(content.encode()).hexdigest()
    config["runtime_api"] = latest["runtime_api"]
    for owned in baseline["node"].values():
        for field, values in owned.items():
            for tool, version in values.items():
                if field == "overrides":
                    config.setdefault("node_overrides", {})[tool] = version
                else:
                    config["node"][tool] = version
    edits[".quality/config.json"] = merge_jsonc(
        safe_tool_path(target, ".quality/config.json").read_text(), config, replace_values=True
    )
    baseline["source_digest"] = hashlib.sha256(
        (SOURCE / "templates/quality/versions.json").read_bytes()
    ).hexdigest()
    edits[".quality/baseline.json"] = json.dumps(baseline, indent=2) + "\n"
    return {
        name: content
        for name, content in edits.items()
        if not safe_tool_path(target, name).exists()
        or (
            safe_tool_path(target, name).read_text() != content
            and (
                not name.endswith(".json")
                or parse_jsonc(safe_tool_path(target, name).read_text()) != parse_jsonc(content)
            )
        )
    }, config


def update_tooling(
    target: Path, *, preview: bool = False, yes: bool = False, adopt: bool = False
) -> None:
    edits, config = plan_tooling_update(target, adopt=adopt)
    if not edits:
        print("Tooling is already up to date; no changes made.")
        return
    import difflib

    print("Proposed tooling update (application settings and hooks are retained):")
    for name, content in edits.items():
        path = safe_tool_path(target, name)
        original = path.read_text() if path.exists() else ""
        print(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True), content.splitlines(True), fromfile=name, tofile=name
                )
            ),
            end="",
        )
    if preview:
        print("Preview only; no files changed or dependencies installed.")
        return
    if not yes:
        if not sys.stdin.isatty():
            raise ValueError("Review --preview, then use --yes for an unattended update")
        try:
            confirmed = (
                input("Apply this tooling update and run project checks? [y/N]: ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("Update cancelled; no changes made.")
            return
        if confirmed != "y":
            print("Update cancelled; no changes made.")
            return
    lock_names = {".quality/locked/requirements.txt"}
    for project in config.get("node_projects", []):
        for lock in ("package-lock.json", "npm-shrinkwrap.json"):
            lock_names.add((Path(project) / lock).as_posix())
    affected = set(edits) | lock_names
    originals = {
        name: safe_tool_path(target, name).read_bytes()
        if safe_tool_path(target, name).exists()
        else None
        for name in affected
    }
    backup = safe_tool_path(target, ".tools/backups/update-" + uuid.uuid4().hex)
    for name, content in originals.items():
        if content is not None:
            path = backup / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "restore.json").write_text(
        json.dumps(
            {
                "restore": sorted(name for name, data in originals.items() if data is not None),
                "remove_if_created": sorted(
                    name for name, data in originals.items() if data is None
                ),
            },
            indent=2,
        )
        + "\n"
    )
    print("Update backups: " + str(backup.relative_to(target)), flush=True)
    pending = safe_tool_path(target, ".tools/update-pending.json")
    with pending.open("x") as marker:
        marker.write(json.dumps({"backup": str(backup.relative_to(target))}) + "\n")
    try:
        for name, content in edits.items():
            path = safe_tool_path(target, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(  # noqa: S603 -- bundled installer, fixed argv; no shell
            [sys.executable, str(target / ".quality/install.py"), "--update-tools"],
            cwd=target,
            check=True,
        )
        if set(config["languages"]) - {"python"} or config["hook_manager"] == "husky":
            subprocess.run(  # noqa: S603 -- local formatter; only edited suite JSON files
                [
                    str(target / "node_modules/.bin/prettier"),
                    "--write",
                    *(
                        name
                        for name in (".quality/config.json", ".quality/baseline.json")
                        if name in edits
                    ),
                ],
                cwd=target,
                check=True,
            )
        environment = dict(os.environ)
        environment["QUALITY_UPDATE_CHECK"] = "1"
        environment["PATH"] = (
            str(target / ".venv/bin")
            + ":"
            + str(target / "node_modules/.bin")
            + ":"
            + str(target / ".tools/bin")
            + ":"
            + environment.get("PATH", "")
        )
        if config["hook_manager"] == "pre-commit":
            commands = [
                [str(target / ".venv/bin/pre-commit"), "run", "--all-files"],
                [
                    str(target / ".venv/bin/pre-commit"),
                    "run",
                    "--hook-stage",
                    "pre-push",
                    "--all-files",
                ],
            ]
        else:
            git = shutil.which("git")
            if git is None:
                raise FileNotFoundError("Git is required")
            _, directory = inspect_tooling(target, git)
            shell = shutil.which("sh")
            if shell is None:
                raise FileNotFoundError("sh is required for existing hook validation")
            commands = [
                [
                    shell,
                    str(safe_tool_path(target, (directory / stage).relative_to(target).as_posix())),
                ]
                for stage in ("pre-commit", "pre-push")
            ]
        for command in commands:
            subprocess.run(  # noqa: S603 -- local hook executables, fixed argv; no shell expansion
                command,
                cwd=target,
                env=environment,
                check=True,
            )
        for name, content in edits.items():
            actual = safe_tool_path(target, name).read_text()
            unchanged = (
                parse_jsonc(actual) == parse_jsonc(content)
                if name.endswith(".json")
                else actual == content
            )
            if not unchanged:
                raise ValueError("Project checks altered a managed update file: " + name)
    except (OSError, ValueError, subprocess.CalledProcessError, KeyboardInterrupt):
        for name, content in originals.items():
            path = safe_tool_path(target, name)
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(content)
        pending.unlink()
        print(
            "Update failed; manifests, locks and runtime restored. Installed environments may "
            "have changed. Restore them with python3 .quality/install.py --restore-tools "
            "(older installers: python3 .quality/install.py). Backups: " + str(backup),
            file=sys.stderr,
        )
        raise
    pending.unlink()
    print("Tooling updated and project checks passed. Review the Git diff and commit explicitly.")


def scaffold(
    target: Path,
    languages: set[str],
    *,
    existing: bool = False,
    policy: str | None = None,
    inventory: list[dict[str, Any]] | None = None,
    hook_directory: Path | None = None,
) -> None:
    python = "python" in languages
    node = bool(languages - {"python"})
    react = "react" in languages
    typescript = "typescript" in languages
    files: dict[str, str] = {}
    inventory = inventory or []
    hook_directory = hook_directory or target / ".git/hooks"
    hook_manager = "pre-commit" if python else "husky"
    if policy == "amend":
        if any((target / name).exists() for name in integration.CONFIGS["pre-commit"]):
            hook_manager = "pre-commit"
        elif (target / ".husky").exists():
            hook_manager = "husky"
        elif any(entry["kind"] == "hook" for entry in inventory):
            hook_manager = "existing"
    node = node or hook_manager == "husky"

    def add(name: str, content: str) -> None:
        path = target / name
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != target):
            raise ValueError("Refusing to write through a symlink: " + name)
        files[name] = content

    def merge(name: str, values: dict[str, Any]) -> None:
        path = target / name
        if name == "package.json" and path.exists():
            json.loads(path.read_text())
        add(name, merge_jsonc(path.read_text() if path.exists() else "{}\n", values))

    manifest = json.loads((SOURCE / "templates/quality/versions.json").read_text())
    manifest["languages"] = sorted(languages)
    manifest["semgrep_config"] = "p/security-audit"
    manifest["severity"] = ["HIGH", "CRITICAL"]
    manifest["docker"] = {"builds": [], "images": []}
    manifest["hook_manager"] = hook_manager
    manifest["tooling_policy"] = policy or "amend"
    manifest["node_projects"] = sorted(
        {
            ".",
            *(
                entry["scope"]
                for entry in inventory
                if entry["kind"] in {"package", "eslint", "prettier", "typescript"}
            ),
        }
    )
    manifest["python_projects"] = sorted(
        {".", *(entry["scope"] for entry in inventory if entry["kind"] in {"python", "pyright"})}
    )
    manifest["typescript_configs"] = sorted(
        {"tsconfig.json", *(entry["path"] for entry in inventory if entry["kind"] == "typescript")}
    )
    if (target / ".quality/config.json").exists():
        existing_config = parse_jsonc((target / ".quality/config.json").read_text())
        if existing_config.get("python") != manifest["python"]:
            raise ValueError("Python tool version changes require a reviewed lockfile update")
    config_path = target / ".quality/config.json"
    if config_path.exists() and parse_jsonc(config_path.read_text()).get("languages") != sorted(
        languages
    ):
        raise ValueError(
            "Changing an existing bootstrap profile requires a reviewed manual migration"
        )
    # Preserve deliberate scanner policy/build targets on reruns.
    if config_path.exists():
        current = parse_jsonc(config_path.read_text())
        for key in ("semgrep_config", "severity", "docker"):
            if key in current:
                manifest[key] = current[key]
    if policy and (target / ".quality/config.json").exists():
        current = parse_jsonc((target / ".quality/config.json").read_text())
        current.update(
            {
                key: manifest[key]
                for key in (
                    "hook_manager",
                    "tooling_policy",
                    "node_projects",
                    "python_projects",
                    "typescript_configs",
                )
            }
        )
        add(".quality/config.json", merge_jsonc(json.dumps(current, indent=2), manifest) + "\n")
    else:
        merge(".quality/config.json", manifest)
    for name in ("run.py", "install.py", "jsonc.py"):
        add(".quality/" + name, (SOURCE / "templates/quality" / name).read_text())
    add(
        ".quality/env.sh",
        'export PATH="$(git rev-parse --show-toplevel)/.venv/bin:'
        "$(git rev-parse --show-toplevel)/node_modules/.bin:"
        '$(git rev-parse --show-toplevel)/.tools/bin:$PATH"\n',
    )
    requirements = [
        "pip==" + manifest["python"]["pip"],
        "semgrep==" + manifest["python"]["semgrep"],
        "check-jsonschema==" + manifest["python"]["check-jsonschema"],
    ]
    if python:
        requirements.extend(name + "==" + manifest["python"][name] for name in ("ruff", "pyright"))
    if hook_manager == "pre-commit":
        requirements.append("pre-commit==" + manifest["python"]["pre-commit"])
    add(".quality/requirements.in", "\n".join(requirements) + "\n")
    ignore = (target / ".gitignore").read_text() if (target / ".gitignore").exists() else ""
    patterns = [
        ".tools/",
        ".venv/",
        "node_modules/",
        "__pycache__/",
        ".ruff_cache/",
        ".pytest_cache/",
        "dist/",
        "build/",
        "coverage/",
        ".env",
        ".env.*",
        "!.env.example",
        "!.vscode/",
        "!.vscode/settings.json",
        "!.vscode/extensions.json",
        "!.vscode/tasks.json",
    ]
    missing = [p for p in patterns if p not in ignore.splitlines()]
    add(".gitignore", ignore.rstrip() + "\n" + "\n".join(missing) + "\n" if missing else ignore)
    runner = ".venv/bin/python .quality/run.py"
    recommendations = ["redhat.vscode-yaml"]
    settings: dict[str, Any] = {
        "files.exclude": {"**/.tools": True, "**/.venv": True, "**/node_modules": True},
        "yaml.validate": True,
    }
    if python:
        python_template = (SOURCE / "pyproject.toml").read_text()
        add(
            "pyproject.toml",
            python_template.replace('include = ["src", "tests"]', 'include = ["."]')
            .replace(
                '  ".git",',
                '  ".git",\n  ".quality",\n  ".tools",'
                if '".quality"' not in python_template
                else '  ".git",',
            )
            .replace(
                'exclude = [".venv", "build", "dist"]',
                'exclude = [".venv", ".quality", ".tools", "node_modules", "build", "dist"]',
            )
            .split("\n[tool.pytest.ini_options]", 1)[0]
            + "\n",
        )
        settings.update(
            {
                "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
                "python.analysis.typeCheckingMode": "standard",
                "ruff.path": ["${workspaceFolder}/.venv/bin/ruff"],
                "ruff.importStrategy": "fromEnvironment",
                "[python]": {
                    "editor.defaultFormatter": "charliermarsh.ruff",
                    "editor.formatOnSave": True,
                    "editor.codeActionsOnSave": {
                        "source.fixAll.ruff": "explicit",
                        "source.organizeImports.ruff": "explicit",
                    },
                },
            }
        )
        recommendations.extend(
            ["ms-python.python", "ms-python.vscode-pylance", "charliermarsh.ruff"]
        )
    if node:
        dependencies = {
            k: manifest["node"][k]
            for k in ("eslint", "@eslint/js", "globals", "prettier", "typescript", "@types/node")
        }
        if typescript:
            dependencies["typescript-eslint"] = manifest["node"]["typescript-eslint"]
        if react:
            for name in (
                "@eslint-react/eslint-plugin",
                "eslint-plugin-react-hooks",
                "eslint-plugin-jsx-a11y-x",
                "@types/react",
                "@types/react-dom",
            ):
                dependencies[name] = manifest["node"][name]
        if hook_manager == "husky":
            dependencies["husky"] = manifest["node"]["husky"]
        merge(
            "package.json",
            {
                "private": True,
                "devDependencies": dependencies,
                "overrides": {
                    name: overrides
                    for name, overrides in manifest["node_overrides"].items()
                    if name in dependencies
                },
                "scripts": {
                    "quality": runner + " check",
                    "quality:format": runner + " format",
                    "quality:types": runner + " types",
                    "quality:security": runner + " security",
                    "quality:docker": runner + " docker",
                },
            },
        )
        add(".prettierrc.json", (SOURCE / "prettierrc.json").read_text())
        add(
            ".prettierignore",
            "\n".join(
                [
                    ".git",
                    ".venv",
                    ".tools",
                    "node_modules",
                    "dist",
                    "build",
                    "coverage",
                    "package-lock.json",
                    ".env",
                    ".env.*",
                ]
            )
            + "\n",
        )
        eslint = ['import js from "@eslint/js";', 'import globals from "globals";']
        configs = ["js.configs.recommended"]
        if typescript:
            eslint.append('import tseslint from "typescript-eslint";')
            configs.append("...tseslint.configs.recommended")
        if react:
            eslint.extend(
                [
                    'import react from "@eslint-react/eslint-plugin";',
                    'import hooks from "eslint-plugin-react-hooks";',
                    'import a11y from "eslint-plugin-jsx-a11y-x";',
                ]
            )
            configs.extend(
                [
                    'react.configs["recommended-typescript"]'
                    if typescript
                    else "react.configs.recommended",
                    "a11y.configs.recommended",
                ]
            )
        eslint.append("export default [")
        eslint.append(
            '  { ignores: [".venv/**", ".tools/**", "node_modules/**", "dist/**", "build/**"] },'
        )
        eslint.append('  { files: ["**/*.{js,mjs,cjs,jsx,ts,tsx,mts,cts}"], extends: [')
        eslint.append("    " + ", ".join(configs) + "],")
        eslint.append("    languageOptions: { globals: { ...globals.browser, ...globals.node } },")
        if react:
            eslint.append('    plugins: { "react-hooks": hooks },')
            eslint.append(
                "    rules: { ...hooks.configs.recommended.rules, "
                '"@eslint-react/no-missing-key": "error" },'
            )
        eslint.extend(["  },", "];", ""])
        # Scope shared presets so JSX rules do not leak into configuration files.
        eslint.insert(0, 'import { defineConfig } from "eslint/config";')
        add(
            "eslint.config.mjs",
            "\n".join(eslint)
            .replace("export default [", "export default defineConfig([")
            .replace("\n];", "\n]);"),
        )
        tsconfig = {
            "compilerOptions": {
                "allowJs": True,
                "checkJs": True,
                "noEmit": True,
                "strict": True,
                "target": "ES2022",
                "module": "ESNext",
                "moduleResolution": "Bundler",
                "jsx": "react-jsx" if react else "preserve",
                "skipLibCheck": True,
            },
            "include": ["**/*"] if existing else ["src/**/*"],
            "exclude": ["node_modules", ".venv", ".tools", ".quality", "dist", "build", "vendor"],
        }
        tsconfig_path = target / "tsconfig.json"
        if tsconfig_path.exists():
            parse_jsonc(tsconfig_path.read_text())
            add("tsconfig.json", tsconfig_path.read_text())
        else:
            merge("tsconfig.json", tsconfig)
        recommendations.extend(["dbaeumer.vscode-eslint", "esbenp.prettier-vscode"])
        settings["typescript.tsdk"] = "node_modules/typescript/lib"
        settings["eslint.useFlatConfig"] = True
        for language in (
            "javascript",
            "javascriptreact",
            "typescript",
            "typescriptreact",
            "json",
            "jsonc",
            "yaml",
            "markdown",
            "css",
            "html",
        ):
            settings[f"[{language}]"] = {
                "editor.defaultFormatter": "esbenp.prettier-vscode",
                "editor.formatOnSave": True,
            }
            if language in {"javascript", "javascriptreact", "typescript", "typescriptreact"}:
                settings[f"[{language}]"]["editor.codeActionsOnSave"] = {
                    "source.fixAll.eslint": "explicit"
                }
        settings["prettier.prettierPath"] = "./node_modules/prettier"
    merge(".vscode/settings.json", settings)
    merge(".vscode/extensions.json", {"recommendations": recommendations})
    tasks = [
        {
            "label": "Quality: " + name,
            "type": "process",
            "command": "${workspaceFolder}/.venv/bin/python",
            "args": ["${workspaceFolder}/.quality/run.py", name],
            "options": {"cwd": "${workspaceFolder}"},
            "problemMatcher": [],
        }
        for name in ("check", "format", "lint", "types", "schemas", "security", "docker")
    ]
    tasks_path = target / ".vscode/tasks.json"
    # Labels are task identities, not the full task object.
    if tasks_path.exists():
        parsed = parse_jsonc(tasks_path.read_text())
        labels = {t.get("label") for t in parsed.get("tasks", [])}
        tasks = [t for t in tasks if t["label"] not in labels]
    merge(".vscode/tasks.json", {"version": "2.0.0", "tasks": tasks})
    schema = {
        "type": "object",
        "properties": {
            "recommendations": {"type": "array", "items": {"type": "string"}},
            "unwantedRecommendations": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    add(".quality/extensions.schema.json", json.dumps(schema, indent=2) + "\n")
    merge(
        ".quality/schemas.json",
        {
            "checks": [
                {"schema": ".quality/extensions.schema.json", "files": [".vscode/extensions.json"]},
            ]
        },
    )
    add(
        ".gitleaks.toml",
        "[extend]\nuseDefault = true\n\n[allowlist]\n"
        "paths = ['''(^|/)(\\.quality|\\.venv|\\.tools|node_modules|\\.git)/''']\n",
    )
    add(".semgrepignore", ".quality/\n.venv/\n.tools/\nnode_modules/\ndist/\nbuild/\n")
    if hook_manager == "pre-commit":
        add(".pre-commit-config.yaml", integration.pre_commit_config())
    elif hook_manager == "husky":
        for stage, command in (("pre-commit", "commit"), ("pre-push", "push")):
            add(".husky/" + stage, "#!/bin/sh\nexec " + runner + " " + command + "\n")
    agents_path = target / "AGENTS.md"
    agents = agents_path.read_text() if agents_path.exists() else (SOURCE / "AGENTS.md").read_text()
    marker = "## Project-local quality suite"
    if marker not in agents:
        agents += (
            "\n" + marker + "\n\n"
            "All rules above remain applicable. "
            "Use the following local commands for this profile:\n\n"
            "- `.venv/bin/python .quality/run.py check`: "
            "formatting, lint, types, schemas, Docker.\n"
            "- `.venv/bin/python .quality/run.py security`: Gitleaks, Semgrep, Trivy.\n"
            "- `.venv/bin/python .quality/run.py commit`: fast checks and staged secret scanning.\n"
            "- `.venv/bin/python .quality/run.py push`: full checks and security scanning.\n\n"
            "Python/mixed repositories use `.venv/bin/pre-commit`; Node-only repositories use "
            "Husky instead. For Node-only repositories, the commit/push commands above replace "
            "the two pre-commit framework validation commands; do not add a second hook manager.\n"
            "Use `.tools/bin/trivy` instead of a globally installed Trivy. "
            "The security command applies project exclusions and configured severity thresholds.\n"
            "Docker runtime checks stay configured. Missing Docker CLI, Compose plugin, or "
            "accessible daemon is an explicit dependency skip, not a successful validation. "
            "Invalid configurations and failed builds/scans must fail. Do not install Docker "
            "or change build targets without approval.\n"
            "See [setup and usage](QUALITY.md) for tool responsibilities and commands.\n"
        )
    add("AGENTS.md", agents)
    if policy and "## Existing-tool integration" not in agents:
        agents += (
            "\n## Existing-tool integration\n\n"
            "The hook manager recorded in `.quality/config.json` is authoritative. "
            "Amended projects may retain an existing pre-commit, Husky, or shell-hook setup "
            "instead of the profile default. Do not install a second active hook manager.\n"
            "For a recorded manager other than pre-commit, the commit/push commands above "
            "are the required equivalents of the two original pre-commit validation commands.\n"
            "Amendment retains existing analyzer rules and tests; replacement standardizes "
            "recognized scanning configuration, not application metadata or tests. "
            "Keep the configuration/hook backups until the migration has been validated.\n"
        )
        add("AGENTS.md", agents)
    usage_path = target / "QUALITY.md"
    add(
        "QUALITY.md",
        usage_path.read_text()
        if usage_path.exists()
        else (SOURCE / "templates/quality/USAGE.md").read_text(),
    )
    retired: set[str] = set()
    if policy:
        template = files.get("pyproject.toml", (SOURCE / "pyproject.toml").read_text())
        node_dependencies = (
            {
                name: manifest["node"][name]
                for name in json.loads(files["package.json"]).get("devDependencies", {})
                if name in manifest["node"]
            }
            if "package.json" in files
            else {}
        )
        # Existing pre-commit-managed hooks invoke the amended YAML; don't also wrap them.
        effective = [
            entry
            for entry in inventory
            if not (
                policy == "amend"
                and hook_manager == "pre-commit"
                and entry["kind"] == "hook"
                and ".git" in Path(entry["path"]).parts
            )
        ]
        files, retired = integration.plan_integration(
            target, files, effective, policy, node_dependencies, template, hook_directory
        )
    scanner_config = parse_jsonc(files[".quality/config.json"])
    scanner_config["managed_tooling"] = sorted(
        name for name in {*(entry["path"] for entry in inventory), *files} if name not in retired
    )
    scanner_config["scanner_configs"] = {
        kind: sorted(
            name
            for name in {*(entry["path"] for entry in inventory), *files}
            if name not in retired and Path(name).name in integration.CONFIGS[kind]
        )
        for kind in ("gitleaks", "trivy", "hadolint", "semgrep")
    }
    files[".quality/config.json"] = json.dumps(scanner_config, indent=2) + "\n"
    if not (target / ".quality/config.json").exists():
        files[".quality/baseline.json"] = (
            json.dumps(tooling_baseline(target, files, scanner_config), indent=2) + "\n"
        )

    def equivalent(name: str, content: str) -> bool:
        existing = (target / name).read_text()
        if existing == content:
            return True
        if name.endswith(".json"):
            return parse_jsonc(existing) == parse_jsonc(content)
        if Path(name).name == "eslint.config.mjs":
            # A formatter may change whitespace/quote style, but not the generated syntax.
            import ast

            def normalize(text: str) -> list[str] | None:
                pattern = r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[A-Za-z_$][\w$]*|\d+|[^\s]"""
                items = []
                for match in re.finditer(pattern, text):
                    item = match.group()
                    if item[0] in "'\"":
                        try:
                            item = repr(ast.literal_eval(item))
                        except (SyntaxError, ValueError):
                            # Unfamiliar JS/comments cannot prove formatter equivalence.
                            return None
                    items.append(item)
                return [
                    item
                    for i, item in enumerate(items)
                    if not (item == "," and i + 1 < len(items) and items[i + 1] in ("}", "]", ")"))
                ]

            normalized = normalize(existing)
            return normalized is not None and normalized == normalize(content)
        return False

    conflicts = [
        name
        for name, content in files.items()
        if (target / name).exists()
        and not equivalent(name, content)
        and not policy
        and name
        not in {
            ".gitignore",
            "package.json",
            "tsconfig.json",
            ".quality/config.json",
            ".quality/schemas.json",
            ".vscode/settings.json",
            ".vscode/extensions.json",
            ".vscode/tasks.json",
            "AGENTS.md",
        }
    ]
    # Different conventional configs are also conflicts, even under a different filename.
    if node and not policy:
        conflicts.extend(
            name
            for name in (
                "eslint.config.js",
                "eslint.config.cjs",
                ".eslintrc.json",
                ".prettierrc",
                "prettier.config.js",
                "prettier.config.mjs",
                "prettierrc.json",
            )
            if (target / name).exists()
        )
    if conflicts:
        raise ValueError("Refusing to replace existing configuration: " + ", ".join(conflicts))
    for name in set(files) | retired:
        if any(path.is_symlink() for path in (target / name, *(target / name).parents)):
            raise ValueError("Refusing to write through a symlink: " + name)
    for name in files:
        path = target / name
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("Refusing to write through a symlink: " + name)
    changed = {
        name
        for name, content in files.items()
        if (target / name).exists() and (target / name).read_text() != content
    }
    preserved_hooks = {
        entry["path"] for entry in inventory if policy == "replace" and entry["kind"] == "hook"
    }
    if policy and (changed or retired or preserved_hooks):
        backup = target / ".tools/backups" / uuid.uuid4().hex
        if any(path.is_symlink() for path in (backup, *backup.parents)):
            raise ValueError("Refusing to back up through a symlink")
        for name in sorted(changed | retired | preserved_hooks):
            original = target / name
            if not original.is_file() or any(
                path.is_symlink() for path in (original, *original.parents)
            ):
                raise ValueError("Cannot safely back up configuration: " + name)
        for name in sorted(changed | retired | preserved_hooks):
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name in retired:
                (target / name).rename(destination)
            else:
                shutil.copy2(target / name, destination)
        print("Configuration/hook backups: " + str(backup.relative_to(target)))
    for name, content in files.items():
        path = target / name
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("Refusing to write through a symlink: " + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or not equivalent(name, content):
            path.write_text(content)
            if (
                any(entry["path"] == name and entry["kind"] == "hook" for entry in inventory)
                or hook_manager == "existing"
                and path.parent == hook_directory
            ):
                path.chmod(0o755)
            print("Wrote " + name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a project-local quality suite into a Git repository."
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="Destination Git repository root; prompted for when omitted",
    )
    parser.add_argument(
        "--languages", help="Override detection: python,javascript,typescript,react"
    )
    parser.add_argument(
        "--existing-tools",
        choices=["leave", "amend", "replace"],
        help="Existing analyzer/hook policy; required for unattended reconciliation",
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Report detected languages and evidence; do not prompt or write files",
    )
    parser.add_argument(
        "--configure-only",
        action="store_true",
        help="Generate files; install later with .quality/install.py",
    )
    parser.add_argument(
        "--update-tooling",
        action="store_true",
        help="Update owned tooling from this checkout; preserve application settings and hooks",
    )
    parser.add_argument(
        "--preview", action="store_true", help="Preview a tooling update without writes"
    )
    parser.add_argument("--yes", action="store_true", help="Confirm the displayed tooling update")
    parser.add_argument(
        "--adopt-tooling",
        action="store_true",
        help="Explicitly adopt exact matching tooling in projects predating ownership records",
    )
    args = parser.parse_args()
    if args.update_tooling and (
        args.languages or args.existing_tools or args.detect_only or args.configure_only
    ):
        parser.error("--update-tooling cannot be combined with setup or detection options")
    if not args.update_tooling and (args.preview or args.yes or args.adopt_tooling):
        parser.error("--preview, --yes and --adopt-tooling require --update-tooling")
    if sys.version_info < (3, 11):  # noqa: UP036 -- explicit bootstrap prerequisite diagnostic
        parser.error("Python 3.11+ is required")
    if args.target is None:
        if args.detect_only or not sys.stdin.isatty():
            parser.error("--target is required for detection-only or noninteractive runs")
        try:
            destination = input("Path to the Git repository to set up: ").strip()
        except (EOFError, KeyboardInterrupt):
            parser.error("Repository selection cancelled; no changes made")
        if not destination:
            parser.error("A destination repository path is required; no changes made")
        args.target = Path(destination)
    target = args.target.expanduser().resolve()
    if not target.is_dir():
        parser.error("Destination must be an existing directory; initialize it with git init first")
    git = shutil.which("git")
    if git is None:
        parser.error("Git is required")
    result = subprocess.run(  # noqa: S603 -- resolved Git executable; argv, never a shell
        [git, "-C", str(target), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or Path(result.stdout.strip()).resolve() != target:
        parser.error("--target must be the root of an existing Git working tree")
    if not args.detect_only:
        print("Destination repository: " + str(target), flush=True)
    try:
        if args.update_tooling:
            update_tooling(target, preview=args.preview, yes=args.yes, adopt=args.adopt_tooling)
            return 0
        if (
            not args.detect_only
            and not args.languages
            and not args.existing_tools
            and not args.configure_only
            and (target / ".quality/config.json").exists()
            and sys.stdin.isatty()
        ):
            print("1. Existing setup / restore\n2. Update tooling from this checkout")
            action = input("Configured project action [1/2]: ").strip()
            if action == "2":
                update_tooling(target)
                return 0
            if action != "1":
                raise ValueError("Select 1 (setup) or 2 (update tooling)")
        if args.detect_only:
            evidence, nonempty = inspect_repository(target, git)
            inventory, _ = inspect_tooling(target, git)
            print(
                json.dumps(
                    {
                        "existing": nonempty,
                        "languages": sorted(evidence),
                        "evidence": evidence,
                        "tooling": inventory,
                    },
                    indent=2,
                )
            )
            return 0
        inventory, hook_directory = inspect_tooling(target, git)
        policy = choose_tooling_policy(target, inventory, args.existing_tools)
        if policy == "leave":
            print("Leaving existing tooling and checks in place; no files or hooks changed.")
            return 0
        if not hook_directory.resolve().is_relative_to(target) or any(
            entry.get("external") for entry in inventory
        ):
            raise ValueError("Active hooks are outside this repository; reconcile them manually")
        languages, existing, _ = select_languages(target, git, args.languages)
        if any(entry["kind"] in {"python", "pre-commit"} for entry in inventory):
            arguments = [
                "--target",
                str(target),
                "--languages",
                ",".join(sorted(languages)),
                "--existing-tools",
                policy,
            ]
            if args.configure_only:
                arguments.append("--configure-only")
            result = ensure_configuration_parsers(target, arguments)
            if result is not None:
                return result
        scaffold(
            target,
            languages,
            existing=existing,
            policy=policy,
            inventory=inventory,
            hook_directory=hook_directory,
        )
        if not args.configure_only:
            subprocess.run(  # noqa: S603 -- fixed interpreter and generated installer; no shell
                [sys.executable, str(target / ".quality/install.py")], cwd=target, check=True
            )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Bootstrap failed: {error}", file=sys.stderr)
        return 1
    print(
        "Configured "
        + str(target)
        + (". Installation deferred." if args.configure_only else ". Tools installed.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
