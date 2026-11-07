# AI coding standards

Bootstrap a project-local quality and security suite for new Python, JavaScript,
TypeScript, React, or mixed Git repositories, including existing source trees.
No globally installed scanners,
Docker, or mandatory VS Code extensions are required.

## Setup

Prerequisites: Git, Python 3.11+ with `venv`; Node.js 22.13+ and npm for Node
profiles. Initial support: Linux/macOS x86_64 and arm64.

```sh
git clone https://github.com/bmcdougald/ai-coding-standards.git ~/git/ai-coding-standards
mkdir -p ~/git/your-project-name
git -C ~/git/your-project-name init
cd ~/git/ai-coding-standards
bash scripts/bootstrap.sh
# At the repository-path prompt, enter: ~/git/your-project-name
```

Run the script from the cloned tools repository or any working directory.
It prompts for the **destination Git repository path**, then performs setup there,
not in the tools checkout. The destination must already exist and be initialized
with `git init`. Paths containing spaces and `~` are supported; enter the path
without shell quotes. Relative paths resolve from the directory you ran the script in.
No destination is selected implicitly.

For automated runs, supply the destination explicitly:

```sh
bash ~/git/ai-coding-standards/scripts/bootstrap.sh \
  --target ~/git/temp/my-project --languages python --existing-tools amend
```

Empty or documentation-only repositories prompt for planned languages.
Existing repositories automatically detect supported languages from source files
and package manifests in subfolders, without a language prompt.
Pass `--languages python,typescript,react` to override detection.
Noninteractive and `--detect-only` runs require `--target`. Use `--configure-only`
to generate files without installing tools.

Preview detection without modifying anything:

```sh
bash ~/git/ai-coding-standards/scripts/bootstrap.sh --target /path/to/repo --detect-only
```

Detection respects Git ignore rules for untracked files and excludes dependency,
vendor, build, virtual-environment and suite infrastructure directories. It does
not follow symlinks. JSX/TSX imply the React profile; package manifests can also
identify React. Unsupported-language-only projects stop with an explicit message.
Existing configured profiles are reused; newly discovered languages require a
reviewed migration rather than silently changing hook managers.

When existing analyzers or hooks are found, choose:

1. **Leave Existing Tooling & Checks in Place**: read-only; nothing is installed or changed.
2. **Amend Existing Tooling & Checks**: keep analyzer rules and existing checks;
   add missing tooling and suite checks without replacing tests.
3. **Replace Existing Tooling & Checks**: back up recognized root/nested analyzer
   and hook configuration, then standardize it with the suite.

For unattended runs, pass `--existing-tools leave`, `amend`, or `replace`.
Reruns reuse the chosen policy; newly discovered tooling configuration requires
another reviewed choice rather than silently replacing it.
Amendment is the safer default for established projects. Replacement preserves
application metadata, TypeScript build configuration, tests and build scripts.
Retaining legacy ESLint configuration requires a declared pre-10 exact, caret or
tilde version; ambiguous ranges stop before writes. Use replacement or migrate
to flat configuration for the supported ESLint baseline.
Backups are stored under ignored `.tools/backups/<id>/`; review them before cleanup.
TOML/YAML reconciliation uses pinned `tomlkit` and `ruamel.yaml` parsers in a local
`.tools/bootstrap` environment to preserve metadata and comments. The first
reconciliation may download these parsers even with `--configure-only`; it does
not install analyzers in that mode.

The bootstrapper creates:

- npm development dependencies for Prettier, ESLint, TypeScript and selected React plugins.
- Isolated `.tools/venv` dependencies for Semgrep, check-jsonschema and Ruff/Pyright.
  The application's `.venv` is never created, installed into or removed by setup.
- Checksum-verified Gitleaks, Trivy and Hadolint under ignored `.tools/bin`.
- Local Git hooks: pre-commit for Python/mixed, Husky for Node-only.
- Preserved/merged VS Code settings, extension recommendations and **Quality:** tasks.
- Repository tool configuration, dependency locks, `AGENTS.md` rules and `QUALITY.md` usage.

