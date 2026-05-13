# vividscripts-mcp

> Remote MCP server that lets [Claude Code](https://claude.com/claude-code) act as the AI brain for [VividScripts](https://app.vividscripts.com) — a 16-step pipeline that turns text stories into produced videos.

[![CI](https://github.com/EstebanCastorena/vividscripts-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/EstebanCastorena/vividscripts-mcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## What it does

A user with a Claude Code subscription pastes a story, and Claude Code drives the entire VividScripts workflow conversationally — picking the title, writing image prompts, choosing sound effects, course-correcting whenever the user wants. VividScripts handles the heavy infrastructure (TTS, image generation, video compilation, storage). At the end, the user gets a Vercel-style URL drop:

```
🎬 Your video is ready!
   Watch: https://app.vividscripts.com/m/jR8k2x      ← magic-link, auto-logs in
```

Click once → editor opens, logged in, project loaded.

## Why it exists

- **Cost** — LLM tokens were 40–50% of per-video cost for VividScripts. Pushing them to the user's Claude subscription nearly halves it.
- **UX** — Mid-workflow conversations are impossible in a fire-and-forget web UI but trivial in Claude Code.

## Architecture

Two layers:

- **Intelligence Layer** — Claude Code (user's machine). Handles all reasoning: story analysis, scene grouping, titles, character bibles, image prompts, SFX selection.
- **Infrastructure Layer** — VividScripts server. Handles all media: TTS, Whisper, image generation, SFX generation, music, video compilation.

The MCP server is the bridge. It serves prompts (so Claude Code knows what to think about), accepts results (validates and persists), and dispatches media operations as async jobs.

```
Claude Code  ──MCP/Streamable HTTP──►  vividscripts-mcp  ──►  VividScripts backend
   (user)            OAuth Bearer            (this repo)        (private)
```

See [docs/architecture.md](docs/architecture.md) for the full design with sequence diagrams.

### What's interesting under the hood

- **OAuth 2.1 + Dynamic Client Registration (RFC 7591)** with PKCE, backed by AWS Cognito. Claude Code connects with a single browser auth, no API keys.
- **MCP Tools, Resources, and Prompts** used as the spec intends: ~22 Tools for actions and async job submission, ~10 Resources for read-only data (with `subscribe` for live job-status updates), 19 Prompts for parameterized AI templates (which surface as `/slash-commands` in Claude Code).
- **Magic-link handoff** — a signed 5-minute JWT that auto-creates a browser session and lands you in the editor. Same pattern Notion, Linear, and Vercel use.
- **Async job pattern** — every long-running media operation returns a `job_id`; Claude Code subscribes to the corresponding resource and reports progress to the user without polling.

For the full security design — auth flow, magic-link replay protection, schema validation, supply-chain hardening — see [`docs/security.md`](docs/security.md). For vulnerability reporting, see [`SECURITY.md`](SECURITY.md).

## Pluggable backends

The MCP tool layer talks to a [`BackendProtocol`](src/vividscripts_mcp/adapters/base.py) implementation. The package ships with [`MockBackend`](src/vividscripts_mcp/adapters/mock.py), an in-memory implementation used in tests. Production deployments wire up a real backend that calls VividScripts directly (lives in a separate private repo).

## Development

```bash
git clone https://github.com/EstebanCastorena/vividscripts-mcp.git
cd vividscripts-mcp
python -m venv .venv
. .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
pytest
```

Type-checked with `mypy --strict`, linted and formatted with `ruff`. Python 3.11+.

## License

MIT — see [LICENSE](LICENSE).

## Related

- VividScripts platform: [app.vividscripts.com](https://app.vividscripts.com)
- Model Context Protocol: [modelcontextprotocol.io](https://modelcontextprotocol.io)
- Anthropic SDK for MCP: [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)
