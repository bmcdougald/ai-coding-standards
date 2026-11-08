# Application CI setup and usage

CI is optional and can be added **after** the basic local tooling setup. This guide
stays in the standards checkout; CI setup adds no documentation to the target repository. Local IDE
checks and Git hooks do not require a CI provider or any remote CI configuration.

One provider-neutral runner uses the same pinned scanners and application
exclusions as local checks. Small GitHub, GitLab and Jenkins adapters supply
scheduling and result presentation. This configures the **target application
repository**, not a new pipeline for the standards repository, and does not
replace your application build/deployment system.

## 1. Configure CI when ready

Run the following from your cloned `ai-coding-standards` checkout. The target must
already have its local quality suite configured:

```sh
# Discovery only: no target files are changed. Review/edit the proposed commands.
bash scripts/bootstrap.sh --target /path/to/application \
  --ci-preview > /tmp/application-ci-reviewed.json

# Add CI without replacing or reinstalling the existing quality suite.
bash scripts/bootstrap.sh --target /path/to/application --ci-only \
  --ci-providers github,gitlab,jenkins --ci-config /tmp/application-ci-reviewed.json
```

Select only the providers you use. GitHub is the default when no provider is
specified. Optionally add `--ci` or `--ci-providers` during initial bootstrap;
keeping CI as a later step makes the basic setup simpler.

Older installations must first adopt the current runtime through the guarded
`--update-tooling` workflow (`--migrate-tooling` for API 1). CI-only setup checks
runtime ownership/compatibility rather than installing a runner that calls
unsupported checks.

Interactive setup proposes application setup/test commands and requires approval.
Noninteractive setup with discovered commands requires `--ci-config`; `--yes` is
not a shortcut around this review. Existing `.quality/ci.json` is retained unless
an explicitly reviewed replacement is supplied. Changed CI files are backed up.

Existing different native pipelines are **never overwritten**, even with
`--existing-tools replace`: integrate the generated `.quality/ci-adapters/`
snippets manually. This also applies to upgrading an earlier `--ci` workflow.
Existing test workflows are also retained; review them for overlapping test
runs and consolidate them yourself only after verifying equivalent coverage.
The CI runner is owned/fingerprinted and participates in `--update-tooling`;
application commands and customized pipelines remain application-owned.

## 2. Review application setup and tests

Discovery proposes npm `test:ci` and dedicated unit/integration/e2e scripts,
falling back to `test`; pytest configuration/imports; unittest-based tests; and
Playwright config with an existing `@playwright/test` dependency. It excludes
tooling/generated directories, supports nested projects, and does not install
a new test framework. Ambiguous Python tests are reported for manual configuration.
Existing application test scripts are reused, not rewritten.

Python Playwright is also detected from `playwright`/`pytest-playwright` in the
selected requirements or installed package dependencies/extras, or Playwright
imports in tests and `conftest.py`. Discovery proposes
`{python} -m playwright install --with-deps` after application dependency setup.
Where Playwright and both browser tests in `tests/e2e` and other pytest tests
are detected, discovery proposes `pytest --ignore tests/e2e` and a separate
`pytest tests/e2e` command. Review pytest collection settings, markers and custom
paths to ensure this split covers the intended tests without duplication.
When a safe split is not identified, the existing pytest command remains intact.
Select required browsers in the reviewed argv (for example, append `chromium`);
otherwise Playwright installs its default browsers. Imports alone do not install
the Python package: ensure your application dependency setup includes it.
Existing `.quality/ci.json` commands remain preserved, including during preview.
To enable browser setup in an already configured project, edit a reviewed copy
of that JSON and pass it through `--ci-config`; discovery does not rewrite saved commands.

Review aggregate/workspace scripts to avoid running the same suite twice, and
replace watch-mode commands with CI variants. Discovery cannot infer databases,
service containers, credentials, native builds, private registries, Python
dependency groups or arbitrary test harnesses. Add these explicitly.
Optional test command `"suite": "general"` or `"suite": "browser"` selects its
parallel job; omitted values default to `general` for compatibility. Classify
only commands you have verified, and avoid putting an aggregate command that
already includes browser tests alongside a separate browser command.
Tests already run by custom hooks must be represented here or in the application's
existing pipeline; untracked Git hooks are not recreated or assumed present on CI.

The application-owned `.quality/ci.json` contains named argv commands:

This JSON lists only application setup/tests. Formatting, lint, types, schemas,
Gitleaks, Semgrep, dependency audits and Trivy are built into the CI runner and
do not need to be repeated in this file.

```json
{
  "schema": 1,
  "timeout_seconds": 1800,
  "setup": [
    {
      "name": "Create application environment",
      "cwd": ".",
      "argv": ["{base_python}", "-m", "venv", ".venv"]
    },
    {
      "name": "Install application and test dependencies",
      "cwd": ".",
      "argv": ["{python}", "-m", "pip", "install", "-r", "requirements-test.txt"]
    }
  ],
  "tests": [
    {
      "name": "Unit and integration tests",
      "cwd": ".",
      "argv": ["{python}", "-m", "pytest"]
    }
  ]
}
```

