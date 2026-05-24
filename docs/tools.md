# Tools, Prompts, and Resources

Full catalog of what `vividscripts-mcp` exposes over the wire — every Tool, every Prompt, and what the Resources surface looks like today.

Two notes before diving in:

- **Parameter tables are auto-generated** from the FastMCP tool registry and the `PROMPT_INTERFACES` declaration. The blocks marked with `<!-- gen-tools:start ... -->` are produced by [`scripts/gen_tools_docs.py`](../scripts/gen_tools_docs.py). The doc itself ships as a static Markdown file; run the script to refresh the tables when a tool signature changes.
- **Examples** are JSON-RPC payloads as a fully-conforming MCP client would send them. The `arguments` field of `tools/call` is the tool's input schema; the `arguments` field of `prompts/get` is the prompt's input schema.

## Overview

| Surface | Count | Notes |
|---|---|---|
| Tools | **27** | All user-scoped via Bearer token; `project_id`/`scene_index` validated at the protocol boundary |
| Prompts | **20** | 18 in the main pipeline + 2 user-initiated (`story_optimization`, `image_prompt_edit`) |
| Resources | 0 (planned) | URI scheme reserved; see [Resources](#resources) below |

`list_workflow_steps` describes the 16-step pipeline as one tool call; the 20 Prompts are the AI consultation points within it.

---

## Tools

### Project lifecycle

#### `create_project`

Creates a new VividScripts project from a story plus settings. Returns the project id and an editor URL to open it. The story is bound at 200,000 chars (a long novel chapter); the settings model is validated against `ProjectSettings` so unknown keys are rejected.

<!-- gen-tools:start name=create_project -->
| Param | Type | Required | Description |
|---|---|---|---|
| `story` | `string` | yes | — |
| `settings` | `ProjectSettings` | yes | — |
<!-- gen-tools:end -->

`ProjectSettings`: `style` (default `"vintage_illustrated"`), `voice` (`"male"` \| `"female"`), `dimension` (`"landscape"` \| `"portrait"`), `music_mood` (optional override).

```json
{
  "method": "tools/call",
  "params": {
    "name": "create_project",
    "arguments": {
      "story": "She knocked on her own door at 3 a.m. ...",
      "settings": { "style": "vintage_illustrated", "voice": "female", "dimension": "landscape" }
    }
  }
}
```

#### `list_projects`

Returns the caller's projects — one `ProjectSummary` per row (id, name, status, scene count, created_at, editor_url, optional video_url). Cross-tenant isolation is enforced at the backend; another user's projects are never visible.

<!-- gen-tools:start name=list_projects -->
_No parameters._
<!-- gen-tools:end -->

```json
{ "method": "tools/call", "params": { "name": "list_projects", "arguments": {} } }
```

#### `get_project`

Returns full detail for one project owned by the caller (metadata, scene summaries, video status, blueprint summary, editor URL). A project that doesn't exist *for this user* returns the same 404-shaped error as a project that doesn't exist at all — so probing doesn't reveal other users' projects.

<!-- gen-tools:start name=get_project -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "get_project", "arguments": { "project_id": "Knocking_Inside" } } }
```

---

### Workflow state

#### `list_workflow_steps`

Lists the 16 workflow steps with metadata (name, description, `ai_required`, `depends_on`, `loops_over`). Same answer for every user — the catalog is part of the pipeline definition. Use this to drive the workflow without hard-coding the step list.

<!-- gen-tools:start name=list_workflow_steps -->
_No parameters._
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "list_workflow_steps", "arguments": {} } }
```

#### `save_step_result`

Persists an AI step result for a project. Validates the result against the step's JSON Schema *before* the backend is touched — a mismatch returns `success=False` with field-level paths and persists nothing. For looped steps (per-scene image prompts, etc.), pass `scene_index` to accumulate results; for single-valued steps, omit it.

<!-- gen-tools:start name=save_step_result -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `step_name` | `string` | yes | — |
| `result` | `object` | yes | — |
| `scene_index` | `integer | null` | no | — |
<!-- gen-tools:end -->

```json
{
  "method": "tools/call",
  "params": {
    "name": "save_step_result",
    "arguments": {
      "project_id": "Knocking_Inside",
      "step_name": "story_blueprint",
      "result": {
        "genre": "psychological_horror",
        "tone": "uneasy",
        "narrative_structure": "three_act",
        "creative_direction": "moody, dim interiors, single-light-source compositions",
        "paragraph_analyses": [ { "index": 0, "summary": "The knock.", "characters": ["protagonist"] } ]
      }
    }
  }
}
```

#### `get_workflow_state`

Returns the project's current pipeline position — `status`, `completed_steps`, `current_step`, accumulated `current_data` (blueprint, scenes, bibles, etc.). Enough state to resume a workflow mid-flight if Claude Code is restarted.

