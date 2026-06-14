"""Briefing synthesis — turn the raw data we already collect into a single,
plain-language "lease condition" verdict per area.

This is OysterHealth's core differentiator and the deliberate fix for the
predecessor app's failure mode (a wall of charts with no interpretation). The
output is a *briefing*: one composite verdict, a human sentence explaining it,
a short recommendation, and a row of interpreted drivers. Charts become
supporting evidence in the UI, not the headline.

The briefing is persisted as an `area_indicators` row with
indicator='oyster_condition' so the map and detail endpoints can serve it
cheaply (same pattern as freshwater_intrusion). The full driver breakdown and
narrative live in the `components` JSONB column.

Everything here is rule-based and deterministic — no model calls — so a verdict
is explainable ("why is this CAUTION?") straight from the components.

Oyster-drill ecology note
--------------------------
Oyster drills (southern *Stramonita haemastoma*, Atlantic *Urosalpinx
cinerea*) are marine snails that prey on oysters but cannot tolerate low
salinity. Freshwater pulses suppress/kill them; sustained high salinity + warm
water drives peak predation. The plan calls out a WATCH state that *predicts
rebound*: when a freshwater event recedes and salinity climbs back with warm
water, drills return. We model that explicitly below.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg

from settings import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (kept as named constants so the rationale is legible and tuning
# doesn't mean editing rule code).
# ---------------------------------------------------------------------------

# Oyster drill salinity response (psu)
DRILL_INTOLERANT_PSU = 10.0    # below this drills cannot persist
DRILL_ACTIVE_PSU     = 15.0    # drills active above this
DRILL_PEAK_PSU       = 22.0    # peak predation when also warm
DRILL_WARM_C         = 20.0    # temperature above which drill activity is high

# Oyster suitability (psu) — prolonged extremes stress the oysters themselves
OYSTER_LOW_PSU       = 5.0     # below: prolonged freshwater kill risk
OYSTER_HIGH_PSU      = 30.0    # above: hypersaline stress + disease/predation

# Heat stress / low-DO / Vibrio concern
HEAT_STRESS_C        = 32.0

# Turbidity (Kd_490, m^-1): clear water ~0.1–0.3; >0.5 murky
TURBIDITY_MURKY      = 0.5

# How recent a station reading must be to count as "current" (days)
SIGNAL_FRESHNESS_DAYS = 14

# Distance beyond which an in-situ reading can't represent the area (plan §6)
DISTANCE_WARNING_M = 15_000

GOOD, CAUTION, POOR, UNKNOWN = "good", "caution", "poor", "unknown"
_SEVERITY = {GOOD: 0, UNKNOWN: 1, CAUTION: 2, POOR: 3}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Driver:
    key: str
    label: str
    status: str                  # good | caution | poor | unknown (favorability for the harvester)
    headline: str                # short status word, e.g. "Low", "Active", "8.1 psu"
    detail: str                  # one-sentence plain-language interpretation
    direction: str | None = None  # up | down | steady | None
    value: float | None = None
    units: str | None = None
    confidence: str | None = None  # measured | estimated | None


@dataclass
class Briefing:
    slug: str
    name: str
    verdict: str                 # good | caution | poor | unknown
    headline: str                # the synthesized human sentence(s)
    recommendation: str
    drivers: list[Driver] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)  # {available, total}
    # Forward-looking "Outlook" — a freshwater forecast driver kept SEPARATE from
    # `drivers` so it never feeds the verdict rollup or the coverage count. It
    # informs ("pulse incoming"), it doesn't recolor today's conditions.
    forecast: Driver | None = None

    def to_components(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "recommendation": self.recommendation,
            "coverage": self.coverage,
            "drivers": [asdict(d) for d in self.drivers],
            "forecast": asdict(self.forecast) if self.forecast else None,
        }


# ---------------------------------------------------------------------------
# Driver assessment (pure functions — easy to unit test)
# ---------------------------------------------------------------------------

def assess_oyster_drill(
    salinity: float | None,
    temp: float | None,
    freshwater_status: str | None,
    salinity_confidence: str | None,
) -> Driver:
    """Provisional drill-risk model from salinity, temperature, and the
    freshwater-intrusion state. `status` here is favorability *for the
    harvester*: LOW drill pressure = good, WATCH = caution, HIGH = poor.

    Confidence is `estimated` until CMEMS modeled salinity lands (plan Phase 2);
    a nearby station salinity reading promotes it to `measured`.
    """
    warm = temp is not None and temp >= DRILL_WARM_C

    # --- No salinity: infer from the freshwater state where we can. ---
    if salinity is None:
        if freshwater_status == "active_intrusion":
            return Driver(
                "oyster_drill", "Oyster drill risk", GOOD, "Low",
                "An active freshwater pulse suppresses drills, which can't tolerate low salinity.",
                direction="down", confidence="estimated",
            )
        if freshwater_status == "receding":
            return Driver(
                "oyster_drill", "Oyster drill risk", CAUTION, "Watch — rebound",
                "Freshwater is receding and salinity is climbing back; drills typically rebound within days.",
                direction="up", confidence="estimated",
            )
        if freshwater_status == "drought":
            return Driver(
                "oyster_drill", "Oyster drill risk", CAUTION, "Watch",
                "Low river flow lets salinity rise, favoring drill predation.",
                direction="up", confidence="estimated",
            )
        return Driver(
            "oyster_drill", "Oyster drill risk", UNKNOWN, "No data",
            "Need a nearby salinity reading (or modeled salinity) to estimate drill pressure.",
            confidence=None,
        )

    conf = salinity_confidence or "measured"

    # --- Salinity known: map to base risk. ---
    if salinity < DRILL_INTOLERANT_PSU:
        base = (GOOD, "Low",
                f"Salinity is {salinity:.0f} psu — too fresh for drills to persist.", "down")
    elif salinity < DRILL_ACTIVE_PSU:
        if warm:
            base = (CAUTION, "Watch",
                    f"Salinity {salinity:.0f} psu with warm water — drills becoming active.", "up")
        else:
            base = (GOOD, "Low",
                    f"Salinity {salinity:.0f} psu and cool — drill activity stays low.", "steady")
    elif salinity < DRILL_PEAK_PSU:
        base = (CAUTION, "Watch",
                f"Salinity {salinity:.0f} psu — drills are active in the bay.", "steady")
    else:  # >= peak
        if warm:
            base = (POOR, "High",
                    f"Salinity {salinity:.0f} psu and {temp:.0f}°C — peak drill predation conditions.", "up")
        else:
            base = (CAUTION, "Watch",
                    f"Salinity {salinity:.0f} psu but cool water keeps drill activity in check.", "steady")

    status, headline, detail, direction = base

    # --- Rebound override: receding + warm bumps a quiet bay to WATCH. ---
    if freshwater_status == "receding" and warm and _SEVERITY[status] < _SEVERITY[CAUTION]:
        return Driver(
            "oyster_drill", "Oyster drill risk", CAUTION, "Watch — rebound",
            f"Salinity {salinity:.0f} psu and rising as freshwater recedes; expect drill pressure to climb.",
            direction="up", value=round(salinity, 1), units="psu", confidence=conf,
        )

    return Driver("oyster_drill", "Oyster drill risk", status, headline, detail,
                  direction=direction, value=round(salinity, 1), units="psu", confidence=conf)


def assess_freshwater(fw_status: str | None, ratio: float | None, river: str | None) -> Driver:
    river_txt = f"the {river}" if river else "the upstream river"
    if fw_status == "active_intrusion":
        return Driver("freshwater", "Freshwater intrusion", CAUTION, "Active",
                      f"A discharge pulse from {river_txt} is pushing salinity down across the bay.",
                      direction="down", value=round(ratio, 2) if ratio else None, units="× baseline")
    if fw_status == "receding":
        return Driver("freshwater", "Freshwater intrusion", CAUTION, "Receding",
                      f"The pulse from {river_txt} is easing; salinity is climbing back toward normal.",
                      direction="up", value=round(ratio, 2) if ratio else None, units="× baseline")
    if fw_status == "drought":
        return Driver("freshwater", "Freshwater intrusion", CAUTION, "Low flow",
                      f"{_cap(river_txt)} is running well below normal — salinity is rising.",
                      direction="up", value=round(ratio, 2) if ratio else None, units="× baseline")
    if fw_status == "normal":
        return Driver("freshwater", "Freshwater intrusion", GOOD, "Normal",
                      f"{_cap(river_txt)} discharge is near its 30-day baseline.",
                      direction="steady", value=round(ratio, 2) if ratio else None, units="× baseline")
    return Driver("freshwater", "Freshwater intrusion", UNKNOWN, "No data",
                  "No linked river gauge with enough history to judge freshwater inflow.")


def assess_salinity(salinity: float | None, confidence: str | None) -> Driver:
    if salinity is None:
        return Driver("salinity", "Salinity", UNKNOWN, "No data",
                      "No nearby salinity station reporting; salinity from satellite/model is Phase 2.")
    if salinity < OYSTER_LOW_PSU:
        return Driver("salinity", "Salinity", CAUTION, f"{salinity:.1f} psu",
                      f"At {salinity:.1f} psu, prolonged freshwater can stress or kill oysters.",
                      value=round(salinity, 1), units="psu", confidence=confidence)
    if salinity > OYSTER_HIGH_PSU:
        return Driver("salinity", "Salinity", CAUTION, f"{salinity:.1f} psu",
                      f"At {salinity:.1f} psu the bay is hypersaline — disease and predation pressure rise.",
                      value=round(salinity, 1), units="psu", confidence=confidence)
    return Driver("salinity", "Salinity", GOOD, f"{salinity:.1f} psu",
                  f"{salinity:.1f} psu is within the healthy range for oysters.",
                  value=round(salinity, 1), units="psu", confidence=confidence)


def assess_water_temp(temp: float | None, confidence: str | None) -> Driver:
    if temp is None:
        return Driver("water_temp", "Water temperature", UNKNOWN, "No data",
                      "No nearby station reporting water temperature.")
    if temp >= HEAT_STRESS_C:
        return Driver("water_temp", "Water temperature", CAUTION, f"{temp:.1f}°C",
                      f"{temp:.1f}°C is in the heat-stress range — watch for low oxygen and Vibrio.",
                      value=round(temp, 1), units="°C", confidence=confidence)
    return Driver("water_temp", "Water temperature", GOOD, f"{temp:.1f}°C",
                  f"{temp:.1f}°C is within a normal range for the bay.",
                  value=round(temp, 1), units="°C", confidence=confidence)


def assess_turbidity(latest: float | None, baseline_mean: float | None) -> Driver:
    if latest is None:
        return Driver("turbidity", "Turbidity", UNKNOWN, "No data",
                      "No recent satellite turbidity (Kd_490) for this area.")
    # Estuaries are naturally turbid, so an absolute threshold flags everything.
    # Flag only water that is markedly murkier than this bay's own recent norm
    # (a runoff pulse or bloom), gated by an absolute floor to ignore clear-water
    # noise.
    elevated = (
        latest > TURBIDITY_MURKY
        and baseline_mean is not None and baseline_mean > 0
        and latest > 1.5 * baseline_mean
    )
    if elevated:
        return Driver("turbidity", "Turbidity", CAUTION, "Elevated",
                      f"Kd_490 {latest:.2f} m⁻¹ — markedly murkier than this bay's recent "
                      f"norm ({baseline_mean:.2f}); often a runoff pulse or bloom.",
                      direction="up", value=round(latest, 2), units="m⁻¹")
    return Driver("turbidity", "Turbidity", GOOD, "Normal",
                  f"Kd_490 {latest:.2f} m⁻¹ — near this bay's recent norm.",
                  value=round(latest, 2), units="m⁻¹")


def assess_hab(alert_level: str | None, species: str | None) -> Driver:
    sp = f" ({species})" if species else ""
    if alert_level == "closed":
        return Driver("hab", "Harmful algal bloom", POOR, "Closure",
                      f"A harvest closure is in effect for a bloom{sp} overlapping this area.")
    if alert_level == "warning":
        return Driver("hab", "Harmful algal bloom", POOR, "Warning",
                      f"A bloom warning{sp} overlaps this area — check state closures before harvesting.")
    if alert_level == "watch":
        return Driver("hab", "Harmful algal bloom", CAUTION, "Watch",
                      f"A bloom watch{sp} is posted near this area.")
    return Driver("hab", "Harmful algal bloom", GOOD, "None",
                  "No active harmful algal bloom alerts overlap this area.")


def _cap(s: str) -> str:
    """Capitalize only the first letter (str.capitalize lowercases the rest,
    which mangles proper river names like 'the Atchafalaya')."""
    return s[:1].upper() + s[1:] if s else s


def _human_lead(hours: int | None) -> str | None:
    """Human lead time. 'now' when essentially immediate — i.e. the river is
    already at/over the threshold rather than a future arrival."""
    if hours is None:
        return None
    if hours < 12:
        return "now"
    if hours < 36:
        return "~1 day"
    return f"~{round(hours / 24)} days"


def assess_freshwater_forecast(status: str | None, comp: dict, river: str | None) -> Driver | None:
    """Forward 'Outlook' driver from the freshwater_forecast indicator. Returns
    None when there's no usable forecast (no linked gauge / no NWM reach) so the
    Outlook section simply doesn't appear rather than crying 'no data'. This
    driver is attached to Briefing.forecast, NOT the drivers list, so it never
    feeds the verdict rollup."""
    if not status or status == "unknown":
        return None
    river_txt = f"the {river}" if river else "the upstream river"
    peak = comp.get("peak_ratio")
    low = comp.get("min_ratio")
    horizon_days = round((comp.get("horizon_hours") or 240) / 24)

    if status == "pulse_incoming":
        lead = _human_lead(comp.get("pulse_in_hours"))
        head = "Pulse arriving" if lead in (None, "now") else f"Pulse in {lead}"
        when = "is arriving now" if lead in (None, "now") else f"is forecast to arrive in {lead}"
        return Driver(
            "freshwater_forecast", "Freshwater outlook", CAUTION, head,
            f"A freshwater pulse from {river_txt} {when} "
            f"(peaking near {peak:g}× normal flow). Expect salinity to drop across the "
            f"bay — drill pressure eases, but watch for low-salinity stress if it's large or prolonged.",
            direction="down", value=peak, units="× normal flow",
        )
    if status == "low_flow_building":
        lead = _human_lead(comp.get("lowflow_in_hours"))
        if lead in (None, "now"):
            head = "Low flow"
            when = f"is already running low (~{low:g}× normal) and is forecast to stay down"
        else:
            head = f"Low flow in {lead}"
            when = f"is forecast to fall toward drought levels in {lead} (down to ~{low:g}× normal)"
        return Driver(
            "freshwater_forecast", "Freshwater outlook", CAUTION, head,
            f"{_cap(river_txt)} flow {when} — salinity will rise; "
            f"drill predation pressure may build.",
            direction="up", value=low, units="× normal flow",
        )
    if status == "rising":
        return Driver(
            "freshwater_forecast", "Freshwater outlook", GOOD, "Rising",
            f"{_cap(river_txt)} flow is forecast to rise modestly over the next days "
            f"(to ~{peak:g}× normal); a mild freshening is likely.",
            direction="down", value=peak, units="× normal flow",
        )
    if status == "falling":
        return Driver(
            "freshwater_forecast", "Freshwater outlook", GOOD, "Easing",
            f"{_cap(river_txt)} flow is forecast to ease over the next days "
            f"(to ~{low:g}× normal); salinity may edge up.",
            direction="up", value=low, units="× normal flow",
        )
    # steady
    return Driver(
        "freshwater_forecast", "Freshwater outlook", GOOD, "Steady",
        f"No significant change in {river_txt} inflow is forecast over the next ~{horizon_days} days.",
        direction="steady",
    )


# ---------------------------------------------------------------------------
# Composite verdict + narrative
# ---------------------------------------------------------------------------

# Only these drivers can drive the *composite* verdict all the way to POOR — a
# "can't / shouldn't harvest" signal. A HAB closure/warning qualifies. High
# oyster-drill pressure is a serious concern for the bed, but you can still work
# the lease, so it shows as a red driver yet caps the composite at CAUTION.
POOR_CAPABLE_DRIVERS = {"hab"}

# Drivers serious enough to flag the whole area CAUTION on their own. A lone
# *secondary* caution — notably the near-ubiquitous modeled-salinity drill
# "Watch" in summer — needs corroboration (a second concern) before it paints a
# bay amber, otherwise every bay looks the same and the map says nothing.
PRIMARY_CAUTION_DRIVERS = {"hab", "freshwater", "turbidity", "water_temp"}


def _rollup_verdict(drivers: list[Driver]) -> str:
    known = [d for d in drivers if d.status != UNKNOWN]
    if not known:
        return UNKNOWN
    # A closure-class driver (HAB) at POOR makes the whole area POOR.
    if any(d.status == POOR for d in known if d.key in POOR_CAPABLE_DRIVERS):
        return POOR
    concerns = [d for d in known if _SEVERITY[d.status] >= _SEVERITY[CAUTION]]
    flagging = [
        d for d in concerns
        if d.status == POOR or d.key in PRIMARY_CAUTION_DRIVERS
    ]
    if flagging or len(concerns) >= 2:
        return CAUTION
    return GOOD


# Order in which drivers get to "speak" in the headline sentence.
_NARRATIVE_PRIORITY = ["hab", "oyster_drill", "freshwater", "salinity", "water_temp", "turbidity"]


def _headline(verdict: str, drivers: list[Driver]) -> str:
    if verdict == UNKNOWN:
        return "Not enough current data to assess conditions for this area yet."
    by_key = {d.key: d for d in drivers}
    # A GOOD verdict shouldn't lead with a scary driver sentence. If a benign
    # drill "Watch" is the only flag, say so honestly without crying wolf.
    if verdict == GOOD:
        drill = by_key.get("oyster_drill")
        if drill and drill.status == CAUTION:
            return ("Conditions look favorable overall — oyster drills are present "
                    "at normal summer levels, but nothing else is flagged.")
        return "Conditions look favorable — salinity, temperature, and water clarity are all in good ranges."
    salient = [
        by_key[k] for k in _NARRATIVE_PRIORITY
        if k in by_key and by_key[k].status in (CAUTION, POOR)
    ]
    if not salient:
        return "Conditions look favorable for this area right now."
    # Lead with the two most salient drivers' detail sentences.
    return " ".join(d.detail for d in salient[:2])


def _recommendation(verdict: str, drivers: list[Driver]) -> str:
    by_key = {d.key: d for d in drivers}
    hab = by_key.get("hab")
    drill = by_key.get("oyster_drill")
    if hab and hab.status == POOR:
        return "Confirm state shellfish-harvest closures before working this area."
    if verdict == POOR:
        return "Conditions are poor for harvest right now — hold off and re-check in a day or two."
    if drill and drill.status == POOR:
        return "Oyster-drill predation is high — work vulnerable beds soon and inspect set oysters for drill damage."
    if drill and drill.status == CAUTION and "rebound" in drill.headline.lower():
        return "Drill pressure is set to rebound — prioritize harvesting vulnerable beds while salinity is still low."
    if verdict == CAUTION:
        return "Conditions are shifting — check salinity and recent trends before setting new oysters."
    if verdict == GOOD:
        return "Conditions are favorable. No water-quality flags for this area today."
    return "Limited data for this area — treat indicators as provisional until more sources report."


# ---------------------------------------------------------------------------
# DB signal gathering (sync psycopg, mirrors indicators.py)
# ---------------------------------------------------------------------------

def _latest_freshwater(conn: psycopg.Connection, area_id: str) -> tuple[str | None, float | None]:
    row = conn.execute(
        """
        SELECT status, score
          FROM area_indicators
         WHERE area_id = %s AND indicator = 'freshwater_intrusion'
         ORDER BY computed_at DESC
         LIMIT 1
        """,
        (area_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row[0], (float(row[1]) if row[1] is not None else None)


def _latest_forecast(conn: psycopg.Connection, area_id: str) -> tuple[str | None, dict]:
    """Latest freshwater_forecast row (status + components JSONB)."""
    row = conn.execute(
        """
        SELECT status, components
          FROM area_indicators
         WHERE area_id = %s AND indicator = 'freshwater_forecast'
         ORDER BY computed_at DESC
         LIMIT 1
        """,
        (area_id,),
    ).fetchone()
    if row is None:
        return None, {}
    return row[0], (row[1] or {})


def _nearest_station_value(
    conn: psycopg.Connection, area_id: str, variable: str
) -> tuple[float | None, str | None]:
    """Latest reading of `variable` from the nearest reporting station to the
    area centroid (within the freshness window). Returns (value, confidence)
    where confidence is 'measured' if inside the 15 km trust radius else
    'estimated'.
    """
    row = conn.execute(
        f"""
        WITH a AS (SELECT geom FROM areas WHERE id = %s)
        SELECT sr.value,
               ST_DistanceSphere(
                 ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326),
                 ST_Centroid(a.geom)
               ) AS dist
          FROM station_readings sr
          JOIN stations s ON s.id = sr.station_id
          CROSS JOIN a
         WHERE sr.variable = %s
           AND sr.recorded_at >= now() - interval '{SIGNAL_FRESHNESS_DAYS} days'
         ORDER BY dist ASC, sr.recorded_at DESC
         LIMIT 1
        """,
        (area_id, variable),
    ).fetchone()
    if row is None or row[0] is None:
        return None, None
    value = float(row[0])
    dist = float(row[1]) if row[1] is not None else None
    confidence = "measured" if (dist is not None and dist <= DISTANCE_WARNING_M) else "estimated"
    return value, confidence


def _latest_snapshot(conn: psycopg.Connection, area_id: str, variable: str) -> float | None:
    row = conn.execute(
        f"""
        SELECT value_mean
          FROM data_snapshots
         WHERE area_id = %s AND variable = %s AND value_mean IS NOT NULL
           AND captured_at >= now() - interval '{SIGNAL_FRESHNESS_DAYS} days'
         ORDER BY captured_at DESC
         LIMIT 1
        """,
        (area_id, variable),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _turbidity_stats(conn: psycopg.Connection, area_id: str) -> tuple[float | None, float | None]:
    """Latest turbidity (Kd_490) and the area's 30-day mean, for a relative
    'murkier than usual' read rather than an absolute threshold."""
    row = conn.execute(
        """
        SELECT
          (SELECT value_mean FROM data_snapshots
            WHERE area_id = %s AND variable = 'turbidity' AND value_mean IS NOT NULL
            ORDER BY captured_at DESC LIMIT 1) AS latest,
          (SELECT avg(value_mean) FROM data_snapshots
            WHERE area_id = %s AND variable = 'turbidity' AND value_mean IS NOT NULL
              AND captured_at >= now() - interval '30 days') AS mean30
        """,
        (area_id, area_id),
    ).fetchone()
    latest = float(row[0]) if row and row[0] is not None else None
    mean30 = float(row[1]) if row and row[1] is not None else None
    return latest, mean30


def _active_hab(conn: psycopg.Connection, area_id: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        """
        SELECT h.alert_level, h.species
          FROM hab_alerts h
          JOIN areas a ON ST_Intersects(h.geom, a.geom)
         WHERE a.id = %s AND h.geom IS NOT NULL
           AND (h.expires_at IS NULL OR h.expires_at > now())
         ORDER BY CASE h.alert_level
                    WHEN 'closed' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END
         LIMIT 1
        """,
        (area_id,),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _dominant_river(conn: psycopg.Connection, area_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT COALESCE(g.river, g.name)
          FROM usgs_gauges g
          JOIN areas a ON g.site_no = ANY(a.linked_gauges)
         WHERE a.id = %s
         ORDER BY g.site_no
         LIMIT 1
        """,
        (area_id,),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_briefing(conn: psycopg.Connection, area_id: str, slug: str, name: str) -> Briefing:
    fw_status, fw_ratio = _latest_freshwater(conn, area_id)
    # Prefer in-situ CO-OPS salinity; fall back to CMEMS modeled salinity
    # (data_snapshots) so the drill model has a signal coast-wide.
    salinity, sal_conf = _nearest_station_value(conn, area_id, "salinity")
    if salinity is None:
        modeled = _latest_snapshot(conn, area_id, "salinity")
        if modeled is not None:
            salinity, sal_conf = modeled, "modeled"
    temp, temp_conf = _nearest_station_value(conn, area_id, "water_temperature")
    turb_latest, turb_mean = _turbidity_stats(conn, area_id)
    hab_level, hab_species = _active_hab(conn, area_id)
    river = _dominant_river(conn, area_id)

    drivers = [
        assess_oyster_drill(salinity, temp, fw_status, sal_conf),
        assess_freshwater(fw_status, fw_ratio, river),
        assess_salinity(salinity, sal_conf),
        assess_water_temp(temp, temp_conf),
        assess_turbidity(turb_latest, turb_mean),
        assess_hab(hab_level, hab_species),
    ]

    verdict = _rollup_verdict(drivers)
    available = sum(1 for d in drivers if d.status != UNKNOWN)

    # Forward-looking outlook — read the precomputed freshwater_forecast row and
    # build a separate Driver (NOT added to `drivers`, so it stays out of the
    # rollup and coverage).
    fc_status, fc_comp = _latest_forecast(conn, area_id)
    forecast = assess_freshwater_forecast(fc_status, fc_comp, river)

    return Briefing(
        slug=slug,
        name=name,
        verdict=verdict,
        headline=_headline(verdict, drivers),
        recommendation=_recommendation(verdict, drivers),
        drivers=drivers,
        coverage={"available": available, "total": len(drivers)},
        forecast=forecast,
    )


def persist(conn: psycopg.Connection, area_id: str, briefing: Briefing, computed_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO area_indicators (area_id, indicator, computed_at, status, score, components)
        VALUES (%s, 'oyster_condition', %s, %s, NULL, %s::jsonb)
        ON CONFLICT (area_id, indicator, computed_at) DO UPDATE
          SET status = EXCLUDED.status, components = EXCLUDED.components
        """,
        (area_id, computed_at, briefing.verdict, json.dumps(briefing.to_components(), default=str)),
    )


def compute_all() -> dict[str, int]:
    """Build + persist an oyster_condition briefing for every area.

    Assumes freshwater_intrusion has already been computed this cycle
    (manage.py compute-indicators runs indicators.compute_all first).
    """
    report: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        areas = conn.execute("SELECT id, slug, name FROM areas ORDER BY slug").fetchall()
        for area_id, slug, name in areas:
            briefing = build_briefing(conn, str(area_id), slug, name)
            persist(conn, str(area_id), briefing, now)
            report[briefing.verdict] = report.get(briefing.verdict, 0) + 1
    log.info("Briefing compute_all: %s", report)
    return report
