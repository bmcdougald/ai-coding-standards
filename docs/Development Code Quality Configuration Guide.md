# Development Code Quality Configuration Guide

## 1. Purpose and Scope

This guide defines a free, local-first quality and security baseline for repositories that use one or more of the following:

- Python
- JavaScript, TypeScript, or React
- Docker or Docker Compose

The setup is designed for VS Code and AI-assisted development. It does not require a CI/CD pipeline or paid subscriptions.

The intended workflow is:

1. Repository instructions tell developers and AI tools which standards apply.
2. Project-local, pinned dependencies produce the same results from a terminal, VS Code, or a Git hook.
3. Fast formatting, linting, and secret checks run before each commit.
4. Tests, type checks, dependency audits, and security scans run before each push.
5. Developers review the final diff before merging.

Do not install project quality tools globally and do not bypass hooks with `git commit --no-verify` or `git push --no-verify`. Fix the project environment or hook configuration instead.

## 2. Choose a Repository Profile

Choose one profile before creating files or installing packages. Do not add tools for languages the repository does not use.

| Profile          | Application tooling                                | Required package files                   | Hook configuration                       |
| :--------------- | :------------------------------------------------- | :--------------------------------------- | :--------------------------------------- |
| Python only      | Ruff, Pyright, pytest, pip-audit                   | `requirements-dev.txt`, `pyproject.toml` | Python hooks only                        |
| JS/TS/React only | Prettier, ESLint, TypeScript, Vitest               | `package.json`, `package-lock.json`      | Node hooks only                          |
| Mixed            | Both sets                                          | All files listed above                   | Combined hooks                           |
| Docker-enabled   | Selected profile plus Trivy and Compose validation | Docker files already used by the project | Add Docker hooks to the selected profile |

Every profile uses a project `.venv` for pinned `pre-commit` and Semgrep. In a JS/TS-only repository, the environment is hook infrastructure; it does not make the application a Python project and does not require Python application configuration.

### 2.1 Files by profile

| File                               |      Python      |       JS/TS/React       |          Mixed          | Purpose                                                        |
| :--------------------------------- | :--------------: | :---------------------: | :---------------------: | :------------------------------------------------------------- |
| `AGENTS.md`                        |       Yes        |           Yes           |           Yes           | Repository-specific AI rules                                   |
| `.gitignore`                       |       Yes        |           Yes           |           Yes           | Excludes generated and sensitive local files                   |
| `.pre-commit-config.yaml`          |       Yes        |           Yes           |           Yes           | Commit and push checks                                         |
| `requirements-dev.txt`             |       Yes        |           Yes           |           Yes           | Pinned hook infrastructure and, where applicable, Python tools |
| `pyproject.toml`                   |       Yes        |           No            |           Yes           | Python tool configuration                                      |
| `scripts/verify_python.py`         |       Yes        |           No            |           Yes           | Python pre-push validation                                     |
| `package.json`                     |        No        |           Yes           |           Yes           | Node dependencies and commands                                 |
| `package-lock.json`                |        No        |           Yes           |           Yes           | Exact Node dependency graph                                    |
| `.prettierrc.json`                 |        No        |           Yes           |           Yes           | Prettier rules                                                 |
| `.prettierignore`                  |        No        |           Yes           |           Yes           | Prettier exclusions                                            |
| `eslint.config.js`                 |        No        |           Yes           |           Yes           | ESLint rules                                                   |
| `tsconfig.json`                    |        No        | When TypeScript is used | When TypeScript is used | TypeScript configuration                                       |
| `scripts/verify_docker_compose.py` | Only with Docker |    Only with Docker     |    Only with Docker     | Conditional Compose validation                                 |

## 3. Workstation Prerequisites

Install host software before configuring a repository. Project dependencies are installed later and must remain project-local.

### 3.1 Required for every profile

- Git
- Python 3.11 or newer
- Gitleaks
- Trivy
- VS Code, if it is the team's editor

Python is required in every profile because `pre-commit` and Semgrep are Python-distributed command-line tools. They will be installed inside the repository's `.venv`, not globally.

### 3.2 Required only for JS/TS/React or mixed profiles

- Current Node.js LTS, including npm

### 3.3 Required only for Docker-enabled repositories

