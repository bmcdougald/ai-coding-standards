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
