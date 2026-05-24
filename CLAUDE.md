# CLAUDE.md

Project context for Claude Code sessions opening `vividscripts-mcp`. Also useful for human contributors.

## What this project is

`vividscripts-mcp` is the remote MCP server that lets Claude Code drive VividScripts' story-to-video pipeline. Claude Code does the reasoning (story analysis, scene grouping, image prompts, sound-effect selection); this server handles the OAuth surface, the MCP tool dispatch, and — once Phase 3 wires the real backend — relays calls into VividScripts' media-generation infrastructure.

Public package, designed so a reviewer can clone it and run a working OAuth server against an in-memory mock backend in under a minute.

## Where we are right now

| Phase | Status | Notes |
|---|---|---|
| Phase 0 — repo skeleton | ✅ Done | Initial scaffold, BackendProtocol, MockBackend, CI. |
| Phase 1 — MCP + OAuth 2.1 | ✅ Done (2026-05-14) | OAuth 2.1 + DCR + PKCE + Bearer + 3 user-scoped MCP tools. 103 tests. |
| Phase 2 — prompt serving | 🔜 Next | Wires the 19 AI consultation points as MCP Prompts + the `save_step_result` tool. Tracked as KAN-30. |
| Phase 3 — real backend adapter | 🔜 Pending | First time this package meets the private `slide_editor` repo. KAN-31. |
| Phases 4-6 — media gen, magic-link, polish | 🔜 Pending | KAN-32 / KAN-33 / KAN-34. |

The OAuth surface from Phase 1 is **live on `main`**. All the work is RFC-strict (6749, 6750, 7591, 7636, 8252, 9728) with four documented deviations — see `docs/auth.md`.

## Quickstart

```bash
git clone https://github.com/EstebanCastorena/vividscripts-mcp.git
cd vividscripts-mcp
python -m venv .venv
.venv/Scripts/activate    # Linux/Mac: source .venv/bin/activate
pip install -e .[dev]
pre-commit install
pytest -q                  # expect 103 passed
```

Boot the server:

```bash
vividscripts-mcp serve --port 8000
```

…and in another shell:

```bash
curl -s http://127.0.0.1:8000/.well-known/oauth-protected-resource | jq
```

## Manually driving the OAuth flow

Phase 1's Dynamic Client Registration is session-cookie-gated, so a fresh manual walkthrough needs a session seeded first. The `--seed-session` flag handles this:

```bash
vividscripts-mcp serve --port 8000 --seed-session user-alpha
```

The server prints the cookie value at boot:

```
Seeded mock session for user 'user-alpha'.
  Cookie: vs_session=eyJ...
  Use it on /oauth/register, e.g.:
    curl -H 'Cookie: vs_session=eyJ...' ...
```

From there, `docs/auth.md` carries the full curl walkthrough — DCR → PKCE pair → authorize → mock IdP login → token exchange → MCP `tools/call`.

The full flow is also driven end-to-end in `tests/integration/test_oauth_to_create_project.py` — run that for the closest "real OAuth client talking to real server" experience.

## Coding conventions

