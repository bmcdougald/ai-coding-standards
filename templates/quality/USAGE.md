# Project-local quality suite

## Who maintains the scanner dependencies?

Application scans target this project's artifacts, not the installed quality
suite. Maintainers audit and fix suite dependencies in the ai-coding-standards
checkout, using its separate `tooling:audit:python` and `tooling:audit:node`
commands. See that checkout's README section **Maintaining and scanning this tooling
repository** for the scan, dependency-update and validation workflow. Those audit
aliases are not required in application projects.

After reviewed fixes are published, pull the standards checkout and use the
explicit tooling-update workflow below to adopt them here.

## Tooling updates (not replacement)

Pull the latest reviewed `ai-coding-standards` checkout, then preview and apply:

```sh
bash ~/git/ai-coding-standards/scripts/bootstrap.sh \
  --target "$PWD" --update-tooling --preview
bash ~/git/ai-coding-standards/scripts/bootstrap.sh \
  --target "$PWD" --update-tooling
```

Apply prompts for confirmation; `--yes` explicitly confirms unattended updates.
Commit `.quality/baseline.json` and the resulting locks after review. The ownership
record limits updates to suite dependencies and compatible runtime scripts,
preserving application dependencies/configuration, analyzer rules and hooks.
Retained amendment dependencies stay unowned. Local ownership conflicts, major
upgrades, pre-1.0 minor upgrades and runtime API migrations stop before writes.
Use `--migrate-tooling` only for a supported reviewed transition. API 1 -> 2 isolates
Python tools under `.tools/venv`, retaining the application's `.venv` and rewiring
only suite commands and standard tool settings. Other exact version migrations
must be declared by maintainers; the flag never permits arbitrary breaking changes.
Projects predating the record need explicit `--adopt-tooling` after reviewing its
preview; unfamiliar runtime modifications cannot be adopted automatically.

Updates back up affected files under `.tools/backups/update-<id>/`, install without
running npm lifecycle scripts or reinstalling hooks, and validate both configured
hook stages. Suite formatting is check-only; retained custom hooks may have side
effects, and remote-dependent hooks still need a real push. Failed updates restore
manifests/locks/runtime but may leave installed environments changed: from the
standards checkout, run `bash scripts/bootstrap.sh --target /path/to/project --recover-tooling`.
It restores the pending backup and recreates the isolated tooling environment;
failures remain pending and retryable. To revert a successful update, specify
`--recover-tooling .tools/backups/update-<id>`. API 1 recovery uses the older shared
environment and cannot remove unrelated packages safely.
Recovery also suppresses npm lifecycle scripts; native application modules may
need a project-approved rebuild before application checks.
Review shared transitive lock changes and run application tests before
committing. Updating the standards checkout alone never changes an application.

## First setup

Python tooling is installed in `.tools/venv`, separately from the application's
`.venv`. Setup never installs application packages or creates an application
environment. Pylance/Pyright use the application interpreter when available; Ruff
and all scanner commands use the tooling environment.

Run `python3 .quality/run.py doctor` (or `status`) for JSON health/version/lock/hook
reports; unhealthy tool state returns failure. `freshness` reports the last security
scan and Trivy database metadata, including explicitly missing data. Docker dependency
availability is separate from validation success.

Prerequisites: Git, Python 3.11+ with `venv` support, and Node.js 22.13+ with npm
for JavaScript/TypeScript/React projects. Linux and macOS x86_64/arm64 are supported;
Linux also needs a runtime supported by the selected Semgrep wheel.
Docker and VS Code extensions are optional. No global scanner installations or
administrator permissions are needed.

Clone `ai-coding-standards`, then run its bootstrapper from that checkout or any
working directory:

```sh
bash ~/git/ai-coding-standards/scripts/bootstrap.sh
```

The script prompts for the destination repository path before making changes.
Enter an existing Git repository root (initialize a new directory with `git init`
first). Paths containing spaces and `~` are supported without shell quotes;
relative paths resolve from your working directory. Setup writes to that
destination, not automatically to the tools checkout.
Pass `--target /path/to/project` to skip this prompt. Noninteractive and
`--detect-only` runs require `--target`; they never assume the current directory.

Empty/documentation-only repositories prompt for planned languages.
Existing projects are inspected recursively: Python, JavaScript, TypeScript and
React are selected from source extensions and package manifests, without prompting.
Choose comma-separated languages when prompted: `python`, `javascript`, `typescript`, `react`.
React can accompany JavaScript or TypeScript. For unattended setup:

```sh
bash ~/git/ai-coding-standards/scripts/bootstrap.sh \
  --target "$PWD" --languages python,typescript,react
```

Use `--configure-only` to defer analyzer installation; reconciliation may need
local parser packages as described below.
Use `--detect-only` to print languages and their evidence without writes, downloads
or prompts; `--languages` explicitly overrides detection.
Git-ignored untracked files, dependency/vendor/build directories and suite
infrastructure are excluded; symlinks are not followed. JSX/TSX imply React.
Existing source in unsupported languages does not trigger an empty-project prompt:
setup stops and requires explicit supported language selection.