Existing conflicting analyzer/hook configuration is never silently overwritten.
Existing VS Code JSONC comments, values and unrelated tasks are retained.
Existing TypeScript configuration is retained unchanged; otherwise existing
projects get recursive source inclusion rather than assuming a `src` directory.
Nested npm projects and npm workspaces are included in dependency installation and
checks. Non-npm package managers and externally managed hooks require manual
integration; they are not silently converted. Non-shell custom hooks cannot be
safely amended; choose leave or replacement. Arbitrary custom scanner/build scripts
are retained rather than rewritten.
Workspace patterns support `*`, `?`, character classes and recursive `**`;
brace/extglob layouts stop for manual integration. Pre-commit `.yml` aliases are
backed up and normalized to `.yaml`, retaining existing checks and comments.
Review generated files and commit them, including locks; never commit environments
or downloaded binaries.
Run `format` and stage its changes first: Husky/custom shell hooks verify formatting
without silently changing files after they have been staged.
The default Prettier configuration uses `trailingComma: "es5"` for embedded
JavaScript in `references/*.htm`, avoiding trailing commas in function parameters
and calls. All other files retain `trailingComma: "all"`; this is not a general
ES5 compatibility transformation. Existing application formatter settings remain
subject to the selected integration policy.

## Use

```sh
python3 .quality/install.py                # restore tools/hooks in a fresh clone
.tools/venv/bin/python .quality/run.py format    # format
.tools/venv/bin/python .quality/run.py check     # lint/types/schemas/format check/Docker
.tools/venv/bin/python .quality/run.py security  # secrets/SAST/dependency/infrastructure scans
```

Docker-independent checks always work. Docker runtime checks stay configured and
explicitly skip unavailable dependencies, then activate on future runs when
Docker is available. Builds/image scans require explicit targets; no build context
is guessed. CI is optional and can be added later; see the separate
[application CI setup and usage guide](templates/quality/CI.md).

See the [generated setup and usage guide](templates/quality/USAGE.md) for tool
responsibilities, IDE integration, configuration, network requirements and Docker
behavior. The [manual configuration guide](docs/Development%20Code%20Quality%20Configuration%20Guide.md)
contains older setup examples; the bootstrap workflow is the current local-first path.

## Updating an existing project's tools

Pull the reviewed baseline into your tools checkout, then run:

```sh
git -C ~/git/ai-coding-standards pull --ff-only
bash ~/git/ai-coding-standards/scripts/bootstrap.sh \
  --target /path/to/application --update-tooling --preview
bash ~/git/ai-coding-standards/scripts/bootstrap.sh \
  --target /path/to/application --update-tooling
```

The second command previews changes and asks for confirmation. For unattended
updates, review the preview first and pass `--yes`. Running the script interactively
against a configured project also offers **Update tooling**. No update is automatic:
the installed project keeps its committed pins and locks until its owner updates.
Updates use this checkout's reviewed exact pins, not registry `latest` versions.

New setups record dependency ownership and runtime fingerprints in
`.quality/baseline.json`; commit that file. Updates change only owned Python pins,
npm dependency entries/overrides, binary release specifications and compatible
suite runtime scripts. They preserve application dependencies, custom tests,
analyzer rules, VS Code configuration, scanner policy and existing hooks.
One additive formatter update adds the `references/*.htm` Prettier
`trailingComma: "es5"` override to recorded JSON configurations (including
`package.json` settings), preserving other options and overrides. Explicit
reference-page overrides are retained with a review notice if they differ.
Other Prettier configuration formats require manual editing and are reported.
Dependencies retained during amendment are not owned and remain unchanged.
npm lifecycle scripts are not run during tooling updates/restoration, avoiding
automatic hook reinstallation. Shared transitive dependencies can change during
resolution; review the resulting locks and run your application's tests too.

Updates stop before writes for local changes to owned dependencies/runtime,
major upgrades, pre-1.0 minor upgrades, downgrades, or runtime API migrations.
`--migrate-tooling` explicitly enables supported migrations; it is not an unrestricted
override. Runtime API 1 -> 2 moves Python tooling into `.tools/venv`, rewires only
suite commands/dispatchers and standard Ruff paths, and enables ownership-aware npm
audits. Application interpreters, custom tests and existing rules remain unchanged.
Previously installed scanner packages are not uninstalled from the application's
legacy environment: recreate it from application manifests separately if cleanup
is wanted. Suite commands use explicit tool interpreters; retained shell checks
keep the application `.venv` first on PATH.
Other version transitions require an exact `{tool, from, to}` entry in the reviewed
`reviewed_version_migrations` manifest; maintainers must test compatibility before
publishing one. Unknown runtime transitions still stop.
Same-major compatibility is not guaranteed by semver alone: maintainers must test
new exact pins against the suite before publishing them, and bump `runtime_api`
when preserving existing configurations is not supported. Such migrations require
separate explicit review, not an automatic replacement.