`cwd` must stay inside the repository without symlinks. Commands run as argv, not
shell interpolation; `{base_python}` selects the runner interpreter and
`{python}` selects a nested/root application `.venv`. `CI=true` and check-only
formatting are enforced. Each command has the configured timeout.
Use application lockfiles/install commands appropriate to your project.

Playwright setup proposes installing browsers and Linux system dependencies using
the application's installed version. It may need an ephemeral runner with package
installation permission. Review its `webServer`/base URL and services, and ensure
the application is built before tests. npm rebuild commands enable application
lifecycle scripts only after approval. Never place credentials in this JSON or
test command arguments; use appropriately scoped CI settings.

Conventional `test-results/`, `playwright-report/` and `blob-report/` outputs are
added to Git/Prettier ignores without removing existing entries. Configure other
custom output directories in the application's own analyzer/ignore settings.

## 3. Run and understand the checks

Run these commands from the target application repository:

```sh
python3 .quality/ci.py all
# Individual stages: setup, quality, security, tests, summary
```

`setup` restores scanners without npm lifecycle scripts, then executes approved
application setup commands. `quality` performs check-only formatting, lint, types,
schemas and applicable Docker checks. `security` runs Gitleaks, Semgrep,
ownership-aware npm audits, and Trivy vulnerability/secret/misconfiguration scans.
Quality/security checks continue independently after failures so the summary can
show multiple failing checks. Setup failure stops subsequent stages. Any failed
check/test returns a nonzero gate result.

Docker behavior matches local scans: missing CLI/Compose/daemon produces explicit
**SKIPPED** results, not successful validations. Available Docker enables configured
Compose/build/image checks automatically; invalid configurations or failed scans
fail CI. Image scans need explicit build/image targets in `.quality/config.json`;
CI does not guess Docker build arguments or deploy images.

No result artifacts are uploaded. GitHub displays an inline job summary table;
GitLab prints the table in its job log; Jenkins displays build counts/console
results. GitHub and Jenkins run independent check and reviewed test-category jobs
in parallel, each with isolated workspace and tool restoration. Their combined
summary reports each job's passed/failed/skipped status; each job log has details.
GitHub uses job outputs, Jenkins uses in-memory pipeline results. GitLab deliberately
uses one job so its combined summary needs neither artifacts nor an API token.
Parallel jobs restore tools independently and may increase runner usage.
The table shows passed/failed/skipped checks and test-command outcomes;
individual assertions, vulnerability findings, and browser diagnostics remain in
the job log, not an attached report. No tests configured is an explicit skip.
Review test/log output for sensitive data and apply provider retention/access policies.

## 4. Configure the CI provider

These steps require access to the target repository's build system. Generated
files alone do not register Jenkins jobs, provision runners, configure webhooks,
or enforce merge protection.

### GitHub Actions

- The generated `.github/workflows/quality.yml` runs on all branch pushes, PR updates,
  and manual dispatch. Post-merge branch pushes run it again.
- Enable Actions and review allowed actions/fork approvals.
- Make the `Full CI summary` job required using branch rules/rulesets. It fails
  for failed, cancelled or missing job results.
- If using a merge queue, add the provider's `merge_group` trigger.
- Official actions are commit-pinned and checkout credentials are not persisted.
  Adjust runtime versions to the application's supported Python/Node versions.

### GitLab CI

- The generated `.gitlab-ci.yml` runs on branch pushes, MRs targeting
  `main`/`master`, and manual runs.
- An open MR can produce both push and MR pipelines; this deliberately avoids
  suppressing push checks for MRs targeting other branches.
- Supply an isolated Linux runner/executor with Git, Python/venv and supported
  Node/npm, or adapt your existing image.
- Enable successful-pipeline merge requirements and protect `main`/`master`.

### Jenkins

- Create a Multibranch Pipeline using the generated `Jenkinsfile`.
- Install the relevant SCM branch-source/Declarative Pipeline plugins.
- Configure push webhooks, branch discovery, and PR discovery/target filters for
  `main`/`master`.
- Provide an isolated `linux` agent with Git, Python/venv and supported Node/npm.
- Provide enough executor capacity for parallel checks; each uses a separate workspace.
- Configure SCM status reporting and required checks through your integration.

## 5. Validate before enforcing merges

Do not run untrusted PR code with secrets, privileged shared agents, or a
privileged Docker socket. Do not use `pull_request_target` to execute PR code.
Protect changes to CI configuration through review/CODEOWNERS or equivalent.
Validate each provider on the application's actual hosted runner before relying
on merge enforcement; local tests cannot certify remote webhooks/plugins/settings.

Verify a passing push, a failing test/check, PR/MR updates, and a post-merge run.
Confirm summaries and required checks behave as expected, and inspect all skips.
The standards repository's independent scanner dependency audits remain separate:
accepted tooling findings are visible failures there, not application gates.
