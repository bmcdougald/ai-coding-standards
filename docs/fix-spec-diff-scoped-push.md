# Fix spec: diff-scoped push scans + bootstrap-found suite bugs

These issues were found when bootstrapping the suite into an existing
mixed-language repo (ai-stack: Python + JS/TS, 7 Dockerfiles, pre-commit
hooks already in place). All fixes belong in `templates/quality/` and
`scripts/` so new bootstraps get them; repos that already bootstrapped
can apply the same patches to their generated `.quality/run.py`.

## 1. Diff-scope the heavy scans on `push` (primary fix)

`run.py` runs trivy + semgrep + gitleaks full-tree on every `push`. On a
large repo each costs ~60s; the push path should scan only what changed.
CI must stay full-scope — `quality.yml` is the authoritative gate.

### Mechanism

pre-commit sets `PRE_COMMIT_FROM_REF` / `PRE_COMMIT_TO_REF` while running
pre-push hooks. Use the from-ref as the diff base; fall back to full-tree
when it is unset (CI, manual runs, first push).

In `main()` of `templates/quality/run.py`:

```python
def changed_files() -> list[Path]:
    base = os.environ.get("PRE_COMMIT_FROM_REF") or os.environ.get("BASE_REF")
    if not base:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", base, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return [ROOT / n for n in result.stdout.split() if (ROOT / n).is_file()]
```

Then in the `push` action:

```python
files = changed_files() or source_files()  # empty diff => full scan
```

An empty file list MUST fall back to the full set — a push with no
detectable diff must not produce a false-clean scan.

### Per-scanner changes (all in the `security`/`push` blocks)

**semgrep** (`sast` section): pass `files` as the target list instead of
`.`. semgrep accepts a path list. Optional stronger mode: semgrep
supports `--baseline-commit <sha>` to report only _new_ findings —
prefer it when `PRE_COMMIT_FROM_REF` is set; it also suppresses
pre-existing findings unrelated to the change.

**trivy** (`filesystem` section): `trivy fs` takes a single target path.
Scope to the set of parent dirs of changed files (deduplicated), or
build `--skip-dirs`/`--skip-files` args from unchanged top-level dirs.
Simplest correct approach: `trivy fs <each changed top-level dir>`.

**gitleaks** (`secrets` section, non-commit branch): currently
`gitleaks dir .`. On push use `gitleaks git --log-opts="$BASE..HEAD"`
to scan only the commits being pushed.

### Same change in `templates/quality/ci.py`

The generated `quality.yml` workflow must NOT inherit the diff scope —
CI scans full-tree always. If the scoping lives behind the env-var check
above it is naturally local-only; make that explicit in a comment.

## 2. Add `.tools/` to the generated ignore lists

The generated `eslint.config.*` ignore block lists
`['.venv', 'node_modules', 'dist', 'build', 'coverage']` but not
`.tools/` — the suite's own venv where it installs pyright/semgrep/etc.
Every JS file under `.tools/venv/.../site-packages/` gets linted
(5000+ false errors on first `check`).

Add `.tools` to:

- `integrate_tooling.py` / wherever `eslint.config.*` `ignores` is emitted
- `.prettierignore` template
- `.gitignore` template (verify — ai-stack's had it; confirm the
  template emits it)
- gitleaks/trivy/semgrep skip-dir defaults in `run.py` (audit each
  scanner invocation for whether it can descend into `.tools`)

## 3. Generated files must pass the suite's own format check

The bootstrap wrote `.pre-commit-config.yaml` in a form `prettier
--check` rejects — the suite's first `check` fails on its own output.
After any YAML/JSON/JS config is generated (ruamel/pyyaml round-trips
reformat), run prettier `--write` over generated files before finishing,
or emit canonical formatting from the writer. Applies to at least:

- `.pre-commit-config.yaml`
- `tsconfig.json`, `package.json` edits
- `.github/workflows/quality.yml`
- `.vscode/*.json`, `.quality/*.json`

## 4. Bump locked deps carrying CVEs

`.quality/locked/requirements.txt` (from `requirements.in` +
`versions.json`) pinned versions trivy flagged:

- `mcp==1.29.0` → `1.30.0` (4 MEDIUM CVEs: auth bypass, info disclosure,
  2× DoS — CVE-2026-116684/5/6, CVE-2026-59951)
- `opentelemetry-api==1.37.0` → `1.45.1` (DoS via header parsing,
  CVE-2026-114603)

Update `requirements.in` / `versions.json`, regenerate the lock, and
verify the whole dependency-closure still resolves (dependent
`opentelemetry-*` pins may need coordinated bumps).

## 5. (Minor) CI preview false-ambiguity

`configure_ci.py` marks a dir "ambiguous" and requires manual review
when it can't tell the test framework — but dirs containing
`test_*.py`/`tests/` with pytest files are unambiguous pytest. Treat
"pytest test files present" as sufficient detection so the common case
doesn't require `--ci-config` hand-editing.

## Verification

On the repo that surfaced these (ai-stack): `.quality/run.py check` must
pass from a clean generated state, and a push of a small diff should run
the scoped scans in seconds rather than ~60s each.