- Docker Engine on Linux, or Rancher Desktop on macOS/Windows
- Docker Compose v2 (`docker compose`)

Docker Desktop may have licensing implications for some organizations and is not required as the organizational standard.

### 3.4 macOS

Install Git and Python using the organization-approved installer or Homebrew. Install the host security scanners:

```sh
brew install gitleaks trivy
```

For JS/TS/React or mixed repositories, install the current Node.js LTS release from <https://nodejs.org/> or through the organization's approved version manager.

### 3.5 Ubuntu and Linux

Install Git, Python 3.11+, the matching `venv` package, and Node.js LTS when the selected profile requires Node. Install Gitleaks and Trivy using their official installation instructions or the organization's approved package repository:

- Gitleaks: <https://github.com/gitleaks/gitleaks#installing>
- Trivy: <https://trivy.dev/latest/getting-started/installation/>

The Python package name varies by distribution. On Ubuntu, install the `venv` package that matches the selected Python interpreter, such as `python3.12-venv`.

### 3.6 Windows PowerShell

Install Git and Python 3.11+ with `winget` or the organization-approved installer. During a python.org installation, select **Add Python to PATH**.

```powershell
winget install Gitleaks.Gitleaks
winget install AquaSecurity.Trivy
```

Install Node.js LTS only for JS/TS/React or mixed repositories.

### 3.7 Verify host software

Run the commands that apply to the selected profile:

```sh
git --version
python3 --version
gitleaks version
trivy --version
```

JS/TS/React or mixed profiles:

```sh
node --version
npm --version
```

Docker-enabled repositories:

```sh
docker --version
docker compose version
```

On Windows, use `python --version` or `py --version` instead of `python3 --version`.

## 4. Initialize the Repository

Complete the following steps from the repository root before installing dependencies.

### 4.1 Initialize Git

Skip this command when configuring an existing Git repository.

```sh
git init
```

### 4.2 Create `.gitignore`

Start with this complete baseline, then add project-specific generated files:

```gitignore
# Python and shared hook environment
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.pyright/
.ruff_cache/

# Node
node_modules/
dist/
build/
coverage/

# Environment files and local credentials
.env
.env.*
!.env.example
.npmrc
.pypirc
*.pem
*.key

# VS Code local settings; keep shared configuration
.vscode/*
!.vscode/extensions.json
!.vscode/settings.json

# Operating system files
.DS_Store
Thumbs.db

# Security scan reports
gitleaks-report.json
trivy-report.json
semgrep-report.json
```

Commit `.env.example` with placeholder values when environment variables must be documented. Never commit real credentials or local credential files.

### 4.3 Add repository AI rules

Create `AGENTS.md` in the repository root. Keep personal preferences in the AI tool's user-level configuration and team requirements in the repository file.

Use this baseline and add project-specific build or test requirements where needed:

```markdown
# Project AI Development Rules

## Required behavior

- Read this file and applicable project configuration before making changes.
- Follow existing project patterns and reuse existing dependencies where possible.
- Make small, focused changes and update tests when behavior changes.
- Do not weaken or bypass tests, type checks, security scans, or Git hooks.
- Do not use `git commit --no-verify` or `git push --no-verify`.
- Do not claim a check passed unless it was run and its result was reviewed.

## Required validation

Before reporting work complete, run:

    pre-commit run --all-files
    pre-commit run --hook-stage pre-push --all-files
    git diff --check
    git diff

If the repository uses Docker Compose, also run:

    docker compose config

If a Docker image was built or changed, also run:

    trivy image <image-name>

Fix failures caused by the change before reporting completion. Report every
check that was not run and the reason.

## Security rules

- Never add secrets, credentials, private keys, production data, or customer data.
- Never commit `.env` files. Use `.env.example` with placeholder values.
- Validate untrusted input at application boundaries.
- Use parameterized database queries and do not pass untrusted input to shells.
- Ask before changing authentication, authorization, encryption, payments,
  database schemas, infrastructure, deployment, or external services.
```

Use small adapter files for tools that do not load `AGENTS.md` automatically:

| Tool           | Adapter file                      | Content                                             |
| :------------- | :-------------------------------- | :-------------------------------------------------- |
| Claude Code    | `CLAUDE.md`                       | `Read and follow AGENTS.md in the repository root.` |
| GitHub Copilot | `.github/copilot-instructions.md` | `Read and follow AGENTS.md in the repository root.` |
| Cursor         | `.cursor/rules/project-rules.mdc` | An always-applied rule that references `AGENTS.md`  |

