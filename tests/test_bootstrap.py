from __future__ import annotations

import contextlib
import hashlib
import importlib.util
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

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load("jsonc", ROOT / "templates/quality/jsonc.py")
bootstrap = load("bootstrap", ROOT / "scripts/bootstrap.py")
runner = load("quality_runner", ROOT / "templates/quality/run.py")
installer = load("quality_installer", ROOT / "templates/quality/install.py")


class ToolingUpdateTests(unittest.TestCase):
    def project(self, root: Path, languages: set[str] | None = None) -> None:
        git = shutil.which("git")
        if git is None:
            self.fail("Git is required")
        subprocess.run(  # noqa: S603 -- resolved Git and fixed arguments
            [git, "init", "-q", str(root)], check=True
        )
        with contextlib.redirect_stdout(io.StringIO()):
            bootstrap.scaffold(root, languages or {"python", "typescript", "react"})

    def candidate(self, source: Path) -> None:
        shutil.copytree(ROOT / "templates", source / "templates")
        path = source / "templates/quality/versions.json"
        versions = json.loads(path.read_text())
        versions["python"]["ruff"] = "0.17.5"
        versions["node"]["eslint"] = "10.14.1"
        versions["node_overrides"]["eslint"]["minimatch"] = "11.0.6"
        path.write_text(json.dumps(versions))

    def test_updates_owned_versions_and_preserves_application_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            package = json.loads((root / "package.json").read_text())
            package["dependencies"] = {"application-lib": "1.0.0"}
            package["scripts"]["test"] = "echo custom-tests"
            (root / "package.json").write_text(json.dumps(package))
            (root / ".quality/requirements.in").write_text(
                (root / ".quality/requirements.in").read_text() + "pytest==9.2.0\n"
            )
            original = {
                name: (root / name).read_bytes()
                for name in (
                    "AGENTS.md",
                    ".vscode/settings.json",
                    "eslint.config.mjs",
                    "pyproject.toml",
                )
            }
            with patch.object(bootstrap, "SOURCE", source):
                edits, config = bootstrap.plan_tooling_update(root)
            updated = json.loads(edits["package.json"])
            self.assertEqual(updated["devDependencies"]["eslint"], "10.14.1")
            self.assertEqual(updated["overrides"]["eslint"]["minimatch"], "11.0.6")
            self.assertEqual(updated["dependencies"], package["dependencies"])
            self.assertEqual(updated["scripts"], package["scripts"])
            self.assertIn("ruff==0.17.5", edits[".quality/requirements.in"])
            self.assertIn("pytest==9.2.0", edits[".quality/requirements.in"])
            self.assertEqual(config["languages"], ["python", "react", "typescript"])
            for name, content in original.items():
                self.assertNotIn(name, edits)
                self.assertEqual((root / name).read_bytes(), content)

    def test_preview_and_declined_confirmation_do_not_write_or_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with (
                patch.object(bootstrap, "SOURCE", source),
                patch.object(bootstrap.subprocess, "run") as run,
                patch.object(bootstrap.sys.stdin, "isatty", return_value=True),
                patch.object(bootstrap, "input", return_value="n"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                bootstrap.update_tooling(root, preview=True)
                bootstrap.update_tooling(root)
                run.assert_not_called()
            after = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_local_runtime_and_dependency_edits_block_before_writes(self):
        for name in (".quality/run.py", "package.json", ".quality/requirements.in"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root, source = Path(temp) / "project", Path(temp) / "source"
                self.project(root)
                self.candidate(source)
                path = root / name
                if name == "package.json":
                    package = json.loads(path.read_text())
                    package["devDependencies"]["eslint"] = "10.14.2"
                    path.write_text(json.dumps(package))
                elif name.endswith(".in"):
                    path.write_text(path.read_text().replace("ruff==0.17.4", "ruff==0.17.3"))
                else:
                    path.write_text(path.read_text() + "\n# local customization\n")
                with (
                    patch.object(bootstrap, "SOURCE", source),
                    self.assertRaisesRegex(ValueError, "Locally"),
                ):
                    bootstrap.plan_tooling_update(root)
                self.assertFalse((root / ".tools/backups").exists())

    def test_major_downgrade_and_runtime_api_changes_require_migration(self):
        for old, new in (("10.14.0", "11.0.0"), ("1.2.0", "1.1.0"), ("0.17.4", "0.18.0")):
            with self.subTest(old=old), self.assertRaisesRegex(ValueError, "migration"):
                bootstrap.compatible_version("test-tool", old, new)
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            path = source / "templates/quality/versions.json"
            versions = json.loads(path.read_text())
            versions["runtime_api"] = 2
            path.write_text(json.dumps(versions))
            with (
                patch.object(bootstrap, "SOURCE", source),
                self.assertRaisesRegex(ValueError, "migration"),
            ):
                bootstrap.plan_tooling_update(root)

    def test_install_or_validation_failure_restores_manifests_runtime_and_locks(self):
        for fail_at in (1, 4):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as temp:
                root, source = Path(temp) / "project", Path(temp) / "source"
                self.project(root)
                self.candidate(source)
                lock = root / "package-lock.json"
                lock.write_text('{"original": true}\n')
                before = {
                    str(path.relative_to(root)): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file() and ".git" not in path.parts
                }
                calls = []

                def execute(command, calls=calls, lock=lock, root=root, fail_at=fail_at, **kwargs):
                    calls.append(command)
                    if len(calls) == 1:
                        lock.write_text('{"updated": true}\n')
                        python_lock = root / ".quality/locked/requirements.txt"
                        python_lock.parent.mkdir(parents=True, exist_ok=True)
                        python_lock.write_text("ruff==0.17.5\n")
                    if len(calls) == fail_at:
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0)

                with (
                    patch.object(bootstrap, "SOURCE", source),
                    patch.object(bootstrap.subprocess, "run", side_effect=execute),
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()) as errors,
                    self.assertRaises(subprocess.CalledProcessError),
                ):
                    bootstrap.update_tooling(root, yes=True)
                for name, content in before.items():
                    self.assertEqual((root / name).read_bytes(), content, name)
                self.assertFalse((root / ".quality/locked/requirements.txt").exists())
                self.assertIn("Installed environments may have changed", errors.getvalue())
                self.assertEqual(len(list((root / ".tools/backups").glob("update-*"))), 1)

    def test_success_validates_both_stages_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            with (
                patch.object(bootstrap, "SOURCE", source),
                patch.object(bootstrap.subprocess, "run") as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                bootstrap.update_tooling(root, yes=True)
                self.assertEqual(run.call_count, 4)
                self.assertIn("--update-tools", run.call_args_list[0].args[0])
                self.assertIn("--all-files", run.call_args_list[2].args[0])
                self.assertIn("pre-push", run.call_args_list[3].args[0])
                self.assertEqual(run.call_args_list[2].kwargs["env"]["QUALITY_UPDATE_CHECK"], "1")
                run.reset_mock()
                bootstrap.update_tooling(root, yes=True)
                run.assert_not_called()

    def test_legacy_adoption_requires_explicit_choice_and_matching_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root)
            (root / ".quality/baseline.json").unlink()
            with self.assertRaisesRegex(ValueError, "--adopt-tooling"):
                bootstrap.plan_tooling_update(root)
            with contextlib.redirect_stdout(io.StringIO()):
                edits, _ = bootstrap.plan_tooling_update(root, adopt=True)
            self.assertIn(".quality/baseline.json", edits)
            path = root / ".quality/run.py"
            path.write_text(path.read_text() + "\n# customized\n")
            with self.assertRaisesRegex(ValueError, "Cannot adopt"):
                bootstrap.plan_tooling_update(root, adopt=True)

    def test_unsafe_owned_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root)
            path = root / ".quality/baseline.json"
            baseline = json.loads(path.read_text())
            baseline["node"]["../outside/package.json"] = {"devDependencies": {"eslint": "10.14.0"}}
            path.write_text(json.dumps(baseline))
            with self.assertRaisesRegex(ValueError, "Unsafe tooling path"):
                bootstrap.plan_tooling_update(root)

    def test_amended_workspaces_leave_preexisting_tools_unowned(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            git = shutil.which("git")
            if git is None:
                self.fail("Git is required")
            subprocess.run(  # noqa: S603 -- resolved Git, fixed arguments
                [git, "init", "-q", str(root)], check=True
            )
            (root / "packages/child").mkdir(parents=True)
            package = {
                "workspaces": ["packages/*"],
                "devDependencies": {"eslint": "10.14.0", "application-tool": "1.0.0"},
                "scripts": {"test": "echo retained"},
            }
            (root / "package.json").write_text(json.dumps(package))
            (root / "packages/child/package.json").write_text(
                '{"devDependencies": {"eslint": "9.39.4"}}'
            )
            (root / "packages/child/.eslintrc.json").write_text('{"rules": {}}')
            inventory, hooks = bootstrap.inspect_tooling(root, git)
            with contextlib.redirect_stdout(io.StringIO()):
                bootstrap.scaffold(
                    root, {"javascript"}, policy="amend", inventory=inventory, hook_directory=hooks
                )
            baseline = json.loads((root / ".quality/baseline.json").read_text())
            self.assertNotIn("eslint", baseline["node"]["package.json"]["devDependencies"])
            self.assertNotIn(
                "eslint", baseline["node"]["packages/child/package.json"]["devDependencies"]
            )
            self.assertIn("eslint", baseline["node"]["package.json"]["overrides"])
            self.candidate(source)
            with patch.object(bootstrap, "SOURCE", source):
                edits, _ = bootstrap.plan_tooling_update(root)
            updated = json.loads(edits["package.json"])
            self.assertEqual(updated["devDependencies"]["eslint"], "10.14.0")
            self.assertEqual(updated["scripts"]["test"], "echo retained")
            self.assertNotIn("packages/child/.eslintrc.json", edits)
            if "packages/child/package.json" in edits:
                self.assertEqual(
                    json.loads(edits["packages/child/package.json"])["devDependencies"]["eslint"],
                    "9.39.4",
                )

    def test_shell_manager_validates_physical_hooks_without_installing_another_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            path = root / ".quality/config.json"
            config = json.loads(path.read_text())
            config["hook_manager"] = "existing"
            path.write_text(json.dumps(config))
            with (
                patch.object(bootstrap, "SOURCE", source),
                patch.object(bootstrap, "inspect_tooling", return_value=([], root / ".git/hooks")),
                patch.object(bootstrap.subprocess, "run") as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                bootstrap.update_tooling(root, yes=True)
            self.assertEqual(
                run.call_args_list[-2].args[0][-1], str(root / ".git/hooks/pre-commit")
            )
            self.assertEqual(run.call_args_list[-1].args[0][-1], str(root / ".git/hooks/pre-push"))
            self.assertNotIn("pre-commit", run.call_args_list[-1].args[0][0])

    def test_interrupted_install_restores_files_and_clears_recovery_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            original = (root / ".quality/baseline.json").read_bytes()
            with (
                patch.object(bootstrap, "SOURCE", source),
                patch.object(bootstrap.subprocess, "run", side_effect=KeyboardInterrupt),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(KeyboardInterrupt),
            ):
                bootstrap.update_tooling(root, yes=True)
            self.assertEqual((root / ".quality/baseline.json").read_bytes(), original)
            self.assertFalse((root / ".tools/update-pending.json").exists())

    def test_pending_update_blocks_another_update_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root)
            pending = root / ".tools/update-pending.json"
            pending.parent.mkdir()
            pending.write_text('{"backup": ".tools/backups/update-example"}\n')
            with self.assertRaisesRegex(ValueError, "interrupted update needs recovery"):
                bootstrap.plan_tooling_update(root)

    def test_update_installer_resolves_pins_without_formatting_or_reinstalling_hooks(self):
        for owns_node in (True, False):
            with self.subTest(owns_node=owns_node), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "project"
                self.project(root, {"javascript"})
                python = root / ".venv/bin/python"
                python.parent.mkdir(parents=True)
                python.touch()
                baseline_path = root / ".quality/baseline.json"
                baseline = json.loads(baseline_path.read_text())
                if not owns_node:
                    baseline["node"] = {}
                    baseline_path.write_text(json.dumps(baseline))
                direct = (root / ".quality/requirements.in").read_text()
                lock = root / ".quality/locked/requirements.txt"
                lock.parent.mkdir()
                lock.write_text(direct.replace("semgrep==1.184.0", "semgrep==1.183.0"))
                with (
                    patch.object(installer, "ROOT", root),
                    patch.object(installer.platform, "system", return_value="Linux"),
                    patch.object(installer.platform, "machine", return_value="x86_64"),
                    patch.object(installer, "run") as run,
                    patch.object(installer, "install_binary"),
                    patch.object(
                        installer.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 0, stdout=direct),
                    ) as freeze,
                ):
                    self.assertEqual(installer.main(["--update-tools"]), 0)
                commands = [call.args for call in run.call_args_list]
                npm_commands = [command for command in commands if command[0] == "npm"]
                self.assertEqual(len(npm_commands), int(owns_node))
                if owns_node:
                    self.assertIn("--ignore-scripts", npm_commands[0])
                    self.assertIn("install", npm_commands[0])
                self.assertTrue(any("--upgrade" in command for command in commands))
                self.assertFalse(
                    any("husky" in command or "--write" in command for command in commands)
                )
                self.assertIn("--all", freeze.call_args.args[0])
                self.assertEqual(lock.read_text(), direct)

    def test_python_only_legacy_adoption_does_not_require_a_node_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            (root / ".quality/baseline.json").unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                edits, _ = bootstrap.plan_tooling_update(root, adopt=True)
            baseline = json.loads(edits[".quality/baseline.json"])
            self.assertEqual(baseline["node"], {})
            self.assertIn("ruff", baseline["python"])

    def test_hook_mutation_of_managed_files_is_not_reported_as_update_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            original = (root / "package.json").read_bytes()
            calls = []

            def execute(command, **kwargs):
                calls.append(command)
                if len(calls) == 4:
                    (root / "package.json").write_text('{"modified_by_hook": true}\n')
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(bootstrap, "SOURCE", source),
                patch.object(bootstrap.subprocess, "run", side_effect=execute),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaisesRegex(ValueError, "checks altered a managed update file"),
            ):
                bootstrap.update_tooling(root, yes=True)
            self.assertEqual((root / "package.json").read_bytes(), original)
            self.assertFalse((root / ".tools/update-pending.json").exists())