<!-- gen-tools:start name=get_workflow_state -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "get_workflow_state", "arguments": { "project_id": "Knocking_Inside" } } }
```

---

### Custom prompt overrides

#### `get_custom_prompt_override`

Returns the caller's custom template for `step_name`, if one is set. The response is `{ has_override: bool, template: string | null }`. Use this to inspect what override (if any) will be applied before the next `prompts/get` round-trip.

<!-- gen-tools:start name=get_custom_prompt_override -->
| Param | Type | Required | Description |
|---|---|---|---|
| `step_name` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "get_custom_prompt_override", "arguments": { "step_name": "title_generator" } } }
```

#### `set_custom_prompt_override`

Stores a per-user custom template for one of the 20 known prompts. Rejects unknown step names — a custom override for a prompt that doesn't exist could never be served and would only accumulate. Templates are bound at 50,000 chars (the longest shipped default is ~3k).

<!-- gen-tools:start name=set_custom_prompt_override -->
| Param | Type | Required | Description |
|---|---|---|---|
| `step_name` | `string` | yes | — |
| `template` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{
  "method": "tools/call",
  "params": {
    "name": "set_custom_prompt_override",
    "arguments": {
      "step_name": "title_generator",
      "template": "You are a YouTube title writer. Always include a number..."
    }
  }
}
```

---

### Media generation (async jobs)

Every `generate_*` tool returns a `JobSubmission` (`job_id`, `job_type`) immediately and runs the work in the backend's process. Poll `check_job(job_id)` until `status` is `completed` or `failed`. None of these tools accept job-shaping parameters yet — they read everything they need from the project's current state.

#### `generate_audio`

Starts TTS narration generation for every scene in the project. Requires scenes to already exist (run the narration step first). On `completed`, every scene has `audio_url` populated and word-level timestamps stored for SFX placement.

<!-- gen-tools:start name=generate_audio -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "generate_audio", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `generate_images`

Starts image generation for every scene that has an image direction. Multi-image scenes (decided upstream by `image_split_analyzer`) get one image per beat. On `completed`, every scene has `image_url` populated; the backend handles the per-provider routing (Replicate / BFL / FAL / Grok).

<!-- gen-tools:start name=generate_images -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "generate_images", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `generate_sfx`

Runs sound-effect synthesis for the project — the backend picks categories per scene, looks up samples, and renders the final SFX layer over word-level timestamps. Requires `generate_audio` to have completed (the timestamps drive placement).

<!-- gen-tools:start name=generate_sfx -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "generate_sfx", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `generate_thumbnail`

Renders the YouTube thumbnail image from the prompt produced by the `thumbnail` MCP Prompt. One artifact; the `thumbnail_text` overlay is composed by the editor at view time.

<!-- gen-tools:start name=generate_thumbnail -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "generate_thumbnail", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `animate_scene`

Image-to-video animation for the project's intro scenes — Kling-style motion driven by the `motion_direction` prompt's output. Requires images to already be generated; multi-image scenes animate beat-by-beat.

<!-- gen-tools:start name=animate_scene -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "animate_scene", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `generate_music`

Synthesizes a background-music track for the project's chosen mood. Requires `select_music` to have recorded the mood first; if the catalog already has a track for the mood, this is a no-op and `check_job` reports immediately complete.

<!-- gen-tools:start name=generate_music -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "generate_music", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `compile_video`

Final FFmpeg pass: stitches scenes + audio + SFX + music into the final MP4. Requires every scene to have an image and audio, plus the short title and thumbnail. On `completed`, `JobStatus.result` carries `video_path`; use `get_video_download_url` for a signed download URL.

<!-- gen-tools:start name=compile_video -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "compile_video", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `select_music`

Synchronous catalog lookup (not a job): records the chosen `mood` on the project and reports which catalog tracks already exist for it. If `needs_generation` is true, follow up with `generate_music` to synthesize tracks for the mood.

<!-- gen-tools:start name=select_music -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `mood` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "select_music",
              "arguments": { "project_id": "Knocking_Inside", "mood": "uneasy_strings" } } }
```

#### `regenerate_scene_image`

Re-renders one scene's image from its current prompt. Use this after `update_scene_prompt` to materialize the edit, or to retry a scene whose first render didn't land. Async; only the target scene is regenerated, siblings are untouched.

<!-- gen-tools:start name=regenerate_scene_image -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `scene_index` | `integer` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "regenerate_scene_image",
              "arguments": { "project_id": "Knocking_Inside", "scene_index": 4 } } }
```

#### `regenerate_scene_audio`

Re-synthesizes one scene's narration audio and word-level timestamps from its current text. Use after `update_scene_text`. SFX placement stays correct on the next `compile_video` because the regenerated timestamps replace the old ones.

<!-- gen-tools:start name=regenerate_scene_audio -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `scene_index` | `integer` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "regenerate_scene_audio",
              "arguments": { "project_id": "Knocking_Inside", "scene_index": 4 } } }
```