If existing analyzers/hooks are found, select **Leave**, **Amend**, or **Replace**:

- **Leave Existing Tooling & Checks in Place** performs no writes or installations.
- **Amend Existing Tooling & Checks** retains rules and existing checks, adds missing
  analyzers and suite checks, and preserves declared npm tool versions where compatible.
- **Replace Existing Tooling & Checks** backs up recognized root and nested scanner
  configurations/hooks and installs the baseline. Application metadata, test settings,
  TypeScript build configuration and application/build scripts remain intact.

For noninteractive setup, explicitly pass `--existing-tools leave|amend|replace`.
Backups are stored under ignored `.tools/backups/<id>/`; keep them until validation.
TOML/YAML amendments use pinned comment-preserving `tomlkit`/`ruamel.yaml` parsers
in `.tools/bootstrap`. The first reconciliation can download these parser packages
even with `--configure-only`, but analyzer installation remains deferred.
Review the generated files and commit them, including both dependency lockfiles.
Do not commit `.venv`, `node_modules`, `.tools`, or secrets.

## Fresh clones

```sh
python3 .quality/install.py
```

This restores local tools from lockfiles and installs the appropriate Git hooks.
Rerun after recreating environments or cloning: Git hooks are not tracked.
Amended custom shell hooks must be retained through your existing hook-distribution
workflow; Git does not commit hook-directory files.

## What is installed

| Tools                                        | Location                        | Purpose                                               |
| -------------------------------------------- | ------------------------------- | ----------------------------------------------------- |
| Prettier                                     | `node_modules`                  | Format JS/TS, JSON, YAML, Markdown and web files      |
| ESLint and selected TS/React plugins         | `node_modules`                  | Lint JS/TS/JSX/TSX and React Hooks/accessibility      |
| TypeScript                                   | `node_modules`                  | Check types, including JavaScript with `checkJs`      |
| Ruff, Pyright (Python profiles)              | `.tools/venv`                   | Python formatting, lint and types                     |
| check-jsonschema                             | `.tools/venv`                   | Validate configured JSON/YAML schemas                 |
| Semgrep                                      | `.tools/venv`                   | Source-code security analysis, metrics disabled       |
| Gitleaks                                     | `.tools/bin`                    | Staged and working-tree secret detection              |
| Trivy                                        | `.tools/bin`                    | Dependency, secret, infrastructure and image scanning |
| Hadolint                                     | `.tools/bin`                    | Dockerfile lint without Docker                        |
| pre-commit (Python/mixed), Husky (Node-only) | `.tools/venv` or `node_modules` | Commit and push checks                                |

Release binaries have pinned SHA-256 checksums. Initial Python dependency
resolution generates `.quality/locked/requirements.txt`; npm generates `package-lock.json`.
The resolved Python lock records the complete tooling graph, including pip;
`.quality/requirements.in` holds direct version pins. Application scans exclude
that tooling lock and the installed tooling directories. Tool dependency auditing
and upgrades are maintained in the `ai-coding-standards` source repository rather
than blocking application hooks with scanner dependencies.
Review dependency updates and regenerate locks deliberately. Dependencies may
have advisories even when pinned: security scanning is still required.

The baseline uses supported ESLint 10 with `@eslint-react/eslint-plugin`,
`eslint-plugin-react-hooks` and `eslint-plugin-jsx-a11y-x`. React rule names change
from the previous plugins; review migration results before adopting replacement
mode. Scoped analyzer-only minimatch 11.0.5 overrides fix the current glob-pattern
advisory, without overriding application dependency choices.
Existing projects with legacy ESLint configurations can retain them in amend
mode, but remain on their chosen version; ESLint 10 cannot execute `.eslintrc`.
Use replace for the reviewed supported baseline, or migrate the existing config
and dependencies manually. No peer-dependency checks are bypassed.

## Usage

```sh
.tools/venv/bin/python .quality/run.py format    # apply formatting
.tools/venv/bin/python .quality/run.py check     # format check, lint, types, schemas, Docker
.tools/venv/bin/python .quality/run.py security  # Gitleaks, Semgrep, Trivy
.tools/venv/bin/python .quality/run.py docker    # Docker runtime checks only
```

Individual actions: `lint`, `types`, `schemas`, `commit`, `push`.
Husky/custom shell commit hooks check formatting without rewriting staged source.
Run `format`, review changes, then `git add` before committing. The pre-commit
framework can apply formatting because it detects changed files and rejects that
commit attempt until they are reviewed and staged.
Node profiles also provide `npm run quality`, `quality:format`, `quality:types`,
`quality:security`, and `quality:docker`.
Existing script names are preserved. If one already had a different purpose,
use the direct runner or VS Code Quality tasks instead of assuming that npm alias
was replaced.

