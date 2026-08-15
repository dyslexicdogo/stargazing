"""Pure scoring logic for stargazing conditions.

Deliberately has ZERO Home Assistant imports so it can be unit-tested in
complete isolation before any API/HA plumbing exists (see
PROJECT_PRINCIPLES.md, "Pure scoring logic first").

SCORING MODEL: plateau + smoothstep falloff.

Each factor has a flat "perfect" zone where the score stays at 100, then
a smooth (Hermite/smoothstep) descent to 0 -- no hard cliffs, and the
score barely moves just past the edge of the perfect zone (a little bit
of cloud costs almost nothing; conditions have to get genuinely marginal
before the score really starts dropping).

This replaces an earlier centered-linear-gradient model (50 at threshold,
straight ramp to 0/100) which gave every unit of "worse" equal cost with
no forgiveness near the good end. See PlateauEdges / FalloffSpans below
for how the old FilterThresholds / ScoreRanges concepts map onto this.

Moon illumination is the one factor with an extra wrinkle: its effective
impact is scaled by how high the moon actually is above the horizon,
using a soft linear ramp (0 degrees = no impact, MOON_ALTITUDE_SCALE_DEGREES
and above = full impact) rather than a hard "up or down" cliff. A moon
barely peeking over the horizon shouldn't score identically to one
directly overhead.

Sun altitude is NOT a field here. It's a structural constraint enforced
upstream by the dynamic window boundaries (astronomical dusk/dawn), not
a weighted scoring factor -- see PROJECT_PRINCIPLES.md.
"""

from dataclasses import dataclass

# Altitude (degrees) at which the moon's illumination impact reaches full
# strength. Below this, impact scales down linearly toward zero at 0 deg.
MOON_ALTITUDE_SCALE_DEGREES = 30.0


# ---------------------------------------------------------------------------
# PlateauEdges: the value at which the flat "perfect" (100) zone ends and
# the falloff toward 0 begins, per factor.
#
# Old name: FilterThresholds. Old meaning: "the 50-point midpoint."
# New meaning: "the edge of the perfect zone" -- score is 100 at and
# beyond this edge (in the good direction), not 50.
# ---------------------------------------------------------------------------
@dataclass
class PlateauEdges:
    """Edge of the flat 100-score zone for each factor.

    For lower-is-better factors (clouds, wind, etc.) this is a ceiling:
    score is 100 at or below this value. For higher-is-better factors
    (visibility, dew point spread) this is a floor: score is 100 at or
    above this value.
    """

    low_cloud_max: float = 10.0  # % -- perfect at/below this
    mid_cloud_max: float = 20.0  # %
    high_cloud_max: float = 40.0  # % -- cirrus is often thin, generous ceiling
    dew_point_spread_min: float = 4.0  # °C -- perfect at/above this
    visibility_min: float = 20000.0  # m -- perfect at/above this
    jet_stream_wind_max: float = 20.0  # m/s at ~300hPa
    moon_illumination_max: float = 10.0  # % effective illumination
    precipitation_probability_max: float = 5.0  # %
    wind_speed_max: float = 10.0  # km/h at 10m


# ---------------------------------------------------------------------------
# FalloffSpans: how far, from the plateau edge, the score takes to reach 0.
#
# Old name: ScoreRanges. Old meaning: "span either side of a midpoint."
# New meaning: "one-directional falloff distance" -- zero point sits at
# (plateau_edge + span) for lower-is-better factors, or
# (plateau_edge - span) for higher-is-better factors.
# ---------------------------------------------------------------------------
@dataclass
class FalloffSpans:
    """Distance from each factor's PlateauEdges value to where its score
    reaches 0."""

    low_cloud_max: float = 60.0
    mid_cloud_max: float = 60.0
    high_cloud_max: float = 50.0
    dew_point_spread_min: float = 4.0
    visibility_min: float = 15000.0
    jet_stream_wind_max: float = 40.0
    moon_illumination_max: float = 60.0
    precipitation_probability_max: float = 35.0
    wind_speed_max: float = 30.0


