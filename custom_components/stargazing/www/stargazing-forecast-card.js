/**
 * stargazing-forecast-card
 *
 * Graphical 3-night forecast: one SVG chart showing every scored hour
 * across tonight / tomorrow night / night+2 on a shared (categorical,
 * not wall-clock-proportional) time axis, total score only. Tapping a
 * point selects that hour and reveals its full nine-factor breakdown
 * below the chart. See README.md's "Lovelace cards" section for the
 * design spec this implements.
 *
 * Reads three sensors' `forecast` attribute (see sensor.py's
 * _forecast_entry()) -- each a list of {time, score, <9 factors>} for
 * that night's scored hours -- plus `window_start`/`window_end` for the
 * per-night summary line. Deliberately dumb: no scoring/business logic
 * here, just rendering what the sensors already computed (same
 * "coordinator/sensors do the work, card just displays it" principle
 * as the rest of this project).
 */

const NIGHT_LABELS = ["Tonight", "Tomorrow Night", "Night +2"];

// Order matches score.py's ScoreBreakdown fields / sensor.py's
// _breakdown_dict() keys, so a missing/renamed factor is obvious.
// `unit` is the raw-reading unit shown in the selected-hour panel; the
// dew-point spread is already a derived delta, cloud/precip/illumination
// are percentages, visibility is metres (displayed as km), and the wind
// fields keep their source units (m/s jet-stream vs km/h surface).
const FACTORS = [
  { key: "low_cloud", icon: "☁", label: "Low", unit: "%" },
  { key: "mid_cloud", icon: "☁", label: "Mid", unit: "%" },
  { key: "high_cloud", icon: "☁", label: "High", unit: "%" },
  { key: "visibility", icon: "👁", label: "Vis", unit: "m" },
  { key: "jet_stream_wind", icon: "✈", label: "Jet", unit: "m/s" },
  { key: "moon_illumination", icon: "🌙", label: "Moon", unit: "%" },
  { key: "precipitation_probability", icon: "💧", label: "Precip", unit: "%" },
  { key: "wind_speed", icon: "💨", label: "Wind", unit: "km/h" },
  { key: "dew_point_spread", icon: "☀", label: "Dew", unit: "°C" },
];

// Gap (in "hour slots") drawn between each night's cluster of points,
// so nights read as visually distinct groups on the shared axis rather
// than one continuous (and misleadingly time-proportional) line.
const NIGHT_GAP_SLOTS = 2;

const CHART_WIDTH = 700;
const CHART_HEIGHT = 220;
const CHART_PAD_LEFT = 34;
const CHART_PAD_RIGHT = 12;
const CHART_PAD_TOP = 16;
const CHART_PAD_BOTTOM = 28;