Do not duplicate the full rules in each adapter.

### 4.4 Add shared VS Code configuration

Create `.vscode/extensions.json` with only extensions relevant to the selected profile:

```json
{
  "recommendations": [
    "esbenp.prettier-vscode",
    "dbaeumer.vscode-eslint",
    "ms-python.python",
    "ms-python.vscode-pylance",
    "charliermarsh.ruff",
    "ms-azuretools.vscode-docker",
    "redhat.vscode-yaml",
    "mhutchie.git-graph"
  ]
}
```

Remove Prettier and ESLint for Python-only repositories. Remove Pylance and Ruff for JS/TS-only repositories. Remove Docker when the repository does not use containers. The Python extension remains useful in every profile for selecting the shared `.venv` interpreter.

Create or merge `.vscode/settings.json`:

```json
{
  "editor.formatOnSave": true,
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "files.exclude": {
    "**/.venv": true,
    "**/node_modules": true,
    "**/__pycache__": true,
    "**/.pytest_cache": true,
    "**/.ruff_cache": true
  },
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
  "[javascript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[javascriptreact]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[typescript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[typescriptreact]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[json]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[yaml]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[markdown]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  }
}
```

Remove language blocks that do not apply. Windows repositories should use `${workspaceFolder}\\.venv\\Scripts\\python.exe` for `python.defaultInterpreterPath`.

Editor integration provides immediate feedback but does not replace repository validation or Git hooks.

## 5. Create the Shared Hook Environment

Every profile completes this section. The committed `requirements-dev.txt` is the source of truth; an activated shell alone is never used to select the Python executable in hooks.

### 5.1 Create and activate `.venv`

macOS and Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

In VS Code, run **Python: Select Interpreter** and select the interpreter inside `.venv`.

### 5.2 Create `requirements-dev.txt`

Use exact versions so a fresh clone receives the same tools. Review and update these pins through a normal dependency update with full validation.

JS/TS/React-only profile:

```text
pre-commit==4.7.0
semgrep==1.183.0
```

Python-only or mixed profile:

```text
pre-commit==4.7.0
semgrep==1.183.0
ruff==0.17.3
pyright==1.1.414
pytest==9.2.0
pip-audit==2.10.1
```

Install the committed dependencies:

```sh
python -m pip install -r requirements-dev.txt
```

Do not omit packages because another copy was installed through Homebrew, pipx, or a user-level Python. Hooks in this guide intentionally use `.venv` and do not depend on global packages.

## 6. Python-Only Setup

Skip this section for JS/TS/React-only repositories.

### 6.1 Create `pyproject.toml`

Adjust `target-version`, source paths, and test paths to match the application:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
exclude = [
  ".git",
  ".venv",
  "__pycache__",
  "build",
  "dist",
]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "S"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101"]

