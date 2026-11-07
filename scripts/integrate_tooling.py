from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

CONFIGS = {
    "eslint": {
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
        "eslint.config.mts",
        "eslint.config.cts",
        ".eslintrc",
        ".eslintrc.json",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.yml",
        ".eslintrc.yaml",
    },
    "prettier": {
        ".prettierrc",
        ".prettierrc.json",
        ".prettierrc.yaml",
        ".prettierrc.yml",
        ".prettierrc.toml",
        ".prettierrc.js",
        ".prettierrc.cjs",
        ".prettierrc.mjs",
        "prettier.config.js",
        "prettier.config.cjs",
        "prettier.config.mjs",
        "prettier.config.ts",
        "prettier.config.mts",
    },
    "ruff": {"ruff.toml", ".ruff.toml"},
    "pyright": {"pyrightconfig.json"},
    "gitleaks": {".gitleaks.toml", "gitleaks.toml"},
    "trivy": {"trivy.yaml", "trivy.yml", ".trivy.yaml", ".trivyignore", ".trivyignore.yaml"},
    "hadolint": {".hadolint.yaml", ".hadolint.yml"},
    "semgrep": {".semgrep.yml", ".semgrep.yaml", ".semgrepignore"},
    "pre-commit": {".pre-commit-config.yaml", ".pre-commit-config.yml"},
    "prettier-ignore": {".prettierignore"},
}
NODE_TOOLS = {
    "eslint",
    "@eslint/js",
    "typescript-eslint",
    "prettier",
    "husky",
    "eslint-plugin-react",
    "eslint-plugin-react-hooks",
    "eslint-plugin-jsx-a11y",
    "@eslint-react/eslint-plugin",
    "eslint-plugin-jsx-a11y-x",
    "lint-staged",
    "@biomejs/biome",
}


def merge_python(text: str, template: str, policy: str) -> str:
    import tomlkit

    document = tomlkit.parse(text)
    desired = tomlkit.parse(template)["tool"]
    if "tool" not in document:
        document["tool"] = tomlkit.table()
    for name in ("ruff", "pyright"):
        if policy == "replace" or name not in document["tool"]:
            document["tool"][name] = desired[name]
    return tomlkit.dumps(document)


