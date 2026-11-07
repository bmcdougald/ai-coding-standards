from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bootstrap as existing
from test_bootstrap import ROOT, bootstrap, load, runner

configuration = bootstrap.ci_configuration
ci = load("application_ci", ROOT / "templates/quality/ci.py")


class ApplicationCITests(unittest.TestCase):
    project = existing.ToolingUpdateTests.project

    def test_discovery_proposes_ci_and_dedicated_scripts_and_playwright_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "scripts": {
                            "test": "vitest",
                            "test:ci": "vitest run",
                            "test:unit": "vitest run",
                        },
                        "devDependencies": {"@playwright/test": "1.64.1"},
                    }
                )
            )
            (root / "playwright.config.ts").write_text("export default {};\n")
            excluded = root / "node_modules/hidden"
            excluded.mkdir(parents=True)
            (excluded / "package.json").write_text('{"scripts":{"test":"false"}}')
            config = configuration.discover(root)
            self.assertEqual(
                [entry["name"] for entry in config["tests"]],
                ["npm test:ci", "npm test:unit", "Playwright"],
            )
            self.assertTrue(any("--with-deps" in entry["argv"] for entry in config["setup"]))
            self.assertTrue(all(entry["cwd"] == "." for entry in config["tests"]))

    def test_nested_python_tests_get_application_interpreter_and_requirements(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "services/api"
            (root / "pyproject.toml").write_text("[tool.ruff]\n")
            (app / "tests").mkdir(parents=True)
            (app / "pytest.ini").write_text("[pytest]\n")
            (app / "requirements-test.txt").write_text("pytest==9.2.0\n")
            (app / "tests/test_api.py").write_text("def test_ok(): assert True\n")
            config = configuration.discover(root)
            self.assertEqual(
                config["tests"],
                [{"name": "pytest", "cwd": "services/api", "argv": ["{python}", "-m", "pytest"]}],
            )
            self.assertIn("-r", config["setup"][1]["argv"])

    def test_unittest_and_ambiguous_frameworks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "test_app.py").write_text("import unittest\n")
            self.assertIn("unittest", configuration.discover(root)["tests"][0]["argv"])
            (root / "test_app.py").write_text("def test_app(): pass\n")
            with contextlib.redirect_stderr(io.StringIO()) as warnings:
                self.assertEqual(configuration.discover(root)["tests"], [])
            self.assertIn("ambiguous", warnings.getvalue())

    def test_noninteractive_commands_require_review_and_preview_is_parseable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text('{"scripts":{"test":"node --test"}}')
            config = configuration.plan(root, {"github"}, preview=True)
            self.assertEqual(len(json.loads(config[".quality/ci.json"])["tests"]), 1)
            with (
                patch.object(configuration.sys.stdin, "isatty", return_value=False),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(ValueError, "need review"),
            ):
                configuration.plan(root, {"github"})
            self.assertFalse((root / ".quality").exists())
            result = subprocess.run(  # noqa: S603 -- fixed bootstrap CLI and fixture repository
                [
                    sys.executable,
                    str(ROOT / "scripts/bootstrap.py"),
                    "--target",
                    str(root),
                    "--ci-preview",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)  # Not yet a Git repository.
            git = shutil.which("git")
            if git is None:
                self.fail("Git is required")
            subprocess.run([git, "init", "-q", str(root)], check=True)  # noqa: S603
            result = subprocess.run(  # noqa: S603 -- fixed bootstrap CLI and fixture repository
                [
                    sys.executable,
                    str(ROOT / "scripts/bootstrap.py"),
                    "--target",
                    str(root),
                    "--ci-preview",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout)["schema"], 1)

    def test_all_adapters_preserve_existing_pipelines_and_share_runner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            for destination, _ in configuration.PROVIDERS.values():
                path = root / destination
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("existing pipeline\n")
            with contextlib.redirect_stdout(io.StringIO()):
                files = configuration.plan(root, set(configuration.PROVIDERS))
                configuration.write(root, files)
            for destination, template in configuration.PROVIDERS.values():
                self.assertEqual((root / destination).read_text(), "existing pipeline\n")
                self.assertIn(
                    "python3 .quality/ci.py all",
                    (root / ".quality/ci-adapters" / template).read_text(),
                )
            self.assertIn(
                ".quality/ci.py",
                json.loads((root / ".quality/baseline.json").read_text())["runtime"],
            )
            self.assertIn("test-results/", (root / ".gitignore").read_text())
            self.assertFalse((root / "CI.md").exists())
            edits, _ = bootstrap.plan_tooling_update(root)
            self.assertNotIn(".quality/ci.py", edits)
            (root / ".quality/ci.py").write_text("custom runner\n")
            with self.assertRaisesRegex(ValueError, "modified CI runner"):
                configuration.plan(root, {"github"})

    def test_validation_rejects_unsafe_paths_and_bad_commands(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ci, "ROOT", Path(temp)):
            root = Path(temp)
            config = {
                "schema": 1,
                "setup": [],
                "tests": [
                    {"name": "tests", "cwd": "../escape", "argv": ["python3", "-m", "pytest"]}
                ],
            }
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                ci.validate_config(config)
            config["tests"][0]["cwd"] = "."
            config["tests"][0]["argv"] = "python3 -m pytest"
            with self.assertRaisesRegex(ValueError, "argv"):
                ci.validate_config(config)
            (root / "link").symlink_to(root, target_is_directory=True)
            config["tests"][0].update(cwd="link", argv=["true"])
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                ci.validate_config(config)

    def test_published_api_two_runtime_requires_update_before_ci_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            git = shutil.which("git")
            if git is None:
                self.fail("Git is required")
            old = subprocess.run(  # noqa: S603 -- committed published runtime fixture
                [git, "-C", str(ROOT), "show", "a9c5116:.quality/run.py"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            (root / ".quality/run.py").write_text(old)
            baseline_path = root / ".quality/baseline.json"
            baseline = json.loads(baseline_path.read_text())
            baseline["runtime"][".quality/run.py"] = hashlib.sha256(old.encode()).hexdigest()
            baseline_path.write_text(json.dumps(baseline))
            with self.assertRaisesRegex(ValueError, "--update-tooling"):
                configuration.plan(root, {"github"})
            self.assertFalse((root / ".quality/ci.py").exists())

    def test_failed_checks_do_not_prevent_other_checks_and_summary_is_inline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".quality").mkdir()
            (root / ".quality/ci.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "setup": [],
                        "tests": [
                            {
                                "name": "Unit tests",
                                "cwd": ".",
                                "argv": [sys.executable, "-c", "pass"],
                            },
                        ],
                    }
                )
            )
            (root / ".quality/config.json").write_text("{}")
            summary = root / "github-summary"
            calls = []

            def execute(entry, timeout):
                calls.append(entry["name"])
                failed = entry["name"] == "Quality: lint"
                ci.record(entry["name"], "FAILED" if failed else "PASSED", "fixture")
                return not failed

            with (
                patch.object(ci, "ROOT", root),
                patch.object(ci, "execute", side_effect=execute),
                patch.object(ci, "scan_receipt"),
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(ci.main(["all"]), 1)
            self.assertIn("Security: sast", calls)
            self.assertIn("Security: filesystem", calls)
            self.assertIn("Unit tests", calls)
            self.assertIn("| Quality: lint | FAILED |", summary.read_text())
            self.assertIn("1 failed", (root / ".tools/ci/status.txt").read_text())

    def test_setup_failure_stops_checks_and_missing_tests_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".quality").mkdir()
            (root / ".quality/ci.json").write_text('{"schema":1,"setup":[],"tests":[]}')
            with (
                patch.object(ci, "ROOT", root),
                patch.object(ci, "execute", return_value=False) as execute,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(ci.main(["all"]), 1)
                self.assertEqual(execute.call_count, 1)
            with patch.object(ci, "ROOT", root):
                self.assertEqual(ci.main(["tests"]), 0)
            self.assertIn("SKIPPED", (root / ".tools/ci/checks.jsonl").read_text())

    def test_test_commands_use_application_python_ci_environment_and_no_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python = root / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("")
            with (
                patch.object(ci, "ROOT", root),
                patch.object(
                    ci.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
                ) as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(
                    ci.execute(
                        {"name": "tests", "cwd": ".", "argv": ["{python}", "-m", "pytest"]}, 60
                    )
                )
                self.assertEqual(run.call_args.args[0][0], str(python))
                self.assertEqual(run.call_args.kwargs["env"]["CI"], "true")
                self.assertNotIn("shell", run.call_args.kwargs)
                self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_docker_skips_are_visible_in_summary_and_do_not_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(runner, "ROOT", root),
                patch.dict(os.environ, {"QUALITY_CI_REPORT": ".tools/ci/checks.jsonl"}),
                patch.object(runner.shutil, "which", return_value=None),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                runner.docker_checks([root / "compose.yml"], {"builds": [], "images": []})
            rows = (root / ".tools/ci/checks.jsonl").read_text()
            self.assertIn("SKIPPED", rows)
            self.assertIn("Docker CLI", rows)
            with patch.object(ci, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(ci.summary())

    def test_adapter_triggers_and_no_artifact_uploads(self):
        github = (ROOT / "templates/quality/github-quality.yml").read_text()
        self.assertIn("branches: [main, master]", github)
        self.assertIn("persist-credentials: false", github)
        self.assertNotIn("pull_request_target", github)
        for _, template in configuration.PROVIDERS.values():
            content = (ROOT / "templates/quality" / template).read_text()
            self.assertNotIn("upload-artifact", content)
            self.assertNotIn("archiveArtifacts", content)
            self.assertNotIn("artifacts:", content)

    def test_standards_ci_restores_application_imports_and_migration_history(self):
        from ruamel.yaml import YAML

        workflow = YAML(typ="safe").load((ROOT / ".github/workflows/quality.yml").read_text())
        steps = workflow["jobs"]["quality"]["steps"]
        checkout = next(
            step for step in steps if step.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        setup = next(
            step for step in steps if step.get("name") == "Restore repository Python dependencies"
        )
        self.assertIn("python3 -m venv .venv", setup["run"])
        self.assertIn(
            ".venv/bin/python -m pip install -r scripts/requirements-bootstrap.txt", setup["run"]
        )
        setup_index = steps.index(setup)
        self.assertLess(
            setup_index,
            next(
                i
                for i, step in enumerate(steps)
                if step.get("name") == "Check application artifacts"
            ),
        )

    def test_ci_only_formats_new_node_configuration_without_reinstalling_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prettier = root / "node_modules/.bin/prettier"
            prettier.parent.mkdir(parents=True)
            prettier.write_text("")
            with (
                patch.object(configuration.subprocess, "run") as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                configuration.write(
                    root, {".quality/ci.json": '{"schema":1,"setup":[],"tests":[]}\n'}
                )
                self.assertIn(".quality/ci.json", run.call_args.args[0])
                self.assertNotIn("install", run.call_args.args[0])

    def test_skip_only_docker_stage_does_not_claim_successful_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def docker(*args, **kwargs):
                ci.record("Docker", "SKIPPED", "Docker CLI unavailable")
                return subprocess.CompletedProcess([], 0)

            with (
                patch.object(ci, "ROOT", root),
                patch.object(ci.subprocess, "run", side_effect=docker),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(
                    ci.execute(
                        {
                            "name": "Docker checks",
                            "cwd": ".",
                            "argv": ["python3", ".quality/run.py", "docker"],
                        },
                        60,
                    )
                )
            rows = [
                json.loads(line)
                for line in (root / ".tools/ci/checks.jsonl").read_text().splitlines()
            ]
            self.assertEqual(rows[-1]["status"], "SKIPPED")
