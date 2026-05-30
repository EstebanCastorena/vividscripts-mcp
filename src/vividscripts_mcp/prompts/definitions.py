"""The :class:`PromptInterface` declarations (KAN-56).

Each entry describes one MCP Prompt: name, agent-role description,
JSON-Schema for the context dict the backend will format the body with,
a reference to the output schema (file in ``schemas/``) that
``save_step_result`` validates against, the loop unit (``story`` /
``paragraph`` / ``scene`` / ``segment`` / ``None``), the prior prompts
whose outputs must already exist, and (for documentation-only prompts)
an optional inline ``body`` rendered verbatim instead of going through
the backend.

Source-of-truth maps to ``slide_editor/workflow/prompts/__init__.py``
for 17 in-pipeline + 1 inline (short_title_generator) + 2 out-of-pipeline
(``ai_helper.optimize_story``, ``ai_helper.edit_image_prompt``).

These descriptions intentionally stay at the "what does this agent do"
level — the templates themselves never appear in this repo.

Documentation prompts (KAN-127): a small number of prompts ship public
*how-to* bodies rather than backend-rendered templates. They expose the
same MCP Prompt surface but skip the ``backend.format_prompt`` call and
the trailing ``save_step_result`` instructions, since they are
self-contained runbooks Claude follows directly. ``resume_project`` is
the first one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

#: Allowed values for :attr:`PromptInterface.loops_over`. ``None`` means
#: the prompt runs exactly once per story (not in a loop).
LoopUnit = Literal["story", "paragraph", "scene", "segment"]


class PromptInterface(BaseModel):
    """Public contract for one MCP Prompt.

    Frozen + ``extra="forbid"``: the catalog is declarative state. Any
    drift between the interface and the upstream template should surface
    as a Pydantic validation error at import time, not a runtime
    surprise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema_ref: str
    loops_over: LoopUnit | None
    depends_on: tuple[str, ...]
    #: When set, the prompt body is rendered verbatim from this string
    #: instead of being fetched via ``backend.format_prompt``. Reserved
    #: for *documentation* prompts (KAN-127) — public runbooks Claude
    #: follows directly, with no creative-IP concern around the body.
    #: The trailing ``save_step_result`` reminder is also suppressed
    #: when this is set.
    body: str | None = None


def _schema(
    properties: dict[str, dict[str, Any]],
    required: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build an object JSON Schema (Draft 2020-12) for an input context.

    Defaults: every property is required unless ``required`` is provided,
    and ``additionalProperties`` is forbidden so unknown context fields
    surface as validation errors.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(required) if required is not None else list(properties.keys()),
        "additionalProperties": False,
    }