class DetectionTests(unittest.TestCase):
    def repository(self, root: Path) -> str:
        git = shutil.which("git")
        if git is None:
            self.fail("Git is required for detection tests")
        subprocess.run(  # noqa: S603 -- resolved Git executable and fixed argv
            [git, "init", "-q", str(root)], check=True
        )
        return git

    def write(self, root: Path, name: str, content: str = "") -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_detects_nested_mixed_source_and_react(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            for name in ("backend/app.py", "frontend/ui/App.tsx", "scripts/check.mjs"):
                self.write(root, name)
            with patch.object(bootstrap, "input", side_effect=AssertionError("Must not prompt")):
                languages, existing, evidence = bootstrap.select_languages(root, git, None)
            self.assertEqual(languages, {"python", "typescript", "javascript", "react"})
            self.assertTrue(existing)
            self.assertEqual(evidence["react"], ["frontend/ui/App.tsx"])

    def test_detects_manifests_in_subfolders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "backend/pyproject.toml", '[project]\nname = "backend"\n')
            self.write(
                root,
                "frontend/package.json",
                json.dumps(
                    {
                        "devDependencies": {"typescript": "5"},
                        "peerDependencies": {"react": "19"},
                    }
                ),
            )
            evidence, existing = bootstrap.inspect_repository(root, git)
            self.assertEqual(set(evidence), {"python", "typescript", "react"})
            self.assertTrue(existing)

    def test_ignores_generated_vendored_and_git_ignored_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, ".gitignore", "ignored/\n")
            for name in (
                "node_modules/lib/index.ts",
                ".venv/lib/a.py",
                ".quality/install.py",
                "vendor/a.py",
                "ignored/file.tsx",
                "build/app.js",
                ".tools/bin/a.py",
            ):
                self.write(root, name)
            self.write(root, "app/main.py")
            evidence, _ = bootstrap.inspect_repository(root, git)
            self.assertEqual(evidence, {"python": ["app/main.py"]})

    def test_tracked_code_is_detected_even_if_later_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "app/main.py")
            subprocess.run(  # noqa: S603 -- resolved Git executable and fixed argv
                [git, "-C", str(root), "add", "app/main.py"], check=True
            )
            self.write(root, ".gitignore", "app/\n")
            evidence, _ = bootstrap.inspect_repository(root, git)
            self.assertIn("python", evidence)

    def test_empty_and_documentation_only_repositories_prompt(self):
        for documentation in (False, True):
            with self.subTest(documentation=documentation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                git = self.repository(root)
                if documentation:
                    self.write(root, "README.md", "# Project\n")
                with (
                    patch.object(bootstrap.sys.stdin, "isatty", return_value=True),
                    patch.object(bootstrap, "input", return_value="python,typescript") as prompt,
                ):
                    languages, existing, _ = bootstrap.select_languages(root, git, None)
                self.assertFalse(existing)
                self.assertEqual(languages, {"python", "typescript"})
                prompt.assert_called_once()

    def test_empty_noninteractive_requires_languages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            with (
                patch.object(bootstrap.sys.stdin, "isatty", return_value=False),
                self.assertRaisesRegex(ValueError, "--languages is required"),
            ):
                bootstrap.select_languages(root, git, None)

    def test_override_supported_and_unsupported_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "main.go")
            with self.assertRaisesRegex(ValueError, "no supported language evidence"):
                bootstrap.select_languages(root, git, None)
            languages, existing, _ = bootstrap.select_languages(root, git, "python")
            self.assertEqual(languages, {"python"})
            self.assertTrue(existing)

    def test_saved_node_profile_does_not_detect_its_typescript_cli_as_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            bootstrap.scaffold(root, {"javascript"})
            languages, _, _ = bootstrap.select_languages(root, git, None)
            self.assertEqual(languages, {"javascript"})
            self.write(root, "app.py")
            with self.assertRaisesRegex(ValueError, "New languages detected: python"):
                bootstrap.select_languages(root, git, None)

    def test_invalid_manifest_is_reported_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "frontend/package.json", "{bad")
            with self.assertRaisesRegex(ValueError, "Cannot inspect frontend/package.json"):
                bootstrap.select_languages(root, git, None)
            self.assertFalse((root / ".quality").exists())

    def test_symlinked_source_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            git = self.repository(root)
            outside = Path(temp) / "outside.py"
            outside.write_text("print('outside')\n")
            (root / "linked.py").symlink_to(outside)
            evidence, _ = bootstrap.inspect_repository(root, git)
            self.assertEqual(evidence, {})

    def test_detect_only_cli_does_not_write_or_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, "app/main.py")
            with (
                patch.object(sys, "argv", ["bootstrap.py", "--target", str(root), "--detect-only"]),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(bootstrap.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["languages"], ["python"])
            self.assertFalse((root / ".quality").exists())

    def test_existing_code_configures_recursively_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = "export const value:number=42;\n"
            self.write(root, "lib/main.ts", original)
            with patch.object(
                sys, "argv", ["bootstrap.py", "--target", str(root), "--configure-only"]
            ):
                self.assertEqual(bootstrap.main(), 0)
            self.assertEqual((root / "lib/main.ts").read_text(), original)
            self.assertEqual(json.loads((root / "tsconfig.json").read_text())["include"], ["**/*"])

    def test_prompted_destination_receives_setup_instead_of_tools_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "destination with spaces"
            root.mkdir()
            self.repository(root)
            with (
                patch.object(
                    sys, "argv", ["bootstrap.py", "--configure-only", "--languages", "python"]
                ),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch.object(bootstrap, "input", return_value="  " + str(root) + "  ") as prompt,
                patch.object(bootstrap, "SOURCE", ROOT),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(bootstrap.main(), 0)
            prompt.assert_called_once_with("Path to the Git repository to set up: ")
            self.assertTrue((root / ".quality/config.json").is_file())
            self.assertIn("Destination repository: " + str(root), output.getvalue())

    def test_explicit_destination_never_prompts_and_expands_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            self.repository(root)
            with (
                patch.object(
                    sys, "argv", ["bootstrap.py", "--target", "~/repo", "--existing-tools", "leave"]
                ),
                patch.dict(os.environ, {"HOME": temp}),
                patch.object(bootstrap, "input", side_effect=AssertionError("Must not prompt")),
            ):
                self.assertEqual(bootstrap.main(), 0)
            self.assertFalse((root / ".quality").exists())

    def test_missing_target_noninteractive_or_detection_only_fails_without_prompt(self):
        for terminal, arguments in ((False, []), (True, ["--detect-only"])):
            with (
                self.subTest(arguments=arguments),
                patch.object(sys, "argv", ["bootstrap.py", *arguments]),
                patch.object(sys.stdin, "isatty", return_value=terminal),
                patch.object(bootstrap, "input", side_effect=AssertionError("Must not prompt")),
                contextlib.redirect_stderr(io.StringIO()) as error,
                self.assertRaises(SystemExit) as raised,
            ):
                bootstrap.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--target is required", error.getvalue())

    def test_empty_cancelled_or_invalid_destination_fails_before_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            (root / "subfolder").mkdir()
            for answer in (
                "",
                str(root / "missing"),
                str(root / "subfolder"),
                EOFError(),
                KeyboardInterrupt(),
            ):
                with (
                    self.subTest(answer=answer),
                    patch.object(sys, "argv", ["bootstrap.py"]),
                    patch.object(sys.stdin, "isatty", return_value=True),
                    patch.object(bootstrap, "input", side_effect=[answer]),
                    patch.object(bootstrap, "scaffold") as scaffold,
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    bootstrap.main()
                self.assertEqual(raised.exception.code, 2)
                scaffold.assert_not_called()
            self.assertFalse((root / ".quality").exists())

    def test_existing_tsconfig_and_package_scripts_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = '{// project references\n"files": [], "references": [{"path": "./app"}]}\n'
            self.write(root, "tsconfig.json", config)
            self.write(root, "package.json", '{"scripts": {"build": "custom-build"}}')
            bootstrap.scaffold(root, {"typescript"}, existing=True)
            self.assertEqual((root / "tsconfig.json").read_text(), config)
            self.assertEqual(
                json.loads((root / "package.json").read_text())["scripts"]["build"], "custom-build"
            )


class ToolingPolicyTests(unittest.TestCase):
    repository = DetectionTests.repository
    write = DetectionTests.write

    def reconcile(self, root: Path, languages: set[str], policy: str) -> None:
        git = shutil.which("git")
        if git is None:
            self.fail("Git is required")
        inventory, hooks = bootstrap.inspect_tooling(root, git)
        bootstrap.scaffold(
            root, languages, existing=True, policy=policy, inventory=inventory, hook_directory=hooks
        )

    def test_leave_cli_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = "export default [];\n"
            self.write(root, "eslint.config.mjs", original)
            with patch.object(
                sys, "argv", ["bootstrap.py", "--target", str(root), "--existing-tools", "leave"]
            ):
                self.assertEqual(bootstrap.main(), 0)
            self.assertEqual((root / "eslint.config.mjs").read_text(), original)
            self.assertFalse((root / ".tools").exists())
            self.assertFalse((root / ".quality").exists())

    def test_existing_tooling_requires_explicit_noninteractive_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "eslint.config.mjs", "export default [];\n")
            inventory, _ = bootstrap.inspect_tooling(root, git)
            with (
                patch.object(bootstrap.sys.stdin, "isatty", return_value=False),
                self.assertRaisesRegex(ValueError, "--existing-tools"),
            ):
                bootstrap.choose_tooling_policy(root, inventory, None)

    def test_all_three_interactive_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            for answer, expected in (("1", "leave"), ("2", "amend"), ("3", "replace")):
                with (
                    self.subTest(answer=answer),
                    patch.object(bootstrap.sys.stdin, "isatty", return_value=True),
                    patch.object(bootstrap, "input", return_value=answer),
                ):
                    result = bootstrap.choose_tooling_policy(
                        Path(temp), [{"path": "eslint.config.mjs", "configured": True}], None
                    )
                    self.assertEqual(result, expected)

    def test_amend_preserves_nested_eslint_and_dependency_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = "// existing rules\nexport default [];\n"
            self.write(root, "frontend/eslint.config.mjs", original)
            self.write(
                root,
                "frontend/package.json",
                json.dumps(
                    {
                        "private": True,
                        "devDependencies": {"eslint": "9.39.4"},
                        "scripts": {"test": "node --test"},
                    }
                ),
            )
            self.reconcile(root, {"typescript", "python"}, "amend")
            self.assertEqual((root / "frontend/eslint.config.mjs").read_text(), original)
            self.assertFalse((root / "frontend/eslint.config.js").exists())
            package = json.loads((root / "frontend/package.json").read_text())
            self.assertEqual(package["devDependencies"]["eslint"], "9.39.4")
            self.assertEqual(package["scripts"]["test"], "node --test")
            self.assertIn("prettier", package["devDependencies"])
            config = json.loads((root / ".quality/config.json").read_text())
            self.assertIn("frontend", config["node_projects"])

    def test_replace_standardizes_root_and_nested_analyzers_with_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            originals = {
                "eslint.config.js": "module.exports = {};\n",
                "frontend/eslint.config.cjs": "module.exports = {};\n",
                "frontend/.prettierrc": '{"semi": false}\n',
            }
            for name, text in originals.items():
                self.write(root, name, text)
            self.write(
                root,
                "frontend/package.json",
                json.dumps(
                    {
                        "devDependencies": {"eslint": "8.57.1"},
                        "dependencies": {"application-package": "1.0.0"},
                        "scripts": {"test": "node --test"},
                    }
                ),
            )
            self.reconcile(root, {"typescript", "python"}, "replace")
            self.assertEqual(
                (root / "eslint.config.mjs").read_text(),
                (root / "frontend/eslint.config.mjs").read_text(),
            )
            for name, text in originals.items():
                self.assertFalse((root / name).exists())
                backups = list((root / ".tools/backups").glob("*/" + name))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(), text)
            package = json.loads((root / "frontend/package.json").read_text())
            self.assertEqual(package["devDependencies"]["eslint"], "10.14.0")
            self.assertEqual(package["dependencies"], {"application-package": "1.0.0"})
            self.assertEqual(package["scripts"]["test"], "node --test")

    def test_amend_python_metadata_rules_and_add_missing_pyright(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = (
                '# application metadata\n[project]\nname = "existing"\nversion = "1.0.0"\n'
                "[tool.ruff]\nline-length = 88\n"
                '[tool.pytest.ini_options]\ntestpaths = ["custom-tests"]\n'
            )
            self.write(root, "backend/pyproject.toml", original)
            self.reconcile(root, {"python"}, "amend")
            import tomllib

            updated = (root / "backend/pyproject.toml").read_text()
            document = tomllib.loads(updated)
            self.assertIn("# application metadata", updated)
            self.assertEqual(document["project"]["name"], "existing")
            self.assertEqual(document["tool"]["ruff"]["line-length"], 88)
            self.assertEqual(
                document["tool"]["pytest"]["ini_options"]["testpaths"], ["custom-tests"]
            )
            self.assertIn("pyright", document["tool"])

    def test_replace_python_tables_preserves_metadata_and_tests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = (
                '[project]\nname = "existing"\nversion = "1.0.0"\n'
                "[tool.ruff]\nline-length = 88\n"
                '[tool.pytest.ini_options]\ntestpaths = ["custom-tests"]\n'
            )
            self.write(root, "backend/pyproject.toml", original)
            self.write(root, "backend/.ruff.toml", "line-length = 70\n")
            self.reconcile(root, {"python"}, "replace")
            import tomllib

            document = tomllib.loads((root / "backend/pyproject.toml").read_text())
            self.assertEqual(document["project"]["name"], "existing")
            self.assertEqual(document["tool"]["ruff"]["line-length"], 100)
            self.assertEqual(
                document["tool"]["pytest"]["ini_options"]["testpaths"], ["custom-tests"]
            )
            self.assertFalse((root / "backend/.ruff.toml").exists())

    def test_amend_precommit_preserves_checks_and_adds_both_stages_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = (
                "# team checks\nrepos:\n  - repo: local\n    hooks:\n"
                "      - id: original-test\n        name: Existing tests\n"
                "        entry: python -m unittest\n        language: system\n"
            )
            self.write(root, ".pre-commit-config.yaml", original)
            self.reconcile(root, {"python"}, "amend")
            updated = (root / ".pre-commit-config.yaml").read_text()
            self.assertIn("# team checks", updated)
            self.assertIn("original-test", updated)
            self.assertEqual(updated.count("id: quality-commit"), 1)
            self.assertEqual(updated.count("id: quality-push"), 1)
            self.reconcile(root, {"python"}, "amend")
            self.assertEqual((root / ".pre-commit-config.yaml").read_text(), updated)

    def test_amend_shell_hook_keeps_original_body_and_executes_suite_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = "#!/bin/sh\nprintf 'existing check\\n'\nexit 0\n"
            self.write(root, ".git/hooks/pre-commit", original)
            self.reconcile(root, {"python"}, "amend")
            updated = (root / ".git/hooks/pre-commit").read_text()
            self.assertIn(original.split("\n", 1)[1], updated)
            self.assertIn("# project-local-quality-suite", updated)
            self.assertTrue((root / ".git/hooks/pre-push").exists())
            self.assertEqual(
                json.loads((root / ".quality/config.json").read_text())["hook_manager"], "existing"
            )

    def test_non_shell_amendment_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = "#!/usr/bin/python3\nprint('existing check')\n"
            self.write(root, ".git/hooks/pre-commit", original)
            with self.assertRaisesRegex(ValueError, "non-shell hook"):
                self.reconcile(root, {"python"}, "amend")
            self.assertEqual((root / ".git/hooks/pre-commit").read_text(), original)
            self.assertFalse((root / ".quality").exists())
            self.assertFalse((root / ".tools").exists())

    def test_npm_lock_is_updated_only_when_dependency_manifest_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = {"devDependencies": {"eslint": "9.39.5"}}
            self.write(root, "package.json", json.dumps(package))
            self.write(root, "package-lock.json", json.dumps({"packages": {"": package}}))
            self.assertEqual(installer.npm_action(root), "ci")
            package["devDependencies"]["eslint"] = "9.39.4"
            self.write(root, "package.json", json.dumps(package))
            self.assertEqual(installer.npm_action(root), "install")

    def test_workspace_globs_and_nested_dependency_lock_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = {"workspaces": ["packages/*", "nested/**", "!nested/excluded"]}
            self.write(root, "package.json", json.dumps(package))
            directories = [
                root,
                root / "packages/one",
                root / "packages/one/child",
                root / "nested/two/child",
                root / "nested/excluded",
            ]
            child = {"devDependencies": {"eslint": "9.39.5"}}
            for directory in directories[1:]:
                self.write(
                    root, str(directory.relative_to(root) / "package.json"), json.dumps(child)
                )
            members = installer.workspace_members(root, directories)
            self.assertEqual(set(members), {root / "packages/one", root / "nested/two/child"})
            locked = {"": package, **{path.relative_to(root).as_posix(): child for path in members}}
            self.write(root, "package-lock.json", json.dumps({"packages": locked}))
            self.assertEqual(installer.npm_action(root, members), "ci")
            self.write(
                root, "packages/one/package.json", '{"devDependencies": {"eslint": "9.39.4"}}'
            )
            self.assertEqual(installer.npm_action(root, members), "install")

    def test_non_npm_projects_fail_before_configuration_writes(self):
        for manager, lock in (("pnpm@10.0.0", None), ("npm@10.0.0", "yarn.lock")):
            with self.subTest(manager=manager), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.repository(root)
                self.write(root, "package.json", json.dumps({"packageManager": manager}))
                if lock:
                    self.write(root, lock, "")
                with self.assertRaisesRegex(ValueError, "Non-npm"):
                    self.reconcile(root, {"javascript"}, "replace")
                self.assertFalse((root / ".quality").exists())

    def test_legacy_eslint_without_compatible_declared_version_requires_review(self):
        packages = [
            None,
            "{}",
            *(
                json.dumps({"devDependencies": {"eslint": version}})
                for version in ("10.14.0", "*", ">=9", "^11.0.0", "^9.0.0 || ^10.0.0")
            ),
        ]
        for package in packages:
            with self.subTest(package=package), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.repository(root)
                self.write(root, ".eslintrc.json", '{"rules": {}}')
                if package:
                    self.write(root, "package.json", package)
                with self.assertRaisesRegex(ValueError, "Legacy ESLint configuration"):
                    self.reconcile(root, {"javascript"}, "amend")
                self.assertFalse((root / ".quality").exists())

    def test_legacy_eslint_retains_explicit_pre_10_dependency_ranges(self):
        for version in ("9.39.4", "^9.39.4", "~9.39.4"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.repository(root)
                legacy = '{"rules": {}}'
                self.write(root, ".eslintrc.json", legacy)
                self.write(
                    root, "package.json", json.dumps({"devDependencies": {"eslint": version}})
                )
                self.reconcile(root, {"javascript"}, "amend")
                package = json.loads((root / "package.json").read_text())
                self.assertEqual(package["devDependencies"]["eslint"], version)
                self.assertEqual((root / ".eslintrc.json").read_text(), legacy)
                self.assertFalse((root / "eslint.config.mjs").exists())

    def test_replace_pins_root_tools_without_copying_application_dependencies_to_children(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(
                root,
                "package.json",
                json.dumps(
                    {
                        "devDependencies": {"eslint": "8.57.1", "application-test": "1.0.0"},
                        "dependencies": {"prettier": "2.0.0"},
                    }
                ),
            )
            self.write(root, "frontend/package.json", "{}")
            self.reconcile(root, {"javascript"}, "replace")
            package = json.loads((root / "package.json").read_text())
            child = json.loads((root / "frontend/package.json").read_text())
            self.assertEqual(package["devDependencies"]["eslint"], "10.14.0")
            self.assertEqual(package["dependencies"]["prettier"], "3.9.10")
            self.assertNotIn("prettier", package["devDependencies"])
            self.assertEqual(package["devDependencies"]["application-test"], "1.0.0")
            self.assertNotIn("application-test", child["devDependencies"])

    def test_amend_normalizes_precommit_alias_and_keeps_both_stages_in_nested_configs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            original = (
                "repos:\n  - repo: local\n    hooks:\n      - id: team-check\n"
                "        entry: echo ok\n        name: team\n        language: system\n"
            )
            self.write(root, ".pre-commit-config.yml", original)
            self.write(root, "frontend/.pre-commit-config.yaml", original)
            self.reconcile(root, {"javascript"}, "amend")
            self.assertFalse((root / ".pre-commit-config.yml").exists())
            for name in (".pre-commit-config.yaml", "frontend/.pre-commit-config.yaml"):
                content = (root / name).read_text()
                self.assertIn("team-check", content)
                self.assertIn("stages: [pre-commit]", content)
                self.assertIn("stages: [pre-push]", content)
            self.assertEqual(
                json.loads((root / ".quality/config.json").read_text())["hook_manager"],
                "pre-commit",
            )

    def test_push_hook_passes_remote_arguments_only_to_original_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, ".git/hooks/pre-push", '#!/bin/sh\nprintf "%s\\n" "$@" >> checks\n')
            self.reconcile(root, {"python"}, "amend")
            self.write(
                root,
                ".venv/bin/python",
                '#!/bin/sh\n[ "$#" -eq 2 ] || exit 27\nprintf "suite-%s\\n" "$2" >> checks\n',
            )
            (root / ".venv/bin/python").chmod(0o755)
            subprocess.run(  # noqa: S603 -- fixture-local hook and fixed test arguments
                [str(root / ".git/hooks/pre-push"), "origin", "https://example.test/repo"],
                cwd=root,
                check=True,
            )
            self.assertEqual(
                (root / "checks").read_text().splitlines(),
                ["suite-push", "origin", "https://example.test/repo"],
            )

    def test_nested_formatter_and_analyzers_use_their_own_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, "frontend/package.json", "{}")
            self.write(root, "frontend/code.js", "export const value = 1;\n")
            self.write(root, "frontend/tsconfig.json", '{"include": ["code.js"]}')
            self.write(root, "backend/pyproject.toml", "[tool.ruff]\nline-length = 88\n")
            self.write(root, "backend/.hadolint.yaml", "ignored: []\n")
            self.write(root, "backend/Dockerfile", "FROM scratch\n")
            self.reconcile(root, {"javascript", "python"}, "amend")
            with (
                patch.object(runner, "ROOT", root),
                patch.object(runner, "run") as run,
                patch.object(runner.sys, "argv", ["run.py", "check"]),
            ):
                self.assertEqual(runner.main(), 0)
            calls = run.call_args_list
            self.assertTrue(
                any(
                    call.kwargs.get("directory") == root / "frontend" and "prettier" in call.args[0]
                    for call in calls
                )
            )
            self.assertTrue(
                any(
                    call.kwargs.get("directory") == root / "frontend" and "eslint" in call.args[0]
                    for call in calls
                )
            )
            self.assertTrue(
                any(str(root / "frontend/tsconfig.json") in call.args for call in calls)
            )
            self.assertTrue(
                any(str(root / "backend") in call.args and "pyright" in call.args for call in calls)
            )
            self.assertTrue(
                any(str(root / "backend/.hadolint.yaml") in call.args for call in calls)
            )

    def test_new_tooling_requires_review_instead_of_reusing_saved_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.reconcile(root, {"javascript"}, "replace")
            inventory, _ = bootstrap.inspect_tooling(root, git)
            self.assertEqual(bootstrap.choose_tooling_policy(root, inventory, None), "replace")
            self.write(root, "frontend/.prettierrc", '{"semi": false}')
            inventory, _ = bootstrap.inspect_tooling(root, git)
            with (
                patch.object(bootstrap.sys.stdin, "isatty", return_value=False),
                self.assertRaisesRegex(ValueError, "--existing-tools"),
            ):
                bootstrap.choose_tooling_policy(root, inventory, None)

    def test_nested_suite_dependencies_do_not_introduce_false_typescript_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "frontend/package.json", "{}")
            self.write(root, "frontend/code.js", "export const value = 1;\n")
            self.reconcile(root, {"javascript"}, "amend")
            languages, _, _ = bootstrap.select_languages(root, git, None)
            self.assertEqual(languages, {"javascript"})

    def test_nested_formatted_baseline_config_is_preserved_on_replacement_reruns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, "frontend/package.json", "{}")
            self.reconcile(root, {"javascript"}, "replace")
            config = root / "frontend/eslint.config.mjs"
            formatted = config.read_text().replace("'", '"').replace("  ", "    ")
            config.write_text(formatted)
            self.reconcile(root, {"javascript"}, "replace")
            self.assertEqual(config.read_text(), formatted)

    def test_replace_accepts_existing_javascript_comments_with_apostrophes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, "eslint.config.mjs", "// Team's config\nexport default [];\n")
            self.reconcile(root, {"javascript"}, "replace")
            self.assertIn("defineConfig", (root / "eslint.config.mjs").read_text())

    def test_shell_managed_commit_hooks_check_formatting_without_rewriting_staged_source(self):
        for manager in ("husky", "existing"):
            with self.subTest(manager=manager), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.repository(root)
                self.reconcile(root, {"javascript", "python"}, "amend")
                (root / ".tools").mkdir(exist_ok=True)
                config_path = root / ".quality/config.json"
                config = json.loads(config_path.read_text())
                config["hook_manager"] = manager
                config_path.write_text(json.dumps(config))
                with (
                    patch.object(runner, "ROOT", root),
                    patch.object(runner, "run") as run,
                    patch.object(runner.sys, "argv", ["run.py", "commit"]),
                ):
                    self.assertEqual(runner.main(), 0)
                formatting = [
                    call.args
                    for call in run.call_args_list
                    if "prettier" in call.args[0] or "format" in call.args
                ]
                self.assertEqual(len(formatting), 2)
                self.assertTrue(all("--check" in arguments for arguments in formatting))
                self.assertTrue(all("--write" not in arguments for arguments in formatting))

    def test_husky_rerun_does_not_add_a_second_suite_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.reconcile(root, {"javascript"}, "amend")
            self.reconcile(root, {"javascript"}, "amend")
            for stage in ("pre-commit", "pre-push"):
                self.assertEqual((root / ".husky" / stage).read_text().count(".quality/run.py"), 1)

    def test_malformed_yaml_is_a_diagnostic_and_does_not_write_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, ".pre-commit-config.yaml", "repos: [invalid\n")
            with self.assertRaisesRegex(ValueError, "Invalid pre-commit YAML"):
                self.reconcile(root, {"python"}, "amend")
            self.assertFalse((root / ".quality").exists())

    def test_nested_legacy_eslint_and_scanner_configs_are_invoked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            self.write(root, "frontend/package.json", '{"devDependencies": {"eslint": "9.39.4"}}')
            self.write(root, "frontend/.eslintrc.json", '{"rules": {}}')
            self.write(root, "backend/gitleaks.toml", "[extend]\nuseDefault = true\n")
            self.write(root, "backend/.semgrep.yaml", "rules: []\n")
            self.write(root, "backend/trivy.yml", "severity: [HIGH, CRITICAL]\n")
            self.reconcile(root, {"javascript"}, "amend")
            for action in ("lint", "security"):
                with (
                    patch.object(runner, "ROOT", root),
                    patch.object(runner, "run") as run,
                    patch.object(runner.sys, "argv", ["run.py", action]),
                ):
                    self.assertEqual(runner.main(), 0)
                if action == "lint":
                    self.assertTrue(
                        any(
                            call.kwargs.get("environment") == {"ESLINT_USE_FLAT_CONFIG": "false"}
                            for call in run.call_args_list
                        )
                    )
                else:
                    for name in ("gitleaks.toml", ".semgrep.yaml", "trivy.yml"):
                        self.assertTrue(
                            any(
                                str(root / "backend" / name) in call.args
                                and call.kwargs.get("directory") == root / "backend"
                                for call in run.call_args_list
                            )
                        )


