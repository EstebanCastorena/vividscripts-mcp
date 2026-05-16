# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Production OAuth via Amazon Cognito. `build_app(cognito=...)` runs the
  server as a Dynamic Client Registration facade that delegates the
  browser login to Cognito Hosted UI and passes Cognito's own access and
  refresh tokens through unchanged — the server no longer issues its own
  tokens in this mode. Adds the `/oauth/callback` redirect endpoint and
  an RFC 8414 authorization-server metadata document for client
  discovery; Bearer validation checks tokens against Cognito's JWKS.
- An offline mode (no Cognito configured) is preserved for local
  development and the test suite: the in-memory mock identity provider
  and self-issued tokens are used only when no Cognito config is given,
  and are never mounted in a Cognito-backed deployment.

## [0.1.0a0] — 2026-05-13

### Added
- `BackendProtocol` — structural contract for MCP backends (~20 methods across
  projects, workflow state, prompts, async jobs, scenes, URL handoff).
- `MockBackend` — in-memory implementation of `BackendProtocol`. Thread-safe,
  deterministic project IDs (counter-based), used in tests.
- Pydantic v2 models for the public tool surface (`ProjectInfo`, `Scene`,
  `WorkflowState`, `JobStatus`, `PromptPayload`, etc.).
- GitHub Actions CI: `pytest` + `mypy --strict` + `ruff check` + `ruff format`
  on Python 3.11 and 3.12.
- Pre-commit hooks (`ruff`, `mypy`, basic hygiene).
- `docs/architecture.md` — two-layer split, MCP primitives, OAuth flow,
  magic-link handoff, async job pattern.
