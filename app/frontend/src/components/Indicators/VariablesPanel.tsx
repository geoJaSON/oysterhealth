import { useEffect, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type AreaSnapshot,
  type Variable,
  type VariableSnapshot,
  type VariableTimeseries,
} from "../../api/client";

interface Props {
  areaSlug: string | null;
}

interface VarSpec {
  key: Variable;
  label: string;
  color: string;
  fmt: (v: number) => string;
}

const VARIABLES: VarSpec[] = [
  { key: "sst",         label: "Sea surface temperature", color: "#ff9461",
    fmt: (v) => `${v.toFixed(1)}°C` },
  { key: "chlorophyll", label: "Chlorophyll-a",           color: "#7fd97a",
    fmt: (v) => `${v.toFixed(2)} mg/m³` },
  { key: "turbidity",   label: "Turbidity (Kd₄₉₀)",       color: "#c9a36a",
    fmt: (v) => `${v.toFixed(3)} m⁻¹` },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function VariableCard({
  spec,
  snap,
  areaSlug,
}: {
  spec: VarSpec;
  snap: VariableSnapshot | undefined;
  areaSlug: string;
}) {
  const [series, setSeries] = useState<VariableTimeseries | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSeries(null);
    setError(null);
    api.getAreaTimeseries(areaSlug, spec.key, 30)
      .then(setSeries)
      .catch((e: Error) => setError(e.message));
  }, [areaSlug, spec.key]);

  const data = (series?.points ?? [])
    .filter((p) => p.value_mean !== null)
    .map((p) => ({
      t: p.t,
      mean: p.value_mean,
      min: p.value_min,
      max: p.value_max,
      // Recharts can shade an Area between min and max if both are present
      band: [p.value_min, p.value_max],
    }));

  return (
    <div className="gauge-card">
      <div className="gauge-card-header">
        <div>
          <div className="gauge-name">
            {spec.label}
            {snap?.is_anomaly && snap.anomaly_direction && (
              <span
                className={`anomaly-badge anomaly-${snap.anomaly_direction}`}
                title={`${snap.z_score?.toFixed(1)}σ from 30-day baseline`}
              >
                {snap.anomaly_direction === "high" ? "↑ high" : "↓ low"} ·{" "}
                {Math.abs(snap.z_score ?? 0).toFixed(1)}σ
              </span>
            )}
          </div>
          <div className="gauge-sub">
            {snap?.captured_at
              ? `As of ${formatDate(snap.captured_at)}`
              : "No data yet"}
            {snap?.baseline_mean != null && (
              <>
                {" · "}baseline {spec.fmt(snap.baseline_mean)}
                {snap.baseline_std != null && (
                  <> ±{spec.fmt(snap.baseline_std).replace(/^\D+/, "")}</>
                )}
              </>
            )}
          </div>
        </div>
        <div className="gauge-values">
          <div className="value-big" style={{ color: spec.color }}>
            {snap?.value_mean !== null && snap?.value_mean !== undefined
              ? spec.fmt(snap.value_mean)
              : "—"}
          </div>
          {snap?.value_min !== null && snap?.value_min !== undefined &&
            snap?.value_max !== null && snap?.value_max !== undefined && (
              <div className="value-small">
                range {spec.fmt(snap.value_min)} – {spec.fmt(snap.value_max)}
              </div>
            )}
        </div>
      </div>

      <div className="gauge-chart">
        {error && <div className="error-text">Chart failed: {error}</div>}
        {!error && data.length === 0 && (
          <div className="muted-text">No history yet — run backfill.</div>
        )}
        {!error && data.length > 0 && (
          <ResponsiveContainer width="100%" height={120}>
            <ComposedChart data={data} margin={{ top: 4, right: 8, left: 4, bottom: 4 }}>
              <CartesianGrid stroke="#243a5e" strokeDasharray="3 3" />
              <XAxis
                dataKey="t"
                tickFormatter={(t) => formatDate(t)}
                tick={{ fill: "#9aaccc", fontSize: 10 }}
                stroke="#243a5e"
                minTickGap={28}
              />
              <YAxis
                tick={{ fill: "#9aaccc", fontSize: 10 }}
                stroke="#243a5e"
                width={42}
                domain={["auto", "auto"]}
              />
              <Tooltip
                contentStyle={{
                  background: "#16223a",
                  border: "1px solid #243a5e",
                  fontSize: 12,
                }}
                labelFormatter={(t) => new Date(t).toLocaleDateString()}
                formatter={(v: number | [number, number], name: string) => {
                  if (Array.isArray(v))
                    return [`${spec.fmt(v[0])} – ${spec.fmt(v[1])}`, "range"];
                  return [spec.fmt(v), name];
                }}
              />
              <Area
                type="monotone"
                dataKey="band"
                stroke="none"
                fill={spec.color}
                fillOpacity={0.15}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="mean"
                stroke={spec.color}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                name="mean"
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export function VariablesPanel({ areaSlug }: Props) {
  const [snap, setSnap] = useState<AreaSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!areaSlug) {
      setSnap(null);
      return;
    }
    setSnap(null);
    setError(null);
    api.getAreaSnapshot(areaSlug)
      .then(setSnap)
      .catch((e: Error) => setError(e.message));
  }, [areaSlug]);

  if (!areaSlug) return null;
  if (error) return <div className="error-text">Failed to load variables: {error}</div>;

  return (
    <div className="discharge-panel" style={{ marginBottom: 20 }}>
      <h2>Water variables</h2>
      {VARIABLES.map((spec) => (
        <VariableCard
          key={spec.key}
          spec={spec}
          snap={snap?.variables[spec.key]}
          areaSlug={areaSlug}
        />
      ))}
    </div>
  );
}