#### `check_job`

Polls an async media job. Returns `{ job_id, job_type, status, progress, result?, error? }`. `status` is one of `queued | running | completed | failed`; `progress` is 0.0–1.0. On `completed`, read `result`; on `failed`, read `error`.

<!-- gen-tools:start name=check_job -->
| Param | Type | Required | Description |
|---|---|---|---|
| `job_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "check_job", "arguments": { "job_id": "job_8af2e1" } } }
```

---

### URL handoff

#### `mint_magic_link`

Mints a short-lived signed URL that opens the project in the editor (`view="editor"`) or the player (`view="video"`). Single-use, HS256-signed, hard-capped at 5 minutes. Present to the user to click promptly; never store. The full token threat-model is documented in [`docs/magic-link.md`](magic-link.md).

<!-- gen-tools:start name=mint_magic_link -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `view` | `string` | no | — |
| `ttl_seconds` | `integer` | no | — |
<!-- gen-tools:end -->

`view` must be one of `"editor" | "video"`; `ttl_seconds` is bounded `[1, 300]`.

```json
{ "method": "tools/call",
  "params": { "name": "mint_magic_link",
              "arguments": { "project_id": "Knocking_Inside",
                             "view": "editor",
                             "ttl_seconds": 300 } } }
```

#### `get_video_download_url`

Returns a short-lived signed URL to the compiled video. Requires the project to have been compiled (`compile_video` reached `completed`). The URL expires fast (≤ 5 min) — fetch promptly.

<!-- gen-tools:start name=get_video_download_url -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "get_video_download_url",
              "arguments": { "project_id": "Knocking_Inside" } } }
```

---

### Scene editing (bidirectional with the web editor)

Every mutation goes through the backend onto the same on-disk scene representation the editor reads, so an edit made by Claude Code shows up in the editor on refresh and vice-versa.

#### `get_scenes`

Lists every scene in the project — `index`, `text`, `image_url`, `audio_url`, `image_prompt`, `visual_subject`, `duration_seconds`. Reflects whatever the editor last wrote.

<!-- gen-tools:start name=get_scenes -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "get_scenes", "arguments": { "project_id": "Knocking_Inside" } } }
```

#### `get_scene`

Returns one scene's full data by 0-based index.

<!-- gen-tools:start name=get_scene -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `scene_index` | `integer` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "get_scene",
              "arguments": { "project_id": "Knocking_Inside", "scene_index": 4 } } }
```

#### `update_scene_prompt`

Replaces a scene's image prompt. Visible in the editor on the next refresh; run `regenerate_scene_image` to materialize the edit as a re-rendered image.

<!-- gen-tools:start name=update_scene_prompt -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `scene_index` | `integer` | yes | — |
| `new_prompt` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "update_scene_prompt",
              "arguments": { "project_id": "Knocking_Inside",
                             "scene_index": 4,
                             "new_prompt": "low-angle dim hallway, single sconce ..." } } }
```

#### `update_scene_text`

Replaces a scene's narration text. Run `regenerate_scene_audio` to re-synthesize the audio + word timestamps; SFX placement stays consistent on the next compile.

<!-- gen-tools:start name=update_scene_text -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `scene_index` | `integer` | yes | — |
| `new_text` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "update_scene_text",
              "arguments": { "project_id": "Knocking_Inside",
                             "scene_index": 4,
                             "new_text": "She felt the floorboards remember her weight." } } }
```

#### `add_scene`

Inserts a new scene after `after_index` (0-based) with the given narration text. Downstream scenes are re-indexed; the new scene's index is returned. Image and audio for the new scene are not auto-generated — call `regenerate_scene_*` after.

<!-- gen-tools:start name=add_scene -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `after_index` | `integer` | yes | — |
| `text` | `string` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "add_scene",
              "arguments": { "project_id": "Knocking_Inside",
                             "after_index": 4,
                             "text": "A heartbeat passed. Then another." } } }
```

#### `remove_scene`

Deletes a scene by 0-based index. Downstream scenes are re-indexed. Refuses to remove the last remaining scene — a project must always have at least one.

<!-- gen-tools:start name=remove_scene -->
| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | `string` | yes | — |
| `scene_index` | `integer` | yes | — |
<!-- gen-tools:end -->

```json
{ "method": "tools/call",
  "params": { "name": "remove_scene",
              "arguments": { "project_id": "Knocking_Inside", "scene_index": 7 } } }
```

---

## Prompts

