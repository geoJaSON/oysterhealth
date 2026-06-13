import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type AreaStation,
  type StationTimeseries,
  type StationVariable,
} from "../../api/client";

interface Props {
  areaSlug: string | null;
}

const VARIABLE_SPECS: Array<{
  key: StationVariable;
  label: string;
  color: string;
  fmt: (v: number) => string;
}> = [
  { key: "water_temperature", label: "Water temp",  color: "#ff9461",
    fmt: (v) => `${v.toFixed(1)}°C` },
  { key: "salinity",          label: "Salinity",     color: "#7fbcff",
    fmt: (v) => `${v.toFixed(2)} psu` },
  { key: "water_level",       label: "Water level",  color: "#c084fc",
    fmt: (v) => `${v.toFixed(2)} m` },
];

function formatKm(m: number): string {
  return `${(m / 1000).toFixed(1)} km`;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StationVariableChart({
  stationId,
  variable,
  color,
  fmt,
}: {
  stationId: string;
  variable: StationVariable;
  color: string;
  fmt: (v: number) => string;
}) {
  const [series, setSeries] = useState<StationTimeseries | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSeries(null);
    setError(null);
    api.getStationTimeseries(stationId, variable, 30)
      .then(setSeries)
      .catch((e: Error) => setError(e.message));
  }, [stationId, variable]);

  if (error) return <div className="error-text">Chart failed: {error}</div>;
  if (series === null) return <div className="muted-text">…</div>;
  if (series.points.length === 0) {
    return <div className="muted-text">No history yet.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={80}>
      <LineChart data={series.points} margin={{ top: 2, right: 4, left: 0, bottom: 2 }}>
        <CartesianGrid stroke="#243a5e" strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          tickFormatter={(t) =>
            new Date(t).toLocaleDateString(undefined, {
              month: "numeric",
              day: "numeric",
            })
          }
          tick={{ fill: "#9aaccc", fontSize: 10 }}
          stroke="#243a5e"
          minTickGap={28}
        />
        <YAxis
          tick={{ fill: "#9aaccc", fontSize: 10 }}
          stroke="#243a5e"
          width={36}
          domain={["auto", "auto"]}
        />
        <Tooltip
          contentStyle={{
            background: "#16223a",
            border: "1px solid #243a5e",
            fontSize: 12,
          }}
          labelFormatter={(t) => new Date(t).toLocaleString()}
          formatter={(v: number) => [fmt(v), variable]}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function StationCard({ station }: { station: AreaStation }) {
  const supported = VARIABLE_SPECS.filter((s) =>
    station.variables.includes(s.key),
  );

  return (
    <div className="gauge-card">
      <div className="gauge-card-header">
        <div>
          <div className="gauge-name">{station.name}</div>
          <div className="gauge-sub">
            CO-OPS {station.station_id} · {formatKm(station.distance_m)} away
          </div>
        </div>
        {station.distance_warning && (
          <span className="distance-warning" title="Over 15 km from area centroid — may not represent the bay">
            ⚠ far
          </span>
        )}
      </div>

      {supported.map((spec) => {
        const latest = station.latest[spec.key];
        return (
          <div key={spec.key} className="station-variable">
            <div className="station-variable-header">
              <span className="station-variable-label">{spec.label}</span>
              <span className="station-variable-value" style={{ color: spec.color }}>
                {latest ? spec.fmt(latest.value) : "—"}
              </span>
            </div>
            <div className="station-variable-sub">
              {latest ? formatTimestamp(latest.recorded_at) : "no current reading"}
            </div>
            {latest && (
              <StationVariableChart
                stationId={station.station_id}
                variable={spec.key}
                color={spec.color}
                fmt={spec.fmt}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function StationsPanel({ areaSlug }: Props) {
  const [stations, setStations] = useState<AreaStation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!areaSlug) {
      setStations(null);
      return;
    }
    setStations(null);
    setError(null);
    api.getAreaStations(areaSlug)
      .then(setStations)
      .catch((e: Error) => setError(e.message));
  }, [areaSlug]);

  if (!areaSlug) return null;
  if (error) return <div className="error-text">Failed to load stations: {error}</div>;
  if (stations === null) return <div className="muted-text">Loading stations…</div>;
  if (stations.length === 0) {
    return (
      <div className="discharge-panel" style={{ marginBottom: 20 }}>
        <h2>Tide stations</h2>
        <div className="panel-empty">No CO-OPS stations seeded.</div>
      </div>
    );
  }

  const allFar = stations.every((s) => s.distance_warning);

  return (
    <div className="discharge-panel" style={{ marginBottom: 20 }}>
      <h2>Tide stations</h2>
      {allFar && (
        <div className="panel-note">
          All nearby stations are &gt; 15 km from this area's centroid;
          treat in-situ readings as regional context rather than local truth.
        </div>
      )}
      {stations.slice(0, 3).map((s) => (
        <StationCard key={s.station_id} station={s} />
      ))}
    </div>
  );
}
