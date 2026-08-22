"""Named presets for stargazing scoring: strict / balanced / relaxed.

"Balanced" is score.py's existing default PlateauEdges/FalloffSpans --
the values arrived at through this project's actual design discussion.
"Strict" and "relaxed" are DERIVED from balanced by systematic scaling,
not independently hand-tuned -- none of these three presets have been
validated against real nights yet, so hand-picking 27 x 3 = 81 "looks
about right" numbers would be false precision. Scaling from a single
reasoned baseline is honest about that and easy to re-tune later (adjust
the scale factors, not 81 individual numbers) once real-world data
exists to tune against.

Weights are NOT varied per preset -- presets represent "how strict are
you about conditions", not "what do you care about". Weight tuning
stays a separate, per-user concern (already exposed via ScoreWeights,
editable in the eventual options flow).
"""

from __future__ import annotations

from dataclasses import asdict, fields, replace

from .const import PRESET_BALANCED, PRESET_RELAXED, PRESET_STRICT
from .score import FalloffSpans, PlateauEdges, ScoreWeights

# PlateauEdges fields where the edge is a FLOOR (higher raw value = better,
# e.g. more dew point spread, more visibility). Everything else in
# PlateauEdges is a CEILING (lower raw value = better, e.g. less cloud).
# Direction matters because "stricter" means opposite scaling directions
# for a ceiling vs. a floor -- see _scale_edges().
_HIGHER_IS_BETTER_FIELDS = frozenset({"dew_point_spread_min", "visibility_min"})

# How far strict/relaxed edges move from balanced. Ceilings shrink for
# strict (smaller "perfect" zone) and grow for relaxed; floors do the
# opposite (need more of a good thing to count as perfect vs. less).
_STRICT_CEILING_SCALE = 0.6
_STRICT_FLOOR_SCALE = 1.3
_RELAXED_CEILING_SCALE = 1.6
_RELAXED_FLOOR_SCALE = 0.7

# How far strict/relaxed falloff spans move from balanced. Spans are
# always a positive distance regardless of direction, so this scaling
# doesn't need the ceiling/floor distinction edges do: strict = steeper
# (smaller span, less forgiving), relaxed = gentler (larger span).
_STRICT_SPAN_SCALE = 0.6
_RELAXED_SPAN_SCALE = 1.6


def _scale_edges(edges: PlateauEdges, ceiling_scale: float, floor_scale: float) -> PlateauEdges:
    changes = {}
    for f in fields(edges):
        value = getattr(edges, f.name)
        scale = floor_scale if f.name in _HIGHER_IS_BETTER_FIELDS else ceiling_scale
        changes[f.name] = round(value * scale, 2)
    return replace(edges, **changes)


def _scale_spans(spans: FalloffSpans, scale: float) -> FalloffSpans:
    changes = {f.name: round(getattr(spans, f.name) * scale, 2) for f in fields(spans)}
    return replace(spans, **changes)


_BALANCED_EDGES = PlateauEdges()
_BALANCED_SPANS = FalloffSpans()
_SHARED_WEIGHTS = ScoreWeights()

PRESET_DEFINITIONS: dict[str, tuple[PlateauEdges, FalloffSpans, ScoreWeights]] = {
    PRESET_STRICT: (
        _scale_edges(_BALANCED_EDGES, _STRICT_CEILING_SCALE, _STRICT_FLOOR_SCALE),
        _scale_spans(_BALANCED_SPANS, _STRICT_SPAN_SCALE),
        _SHARED_WEIGHTS,
    ),
    PRESET_BALANCED: (_BALANCED_EDGES, _BALANCED_SPANS, _SHARED_WEIGHTS),
    PRESET_RELAXED: (
        _scale_edges(_BALANCED_EDGES, _RELAXED_CEILING_SCALE, _RELAXED_FLOOR_SCALE),
        _scale_spans(_BALANCED_SPANS, _RELAXED_SPAN_SCALE),
        _SHARED_WEIGHTS,
    ),
}


def get_preset_values(preset_name: str) -> dict:
    """Resolve a preset name into a JSON-serializable dict, suitable for
    storing directly in a config entry's `data`.

    Structure: {"edges": {...}, "spans": {...}, "weights": {...}} --
    matches what config_entry_to_score_config() expects back.
    """
    if preset_name not in PRESET_DEFINITIONS:
        raise ValueError(
            f"Unknown preset {preset_name!r}; must be one of {list(PRESET_DEFINITIONS)}"
        )
    edges, spans, weights = PRESET_DEFINITIONS[preset_name]
    return {
        "edges": asdict(edges),
        "spans": asdict(spans),
        "weights": asdict(weights),
    }


def config_entry_to_score_config(
    data: dict,
) -> tuple[PlateauEdges, FalloffSpans, ScoreWeights]:
    """Reverse of get_preset_values() -- reconstructs the three score.py
    dataclasses from a config entry's stored data dict. Missing keys/fields
    fall back to defaults (fail-soft per README principle)."""
    # Helper: merge stored dict with dataclass defaults
    def merge_with_defaults(stored: dict | None, defaults: dict) -> dict:
        if not stored:
            return defaults
        # Only keep known fields, merge with defaults
        known_stored = {k: v for k, v in stored.items() if k in defaults}
        return {**defaults, **known_stored}

    edges_defaults = {f.name: f.default for f in fields(PlateauEdges)}
    spans_defaults = {f.name: f.default for f in fields(FalloffSpans)}
    weights_defaults = {f.name: f.default for f in fields(ScoreWeights)}

    edges_data = merge_with_defaults(data.get("edges"), edges_defaults)
    spans_data = merge_with_defaults(data.get("spans"), spans_defaults)
    weights_data = merge_with_defaults(data.get("weights"), weights_defaults)

    return (
        PlateauEdges(**edges_data),
        FalloffSpans(**spans_data),
        ScoreWeights(**weights_data),
    )


def overlay_score_config(base: dict | None, overlay: dict | None) -> dict:
    """Merge an options-layer score_config over a data-layer one.

    Both layers use the same {"edges"/"spans"/"weights"} shape produced by
    get_preset_values(). The options layer wins per-key, so the options
    wizard can persist just the sections it touched while unchanged keys
    keep falling through to whatever the original config flow stored.
    Unknown keys are deliberately passed through here --
    config_entry_to_score_config() already filters those against the
    dataclass fields, keeping this function shape-agnostic.
    """
    merged: dict = {}
    for section in ("edges", "spans", "weights"):
        combined = {
            **((base or {}).get(section) or {}),
            **((overlay or {}).get(section) or {}),
        }
        if combined:
            merged[section] = combined
    return merged