The 20 AI consultation points in the VividScripts pipeline are MCP Prompts. In Claude Code each one surfaces as a `/slash-command`; Claude Code also calls them programmatically via `prompts/get`. Bodies live in the production backend (creative IP); the public package declares only the interfaces. Output schemas are in [`src/vividscripts_mcp/schemas/`](../src/vividscripts_mcp/schemas/).

The round-trip is:

1. `prompts/get(<name>, arguments=<context>)` — the server validates the context against the input schema, asks the backend to render the body, and returns the rendered prompt with the **output schema embedded** plus a reminder to call `save_step_result`.
2. Claude produces a structured result against that embedded schema.
3. `tools/call("save_step_result", { project_id, step_name: <name>, result })` — the server validates the result against the same output schema before persistence.

A full doc with cadence, dependencies, and prose for every prompt lives in [`docs/prompts.md`](prompts.md). The tables below are the contract.

### `story_blueprint`

Creative-director pass that produces a structural blueprint for a story — genre, tone, narrator visibility, narrative structure, character archetypes, pacing signature. Runs once per story; everything downstream depends on it.

- **Cadence:** Once per story. No dependencies.
- **Output schema:** [`story_blueprint.json`](../src/vividscripts_mcp/schemas/story_blueprint.json) — required: `genre`, `tone`, `narrative_structure`, `creative_direction`, `paragraph_analyses`.

<!-- gen-tools:start name=prompt_story_blueprint -->
| Param | Type | Required | Description |
|---|---|---|---|
| `story` | `string` | yes | Full story text the user submitted. |
| `numbered_story` | `string` | yes | Story with paragraph numbers prepended. |
| `paragraph_count` | `integer` | yes | Number of paragraphs in the story. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "story_blueprint",
              "arguments": { "story": "...", "numbered_story": "1. ...", "paragraph_count": 12 } } }
```

### `narration_grouping`

Storyboarder that splits one paragraph of narration into a sequence of visual scenes. Drives downstream image and audio counts. Loops over every paragraph.

- **Cadence:** Once per paragraph. Depends on `story_blueprint`.
- **Output schema:** [`narration_grouping.json`](../src/vividscripts_mcp/schemas/narration_grouping.json) — required: `scenes`.

<!-- gen-tools:start name=prompt_narration_grouping -->
| Param | Type | Required | Description |
|---|---|---|---|
| `paragraph` | `string` | yes | One paragraph of narration text. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "narration_grouping",
              "arguments": { "paragraph": "She knocked. The door breathed back." } } }
```

### `story_summarizer`

Editor that distills the story into a hook brief used downstream by the title generator and thumbnail prompts. Captures what the story is actually *about* in a form optimized for titling.

- **Cadence:** Once per story. Depends on `narration_grouping`.
- **Output schema:** [`story_summarizer.json`](../src/vividscripts_mcp/schemas/story_summarizer.json) — required: `short_summary`.

<!-- gen-tools:start name=prompt_story_summarizer -->
| Param | Type | Required | Description |
|---|---|---|---|
| `story` | `string` | yes | Full story text. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "story_summarizer", "arguments": { "story": "..." } } }
```

### `title_generator`

YouTube-title writer that takes the hook brief and the story's visual style anchors and produces a click-worthy title. Runs once per story.

- **Cadence:** Once per story. Depends on `story_summarizer`.
- **Output schema:** [`title_generator.json`](../src/vividscripts_mcp/schemas/title_generator.json) — required: `title`.

<!-- gen-tools:start name=prompt_title_generator -->
| Param | Type | Required | Description |
|---|---|---|---|
| `hook_brief` | `string` | yes | Hook summary from story_summarizer. |
| `style_anchors` | `string` | yes | Visual style anchors derived from the blueprint. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "title_generator",
              "arguments": { "hook_brief": "...", "style_anchors": "vintage_illustrated, moody" } } }
```

### `short_title_generator`

Naming utility that takes the full title and produces a 2–3 word filename-safe project name used for folder naming and the final video file.

- **Cadence:** Once per story. Depends on `title_generator`.
- **Output schema:** [`short_title_generator.json`](../src/vividscripts_mcp/schemas/short_title_generator.json) — required: `short_title`.

<!-- gen-tools:start name=prompt_short_title_generator -->
| Param | Type | Required | Description |
|---|---|---|---|
| `title` | `string` | yes | Full YouTube title. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "short_title_generator",
              "arguments": { "title": "She knocked on her own door at 3 a.m." } } }
```

### `stage_direction_bible`

Reference builder that produces a story-wide visual continuity guide (characters, settings, lighting, motifs) for every later scene-level prompt to draw on.

- **Cadence:** Once per story. Depends on `story_blueprint`, `narration_grouping`.
- **Output schema:** [`stage_direction_bible.json`](../src/vividscripts_mcp/schemas/stage_direction_bible.json) — required: `reference_guide`.

<!-- gen-tools:start name=prompt_stage_direction_bible -->
| Param | Type | Required | Description |
|---|---|---|---|
| `story` | `string` | yes | Full story text. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "stage_direction_bible", "arguments": { "story": "..." } } }
```