Projects created before ownership tracking need a one-time explicit adoption:
add `--adopt-tooling` to both preview and apply commands. Adoption accepts only
recognized generated runtime fingerprints and claims exact dependencies matching
the recorded baseline; review that ownership map, especially in amended projects.
Unfamiliar/local runtime modifications require manual reconciliation.

Before installation, changed manifests/runtime and affected npm/Python locks are
backed up under `.tools/backups/update-<id>/`, with a restoration inventory.
The updater installs tools without rewriting hooks/settings, then runs both
configured hook stages (including retained checks). Suite formatting is check-only
during this validation; arbitrary retained hooks may still have their own side
effects. For Husky/custom hooks, validation invokes the local shell hooks without
remote push arguments; remote-dependent checks also need a real push.

Installation or validation failure restores the backed-up manifests, locks and
runtime automatically and returns failure. Installed environments may have changed;
restore them using `bash scripts/bootstrap.sh --target /path/to/app --recover-tooling`
from the standards checkout. Recovery restores backed-up files, locks and binary
permissions, recreates only the isolated tooling environment, and restores npm from
the old lock without lifecycle scripts. The application's `.venv` is untouched for
API 2 backups. For API 1 backups, recovery uses the previous shared-environment
installer; it cannot safely remove unrelated packages from that old environment.
Recovery suppresses npm lifecycle scripts; application native modules that depend
on those scripts may need an explicitly reviewed rebuild. Run application checks
after recovery before assuming the whole application environment is ready.
Review any files changed by retained third-party
hooks. No commits are created, and successful updates still require reviewing the
diff and committing the new baseline/locks. Keep backups until that review passes.
Failures and interrupted processes leave `.tools/update-pending.json`; subsequent
updates stop until the recovery command succeeds. To revert a successful update,
pass `--recover-tooling .tools/backups/update-<id>` explicitly. Recovery failures
retain the marker and can be retried; review the backup inventory before recovery.

### Tool health and scan freshness

```sh
python3 .quality/run.py doctor
python3 .quality/run.py status
python3 .quality/run.py freshness
```

`doctor`/`status` emit JSON and return failure for missing tools, version/ownership
drift, stale manifests/locks, missing hook wiring or interrupted updates. Docker
CLI/Compose/daemon availability is reported separately as dependency skips.
`freshness` reports the last completed security scan and Trivy database metadata;
missing data is explicit. Executable pins do not pin registry rules or databases.
Local Semgrep rule files have a recorded SHA-256; registry packs have no immutable
content identity in the receipt, so repeat results are not guaranteed.

For offline security scans, set `semgrep_config` to a reviewed local rule file and
run `.tools/venv/bin/python .quality/run.py security --offline`. Trivy must already
have its databases; updates are disabled. npm uses a cached advisory result tied
to the exact current lock, no older than 24 hours; absent/expired results fail.
An offline success is based on cached advisories, not a current vulnerability check.

### Optional application CI

The basic setup needs no CI configuration. When ready, follow the separate
[CI setup and usage guide](templates/quality/CI.md) for GitHub, GitLab or Jenkins,
including application tests, runner prerequisites and remote merge protection.
The guide stays in this checkout; CI setup adds no documentation to the target repository.

## Bootstrapper tests

The standards repository's own GitHub quality workflow restores its Python parser
dependencies into an application `.venv`, separately from scanner tools. Its full
checkout history supplies the published-runtime fixtures used by migration tests.
Application CI templates require project-specific environment restoration instead;
they do not install the standards repository's parser dependencies.

When onboarding existing Python project, standalone Ruff/Pyright, or pre-commit
configurations, bootstrap automatically installs missing comment-preserving
parsers in the target's `.tools/bootstrap` and resumes setup there. No global
parser installation or changes to the application's `.venv` are required.

```sh
python3 .quality/install.py
.tools/venv/bin/python scripts/verify_python.py
npm run check
```

