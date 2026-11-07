from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bootstrap as existing
from test_bootstrap import ROOT, bootstrap, installer, maintenance, runner


class PhaseTwoTests(unittest.TestCase):
    project = existing.ToolingUpdateTests.project
    candidate = existing.ToolingUpdateTests.candidate

    def audit_fixture(self, root: Path) -> dict:
        self.project(root, {"javascript"})
        package = {"devDependencies": {"eslint": "10.14.0", "application-test": "1.0.0"}}
        (root / "package.json").write_text(json.dumps(package))
        lock = {
            "packages": {
                "": package,
                "node_modules/eslint": {"version": "10.14.0", "dependencies": {"shared": "1"}},
                "node_modules/application-test": {
                    "version": "1.0.0",
                    "dependencies": {"shared": "1"},
                },
                "node_modules/shared": {"version": "1.0.0"},
            }
        }
        (root / "package-lock.json").write_text(json.dumps(lock))
        (root / ".quality/baseline.json").write_text(
            json.dumps({"node": {"package.json": {"devDependencies": {"eslint": "10.14.0"}}}})
        )
        return {
            "node_projects": ["."],
            "severity": ["HIGH", "CRITICAL"],
            "languages": ["javascript"],
        }

    def test_shared_transitives_and_application_dev_dependencies_are_in_scope(self):
        packages = {
            "": {"devDependencies": {"scanner": "1", "test-framework": "1"}},
            "node_modules/scanner": {"dependencies": {"shared": "1", "tool-only": "1"}},
            "node_modules/test-framework": {"dependencies": {"shared": "1"}},
            "node_modules/shared": {},
            "node_modules/tool-only": {},
        }
        nodes = maintenance.application_nodes(
            packages, {"package.json": {"devDependencies": {"scanner": "1"}}}
        )
        self.assertEqual(nodes, {"node_modules/test-framework", "node_modules/shared"})

    def test_workspace_application_roots_are_traversed(self):
        packages = {
            "": {"devDependencies": {"scanner": "1"}},
            "packages/app": {"devDependencies": {"tests": "1"}},
            "node_modules/scanner": {},
            "node_modules/tests": {"optionalDependencies": {"platform-only": "1"}},
        }
        self.assertEqual(
            maintenance.application_nodes(
                packages, {"package.json": {"devDependencies": {"scanner": "1"}}}
            ),
            {"node_modules/tests"},
        )

    def test_application_root_peers_are_audited_and_peer_drift_is_rejected(self):
        packages = {
            "": {
                "peerDependencies": {"application-peer": "1", "optional-peer": "1"},
                "peerDependenciesMeta": {"optional-peer": {"optional": True}},
            },
            "node_modules/application-peer": {},
        }
        self.assertEqual(
            maintenance.application_nodes(packages, {}), {"node_modules/application-peer"}
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            config = self.audit_fixture(root)
            package = json.loads((root / "package.json").read_text())
            package["peerDependencies"] = {"application-peer": "1"}
            (root / "package.json").write_text(json.dumps(package))
            with patch.object(maintenance.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Manifest/lock drift"):
                    maintenance.audit_node(root, config)
                run.assert_not_called()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(installer.npm_action(root), "install")

    def test_application_audit_rejects_shared_findings_but_not_suite_only_findings(self):
        for node, fails in (("node_modules/eslint", False), ("node_modules/shared", True)):
            with self.subTest(node=node), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "project"
                config = self.audit_fixture(root)
                report = {"vulnerabilities": {"affected": {"nodes": [node], "severity": "high"}}}
                with (
                    patch.object(
                        maintenance.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            [], 1, stdout=json.dumps(report), stderr=""
                        ),
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    if fails:
                        with self.assertRaisesRegex(
                            ValueError, "Application dependency vulnerabilities"
                        ):
                            maintenance.audit_node(root, config)
                    else:
                        maintenance.audit_node(root, config)

    def test_audit_network_errors_do_not_look_like_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            config = self.audit_fixture(root)
            with (
                patch.object(
                    maintenance.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [], 1, stdout='{"error": {"code": "ENETUNREACH"}}', stderr=""
                    ),
                ),
                self.assertRaisesRegex(ValueError, "advisory audit failed"),
            ):
                maintenance.audit_node(root, config)

    def test_offline_audits_require_recent_cache_bound_to_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            config = self.audit_fixture(root)
            digest = hashlib.sha256((root / "package-lock.json").read_bytes()).hexdigest()
            cache = root / ".tools/cache/npm-audits" / (digest + ".json")
            cache.parent.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "cached result"):
                maintenance.audit_node(root, config, offline=True)
            record = {
                "timestamp": datetime.datetime.now(datetime.UTC).timestamp(),
                "report": {"vulnerabilities": {}},
            }
            cache.write_text(json.dumps(record))
            with patch.object(maintenance.subprocess, "run") as run:
                maintenance.audit_node(root, config, offline=True)
                run.assert_not_called()
            record["timestamp"] -= 86401
            cache.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "stale"):
                maintenance.audit_node(root, config, offline=True)

    def test_python_tool_installer_never_writes_application_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            marker = root / ".venv/application-state"
            marker.parent.mkdir()
            marker.write_text("must stay unchanged")
            with (
                patch.object(installer, "ROOT", root),
                patch.object(installer.platform, "system", return_value="Linux"),
                patch.object(installer.platform, "machine", return_value="x86_64"),
                patch.object(installer, "run") as run,
                patch.object(installer, "install_binary"),
                patch.object(
                    installer.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [], 0, stdout=(root / ".quality/requirements.in").read_text()
                    ),
                ),
            ):
                installer.main(["--restore-tools"])
            self.assertEqual(marker.read_text(), "must stay unchanged")
            self.assertTrue(
                any(str(root / ".tools/venv") in call.args for call in run.call_args_list)
            )
            self.assertFalse(any(str(root / ".venv") in call.args for call in run.call_args_list))

    def test_published_runtime_migration_is_explicit_and_preserves_app_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            config_path = root / ".quality/config.json"
            config = json.loads(config_path.read_text())
            config["runtime_api"] = 1
            config_path.write_text(json.dumps(config))
            baseline_path = root / ".quality/baseline.json"
            baseline = json.loads(baseline_path.read_text())
            baseline["runtime_api"] = 1
            baseline["runtime"].pop(".quality/maintenance.py")
            (root / ".quality/maintenance.py").unlink()
            git = shutil.which("git")
            if git is None:
                self.fail("Git required")
            for name in baseline["runtime"]:
                result = subprocess.run(  # noqa: S603 -- Git and fixed committed fixture revision
                    [git, "show", "59aa88a:" + name],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                (root / name).write_text(result.stdout)
                baseline["runtime"][name] = hashlib.sha256(result.stdout.encode()).hexdigest()
            baseline_path.write_text(json.dumps(baseline))
            hook = root / ".git/hooks/pre-commit"
            hook.write_text(f"#!/bin/sh\nINSTALL_PYTHON='{root}/.venv/bin/python'\n")
            (root / ".pre-commit-config.yaml").write_text(
                "repos:\n- repo: local\n  hooks:\n  - id: quality-commit\n"
                "    entry: .venv/bin/python .quality/run.py commit\n"
                "  - id: app-tests\n    entry: .venv/bin/python -m pytest\n"
            )
            app = root / ".venv/application-marker"
            app.parent.mkdir()
            app.write_text("application only")
            with self.assertRaisesRegex(ValueError, "migration"):
                bootstrap.plan_tooling_update(root)
            edits, _ = bootstrap.plan_tooling_update(root, migrate=True)
            self.assertIn(".quality/maintenance.py", edits)
            self.assertIn(".tools/venv/bin/python", edits[".git/hooks/pre-commit"])
            self.assertIn("entry: .venv/bin/python -m pytest", edits[".pre-commit-config.yaml"])
            self.assertNotIn(".vscode/settings.json", edits)
            self.assertEqual(app.read_text(), "application only")

    def test_major_version_migrations_require_explicit_reviewed_exact_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source = Path(temp) / "project", Path(temp) / "source"
            self.project(root)
            self.candidate(source)
            manifest_path = source / "templates/quality/versions.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["node"]["eslint"] = "11.0.0"
            manifest_path.write_text(json.dumps(manifest))
            with (
                patch.object(bootstrap, "SOURCE", source),
                self.assertRaisesRegex(ValueError, "migration"),
            ):
                bootstrap.plan_tooling_update(root, migrate=True)
            manifest["reviewed_version_migrations"] = [
                {"tool": "eslint", "from": "10.14.0", "to": "11.0.0"}
            ]
            manifest_path.write_text(json.dumps(manifest))
            with patch.object(bootstrap, "SOURCE", source):
                edits, _ = bootstrap.plan_tooling_update(root, migrate=True)
            self.assertEqual(
                json.loads(edits["package.json"])["devDependencies"]["eslint"], "11.0.0"
            )

    def test_recovery_restores_files_and_retains_marker_if_installation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            backup = root / ".tools/backups/update-test"
            (backup / ".quality").mkdir(parents=True)
            original = (root / ".quality/config.json").read_bytes()
            (backup / ".quality/config.json").write_bytes(original)
            (backup / "restore.json").write_text(
                json.dumps({"restore": [".quality/config.json"], "remove_if_created": []})
            )
            (root / ".quality/config.json").write_text("{}")
            app = root / ".venv/application-marker"
            app.parent.mkdir()
            app.write_text("unchanged")
            tool_python = root / ".tools/venv/bin/python"
            tool_python.parent.mkdir(parents=True)
            tool_python.write_text("tool interpreter")
            with (
                patch.object(bootstrap.sys, "executable", str(tool_python)),
                patch.object(
                    bootstrap.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, ["installer"]),
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                bootstrap.recover_tooling(root, ".tools/backups/update-test")
            self.assertEqual((root / ".quality/config.json").read_bytes(), original)
            self.assertFalse(tool_python.exists())
            self.assertTrue((root / ".tools/update-pending.json").exists())
            with (
                patch.object(bootstrap.sys, "executable", str(tool_python)),
                patch.object(bootstrap.subprocess, "run") as run,
            ):
                bootstrap.recover_tooling(root)
                self.assertIn("--restore-tools", run.call_args.args[0])
                self.assertEqual(
                    run.call_args.args[0][0], str(Path(bootstrap.sys.base_prefix) / "bin/python3")
                )
            self.assertFalse((root / ".tools/update-pending.json").exists())
            self.assertEqual(app.read_text(), "unchanged")

    def test_doctor_reports_missing_tools_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            report = maintenance.doctor(
                root, json.loads((root / ".quality/config.json").read_text())
            )
            self.assertFalse(report["ok"])
            self.assertTrue(any("environment is missing" in issue for issue in report["issues"]))
            self.assertEqual(report["freshness"]["last_scan"], {"status": "missing"})

    def test_freshness_receipt_records_local_rule_digest_and_offline_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules.yml"
            rules.write_text("rules: []\n")
            maintenance.scan_receipt(root, {"semgrep_config": str(rules)}, True)
            report = maintenance.freshness(root)
            self.assertTrue(report["last_scan"]["offline"])
            self.assertEqual(
                report["last_scan"]["local_rules_sha256"],
                hashlib.sha256(rules.read_bytes()).hexdigest(),
            )

    def test_ci_generation_is_explicit_and_never_overwrites_existing_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            workflow = root / ".github/workflows/quality.yml"
            self.assertFalse(workflow.exists())
            with contextlib.redirect_stdout(io.StringIO()):
                bootstrap.scaffold(root, {"python"}, ci=True)
            self.assertIn("permissions:\n  contents: read", workflow.read_text())
            workflow.write_text("name: existing\n")
            with self.assertRaisesRegex(ValueError, "manual reconciliation"):
                bootstrap.scaffold(root, {"python"}, ci=True)
            self.assertEqual(workflow.read_text(), "name: existing\n")

    def test_offline_scanning_rejects_unpinned_registry_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            self.project(root, {"python"})
            with (
                patch.object(runner, "ROOT", root),
                patch.object(runner, "run"),
                patch.object(runner.sys, "argv", ["run.py", "security", "--offline"]),
                self.assertRaisesRegex(ValueError, "local rule file"),
            ):
                runner.main()

    def test_retained_shell_hooks_use_application_python_while_suite_uses_tool_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            git = shutil.which("git")
            if git is None:
                self.fail("Git required")
            subprocess.run(  # noqa: S603 -- resolved Git, fixed test repository
                [git, "init", "-q", str(root)], check=True
            )
            hook = root / ".git/hooks/pre-commit"
            hook.write_text("#!/bin/sh\npython -c ignored >> observations\n")
            inventory, directory = bootstrap.inspect_tooling(root, git)
            with contextlib.redirect_stdout(io.StringIO()):
                bootstrap.scaffold(
                    root, {"python"}, policy="amend", inventory=inventory, hook_directory=directory
                )
            for relative, message in (
                (".venv/bin/python", "application"),
                (".tools/venv/bin/python", "tooling"),
            ):
                executable = root / relative
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text(f"#!/bin/sh\nprintf '{message}\\n' >> observations\n")
                executable.chmod(0o755)
            subprocess.run(  # noqa: S603 -- fixture-local generated hook and fixed arguments
                [str(hook)], cwd=root, check=True
            )
            self.assertEqual(
                (root / "observations").read_text().splitlines(), ["tooling", "application"]
            )
