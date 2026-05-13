# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
