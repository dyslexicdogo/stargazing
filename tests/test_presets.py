"""Tests for presets.py. Pure Python, no hass needed."""

from dataclasses import asdict

import pytest

from custom_components.stargazing.const import (
    PRESET_BALANCED,
    PRESET_RELAXED,
    PRESET_STRICT,
)
from custom_components.stargazing.presets import (
    PRESET_DEFINITIONS,
    config_entry_to_score_config,
    get_preset_values,
)
from custom_components.stargazing.score import FalloffSpans, PlateauEdges, ScoreWeights


class TestBalancedMatchesScoreDefaults:
    def test_balanced_edges_equal_score_defaults(self):
        edges, _spans, _weights = PRESET_DEFINITIONS[PRESET_BALANCED]
        assert edges == PlateauEdges()

    def test_balanced_spans_equal_score_defaults(self):
        _edges, spans, _weights = PRESET_DEFINITIONS[PRESET_BALANCED]
        assert spans == FalloffSpans()


class TestStrictDirection:
    def test_strict_ceiling_fields_are_smaller_than_balanced(self):
        # ceiling fields: lower raw value = better, so a smaller ceiling
        # is a stricter (harder to hit 100) standard
        strict_edges, _, _ = PRESET_DEFINITIONS[PRESET_STRICT]
        balanced_edges = PlateauEdges()
        assert strict_edges.low_cloud_max < balanced_edges.low_cloud_max
        assert strict_edges.mid_cloud_max < balanced_edges.mid_cloud_max
        assert strict_edges.high_cloud_max < balanced_edges.high_cloud_max
        assert strict_edges.jet_stream_wind_max < balanced_edges.jet_stream_wind_max
        assert strict_edges.moon_illumination_max < balanced_edges.moon_illumination_max
        assert (
            strict_edges.precipitation_probability_max
            < balanced_edges.precipitation_probability_max
        )
        assert strict_edges.wind_speed_max < balanced_edges.wind_speed_max

    def test_strict_floor_fields_are_larger_than_balanced(self):
        # floor fields: higher raw value = better, so a LARGER floor is
        # the stricter standard (need more spread/visibility to be perfect)
        strict_edges, _, _ = PRESET_DEFINITIONS[PRESET_STRICT]
        balanced_edges = PlateauEdges()
        assert strict_edges.dew_point_spread_min > balanced_edges.dew_point_spread_min
        assert strict_edges.visibility_min > balanced_edges.visibility_min

    def test_strict_spans_are_narrower_than_balanced(self):
        strict_spans, _, _ = (PRESET_DEFINITIONS[PRESET_STRICT][1], None, None)
        balanced_spans = FalloffSpans()
        for field_name in balanced_spans.__dataclass_fields__:
            assert getattr(strict_spans, field_name) < getattr(balanced_spans, field_name)


class TestRelaxedDirection:
    def test_relaxed_ceiling_fields_are_larger_than_balanced(self):
        relaxed_edges, _, _ = PRESET_DEFINITIONS[PRESET_RELAXED]
        balanced_edges = PlateauEdges()
        assert relaxed_edges.low_cloud_max > balanced_edges.low_cloud_max
        assert relaxed_edges.mid_cloud_max > balanced_edges.mid_cloud_max
        assert relaxed_edges.high_cloud_max > balanced_edges.high_cloud_max

    def test_relaxed_floor_fields_are_smaller_than_balanced(self):
        relaxed_edges, _, _ = PRESET_DEFINITIONS[PRESET_RELAXED]
        balanced_edges = PlateauEdges()
        assert relaxed_edges.dew_point_spread_min < balanced_edges.dew_point_spread_min
        assert relaxed_edges.visibility_min < balanced_edges.visibility_min

    def test_relaxed_spans_are_wider_than_balanced(self):
        relaxed_spans = PRESET_DEFINITIONS[PRESET_RELAXED][1]
        balanced_spans = FalloffSpans()
        for field_name in balanced_spans.__dataclass_fields__:
            assert getattr(relaxed_spans, field_name) > getattr(balanced_spans, field_name)


