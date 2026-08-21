# 🔭 Stargazing

A Home Assistant custom integration that scores hourly stargazing conditions
using [Open-Meteo](https://open-meteo.com/) weather data — tuned for places
where dark skies are worth checking before you drag a telescope outside
(built with Scotland's weather in mind).

Inspired by the [Clear Outside](https://clearoutside.com/) astronomy weather
site and the
[AstroWeather](https://github.com/mawinkler/astroweather) integration.
Unlike fixed daytime windows, stargazing windows are **dynamic** — tied to
the actual astronomical dusk/dawn for your location and season.

> **Status:** Phases 1–9 of the build roadmap are complete (scoring, API
> client, astronomical windows, coordinator, config flow, sensors, full
> integration tests, and the 3-night forecast Lovelace card). See
> [Roadmap](#roadmap).

---

## How it works

```
Open-Meteo API (hourly forecast)
        ↓
client.py — dumb aiohttp wrapper, zero domain knowledge
        ↓
coordinator.py — polls every 30 min, combines weather with the
        dynamically-computed darkness windows (dusk→dawn)
        ↓
score.py — pure, zero-HA-imports scoring: 9 factors, each 0–100,
        weighted into a total per hour (higher = better)
        ↓
sensor.py — 3 night-forecast sensors (peak score per night, per-hour
        forecast attributes)
        ↓
Lovelace card (custom:stargazing-forecast-card) — 3-night chart with
        tap-to-see-breakdown
```

- **Darkness windows** are computed from real astronomical dusk/dawn
  (`astral`), so a night's usable hours change with the season and your
  location/elevation — no fixed 10am–5pm grid here.
- **Moon illumination** (bright moon washes out faint objects) comes from
  `skyfield` + a bundled ephemeris (`de421.bsp`), reported as numeric
  illumination %.
- Each scored hour is a **0–100 score** (higher = better), with the
  nine factor sub-scores exposed so you can see *why* a night scored
  what it did.

## Features

**Built today:**
- Two-step config flow — location (defaults to your HA location) then a
  preset + twilight-tier pick, with presets available **from day one**
- Three scoring presets: Strict / Balanced / Relaxed
- Three "preferred darkness" night types: Astronomical (default) /
  Nautical / Civil
- Three night sensors: tonight, tomorrow night, and night+2 peaks, each
  with the full per-hour forecast available as attributes
- 143 passing tests across the client, scoring, windows, coordinator,
  config flow, and sensors

**Built in Phase 9:**
- `custom:stargazing-forecast-card` — a graphical 3-night score-over-time
  chart; tap any hour to see its nine-factor breakdown below the chart

**Planned:**
- Options flow (edit preset/tier after setup, collapsible sections)
- Notifications + HACS packaging

## Scoring

Each hour in a darkness window is scored 0–100 from nine weighted factors
(higher = better). Raw values at or inside the plateau edge score ~50 and
better; conditions worse than that fall off via a soft gradient.

| Factor | What matters | Perfect at/under |
|---|---|---|
| Low cloud | Most disruptive to a dark sky | ≤ 10% |
| Mid cloud | — | ≤ 20% |
| High cloud | Cirrus is often thin — generous ceiling | ≤ 40% |
| Dew-point spread | Temp minus dew point; small spread = dew/fog on optics | ≥ 4 °C |
| Visibility | Seeing clarity | ≥ 20 km |
| Jet-stream wind (~300 hPa) | High-altitude turbulence (seeing proxy) | ≤ 20 m/s |
| Moon illumination | Bright moon washes out faint objects | ≤ 10% |
| Precipitation probability | — | ≤ 5% |
| Wind speed (10 m) | Telescope stability | ≤ 10 km/h |

### Presets

| Preset | Mood |
|---|---|
| **Balanced** | The reasoned baseline — `score.py` defaults |
| **Strict** | Half the "perfect" zone, steeper falloff — picky nights only |
| **Relaxed** | Almost twice the "perfect" zone, gentler falloff — forgive the Scottish weather |

Presets are derived by systematic scaling from the single balanced
baseline (not hand-picked numbers), so they're honest about being
unvalidated against real nights and cheap to re-tune later. Presets tune
how *strict* you are; per-factor *weights* stay the same across presets.

### Night type (preferred darkness)

The night type is a *preference*: the darkest tier you choose is used
whenever it's reachable, otherwise the window falls back to a shallower
tier so a night is never skipped. Example scored-hour counts for Inverness
in August: astronomical ~3h, nautical ~6h, civil ~8h.

| Night type | Window used (fallback chain) | Example (Inverness, August) |
|---|---|---|
| **Astronomical** (default) | Astronomical → Nautical → Civil | ~3h (darkest-first, never misses a window) |
| **Nautical** | Nautical → Civil | ~6h |
| **Civil** | Civil only | ~8h (most hours) |

Where the sun reaches −18° every night (e.g. Scottish winter), all three
resolve to astronomical darkness — the choice matters most in summer,
when astronomical/nautical darkness can disappear entirely and only the
fallback keeps a window.

## Requirements

- **Home Assistant Core 2026.2.3 or later** (the version the integration
  is developed and tested against — confirm the PyPI `homeassistant`
  package matches your real instance; PyPI can lag behind HAOS releases).
- `astral>=2.2` and `skyfield>=1.42` are declared in the manifest and
  installed automatically.

## Installation

> HACS packaging ships in Phase 11 — until then, install manually.

### Manual

1. Copy `custom_components/stargazing` into your Home Assistant
   `config/custom_components/` folder (keep the bundled `de421.bsp`
   ephemeris — it's required, ~16 MB).
2. Restart Home Assistant.

### HACS (once packaged)

1. HACS → Integrations → ⋮ → **Custom repositories** → add
   `https://github.com/dyslexicdogo/stargazing` as an **Integration**.
2. Search for **Stargazing** and install.
3. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Stargazing**
3. **Step 1 — Location**: latitude/longitude/elevation, pre-filled with
   your Home Assistant location
4. **Step 2 — Preset & night type**: pick Strict/Balanced/Relaxed and
   your preferred darkness — Astronomical (darkest, default), Nautical, or
   Civil (most hours). The darkest choice is used when reachable; the
   window falls back to a shallower tier so a night is never skipped.

You'll end up with three sensors:
`sensor.stargazing_tonight`, `sensor.stargazing_tomorrow_night`, and
`sensor.stargazing_in_two_nights`.

## Sensors

| Entity | State | Notable attributes |
|---|---|---|
| `sensor.stargazing_tonight` | Best (peak) score tonight, 0–100 | `night_of`, `window_start`, `window_end`, `twilight_tier`, `hourly_scores_count`, `forecast`¹ |
| `sensor.stargazing_tomorrow_night` | Peak score tomorrow night | same as above |
| `sensor.stargazing_in_two_nights` | Peak score in two nights | same as above |

¹ `forecast` — the full per-hour breakdown for that night: one dict per
scored hour with `time`, `score`, and all nine factor sub-scores. Each
entry also carries a `raw` bundle with the un-rounded raw readings, so
the card can show each factor as "reading (score)". This is what the
forecast card renders.

## Lovelace card (Phase 9 — complete)

One card, designed around one principle: the overview shows *only
scores* (no parameter walls), and the nine factors appear on demand for
the hour you care about.

### `custom:stargazing-forecast-card` — graphical 3-night forecast

```
┌───────────────────────────────────────────────────────────┐
│  Stargazing Forecast · Wed 15 – Fri 17 Jan                │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 100 ·                                              │  │
│  │     ·     Tonight        Tomorrow      Night +2     │  │
│  │     ·       78●            72●            ●61       │  │
│  │  70 ·      ●   ● ●●     ●    ●  ●      ●   ●       │  │
│  │     ·     ●  ●   ●   ●  ●  ●  ●  ●    ●    ●       │  │
│  │     ·    ● ●  ●  ●  ●  ●  ●   ●  ●  ● ●    ●       │  │
│  │  40 ·   ●    ● ●  ●  ●   ●  ●   ●  ●   ●    ●      │  │
│  │     ·                                                  │  │
│  │  0 ────────────────────────────────────────────────   │  │
│  │  20:00   00:00   04:00  20:00   00:00  20:00   00:00 │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  Tonight       20:12–04:47 · peak 78  ● (tap point)       │
│  Tomorrow N.   20:10–04:45 · peak 72                     │
│  Night +2      20:08–04:40 · peak 61                     │
│  ──────────────────────────────────────────────────────   │
│  Selected: 21:00 · score 78                               │
│  ☁ Low 10% (82) ☁ Mid 15% (74) ☁ High 20% (90)           │
│  👁 Vis 20 km (71) ✈ Jet 12 m/s (68) 🌙 Moon 30% (85)     │
│  💧 Precip 5% (85) 💨 Wind 8 km/h (72) ☀ Dew 4 °C (65)    │
└───────────────────────────────────────────────────────────┘
```

- One chart, all three nights, on a shared time axis — total score only.
- **Tap a point** to see that hour's nine-factor breakdown below the chart
  (defaults to tonight's peak).
- Nights without a darkness window render dimmed with a "no darkness
  window" note.

```yaml
type: custom:stargazing-forecast-card
# optional: entity_tonight / entity_tomorrow / entity_night2 overrides
```

### Resources

The card registers itself as a Lovelace resource automatically once the
integration is set up. If you manage dashboards in **YAML mode**,
Lovelace's resource collection isn't available — add the resource
manually instead:

```yaml
resources:
  - url: /stargazing/stargazing-forecast-card.js
    type: module
```

## Roadmap

| # | Phase | Status |
|---|---|---|
| 1 | Environment / scripts / skeleton loads | ✅ |
| 2 | Pure scoring logic (`score.py`) | ✅ |
| 3 | Dumb API client (`client.py`) | ✅ |
| 4 | Astronomical windows + moon (`astro.py`) | ✅ |
| 5 | Coordinator (`coordinator.py`) | ✅ |
| 6 | Config flow with presets | ✅ |
| 7 | Sensors (`sensor.py`) | ✅ |
| 8 | Full integration tests | ✅ |
| 9 | **Lovelace forecast card** (`custom:stargazing-forecast-card`, served from `www/`) | ✅ |
| 10 | Options flow with collapsible sections | ⏳ |
| 11 | Notifications + HACS packaging | ⏳ |

Follow-ups also tracked: ozone/fog factors, `diagnostics.py` support, a
brand icon, and a test-timezone cleanup in the coordinator tests.

## Development

Built incrementally as a learning project. See
[`PROJECT_PRINCIPLES.md`](PROJECT_PRINCIPLES.md) for the architecture
notes, design decisions, phase-by-phase build log, and the HA API gotchas
learned the hard way (both on this project and its predecessor,
[`sun_bathing`](https://github.com/dyslexicdogo/sun_bathing)).

```bash
scripts/setup     # create the .venv and install dependencies
scripts/develop   # boot Home Assistant against ./config with the integration loaded
scripts/test      # run the full pytest suite
```

- Tests: `pytest tests/ -v` (137 passing). Scoring and astronomical
  logic are unit-tested in isolation with hand-built fakes; the API
  client is tested with `aioresponses`; the integration has a real-hass,
  real-config-entry end-to-end test.

## Lessons learned from sun_bathing

Everything in `PROJECT_PRINCIPLES.md` traces back to what `sun_bathing`
taught us, but the card-related ones are worth calling out here because
they shape Phase 9:

- **Cards must live inside `custom_components/<domain>/www/`.** HACS
  "Integration" category repos only download `custom_components/` — a
  repo-root `www/` installs the Python fine but silently never ships the
  card ("Custom element not found", no cause).
- **Auto-injecting card scripts races the frontend.** `add_extra_js_url`
  intermittently loses a hard-refresh race. The reliable path is
  registering a Lovelace **resource** through the storage-collection API,
  idempotently guarded.
- **Verify HA APIs against the installed version before coding.**
  `StaticPathConfig` objects (not dicts), read-only
  `OptionsFlow.config_entry`, thread-safety on time-change callbacks,
  `hass.http` being `None` in tests — all found by checking the actual
  source.
- **Test every increment before moving on**, with minimal fakes, not full
  mocks or real network. Exception: UI/card work is verified manually
  first, then backfilled once the design settles.

## Inspiration & credits

- **AstroWeather** and the **AstroWeather Card** by
  [@mawinkler](https://github.com/mawinkler) — the "percent good per
  factor" visualization and dense single-panel readouts inspired our
  per-factor color coding and the forecast card's breakdown panel.
- **Clear Outside** — the original astronomy-weather concept this
  integration is inspired by.
- **Sun Bathing** by the same author — the structural blueprint: project
  phases, test discipline, and the HACS packaging lessons above.
- Scoring/astronomy libraries: [Open-Meteo](https://open-meteo.com/),
  [astral](https://github.com/sffjunkie/astral),
  [Skyfield](https://rhodesmill.org/skyfield/) + NASA JPL
  `de421.bsp` ephemeris.

## License

MIT (license file to be added with HACS packaging).