def _str(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _int(description: str, minimum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "description": description}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


PROMPT_INTERFACES: dict[str, PromptInterface] = {
    "story_blueprint": PromptInterface(
        name="story_blueprint",
        description=(
            "Creative-director pass that produces a structural blueprint for a "
            "story before it enters the video pipeline. Identifies genre, tone, "
            "narrator visibility, narrative structure, character archetypes, "
            "pacing signature, and the visual creative direction every "
            "downstream prompt will rely on. Runs once per story."
        ),
        input_schema=_schema(
            {
                "story": _str("Full story text the user submitted."),
                "numbered_story": _str("Story with paragraph numbers prepended."),
                "paragraph_count": _int("Number of paragraphs in the story.", minimum=1),
            }
        ),
        output_schema_ref="story_blueprint.json",
        loops_over=None,
        depends_on=(),
    ),
    "narration_grouping": PromptInterface(
        name="narration_grouping",
        description=(
            "Storyboarder that splits one paragraph of narration into a sequence "
            "of visual scenes. The output drives how many images and how many "
            "audio segments the rest of the pipeline produces for that paragraph. "
            "Runs once per paragraph."
        ),
        input_schema=_schema({"paragraph": _str("One paragraph of narration text.")}),
        output_schema_ref="narration_grouping.json",
        loops_over="paragraph",
        depends_on=("story_blueprint",),
    ),
    "story_summarizer": PromptInterface(
        name="story_summarizer",
        description=(
            "Editor that distills the story into a hook brief used downstream "
            "by the title generator and thumbnail prompts. Captures what the "
            "story is actually about in a form optimized for titling and "
            "thumbnail composition. Runs once per story."
        ),
        input_schema=_schema({"story": _str("Full story text.")}),
        output_schema_ref="story_summarizer.json",
        loops_over=None,
        depends_on=("narration_grouping",),
    ),
    "title_generator": PromptInterface(
        name="title_generator",
        description=(
            "YouTube-title writer that takes the hook brief and the story's "
            "visual style anchors and produces a click-worthy title optimized "
            "for the platform. Runs once per story."
        ),
        input_schema=_schema(
            {
                "hook_brief": _str("Hook summary from story_summarizer."),
                "style_anchors": _str("Visual style anchors derived from the blueprint."),
            }
        ),
        output_schema_ref="title_generator.json",
        loops_over=None,
        depends_on=("story_summarizer",),
    ),
    "short_title_generator": PromptInterface(
        name="short_title_generator",
        description=(
            "Naming utility that takes the full title and produces a 2-3 word "
            "filename-safe project name used for folder naming and the final "
            "video file. Runs once per story."
        ),
        input_schema=_schema({"title": _str("Full YouTube title.")}),
        output_schema_ref="short_title_generator.json",
        loops_over=None,
        depends_on=("title_generator",),
    ),
    "stage_direction_bible": PromptInterface(
        name="stage_direction_bible",
        description=(
            "Reference builder that produces a durable story-wide visual "
            "continuity guide (characters, settings, lighting, motifs) for "
            "every subsequent scene-level prompt to draw on. Runs once per "
            "story."
        ),
        input_schema=_schema({"story": _str("Full story text.")}),
        output_schema_ref="stage_direction_bible.json",
        loops_over=None,
        depends_on=("story_blueprint", "narration_grouping"),
    ),
    "stage_direction_first": PromptInterface(
        name="stage_direction_first",
        description=(
            "Strict context builder that establishes scene 1's visual state — "
            "setting, lighting, characters in frame — using the story bible as "
            "the baseline. Output becomes the seed that stage_direction_subsequent "
            "refines for each later scene."
        ),
        input_schema=_schema(
            {
                "current_context": _str("Baseline context from the story bible."),
                "narration": _str("Narration text for scene 1."),
                "story_bibles": _str("Full story bibles (characters + locations)."),
            }
        ),
        output_schema_ref="stage_direction_first.json",
        loops_over=None,
        depends_on=("stage_direction_bible",),
    ),
    "stage_direction_subsequent": PromptInterface(
        name="stage_direction_subsequent",
        description=(
            "Strict context refiner that takes the prior scene's stage "
            "direction and updates only what changes for the new scene "
            "(lighting shifts, character entrances/exits, setting "
            "transitions). Preserves continuity directives. Loops over scenes "
            "2 through N."
        ),
        input_schema=_schema(
            {
                "current_context": _str("Prior scene's stage direction (the refining baseline)."),
                "narration": _str("Narration text for the new scene."),
                "story_bibles": _str("Full story bibles (characters + locations)."),
            }
        ),
        output_schema_ref="stage_direction_subsequent.json",
        loops_over="scene",
        depends_on=("stage_direction_first",),
    ),
    "image_split_analyzer": PromptInterface(
        name="image_split_analyzer",
        description=(
            "Pacing analyzer that decides whether a scene needs one image or "
            "multiple to capture distinct action beats. Driven by narration "
            "length, character action, scene duration, and the tone of the "
            "moment. Loops over every scene."
        ),
        input_schema=_schema(
            {
                "characters": _str("Characters in frame for this scene."),
                "duration_context": _str("Audio duration / pacing hint."),
                "max_images": _int("Cap on images per scene.", minimum=1),
                "narration": _str("Narration text for the scene."),
                "setting": _str("Scene setting."),
                "tone": _str("Scene tone."),
            }
        ),
        output_schema_ref="image_split_analyzer.json",
        loops_over="scene",
        depends_on=("stage_direction_first",),
    ),
    "image_director_first": PromptInterface(
        name="image_director_first",
        description=(
            "Visual director that translates the first image of the first "
            "scene into an image-generation instruction. Encodes character "
            "identity packs, lighting motif, narrator-visibility rules, and "
            "the visual direction from the stage direction. Runs once for "
            "scene 1; image_director_subsequent handles the rest."
        ),
        input_schema=_schema(
            {
                "art_style": _str("Art style anchor."),
                "characters": _str("Characters in frame."),
                "identity_packs": _str("Per-character identity packs."),
                "key_elements": _str("Key visual elements for the scene."),
                "lighting": _str("Lighting state at this moment."),
                "lighting_motif": _str("Overall lighting motif from the bible."),
                "narration": _str("Narration text for the scene."),
                "narrator_rule": _str("Narrator visibility rule (in-frame vs out-of-frame)."),
                "setting": _str("Scene setting."),
                "setting_now": _str("Setting at this exact moment."),
                "tone": _str("Scene tone."),
                "visual_direction": _str("Visual direction inherited from the blueprint."),
            }
        ),
        output_schema_ref="image_director_first.json",
        loops_over=None,
        depends_on=("image_split_analyzer", "stage_direction_first"),
    ),
    "image_director_subsequent": PromptInterface(
        name="image_director_subsequent",
        description=(
            "Visual director writing continuation images for scenes 2..N. "
            "Takes the prior scene's image instruction and updates only what "
            "changes (character pose, camera shift, lighting evolution) while "
            "honoring continuity directives. Loops over the later scenes."
        ),
        input_schema=_schema(
            {
                "art_style": _str("Art style anchor."),
                "characters": _str("Characters in frame."),
                "continuity_directives": _str("LOCK/PREF directives from the bible."),
                "identity_packs": _str("Per-character identity packs."),
                "key_elements": _str("Key visual elements for the scene."),
                "lighting": _str("Lighting state at this moment."),
                "lighting_motif": _str("Overall lighting motif."),
                "narration": _str("Narration for the scene."),
                "narrator_rule": _str("Narrator visibility rule."),
                "previous_instructions": _str("Prior image instruction (continuation seed)."),
                "setting": _str("Scene setting."),
                "setting_now": _str("Setting at this exact moment."),
                "tone": _str("Scene tone."),
                "visual_direction": _str("Visual direction inherited from the blueprint."),
            }
        ),
        output_schema_ref="image_director_subsequent.json",
        loops_over="scene",
        depends_on=("image_director_first", "stage_direction_subsequent"),
    ),
    "image_director_followup": PromptInterface(
        name="image_director_followup",
        description=(
            "Visual director writing a follow-up image inside a multi-image "
            "scene. Takes the scene's first-image instruction plus the visual "
            "beat for the new image and updates only what changes. Invoked "
            "from two slide_editor code paths (image_director.py and "
            "image_generator.py) — the input schema covers both call sites."
        ),
        input_schema=_schema(
            {
                "continuity_directives": _str("LOCK/PREF directives."),
                "first_image_instruction": _str("Scene's first image instruction."),
                "identity_packs": _str("Per-character identity packs."),
                "image_index": _int("Position in the multi-image sequence (1-based).", minimum=1),
                "lighting": _str("Lighting state."),
                "setting": _str("Scene setting."),
                "text_portion": _str("Portion of narration this follow-up covers."),
                "tone": _str("Scene tone."),
                "total_images": _int("Total images in this scene.", minimum=2),
                "visual_beat": _str("Visual beat the follow-up captures."),
                "visual_direction": _str("Visual direction inherited from the blueprint."),
            }
        ),
        output_schema_ref="image_director_followup.json",
        loops_over="segment",
        depends_on=("image_director_first",),
    ),
    "sound_effect_category": PromptInterface(
        name="sound_effect_category",
        description=(
            "Sound designer pass 1: selects sound-effect categories "
            "appropriate for the scene's mood, setting, and visual action. "
            "Output is a category list consumed by sound_effect_analyzer in "
            "pass 2. Loops over every scene."
        ),
        input_schema=_schema(
            {
                "blueprint_context": _str("Excerpt of the story blueprint relevant to SFX."),
                "categories": _str("Catalog of available SFX categories."),
                "narration": _str("Narration text for the scene."),
                "setting": _str("Scene setting."),
                "tone": _str("Scene tone."),
            }
        ),
        output_schema_ref="sound_effect_category.json",
        loops_over="scene",
        depends_on=("narration_grouping", "stage_direction_bible"),
    ),
    "sound_effect_analyzer": PromptInterface(
        name="sound_effect_analyzer",
        description=(
            "Sound designer pass 2: takes the available category catalog and "
            "the word-level audio timestamps and emits concrete sound-effect "
            "placements with timing and volume per effect. Loops over every "
            "scene."
        ),
        input_schema=_schema(
            {
                "available_effects": _str("Catalog of effects in the chosen categories."),
                "blueprint_context": _str("Excerpt of the story blueprint relevant to SFX."),
                "narration": _str("Narration text for the scene."),
                "word_timestamps": _str("Word-level audio timestamps for placement."),
            }
        ),
        output_schema_ref="sound_effect_analyzer.json",
        loops_over="scene",
        depends_on=("sound_effect_category",),
    ),
    "thumbnail": PromptInterface(
        name="thumbnail",
        description=(
            "Thumbnail art director that produces an image-generation prompt "
            "for one eye-catching YouTube thumbnail. Pulls from the title, "
            "story summary, story bibles, and visual genre direction. Runs "
            "once per story."
        ),
        input_schema=_schema(
            {
                "art_style": _str("Art style anchor."),
                "genre_direction": _str("Genre-specific visual direction."),
                "story_bibles": _str("Story bibles (characters + locations)."),
                "story_summary": _str("Hook brief from story_summarizer."),
                "title": _str("Full YouTube title."),
            }
        ),
        output_schema_ref="thumbnail.json",
        loops_over=None,
        depends_on=("title_generator", "story_summarizer", "stage_direction_bible"),
    ),
    "thumbnail_text": PromptInterface(
        name="thumbnail_text",
        description=(
            "Copywriter producing a short curiosity-building text overlay for "
            "the thumbnail image. 1-5 words; complements the title without "
            "repeating its words. Runs once per story."
        ),
        input_schema=_schema(
            {
                "story_summary": _str("Hook brief from story_summarizer."),
                "thumbnail_description": _str("Description of the chosen thumbnail image."),
                "title": _str("Full YouTube title."),
            }
        ),
        output_schema_ref="thumbnail_text.json",
        loops_over=None,
        depends_on=("title_generator", "thumbnail"),
    ),
    "thumbnail_format_selector": PromptInterface(
        name="thumbnail_format_selector",
        description=(
            "Thumbnail strategist that picks the best composition format "
            "(face close-up, before/after split, environmental wide, etc.) "
            "from a 15-format catalog based on the story's genre, hook, "
            "character archetypes, and pacing signature. Runs once per story. "
            "Added to the pipeline in slide_editor commit 8ae047d (post-PRD)."
        ),
        input_schema=_schema(
            {
                "character_archetypes": _str("Character archetypes from the blueprint."),
                "climax_paragraph": _str("Identified climax paragraph."),
                "creative_direction": _str("Creative direction summary."),
                "format_catalog": _str("The 15-format catalog to pick from."),
                "genre": _str("Story genre."),
                "hook_brief": _str("Hook brief from story_summarizer."),
                "narrative_structure": _str("Narrative structure identified by the blueprint."),
                "pacing_signature": _str("Pacing signature."),
                "title": _str("Full YouTube title."),
                "tone": _str("Overall tone."),
                "viewer_emotion_arc": _str("Target emotional arc for the viewer."),
            }
        ),
        output_schema_ref="thumbnail_format_selector.json",
        loops_over=None,
        depends_on=("story_blueprint", "title_generator"),
    ),
    # motion_direction removed 2026-05-25 (Test 2 post-mortem): it writes
    # camera/motion prompts for the Kling animation step, which is no longer
    # part of the default MCP routine because of its cost. The corresponding
    # ``animate_scene`` MCP tool is also de-registered (see tools/media.py).
    # The schema + fixture were deleted with this change. To re-enable the
    # Kling path, restore this PromptInterface, restore the fixture and
    # output schema, and re-register ``make_animate_scene_tool`` in
    # ``tools/media.py::register_media_tools``.
    "story_optimization": PromptInterface(
        name="story_optimization",
        description=(
            "Editor that takes a user-supplied story plus optional custom "
            "instructions and produces an optimized version better suited to "
            "the video pipeline (clearer scene boundaries, stronger imagery, "
            "better pacing). User-initiated from the Story Enhancement tab; "
            "not part of the main workflow."
        ),
        input_schema=_schema(
            {
                "story": _str("Original story text."),
                "custom_instructions": _str(
                    "Optional additional instructions; empty string when none."
                ),
            }
        ),
        output_schema_ref="story_optimization.json",
        loops_over=None,
        depends_on=(),
    ),
    "image_prompt_edit": PromptInterface(
        name="image_prompt_edit",
        description=(
            "Image-prompt editor that takes an existing image-generation "
            "prompt and a user's edit suggestion and produces a revised "
            "prompt that incorporates the change. User-initiated from the "
            "AI Edit tab; not part of the main workflow."
        ),
        input_schema=_schema(
            {
                "current_prompt": _str("Existing image-generation prompt to edit."),
                "edit_suggestion": _str("What the user wants to change."),
            }
        ),
        output_schema_ref="image_prompt_edit.json",
        loops_over=None,
        depends_on=(),
    ),
    # ---- documentation prompts (KAN-127) ------------------------------
    # Public runbooks for operational gotchas. The body is rendered
    # verbatim — no backend.format_prompt round-trip, no save_step_result
    # suffix. Schema + fixture exist to keep catalog-alignment tests
    # happy; in practice nothing calls ``save_step_result`` for these.
    "resume_project": PromptInterface(
        name="resume_project",
        description=(
            "Operational runbook for picking up a story-to-video pipeline "
            "after the MCP session that started it died (transport drop, "
            "token TTL expiry, Claude Code restart). The server-side "
            "workflow state survives — this prompt walks you through "
            "rediscovering the project_id, reading the surviving state, "
            "and resuming from the next un-completed media step. "
            "Documentation-only: produces no data; does not call "
            "save_step_result. See KAN-127."
        ),
        input_schema=_schema(
            {
                "context_hint": _str(
                    "Optional free-text hint about what was being attempted "
                    "in the dead session (e.g. story title, last tool "
                    "called). Empty string when none."
                ),
            },
            required=(),
        ),
        output_schema_ref="resume_project.json",
        loops_over=None,
        depends_on=(),
        body=(
            "# Resume a project after a dropped MCP session\n"
            "\n"
            "Your previous Claude Code session driving the VividScripts "
            "pipeline died mid-flight (transport drop, token expiry, "
            "Claude Code restart, etc.). The server-side workflow state "
            "is persisted per-project in `mcp_workflow_state.json` — "
            "nothing was lost. Follow these steps to pick up where the "
            "previous session left off.\n"
            "\n"
            "## Step 1 — Find the orphaned project\n"
            "\n"
            "Call `list_projects()`. The response includes every project "
            "you own with its `project_id`, `project_name`, `status` "
            "(`draft` / `running` / `compiled` / `failed`), and "
            "`scene_count`. The orphaned project is almost always the "
            "most recent one in `running` status.\n"
            "\n"
            "If the user gave you a hint (story title, last tool, etc.) "
            "use it to disambiguate. If multiple `running` projects "
            "look plausible, ask the user which one before continuing.\n"
            "\n"
            "## Step 2 — Read the surviving workflow state\n"
            "\n"
            "Call `get_workflow_state(project_id)`. The response gives "
            "you `completed_steps` (the steps that finished before the "
            "drop) and `current_step` (the next step the workflow would "
            "advance to). Treat `current_step` as the resume point.\n"
            "\n"
            "## Step 3 — Identify the next media step\n"
            "\n"
            "The media steps in order are:\n"
            "\n"
            "1. `generate_audio` — TTS narration for every scene.\n"
            "2. `generate_images` — image generation for every scene.\n"
            "3. `generate_sfx` — sound-effect synthesis "
            "(requires `generate_audio` complete).\n"
            "4. `generate_music` — background music; pass the chosen "
            "`mood` (e.g. `generate_music(project_id, mood)`).\n"
            "5. `generate_thumbnail` — YouTube thumbnail.\n"
            "6. `compile_video` — final FFmpeg assembly.\n"
            "\n"
            "Pick the first one whose corresponding step does not yet "
            "appear in `completed_steps`. Call `list_workflow_steps()` "
            "if you need the canonical step catalog and dependency "
            "graph.\n"
            "\n"
            "## Step 4 — Resume by invoking the matching tool\n"
            "\n"
            "Call the tool from step 3 with `project_id` as the only "
            "argument. It returns a `JobSubmission` (`job_id`, "
            "`job_type`) immediately. Surface that to the user as one "
            "line: `Resumed: <job_type> job started: <job_id>`.\n"
            "\n"
            "## Step 5 — Poll until terminal\n"
            "\n"
            "Call `check_job(job_id)` until `status` is `completed` or "
            "`failed`. On `completed`, repeat steps 3-5 for the next "
            "media step. On `failed`, surface `JobStatus.error` to the "
            "user and stop — do not auto-retry.\n"
            "\n"
            "Once `compile_video` reports `completed`, the project is "
            "finished. Call `mint_magic_link(project_id)` to hand the "
            "user a one-click URL into the editor.\n"
            "\n"
            "## What this prompt is *not*\n"
            "\n"
            "This is a documentation runbook. There is no "
            "`save_step_result` call associated with `resume_project` — "
            "it produces no AI step output. The schema file exists only "
            "to keep the catalog-alignment tests honest.\n"
        ),
    ),
}