class BootstrapTests(unittest.TestCase):
    repository = DetectionTests.repository
    write = DetectionTests.write

    def test_supported_eslint_react_and_accessibility_rules_execute_for_js_and_ts(self):
        node = shutil.which("node")
        if node is None:
            self.fail("Node is required; install repository development prerequisites")
        cache = ROOT / ".tools"
        cache.mkdir(exist_ok=True)
        for language, extension in (("javascript", "jsx"), ("typescript", "tsx")):
            with self.subTest(language=language), tempfile.TemporaryDirectory(dir=cache) as temp:
                root = Path(temp)
                bootstrap.scaffold(root, {language, "react"})
                result = subprocess.run(  # noqa: S603 -- resolved Node and repository test harness
                    [node, str(ROOT / "tests/verify-eslint.mjs"), str(root), extension],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_application_security_excludes_generated_tool_files_and_dependency_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap.scaffold(root, {"python"})
            (root / ".tools").mkdir()
            (root / "application.py").write_text("value = 1\n")
            with patch.object(runner, "ROOT", root):
                sources = runner.source_files()
                self.assertIn(root / "application.py", sources)
                self.assertFalse(any(path.is_relative_to(root / ".quality") for path in sources))
                with (
                    patch.object(runner, "run") as run,
                    patch.object(sys, "argv", ["run.py", "security"]),
                ):
                    self.assertEqual(runner.main(), 0)
            trivy = [call for call in run.call_args_list if call.args[0].endswith("/trivy")]
            self.assertEqual(len(trivy), 1)
            arguments = trivy[0].args
            self.assertTrue(
                any(
                    arguments[index : index + 2] == ("--skip-dirs", ".quality")
                    for index in range(len(arguments) - 1)
                )
            )
            self.assertNotIn("--include-dev-deps", arguments)
            self.assertIn(".quality/", (root / ".semgrepignore").read_text())
            self.assertIn("\\.quality", (root / ".gitleaks.toml").read_text())

    def test_scoped_analyzer_overrides_do_not_replace_application_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git = self.repository(root)
            self.write(root, "package.json", '{"overrides": {"application-package": "1.0.0"}}')
            inventory, hooks = bootstrap.inspect_tooling(root, git)
            bootstrap.scaffold(
                root,
                {"typescript", "react"},
                policy="replace",
                inventory=inventory,
                hook_directory=hooks,
            )
            overrides = json.loads((root / "package.json").read_text())["overrides"]
            self.assertEqual(overrides["application-package"], "1.0.0")
            self.assertEqual(overrides["eslint"]["minimatch"], "11.0.5")
            self.assertNotIn("minimatch", overrides)

    def test_jsonc_preserves_comments_strings_values_and_nested_settings(self):
        text = """{
// existing preferences
"url": "https://example.test/a,}",
"[python]": {"editor.tabSize": 4,}, /* block */
"recommendations": ["existing.extension",],
}"""
        changes = {
            "url": "other",
            "[python]": {"editor.tabSize": 2, "editor.formatOnSave": True},
            "recommendations": ["existing.extension", "new.extension"],
        }
        merged = bootstrap.merge_jsonc(text, changes)
        self.assertIn("// existing preferences", merged)
        self.assertIn("/* block */", merged)
        parsed = bootstrap.parse_jsonc(merged)
        self.assertEqual(parsed["url"], "https://example.test/a,}")
        self.assertEqual(parsed["[python]"]["editor.tabSize"], 4)
        self.assertTrue(parsed["[python]"]["editor.formatOnSave"])
        self.assertEqual(parsed["recommendations"], ["existing.extension", "new.extension"])
        self.assertEqual(bootstrap.merge_jsonc(merged, changes), merged)

    def test_invalid_jsonc_is_rejected(self):
        for text in ("[]", '{"bad":}', "{unquoted: 3}", '{"duplicate": 1, "duplicate": 2}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                bootstrap.merge_jsonc(text, {"setting": True})

    def test_incompatible_settings_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "incompatible value type"):
            bootstrap.merge_jsonc('{"files.exclude": false}', {"files.exclude": {"**/.venv": True}})

    def test_existing_agents_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            original = "# Team rules\n\nKeep these existing behaviors.\n"
            (target / "AGENTS.md").write_text(original)
            bootstrap.scaffold(target, {"python"})
            result = (target / "AGENTS.md").read_text()
            self.assertTrue(result.startswith(original))
            self.assertIn("## Project-local quality suite", result)
            self.assertEqual(result.count("## Project-local quality suite"), 1)

    def test_symlinked_configuration_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "repo"
            target.mkdir()
            outside = Path(temp) / "outside"
            outside.mkdir()
            (target / ".vscode").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                bootstrap.scaffold(target, {"python"})
            self.assertEqual(list(outside.iterdir()), [])

    def test_scanner_policy_survives_reruns(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            bootstrap.scaffold(target, {"python"})
            config_path = target / ".quality/config.json"
            config = json.loads(config_path.read_text())
            config["severity"] = ["LOW"]
            config["semgrep_config"] = ".quality/local-rules.yaml"
            config["docker"]["images"] = ["example:1"]
            config_path.write_text(json.dumps(config))
            bootstrap.scaffold(target, {"python"})
            self.assertEqual(json.loads(config_path.read_text()), config)

    def test_schema_check_accepts_jsonc_recommendations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap.scaffold(root, {"python"})
            (root / ".tools").mkdir()
            (root / ".vscode/extensions.json").write_text(
                '{// comment\n"recommendations": ["existing.extension",],}\n'
            )
            with (
                patch.object(runner, "ROOT", root),
                patch.object(sys, "argv", ["run.py", "schemas"]),
                patch.object(runner, "run") as run,
            ):
                runner.main()
                self.assertIn("--schemafile", run.call_args.args)

    def test_profiles_are_idempotent(self):
        for languages in (
            {"python"},
            {"javascript"},
            {"typescript", "react"},
            {"python", "typescript"},
        ):
            with self.subTest(languages=languages), tempfile.TemporaryDirectory() as temp:
                target = Path(temp)
                with contextlib.redirect_stdout(io.StringIO()):
                    bootstrap.scaffold(target, languages)
                first = {
                    p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()
                }
                with contextlib.redirect_stdout(io.StringIO()):
                    bootstrap.scaffold(target, languages)
                second = {
                    p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()
                }
                self.assertEqual(first, second)
                self.assertIn((ROOT / "AGENTS.md").read_text(), (target / "AGENTS.md").read_text())
                self.assertEqual((target / "pyproject.toml").exists(), "python" in languages)
                self.assertEqual(
                    (target / ".pre-commit-config.yaml").exists(), "python" in languages
                )
                self.assertEqual((target / ".husky/pre-commit").exists(), "python" not in languages)
                self.assertEqual((target / "package.json").exists(), bool(languages - {"python"}))
                for path in target.rglob("*.py"):
                    compile(path.read_text(), str(path), "exec")
                if (target / "package.json").exists():
                    package = json.loads((target / "package.json").read_text())
                    self.assertIn("@types/node", package["devDependencies"])
                    self.assertNotIn(
                        "husky", package["devDependencies"]
                    ) if "python" in languages else (
                        self.assertIn("husky", package["devDependencies"])
                    )

    def test_conflict_does_not_partially_modify_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            original = "# existing configuration\n"
            (target / "pyproject.toml").write_text(original)
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                bootstrap.scaffold(target, {"python"})
            self.assertEqual(list(target.iterdir()), [target / "pyproject.toml"])
            self.assertEqual((target / "pyproject.toml").read_text(), original)

    def test_existing_tasks_not_duplicated_by_label(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / ".vscode").mkdir()
            original = {
                "version": "2.0.0",
                "tasks": [{"label": "Quality: check", "command": "custom"}],
            }
            (target / ".vscode/tasks.json").write_text(json.dumps(original))
            bootstrap.scaffold(target, {"python"})
            tasks = bootstrap.parse_jsonc((target / ".vscode/tasks.json").read_text())["tasks"]
            self.assertEqual(
                [t for t in tasks if t["label"] == "Quality: check"], original["tasks"]
            )

    def test_checksum_mismatch_never_installs_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = {
                "version": "1.0.0",
                "repository": "gitleaks/gitleaks",
                "assets": {"Linux-x86_64": ["gitleaks", "0" * 64]},
            }
            with (
                patch.object(installer, "ROOT", root),
                patch.object(
                    installer.urllib.request.OpenerDirector,
                    "open",
                    return_value=io.BytesIO(b"bad download"),
                ),
                self.assertRaisesRegex(ValueError, "Checksum mismatch"),
            ):
                installer.install_binary("gitleaks", spec, "Linux-x86_64")
            self.assertFalse((root / ".tools/bin/gitleaks").exists())

    def test_install_restores_resolved_lock_and_rejects_changed_direct_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.repository(root)
            for stage in ("pre-commit", "pre-push"):
                self.write(root, ".git/hooks/" + stage, "#!/bin/sh\nexit 0\n")
            bootstrap.scaffold(root, {"python"})
            python = root / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            lock = root / ".quality/locked/requirements.txt"
            lock.parent.mkdir()
            direct = root / ".quality/requirements.in"
            lock.write_text(direct.read_text() + "indirect-package==1.0.0\n")
            with (
                patch.object(installer, "ROOT", root),
                patch.object(installer.platform, "system", return_value="Linux"),
                patch.object(installer.platform, "machine", return_value="x86_64"),
                patch.object(installer, "run") as run,
                patch.object(installer, "install_binary"),
            ):
                self.assertEqual(installer.main([]), 0)
                self.assertEqual(run.call_args_list[0].args[-1], str(lock))
                self.assertIn(".quality/env.sh", (root / ".git/hooks/pre-push").read_text())
                direct.write_text(direct.read_text() + "new-package==1.0.0\n")
                with self.assertRaisesRegex(ValueError, "pins and lock disagree"):
                    installer.main([])

    def test_binary_receipt_detects_modified_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / ".tools/bin/gitleaks"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"original")
            binary.with_suffix(".sha256").write_text(
                "1.0.0 "
                + hashlib.sha256(b"correct").hexdigest()
                + " "
                + hashlib.sha256(b"original").hexdigest()
            )
            spec = {
                "version": "1.0.0",
                "repository": "gitleaks/gitleaks",
                "assets": {"Linux-x86_64": ["gitleaks", hashlib.sha256(b"correct").hexdigest()]},
            }
            with (
                patch.object(installer, "ROOT", root),
                patch.object(
                    installer.urllib.request.OpenerDirector,
                    "open",
                    return_value=io.BytesIO(b"correct"),
                ) as download,
            ):
                installer.install_binary("gitleaks", spec, "Linux-x86_64")
                download.assert_not_called()
            binary.write_bytes(b"modified")
            with (
                patch.object(installer, "ROOT", root),
                patch.object(
                    installer.urllib.request.OpenerDirector,
                    "open",
                    return_value=io.BytesIO(b"correct"),
                ),
            ):
                installer.install_binary("gitleaks", spec, "Linux-x86_64")
            self.assertEqual(binary.read_bytes(), b"correct")


class DockerTests(unittest.TestCase):
    def test_typecheck_does_not_silently_skip_non_src_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap.scaffold(root, {"typescript"})
            (root / "lib").mkdir()
            (root / "lib/main.ts").write_text("const answer: number = 42;\n")
            with (
                patch.object(runner, "ROOT", root),
                patch.object(sys, "argv", ["run.py", "types"]),
                patch.object(runner, "run") as run,
            ):
                runner.main()
            self.assertEqual(run.call_args.args[1], "--noEmit")

    def test_missing_compose_plugin_skips_only_compose(self):
        with (
            patch.object(runner.shutil, "which", return_value="/bin/docker"),
            patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)),
            patch.object(runner, "run") as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            runner.docker_checks([Path("compose.yaml")], {"builds": [], "images": []})
        run.assert_not_called()
        self.assertIn("(Compose v2)", output.getvalue())

    def test_docker_install_activates_existing_image_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".quality").mkdir()
            (root / ".quality/config.json").write_text('{"severity": ["HIGH", "CRITICAL"]}')
            with (
                patch.object(runner, "ROOT", root),
                patch.object(runner.shutil, "which", return_value="/bin/docker"),
                patch.object(
                    runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
                ),
                patch.object(runner, "run") as run,
            ):
                runner.docker_checks(
                    [],
                    {
                        "builds": [
                            {"dockerfile": "Dockerfile", "context": ".", "image": "example:1"}
                        ],
                        "images": [],
                    },
                )
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[:2], ("docker", "build"))
            self.assertEqual(run.call_args_list[1].args[1], "image")

    def test_missing_docker_is_explicit_skip(self):
        output = io.StringIO()
        with (
            patch.object(runner.shutil, "which", return_value=None),
            contextlib.redirect_stdout(output),
        ):
            runner.docker_checks([Path("compose.yaml")], {"builds": [], "images": []})
        self.assertIn("skipped due to missing local docker dependencies", output.getvalue())

    def test_compose_validation_does_not_require_daemon(self):
        with (
            patch.object(runner.shutil, "which", return_value="/bin/docker"),
            patch.object(
                runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as probe,
            patch.object(runner, "run") as run,
        ):
            runner.docker_checks([Path("compose.yaml")], {"builds": [], "images": []})
        probe.assert_called_once()
        self.assertIn("config", run.call_args.args)

    def test_invalid_compose_fails_not_skips(self):
        with (
            patch.object(runner.shutil, "which", return_value="/bin/docker"),
            patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
            patch.object(runner, "run", side_effect=subprocess.CalledProcessError(1, "docker")),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            runner.docker_checks([Path("compose.yaml")], {"builds": [], "images": []})

    def test_missing_daemon_skips_image_scanning(self):
        with (
            patch.object(runner.shutil, "which", return_value="/bin/docker"),
            patch.object(
                runner.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 1),
                ],
            ),
            patch.object(runner, "run") as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            runner.docker_checks([], {"builds": [], "images": ["example:1"]})
        run.assert_not_called()
        self.assertIn("accessible Docker daemon", output.getvalue())

    def test_missing_non_docker_tool_fails(self):
        with (
            patch.object(runner.shutil, "which", return_value=None),
            self.assertRaisesRegex(FileNotFoundError, "Required tool missing"),
        ):
            runner.run("/nonexistent/semgrep", "--version")


if __name__ == "__main__":
    unittest.main()
