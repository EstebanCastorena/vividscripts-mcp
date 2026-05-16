"""Per-scene step-result storage semantics (KAN-90).

One source of truth for how a step result lands in ``current_data``,
shared by ``MockBackend`` and the real slide_editor adapter so the
single-vs-per-scene contract cannot diverge between them.

- ``scene_index is None`` → single-valued step:
  ``current_data[step] = result`` (unchanged pre-KAN-90 behavior).
- ``scene_index >= 0`` → per-scene/looped step: results accumulate at
  ``current_data[step]`` as ``{str(scene_index): result, ...}``.

A step must be used one way consistently; mixing returns an error
string (callers surface it as a failed ``StepResultOutcome``). A
per-scene bucket is recognized as a non-empty dict whose keys are all
digit strings (step-result schemas never have all-numeric top-level
keys), so the two shapes are unambiguous on disk.
"""

from __future__ import annotations

from typing import Any


def _is_per_scene_bucket(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and len(value) > 0
        and all(isinstance(k, str) and k.isdigit() for k in value)
    )


def store_step_result(
    current_data: dict[str, Any],
    step_name: str,
    result: dict[str, Any],
    scene_index: int | None,
) -> str | None:
    """Apply a step result to ``current_data`` in place.

    Returns ``None`` on success, or an error message if the step is
    being used in a mode inconsistent with how it was first stored.
    """
    existing = current_data.get(step_name)

    if scene_index is None:
        if _is_per_scene_bucket(existing):
            return (
                f"step {step_name!r} already has per-scene results; "
                f"cannot also save it single-valued"
            )
        current_data[step_name] = result
        return None

    if scene_index < 0:
        return "scene_index must be >= 0"
    if existing is not None and not (existing == {} or _is_per_scene_bucket(existing)):
        return f"step {step_name!r} already saved single-valued; cannot also save it per-scene"
    bucket = current_data.setdefault(step_name, {})
    bucket[str(scene_index)] = result
    return None
