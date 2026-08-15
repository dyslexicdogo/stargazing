# Stargazing HA Integration — Project Principles
 
Scaffolded from lessons learned building `sun_bathing`
(https://github.com/dyslexicdogo/sun_bathing). This document is meant to be
pasted into a fresh chat session to carry that project's hard-won lessons
forward without re-learning them.
 
---
 
## Project concept
 
A Home Assistant custom integration scoring stargazing conditions,
inspired by the "Clear Outside" astronomy weather site. Unlike
`sun_bathing`'s fixed daytime windows, stargazing windows are **dynamic**
— tied to actual astronomical dusk/dawn for the location and season
(important given how much daylight hours swing in Scotland).
 
### Scoring factors (richer than sun_bathing's 6)
 
- Total cloud cover
- Low / mid / high cloud cover (separately — low cloud is usually most
  disruptive; high cirrus can be thin enough to partially see through)
- Visibility
- Fog %
- Dew point vs. temperature (dew risk on optics)
- Relative humidity (dew risk + atmospheric "seeing" quality)
- Surface pressure (rising = often clearing; falling = weather moving in)
- Ozone (rough transparency proxy, as Clear Outside uses it)
- Precipitation / precipitation probability
- Wind speed (telescope stability)
- Apparent temperature (comfort)
- Moon phase / illumination (bright moon washes out faint objects)
### Data sources (researched, not yet fully verified in code)
 
- **Open-Meteo** (same provider as sun_bathing) covers: cloud cover
  (total/low/mid/high), visibility, dew point, relative humidity, surface
  pressure, precipitation, wind speed, apparent temperature — all in the
  standard hourly forecast endpoint.
- **Open-Meteo Air Quality API** (separate endpoint, same provider) —
  needed for ozone.
- **Fog %** — not directly available; likely approximate from low
  visibility + high humidity rather than a dedicated field.
- **Moon phase** — Home Assistant's built-in `moon` integration
  (`sensor.moon`) gives 8 discrete phases (new_moon, waxing_crescent,
  full_moon, etc.), no percentage. If numeric illumination % is wanted
  instead, that needs the `astral` library directly (already a dependency
  of HA core, confirmed present in a working devcontainer — no new pip
  install required) rather than HA's built-in moon sensor.
- **Astronomical dusk/dawn** — HA's `sun.dawn`/`sun.dusk` **triggers**
  natively support a `type: astronomical` option (18° below horizon,
  "sky fully dark"), confirmed via official HA docs. These are trigger
  definitions, not directly-readable entity attributes — the underlying
  calculation is likely exposed via a helper function
  (`homeassistant.helpers.sun.get_astral_event_date(hass, "astronomical_dusk")`
  or similar — **verify the exact function name/signature/import path
  against the actual installed HA core version before writing code
  against it**, rather than assuming from this note).
### Open design decisions still to make
 
- 8-phase discrete moon vs. numeric illumination % (trade-off: simplicity
  vs. precision — discrete needs no `astral` calls, percentage does)
- Whether to include ozone from day one or defer it
- Exact scoring weights/formula per factor (likely reuse sun_bathing's
  centered soft-gradient approach, but with more factors to balance)
---
 
## Learning-by-doing: phase sequence
 
1. **Environment** — devcontainer, `scripts/setup`/`scripts/develop`,
   confirm a skeleton integration loads in HA before anything else
2. **Pure scoring logic first** — dataclasses + `calculate_score()`
   equivalent, zero HA imports, unit-testable in complete isolation
   before any API/HA plumbing exists at all
3. **Dumb API client** — Open-Meteo (+ Air Quality endpoint if ozone is
   included) wrapper, no domain knowledge, tested with `aioresponses`
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
  `StaticPathConfig`, `OptionsFlow.config_entry`, and today's
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
  `entry.async_on_unload`
### Keep a living project doc from day one
- Update `project_summary.md` as you go, right after each real bug/lesson
  — cheaper to write two sentences immediately than reconstruct the story
  weeks later (as had to be done retroactively for sun_bathing)
---
 
## Known HA API gotchas to remember (sun_bathing history)
 
- `hass.http.async_register_static_paths()` wants a list of
  `StaticPathConfig` **objects**, not plain dicts
- `OptionsFlow.config_entry` is a **read-only property** in current HA
  core — don't assign it in `__init__`; the base class populates it
  automatically
- `async_track_time_change`'s callback should be a plain `async def`
  function — wrapping it in `hass.async_create_task(...)` via a lambda
  triggers a thread-safety violation
- Custom Lovelace cards **must** live inside
  `custom_components/<domain>/www/`, never the repo root — HACS
  "Integration" category repos only download `custom_components/`
- `asyncio_mode = auto` (not `strict`) needed in `pytest.ini` for
  `pytest-homeassistant-custom-component`'s `hass` fixture to work
- `pythonpath = .` may be needed in `pytest.ini` for `custom_components`
  imports to resolve from `tests/`, depending on pytest/dependency
  versions
- PyPI's `homeassistant` package can lag behind HA's dated Core/HAOS
  releases by weeks — always confirm actual installed versions on both
  dev and real instances rather than assuming they match
