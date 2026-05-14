# vividscripts-mcp

> Remote MCP server for [VividScripts](https://vividscripts.ai/) — turn written stories into produced videos directly from [Claude Code](https://claude.com/claude-code).

[![CI](https://github.com/EstebanCastorena/vividscripts-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/EstebanCastorena/vividscripts-mcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## What it does

Connect Claude Code to your VividScripts account and you can paste a story and have it produced into a video — narration, illustration, sound effects, music, and final assembly — through a single conversation. You guide the creative choices; the server runs the pipeline.

When the video is finished, the server returns a URL that opens your project in the browser. From there you can review every scene, swap an image, adjust a sound effect, regenerate audio, and download the final MP4.

## Connect

In Claude Code:

```
/mcp add https://app.vividscripts.com/mcp
```

A browser window opens for you to authorize the connection with your VividScripts account. After authorizing once, Claude Code can drive the workflow on your behalf — no API keys to manage, no tokens to paste.

## Architecture

```
Claude Code  ──MCP/Streamable HTTP──►  vividscripts-mcp  ──►  VividScripts backend
   (your machine)         OAuth Bearer          (this repo)        (private)
```

Two layers connected by MCP:

- **Claude Code** (your machine) handles the reasoning — analyzing the story, grouping scenes, writing image prompts, picking sound effects.
- **VividScripts** (server) handles the media — text-to-speech, image generation, sound effects, music, video compilation, storage.

The package in this repo is the bridge between them. See [`docs/architecture.md`](docs/architecture.md) for the full design with diagrams.

## What's in the package

- **OAuth 2.1 with Dynamic Client Registration (RFC 7591)** with PKCE, backed by AWS Cognito. Single browser authorization; no manual key management.
- **MCP Tools, Resources, and Prompts** exposed as the spec defines them: ~22 Tools for actions and asynchronous jobs, ~10 Resources for read-only project data with `subscribe` for live status updates, 19 Prompts for parameterized AI templates that appear as `/slash-commands` in Claude Code.
- **Asynchronous job pattern.** Long-running media operations return a job identifier immediately; status streams back over the MCP transport, so progress shows in Claude Code without polling.
- **Auto-login handoff.** Workflow completion returns a short-lived signed URL that opens the editor with your account already signed in.

## Pluggable backends

The MCP tool layer talks to a [`BackendProtocol`](src/vividscripts_mcp/adapters/base.py). The package ships [`MockBackend`](src/vividscripts_mcp/adapters/mock.py), an in-memory implementation used in tests. Production deployments inject a real backend that calls VividScripts directly.

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

## Security

See [`docs/security.md`](docs/security.md) for the security design — authentication, token handling, schema validation, supply chain. For vulnerability reporting, see [`SECURITY.md`](SECURITY.md).

## License

MIT — see [`LICENSE`](LICENSE).

## Related

- VividScripts: [vividscripts.ai](https://vividscripts.ai/)
- Model Context Protocol: [modelcontextprotocol.io](https://modelcontextprotocol.io)
- Anthropic MCP Python SDK: [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)
