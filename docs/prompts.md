# Prompts

VividScripts' AI work is expressed as **MCP Prompts** — parameterized templates the server exposes over the Model Context Protocol. In Claude Code each one appears as a `/slash-command`; Claude Code also invokes them programmatically via `prompts/get`. Claude does the reasoning; the server supplies the prompt and validates the result.

This page documents the **interface** of every prompt — what it does, what context it needs, and what shape its output must take. The prompt **bodies** are served by the production backend at request time and are not part of this package; the schemas here are the contract that matters for integration.

## How a prompt round-trip works

1. Call `prompts/get(<name>, context)`. The context is validated against the prompt's input schema before anything is rendered.
2. The server returns the rendered prompt with the **output schema embedded** plus a reminder to call `save_step_result`.
3. Produce the structured result and call `save_step_result(project_id, <name>, result)`. The result is validated against the same output schema before it is persisted; validation errors come back with field-level paths.

## Custom overrides

Any prompt can be replaced with your own template:

- `set_custom_prompt_override(step_name, template)` stores it (per-user; rejected if `step_name` isn't one of the prompts below).
- `get_custom_prompt_override(step_name)` reads it back.
- Once set, `prompts/get` serves your template instead of the default.

**Gotcha:** a custom template must supply every placeholder the default uses. If a future default adds a new placeholder, overrides that omit it will fail at render time rather than silently degrade — re-check your overrides after an upgrade.

## Shared templates

`image_director_followup` is invoked from two code paths in the production pipeline (the image director and the image generator). Its input schema covers both call sites; treat it as one interface with two callers.

## The prompts

Grouped pipeline-first (in dependency order), then the two user-initiated prompts that sit outside the linear workflow.

### `story_blueprint`

Creative-director pass that produces a structural blueprint for a story before it enters the video pipeline. Identifies genre, tone, narrator visibility, narrative structure, character archetypes, pacing signature, and the visual creative direction every downstream prompt will rely on. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** nothing (entry point)
- **Output schema:** [`schemas/story_blueprint.json`](../src/vividscripts_mcp/schemas/story_blueprint.json) — required: `genre`, `tone`, `narrative_structure`, `creative_direction`, `paragraph_analyses`

| Context field | Type | Required | Description |
|---|---|---|---|
| `story` | string | yes | Full story text the user submitted. |
| `numbered_story` | string | yes | Story with paragraph numbers prepended. |
| `paragraph_count` | integer | yes | Number of paragraphs in the story. |

### `narration_grouping`

Storyboarder that splits one paragraph of narration into a sequence of visual scenes. The output drives how many images and how many audio segments the rest of the pipeline produces for that paragraph. Runs once per paragraph.

- **Cadence:** Once per paragraph (loops).
- **Depends on:** `story_blueprint`
- **Output schema:** [`schemas/narration_grouping.json`](../src/vividscripts_mcp/schemas/narration_grouping.json) — required: `scenes`

| Context field | Type | Required | Description |
|---|---|---|---|
| `paragraph` | string | yes | One paragraph of narration text. |

### `story_summarizer`

Editor that distills the story into a hook brief used downstream by the title generator and thumbnail prompts. Captures what the story is actually about in a form optimized for titling and thumbnail composition. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** `narration_grouping`
- **Output schema:** [`schemas/story_summarizer.json`](../src/vividscripts_mcp/schemas/story_summarizer.json) — required: `short_summary`

| Context field | Type | Required | Description |
|---|---|---|---|
| `story` | string | yes | Full story text. |

### `title_generator`

YouTube-title writer that takes the hook brief and the story's visual style anchors and produces a click-worthy title optimized for the platform. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** `story_summarizer`
- **Output schema:** [`schemas/title_generator.json`](../src/vividscripts_mcp/schemas/title_generator.json) — required: `title`

| Context field | Type | Required | Description |
|---|---|---|---|
| `hook_brief` | string | yes | Hook summary from story_summarizer. |
| `style_anchors` | string | yes | Visual style anchors derived from the blueprint. |

### `short_title_generator`

Naming utility that takes the full title and produces a 2-3 word filename-safe project name used for folder naming and the final video file. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** `title_generator`
- **Output schema:** [`schemas/short_title_generator.json`](../src/vividscripts_mcp/schemas/short_title_generator.json) — required: `short_title`

| Context field | Type | Required | Description |
|---|---|---|---|
| `title` | string | yes | Full YouTube title. |

### `stage_direction_bible`

Reference builder that produces a durable story-wide visual continuity guide (characters, settings, lighting, motifs) for every subsequent scene-level prompt to draw on. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** `story_blueprint`, `narration_grouping`
- **Output schema:** [`schemas/stage_direction_bible.json`](../src/vividscripts_mcp/schemas/stage_direction_bible.json) — required: `reference_guide`

| Context field | Type | Required | Description |
|---|---|---|---|
| `story` | string | yes | Full story text. |

### `stage_direction_first`

Strict context builder that establishes scene 1's visual state — setting, lighting, characters in frame — using the story bible as the baseline. Output becomes the seed that stage_direction_subsequent refines for each later scene.

- **Cadence:** Once per story.
- **Depends on:** `stage_direction_bible`
- **Output schema:** [`schemas/stage_direction_first.json`](../src/vividscripts_mcp/schemas/stage_direction_first.json) — required: `context`

| Context field | Type | Required | Description |
|---|---|---|---|
| `current_context` | string | yes | Baseline context from the story bible. |
| `narration` | string | yes | Narration text for scene 1. |
| `story_bibles` | string | yes | Full story bibles (characters + locations). |

### `stage_direction_subsequent`

Strict context refiner that takes the prior scene's stage direction and updates only what changes for the new scene (lighting shifts, character entrances/exits, setting transitions). Preserves continuity directives. Loops over scenes 2 through N.

- **Cadence:** Once per scene (loops).
- **Depends on:** `stage_direction_first`
- **Output schema:** [`schemas/stage_direction_subsequent.json`](../src/vividscripts_mcp/schemas/stage_direction_subsequent.json) — required: `context`

| Context field | Type | Required | Description |
|---|---|---|---|
| `current_context` | string | yes | Prior scene's stage direction (the refining baseline). |
| `narration` | string | yes | Narration text for the new scene. |
| `story_bibles` | string | yes | Full story bibles (characters + locations). |

### `image_split_analyzer`

Pacing analyzer that decides whether a scene needs one image or multiple to capture distinct action beats. Driven by narration length, character action, scene duration, and the tone of the moment. Loops over every scene.

- **Cadence:** Once per scene (loops).
- **Depends on:** `stage_direction_first`
- **Output schema:** [`schemas/image_split_analyzer.json`](../src/vividscripts_mcp/schemas/image_split_analyzer.json) — required: `image_count`

| Context field | Type | Required | Description |
|---|---|---|---|
| `characters` | string | yes | Characters in frame for this scene. |
| `duration_context` | string | yes | Audio duration / pacing hint. |
| `max_images` | integer | yes | Cap on images per scene. |
| `narration` | string | yes | Narration text for the scene. |
| `setting` | string | yes | Scene setting. |
| `tone` | string | yes | Scene tone. |

### `image_director_first`

Visual director that translates the first image of the first scene into an image-generation instruction. Encodes character identity packs, lighting motif, narrator-visibility rules, and the visual direction from the stage direction. Runs once for scene 1; image_director_subsequent handles the rest.

- **Cadence:** Once per story.
- **Depends on:** `image_split_analyzer`, `stage_direction_first`
- **Output schema:** [`schemas/image_director_first.json`](../src/vividscripts_mcp/schemas/image_director_first.json) — required: `image_instruction`

| Context field | Type | Required | Description |
|---|---|---|---|
| `art_style` | string | yes | Art style anchor. |
| `characters` | string | yes | Characters in frame. |
| `identity_packs` | string | yes | Per-character identity packs. |
| `key_elements` | string | yes | Key visual elements for the scene. |
| `lighting` | string | yes | Lighting state at this moment. |
| `lighting_motif` | string | yes | Overall lighting motif from the bible. |
| `narration` | string | yes | Narration text for the scene. |
| `narrator_rule` | string | yes | Narrator visibility rule (in-frame vs out-of-frame). |
| `setting` | string | yes | Scene setting. |
| `setting_now` | string | yes | Setting at this exact moment. |
| `tone` | string | yes | Scene tone. |
| `visual_direction` | string | yes | Visual direction inherited from the blueprint. |

### `image_director_subsequent`

Visual director writing continuation images for scenes 2..N. Takes the prior scene's image instruction and updates only what changes (character pose, camera shift, lighting evolution) while honoring continuity directives. Loops over the later scenes.

- **Cadence:** Once per scene (loops).
- **Depends on:** `image_director_first`, `stage_direction_subsequent`
- **Output schema:** [`schemas/image_director_subsequent.json`](../src/vividscripts_mcp/schemas/image_director_subsequent.json) — required: `image_instruction`

| Context field | Type | Required | Description |
|---|---|---|---|
| `art_style` | string | yes | Art style anchor. |
| `characters` | string | yes | Characters in frame. |
| `continuity_directives` | string | yes | LOCK/PREF directives from the bible. |
| `identity_packs` | string | yes | Per-character identity packs. |
| `key_elements` | string | yes | Key visual elements for the scene. |
| `lighting` | string | yes | Lighting state at this moment. |
| `lighting_motif` | string | yes | Overall lighting motif. |
| `narration` | string | yes | Narration for the scene. |
| `narrator_rule` | string | yes | Narrator visibility rule. |
| `previous_instructions` | string | yes | Prior image instruction (continuation seed). |
| `setting` | string | yes | Scene setting. |
| `setting_now` | string | yes | Setting at this exact moment. |
| `tone` | string | yes | Scene tone. |
| `visual_direction` | string | yes | Visual direction inherited from the blueprint. |

### `image_director_followup`

Visual director writing a follow-up image inside a multi-image scene. Takes the scene's first-image instruction plus the visual beat for the new image and updates only what changes. Invoked from two slide_editor code paths (image_director.py and image_generator.py) — the input schema covers both call sites.

- **Cadence:** Once per image segment within a multi-image scene (loops).
- **Depends on:** `image_director_first`
- **Output schema:** [`schemas/image_director_followup.json`](../src/vividscripts_mcp/schemas/image_director_followup.json) — required: `image_instruction`

| Context field | Type | Required | Description |
|---|---|---|---|
| `continuity_directives` | string | yes | LOCK/PREF directives. |
| `first_image_instruction` | string | yes | Scene's first image instruction. |
| `identity_packs` | string | yes | Per-character identity packs. |
| `image_index` | integer | yes | Position in the multi-image sequence (1-based). |
| `lighting` | string | yes | Lighting state. |
| `setting` | string | yes | Scene setting. |
| `text_portion` | string | yes | Portion of narration this follow-up covers. |
| `tone` | string | yes | Scene tone. |
| `total_images` | integer | yes | Total images in this scene. |
| `visual_beat` | string | yes | Visual beat the follow-up captures. |
| `visual_direction` | string | yes | Visual direction inherited from the blueprint. |

### `sound_effect_category`

Sound designer pass 1: selects sound-effect categories appropriate for the scene's mood, setting, and visual action. Output is a category list consumed by sound_effect_analyzer in pass 2. Loops over every scene.

- **Cadence:** Once per scene (loops).
- **Depends on:** `narration_grouping`, `stage_direction_bible`
- **Output schema:** [`schemas/sound_effect_category.json`](../src/vividscripts_mcp/schemas/sound_effect_category.json) — required: `selected_categories`

| Context field | Type | Required | Description |
|---|---|---|---|
| `blueprint_context` | string | yes | Excerpt of the story blueprint relevant to SFX. |
| `categories` | string | yes | Catalog of available SFX categories. |
| `narration` | string | yes | Narration text for the scene. |
| `setting` | string | yes | Scene setting. |
| `tone` | string | yes | Scene tone. |

### `sound_effect_analyzer`

Sound designer pass 2: takes the available category catalog and the word-level audio timestamps and emits concrete sound-effect placements with timing and volume per effect. Loops over every scene.

- **Cadence:** Once per scene (loops).
- **Depends on:** `sound_effect_category`
- **Output schema:** [`schemas/sound_effect_analyzer.json`](../src/vividscripts_mcp/schemas/sound_effect_analyzer.json) — required: `effects`

| Context field | Type | Required | Description |
|---|---|---|---|
| `available_effects` | string | yes | Catalog of effects in the chosen categories. |
| `blueprint_context` | string | yes | Excerpt of the story blueprint relevant to SFX. |
| `narration` | string | yes | Narration text for the scene. |
| `word_timestamps` | string | yes | Word-level audio timestamps for placement. |

### `thumbnail`

Thumbnail art director that produces an image-generation prompt for one eye-catching YouTube thumbnail. Pulls from the title, story summary, story bibles, and visual genre direction. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** `title_generator`, `story_summarizer`, `stage_direction_bible`
- **Output schema:** [`schemas/thumbnail.json`](../src/vividscripts_mcp/schemas/thumbnail.json) — required: `image_prompt`

| Context field | Type | Required | Description |
|---|---|---|---|
| `art_style` | string | yes | Art style anchor. |
| `genre_direction` | string | yes | Genre-specific visual direction. |
| `story_bibles` | string | yes | Story bibles (characters + locations). |
| `story_summary` | string | yes | Hook brief from story_summarizer. |
| `title` | string | yes | Full YouTube title. |

### `thumbnail_text`

Copywriter producing a short curiosity-building text overlay for the thumbnail image. 1-5 words; complements the title without repeating its words. Runs once per story.

- **Cadence:** Once per story.
- **Depends on:** `title_generator`, `thumbnail`
- **Output schema:** [`schemas/thumbnail_text.json`](../src/vividscripts_mcp/schemas/thumbnail_text.json) — required: `text`

| Context field | Type | Required | Description |
|---|---|---|---|
| `story_summary` | string | yes | Hook brief from story_summarizer. |
| `thumbnail_description` | string | yes | Description of the chosen thumbnail image. |
| `title` | string | yes | Full YouTube title. |

### `thumbnail_format_selector`

Thumbnail strategist that picks the best composition format (face close-up, before/after split, environmental wide, etc.) from a 15-format catalog based on the story's genre, hook, character archetypes, and pacing signature. Runs once per story. Added to the pipeline in slide_editor commit 8ae047d (post-PRD).

- **Cadence:** Once per story.
- **Depends on:** `story_blueprint`, `title_generator`
- **Output schema:** [`schemas/thumbnail_format_selector.json`](../src/vividscripts_mcp/schemas/thumbnail_format_selector.json) — required: `format_id`

| Context field | Type | Required | Description |
|---|---|---|---|
| `character_archetypes` | string | yes | Character archetypes from the blueprint. |
| `climax_paragraph` | string | yes | Identified climax paragraph. |
| `creative_direction` | string | yes | Creative direction summary. |
| `format_catalog` | string | yes | The 15-format catalog to pick from. |
| `genre` | string | yes | Story genre. |
| `hook_brief` | string | yes | Hook brief from story_summarizer. |
| `narrative_structure` | string | yes | Narrative structure identified by the blueprint. |
| `pacing_signature` | string | yes | Pacing signature. |
| `title` | string | yes | Full YouTube title. |
| `tone` | string | yes | Overall tone. |
| `viewer_emotion_arc` | string | yes | Target emotional arc for the viewer. |

### `motion_direction`

Animation director writing concise camera/motion instructions for a Kling-style video model that will animate a still image from the storyboard. Takes the visual subject, action, tone, and shot type. Loops over each scene that gets animated.

- **Cadence:** Once per scene (loops).
- **Depends on:** `image_director_first`
- **Output schema:** [`schemas/motion_direction.json`](../src/vividscripts_mcp/schemas/motion_direction.json) — required: `motion_prompt`

| Context field | Type | Required | Description |
|---|---|---|---|
| `narration` | string | yes | Narration text for the scene. |
| `setting` | string | yes | Scene setting. |
| `shot_type` | string | yes | Shot type (close-up, wide, etc.). |
| `tone` | string | yes | Scene tone. |
| `visual_action` | string | yes | Visual action described in the image. |
| `visual_subject` | string | yes | Visual subject of the image. |

### `story_optimization` *(user-initiated, outside the main pipeline)*

Editor that takes a user-supplied story plus optional custom instructions and produces an optimized version better suited to the video pipeline (clearer scene boundaries, stronger imagery, better pacing). User-initiated from the Story Enhancement tab; not part of the main workflow.

- **Cadence:** Once per story.
- **Depends on:** nothing (entry point)
- **Output schema:** [`schemas/story_optimization.json`](../src/vividscripts_mcp/schemas/story_optimization.json) — required: `optimized_story`

| Context field | Type | Required | Description |
|---|---|---|---|
| `story` | string | yes | Original story text. |
| `custom_instructions` | string | yes | Optional additional instructions; empty string when none. |

### `image_prompt_edit` *(user-initiated, outside the main pipeline)*

Image-prompt editor that takes an existing image-generation prompt and a user's edit suggestion and produces a revised prompt that incorporates the change. User-initiated from the AI Edit tab; not part of the main workflow.

- **Cadence:** Once per story.
- **Depends on:** nothing (entry point)
- **Output schema:** [`schemas/image_prompt_edit.json`](../src/vividscripts_mcp/schemas/image_prompt_edit.json) — required: `edited_prompt`

| Context field | Type | Required | Description |
|---|---|---|---|
| `current_prompt` | string | yes | Existing image-generation prompt to edit. |
| `edit_suggestion` | string | yes | What the user wants to change. |

---

See [`docs/architecture.md`](architecture.md) for how prompts fit the two-layer split and [`docs/auth.md`](auth.md) for how a client authenticates before it can call any of this.
