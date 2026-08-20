# Stargazing HA Integration — Project Principles

Scaffolded from lessons learned building `sun_bathing`
(https://github.com/dyslexicdogo/sun_bathing). This document is meant to be
pasted into a fresh chat session to carry that project's hard-won lessons
forward without re-learning them. The user-facing guide (installation,
setup, cards, roadmap) lives in `README.md`; this is the internal
principles / build-log reference.

---

## Project concept

A Home Assistant custom integration scoring stargazing conditions,
inspired by the "Clear Outside" astronomy weather site. Unlike
`sun_bathing`'s fixed daytime windows, stargazing windows are **dynamic**
— tied to actual astronomical dusk/dawn for the location and season
(important given how much daylight hours swing in Scotland).

### Scoring factors (richer than sun_bathing's 6)

- Total cloud cover (via the low/mid/high split below)
- Low / mid / high cloud cover (separately — low cloud is usually most
  disruptive; high cirrus can be thin enough to partially see through)
- Visibility
- Dew point vs. temperature (dew risk on optics)
- Jet-stream wind (~300 hPa, telescope seeing proxy)
- Moon illumination (bright moon washes out faint objects)
- Precipitation probability
- Wind speed at the surface (telescope stability)

Each factor is scored 0–100 (higher = better) and combined into a weighted
`total`. Nine factor sub-scores + the total are exposed per hour.

### Data sources (implemented)

- **Open-Meteo** (same provider as sun_bathing) covers: cloud cover
  (low/mid/high), visibility, dew point, precipitation probability, wind
  speed — via the standard hourly forecast endpoint, fetched by a dumb
  aiohttp client.
- **Dusk/dawn windows** — computed with `astral` (already a dependency of
  HA core), configured per location/elevation and a twilight-tier choice.
- **Moon illumination** — computed with `skyfield` + the bundled
  `de421.bsp` ephemeris (numeric illumination %, not HA's 8-phase moon
  sensor).
- **Fog % / ozone / RH / pressure / apparent temperature** — deliberately
  NOT in the current factor set (see open decisions below).

### Open design decisions

- **Moon phase**: RESOLVED → numeric illumination % via `astral`/`skyfield`
  (not HA's discrete 8-phase `sensor.moon`).
- **Ozone**: deferred — the air-quality endpoint exists but ozone is not
  in the current 9-factor breakdown; revisit when real nights have been
  validated against the existing factors.
- **Fog %**: not directly available from Open-Meteo; would be approximated
  from low visibility + high humidity. Deferred for the same reason as
  ozone.
- **Scoring weights/formula**: settled — centered soft-gradient approach
  (raw "perfect" value = score 50 at the plateau edge), auto-normalized
  weights, three presets (strict/balanced/relaxed) derived by systematic
  scaling from a single reasoned baseline rather than hand-picked numbers
  (honest about the lack of real-world validation; easy to re-tune later).

---

## Learning-by-doing: phase sequence

1. **Environment** — devcontainer, `scripts/setup`/`scripts/develop`,
   confirm a skeleton integration loads in HA before anything else
2. **Pure scoring logic first** — dataclasses + `calculate_score()`
   equivalent, zero HA imports, unit-testable in complete isolation
   before any API/HA plumbing exists at all
3. **Dumb API client** — Open-Meteo wrapper, no domain knowledge, tested
   with `aioresponses`
4. **Astronomical window calculation** — the genuinely new piece this
   project needs; build and test dusk/dawn + moon phase lookup as its
   own isolated unit before wiring it into a coordinator
5. **Coordinator** — fetch + filter + combine weather data with the
   dynamic window boundaries
6. **Config flow** — location, thresholds, **presets from day one**
   (rather than bolting them on later, as happened in sun_bathing)
7. **Sensors** — entities exposing scores + attributes
8. **Full integration test** — real `hass`, real config entry, confirm
   entities land correctly
9. **Lovelace card** — built inside `custom_components/<domain>/www/`
   **from the start**, never at the repo root
10. **Options flow with sections** — thresholds/ranges/weights/
    notifications, built with collapsible sections from day one
11. **Notifications + HACS packaging** — same proven pattern

### Current status

- **Done:** Phases 1–8. 137 tests passing. `scripts/develop` boots HA with
  the integration loaded; the config flow is verified working in the live
  UI.
- **In progress:** Phase 9 — two Lovelace cards (graphical 3-night
  forecast + current conditions). Design spec finalized; sensor
  `forecast`/`upcoming` attributes and the card files are next.
- **Pending:** Phase 10 (options flow), Phase 11 (notifications + HACS
  packaging), plus the follow-ups listed below.

### Flagged follow-ups

- `tests/test_coordinator.py` still sets `hass.config.time_zone` directly
  in places (works only by the noon-cutover coincidence); migrate those to
  `await hass.config.async_set_time_zone(...)` like the sensor tests.
- Ozone / fog factors (see open decisions).
- `diagnostics.py` ("Download Diagnostics" support).
- Brand icon (`brand/icon.png` + `icon@2x.png`).
- Robust Lovelace resource auto-registration edge cases (YAML-mode
  dashboards need manual resource registration).

---

## Design principles carried forward from sun_bathing

### Separation of concerns, strictly enforced
- API client knows *only* how to talk to the API — zero domain logic,
  zero knowledge of "windows" or "scoring"
- Scoring logic has *zero* HA imports — pure, fast, trivially
  unit-testable
- Coordinator is thin orchestration glue only — fetch, filter, cache; no
  business logic lives here
- One source of truth for shared config (presets, thresholds) — never
  redefine the same data in two files (sun_bathing's `presets.py` pattern)
### Test discipline
- Every new piece of logic gets a test *before* moving to the next piece,
  using minimal hand-built fakes (not full mocks, not real network) — the
  single most valuable habit from sun_bathing
- Exception: pure UI/UX flow work (config flow screens, card visuals) is
  fine to verify manually first, then backfill tests once the design has
  settled — testing broken/unsettled UI first just means debugging tests
  instead of debugging the UI
### Verify before coding, especially for HA APIs
- Never assume an HA API signature/behavior from memory or a tutorial —
  check the actual installed version's source, or search for current
  documentation, *before* writing code around it
- This one habit would have saved real time on sun_bathing's
  `StaticPathConfig`, `OptionsFlow.config_entry`, and the
  astral/sun-trigger research
- For anything with a **severe failure mode** (data loss, integration-wide
  crash) — research thoroughly, confirm the fix/behavior is actually
  shipped in the target HA version, and test cautiously with a backup,
  before it ever touches a real instance (see: sun_bathing's Lovelace
  resource-wiping bug investigation, home-assistant/core#165767)
### Defensive parsing for anything user-configurable
- Any string/value a user can type into a config flow should fail *soft*
  with a sensible fallback, never crash the whole integration's setup
  over one bad field (sun_bathing's `notify_time` crash lesson)
### Idempotency for anything that runs on every reload
- Scheduled tasks, registered resources, listeners — always guard against
  duplicating on every restart/reload, and always clean up via
  `entry.async_on_unload` / `async_on_remove`
### Keep a living project doc from day one
- Update this doc (and the README) as you go, right after each real
  bug/lesson — cheaper to write two sentences immediately than reconstruct
  the story weeks later (as had to be done retroactively for sun_bathing)

---

## Known HA API gotchas to remember (sun_bathing history)

- `hass.http.async_register_static_paths()` wants a list of
  `StaticPathConfig` **objects**, not plain dicts
- `OptionsFlow.config_entry` is a **read-only property** in current HA
  core — don't assign it in `__init__`; the base class populates it
  automatically
- `async_track_time_change`'s callback should be a plain `async def`
  function — wrapping it in `hass.async_create_task(...)` via a lambda
  triggers a thread-safety violation. (Stargazing's own hour-boundary
  listener is a `@callback`-decorated sync function — a plain sync
  function becomes a `HassJobType.EXECUTOR` job and runs in a thread,
  which is also a thread-safety violation.)
- Custom Lovelace cards **must** live inside
  `custom_components/<domain>/www/`, never the repo root — HACS
  "Integration" category repos only download `custom_components/`
- Auto-injecting card scripts via `add_extra_js_url` races the frontend on
  hard refresh ("Custom element not found") — prefer registering a
  Lovelace **resource** through the storage-collection API instead
- `asyncio_mode = auto` (not `strict`) needed in `pytest.ini` for
  `pytest-homeassistant-custom-component`'s `hass` fixture to work
- `pythonpath = .` may be needed in `pytest.ini` for `custom_components`
  imports to resolve from `tests/`, depending on pytest/dependency
  versions
- `hass.http` is `None` in the bare test `hass` fixture — tests that
  exercise `async_setup_entry` static-path registration must
  `await async_setup_component(hass, "http", {})` first
- Time-zone in tests: `dt_util.now()` follows the harness-set default
  timezone, NOT a plain `hass.config.time_zone = "..."` assignment — tests
  must use `await hass.config.async_set_time_zone(...)`
- On config-entry unload in current HA, entities with live registry
  entries become `unavailable` + `restored=True`, not deleted — assert
  `state == "unavailable"`, not `is None`
- PyPI's `homeassistant` package can lag behind HA's dated Core/HAOS
  releases by weeks — always confirm actual installed versions on both
  dev and real instances rather than assuming they match