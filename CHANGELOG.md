# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — Unreleased

> **v1.0 is the first portfolio-ready release of `vividscripts-mcp`.** Phases 0–5 of the integration are shipped, the package surfaces 27 Tools + 20 Prompts over a Cognito-brokered OAuth 2.1 endpoint, and a 565-test suite — including a 232-test security regression block — gates every commit. The repository is intended to be read top-to-bottom by a reviewer; the [README](README.md), [`docs/architecture.md`](docs/architecture.md), and [`docs/tools.md`](docs/tools.md) are the entry points.

### Highlights

- **Story → finished video, end-to-end from Claude Code.** A reviewer can sign up at [app.vividscripts.com](https://app.vividscripts.com/), connect Claude Code with a single `.mcp.json` snippet, paste a story, and click a magic-link into the editor when the pipeline finishes. No API keys, no token paste. See [`examples/claude-code-demo.md`](examples/claude-code-demo.md).
- **OAuth 2.1 + Cognito broker.** Dynamic Client Registration ([RFC 7591](https://www.rfc-editor.org/rfc/rfc7591)), PKCE-required ([RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)), JWT validation against Cognito JWKS with RS256 pinned. Offline mode preserved for tests and local dev, gated by an explicit env opt-in. Full walkthrough in [`docs/auth.md`](docs/auth.md).
- **Async-job media pipeline.** `generate_audio` / `generate_images` / `generate_sfx` / `generate_music` / `animate_scene` / `compile_video` all return `job_id` immediately; Claude Code polls `check_job` and surfaces progress in chat. Workflows survive crashes and session disconnects.
- **Magic-link URL handoff.** When `compile_video` completes, `mint_magic_link` returns a short-lived HS256-signed URL that opens the editor with the project loaded and the user already authenticated. TTL ≤ 5 min, single-use via 122-bit `jti`, algorithm pinned, token scrubbed from logs and browser history. Full threat model in [`docs/magic-link.md`](docs/magic-link.md).
- **20 MCP Prompts as integration contract.** Every AI consultation point in the VividScripts pipeline is an MCP Prompt with a JSON-Schema-bound input and a JSON-Schema-validated output. Template bodies stay in the private backend (creative IP); the public package ships only the interfaces — which is the integration contract that matters. Full catalog: [`docs/prompts.md`](docs/prompts.md).
- **565-test suite, including 232 security regressions.** The regression block was written against the 2026-05-17 third-party audit closure (KAN-93 → KAN-98). Every audit finding is an executable assertion, so a regression that re-introduces the vulnerability would re-open the audit.
- **`bandit` (blocking) + `pip-audit` (advisory) on every PR.** Static security gate runs in CI against `src/`; dependency CVE matches surface as workflow annotations. Both are wired in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

### Phase-by-phase summary

| Phase | What it shipped | Tickets |
|---|---|---|
| **Phase 0** — Repo scaffold | `BackendProtocol`, `MockBackend`, Pydantic models, CI on Python 3.11/3.12, pre-commit | KAN-30-ish parents |
| **Phase 1** — MCP + OAuth 2.1 | DCR, PKCE, Bearer middleware, `WWW-Authenticate` discovery, mock IdP, three user-scoped project tools | KAN-46 → KAN-53 |
| **Phase 2** — Prompt serving | 20 MCP Prompts, schema-validated `save_step_result`, custom-override tools, `list_workflow_steps` | KAN-30 / KAN-56 → KAN-59 |
| **Phase 3** — Real backend adapter | Backend dispatch contract; production adapter lives in the private repo | KAN-31 |
| **Phase 4** — Async media | 11 media tools (`generate_*`, `check_job`, `select_music`, `regenerate_scene_*`) over `submit_job` | KAN-68 → KAN-79 |
| **Phase 5** — Magic-link + scenes | `mint_magic_link`, `get_video_download_url`, 6 scene-editing tools bidirectional with the web editor | KAN-77 / KAN-78 / KAN-33 |
| **Phase 6** — Portfolio polish | README rewrite, three architectural Mermaid diagrams, full Tools/Prompts catalog, demo + local-dev walkthroughs, PyPI publish workflow, v1.0 release notes (this entry) | KAN-34 |

### Public surface (v1.0)

**Tools (27).** Project lifecycle: `create_project`, `list_projects`, `get_project`. Workflow state: `save_step_result`, `get_workflow_state`, `list_workflow_steps`. Custom prompt overrides: `set_custom_prompt_override`, `get_custom_prompt_override`. Media (async): `generate_audio`, `generate_images`, `generate_sfx`, `generate_thumbnail`, `animate_scene`, `generate_music`, `compile_video`, `regenerate_scene_image`, `regenerate_scene_audio`. Media (sync): `select_music`, `check_job`. URL handoff: `mint_magic_link`, `get_video_download_url`. Scenes: `get_scenes`, `get_scene`, `update_scene_prompt`, `update_scene_text`, `add_scene`, `remove_scene`.

**Prompts (20).** Pipeline order: `story_blueprint`, `narration_grouping`, `story_summarizer`, `title_generator`, `short_title_generator`, `stage_direction_bible`, `stage_direction_first`, `stage_direction_subsequent`, `image_split_analyzer`, `image_director_first`, `image_director_subsequent`, `image_director_followup`, `sound_effect_category`, `sound_effect_analyzer`, `thumbnail`, `thumbnail_text`, `thumbnail_format_selector`, `motion_direction`. User-initiated (outside the linear pipeline): `story_optimization`, `image_prompt_edit`.

**Resources.** Not exposed in v1.0. The URI scheme (`vividscripts://...`) is reserved for a future minor release that will let Claude Code subscribe to live job and project state instead of polling `check_job`. See [`docs/tools.md`](docs/tools.md#resources).

### Security audit closure (2026-05-17)

The third-party security audit identified 23 findings across the OAuth surface, the magic-link handoff, the bearer middleware, and the input-handling boundary. Every finding is closed in v1.0:

- **KAN-93** — input-handling hardening: project name regex bound, story / template / scene-text size caps, magic-link `view` and `ttl_seconds` allow-list.
- **KAN-94** — bearer middleware: `aud` / `iss` / `token_use` / `exp` all checked; algorithm pinned to `["RS256"]`; rejection of unknown `kid`.
- **KAN-95** — JWT claim-policy hardening + JWK key-confusion binding.
- **KAN-96** — refuse-to-start guard for mock IdP + self-mint signer. Offline path requires `VIVIDSCRIPTS_ALLOW_OFFLINE_AUTH=1`; non-loopback bind additionally requires `VIVIDSCRIPTS_ALLOW_OFFLINE_NETWORK=1`. Strict `"1"` matching to avoid truthy-string footgun.
- **KAN-97** — URL / input / redirect hardening pass: `redirect_uri` exact-match, magic-link view allow-list, TTL cap, payload caps on every free-text field.
- **KAN-98** — `bandit` (blocking) + `pip-audit` (advisory) CI gates; `--seed-session` user-id alphabet bound and stderr-only cookie emission.

The audit register itself is private; the 232-test regression block in `tests/` encodes every finding as an executable assertion.

### Notable design decisions

- **Cognito tokens pass through unchanged.** The server validates Cognito's RS256 JWTs against the JWKS and never re-signs anything in broker mode. Fewer keys to manage, fewer points of failure.
- **`user_id` is sourced only from the validated Bearer token.** Every `BackendProtocol` method takes `user_id` as positional-first; `BearerEnforcementMiddleware` binds claims to a `contextvars` slot; tool handlers read `require_user_claims().sub`. A new tool that tries to read `user_id` from a request body fails `mypy --strict`.
- **Cross-tenant access returns 404, not 403.** A tool call against another user's `project_id` reports "project not found" rather than "permission denied", so probing doesn't reveal that other users' projects exist.
- **Magic-link is a stateless signed JWT, not an opaque token.** Mint side needs no storage; redemption verifies signature + expiry + purpose claim + single-use `jti` (cache fails closed). Works the moment it's created, including across processes.

### Quality gates on every PR

```bash
pytest -q                                # 565 passed
mypy --strict src/vividscripts_mcp       # mypy strict clean
ruff check src/ tests/                   # lint clean
ruff format --check src/ tests/          # format clean
bandit -c bandit.yaml -r src/            # 0 issues
pre-commit run --all-files               # everything together
```

Plus an advisory `pip-audit` job (non-blocking) that surfaces CVE matches as workflow annotations.

### Cutting the release

The v1.0.0 tag and the PyPI upload are human steps — this CHANGELOG entry is the draft. Once the entry is approved, a maintainer cuts the release with:

```bash
gh release create v1.0.0 --notes-file CHANGELOG.md
```

That triggers the `release.published` event, which fires [`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml). The workflow builds the wheel + sdist with `python -m build` and uploads via PyPA's OIDC trusted-publishing — no long-lived API tokens in secrets. The `pypi` Environment (Settings → Environments) is what gates the OIDC token; the trusted publisher is configured on the PyPI side to point at this repository.

A `workflow_dispatch` entry is available for dry-run builds — manual run with `dry-run: true` (default) produces the wheel + sdist as a workflow artifact for inspection without uploading.

### Out of scope for this entry

- **Recording the README demo GIF.** Tracked separately as [KAN-92](https://estebancastorenajr.atlassian.net/browse/KAN-92) — it needs a clean prod run and an actual human, and won't hold the engineering phase open. README ships with a placeholder `![demo](docs/img/demo.gif)`.
- **Cutting the v1.0.0 tag.** Human action, see above.
- **Publishing to PyPI.** Triggered automatically by the tag-release event; nothing for the PR-merge step to do.

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
