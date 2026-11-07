# Project AI Development Rules

## Required behavior

- Read this file and applicable project configuration before making changes.
- Follow existing project patterns and reuse existing dependencies where possible.
- Make small, focused changes. Do not make unrelated refactors.
- Add or update tests when behavior changes.
- Do not weaken tests, lint rules, type checks, security scans, or Git hooks to make checks pass.
- Do not disable, skip, delete, or bypass pre-commit hooks.
- Do not use `git commit --no-verify` or `git push --no-verify`.
- Do not claim a check passed unless it was run and its result was reviewed.

## Security rules

- Never add secrets, passwords, tokens, private keys, connection strings, production data, or customer data to the repository.
- Never commit `.env` files. Use `.env.example` with placeholder values.
- Never log credentials, tokens, session identifiers, or sensitive user data.
- Validate untrusted input at application boundaries.
- Use parameterized queries or approved ORM APIs. Do not construct SQL with string concatenation.
- Do not pass untrusted input to shell commands.
- Do not add dependencies unless necessary. Explain every new dependency.

## Development Policies

- Do not work on master/main directly unless specifically told to do so.
- Use feature branches for repository changes or new development.
- Naming for feature branches should follow the standard naming convention of branch work type (feature, bugfix, docs, tests, etc) followed by a forward slash and descriptive branch name (for example, feature/new-feature-shortname).
- Creating and publishing new branches that don't have work yet should not trigger pre-commit or pre-push because the branch is clean with previously scanned code.
- Check for existing functionality first before creating something new to avoid duplication or redundant code/architecture.

## Required validation

Before reporting work complete, run:

    pre-commit run --all-files
    pre-commit run --hook-stage pre-push --all-files

If the project contains Docker or Docker Compose files, also run:

    trivy fs --scanners vuln,secret,misconfig .

If a Docker Compose file exists, also run:

    docker compose config

If a Docker image was built or changed, also run:

    trivy image <image-name>

Review the final changes with:

    git diff --check
    git diff

Fix failures caused by the change before reporting completion.

## Approval required

Ask before:

- deleting data;
- changing database schemas or migrations;
- changing authentication, authorization, roles, permissions, encryption, payments, or billing;
- changing Docker, infrastructure, deployment, cloud, or environment configuration;
- adding external network calls, analytics, telemetry, or third-party services;
- adding dependencies with unclear ownership, maintenance, licensing, or security history.

## Required completion summary

End every completed task with:

Files changed:

- ...

Tests added or updated:

- ...

Validation run:

- command: passed/failed

Security scans:

- command: passed/failed

Checks not run:

- command and reason

Notes or known limitations:

- ...

## Project-local quality suite

All rules above remain applicable. Use the following local commands for this profile:

- `.tools/venv/bin/python .quality/run.py check`: formatting, lint, types, schemas, Docker.
- `.tools/venv/bin/python .quality/run.py security`: Gitleaks, Semgrep, Trivy.
- `.tools/venv/bin/python .quality/run.py commit`: fast checks and staged secret scanning.
- `.tools/venv/bin/python .quality/run.py push`: full checks and security scanning.

Python/mixed repositories use `.tools/venv/bin/pre-commit`; Node-only repositories use Husky instead. For Node-only repositories, the commit/push commands above replace the two pre-commit framework validation commands; do not add a second hook manager.
Use `.tools/bin/trivy` instead of a globally installed Trivy. The security command applies project exclusions and configured severity thresholds.
Docker runtime checks stay configured. Missing Docker CLI, Compose plugin, or accessible daemon is an explicit dependency skip, not a successful validation. Invalid configurations and failed builds/scans must fail. Do not install Docker or change build targets without approval.
See [setup and usage](QUALITY.md) for tool responsibilities and commands.

## Existing-tool integration

The hook manager recorded in `.quality/config.json` is authoritative. Amended projects may retain an existing pre-commit, Husky, or shell-hook setup instead of the profile default. Do not install a second active hook manager.
For a recorded manager other than pre-commit, the commit/push commands above are the required equivalents of the two original pre-commit validation commands.
Amendment retains existing analyzer rules and tests; replacement standardizes recognized scanning configuration, not application metadata or tests. Keep the configuration/hook backups until the migration has been validated.