### `stage_direction_first`

Strict context builder that establishes scene 1's visual state using the bible as the baseline. Output seeds `stage_direction_subsequent` for every later scene.

- **Cadence:** Once per story. Depends on `stage_direction_bible`.
- **Output schema:** [`stage_direction_first.json`](../src/vividscripts_mcp/schemas/stage_direction_first.json) — required: `context`.

<!-- gen-tools:start name=prompt_stage_direction_first -->
| Param | Type | Required | Description |
|---|---|---|---|
| `current_context` | `string` | yes | Baseline context from the story bible. |
| `narration` | `string` | yes | Narration text for scene 1. |
| `story_bibles` | `string` | yes | Full story bibles (characters + locations). |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "stage_direction_first",
              "arguments": { "current_context": "...", "narration": "...", "story_bibles": "..." } } }
```

### `stage_direction_subsequent`

Refiner that takes the prior scene's stage direction and updates only what changes for the new scene. Preserves continuity directives. Loops over scenes 2 through N.

- **Cadence:** Once per scene. Depends on `stage_direction_first`.
- **Output schema:** [`stage_direction_subsequent.json`](../src/vividscripts_mcp/schemas/stage_direction_subsequent.json) — required: `context`.

<!-- gen-tools:start name=prompt_stage_direction_subsequent -->
| Param | Type | Required | Description |
|---|---|---|---|
| `current_context` | `string` | yes | Prior scene's stage direction (the refining baseline). |
| `narration` | `string` | yes | Narration text for the new scene. |
| `story_bibles` | `string` | yes | Full story bibles (characters + locations). |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "stage_direction_subsequent",
              "arguments": { "current_context": "...", "narration": "...", "story_bibles": "..." } } }
```

### `image_split_analyzer`

Pacing analyzer that decides whether a scene needs one image or multiple to capture distinct action beats. Loops over every scene.

- **Cadence:** Once per scene. Depends on `stage_direction_first`.
- **Output schema:** [`image_split_analyzer.json`](../src/vividscripts_mcp/schemas/image_split_analyzer.json) — required: `image_count`.

<!-- gen-tools:start name=prompt_image_split_analyzer -->
| Param | Type | Required | Description |
|---|---|---|---|
| `characters` | `string` | yes | Characters in frame for this scene. |
| `duration_context` | `string` | yes | Audio duration / pacing hint. |
| `max_images` | `integer` | yes | Cap on images per scene. |
| `narration` | `string` | yes | Narration text for the scene. |
| `setting` | `string` | yes | Scene setting. |
| `tone` | `string` | yes | Scene tone. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "image_split_analyzer",
              "arguments": { "characters": "protagonist", "duration_context": "12s",
                             "max_images": 3, "narration": "...",
                             "setting": "front door, 3 a.m.", "tone": "uneasy" } } }
```

### `image_director_first`

Visual director that translates the first image of the first scene into an image-generation instruction. Encodes character identity packs, lighting motif, narrator-visibility rules, and the visual direction from the stage direction.

- **Cadence:** Once per story. Depends on `image_split_analyzer`, `stage_direction_first`.
- **Output schema:** [`image_director_first.json`](../src/vividscripts_mcp/schemas/image_director_first.json) — required: `image_instruction`.

<!-- gen-tools:start name=prompt_image_director_first -->
| Param | Type | Required | Description |
|---|---|---|---|
| `art_style` | `string` | yes | Art style anchor. |
| `characters` | `string` | yes | Characters in frame. |
| `identity_packs` | `string` | yes | Per-character identity packs. |
| `key_elements` | `string` | yes | Key visual elements for the scene. |
| `lighting` | `string` | yes | Lighting state at this moment. |
| `lighting_motif` | `string` | yes | Overall lighting motif from the bible. |
| `narration` | `string` | yes | Narration text for the scene. |
| `narrator_rule` | `string` | yes | Narrator visibility rule (in-frame vs out-of-frame). |
| `setting` | `string` | yes | Scene setting. |
| `setting_now` | `string` | yes | Setting at this exact moment. |
| `tone` | `string` | yes | Scene tone. |
| `visual_direction` | `string` | yes | Visual direction inherited from the blueprint. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "image_director_first",
              "arguments": { "art_style": "vintage_illustrated", "characters": "protagonist",
                             "identity_packs": "...", "key_elements": "door, knocker",
                             "lighting": "single sconce", "lighting_motif": "low-key amber",
                             "narration": "...", "narrator_rule": "out_of_frame",
                             "setting": "front porch", "setting_now": "rain just stopped",
                             "tone": "uneasy", "visual_direction": "..." } } }
```

