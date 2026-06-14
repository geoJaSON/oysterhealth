"""Composite ecosystem indicators — rule-based scoring against the data we
already collect.

Phase 1 implements the freshwater-intrusion indicator (plan §7). Oyster drill
risk (plan §6) waits on CMEMS modeled salinity in Phase 2.

Each indicator function is pure: it reads from the DB, returns a structured
result, and the calling orchestrator handles persistence. This makes them
trivial to unit-test later and easy to recompute on demand.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from settings import settings

log = logging.getLogger(__name__)

# Plan §7 thresholds. Kept as module constants so the rationale below stays
# readable, and so tweaking them doesn't require editing the rule code.
#
# ratio = current_discharge / 30-day mean discharge
ACTIVE_INTRUSION_RATIO = 1.50    # >150% of baseline → river is pulsing
DROUGHT_RATIO          = 0.50    # <50% of baseline → low-flow regime
RECEDING_BAND          = (0.80, 1.20)  # near baseline AND status was active recently

# Kd_490 threshold: typical clear water is 0.1–0.3 m⁻¹; >0.5 is murky.
# Used as an OR fallback when current turbidity exceeds the local 30-day mean.
TURBIDITY_ELEVATED_ABSOLUTE = 0.5

# How far back to look for "was recently active intrusion" → "receding" classification.
RECEDING_LOOKBACK_DAYS = 14

# Minimum days of discharge history required to compute a baseline. Below this,
# the indicator reports `unknown` rather than guessing from a tiny sample.
MIN_DAYS_FOR_BASELINE = 7


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class IndicatorResult:
    area_id: str
    indicator: str
    computed_at: datetime
    status: str
    score: float | None
    components: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Freshwater intrusion
# ---------------------------------------------------------------------------

def _discharge_stats(conn: psycopg.Connection, area_id: str) -> dict[str, Any]:
    """Pull the latest reading and the 30-day mean for the area's linked gauges.

    Multiple linked gauges are aggregated by summing latest values and means
    (matches the physical question: how much fresh water is coming in TOTAL).
    """
    cur = conn.execute(
        """
        WITH linked AS (
          SELECT g.id
            FROM areas a
            JOIN usgs_gauges g ON g.site_no = ANY(a.linked_gauges)
           WHERE a.id = %s
        ),
        latest AS (
          SELECT gauge_id, recorded_at, discharge_cfs
            FROM (
              SELECT DISTINCT ON (gauge_id) gauge_id, recorded_at, discharge_cfs
                FROM gauge_readings
               WHERE discharge_cfs IS NOT NULL
                 AND gauge_id IN (SELECT id FROM linked)
               ORDER BY gauge_id, recorded_at DESC
            ) t
        ),
        window_stats AS (
          SELECT count(*) AS reading_count,
                 count(DISTINCT date_trunc('day', recorded_at)) AS day_count,
                 avg(discharge_cfs) AS mean_30d
            FROM gauge_readings
           WHERE discharge_cfs IS NOT NULL
             AND gauge_id IN (SELECT id FROM linked)
             AND recorded_at >= now() - interval '30 days'
        )
        SELECT
          (SELECT count(*) FROM linked) AS gauge_count,
          (SELECT sum(discharge_cfs) FROM latest) AS latest_total_cfs,
          (SELECT max(recorded_at)  FROM latest) AS latest_at,
          (SELECT mean_30d FROM window_stats) AS mean_per_gauge_30d,
          (SELECT day_count FROM window_stats) AS days_of_history
        """,
        (area_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {"gauge_count": 0}

    gauge_count = row[0]
    latest_total = float(row[1]) if row[1] is not None else None
    latest_at = row[2]
    mean_per_gauge = float(row[3]) if row[3] is not None else None
    days_of_history = row[4] or 0

    # Convert per-gauge mean to area-summed mean to match latest_total
    mean_total = mean_per_gauge * gauge_count if mean_per_gauge is not None else None

    return {
        "gauge_count": gauge_count,
        "latest_total_cfs": latest_total,
        "latest_at": latest_at.isoformat() if latest_at else None,
        "mean_30d_total_cfs": mean_total,
        "days_of_history": days_of_history,
    }


def _turbidity_stats(conn: psycopg.Connection, area_id: str) -> dict[str, Any]:
    cur = conn.execute(
        """
        WITH latest AS (
          SELECT value_mean, captured_at
            FROM data_snapshots
           WHERE area_id = %s AND variable = 'turbidity'
             AND value_mean IS NOT NULL
           ORDER BY captured_at DESC
           LIMIT 1
        ),
        mean_30d AS (
          SELECT avg(value_mean) AS m
            FROM data_snapshots
           WHERE area_id = %s AND variable = 'turbidity'
             AND value_mean IS NOT NULL
             AND captured_at >= now() - interval '30 days'
        )
        SELECT
          (SELECT value_mean FROM latest)   AS latest_value,
          (SELECT captured_at FROM latest)  AS latest_at,
          (SELECT m FROM mean_30d)          AS mean_30d
        """,
        (area_id, area_id),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return {"available": False}
    return {
        "available": True,
        "latest_value": float(row[0]),
        "latest_at": row[1].isoformat() if row[1] else None,
        "mean_30d": float(row[2]) if row[2] is not None else None,
    }


def _recent_active_intrusion(conn: psycopg.Connection, area_id: str) -> bool:
    """Was freshwater_intrusion = 'active_intrusion' at any point in the last
    RECEDING_LOOKBACK_DAYS days? Used to distinguish "receding" from "normal".
    """
    # RECEDING_LOOKBACK_DAYS is an int module constant, safe to interpolate.
    cur = conn.execute(
        f"""
        SELECT 1
          FROM area_indicators
         WHERE area_id = %s
           AND indicator = 'freshwater_intrusion'
           AND status = 'active_intrusion'
           AND computed_at >= now() - interval '{RECEDING_LOOKBACK_DAYS} days'
         LIMIT 1
        """,
        (area_id,),
    )
    return cur.fetchone() is not None


def compute_freshwater_intrusion(
    conn: psycopg.Connection,
    area_id: str,
) -> IndicatorResult:
    """Apply plan §7 rules to one area.

    Status semantics:
      active_intrusion  — discharge > 150% of 30d mean AND (turbidity elevated OR unknown)
      drought           — discharge < 50% of 30d mean (low-flow regime)
      receding          — discharge back near baseline but was active in last 14 days
      normal            — discharge near baseline, no recent active episode
      unknown           — no linked gauges, or insufficient history (<7 days)

    Score: discharge ratio (current / 30-day mean), 1.0 = baseline.
    """
    now = datetime.now(timezone.utc)
    discharge = _discharge_stats(conn, area_id)
    turbidity = _turbidity_stats(conn, area_id)

    components = {
        "discharge": discharge,
        "turbidity": turbidity,
        "thresholds": {
            "active_intrusion_ratio": ACTIVE_INTRUSION_RATIO,
            "drought_ratio": DROUGHT_RATIO,
            "turbidity_elevated_absolute": TURBIDITY_ELEVATED_ABSOLUTE,
            "receding_lookback_days": RECEDING_LOOKBACK_DAYS,
            "min_days_for_baseline": MIN_DAYS_FOR_BASELINE,
        },
    }

    if discharge["gauge_count"] == 0:
        return IndicatorResult(
            area_id, "freshwater_intrusion", now,
            status="unknown",
            score=None,
            components={**components, "reason": "no_linked_gauges"},
        )

    latest = discharge.get("latest_total_cfs")
    mean_30d = discharge.get("mean_30d_total_cfs")

    if (latest is None or mean_30d is None or
            mean_30d == 0 or
            discharge["days_of_history"] < MIN_DAYS_FOR_BASELINE):
        return IndicatorResult(
            area_id, "freshwater_intrusion", now,
            status="unknown",
            score=None,
            components={**components, "reason": "insufficient_history"},
        )

    ratio = latest / mean_30d
    components["discharge_ratio"] = ratio

    # --- Drought first: low flow is unambiguous, ignore turbidity. ---
    if ratio < DROUGHT_RATIO:
        return IndicatorResult(
            area_id, "freshwater_intrusion", now,
            status="drought",
            score=ratio,
            components={**components, "reason": "discharge_below_50pct"},
        )

    # --- Active intrusion: high flow, possibly corroborated by turbidity. ---
    if ratio >= ACTIVE_INTRUSION_RATIO:
        turbidity_elevated = (
            turbidity["available"] and (
                (turbidity.get("mean_30d") is not None
                 and turbidity["latest_value"] > turbidity["mean_30d"])
                or turbidity["latest_value"] > TURBIDITY_ELEVATED_ABSOLUTE
            )
        )
        # When turbidity is unavailable we still call it active intrusion
        # rather than dropping to "unknown" — discharge alone is the dominant
        # signal in the plan's logic table.
        return IndicatorResult(
            area_id, "freshwater_intrusion", now,
            status="active_intrusion",
            score=ratio,
            components={
                **components,
                "turbidity_elevated": turbidity_elevated,
                "reason": (
                    "discharge_above_150pct_with_turbidity"
                    if turbidity_elevated
                    else "discharge_above_150pct"
                ),
            },
        )

    # --- Near baseline: receding vs. normal depends on recent history. ---
    if RECEDING_BAND[0] <= ratio <= RECEDING_BAND[1] and _recent_active_intrusion(conn, area_id):
        return IndicatorResult(
            area_id, "freshwater_intrusion", now,
            status="receding",
            score=ratio,
            components={**components, "reason": "recovering_from_active_intrusion"},
        )

    return IndicatorResult(
        area_id, "freshwater_intrusion", now,
        status="normal",
        score=ratio,
        components={**components, "reason": "within_baseline"},
    )


# ---------------------------------------------------------------------------
# Freshwater forecast (forward-looking, from NWM streamflow forecasts)
# ---------------------------------------------------------------------------
# The forward companion to freshwater_intrusion: instead of "is a pulse
# happening now", it answers "is one COMING, and when". Reads the latest NWM
# medium-range-blend trajectory (~10 days) for the area's linked gauges and
# compares the summed forecast flow to the same 30-day baseline the intrusion
# indicator uses. Persisted as indicator='freshwater_forecast'; synthesis.py
# turns it into a forward "Outlook" driver that informs but never recolors the
# current verdict.

FORECAST_RISING_RATIO = 1.20   # mild freshening, between baseline and a pulse


def _forecast_trajectory(
    conn: psycopg.Connection, area_id: str
) -> tuple[float | None, list[tuple[datetime, float]], dict[str, Any]]:
    """(baseline_total_cfs, [(valid_time, ratio)], meta) for the area's linked
    gauges, where ratio = summed forecast flow / summed 30-day-mean flow. Uses
    only valid_times where every forecasting gauge is present, for a clean sum."""
    base_rows = conn.execute(
        """
        SELECT r.gauge_id, avg(r.discharge_cfs)
          FROM gauge_readings r
          JOIN areas a ON a.id = %s
          JOIN usgs_gauges g ON g.id = r.gauge_id AND g.site_no = ANY(a.linked_gauges)
         WHERE r.discharge_cfs IS NOT NULL
           AND r.recorded_at >= now() - interval '30 days'
         GROUP BY r.gauge_id
        """,
        (area_id,),
    ).fetchall()
    baseline = {gid: float(m) for gid, m in base_rows if m is not None and m > 0}
    if not baseline:
        return None, [], {"reason": "no_baseline"}

    fc_rows = conn.execute(
        """
        WITH linked AS (
          SELECT g.id FROM areas a
            JOIN usgs_gauges g ON g.site_no = ANY(a.linked_gauges)
           WHERE a.id = %s
        ),
        latest AS (
          SELECT gauge_id, max(issued_at) AS issued
            FROM nwm_forecasts
           WHERE series = 'medium_range_blend' AND gauge_id IN (SELECT id FROM linked)
           GROUP BY gauge_id
        )
        SELECT f.gauge_id, f.valid_time, f.flow_cfs
          FROM nwm_forecasts f
          JOIN latest l ON l.gauge_id = f.gauge_id AND l.issued = f.issued_at
         WHERE f.series = 'medium_range_blend' AND f.valid_time > now()
         ORDER BY f.valid_time
        """,
        (area_id,),
    ).fetchall()
    if not fc_rows:
        return sum(baseline.values()), [], {"reason": "no_forecast"}

    by_time: dict[datetime, dict[str, float]] = {}
    gauges_fc: set = set()
    for gid, vt, flow in fc_rows:
        if gid in baseline and flow is not None:
            by_time.setdefault(vt, {})[gid] = float(flow)
            gauges_fc.add(gid)
    baseline_total = sum(baseline[g] for g in gauges_fc)
    if not gauges_fc or baseline_total <= 0:
        return None, [], {"reason": "no_baseline"}

    traj = [
        (vt, sum(vals.values()) / baseline_total)
        for vt, vals in sorted(by_time.items())
        if len(vals) == len(gauges_fc)
    ]
    return baseline_total, traj, {"n_gauges": len(gauges_fc)}


def compute_freshwater_forecast(
    conn: psycopg.Connection, area_id: str
) -> IndicatorResult:
    """Forward-looking freshwater signal from the NWM streamflow forecast.

    Status: pulse_incoming / low_flow_building / rising / falling / steady /
    unknown. Score is the headline ratio (peak for pulses, min for low flow).
    """
    now = datetime.now(timezone.utc)
    baseline_total, traj, meta = _forecast_trajectory(conn, area_id)
    components: dict[str, Any] = {"baseline_cfs": baseline_total, **meta}

    if not traj:
        return IndicatorResult(
            area_id, "freshwater_forecast", now, status="unknown", score=None,
            components={**components, "reason": meta.get("reason", "no_forecast")},
        )

    ratios = [r for _, r in traj]
    peak_ratio = max(ratios)
    min_ratio = min(ratios)
    peak_time = traj[ratios.index(peak_ratio)][0]

    def _lead(threshold: float, above: bool) -> int | None:
        for vt, r in traj:
            if (r >= threshold) if above else (r <= threshold):
                return max(0, round((vt - now).total_seconds() / 3600))
        return None

    components.update({
        "peak_ratio": round(peak_ratio, 2),
        "peak_in_hours": max(0, round((peak_time - now).total_seconds() / 3600)),
        "min_ratio": round(min_ratio, 2),
        "horizon_hours": round((traj[-1][0] - now).total_seconds() / 3600),
    })

    if peak_ratio >= ACTIVE_INTRUSION_RATIO:
        components["pulse_in_hours"] = _lead(ACTIVE_INTRUSION_RATIO, above=True)
        return IndicatorResult(area_id, "freshwater_forecast", now,
                               status="pulse_incoming", score=round(peak_ratio, 2), components=components)
    if min_ratio <= DROUGHT_RATIO:
        components["lowflow_in_hours"] = _lead(DROUGHT_RATIO, above=False)
        return IndicatorResult(area_id, "freshwater_forecast", now,
                               status="low_flow_building", score=round(min_ratio, 2), components=components)
    if peak_ratio >= FORECAST_RISING_RATIO:
        return IndicatorResult(area_id, "freshwater_forecast", now,
                               status="rising", score=round(peak_ratio, 2), components=components)
    if min_ratio <= 0.8:
        return IndicatorResult(area_id, "freshwater_forecast", now,
                               status="falling", score=round(min_ratio, 2), components=components)
    return IndicatorResult(area_id, "freshwater_forecast", now,
                           status="steady", score=round(sum(ratios) / len(ratios), 2), components=components)


# ---------------------------------------------------------------------------
# Persistence + orchestration
# ---------------------------------------------------------------------------

def persist(conn: psycopg.Connection, result: IndicatorResult) -> None:
    conn.execute(
        """
        INSERT INTO area_indicators (area_id, indicator, computed_at, status, score, components)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (area_id, indicator, computed_at) DO UPDATE
          SET status     = EXCLUDED.status,
              score      = EXCLUDED.score,
              components = EXCLUDED.components
        """,
        (
            result.area_id,
            result.indicator,
            result.computed_at,
            result.status,
            result.score,
            json.dumps(result.components, default=str),
        ),
    )


def compute_all() -> dict[str, int]:
    """Compute every indicator for every area, persist results, return the
    freshwater_intrusion status counts (unchanged contract). The forward-looking
    freshwater_forecast is computed + persisted alongside and logged separately."""
    report: dict[str, int] = {}
    fc_report: dict[str, int] = {}
    with psycopg.connect(settings.database_dsn, autocommit=True) as conn:
        area_ids = [r[0] for r in conn.execute("SELECT id FROM areas").fetchall()]
        for area_id in area_ids:
            result = compute_freshwater_intrusion(conn, area_id)
            persist(conn, result)
            report[result.status] = report.get(result.status, 0) + 1

            forecast = compute_freshwater_forecast(conn, area_id)
            persist(conn, forecast)
            fc_report[forecast.status] = fc_report.get(forecast.status, 0) + 1
    log.info("Indicator compute_all: intrusion=%s forecast=%s", report, fc_report)
    return report