This standards repository now uses the same local suite it generates. Its
tracked `.quality/locked/requirements.txt` and `package-lock.json` restore
development tools without global scanner installs. Tests execute generated
JavaScript and TypeScript React lint configurations against the real plugins.
Both Git hook stages retain formatting, lint, types, schemas, security and test
checks. The old ESLint configuration is superseded; the Python/Compose entry
points remain, with corrected interpreter resolution and local-suite delegation.

## Supported ESLint and tool updates

The baseline uses ESLint 10.14.0, the supported major release.
[ESLint 9 reached EOL on August 6, 2026](https://eslint.org/version-support/).
The approved replacements are `@eslint-react/eslint-plugin` (MIT) and
`eslint-plugin-jsx-a11y-x` (MIT), with the maintained React Hooks plugin retained.
Generated rules are tested for valid JSX/TSX, missing alternative text, missing
list keys and conditional Hooks. This is a reviewed rule-set migration, not a
claim that every rule from the old React plugin has an identical replacement.

Scoped npm overrides pin minimatch 11.0.5 (BlueOak-1.0.0) beneath the analyzers,
fixing [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)
without overriding application dependencies. Fresh installs and the real
JS/TS/React analyzer tests validate the override. When upgrading an existing
lock, use `npm update minimatch`, then `npm ls minimatch` and `npm audit`.
Do not use forced peer-dependency installs or downgrade to an unsupported ESLint
release to make npm audit pass.

Application repositories ignore generated `.quality`, `.venv`, `.tools` and
`node_modules` during source/security scanning. A separate npm advisory audit walks
the application dependency graph, including development dependencies and shared
transitives, excluding only suite-owned dependency roots. Ownership/manifest/lock
drift fails explicitly. npm package manifests/locks are excluded from Trivy to avoid
duplicate findings that cannot distinguish suite ownership. Python requirements and
locks remain scanned by Trivy; the suite's isolated lock is not part of
the application's dependency audit; application requirements and locks remain
in scope. Analyzer installation still verifies checksums and Python dependency
consistency.

Tool dependency audits belong here, separately from application commit/push gates:

```sh
.tools/venv/bin/python -m pip_audit -r .quality/locked/requirements.txt
npm audit
# Equivalent manual Python audit hook:
.tools/venv/bin/pre-commit run dependency-audit --hook-stage manual --all-files
```

## Maintaining and scanning this tooling repository

Run these commands from the **ai-coding-standards checkout**, not an application
repository. Review tool dependencies monthly and before publishing a baseline.
Use a feature branch for fixes; do not change application scan exclusions to
hide tooling findings.

### Restore and scan

```sh
python3 .quality/install.py
npm run check
npm run security
# Run both audits separately, even if one fails:
npm run tooling:audit:python
npm run tooling:audit:node
```

`check` exercises formatting, analyzers, types and bootstrap tests. `security`
scans repository artifacts for secrets, source issues and dependency/configuration
findings, using the normal exclusions. It does **not** audit the installed scanner
environments or generated `.quality` files. The separate Python audit checks the
complete tooling lock, including transitive packages; `npm audit` includes this
repository's development dependencies. A passing source scan or hook stage is
not evidence that tool dependencies are vulnerability-free.

For each finding, inspect the advisory, affected versions, fixed versions and
the dependency chain. For example:

```sh
.tools/venv/bin/python -m pip show semgrep mcp opentelemetry-api
.tools/venv/bin/python -m pip index versions semgrep
npm explain minimatch
npm view eslint version engines peerDependencies
```

Python/npm audits do not cover downloaded Gitleaks, Trivy and Hadolint executables.
Review their official release notes and security advisories too. Their pinned
release repositories and per-platform SHA-256 values live in
`templates/quality/versions.json`; update version, asset names and verified
checksums together. Checksums verify downloads, not absence of vulnerabilities.

### Resolve findings and validate candidate updates

1. Identify the direct tool that brings in an affected dependency. Prefer an
   upstream-compatible tool release that permits the patched dependency. Check
   maintenance, licensing, runtime requirements and analyzer compatibility.
   Do not use `latest`, forced peer installs, incompatible Python overrides or
   advisory suppression to obtain a green result.
2. Update reviewed exact suite pins in `templates/quality/versions.json`. Mirror
   overlapping Python pins in `requirements-dev.txt`. For npm transitive fixes,
   update the reviewed analyzer-scoped `node_overrides`, not a global application
   override. For repository-only test/audit/parser dependencies, update
   `requirements-dev.txt` or `scripts/requirements-bootstrap.txt`; these are not
   application-suite dependencies.
3. Bootstrap a fresh disposable Git repository under `~/git/temp/` from the
   candidate checkout. Exercise affected profiles, real rules, both hook stages,
   tool dependency audits and an existing-project update. Do not publish candidates
   that only pass mocked tests. Remove the named sandbox when no longer needed.
4. Preview and apply compatible suite changes to this repository:

   ```sh
   bash scripts/bootstrap.sh --target "$PWD" --update-tooling --preview
   bash scripts/bootstrap.sh --target "$PWD" --update-tooling
   ```

   This synchronizes owned pins, runtime, configuration, ownership metadata and
   locks without replacing rules/hooks. Major upgrades, pre-1.0 minor upgrades or
   runtime API changes require a supported, explicitly reviewed `--migrate-tooling`
   migration instead of falsifying the recorded baseline. Bump `runtime_api` when
   existing application configuration is incompatible with new runtime behavior.

5. If repository-only Python dependencies changed, install their reviewed manifest,
   check consistency and refresh the complete tooling lock:

   ```sh
   .tools/venv/bin/python -m pip install -r requirements-dev.txt
   .tools/venv/bin/python -m pip check
   .tools/venv/bin/python -m pip freeze --all > .quality/locked/requirements.txt
   ```

   Review every resolved change; this lock must retain the test/audit/parser
   dependencies as well as suite tools. If npm retains an old overridden resolution,
   use a targeted `npm update minimatch`, inspect `npm ls minimatch`, and re-audit.
   Do not run a blanket `npm audit fix --force`.

6. Re-run the scans/audits above and both required hook stages:

   ```sh
   .tools/venv/bin/pre-commit run --all-files
   .tools/venv/bin/pre-commit run --hook-stage pre-push --all-files
   git diff --check
   git diff
   git diff --cached
   ```

   Address failures caused by the update. If upstream constraints prevent a fix,
   document the advisory, affected package, required patched version, operational
   restrictions and next review date. Keep the audit failing visibly; retain an
   exception only with explicit risk acceptance. The existing Semgrep exception
   below is not blanket acceptance of future findings.

7. Update the documented audit result, review pins/checksums, manifests, locks,
   runtime and `.quality/baseline.json`, then commit and publish the feature-branch
   change through the project's review process. Fresh setups use the new versions;
   existing application owners pull the updated checkout and explicitly run
   `--update-tooling`. Their projects never update silently.

### Accepted local-scanner dependency risk

As of November 7, 2026, the latest Semgrep 1.184.0 requires `mcp==1.29.0`
and OpenTelemetry 1.37.x. The audit reports these five findings:

- MCP: CVE-2026-59951, CVE-2026-116685, CVE-2026-116684, CVE-2026-116686.
  Patched MCP versions start at 1.30.0.
- OpenTelemetry API: CVE-2026-114603. Patched versions start at 1.45.1.

These findings are accepted for local development tooling, not production
application dependencies. The manual audit continues to fail and report them;
there are no suppressed advisory IDs or incompatible dependency overrides.
Do not expose Semgrep's MCP server as a network service. Scanner metrics and
version telemetry remain disabled. Pip itself is updated and pinned to 26.3.

Review this exception monthly and before each baseline release:

1. Check `python3 -m pip index versions semgrep` and Semgrep release notes for a
   release allowing patched MCP/OpenTelemetry dependencies.
2. Update the exact Semgrep pin in `templates/quality/versions.json` and
   `requirements-dev.txt`, then use `--update-tooling` to update this repository's
   suite requirements/config, lock and ownership record together.
   Never substitute `latest` or force dependencies outside Semgrep's constraints.
3. Resolve the candidate in a fresh sandbox, regenerate the complete Python lock
   with `pip freeze --all`, and run `pip check`, `pip_audit`, both hook stages and
   the React integration tests.
4. Update the recorded audit result and remove this exception only when the
   dependency audit confirms the findings are gone. Commit the pins and locks so
   future clones receive the reviewed update.

## Baseline limitations

- Linux installs and checks have been exercised; macOS assets are pinned but need
  native platform testing before broad rollout.
- The accepted Semgrep dependency findings above remain in the separate tooling
  audit until an upstream-compatible patched release is available.
