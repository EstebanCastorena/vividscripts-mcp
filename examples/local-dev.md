# Local development against MockBackend

> **This is not how end users use VividScripts.** Real users go to [app.vividscripts.com](https://app.vividscripts.com/), sign in via Google, and connect Claude Code over OAuth — see [`examples/claude-code-demo.md`](claude-code-demo.md). The setup on this page is for protocol-level debugging: contributors who want to inspect the MCP wire traffic, exercise the OAuth surface with `curl`, or develop new tools without a network round-trip.

`MockBackend` is an in-memory implementation of `BackendProtocol`. It satisfies every method the production backend does, with deterministic project IDs and stubbed media artifacts, so the package boots without any external dependencies — no AWS, no Cognito, no Replicate, no FFmpeg.

## What this gets you

- A working MCP server on `http://127.0.0.1:8000` with all 27 Tools + 20 Prompts registered.
- The full OAuth 2.1 + DCR + PKCE surface, running against an in-process mock IdP.
- Deterministic `MockBackend` so tool calls return predictable shapes — useful for snapshot tests or wire-format debugging.

What it does **not** get you: real images, real audio, a real compiled MP4, or anything that involves an external API. For an end-to-end demo with real media, go to production.

## Prerequisites

- Python 3.11 or 3.12 (CI runs both).
- Git.

## One-time setup

```bash
git clone https://github.com/EstebanCastorena/vividscripts-mcp.git
cd vividscripts-mcp
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
pytest -q                      # 565 passed
```

The pre-commit hooks (`ruff`, `mypy`, basic hygiene) run automatically on `git commit`. They mirror the CI gates exactly — if `pre-commit run --all-files` is green, CI will be green.

## Booting the server

The offline path (mock IdP + in-process self-mint signer) is explicitly opt-in: the server refuses to boot otherwise. Set the env flag, then start the server with a pre-seeded session so you can exercise Dynamic Client Registration without first walking the browser login:

```bash
export VIVIDSCRIPTS_ALLOW_OFFLINE_AUTH=1   # Windows: $env:VIVIDSCRIPTS_ALLOW_OFFLINE_AUTH=1
vividscripts-mcp serve --port 8000 --seed-session user-alpha
```

The server prints the seeded cookie to **stderr** (not stdout — stdout ends up in CI logs):

```
WARNING: dev seed-session active — full power, no auth. DO NOT use VIVIDSCRIPTS_ALLOW_DEV_SEED=1 in production.
Seeded mock session for user 'user-alpha'.
  Cookie: vs_session=eyJ...
  Use it on /oauth/register, e.g.:
    curl -H 'Cookie: vs_session=eyJ...' ...
```

Copy that cookie — DCR requires it.

## Driving the OAuth dance

In a second terminal:

```bash
VS=http://127.0.0.1:8000
COOKIE='vs_session=eyJ...'    # from the boot output

# 1. Naked /mcp returns 401 with a pointer to the PRM document.
curl -i -X POST $VS/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# 2. Discovery.
curl -s $VS/.well-known/oauth-protected-resource | jq

# 3. Register a client (Dynamic Client Registration).
CLIENT=$(curl -s -X POST $VS/oauth/register \
  -H 'Content-Type: application/json' \
  -b "$COOKIE" \
  -d '{
        "redirect_uris": ["http://127.0.0.1:8080/callback"],
        "client_name": "local-dev"
      }' | jq -r .client_id)
echo "client_id=$CLIENT"

# 4. PKCE pair.
VERIFIER=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
CHALLENGE=$(python -c "
import base64, hashlib, sys
v = sys.argv[1].encode()
print(base64.urlsafe_b64encode(hashlib.sha256(v).digest()).rstrip(b'=').decode())
" "$VERIFIER")

# 5. Begin authorization — get redirected to the mock IdP login.
curl -i "$VS/oauth/authorize?response_type=code&client_id=$CLIENT&\
redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Fcallback&\
code_challenge=$CHALLENGE&code_challenge_method=S256&state=csrf-xyz"
```

The integration test [`tests/integration/test_oauth_to_create_project.py`](../tests/integration/test_oauth_to_create_project.py) drives the same dance against the in-process ASGI app and is the canonical reference for the exact request/response shapes. If you want a runnable example, run it under `pytest -v -s` and read the assertions.

After the mock IdP login and code exchange, you'll have an access token. Every subsequent MCP request needs `Authorization: Bearer <access_token>`.

## Calling a tool

The MCP wire flow is `initialize` → `notifications/initialized` → `tools/call`. The `Mcp-Session-Id` header from `initialize` must be echoed on subsequent requests.

```bash
# initialize
SESS=$(curl -sD - -X POST $VS/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}' \
  | grep -i '^Mcp-Session-Id:' | awk '{print $2}' | tr -d '\r')

# tools/call create_project
curl -s -X POST $VS/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SESS" \
  -d '{
        "jsonrpc":"2.0","id":2,"method":"tools/call",
        "params":{
          "name":"create_project",
          "arguments":{
            "story":"She knocked on her own door at 3 a.m.",
            "settings":{"voice":"female","dimension":"landscape"}
          }
        }
      }'
```

The shape returned is `ProjectInfo` — `project_id`, `project_name`, `editor_url`, `created_at`. `editor_url` is a `MockBackend` stub; in production the URL opens a real editor.

## Inspecting what's wired up

```bash
# Every Tool currently registered
.venv/Scripts/python.exe -c "
import asyncio
from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.server import create_mcp_server
mcp = create_mcp_server(MockBackend())
for t in asyncio.run(mcp.list_tools()):
    print(t.name)
"

# Every Prompt and its required input fields
.venv/Scripts/python.exe -c "
from vividscripts_mcp.prompts import PROMPT_INTERFACES
for name, p in PROMPT_INTERFACES.items():
    required = p.input_schema.get('required', [])
    print(f'{name}: {required}')
"
```

The full catalog (parameters + 2–3 sentence descriptions + example calls) is in [`docs/tools.md`](../docs/tools.md).

## Common workflows

### "I added a new tool — does the wire surface look right?"

1. Register it in the appropriate `tools/*.py` registrar.
2. Run `pytest -q` — your tool factory should already have unit tests.
3. Boot the server (`vividscripts-mcp serve --port 8000 --seed-session user-alpha`).
4. Drive the OAuth dance, then `tools/list` to confirm the tool's `inputSchema` looks right.
5. Run `python scripts/gen_tools_docs.py --check` — if it exits non-zero, the catalog is stale.

### "I changed a Pydantic model"

`pydantic.BaseModel` with `ConfigDict(extra="forbid")` is the rule, not the exception. Adding a field is backwards-compatible; removing or renaming one is a wire break — bump the version and document it in `CHANGELOG.md`.

### "I want to see what a failing schema validation looks like"

Call `save_step_result` with a deliberately wrong `result` shape (e.g. an integer where the schema expects a string). The response is `success=False` with `validation_errors` carrying field-level paths. Nothing is persisted on a validation failure — that's the gate that lets Claude self-correct mid-pipeline without contaminating state.

### "I want to test the magic-link mint without a real backend"

`MockBackend.mint_magic_link` returns a stub URL. The signing / redemption logic lives in the production backend (`cognito_auth`), so the wire return shape is exercised but the URL isn't actually clickable. For real magic-link end-to-end, you need production.

## Quality gates

Before opening a PR:

```bash
pytest -q                                # 565 passed
mypy --strict src/vividscripts_mcp       # mypy strict clean
ruff check src/ tests/                   # lint clean
ruff format --check src/ tests/          # format clean
bandit -c bandit.yaml -r src/            # 0 issues
pre-commit run --all-files               # everything together
python scripts/gen_tools_docs.py --check # docs/tools.md in sync
```

If any of those fail, CI will fail too. The fast path is `pre-commit run --all-files`; it covers lint, format, mypy, and basic hygiene in one command.

## When you're done

If you have working changes you don't want to lose, commit them on a branch before exiting the shell. Worktrees and venvs are disposable; commits are durable.

```bash
git checkout -b kan-XX-short-description
git add path/to/changed/files
git commit -m "KAN-XX: short summary"
git push -u origin kan-XX-short-description
gh pr create --base main
```

See the top-level [`CLAUDE.md`](../CLAUDE.md) for the full branch-and-merge convention.