# ---------------------------------------------------------------------------
# Weights: 0-5 "how much do I care" sliders, normalized automatically.
# Unchanged from the linear-gradient model -- meaning didn't shift.
# ---------------------------------------------------------------------------
@dataclass
class ScoreWeights:
    """User-facing importance sliders, 0-5. Do not need to sum to
    anything in particular -- calculate_score() normalizes by the sum."""

    low_cloud: float = 5.0
    mid_cloud: float = 3.0
    high_cloud: float = 1.5
    dew_point_spread: float = 1.5
    visibility: float = 1.0
    jet_stream_wind: float = 1.5
    moon_illumination: float = 2.0
    precipitation_probability: float = 1.5
    wind_speed: float = 0.5


# ---------------------------------------------------------------------------
# Raw hourly inputs, as handed over by the coordinator. Unchanged --
# the scoring curve shape doesn't affect what raw data is needed.
# ---------------------------------------------------------------------------
@dataclass
class HourlyConditions:
    """Raw data for a single hourly window. Dew point spread is derived
    from temperature and dew_point rather than passed in directly, since
    Open-Meteo supplies both raw fields."""

    low_cloud_cover: float  # %
    mid_cloud_cover: float  # %
    high_cloud_cover: float  # %
    temperature: float  # °C, 2m
    dew_point: float  # °C, 2m
    visibility: float  # m
    jet_stream_wind_speed: float  # m/s, ~300hPa
    moon_illumination: float  # % (0-100), raw phase illumination
    moon_altitude: float  # degrees above horizon; <= 0 means below horizon
    precipitation_probability: float  # %
    wind_speed: float  # km/h, 10m

    @property
    def dew_point_spread(self) -> float:
        """Temperature minus dew point. Small spread = dew/fog risk."""
        return self.temperature - self.dew_point


# ---------------------------------------------------------------------------
# Per-factor breakdown, useful for sensor attributes / diagnostics later.
# Unchanged shape -- still just holds the computed results.
# ---------------------------------------------------------------------------
@dataclass
class ScoreBreakdown:
    """Individual factor scores plus the final combined score. Handy for
    exposing "why is tonight a 62" as entity attributes down the line."""

    low_cloud: float
    mid_cloud: float
    high_cloud: float
    dew_point_spread: float
    visibility: float
    jet_stream_wind: float
    moon_illumination: float
    precipitation_probability: float
    wind_speed: float
    total: float


def _smoothstep(x: float, edge0: float, edge1: float) -> float:
    """Standard Hermite smoothstep, clamped to [0, 1].

    Monotonic increasing from 0 (at x == edge0) to 1 (at x == edge1),
    with zero slope at both ends -- the "gentle shoulder" that makes the
    plateau forgiving right past its edge.
    """
    if edge1 == edge0:
        raise ValueError("edge0 and edge1 must differ")
    t = (x - edge0) / (edge1 - edge0)
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _plateau_score(value: float, zero_at: float, hundred_at: float) -> float:
    """Plateau + smoothstep falloff, direction-agnostic.

    Score is 100 at/beyond hundred_at, 0 at/beyond zero_at (relative to
    hundred_at), and smoothly interpolated between. Works for both
    directions automatically:
      - lower-is-better: hundred_at < zero_at  (e.g. 10 -> 70)
      - higher-is-better: hundred_at > zero_at  (e.g. 20000 -> 5000)
    """
    return 100.0 * _smoothstep(value, zero_at, hundred_at)


def _factor_score(
    value: float,
    plateau_edge: float,
    falloff_span: float,
    higher_is_better: bool,
) -> float:
    """Resolves a factor's PlateauEdges + FalloffSpans values into the
    two absolute edges _plateau_score() needs, honoring the "threshold +
    span" mental model from PlateauEdges/FalloffSpans."""
    if falloff_span <= 0:
        raise ValueError("falloff_span must be positive")

    zero_at = (
        plateau_edge - falloff_span if higher_is_better else plateau_edge + falloff_span
    )
    return _plateau_score(value, hundred_at=plateau_edge, zero_at=zero_at)