### `image_director_subsequent`

Visual director writing continuation images for scenes 2..N. Takes the prior scene's image instruction and updates only what changes while honoring continuity directives.

- **Cadence:** Once per scene. Depends on `image_director_first`, `stage_direction_subsequent`.
- **Output schema:** [`image_director_subsequent.json`](../src/vividscripts_mcp/schemas/image_director_subsequent.json) — required: `image_instruction`.

<!-- gen-tools:start name=prompt_image_director_subsequent -->
| Param | Type | Required | Description |
|---|---|---|---|
| `art_style` | `string` | yes | Art style anchor. |
| `characters` | `string` | yes | Characters in frame. |
| `continuity_directives` | `string` | yes | LOCK/PREF directives from the bible. |
| `identity_packs` | `string` | yes | Per-character identity packs. |
| `key_elements` | `string` | yes | Key visual elements for the scene. |
| `lighting` | `string` | yes | Lighting state at this moment. |
| `lighting_motif` | `string` | yes | Overall lighting motif. |
| `narration` | `string` | yes | Narration for the scene. |
| `narrator_rule` | `string` | yes | Narrator visibility rule. |
| `previous_instructions` | `string` | yes | Prior image instruction (continuation seed). |
| `setting` | `string` | yes | Scene setting. |
| `setting_now` | `string` | yes | Setting at this exact moment. |
| `tone` | `string` | yes | Scene tone. |
| `visual_direction` | `string` | yes | Visual direction inherited from the blueprint. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "image_director_subsequent",
              "arguments": { "previous_instructions": "...", "art_style": "vintage_illustrated",
                             "characters": "...", "continuity_directives": "LOCK lighting",
                             "identity_packs": "...", "key_elements": "...",
                             "lighting": "amber sconce", "lighting_motif": "low-key amber",
                             "narration": "...", "narrator_rule": "out_of_frame",
                             "setting": "front porch", "setting_now": "...",
                             "tone": "uneasy", "visual_direction": "..." } } }
```

### `image_director_followup`

Writes a follow-up image inside a multi-image scene. Takes the scene's first-image instruction plus the visual beat for the new image and updates only what changes.

- **Cadence:** Once per image segment within a multi-image scene. Depends on `image_director_first`.
- **Output schema:** [`image_director_followup.json`](../src/vividscripts_mcp/schemas/image_director_followup.json) — required: `image_instruction`.

<!-- gen-tools:start name=prompt_image_director_followup -->
| Param | Type | Required | Description |
|---|---|---|---|
| `continuity_directives` | `string` | yes | LOCK/PREF directives. |
| `first_image_instruction` | `string` | yes | Scene's first image instruction. |
| `identity_packs` | `string` | yes | Per-character identity packs. |
| `image_index` | `integer` | yes | Position in the multi-image sequence (1-based). |
| `lighting` | `string` | yes | Lighting state. |
| `setting` | `string` | yes | Scene setting. |
| `text_portion` | `string` | yes | Portion of narration this follow-up covers. |
| `tone` | `string` | yes | Scene tone. |
| `total_images` | `integer` | yes | Total images in this scene. |
| `visual_beat` | `string` | yes | Visual beat the follow-up captures. |
| `visual_direction` | `string` | yes | Visual direction inherited from the blueprint. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "image_director_followup",
              "arguments": { "first_image_instruction": "...", "continuity_directives": "...",
                             "identity_packs": "...", "image_index": 2,
                             "lighting": "...", "setting": "...", "text_portion": "...",
                             "tone": "uneasy", "total_images": 3,
                             "visual_beat": "the door swings inward",
                             "visual_direction": "..." } } }
```

### `sound_effect_category`

Sound designer pass 1: selects sound-effect categories appropriate for the scene's mood, setting, and visual action. Output is a category list consumed by `sound_effect_analyzer` in pass 2.

- **Cadence:** Once per scene. Depends on `narration_grouping`, `stage_direction_bible`.
- **Output schema:** [`sound_effect_category.json`](../src/vividscripts_mcp/schemas/sound_effect_category.json) — required: `selected_categories`.

<!-- gen-tools:start name=prompt_sound_effect_category -->
| Param | Type | Required | Description |
|---|---|---|---|
| `blueprint_context` | `string` | yes | Excerpt of the story blueprint relevant to SFX. |
| `categories` | `string` | yes | Catalog of available SFX categories. |
| `narration` | `string` | yes | Narration text for the scene. |
| `setting` | `string` | yes | Scene setting. |
| `tone` | `string` | yes | Scene tone. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "sound_effect_category",
              "arguments": { "blueprint_context": "...", "categories": "...",
                             "narration": "...", "setting": "front porch",
                             "tone": "uneasy" } } }
