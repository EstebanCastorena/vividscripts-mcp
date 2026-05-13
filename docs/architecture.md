# Architecture

How the VividScripts MCP server is put together, and why.

## The two-layer split

```
┌─────────────────────────────────────────────────┐
│         User's Machine (Claude Code)             │
│                                                  │
│  Claude Code                                     │
│  ├── AI brain (Claude Sonnet/Opus)               │
│  ├── Orchestrator (drives 16-step workflow)     │
│  └── User-facing chat (course-correct anytime)   │
└─────────────────┬──────────────────────────────┘
                  │ MCP over Streamable HTTP
                  │ Authorization: Bearer <oauth-token>
                  ▼
┌─────────────────────────────────────────────────┐
│            vividscripts-mcp (this repo)          │
│                                                  │
│   FastMCP server  +  OAuth 2.1 + DCR             │
│   Tool registry   +  Bearer middleware           │
│                                                  │
│         │ BackendProtocol                         │
│         ▼                                        │
│   ┌──────────────────┐    ┌─────────────────┐    │
│   │ Real backend     │ or │  MockBackend   │    │
│   │ (private repo)   │    │  (in-memory)   │    │
│   └────────┬─────────┘    └─────────────────┘    │
└────────────┼─────────────────────────────────────┘
             │
             ▼
   VividScripts platform
   (ProjectManager, MediaServices, EFS, ...)
```

**Intelligence Layer** (top): Claude Code. All reasoning lives here — story
analysis, scene splitting, title writing, character bible construction, image
prompt composition, SFX selection. Costs are paid by the user's Claude
subscription.

**Infrastructure Layer** (bottom): VividScripts. All media — TTS, Whisper,
image generation, SFX generation, music, video compilation, project storage.
Costs are paid by VividScripts.

This package is the bridge.

## Pluggable backends

The MCP tool layer never talks to VividScripts directly. It talks to a
[`BackendProtocol`](../src/vividscripts_mcp/adapters/base.py) — a structural
type with ~20 methods covering projects, workflow state, prompts, async jobs,
scenes, and URL handoff.

| Backend | Where it lives | Used for |
|---------|---------------|----------|
| `MockBackend` | This repo (`src/vividscripts_mcp/adapters/mock.py`) | Tests, local protocol development |
| Production backend | Separate private repo | Real VividScripts integration |

## MCP primitives

The server uses all three MCP primitive types as the spec intends.

### Tools (~22) — actions with side effects

Project lifecycle (`create_project`, `delete_project`, `duplicate_project`,
`set_project_settings`), workflow advancement (`save_step_result`), async
media job submissions (`generate_audio`, `generate_images`, `generate_sfx`,
`generate_music`, `generate_thumbnail`, `animate_scene`, `compile_video`,
`regenerate_scene_*`), scene editing (`update_scene_*`, `add_scene`,
`remove_scene`), URL handoff (`mint_magic_link`, `get_video_download_url`),
and custom prompt overrides (`set_custom_prompt_override`).

### Resources (~10) — read-only data via URIs

| URI Template | Returns | Subscribable |
|---|---|---|
| `vividscripts://projects/` | List of user's projects | Yes |
| `vividscripts://projects/{id}` | Full project detail | Yes |
| `vividscripts://projects/{id}/state` | `WorkflowState` | Yes |
| `vividscripts://projects/{id}/scenes` | Array of scenes | Yes |
| `vividscripts://projects/{id}/scenes/{index}` | Single scene | Yes |
| `vividscripts://projects/{id}/blueprint` | Story blueprint | No |
| `vividscripts://projects/{id}/bibles` | Character + location bibles | No |
| `vividscripts://projects/{id}/video` | Video status + download URL | Yes |
| `vividscripts://workflow/steps` | The 16-step pipeline definition | No |
| `vividscripts://jobs/{job_id}` | Live job status | Yes (primary use) |

`subscribe` on `vividscripts://jobs/{job_id}` lets Claude Code stream
progress updates while a long media job runs — no polling loop.

### Prompts (19) — parameterized templates

The 19 AI consultation points in the VividScripts pipeline are exposed as
MCP Prompts (`prompts/list`, `prompts/get`). Each surfaces as a
`/slash-command` in Claude Code, and can also be retrieved
programmatically while the workflow is being driven autonomously.

The template **bodies** are served by the production backend (where they
live as private IP); the public package declares only the prompt
**interfaces** (name, input/output schemas, descriptions). Custom user
overrides are layered on top before the rendered prompt is returned.

## OAuth 2.1 + Dynamic Client Registration

```
1. Claude Code POSTs /mcp without auth
   → 401 + WWW-Authenticate: Bearer resource_metadata=<discovery URL>

2. Claude Code GETs /.well-known/oauth-protected-resource
   → metadata

3. Claude Code POSTs /oauth/register
   → client_id (RFC 7591 dynamic registration)

4. Browser opens /oauth/authorize with PKCE
   → Redirects to Cognito Hosted UI → user logs in
   → Returns to server → server returns code to Claude Code

5. Claude Code POSTs /oauth/token with code + code_verifier
   → access_token (Cognito-issued JWT)

6. All subsequent calls: Authorization: Bearer <token>
   → Bearer middleware validates via Cognito JWKS, extracts cognito_sub
```

Notes:

- **PKCE required.** No auth code requests without `code_challenge`.
- **Cognito-direct tokens.** We don't re-sign; we pass through Cognito's
  access tokens and validate via JWKS. Fewer keys to manage.
- **JWKS cached** for 1 hour.

## Magic-link URL handoff

The "here's your URL" moment when the workflow finishes. The MCP returns
something like `https://app.vividscripts.com/m/jR8k2x` and clicking it:

1. Verifies a signed JWT (HS256, 5-min TTL, single-use via `jti` cache).
2. Sets the Flask session as if the user just logged in.
3. Redirects to `/studio?project=<name>` — editor opens with the project loaded.

Same pattern Notion, Linear, and Vercel use for email magic links.

## Async job pattern

Long-running media operations return a `job_id` immediately:

```
generate_images(project_id, scenes_with_prompts, style) → {job_id: "..."}
```

Claude Code subscribes to `vividscripts://jobs/{job_id}` and receives
streaming status updates over the MCP transport. Job status is persisted
on the server so workflows survive crashes and session disconnects.