class StargazingForecastCard extends HTMLElement {
  constructor() {
    super();
    // {nightIndex, hourIndex} of the currently-selected point, or null
    // until the first render picks a default (tonight's peak).
    this._selected = null;
  }

  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 6;
  }

  // -- data -----------------------------------------------------------

  _entityIds() {
    return [
      this._config.entity_tonight || "sensor.stargazing_tonight",
      this._config.entity_tomorrow || "sensor.stargazing_tomorrow_night",
      this._config.entity_night2 || "sensor.stargazing_in_two_nights",
    ];
  }

  _nights() {
    return this._entityIds().map((entityId, index) => {
      const state = this._hass.states[entityId];
      if (!state) {
        return { label: NIGHT_LABELS[index], missing: true };
      }
      const attrs = state.attributes || {};
      const forecast = Array.isArray(attrs.forecast) ? attrs.forecast : [];
      return {
        label: NIGHT_LABELS[index],
        missing: false,
        nightOf: attrs.night_of || null,
        windowStart: attrs.window_start || null,
        windowEnd: attrs.window_end || null,
        peak: state.state === "unknown" || state.state === "unavailable"
          ? null
          : Number(state.state),
        forecast,
      };
    });
  }

  // -- formatting helpers ----------------------------------------------

  _hhmm(isoString) {
    // Works for both naive ("2026-01-15T20:00:00") and tz-aware-with-
    // offset ("2026-01-15T18:30:19.314495+00:00") ISO strings -- both
    // put HH:MM right after the "T", which is all a display label needs.
    if (!isoString) return "--:--";
    const tIndex = isoString.indexOf("T");
    if (tIndex === -1) return "--:--";
    return isoString.slice(tIndex + 1, tIndex + 6);
  }

  _dateLabel(isoDateString) {
    if (!isoDateString) return "";
    const [y, m, d] = isoDateString.split("-").map(Number);
    const date = new Date(y, (m || 1) - 1, d || 1);
    return date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  // Format a factor's raw reading for the selected-hour panel, e.g.
  // "10%", "20 km", "12 m/s". Visibility arrives in metres (20000) but
  // reads far better as km; everything else is a plain rounded number
  // plus its unit from FACTORS. Falls back to "—" when the raw value is
  // missing (e.g. an older sensor still emitting score-only entries).
  _fmtFactorRaw(factor, rawValue) {
    if (rawValue === null || rawValue === undefined || Number.isNaN(rawValue)) {
      return "—";
    }
    if (factor.key === "visibility") {
      const km = rawValue / 1000;
      const text = km >= 100 || Number.isInteger(km) ? String(Math.round(km)) : km.toFixed(1);
      return `${text} km`;
    }
    return `${Math.round(rawValue)}${factor.unit}`;
  }

  _headerRange(nights) {
    const withDates = nights.filter((n) => !n.missing && n.nightOf);
    if (withDates.length === 0) return "Stargazing Forecast";
    const first = this._dateLabel(withDates[0].nightOf);
    const last = this._dateLabel(withDates[withDates.length - 1].nightOf);
    return first === last
      ? `Stargazing Forecast · ${first}`
      : `Stargazing Forecast · ${first} – ${last}`;
  }

  // -- chart geometry ----------------------------------------------------

  /**
   * Lays out every scored hour across all nights as {x, y, nightIndex,
   * hourIndex, hour} points on a shared categorical axis: hours within
   * a night sit at consecutive integer slots, nights are separated by
   * NIGHT_GAP_SLOTS empty slots. Real wall-clock spacing is deliberately
   * not attempted -- the daytime gap between nights would dwarf each
   * night's few dark hours and make the chart unreadable.
   */
  _layout(nights) {
    let slot = 0;
    const points = [];
    const nightSlotRanges = []; // for drawing each night's label span

    nights.forEach((night, nightIndex) => {
      const startSlot = slot;
      if (!night.missing && night.forecast.length > 0) {
        night.forecast.forEach((hour, hourIndex) => {
          points.push({ slot, nightIndex, hourIndex, hour });
          slot += 1;
        });
      } else {
        slot += 1; // reserve a slot so the "no window" label has a home
      }
      nightSlotRanges.push({ nightIndex, startSlot, endSlot: slot - 1 });
      slot += NIGHT_GAP_SLOTS;
    });

    const maxSlot = Math.max(slot - NIGHT_GAP_SLOTS - 1, 1);
    const plotWidth = CHART_WIDTH - CHART_PAD_LEFT - CHART_PAD_RIGHT;
    const plotHeight = CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM;

    const xFor = (s) => CHART_PAD_LEFT + (s / maxSlot) * plotWidth;
    const yFor = (score) => CHART_PAD_TOP + (1 - score / 100) * plotHeight;

    points.forEach((p) => {
      p.x = xFor(p.slot);
      p.y = yFor(p.hour.score);
    });

    nightSlotRanges.forEach((r) => {
      r.xStart = xFor(r.startSlot);
      r.xEnd = xFor(r.endSlot);
      r.xCenter = (r.xStart + r.xEnd) / 2;
    });

    return { points, nightSlotRanges, xFor, yFor };
  }

  // -- selection ---------------------------------------------------------

  _defaultSelection(nights) {
    // Tonight's peak hour, per the README spec ("defaults to tonight's
    // peak"). Falls back to the first night with any scored hours if
    // tonight has none (e.g. no darkness window tonight specifically).
    for (let nightIndex = 0; nightIndex < nights.length; nightIndex += 1) {
      const forecast = nights[nightIndex].forecast;
      if (!forecast || forecast.length === 0) continue;
      let bestIndex = 0;
      forecast.forEach((hour, hourIndex) => {
        if (hour.score > forecast[bestIndex].score) bestIndex = hourIndex;
      });
      return { nightIndex, hourIndex: bestIndex };
    }
    return null;
  }

  _selectedHour(nights) {
    if (!this._selected) return null;
    const night = nights[this._selected.nightIndex];
    if (!night || night.missing) return null;
    return night.forecast[this._selected.hourIndex] || null;
  }

  // -- render --------------------------------------------------------------

  _render() {
    if (!this._hass || !this._config) return;

    const nights = this._nights();
    if (this._selected === null) {
      this._selected = this._defaultSelection(nights);
    }

    const { points, nightSlotRanges, yFor } = this._layout(nights);
    const selectedHour = this._selectedHour(nights);

    this.innerHTML = `
      <ha-card style="padding:16px">
        <div style="font-size:15px;font-weight:600;margin-bottom:10px">
          ${this._headerRange(nights)}
        </div>
        ${this._renderChart(nights, points, nightSlotRanges, yFor)}
        ${this._renderNightSummaries(nights)}
        ${this._renderSelectedPanel(selectedHour)}
      </ha-card>
    `;

    this.querySelectorAll("circle[data-night][data-hour]").forEach((el) => {
      el.addEventListener("click", () => {
        this._selected = {
          nightIndex: Number(el.dataset.night),
          hourIndex: Number(el.dataset.hour),
        };
        this._render();
      });
    });
  }

  _renderChart(nights, points, nightSlotRanges, yFor) {
    const gridLines = [0, 40, 70, 100]
      .map((score) => {
        const y = yFor(score);
        return `
          <line x1="${CHART_PAD_LEFT}" y1="${y}" x2="${CHART_WIDTH - CHART_PAD_RIGHT}" y2="${y}"
                stroke="var(--divider-color, #444)" stroke-width="1" opacity="0.4" />
          <text x="2" y="${y + 4}" font-size="11" fill="var(--secondary-text-color)">${score}</text>
        `;
      })
      .join("");

    const nightLines = nights
      .map((night, nightIndex) => {
        const nightPoints = points.filter((p) => p.nightIndex === nightIndex);
        if (nightPoints.length === 0) return "";
        const d = nightPoints.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
        return `<path d="${d}" fill="none" stroke="var(--primary-color)" stroke-width="2" opacity="0.85" />`;
      })
      .join("");

    const dots = points
      .map((p) => {
        const isSelected =
          this._selected &&
          this._selected.nightIndex === p.nightIndex &&
          this._selected.hourIndex === p.hourIndex;
        const r = isSelected ? 6 : 4;
        const fill = isSelected ? "var(--accent-color, #ffb300)" : "var(--primary-color)";
        return `<circle cx="${p.x}" cy="${p.y}" r="${r}" fill="${fill}"
                        stroke="var(--card-background-color, #1c1c1c)" stroke-width="1.5"
                        data-night="${p.nightIndex}" data-hour="${p.hourIndex}"
                        style="cursor:pointer" />`;
      })
      .join("");

    const nightLabels = nightSlotRanges
      .map((r) => {
        const night = nights[r.nightIndex];
        if (!night) return "";
        if (night.missing || night.forecast.length === 0) {
          return `
            <text x="${r.xCenter}" y="${CHART_PAD_TOP + 12}" font-size="11" text-anchor="middle"
                  fill="var(--secondary-text-color)" opacity="0.6">no darkness window</text>`;
        }
        const peakLabel = night.peak !== null ? Math.round(night.peak) : "--";
        return `
          <text x="${r.xCenter}" y="${CHART_PAD_TOP - 2}" font-size="12" font-weight="600"
                text-anchor="middle" fill="var(--primary-text-color)">${night.label} · ${peakLabel}</text>`;
      })
      .join("");

    const dimmedBands = nightSlotRanges
      .map((r) => {
        const night = nights[r.nightIndex];
        if (!night || (!night.missing && night.forecast.length > 0)) return "";
        return `<rect x="${r.xStart - 8}" y="${CHART_PAD_TOP}" width="${r.xEnd - r.xStart + 16}"
                      height="${CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM}"
                      fill="var(--secondary-text-color)" opacity="0.06" />`;
      })
      .join("");

    return `
      <svg viewBox="0 0 ${CHART_WIDTH} ${CHART_HEIGHT}" style="width:100%;height:auto;display:block">
        ${dimmedBands}
        ${gridLines}
        ${nightLines}
        ${nightLabels}
        ${dots}
      </svg>
    `;
  }

  _renderNightSummaries(nights) {
    const rows = nights
      .map((night) => {
        if (night.missing) {
          return `<div style="color:var(--secondary-text-color)">${night.label} · sensor unavailable</div>`;
        }
        if (!night.windowStart || night.forecast.length === 0) {
          return `<div style="color:var(--secondary-text-color)">${night.label} · no darkness window</div>`;
        }
        const peakLabel = night.peak !== null ? Math.round(night.peak) : "--";
        return `
          <div>
            <span style="font-weight:600">${night.label}</span>
            <span style="color:var(--secondary-text-color)">
              ${this._hhmm(night.windowStart)}–${this._hhmm(night.windowEnd)} · peak ${peakLabel}
            </span>
          </div>`;
      })
      .join("");

    return `
      <div style="display:flex;flex-direction:column;gap:4px;font-size:13px;margin-top:10px;
                  padding-top:10px;border-top:1px solid var(--divider-color, #444)">
        ${rows}
      </div>
    `;
  }

  _renderSelectedPanel(selectedHour) {
    if (!selectedHour) {
      return `
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--divider-color, #444);
                    color:var(--secondary-text-color);font-size:13px">
          No scored hour available -- every night's darkness window is empty right now.
        </div>
      `;
    }

    const factorChips = FACTORS.map((f) => {
      const score = Math.round(selectedHour[f.key]);
      const raw = selectedHour.raw ? selectedHour.raw[f.key] : undefined;
      const reading = this._fmtFactorRaw(f, raw);
      return `
        <span style="margin-right:12px;white-space:nowrap">
          ${f.icon} ${f.label} ${reading} (${score})
        </span>`;
    }).join("");

    return `
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--divider-color, #444);font-size:13px">
        <div style="margin-bottom:6px">
          <span style="font-weight:600">Selected: ${this._hhmm(selectedHour.time)}</span>
          <span style="color:var(--secondary-text-color)"> · score ${Math.round(selectedHour.score)}</span>
        </div>
        <div style="display:flex;flex-wrap:wrap;color:var(--secondary-text-color)">
          ${factorChips}
        </div>
      </div>
    `;
  }
}

customElements.define("stargazing-forecast-card", StargazingForecastCard);