```

### `sound_effect_analyzer`

Sound designer pass 2: takes the available category catalog and the word-level audio timestamps and emits concrete sound-effect placements with timing and volume per effect.

- **Cadence:** Once per scene. Depends on `sound_effect_category`.
- **Output schema:** [`sound_effect_analyzer.json`](../src/vividscripts_mcp/schemas/sound_effect_analyzer.json) — required: `effects`.

<!-- gen-tools:start name=prompt_sound_effect_analyzer -->
| Param | Type | Required | Description |
|---|---|---|---|
| `available_effects` | `string` | yes | Catalog of effects in the chosen categories. |
| `blueprint_context` | `string` | yes | Excerpt of the story blueprint relevant to SFX. |
| `narration` | `string` | yes | Narration text for the scene. |
| `word_timestamps` | `string` | yes | Word-level audio timestamps for placement. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "sound_effect_analyzer",
              "arguments": { "available_effects": "...", "blueprint_context": "...",
                             "narration": "...",
                             "word_timestamps": "[{\"word\":\"She\",\"t\":0.12}, ...]" } } }
```

### `thumbnail`

Thumbnail art director that produces an image-generation prompt for one eye-catching YouTube thumbnail.

- **Cadence:** Once per story. Depends on `title_generator`, `story_summarizer`, `stage_direction_bible`.
- **Output schema:** [`thumbnail.json`](../src/vividscripts_mcp/schemas/thumbnail.json) — required: `image_prompt`.

<!-- gen-tools:start name=prompt_thumbnail -->
| Param | Type | Required | Description |
|---|---|---|---|
| `art_style` | `string` | yes | Art style anchor. |
| `genre_direction` | `string` | yes | Genre-specific visual direction. |
| `story_bibles` | `string` | yes | Story bibles (characters + locations). |
| `story_summary` | `string` | yes | Hook brief from story_summarizer. |
| `title` | `string` | yes | Full YouTube title. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "thumbnail",
              "arguments": { "art_style": "vintage_illustrated",
                             "genre_direction": "psychological_horror",
                             "story_bibles": "...", "story_summary": "...",
                             "title": "She knocked on her own door at 3 a.m." } } }
```

### `thumbnail_text`

Copywriter producing a short curiosity-building text overlay for the thumbnail image. 1–5 words; complements the title without repeating its words.

- **Cadence:** Once per story. Depends on `title_generator`, `thumbnail`.
- **Output schema:** [`thumbnail_text.json`](../src/vividscripts_mcp/schemas/thumbnail_text.json) — required: `text`.

<!-- gen-tools:start name=prompt_thumbnail_text -->
| Param | Type | Required | Description |
|---|---|---|---|
| `story_summary` | `string` | yes | Hook brief from story_summarizer. |
| `thumbnail_description` | `string` | yes | Description of the chosen thumbnail image. |
| `title` | `string` | yes | Full YouTube title. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "thumbnail_text",
              "arguments": { "story_summary": "...", "thumbnail_description": "...",
                             "title": "She knocked on her own door at 3 a.m." } } }
```

### `thumbnail_format_selector`

Thumbnail strategist that picks the best composition format (face close-up, before/after split, environmental wide, etc.) from a 15-format catalog based on the story's genre, hook, character archetypes, and pacing signature.

- **Cadence:** Once per story. Depends on `story_blueprint`, `title_generator`.
- **Output schema:** [`thumbnail_format_selector.json`](../src/vividscripts_mcp/schemas/thumbnail_format_selector.json) — required: `format_id`.

<!-- gen-tools:start name=prompt_thumbnail_format_selector -->
| Param | Type | Required | Description |
|---|---|---|---|
| `character_archetypes` | `string` | yes | Character archetypes from the blueprint. |
| `climax_paragraph` | `string` | yes | Identified climax paragraph. |
| `creative_direction` | `string` | yes | Creative direction summary. |
| `format_catalog` | `string` | yes | The 15-format catalog to pick from. |
| `genre` | `string` | yes | Story genre. |
| `hook_brief` | `string` | yes | Hook brief from story_summarizer. |
| `narrative_structure` | `string` | yes | Narrative structure identified by the blueprint. |
| `pacing_signature` | `string` | yes | Pacing signature. |
| `title` | `string` | yes | Full YouTube title. |
| `tone` | `string` | yes | Overall tone. |
| `viewer_emotion_arc` | `string` | yes | Target emotional arc for the viewer. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "thumbnail_format_selector",
              "arguments": { "character_archetypes": "...", "climax_paragraph": "...",
                             "creative_direction": "...", "format_catalog": "...",
                             "genre": "psychological_horror", "hook_brief": "...",
                             "narrative_structure": "three_act",
                             "pacing_signature": "slow_burn_then_break",
                             "title": "...", "tone": "uneasy",
                             "viewer_emotion_arc": "curiosity → dread → release" } } }