- **Python 3.11+** with explicit type annotations everywhere.
- **mypy `--strict`** clean across `src/vividscripts_mcp`. CI runs it; pre-commit runs it. New imports need entries in `.pre-commit-config.yaml` under the mypy hook's `additional_dependencies`.
- **Pydantic v2** with `ConfigDict(extra="forbid")` on every model unless explicitly accepting unknown fields (e.g., RFC 7591's "extra metadata allowed").
- **Ruff** for lint + format. Selected rules: `E W F I B UP SIM RUF`. Run `ruff check --fix` + `ruff format` before committing — pre-commit does both for you.
- **Pluggable backends via Protocols.** Tools talk to a `BackendProtocol`; OAuth stores talk to `ClientStore` / `SessionStore` / `AuthCodeStore` / `RefreshTokenStore` / `JWKSProvider` Protocols. Phase 1 ships `Mock*` implementations; production replaces them in Phase 3.
- **Closure factories for MCP tools** (`make_create_project_tool(backend)` → callable). The factory hides the backend from the tool's MCP-visible input schema.
- **Per-request user identity via `contextvars`.** `BearerEnforcementMiddleware` binds `UserClaims` to a contextvar; tools read it via `require_user_claims()`. Never accept a `user_id` from the request body — that would be spoofable.

## Git workflow

The remote `main` branch is protected by a ruleset:

- No deletion, no force-push.
- **Linear history required (no merge commits).**
- Required status checks: `Test (Python 3.11)` + `Test (Python 3.12)` must pass.

Practical flow:

```bash
git checkout main && git pull
git checkout -b kan-XX-short-description     # work happens here
# … code, commit small, push the branch
git push -u origin kan-XX-short-description
gh pr create --base main --head kan-XX-short-description --title "..." --body "..."
# wait for CI green
gh pr merge --rebase --delete-branch         # preserves per-commit messages
```

Do **not** push directly to `main` — the ruleset will reject it.

When committing, use the `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer if Claude Code wrote the commit.

## Repo layout

```
src/vividscripts_mcp/
├── __main__.py              CLI entry (vividscripts-mcp serve)
├── server.py                Starlette host + FastMCP mount + middleware
├── models.py                Shared Pydantic models (Project*, Scene, JobStatus, ...)
├── adapters/
│   ├── base.py              BackendProtocol — the contract for any backend
│   └── mock.py              MockBackend — in-memory test fixture
├── oauth/
│   ├── metadata.py          RFC 9728 PRM + BearerEnforcementMiddleware
│   ├── dcr.py               RFC 7591 Dynamic Client Registration
│   ├── authorize.py         /oauth/authorize with PKCE
│   ├── token.py             /oauth/token (authorization_code + refresh_token)
│   ├── bearer.py            JWKS-based Bearer validator + redact_token
│   ├── mock_idp.py          Phase 1 inline mock IdP (Phase 3 removes)
│   ├── session.py           SessionStore Protocol + MockSessionStore
│   ├── store.py             ClientStore Protocol + MockClientStore
│   ├── codes.py             AuthCode + AuthRequestState stores
│   ├── tokens.py            Access/refresh token minting + RefreshTokenStore
│   ├── keys.py              Mock RSA signing key (Phase 3 swaps to Cognito JWKS)
│   ├── audit.py             Structured JSON-line audit logging
│   └── context.py           Per-request UserClaims contextvar
└── tools/
    └── projects.py          create_project, list_projects, get_project

tests/
├── unit/                    100 unit tests — module-level
└── integration/             3 integration tests — full ASGI stack
                             (test_oauth_to_create_project is the E2E walkthrough)

docs/
├── architecture.md          Two-layer split, design tradeoffs
├── auth.md                  Full OAuth walkthrough + security guarantees
└── security.md              Security design + threat-model summary
```

## Documentation outside this repo

- **`MCP Server PRD`** (Obsidian) — canonical spec; revised 2026-05-14 with Phase 1 status + deviations.
- **`MCP Roadmap`** (Obsidian) — phase dependency graph, parallelism wins.
- **`MCP Security Threat Model`** (Obsidian) — STRIDE register; §1 + §4 mitigations Resolved through Phase 1.
- **`MCP Phase 1 Completion Notes`** (Obsidian) — what shipped, deviations, lessons.
- **`MCP Phase 1 Open Items and Next Steps`** (Obsidian) — decision matrix + Phase 2 / Phase 3 scopes.
- **Jira epic KAN-27** — Claude Code MCP Server Integration. All KAN-XX tickets roll up here.

## Reading list before starting work

In order:

1. `README.md` — what this project is for (the pitch).
2. `docs/architecture.md` — two-layer split (intelligence vs infrastructure).
3. `docs/auth.md` — full OAuth flow if you'll be touching auth.
4. `tests/integration/test_oauth_to_create_project.py` — the canonical E2E flow as executable code.
5. For Phase 2 work: the Obsidian `MCP Server PRD § Phase 2` section + the [Anthropic MCP Prompts spec](https://modelcontextprotocol.io/).

## Quality gates (before opening a PR)

```bash
pytest -q                                      # all tests pass
mypy                                            # mypy strict clean
ruff check src/ tests/                         # lint clean
ruff format --check src/ tests/                # format clean
bandit -c bandit.yaml -r src/                  # static security clean (KAN-98)
pre-commit run --all-files                     # everything together
```

If any of those fail, the PR's CI will fail too.

### Bandit (KAN-98)

`bandit` runs against `src/` only (production code) and is configured in
`bandit.yaml` at the repo root. It catches the broad category of
`==`-on-secret, `subprocess shell=True`, `yaml.load`, etc. Half of the
audit's #14–#23 findings would have been auto-flagged.

When bandit flags a real false positive, annotate with `# nosec
B<rule_id> — <one-line justification>`. Never blanket `# nosec` without
a rule id or a reason.

### pip-audit (advisory, KAN-98)

`pip-audit` runs as a separate CI job that **does not block merges**. It
emits CVE matches against installed deps as workflow annotations. Until
the team writes down a triage policy (when to upgrade a transitive dep
vs. when to file an issue), treat the output as informational. Promote
to a blocking gate once the policy is decided.

## What's NOT in this repo (intentional)

- Real prompt template bodies (those stay in the private `slide_editor` repo — Phase 0 design refinement).
- Production secrets, Cognito user-pool IDs, real API keys.
- The VividScripts media-generation pipeline (TTS, image gen, video compilation).
- Any `slide_editor` integration code (that lives in the private repo and lands in Phase 3 via the `VividScriptsAdapter`).
