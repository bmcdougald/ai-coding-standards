from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from jsonc import parse_jsonc
from maintenance import checked_path, scan_receipt

ROOT = Path(__file__).resolve().parents[1]
REPORT = ".tools/ci/checks.jsonl"
JOBS = {
    "format": "Formatting",
    "lint": "Linting",
    "types": "Type checks",
    "schemas": "Schema validation",
    "docker": "Docker checks",
    "secrets": "Secret detection",
    "sast": "SAST",
    "dependencies": "Dependency vulnerabilities",
    "filesystem": "Filesystem security",
    "general": "Unit and integration tests",
    "browser": "Playwright tests",
}


def validate_config(config: dict, root: Path | None = None) -> None:
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise ValueError("CI configuration requires schema 1")
    if not {"schema", "setup", "tests"} <= config.keys():
        raise ValueError("CI configuration requires setup and tests lists")
    if set(config) - {"schema", "setup", "tests", "timeout_seconds"}:
        raise ValueError("Unknown CI configuration fields")
    timeout = config.get("timeout_seconds", 1800)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("CI timeout_seconds must be a positive integer")
    for group in ("setup", "tests"):
        entries = config.get(group, [])
        if not isinstance(entries, list):
            raise ValueError("CI " + group + " must be a list")
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not {"name", "cwd", "argv"} <= entry.keys()
                or set(entry) - {"name", "cwd", "argv", "suite"}
                or (
                    "suite" in entry
                    and (
                        group != "tests"
                        or not isinstance(entry["suite"], str)
                        or entry["suite"] not in {"general", "browser"}
                    )
                )
                or not isinstance(entry["name"], str)
                or not entry["name"].strip()
                or "\n" in entry["name"]
                or not isinstance(entry["cwd"], str)
                or not isinstance(entry["argv"], list)
                or not entry["argv"]
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in entry["argv"])
            ):
                raise ValueError("CI commands require name, cwd, and a nonempty argv string list")
            checked_path(root or ROOT, entry["cwd"])


def record(name: str, status: str, detail: str = "") -> None:
    path = checked_path(ROOT, REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps({"name": name, "status": status, "detail": detail}) + "\n")