```

### `motion_direction`

Animation director writing concise camera/motion instructions for a Kling-style video model that will animate a still image from the storyboard.

- **Cadence:** Once per animated scene. Depends on `image_director_first`.
- **Output schema:** [`motion_direction.json`](../src/vividscripts_mcp/schemas/motion_direction.json) — required: `motion_prompt`.

<!-- gen-tools:start name=prompt_motion_direction -->
| Param | Type | Required | Description |
|---|---|---|---|
| `narration` | `string` | yes | Narration text for the scene. |
| `setting` | `string` | yes | Scene setting. |
| `shot_type` | `string` | yes | Shot type (close-up, wide, etc.). |
| `tone` | `string` | yes | Scene tone. |
| `visual_action` | `string` | yes | Visual action described in the image. |
| `visual_subject` | `string` | yes | Visual subject of the image. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "motion_direction",
              "arguments": { "narration": "...", "setting": "front porch",
                             "shot_type": "medium close-up",
                             "tone": "uneasy", "visual_action": "raises a hand to the door",
                             "visual_subject": "protagonist" } } }
```

### `story_optimization` *(user-initiated)*

Editor that takes a user-supplied story plus optional custom instructions and produces an optimized version better suited to the video pipeline. User-initiated from the Story Enhancement tab; not part of the main workflow.

- **Cadence:** Once per story. No dependencies (entry point).
- **Output schema:** [`story_optimization.json`](../src/vividscripts_mcp/schemas/story_optimization.json) — required: `optimized_story`.

<!-- gen-tools:start name=prompt_story_optimization -->
| Param | Type | Required | Description |
|---|---|---|---|
| `story` | `string` | yes | Original story text. |
| `custom_instructions` | `string` | yes | Optional additional instructions; empty string when none. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "story_optimization",
              "arguments": { "story": "...", "custom_instructions": "tighten the pacing" } } }
```

### `image_prompt_edit` *(user-initiated)*

Image-prompt editor that takes an existing image-generation prompt and a user's edit suggestion and produces a revised prompt that incorporates the change. User-initiated from the AI Edit tab.

- **Cadence:** Once per story. No dependencies (entry point).
- **Output schema:** [`image_prompt_edit.json`](../src/vividscripts_mcp/schemas/image_prompt_edit.json) — required: `edited_prompt`.

<!-- gen-tools:start name=prompt_image_prompt_edit -->
| Param | Type | Required | Description |
|---|---|---|---|
| `current_prompt` | `string` | yes | Existing image-generation prompt to edit. |
| `edit_suggestion` | `string` | yes | What the user wants to change. |
<!-- gen-tools:end -->

```json
{ "method": "prompts/get",
  "params": { "name": "image_prompt_edit",
              "arguments": { "current_prompt": "moody hallway, single sconce",
                             "edit_suggestion": "make the lighting harsher" } } }
```

---

## Resources

The v1.0 surface does **not** expose MCP Resources. The URI scheme is reserved for a future minor release that will let Claude Code subscribe to live status — `vividscripts://jobs/{job_id}`, `vividscripts://projects/{id}/state`, etc. — instead of polling `check_job`.

Today, async media work uses the `job_id` + `check_job` polling pattern. That pattern is what v1.0 ships with; the Resources path is additive and won't change tool call shapes.

Reserved URI templates (planned):

| URI Template | Returns | Subscribable |
|---|---|---|
| `vividscripts://projects/` | List of user's projects | yes |
| `vividscripts://projects/{id}` | Full project detail | yes |
| `vividscripts://projects/{id}/state` | `WorkflowState` | yes |
| `vividscripts://projects/{id}/scenes` | Array of scenes | yes |
| `vividscripts://projects/{id}/scenes/{index}` | Single scene | yes |
| `vividscripts://projects/{id}/blueprint` | Story blueprint | no |
| `vividscripts://projects/{id}/bibles` | Character + location bibles | no |
| `vividscripts://projects/{id}/video` | Video status + download URL | yes |
| `vividscripts://workflow/steps` | The 16-step pipeline definition | no |
| `vividscripts://jobs/{job_id}` | Live job status | yes (primary use) |

When the Resources surface lands, this section will list each one with its return type and the equivalent Tool call (e.g. `tools/call get_workflow_state` becomes `resources/read vividscripts://projects/{id}/state` with `subscribe` support).

---

## Regenerating this catalog

```bash
python scripts/gen_tools_docs.py            # rewrite parameter blocks in place
python scripts/gen_tools_docs.py --check    # CI-friendly: exit 1 if stale
python scripts/gen_tools_docs.py --print-json  # dump the raw tool schemas
```

The script reads from the live FastMCP registry and from `PROMPT_INTERFACES`, so a tool signature change shows up the moment the source is edited.