def merge_pre_commit(text: str, template: str) -> str:
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    yaml = YAML()
    try:
        document = yaml.load(text)
        desired = yaml.load(template)
    except YAMLError as error:
        raise ValueError(f"Invalid pre-commit YAML: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("repos"), list):
        raise ValueError("pre-commit configuration must contain a repos list")
    for repo in document["repos"]:
        if not isinstance(repo, dict) or not isinstance(repo.get("hooks"), list):
            raise ValueError("Each pre-commit repository must contain a hooks list")
        if any(
            not isinstance(hook, dict) or not isinstance(hook.get("id"), str)
            for hook in repo["hooks"]
        ):
            raise ValueError("Each pre-commit hook must be a mapping with a string id")
    identifiers = {hook.get("id") for repo in document["repos"] for hook in repo.get("hooks", [])}
    for repo in desired["repos"]:
        repo["hooks"] = [hook for hook in repo["hooks"] if hook["id"] not in identifiers]
        if repo["hooks"]:
            document["repos"].append(repo)
    output = io.StringIO()
    yaml.dump(document, output)
    return output.getvalue()


def pre_commit_config() -> str:
    hooks = []
    for action, stage in (("commit", "pre-commit"), ("push", "pre-push")):
        hooks.append(
            f"      - id: quality-{action}\n        name: Project quality ({action})\n"
            f"        entry: .venv/bin/python .quality/run.py {action}\n"
            "        language: system\n        pass_filenames: false\n"
            f"        always_run: true\n        stages: [{stage}]\n"
        )
    return "repos:\n  - repo: local\n    hooks:\n" + "".join(hooks)


def amend_shell_hook(text: str, command: str) -> str:
    marker = "# project-local-quality-suite"
    if marker in text:
        return text
    first, separator, remainder = text.partition("\n")
    if first.startswith("#!") and not re.search(r"(?:/|\s)(?:ba|da|k|z)?sh(?:\s|$)", first):
        raise ValueError("Cannot safely amend a non-shell hook; use replace or leave")
    insertion = (
        marker
        + '\n. "$(git rev-parse --show-toplevel)/.quality/env.sh" || exit $?\n'
        + command
        + " || exit $?\n"
    )
    if first.startswith("#!"):
        return first + separator + insertion + remainder
    return "#!/bin/sh\n" + insertion + text


def plan_integration(
    target: Path,
    files: dict[str, str],
    inventory: list[dict[str, Any]],
    policy: str,
    node_dependencies: dict[str, str],
    python_template: str,
    hook_directory: Path,
) -> tuple[dict[str, str], set[str]]:
    """Produce all edits before the caller backs up or writes any configuration."""
    edits = dict(files)
    retire: set[str] = set()
    scopes = {
        entry["scope"] for entry in inventory if entry["kind"] in {"package", "eslint", "prettier"}
    }
    node_projects = {".", *scopes}
    for scope in sorted(node_projects):
        prefix = "" if scope == "." else scope + "/"
        package_name = prefix + "package.json"
        directory = target / prefix
        if any(
            (directory / lock).exists()
            for lock in ("pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb")
        ):
            raise ValueError(
                "Non-npm lockfile requires explicit package-manager migration: " + package_name
            )
        configs = [entry for entry in inventory if entry["scope"] == scope]
        for tool, canonical in (("eslint", "eslint.config.mjs"), ("prettier", ".prettierrc.json")):
            existing = [entry for entry in configs if entry["kind"] == tool]
            package_path = target / prefix / "package.json"
            package_config = False
            if package_path.exists():
                package = json.loads(package_path.read_text())
                manager = package.get("packageManager", "npm")
                if not isinstance(manager, str) or not (
                    manager == "npm" or manager.startswith("npm@")
                ):
                    raise ValueError(
                        "Non-npm project requires package-manager-specific integration: "
                        + package_name
                    )
                patterns = package.get("workspaces", [])
                if not isinstance(patterns, list) or any(
                    not isinstance(pattern, str) for pattern in patterns
                ):
                    raise ValueError(
                        "npm workspaces must be an array of directory patterns: " + package_name
                    )
                if any(any(character in pattern for character in "{}()") for pattern in patterns):
                    raise ValueError(
                        "Workspace brace/extglob patterns require manual integration: "
                        + package_name
                    )
                package_config = ("eslintConfig" if tool == "eslint" else "prettier") in package
                if policy == "amend" and tool == "eslint":
                    legacy = package_config or any(
                        Path(entry["path"]).name.startswith(".eslintrc") for entry in existing
                    )
                    version = package.get("devDependencies", {}).get(
                        "eslint", package.get("dependencies", {}).get("eslint")
                    )
                    if legacy and (
                        not isinstance(version, str)
                        or not re.fullmatch(r"[~^]?[1-9]\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version)
                    ):
                        raise ValueError(
                            "Legacy ESLint configuration needs an explicit pre-10 version "
                            "(exact, caret or tilde); "
                            "choose replace for ESLint 10 or migrate to flat config: "
                            + package_name
                        )
            generated = files.get(canonical)
            if generated is None:
                continue
            if (
                policy == "amend"
                and tool == "eslint"
                and not package_path.exists()
                and any(Path(entry["path"]).name.startswith(".eslintrc") for entry in existing)
            ):
                raise ValueError(
                    "Legacy ESLint configuration has no declared compatible dependency; "
                    "choose replace for the supported baseline: " + prefix
                )
            if policy == "amend" and (existing or package_config):
                edits.pop(prefix + canonical, None)
                if scope == ".":
                    edits.pop(canonical, None)
            elif scope == "." or existing or scope in scopes:
                if policy == "amend" and tool == "eslint" and package_path.exists():
                    existing_package = json.loads(package_path.read_text())
                    version = existing_package.get("devDependencies", {}).get(
                        "eslint"
                    ) or existing_package.get("dependencies", {}).get("eslint")
                    if version and not re.match(r"^[~^]?10\.", version):
                        raise ValueError(
                            "Adding baseline ESLint configuration requires ESLint 10; "
                            "use replace or supply the existing analyzer configuration: " + prefix
                        )
                edits[prefix + canonical] = generated
                retire.update(
                    entry["path"] for entry in existing if entry["path"] != prefix + canonical
                )
        package_path = target / package_name
        if package_path.exists():
            package = json.loads(package_path.read_text())
            if scope == "." and package_name in files:
                generated = json.loads(files[package_name])
                package.setdefault("private", generated.get("private", True))
                scripts = package.setdefault("scripts", {})
                for name, command in generated.get("scripts", {}).items():
                    scripts.setdefault(name, command)
            dev = package.setdefault("devDependencies", {})
            if not isinstance(dev, dict):
                raise ValueError(f"{package_name}: devDependencies must be an object")
            dependencies = dict(node_dependencies)
            if scope != ".":
                dependencies.pop("husky", None)
            if policy == "amend" and (
                any(c["kind"] == "eslint" for c in configs) or "eslintConfig" in package
            ):
                version = dev.get("eslint", package.get("dependencies", {}).get("eslint"))
                if version and not re.match(r"^[~^]?10\.", version):
                    print(
                        "Preserving existing ESLint "
                        + version
                        + " in "
                        + package_name
                        + "; review support status or choose replace for the supported baseline."
                    )
                dependencies = {
                    name: value
                    for name, value in dependencies.items()
                    if name
                    in {
                        "eslint",
                        "prettier",
                        "typescript",
                        "@types/node",
                        "@types/react",
                        "@types/react-dom",
                        "husky",
                    }
                }
            for name, value in dependencies.items():
                if policy == "replace":
                    if name in package.get("dependencies", {}):
                        package["dependencies"][name] = value
                    else:
                        dev[name] = value
                elif name not in package.get("dependencies", {}):
                    dev.setdefault(name, value)
            if policy == "replace":
                for name in ("eslint-plugin-react", "eslint-plugin-jsx-a11y"):
                    dev.pop(name, None)
                    package.get("dependencies", {}).pop(name, None)
                package.pop("eslintConfig", None)
                package.pop("prettier", None)
            baseline_overrides = json.loads(files.get("package.json", "{}")).get("overrides", {})
            if policy == "replace" or not (
                any(c["kind"] == "eslint" for c in configs) or "eslintConfig" in package
            ):
                overrides = package.setdefault("overrides", {})
                for name, value in baseline_overrides.items():
                    if name in dependencies:
                        if policy == "replace":
                            overrides[name] = value
                        else:
                            overrides.setdefault(name, value)
            edits[package_name] = json.dumps(package, indent=2) + "\n"
    for entry in sorted(inventory, key=lambda item: item["kind"] != "python"):
        name = entry["path"]
        scope = entry["scope"]
        prefix = "" if scope == "." else scope + "/"
        path = target / name
        kind = entry["kind"]
        if kind == "python":
            edits[name] = merge_python(path.read_text(), python_template, policy)
        elif kind in {"ruff", "pyright"}:
            if policy == "replace":
                retire.add(name)
                project = prefix + "pyproject.toml"
                if project not in edits:
                    original = (target / project).read_text() if (target / project).exists() else ""
                    edits[project] = merge_python(original, python_template, policy)
            elif prefix + "pyproject.toml" in edits:
                # Standalone configs remain authoritative; do not add competing tool tables.
                project = prefix + "pyproject.toml"
                original = (target / project).read_text() if (target / project).exists() else ""
                import tomlkit

                document = tomlkit.parse(edits[project])
                original_doc = tomlkit.parse(original)
                if kind not in original_doc.get("tool", {}):
                    document["tool"].pop(kind, None)
                edits[project] = tomlkit.dumps(document)
        elif kind == "pre-commit":
            if policy == "amend":
                baseline = files.get(".pre-commit-config.yaml", pre_commit_config())
                canonical = prefix + ".pre-commit-config.yaml"
                if name != canonical and (target / canonical).exists():
                    raise ValueError(
                        "Both pre-commit config aliases exist; reconcile them first: " + scope
                    )
                edits[canonical] = merge_pre_commit(path.read_text(), baseline)
                if name != canonical:
                    retire.add(name)
            elif ".pre-commit-config.yaml" not in files:
                retire.add(name)
            elif scope != ".":
                edits[name] = files[".pre-commit-config.yaml"]
            elif name != ".pre-commit-config.yaml":
                retire.add(name)
        elif kind == "prettier-ignore" or path.name == ".semgrepignore":
            baseline = files.get(
                ".prettierignore" if kind == "prettier-ignore" else ".semgrepignore", ""
            )
            if policy == "replace":
                edits[name] = baseline
            else:
                original = path.read_text()
                missing = [
                    line for line in baseline.splitlines() if line not in original.splitlines()
                ]
                edits[name] = (
                    original
                    if not missing
                    else original.rstrip() + "\n" + "\n".join(missing) + "\n"
                )
        elif kind in {"gitleaks", "trivy", "hadolint", "semgrep"}:
            if policy == "replace":
                canonical = (
                    ".gitleaks.toml"
                    if kind == "gitleaks"
                    else ".semgrepignore"
                    if kind == "semgrep"
                    else None
                )
                if canonical and canonical in files:
                    edits[prefix + canonical] = files[canonical]
                if name != prefix + str(canonical):
                    retire.add(name)
            else:
                edits[name] = path.read_text()
        elif kind == "hook":
            if policy == "replace":
                # Leave active dispatchers until the installer switches managers atomically.
                continue
            else:
                original = path.read_text()
                if original == files.get(name):
                    edits[name] = original
                    continue
                action = "commit" if path.name == "pre-commit" else "push"
                root_command = (
                    '"$(git rev-parse --show-toplevel)/.venv/bin/python" '
                    '"$(git rev-parse --show-toplevel)/.quality/run.py" ' + action
                )
                edits[name] = amend_shell_hook(original, root_command)
    if policy == "amend" and files.get(".quality/config.json"):
        config = json.loads(files[".quality/config.json"])
        if config["hook_manager"] == "existing":
            for stage, action in (("pre-commit", "commit"), ("pre-push", "push")):
                name = (hook_directory / stage).relative_to(target).as_posix()
                if name not in edits:
                    command = (
                        '"$(git rev-parse --show-toplevel)/.venv/bin/python" '
                        '"$(git rev-parse --show-toplevel)/.quality/run.py" ' + action
                    )
                    edits[name] = amend_shell_hook("", command)
            edits.pop(".pre-commit-config.yaml", None)
            edits.pop(".husky/pre-commit", None)
            edits.pop(".husky/pre-push", None)
    return edits, retire - set(edits)