def execute(entry: dict, timeout: int) -> bool:
    directory = checked_path(ROOT, entry["cwd"])
    python = directory / ".venv/bin/python"
    if not python.exists():
        python = ROOT / ".venv/bin/python"
    command = [
        str(Path(sys.base_prefix) / "bin/python3")
        if arg == "{base_python}"
        else str(python)
        if arg == "{python}"
        else arg
        for arg in entry["argv"]
    ]
    environment = os.environ.copy()
    # run.py diff-scopes push scans on these local hook variables; CI always scans
    # the full tree, so they must not leak through the inherited environment.
    for variable in ("PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF", "BASE_REF"):
        environment.pop(variable, None)
    environment["CI"] = "true"
    environment["QUALITY_UPDATE_CHECK"] = "1"
    environment["QUALITY_CI_REPORT"] = REPORT
    environment["PATH"] = (
        str(directory / ".venv/bin")
        + ":"
        + str(ROOT / ".venv/bin")
        + ":"
        + str(ROOT / ".tools/venv/bin")
        + ":"
        + str(directory / "node_modules/.bin")
        + ":"
        + environment.get("PATH", "")
    )
    print(f"\n=== {entry['name']} ({entry['cwd']}) ===", flush=True)
    report = checked_path(ROOT, REPORT)
    offset = report.stat().st_size if report.exists() else 0
    started = time.monotonic()
    try:
        result = subprocess.run(  # noqa: S603 -- reviewed repository argv; no shell interpolation
            command, cwd=directory, env=environment, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        record(entry["name"], "FAILED", "Timeout or unavailable command; see job log")
        print(f"CI command failed: {error}", file=sys.stderr, flush=True)
        return False
    passed = result.returncode == 0
    status = "PASSED" if passed else "FAILED"
    detail = f"Exit {result.returncode}; {time.monotonic() - started:.1f}s"
    if passed and ".quality/run.py" in command:
        checks = []
        if report.exists():
            with report.open() as stream:
                stream.seek(offset)
                checks = [json.loads(line) for line in stream]
        if not checks or all(check["status"] == "SKIPPED" for check in checks):
            status = "SKIPPED"
            detail = "No applicable checks ran" if not checks else "All checks skipped; see job log"
    record(entry["name"], status, detail)
    return passed


def summary() -> bool:
    path = checked_path(ROOT, REPORT)
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    if not rows:
        record("CI", "FAILED", "No CI results were recorded")
        rows = [{"name": "CI", "status": "FAILED", "detail": "No CI results were recorded"}]

    def cell(value: str) -> str:
        return html.escape(value).replace("|", "&#124;").replace("\n", " ")

    lines = ["## Application CI results", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines.extend(
        f"| {cell(row['name'])} | {cell(row['status'])} | {cell(row['detail'])} |" for row in rows
    )
    lines.extend(["", "Skipped checks are not successful validations. Details are in the job log."])
    text = "\n".join(lines) + "\n"
    print("\n" + text, flush=True)
    failed = any(row["status"] == "FAILED" for row in rows)
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("PASSED", "FAILED", "SKIPPED")
    }
    status = "; ".join(f"{count} {name.lower()}" for name, count in counts.items())
    checked_path(ROOT, ".tools/ci/status.txt").write_text(status + "\n")
    checked_path(ROOT, ".tools/ci/summary.md").write_text(text)
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a") as stream:
            stream.write(text)
    return not failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run application CI independently of its provider."
    )
    parser.add_argument(
        "stage",
        choices=[
            "setup",
            "quality",
            "security",
            "tests",
            "all",
            "summary",
            "job",
            "github-summary",
        ],
    )
    parser.add_argument("job", nargs="?")
    args = parser.parse_args(argv)
    if (args.stage == "job") != (args.job is not None):
        parser.error("job requires an action; other stages do not accept one")
    if args.stage == "github-summary":
        results = json.loads(os.environ["QUALITY_JOB_RESULTS"])
        if not isinstance(results, dict):
            raise ValueError("Invalid GitHub job results")
        report = checked_path(ROOT, REPORT)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("")
        for key, name in JOBS.items():
            result = results.get(key, {})
            if not isinstance(result, dict):
                raise ValueError("Invalid GitHub result: " + key)
            outputs = result.get("outputs") or {}
            status = outputs.get("status") if isinstance(outputs, dict) else None
            if result.get("result") != "success" or status not in {"PASSED", "SKIPPED"}:
                status = "FAILED"
            record(name, status, "See individual job log")
        return 0 if summary() else 1
    if args.stage == "summary":
        return 0 if summary() else 1
    if args.stage in {"setup", "all", "job"}:
        report = checked_path(ROOT, REPORT)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("")
    try:
        config = json.loads(checked_path(ROOT, ".quality/ci.json").read_text())
        validate_config(config)
    except (OSError, ValueError) as error:
        record("CI configuration", "FAILED", "Invalid or unavailable configuration; see job log")
        print(f"CI configuration failed: {error}", file=sys.stderr)
        summary()
        return 1
    timeout = config.get("timeout_seconds", 1800)
    if args.stage == "job":
        actions = {
            "format-check",
            "lint",
            "types",
            "schemas",
            "docker",
            "secrets",
            "sast",
            "dependencies",
            "filesystem",
        }
        if args.job not in actions | {"general", "browser"}:
            raise ValueError("Unknown CI job: " + str(args.job))
        setup = [
            {
                "name": "Restore pinned scanner tools",
                "cwd": ".",
                "argv": ["{base_python}", ".quality/install.py", "--restore-tools"],
            },
            *(config["setup"] if args.job in {"types", "general", "browser"} else []),
        ]
        for entry in setup:
            if not execute(entry, timeout):
                summary()
                return 1
        report = checked_path(ROOT, REPORT)
        offset = report.stat().st_size
        if args.job in actions:
            entries = [
                {
                    "name": args.job,
                    "cwd": ".",
                    "argv": [str(ROOT / ".tools/venv/bin/python"), ".quality/run.py", args.job],
                }
            ]
        else:
            entries = [
                entry for entry in config["tests"] if entry.get("suite", "general") == args.job
            ]
            if not entries:
                record(args.job + " tests", "SKIPPED", "No reviewed commands configured")
        failed = False
        for entry in entries:
            failed = not execute(entry, timeout) or failed
        with report.open() as stream:
            stream.seek(offset)
            rows = [json.loads(line) for line in stream]
        status = (
            "FAILED"
            if failed
            else (
                "SKIPPED" if rows and all(row["status"] == "SKIPPED" for row in rows) else "PASSED"
            )
        )
        checked_path(ROOT, ".tools/ci/job-status.txt").write_text(status + "\n")
        return 0 if summary() and not failed else 1
    stages = ["setup", "quality", "security", "tests"] if args.stage == "all" else [args.stage]
    failed = False
    for stage in stages:
        if stage == "setup":
            entries = [
                {
                    "name": "Restore pinned scanner tools",
                    "cwd": ".",
                    "argv": ["{base_python}", ".quality/install.py", "--restore-tools"],
                },
                *config.get("setup", []),
            ]
        elif stage in {"quality", "security"}:
            actions = (
                ("format-check", "lint", "types", "schemas", "docker")
                if stage == "quality"
                else ("secrets", "sast", "dependencies", "filesystem")
            )
            entries = [
                {
                    "name": stage.capitalize() + ": " + action,
                    "cwd": ".",
                    "argv": [
                        str(ROOT / ".tools/venv/bin/python"),
                        ".quality/run.py",
                        action,
                    ],
                }
                for action in actions
            ]
        else:
            entries = config.get("tests", [])
            if not entries:
                record("Application tests", "SKIPPED", "No reviewed test commands configured")
        stage_failed = False
        for entry in entries:
            if not execute(entry, timeout):
                failed = True
                stage_failed = True
                if stage == "setup":
                    summary()
                    return 1
        if stage == "security" and not stage_failed:
            scan_receipt(
                ROOT, parse_jsonc(checked_path(ROOT, ".quality/config.json").read_text()), False
            )
    if args.stage == "all":
        return 0 if summary() and not failed else 1
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"Application CI failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