class TestWeightsUnchangedAcrossPresets:
    def test_all_three_presets_share_identical_weights(self):
        strict_weights = PRESET_DEFINITIONS[PRESET_STRICT][2]
        balanced_weights = PRESET_DEFINITIONS[PRESET_BALANCED][2]
        relaxed_weights = PRESET_DEFINITIONS[PRESET_RELAXED][2]
        assert strict_weights == balanced_weights == relaxed_weights == ScoreWeights()


class TestGetPresetValues:
    def test_returns_serializable_dict_with_three_sections(self):
        values = get_preset_values(PRESET_BALANCED)
        assert set(values) == {"edges", "spans", "weights"}
        assert isinstance(values["edges"], dict)
        assert isinstance(values["spans"], dict)
        assert isinstance(values["weights"], dict)

    def test_unknown_preset_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            get_preset_values("does_not_exist")


class TestRoundTrip:
    @pytest.mark.parametrize("preset_name", [PRESET_STRICT, PRESET_BALANCED, PRESET_RELAXED])
    def test_preset_survives_serialize_deserialize_round_trip(self, preset_name):
        original_edges, original_spans, original_weights = PRESET_DEFINITIONS[preset_name]

        stored = {"score_config": get_preset_values(preset_name)}
        edges, spans, weights = config_entry_to_score_config(stored["score_config"])

        assert edges == original_edges
        assert spans == original_spans
        assert weights == original_weights

    def test_round_trip_matches_asdict_directly(self):
        # sanity check the round trip isn't accidentally lossy in a way
        # equality alone might not catch
        values = get_preset_values(PRESET_BALANCED)
        edges, spans, weights = config_entry_to_score_config(values)
        assert asdict(edges) == values["edges"]
        assert asdict(spans) == values["spans"]
        assert asdict(weights) == values["weights"]


class TestConfigEntryToScoreConfigDefensive:
    """Tests that config_entry_to_score_config fails soft (uses defaults)
    for missing/malformed stored data -- per README fail-soft principle."""

    def test_missing_edges_section_uses_defaults(self):
        """Entire 'edges' key missing from stored data."""
        stored = {"spans": {}, "weights": {}}
        edges, spans, weights = config_entry_to_score_config(stored)
        # edges should be all defaults
        assert edges == PlateauEdges()

    def test_missing_spans_section_uses_defaults(self):
        """Entire 'spans' key missing."""
        stored = {"edges": {}, "weights": {}}
        edges, spans, weights = config_entry_to_score_config(stored)
        assert spans == FalloffSpans()

    def test_missing_weights_section_uses_defaults(self):
        """Entire 'weights' key missing."""
        stored = {"edges": {}, "spans": {}}
        edges, spans, weights = config_entry_to_score_config(stored)
        assert weights == ScoreWeights()

    def test_completely_empty_stored_data_uses_all_defaults(self):
        """No 'score_config' dict at all - empty dict input."""
        edges, spans, weights = config_entry_to_score_config({})
        assert edges == PlateauEdges()
        assert spans == FalloffSpans()
        assert weights == ScoreWeights()

    def test_partial_edges_falls_back_to_defaults(self):
        """Some edge fields provided, rest should fall back to defaults."""
        stored = {
            "edges": {"low_cloud_max": 5.0},  # only one field
            "spans": {},
            "weights": {},
        }
        edges, spans, weights = config_entry_to_score_config(stored)
        assert edges.low_cloud_max == 5.0  # provided
        assert edges.mid_cloud_max == PlateauEdges().mid_cloud_max  # default

    def test_unknown_fields_are_ignored(self):
        """Extra fields not in dataclass should be silently ignored."""
        stored = {
            "edges": {"low_cloud_max": 5.0, "nonexistent_field": 999.0},
            "spans": {},
            "weights": {},
        }
        edges, spans, weights = config_entry_to_score_config(stored)
        # Should not raise, unknown field ignored
        assert edges.low_cloud_max == 5.0
        assert not hasattr(edges, "nonexistent_field")

    def test_none_section_values_handled(self):
        """Section explicitly set to None should fall back to defaults."""
        stored = {"edges": None, "spans": None, "weights": None}
        edges, spans, weights = config_entry_to_score_config(stored)
        assert edges == PlateauEdges()
        assert spans == FalloffSpans()
        assert weights == ScoreWeights()