def _moon_score(
    conditions: HourlyConditions, edges: PlateauEdges, spans: FalloffSpans
) -> float:
    """Moon illumination score, softly scaled by altitude.

    Effective illumination ramps from 0% impact at 0 degrees altitude to
    full impact at MOON_ALTITUDE_SCALE_DEGREES -- no hard cliff at the
    horizon. A moon barely above the horizon is treated as having far
    less practical impact than one high overhead, even at identical
    phase/illumination.
    """
    altitude_factor = max(
        0.0, min(1.0, conditions.moon_altitude / MOON_ALTITUDE_SCALE_DEGREES)
    )
    effective_illumination = conditions.moon_illumination * altitude_factor

    return _factor_score(
        effective_illumination,
        edges.moon_illumination_max,
        spans.moon_illumination_max,
        higher_is_better=False,
    )


def calculate_score(
    conditions: HourlyConditions,
    edges: PlateauEdges,
    spans: FalloffSpans,
    weights: ScoreWeights,
) -> float:
    """Combine all factors into a single 0-100 weighted-average score."""
    return _calculate_breakdown(conditions, edges, spans, weights).total


def calculate_score_breakdown(
    conditions: HourlyConditions,
    edges: PlateauEdges,
    spans: FalloffSpans,
    weights: ScoreWeights,
) -> ScoreBreakdown:
    """Same as calculate_score(), but returns every factor's individual
    score alongside the total -- useful for entity attributes/diagnostics
    so a user can see *why* a given hour scored the way it did."""
    return _calculate_breakdown(conditions, edges, spans, weights)


def _calculate_breakdown(
    conditions: HourlyConditions,
    edges: PlateauEdges,
    spans: FalloffSpans,
    weights: ScoreWeights,
) -> ScoreBreakdown:
    low_cloud = _factor_score(
        conditions.low_cloud_cover,
        edges.low_cloud_max,
        spans.low_cloud_max,
        higher_is_better=False,
    )
    mid_cloud = _factor_score(
        conditions.mid_cloud_cover,
        edges.mid_cloud_max,
        spans.mid_cloud_max,
        higher_is_better=False,
    )
    high_cloud = _factor_score(
        conditions.high_cloud_cover,
        edges.high_cloud_max,
        spans.high_cloud_max,
        higher_is_better=False,
    )
    dew_point_spread = _factor_score(
        conditions.dew_point_spread,
        edges.dew_point_spread_min,
        spans.dew_point_spread_min,
        higher_is_better=True,
    )
    visibility = _factor_score(
        conditions.visibility,
        edges.visibility_min,
        spans.visibility_min,
        higher_is_better=True,
    )
    jet_stream_wind = _factor_score(
        conditions.jet_stream_wind_speed,
        edges.jet_stream_wind_max,
        spans.jet_stream_wind_max,
        higher_is_better=False,
    )
    moon_illumination = _moon_score(conditions, edges, spans)
    precipitation_probability = _factor_score(
        conditions.precipitation_probability,
        edges.precipitation_probability_max,
        spans.precipitation_probability_max,
        higher_is_better=False,
    )
    wind_speed = _factor_score(
        conditions.wind_speed,
        edges.wind_speed_max,
        spans.wind_speed_max,
        higher_is_better=False,
    )

    factor_scores = {
        "low_cloud": (low_cloud, weights.low_cloud),
        "mid_cloud": (mid_cloud, weights.mid_cloud),
        "high_cloud": (high_cloud, weights.high_cloud),
        "dew_point_spread": (dew_point_spread, weights.dew_point_spread),
        "visibility": (visibility, weights.visibility),
        "jet_stream_wind": (jet_stream_wind, weights.jet_stream_wind),
        "moon_illumination": (moon_illumination, weights.moon_illumination),
        "precipitation_probability": (
            precipitation_probability,
            weights.precipitation_probability,
        ),
        "wind_speed": (wind_speed, weights.wind_speed),
    }

    weight_sum = sum(w for _, w in factor_scores.values())
    if weight_sum <= 0:
        raise ValueError("Sum of weights must be positive")

    total = sum(score * w for score, w in factor_scores.values()) / weight_sum

    return ScoreBreakdown(
        low_cloud=low_cloud,
        mid_cloud=mid_cloud,
        high_cloud=high_cloud,
        dew_point_spread=dew_point_spread,
        visibility=visibility,
        jet_stream_wind=jet_stream_wind,
        moon_illumination=moon_illumination,
        precipitation_probability=precipitation_probability,
        wind_speed=wind_speed,
        total=round(total, 2),
    )