[tool.pyright]
typeCheckingMode = "standard"
include = ["src", "tests"]
exclude = [".venv", "build", "dist"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Keep runtime dependencies in the project's normal runtime manifest, not in `requirements-dev.txt`.

### 6.2 Create `scripts/verify_python.py`

```python
from __future__ import annotations

import subprocess
import sys


def run(*command: str) -> None:
    print(f"\n$ {' '.join(command)}")
    subprocess.run(command, check=True)


def main() -> int:
    try:
        run(sys.executable, "-m", "ruff", "format", "--check", ".")
        run(sys.executable, "-m", "ruff", "check", ".")
        run(sys.executable, "-m", "pyright")
        run(sys.executable, "-m", "pytest")
    except subprocess.CalledProcessError as error:
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The hook launches this script with `.venv/bin/python`, and `sys.executable` preserves that interpreter for every child command.

### 6.3 Create the Python `.pre-commit-config.yaml`

This configuration has no Node hooks and does not require `package.json`, npm, Prettier, or ESLint.

```yaml
minimum_pre_commit_version: 4.7.0
default_install_hook_types: [pre-commit, pre-push]
exclude: >-
  (?x)(^|/)(
    \.venv/|
    node_modules/|
    dist/|
    build/|
    coverage/|
    \.env(?:\..*)?$|
    \.npmrc$|
    \.pypirc$|
    .*\.(?:pem|key)$
  )

repos:
  - repo: local
    hooks:
      - id: ruff-format
        name: Format Python with Ruff
        entry: .venv/bin/python -m ruff format
        language: system
        files: \.py$
        stages: [pre-commit]

      - id: ruff-check
        name: Lint Python with Ruff
        entry: .venv/bin/python -m ruff check
        language: system
        files: \.py$
        stages: [pre-commit]

      - id: gitleaks
        name: Scan staged changes for secrets
        entry: gitleaks protect --staged --redact
        language: system
        pass_filenames: false
        stages: [pre-commit]

      - id: python-check
        name: Run Python formatting, linting, types, and tests
        entry: .venv/bin/python scripts/verify_python.py
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: dependency-audit
        name: Scan Python dependencies for vulnerabilities
        entry: .venv/bin/python -m pip_audit
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: semgrep
        name: Run Semgrep security scan
        entry: .venv/bin/python -m semgrep scan --error --config auto --exclude .venv --exclude node_modules --exclude dist --exclude build --exclude coverage --exclude .env --exclude '.env.*' --exclude .npmrc --exclude .pypirc --exclude '*.pem' --exclude '*.key'
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: trivy
        name: Run dependency, secret, and configuration scan
        entry: trivy fs --exit-code 1 --include-dev-deps --scanners vuln,secret,misconfig --skip-dirs .venv --skip-dirs node_modules --skip-dirs dist --skip-dirs build --skip-dirs coverage --skip-files .env --skip-files '.env.*' --skip-files .npmrc --skip-files .pypirc --skip-files '*.pem' --skip-files '*.key' .
        language: system
        pass_filenames: false
        stages: [pre-push]
```

For Windows, replace each `.venv/bin/python` entry with `.venv/Scripts/python.exe`. Make that substitution in the committed profile used by the team; do not fall back to bare `python`.

Continue with [Section 9](#9-optional-docker-and-docker-compose-setup) when the repository uses Docker. Otherwise continue with [Section 10](#10-install-and-validate-git-hooks).

## 7. JS/TS/React-Only Setup

Skip this section for Python-only repositories. This profile uses Python only for the shared hook environment created in Section 5; it has no Ruff, Pyright, pytest, pip-audit, or Python application hooks.

### 7.1 Create or update `package.json`

Preserve the application's existing dependencies and scripts. Add exact development dependencies and coherent validation scripts. The example versions below were current when this guide was revised and must be updated through reviewed dependency changes.

```json
{
  "private": true,
  "type": "module",
  "scripts": {
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "check": "npm run format:check && npm run lint && npm run typecheck && npm run test"
  },
  "devDependencies": {
    "@eslint/js": "9.39.5",
    "eslint": "9.39.5",
    "eslint-plugin-jsx-a11y": "6.10.2",
    "eslint-plugin-react-hooks": "7.1.1",
    "eslint-plugin-react-refresh": "0.5.7",
    "globals": "17.13.0",
    "prettier": "3.9.10",
    "typescript": "6.0.2",
    "typescript-eslint": "8.72.1",
    "vitest": "5.0.5"
  }
}
```

Remove React-specific plugins when the project does not use React. Preserve framework packages such as Vite and application dependencies already required by the project.

### 7.2 Generate and use the lockfile

For the first setup, generate `package-lock.json` from the exact versions in `package.json`, then perform a clean install:

```sh
npm install --package-lock-only
npm ci
```

Commit both `package.json` and `package-lock.json`. Fresh clones and normal setup must use `npm ci`, not an unpinned global installation and not `npm install`.

### 7.3 Create Prettier configuration

Create `.prettierrc.json`:

```json
{
  "singleQuote": true,
  "semi": true,
  "trailingComma": "all",
  "printWidth": 100,
  "tabWidth": 2
}
```

Create `.prettierignore`:

```text
.venv
node_modules
dist
build
coverage
.env
.env.*
.npmrc
.pypirc
package-lock.json
```

### 7.4 Create `eslint.config.js`

```javascript
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import jsxA11y from "eslint-plugin-jsx-a11y";

export default tseslint.config(
  {
    ignores: [".venv", "node_modules", "dist", "build", "coverage"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{js,mjs,cjs,ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
  jsxA11y.flatConfigs.recommended,
  {
    files: ["**/*.{jsx,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
);
```

Use the project's existing `tsconfig.json` when available. For JavaScript-only repositories, remove the `typecheck` script and TypeScript packages unless the project uses TypeScript for checking JavaScript.

### 7.5 Create `tsconfig.json` when TypeScript is used

Use the framework's generated configuration when it provides one. Otherwise, start with:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true
  },
  "include": ["src", "tests"]
}
```

Adjust source and test paths to the repository. JavaScript-only projects must remove the `typecheck` script, `typescript` and `typescript-eslint` dependencies, and the TypeScript-specific imports/config blocks from `eslint.config.js`.

### 7.6 Create the JS/TS `.pre-commit-config.yaml`

```yaml
minimum_pre_commit_version: 4.7.0
default_install_hook_types: [pre-commit, pre-push]
exclude: >-
  (?x)(^|/)(
    \.venv/|
    node_modules/|
    dist/|
    build/|
    coverage/|
    \.env(?:\..*)?$|
    \.npmrc$|
    \.pypirc$|
    .*\.(?:pem|key)$
  )

repos:
  - repo: local
    hooks:
      - id: prettier
        name: Format repository text files with Prettier
        entry: npm exec --no -- prettier --write
        language: system
        files: \.(js|jsx|ts|tsx|json|css|scss|md|yaml|yml)$
        stages: [pre-commit]

      - id: eslint
        name: Lint JavaScript and TypeScript
        entry: npm exec --no -- eslint
        language: system
        files: \.(js|jsx|ts|tsx)$
        stages: [pre-commit]

      - id: gitleaks
        name: Scan staged changes for secrets
        entry: gitleaks protect --staged --redact
        language: system
        pass_filenames: false
        stages: [pre-commit]

      - id: project-check
        name: Run formatting, linting, types, and tests
        entry: npm run check
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: semgrep
        name: Run Semgrep security scan
        entry: .venv/bin/python -m semgrep scan --error --config auto --exclude .venv --exclude node_modules --exclude dist --exclude build --exclude coverage --exclude .env --exclude '.env.*' --exclude .npmrc --exclude .pypirc --exclude '*.pem' --exclude '*.key'
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: trivy
        name: Run dependency, secret, and configuration scan
        entry: trivy fs --exit-code 1 --include-dev-deps --scanners vuln,secret,misconfig --skip-dirs .venv --skip-dirs node_modules --skip-dirs dist --skip-dirs build --skip-dirs coverage --skip-files .env --skip-files '.env.*' --skip-files .npmrc --skip-files .pypirc --skip-files '*.pem' --skip-files '*.key' .
        language: system
        pass_filenames: false
        stages: [pre-push]
```

The `--no` option is essential: `npm exec` must fail when a package is absent instead of downloading an undeclared version. If ESLint reports a missing package such as `@eslint/js`, repair `package.json`/`package-lock.json` and run `npm ci`.

For Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe` in the Semgrep entry.

Continue with Section 9 when the repository uses Docker. Otherwise continue with Section 10.

## 8. Mixed Python and JS/TS Setup

Complete Sections 5, 6.1, 6.2, and 7.1 through 7.4 in order. Use the Python/mixed `requirements-dev.txt`, and commit both Python and Node configuration files.

Create this combined `.pre-commit-config.yaml` rather than copying one profile and removing hooks manually:

```yaml
minimum_pre_commit_version: 4.7.0
default_install_hook_types: [pre-commit, pre-push]
exclude: >-
  (?x)(^|/)(
    \.venv/|
    node_modules/|
    dist/|
    build/|
    coverage/|
    \.env(?:\..*)?$|
    \.npmrc$|
    \.pypirc$|
    .*\.(?:pem|key)$
  )

repos:
  - repo: local
    hooks:
      - id: prettier
        name: Format repository text files with Prettier
        entry: npm exec --no -- prettier --write
        language: system
        files: \.(js|jsx|ts|tsx|json|css|scss|md|yaml|yml)$
        stages: [pre-commit]

      - id: eslint
        name: Lint JavaScript and TypeScript
        entry: npm exec --no -- eslint
        language: system
        files: \.(js|jsx|ts|tsx)$
        stages: [pre-commit]

      - id: ruff-format
        name: Format Python with Ruff
        entry: .venv/bin/python -m ruff format
        language: system
        files: \.py$
        stages: [pre-commit]

      - id: ruff-check
        name: Lint Python with Ruff
        entry: .venv/bin/python -m ruff check
        language: system
        files: \.py$
        stages: [pre-commit]

      - id: gitleaks
        name: Scan staged changes for secrets
        entry: gitleaks protect --staged --redact
        language: system
        pass_filenames: false
        stages: [pre-commit]

      - id: project-check
        name: Run JS/TS formatting, linting, types, and tests
        entry: npm run check
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: python-check
        name: Run Python formatting, linting, types, and tests
        entry: .venv/bin/python scripts/verify_python.py
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: dependency-audit
        name: Scan Python dependencies for vulnerabilities
        entry: .venv/bin/python -m pip_audit
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: semgrep
        name: Run Semgrep security scan
        entry: .venv/bin/python -m semgrep scan --error --config auto --exclude .venv --exclude node_modules --exclude dist --exclude build --exclude coverage --exclude .env --exclude '.env.*' --exclude .npmrc --exclude .pypirc --exclude '*.pem' --exclude '*.key'
        language: system
        pass_filenames: false
        stages: [pre-push]

      - id: trivy
        name: Run dependency, secret, and configuration scan
        entry: trivy fs --exit-code 1 --include-dev-deps --scanners vuln,secret,misconfig --skip-dirs .venv --skip-dirs node_modules --skip-dirs dist --skip-dirs build --skip-dirs coverage --skip-files .env --skip-files '.env.*' --skip-files .npmrc --skip-files .pypirc --skip-files '*.pem' --skip-files '*.key' .
        language: system
        pass_filenames: false
        stages: [pre-push]
```

For Windows, replace every `.venv/bin/python` entry with `.venv/Scripts/python.exe`.

## 9. Optional Docker and Docker Compose Setup

Complete this section only when the repository contains a Dockerfile or Docker Compose file.

### 9.1 Create `scripts/verify_docker_compose.py`

Python is available in every profile through the shared `.venv`.

```python
from __future__ import annotations

import subprocess
from pathlib import Path

COMPOSE_FILES = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)


def main() -> int:
    if not any(Path(name).exists() for name in COMPOSE_FILES):
        print("No Docker Compose file found; skipping Compose validation.")
        return 0
    return subprocess.run(["docker", "compose", "config"], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
```

### 9.2 Add the Compose pre-push hook

Append this hook to the local `hooks` list in the selected profile:

```yaml
- id: docker-compose-validate
  name: Validate Docker Compose configuration when present
  entry: .venv/bin/python scripts/verify_docker_compose.py
  language: system
  pass_filenames: false
  stages: [pre-push]
```

Use `.venv/Scripts/python.exe` on Windows.

### 9.3 Validate and scan containers

The profile's Trivy hook scans repository dependencies and configuration. Also run:

```sh
docker compose config
```

When an image is built or changed:

```sh
docker build -t project-name:local .
trivy image project-name:local
```

Hadolint is optional. Trivy is the required baseline because it covers Dockerfiles, Compose/configuration, dependencies, secrets, and built images. Hadolint provides additional Dockerfile style guidance but does not validate Compose files.

## 10. Install and Validate Git Hooks

At this point, the selected dependency manifests, installed dependencies, configuration files, scripts, and `.pre-commit-config.yaml` must all exist.

### 10.1 Install both hook stages

Activate `.venv`, then install both stages in one command:

```sh
pre-commit install --hook-type pre-commit --hook-type pre-push
```

This command is required once per local clone. Because `pre-commit` was installed in `.venv`, run it from an activated `.venv` or invoke `.venv/bin/pre-commit` directly. On Windows, invoke `.venv\Scripts\pre-commit.exe` when the environment is not activated.

### 10.2 Validate configuration and run all checks

```sh
pre-commit validate-config
pre-commit run --all-files --verbose
pre-commit run --hook-stage pre-push --all-files --verbose
```

The first full run may modify files through Ruff or Prettier. Review the changes, stage them, and rerun both commands until they pass. Do not bypass a failing hook.

### 10.3 Commit the baseline

After all checks pass:

```sh
git add .
git diff --cached --check
git diff --cached
git commit -m "Add development quality and security configuration"
```

## 11. Troubleshooting

Always begin with verbose output:

```sh
pre-commit run --all-files --verbose
pre-commit run --hook-stage pre-push --all-files --verbose
```

### 11.1 Ruff uses the wrong Python interpreter

**Symptom:** Ruff is reported missing even though it was installed, or the output references Homebrew or another host Python.

**Cause:** The hook uses bare `python`, `python3`, or `ruff`, so VS Code's inherited `PATH` selects a different interpreter.

**Fix:** Confirm Ruff is installed in the project environment and confirm every Python hook entry begins with the explicit repository path:

```sh
.venv/bin/python -m ruff --version
```

Use `.venv/Scripts/python.exe -m ruff --version` on Windows. Do not solve this by installing Ruff globally.

### 11.2 ESLint cannot find `@eslint/js` or another package

**Cause:** `node_modules` was not restored from the committed lockfile, or the dependency is absent from `package.json`.

**Fix:** Confirm both Node package files are committed, then run:

```sh
npm ci
npm exec --no -- eslint --version
```

Do not install ESLint globally. `npm exec --no` intentionally fails rather than downloading undeclared packages.

### 11.3 Hook configuration or stage is missing

Create `.pre-commit-config.yaml` before installing hooks. Then reinstall both stages:

```sh
pre-commit validate-config
pre-commit install --hook-type pre-commit --hook-type pre-push
```

### 11.4 A full-repository scan enters generated or sensitive local directories

Confirm all three layers are present:

- `.gitignore` excludes `.venv`, `node_modules`, `.env*`, `.npmrc`, `.pypirc`, caches, and build output.
- The top-level pre-commit `exclude` prevents filename-based hooks from receiving those paths.
- Semgrep `--exclude` and Trivy `--skip-dirs`/`--skip-files` options prevent full-filesystem scans from entering them.

Do not remove scan exclusions from one layer on the assumption that another tool will honor them automatically.

### 11.5 VS Code and the terminal behave differently

Restart VS Code after installing host software, select the `.venv` interpreter, and inspect the verbose hook's reported command. Hook entries must use explicit project executables and must not rely on the editor's `PATH`.

## 12. Ongoing Development Standard

Before reporting any task complete, developers and AI tools run:

```sh
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
git diff --check
git diff
```

Use `--verbose` when diagnosing a failure. Git automatically enforces the same hook stages during commit and push.

For Docker-enabled repositories, also run `docker compose config` when Compose files change and `trivy image <image-name>` when an image is built or changed.

The completion report should state:

```text
Files changed:
- <file list>

Tests added or updated:
- <test list or reason none were needed>

Validation run:
- <command>: passed/failed

Security scans:
- <command>: passed/failed

Checks not run:
- <command and reason>

Notes or known limitations:
- <remaining concerns, if any>
```

Human review remains required, especially for authentication, authorization, database migrations, dependency changes, Docker/infrastructure configuration, payments, encryption, customer data, and external services.

## 13. Baseline Summary

| Concern                           | Standard                                                                |
| :-------------------------------- | :---------------------------------------------------------------------- |
| AI repository rules               | `AGENTS.md` plus small tool adapters                                    |
| Hook infrastructure               | Pinned `pre-commit` and Semgrep in `.venv`                              |
| Python formatting and linting     | Ruff                                                                    |
| Python type checking              | Pyright                                                                 |
| Python tests                      | pytest                                                                  |
| Python dependency audit           | pip-audit                                                               |
| JS/TS formatting                  | Project-local Prettier                                                  |
| JS/TS linting                     | Project-local ESLint                                                    |
| JS/TS type checking               | TypeScript                                                              |
| JS/TS tests                       | Vitest or the project's established runner                              |
| Node reproducibility              | Committed `package.json` and `package-lock.json`; install with `npm ci` |
| Secret detection                  | Gitleaks                                                                |
| Static security analysis          | Semgrep                                                                 |
| Dependency/configuration scanning | Trivy                                                                   |
| Compose validation                | `docker compose config` when applicable                                 |
| Local enforcement                 | pre-commit and pre-push hooks                                           |

The central rule is profile isolation: configure and run only the language tooling the repository actually needs, while every configured tool is pinned or explicitly installed and invoked through a deterministic project path.