Empty projects initially include `src/**/*`; existing projects without a
TypeScript config include source recursively. Existing `tsconfig.json` is preserved
unchanged, including project references and exclusions. Review inclusion for
monorepos and use the existing workflow to install application dependencies.
Declared npm workspaces are installed through their workspace root; standalone
nested npm packages get their own installation. ESLint runs in each discovered
Node project; TypeScript checks each discovered config, and Pyright checks each
Python project. Ruff discovers nested settings normally. Non-npm projects and
external hook directories stop for manual integration rather than being converted.
Workspace patterns support `*`, `?`, character classes and `**`; brace/extglob
patterns require manual integration. Pre-commit `.yml` aliases are backed up and
normalized to `.yaml` so default pre-commit commands work, preserving existing checks.
Hadolint uses the nearest configured Dockerfile scope. Push/security also run
discovered scoped Gitleaks, Semgrep and Trivy configuration; staged Gitleaks uses
the root configuration.
JavaScript type checking is strict: add JSDoc types when needed.
Python-only projects do not install Node/Prettier; their Python formatter is Ruff.

Commit hooks format/lint, validate schemas, lint Dockerfiles and check staged
secrets. Push hooks perform the full checks and security scans. Checks fail when
required non-Docker tools are missing; reinstall tools rather than bypass hooks.
No application test framework is imposed: wire your project's test command
separately once its framework is selected.

## VS Code

Open the repository and review the recommended extensions. They are optional
IDE adapters, not CLI prerequisites. Accepting recommendations enables Ruff or
Prettier formatting on save, ESLint fixes and language diagnostics. Python uses
the local interpreter and Pylance; no second Python type-checker extension is added.
Use **Tasks: Run Task** and select a **Quality:** task for checks without extensions.

Existing JSONC comments, settings and unrelated tasks are preserved. Conflicting
values remain unchanged and are reported; inspect them to align the IDE with
project configuration. The selected existing-tool policy controls analyzer/hook
changes. Profiles cannot be switched automatically after setup.
On reruns the saved profile is reused; new language evidence stops setup for
reviewed migration. Amendment preserves existing analyzer rules rather than
blindly merging possibly conflicting baseline rules. pre-commit YAML checks are
extended; Husky/custom shell hooks run suite checks before their original bodies.
Non-shell hooks cannot be safely amended and require leave or replacement.
Replacement activates only the selected suite hook manager. Arbitrary custom
application/scanner scripts are preserved, not rewritten.
Installation formats suite/IDE configuration only, not existing application source.

## Configuration and Docker

Edit `.quality/config.json` for scanner rules, vulnerability severity thresholds
(default HIGH/CRITICAL) and optional Docker targets. Semgrep defaults to the
explicit `p/security-audit` rule pack; it downloads rules without uploading source
or enabling metrics. Use a reviewed local rule file for offline scans.
Trivy downloads vulnerability databases; caches are stored under `.tools/cache`.
Application scans exclude `.quality`, `.venv`, `.tools` and `node_modules`;
The npm advisory audit includes application development dependencies and shared
transitives, excluding only suite-owned roots; it fails on ownership/lock drift.
Trivy handles application Python requirements/locks and other filesystem artifacts,
but npm manifests/locks are excluded there to avoid ownership-blind duplicate audits.
Offline scanning requires prepopulated databases and a reviewed local Semgrep rule
file. Pass `security --offline` to disable database updates and use npm advisory
results cached against the exact lock for at most 24 hours; missing/stale caches fail.
Receipts record local rule hashes, but registry packs are not immutably pinned.

Add application schemas to `.quality/schemas.json` as
`{"schema": "schemas/app.json", "files": ["config.yaml"]}` entries in `checks`.
The initial schema validates VS Code extension recommendations. Formatting alone
does not validate arbitrary application configuration.

Dockerfiles are linted by Hadolint and scanned by Trivy without Docker.
Compose files are detected on future runs and validated with `docker compose config -q`.
Compose validation needs Docker CLI and Compose v2, not a running daemon.

Builds require explicit, approved targets; the suite does not guess build contexts
or build every Dockerfile:

```json
{
  "docker": {
    "builds": [{ "dockerfile": "Dockerfile", "context": ".", "image": "my-project:dev" }],
    "images": ["my-project:dev"]
  }
}
```

Configured builds and image scans require an accessible Docker daemon. Missing
Docker dependencies produce **SKIPPED: skipped due to missing local docker
dependencies**, then run automatically after installation. Invalid Compose,
failed builds and scan findings fail normally. A dependency skip is not a passed
scan; inspect output before claiming validation.

## Optional application CI

CI is not required for local setup and can be added later. Follow the separate
**Application CI setup and usage** guide in your standards checkout
(`templates/quality/CI.md`), covering provider configuration, application tests and
remote merge protection. The guide stays there; CI setup adds no documentation here.
