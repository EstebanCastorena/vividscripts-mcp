# Claude Code → VividScripts demo

End-to-end walkthrough of using Claude Code to drive VividScripts: sign up, connect, paste a story, watch the pipeline run, click the magic-link, edit the result. Real commands, real URLs, real expected outputs.

If you want to develop the MCP server itself or inspect the wire protocol, see [`examples/local-dev.md`](local-dev.md) instead. This page is the *user* path.

## What you'll do

1. Sign up at [app.vividscripts.com](https://app.vividscripts.com/) with Google.
2. Add VividScripts to your Claude Code config.
3. Authorize once in the browser.
4. Paste a story and ask for a video.
5. Click the magic-link, land in the editor.

Total time: about ten minutes (most of it watching the pipeline run).

## Step 1 — Sign up

Go to **[app.vividscripts.com](https://app.vividscripts.com/)**. Click "Sign in with Google". Authorize the standard Google OAuth scopes (email, profile — no Drive, no Calendar). You'll land on your dashboard with no projects yet.

Behind the scenes, Cognito federates your Google sign-in and creates a user pool entry. No credit card, no API keys; the OAuth scopes Claude Code will use are tied to this same identity.

## Step 2 — Connect Claude Code

Add VividScripts to your Claude Code MCP config. Either edit `~/.claude.json` (global) or drop a `.mcp.json` in the project where you'll be working:

```json
{
  "mcpServers": {
    "vividscripts": {
      "type": "http",
      "url": "https://app.vividscripts.com/mcp"
    }
  }
}
```

Then start (or restart) Claude Code in that directory and run:

```
/mcp
```

Claude Code lists `vividscripts` as "needs authorization". Approve it; a browser window opens to `app.vividscripts.com/oauth/authorize`. Sign in (same Google → Cognito path) and approve the consent screen.

You're back in Claude Code. The MCP indicator shows `vividscripts` as connected. No tokens to paste — Claude Code holds the access token and the refresh token for next time.

> Under the hood: Claude Code did Dynamic Client Registration (RFC 7591), generated a PKCE pair, sent you to `/oauth/authorize`, the server bounced you to Cognito Hosted UI, your Google session signed you in, Cognito issued an access token, the server passed it through, and Claude Code stashed it. The whole exchange is documented in [`docs/auth.md`](../docs/auth.md) if you want to see the request shapes.

## Step 3 — Paste a story

In Claude Code:

```
I want to make a 60-second VividScripts video from this story:

She knocked on her own door at 3 a.m. The porch light was off,
the rain had just stopped, and the knocker was warmer than it
should have been. She heard footsteps inside — her footsteps,
unmistakably — pacing in a careful rectangle. The door clicked
open from the inside. She did not look up.

Make it psychological horror, female narrator, landscape, vintage
illustrated. Use whatever music mood fits.
```

Claude Code knows the workflow because the server told it: 27 Tools, 20 Prompts, an embedded JSON Schema on every step. The conversation goes something like this:

```
> Claude (reading tools/list): I'll start by creating the project, then
> walk the 16-step pipeline.

Tool call: create_project
  story: "She knocked..."
  settings: { style: "vintage_illustrated", voice: "female",
              dimension: "landscape" }
Result: project_id="Knocking_Inside", editor_url="https://app.vividscripts.com/studio?project=Knocking_Inside"

> Claude: Project created. Running the blueprint.

Slash command: /story_blueprint
  story: <the story>
  numbered_story: "1. She knocked..."
  paragraph_count: 4
[Claude composes the structured result; save_step_result validates and persists]

> Claude: Blueprint done — psychological horror, three-act, narrator
> out-of-frame, "low-key amber" lighting motif. Splitting paragraphs
> into scenes...

[narration_grouping × 4 paragraphs]
[stage_direction_bible, then per-scene stage directions]
[image_split_analyzer × N scenes]
[image_director_first, then per-scene image directors]
[sound_effect_category + sound_effect_analyzer per scene]
[title_generator, short_title_generator, thumbnail_format_selector, thumbnail, thumbnail_text]

> Claude: Story analysis done. Kicking off media generation.

Tool call: generate_audio { project_id: "Knocking_Inside" }
  → job_id=job_aud_91a7
Tool call: generate_images { project_id: "Knocking_Inside" }
  → job_id=job_img_4f02
Tool call: select_music { project_id: ..., mood: "uneasy_strings" }
  → needs_generation: false
[Claude polls check_job; progress streams back in chat]
```

You can interrupt and redirect at any point ("make the title less clickbaity", "rerender scene 4 with harsher lighting", "swap to a male narrator"). Each correction is one tool call.

## Step 4 — Watch the pipeline

Real numbers from a typical run:

| Stage | Duration | What's happening |
|---|---|---|
| Project + blueprint + scenes | ~30s | All Claude reasoning, no media |
| `generate_audio` | ~45s | TTS for every scene + word-level transcription |
| `generate_images` | ~3–6 min | Per-scene image gen via Replicate / BFL / FAL |
| `generate_sfx` | ~30s | SFX selection + synthesis on word timestamps |
| `generate_music` | ~30s | If catalog miss — else instant |
| `animate_scene` | ~2–4 min | Image-to-video for intro scenes |
| `generate_thumbnail` | ~20s | One YouTube thumbnail |
| `compile_video` | ~30s | Final FFmpeg pass |

Claude keeps you updated as jobs progress — that's the `check_job` polling pattern, surfaced as a status line in chat. You can keep working in another window; the workflow survives reconnects.

## Step 5 — Click the magic-link

When `compile_video` reaches `completed`, Claude mints a magic-link:

```
Tool call: mint_magic_link
  project_id: "Knocking_Inside"
  view: "editor"
  ttl_seconds: 300
Result: url="https://app.vividscripts.com/m/jR8k2x...", expires_at="2026-05-24T16:42:03Z"

> Claude: Your video is ready.
>   Project ID: Knocking_Inside
>   Editor: https://app.vividscripts.com/m/jR8k2x...
```

Click that link. The browser opens directly into the editor at `/studio?project=Knocking_Inside` — already signed in, with the project loaded. The magic-link token is single-use (clicking it again returns "link expired or already used"), hard-capped at 5 minutes, and scrubbed from the URL bar after the editor loads.

In the editor you can:

- Preview the compiled video.
- Swap an image (`update_scene_prompt` + `regenerate_scene_image` from Claude, or just edit in the UI — both write to the same on-disk representation).
- Adjust narration text (`update_scene_text` + `regenerate_scene_audio`).
- Download the MP4 (`get_video_download_url` mints a short-lived signed URL).

Every edit you make in the editor is reflected back in Claude Code on the next `get_scenes` call, and vice-versa.

## Going further

- **Custom prompts.** `set_custom_prompt_override("title_generator", "<template>")` replaces the default template for your account. Useful if your style guide is specific.
- **Resume mid-flight.** If Claude Code disconnects, `get_workflow_state(project_id)` returns enough state to resume. The pipeline is idempotent at the step boundary.
- **Multiple projects.** `list_projects` returns everything you've made; `get_project(project_id)` returns full detail.

## Reference

- [`docs/tools.md`](../docs/tools.md) — every Tool and Prompt with parameters and example calls.
- [`docs/architecture.md`](../docs/architecture.md) — the two-layer split and the three sequence diagrams (auth, workflow, magic-link).
- [`docs/auth.md`](../docs/auth.md) — full OAuth 2.1 flow with `curl` walkthrough.
- [`docs/magic-link.md`](../docs/magic-link.md) — magic-link token format, replay protection, rotation playbook.

## Troubleshooting

- **"Connection refused" in `/mcp`.** Make sure the URL is `https://app.vividscripts.com/mcp` (HTTPS, not HTTP) and that your Claude Code build supports remote MCP servers (`/mcp` should list `vividscripts` even before authorization).
- **Browser doesn't open during authorization.** Your terminal may have lost the loopback callback. Try `/mcp` again, or open the printed URL by hand.
- **"Link expired or already used".** Magic-links are single-use and TTL ≤ 5 min. Ask Claude to mint a fresh one (`mint_magic_link` is idempotent on your project).
- **A media job is stuck on `running`.** `check_job` returns `error` on failure and `result` on success; if it stays on `running` past the typical duration above, the backend may be saturated. Retry the specific `generate_*` tool; jobs are isolated, so retrying one doesn't restart the pipeline.
