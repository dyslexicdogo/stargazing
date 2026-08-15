"""Unit tests for score.py (plateau + smoothstep model).

No HA imports, no network, no JSON fixtures -- HourlyConditions is built
directly with hand-picked numbers. Exact expected values for the moon
and edge-case tests were computed and verified against the real
implementation before being hard-coded here.
"""

import pytest

from custom_components.stargazing.score import (
    MOON_ALTITUDE_SCALE_DEGREES,
    FalloffSpans,
    HourlyConditions,
    PlateauEdges,
    ScoreWeights,
    _factor_score,
    _moon_score,
    _plateau_score,
    _smoothstep,
    calculate_score,
    calculate_score_breakdown,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def edges() -> PlateauEdges:
    return PlateauEdges()  # defaults from score.py


@pytest.fixture
def spans() -> FalloffSpans:
    return FalloffSpans()


@pytest.fixture
def weights() -> ScoreWeights:
    return ScoreWeights()


def make_conditions(**overrides) -> HourlyConditions:
    """Baseline where every field sits exactly at its PlateauEdges value
    (i.e. every factor scores exactly 100 unless overridden)."""
    defaults = dict(
        low_cloud_cover=10.0,
        mid_cloud_cover=20.0,
        high_cloud_cover=40.0,
        temperature=10.0,
        dew_point=6.0,           # spread == 4.0 == dew_point_spread_min edge
        visibility=20000.0,
        jet_stream_wind_speed=20.0,
        moon_illumination=10.0,
        moon_altitude=30.0,      # >= MOON_ALTITUDE_SCALE_DEGREES, full impact
        precipitation_probability=5.0,
        wind_speed=10.0,
    )
    defaults.update(overrides)
    return HourlyConditions(**defaults)


# ---------------------------------------------------------------------------
# 1. _smoothstep
# ---------------------------------------------------------------------------
class TestSmoothstep:
    def test_returns_zero_at_edge0(self):
        assert _smoothstep(edge0=10.0, edge1=70.0, x=10.0) == 0.0

    def test_returns_one_at_edge1(self):
        assert _smoothstep(edge0=10.0, edge1=70.0, x=70.0) == 1.0

    def test_returns_half_at_midpoint(self):
        # smoothstep's symmetric property: the midpoint always maps to 0.5
        assert _smoothstep(edge0=10.0, edge1=70.0, x=40.0) == pytest.approx(0.5)

    def test_clamps_below_edge0(self):
        assert _smoothstep(edge0=10.0, edge1=70.0, x=-50.0) == 0.0

    def test_clamps_above_edge1(self):
        assert _smoothstep(edge0=10.0, edge1=70.0, x=500.0) == 1.0

    def test_works_in_reverse_order(self):
        # edge0 > edge1 (used internally for higher-is-better factors)
        assert _smoothstep(edge0=70.0, edge1=10.0, x=70.0) == 0.0
        assert _smoothstep(edge0=70.0, edge1=10.0, x=10.0) == 1.0

    def test_raises_when_edges_are_equal(self):
        with pytest.raises(ValueError):
            _smoothstep(edge0=30.0, edge1=30.0, x=30.0)


# ---------------------------------------------------------------------------
# 2. _plateau_score (direction-agnostic, works off raw hundred_at/zero_at)
# ---------------------------------------------------------------------------
class TestPlateauScore:
    def test_lower_is_better_direction(self):
        # hundred_at (10) < zero_at (70): score falls as value rises
        assert _plateau_score(value=10.0, hundred_at=10.0, zero_at=70.0) == 100.0
        assert _plateau_score(value=70.0, hundred_at=10.0, zero_at=70.0) == 0.0
        assert _plateau_score(value=40.0, hundred_at=10.0, zero_at=70.0) == pytest.approx(
            50.0
        )
        # matches the worked example from the design discussion
        assert _plateau_score(value=15.0, hundred_at=10.0, zero_at=70.0) == pytest.approx(
            98.03, abs=0.01
        )
        assert _plateau_score(value=65.0, hundred_at=10.0, zero_at=70.0) == pytest.approx(
            1.97, abs=0.01
        )

    def test_higher_is_better_direction(self):
        # hundred_at (20000) > zero_at (5000): score falls as value drops
        assert _plateau_score(value=20000.0, hundred_at=20000.0, zero_at=5000.0) == 100.0
        assert _plateau_score(value=5000.0, hundred_at=20000.0, zero_at=5000.0) == 0.0
        assert _plateau_score(
            value=12500.0, hundred_at=20000.0, zero_at=5000.0
        ) == pytest.approx(50.0)

    def test_never_exceeds_bounds_beyond_edges(self):
        assert _plateau_score(value=-1000.0, hundred_at=10.0, zero_at=70.0) == 100.0
        assert _plateau_score(value=1000.0, hundred_at=10.0, zero_at=70.0) == 0.0


# ---------------------------------------------------------------------------
# 3. _factor_score (resolves PlateauEdges + FalloffSpans, both directions)
# ---------------------------------------------------------------------------
class TestFactorScore:
    def test_lower_is_better_edges(self):
        # plateau_edge=10, span=60 -> zero at 70
        assert _factor_score(
            10.0, plateau_edge=10.0, falloff_span=60.0, higher_is_better=False
        ) == 100.0
        assert _factor_score(
            70.0, plateau_edge=10.0, falloff_span=60.0, higher_is_better=False
        ) == 0.0
        assert _factor_score(
            40.0, plateau_edge=10.0, falloff_span=60.0, higher_is_better=False
        ) == pytest.approx(50.0)

    def test_higher_is_better_edges(self):
        # plateau_edge=20000, span=15000 -> zero at 5000
        assert _factor_score(
            20000.0, plateau_edge=20000.0, falloff_span=15000.0, higher_is_better=True
        ) == 100.0
        assert _factor_score(
            5000.0, plateau_edge=20000.0, falloff_span=15000.0, higher_is_better=True
        ) == 0.0
        assert _factor_score(
            12500.0, plateau_edge=20000.0, falloff_span=15000.0, higher_is_better=True
        ) == pytest.approx(50.0)

    def test_raises_on_zero_span(self):
        with pytest.raises(ValueError):
            _factor_score(10.0, plateau_edge=10.0, falloff_span=0.0, higher_is_better=False)

    def test_raises_on_negative_span(self):
        with pytest.raises(ValueError):
            _factor_score(10.0, plateau_edge=10.0, falloff_span=-5.0, higher_is_better=False)


# ---------------------------------------------------------------------------
# 4. _moon_score at different altitudes (exact values verified beforehand)
# ---------------------------------------------------------------------------
class TestMoonScore:
    def test_below_horizon_scores_full_marks_regardless_of_illumination(
        self, edges, spans
    ):
        conditions = make_conditions(moon_illumination=100.0, moon_altitude=-5.0)
        assert _moon_score(conditions, edges, spans) == 100.0

    def test_exactly_at_horizon_scores_full_marks(self, edges, spans):
        conditions = make_conditions(moon_illumination=100.0, moon_altitude=0.0)
        assert _moon_score(conditions, edges, spans) == 100.0

    def test_low_altitude_full_moon_mostly_forgiven(self, edges, spans):
        # altitude=5 (1/6 of the way to full impact) -- still fairly high
        conditions = make_conditions(moon_illumination=100.0, moon_altitude=5.0)
        assert _moon_score(conditions, edges, spans) == pytest.approx(96.57, abs=0.01)

    def test_mid_altitude_full_moon_scores_worse(self, edges, spans):
        conditions = make_conditions(moon_illumination=100.0, moon_altitude=15.0)
        assert _moon_score(conditions, edges, spans) == pytest.approx(25.93, abs=0.01)

    def test_at_scale_degrees_full_moon_scores_zero(self, edges, spans):
        # at MOON_ALTITUDE_SCALE_DEGREES, effective illumination == raw (100%),
        # which is past this factor's zero point (edge 10 + span 60 = 70)
        conditions = make_conditions(
            moon_illumination=100.0, moon_altitude=MOON_ALTITUDE_SCALE_DEGREES
        )
        assert _moon_score(conditions, edges, spans) == 0.0

    def test_altitude_beyond_scale_degrees_clamps_same_as_at_scale(self, edges, spans):
        at_scale = make_conditions(
            moon_illumination=100.0, moon_altitude=MOON_ALTITUDE_SCALE_DEGREES
        )
        beyond_scale = make_conditions(moon_illumination=100.0, moon_altitude=80.0)
        assert _moon_score(at_scale, edges, spans) == _moon_score(
            beyond_scale, edges, spans
        )

    def test_higher_altitude_never_scores_better_than_lower_altitude(
        self, edges, spans
    ):
        # monotonic: as altitude rises (more impact), score should not improve
        altitudes = [-5.0, 0.0, 5.0, 15.0, 30.0, 60.0]
        scores = [
            _moon_score(
                make_conditions(moon_illumination=100.0, moon_altitude=alt),
                edges,
                spans,
            )
            for alt in altitudes
        ]
        assert scores == sorted(scores, reverse=True)

    def test_new_moon_scores_full_marks_at_any_altitude(self, edges, spans):
        # 0% illumination -> effective illumination is 0 regardless of altitude
        conditions = make_conditions(moon_illumination=0.0, moon_altitude=45.0)
        assert _moon_score(conditions, edges, spans) == 100.0


# ---------------------------------------------------------------------------
# 5. dew_point_spread property
# ---------------------------------------------------------------------------
class TestDewPointSpread:
    def test_spread_is_temperature_minus_dew_point(self):
        conditions = make_conditions(temperature=10.0, dew_point=3.0)
        assert conditions.dew_point_spread == 7.0

    def test_spread_can_be_zero(self):
        conditions = make_conditions(temperature=10.0, dew_point=10.0)
        assert conditions.dew_point_spread == 0.0

    def test_spread_can_be_negative(self):
        # not physically typical, but the property shouldn't crash on it
        conditions = make_conditions(temperature=5.0, dew_point=8.0)
        assert conditions.dew_point_spread == -3.0

    def test_larger_spread_scores_higher(self, edges, spans, weights):
        dry = calculate_score_breakdown(
            make_conditions(temperature=10.0, dew_point=0.0), edges, spans, weights
        )
        damp = calculate_score_breakdown(
            make_conditions(temperature=10.0, dew_point=9.5), edges, spans, weights
        )
        assert dry.dew_point_spread > damp.dew_point_spread


# ---------------------------------------------------------------------------
# 6. calculate_score / calculate_score_breakdown across full scenarios
# ---------------------------------------------------------------------------
class TestCalculateScore:
    def test_all_factors_at_plateau_edge_scores_hundred(self, edges, spans, weights):
        conditions = make_conditions()  # every field exactly at its edge
        assert calculate_score(conditions, edges, spans, weights) == 100.0

    def test_all_factors_better_than_plateau_edge_scores_hundred(
        self, edges, spans, weights
    ):
        conditions = make_conditions(
            low_cloud_cover=0.0,
            mid_cloud_cover=0.0,
            high_cloud_cover=0.0,
            temperature=15.0,
            dew_point=0.0,
            visibility=50000.0,
            jet_stream_wind_speed=0.0,
            moon_illumination=0.0,
            precipitation_probability=0.0,
            wind_speed=0.0,
        )
        assert calculate_score(conditions, edges, spans, weights) == 100.0

    def test_one_factor_just_past_edge_barely_moves_total(
        self, edges, spans, weights
    ):
        # low_cloud at 11% instead of the 10% edge -- everything else perfect
        conditions = make_conditions(low_cloud_cover=11.0)
        breakdown = calculate_score_breakdown(conditions, edges, spans, weights)
        assert breakdown.low_cloud == pytest.approx(99.92, abs=0.01)
        assert breakdown.total == pytest.approx(99.98, abs=0.01)
        # the whole point of the plateau model: a small excursion barely dents
        # the overall score
        assert breakdown.total > 99.5

    def test_all_factors_at_or_beyond_zero_point_scores_zero(
        self, edges, spans, weights
    ):
        conditions = make_conditions(
            low_cloud_cover=70.0,
            mid_cloud_cover=80.0,
            high_cloud_cover=90.0,
            temperature=10.0,
            dew_point=10.0,  # spread 0, zero point for spread edge(4)-span(4)=0
            visibility=5000.0,  # zero point = 20000 - 15000
            jet_stream_wind_speed=60.0,  # zero point = 20 + 40
            moon_illumination=100.0,
            moon_altitude=30.0,  # full effective illumination, zero point = 70
            precipitation_probability=40.0,  # zero point = 5 + 35
            wind_speed=40.0,  # zero point = 10 + 30
        )
        assert calculate_score(conditions, edges, spans, weights) == 0.0

    def test_zero_total_weight_raises(self, edges, spans):
        all_zero = ScoreWeights(
            low_cloud=0.0,
            mid_cloud=0.0,
            high_cloud=0.0,
            dew_point_spread=0.0,
            visibility=0.0,
            jet_stream_wind=0.0,
            moon_illumination=0.0,
            precipitation_probability=0.0,
            wind_speed=0.0,
        )
        with pytest.raises(ValueError):
            calculate_score(make_conditions(), edges, spans, all_zero)

    def test_wind_speed_weight_is_deemphasized(self, edges, spans):
        # confirms the current default (0.5) barely moves the total even
        # when wind is fully ruined, since it's now a small slice of weight
        weights = ScoreWeights()
        conditions = make_conditions(wind_speed=40.0)  # fully ruined (zero point)
        breakdown = calculate_score_breakdown(conditions, edges, spans, weights)
        assert breakdown.wind_speed == 0.0
        # 0.5 / 17.5 total weight -> ruining wind alone costs under 3 points
        assert breakdown.total > 97.0


# ---------------------------------------------------------------------------
# 7. ScoreBreakdown -- individual fields match their factor, not just total
# ---------------------------------------------------------------------------
class TestScoreBreakdown:
    def test_breakdown_exposes_every_factor(self, edges, spans, weights):
        conditions = make_conditions()
        breakdown = calculate_score_breakdown(conditions, edges, spans, weights)
        assert breakdown.low_cloud == 100.0
        assert breakdown.mid_cloud == 100.0
        assert breakdown.high_cloud == 100.0
        assert breakdown.dew_point_spread == 100.0
        assert breakdown.visibility == 100.0
        assert breakdown.jet_stream_wind == 100.0
        assert breakdown.moon_illumination == 100.0
        assert breakdown.precipitation_probability == 100.0
        assert breakdown.wind_speed == 100.0
        assert breakdown.total == 100.0

    def test_breakdown_total_matches_calculate_score(self, edges, spans, weights):
        conditions = make_conditions(low_cloud_cover=25.0, moon_altitude=15.0)
        breakdown = calculate_score_breakdown(conditions, edges, spans, weights)
        total_only = calculate_score(conditions, edges, spans, weights)
        assert breakdown.total == total_only

    def test_breakdown_isolates_which_factor_is_responsible(
        self, edges, spans, weights
    ):
        # only high_cloud is bad -- breakdown should show that specifically,
        # not just a lowered total with no way to tell why
        conditions = make_conditions(high_cloud_cover=90.0)
        breakdown = calculate_score_breakdown(conditions, edges, spans, weights)
        assert breakdown.high_cloud < 50.0
        assert breakdown.low_cloud == 100.0
        assert breakdown.mid_cloud == 100.0
        assert breakdown.dew_point_spread == 100.0


# ---------------------------------------------------------------------------
# Integration-style sanity checks: hand-picked known-good / known-bad nights
# ---------------------------------------------------------------------------
class TestKnownScenarios:
    def test_known_good_night_scores_very_high(self, edges, spans, weights):
        clear_dry_calm_new_moon = make_conditions(
            low_cloud_cover=2.0,
            mid_cloud_cover=5.0,
            high_cloud_cover=10.0,
            temperature=8.0,
            dew_point=1.0,
            visibility=24000.0,
            jet_stream_wind_speed=10.0,
            moon_illumination=3.0,
            moon_altitude=-20.0,
            precipitation_probability=0.0,
            wind_speed=6.0,
        )
        score = calculate_score(clear_dry_calm_new_moon, edges, spans, weights)
        assert score > 98.0

    def test_known_bad_night_scores_very_low(self, edges, spans, weights):
        overcast_wet_stormy_full_moon = make_conditions(
            low_cloud_cover=95.0,
            mid_cloud_cover=90.0,
            high_cloud_cover=95.0,
            temperature=10.0,
            dew_point=9.9,
            visibility=500.0,
            jet_stream_wind_speed=70.0,
            moon_illumination=98.0,
            moon_altitude=50.0,
            precipitation_probability=95.0,
            wind_speed=45.0,
        )
        score = calculate_score(overcast_wet_stormy_full_moon, edges, spans, weights)
        assert score < 5.0