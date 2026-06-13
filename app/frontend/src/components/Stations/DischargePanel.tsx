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
import { api, type AreaGauge, type GaugeTimeseries } from "../../api/client";

interface Props {
  areaSlug: string | null;
}

const numberFmt = new Intl.NumberFormat("en-US");

function formatCfs(v: number | null): string {
  return v === null ? "—" : `${numberFmt.format(Math.round(v))} cfs`;
}

function formatStage(v: number | null): string {
  return v === null ? "—" : `${v.toFixed(2)} ft`;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function GaugeCard({ gauge }: { gauge: AreaGauge }) {
  const [series, setSeries] = useState<GaugeTimeseries | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSeries(null);
    setError(null);
    api.getGaugeTimeseries(gauge.site_no, 30)
      .then(setSeries)
      .catch((e: Error) => setError(e.message));
  }, [gauge.site_no]);

  const chartData = (series?.points ?? [])
    .filter((p) => p.discharge_cfs !== null)
    .map((p) => ({ t: p.t, discharge: p.discharge_cfs }));

  return (
    <div className="gauge-card">
      <div className="gauge-card-header">
        <div>
          <div className="gauge-name">{gauge.name}</div>
          <div className="gauge-sub">
            {gauge.river ?? "—"} · USGS {gauge.site_no}
          </div>
        </div>
        <div className="gauge-values">
          <div className="value-big">{formatCfs(gauge.latest_discharge_cfs)}</div>
          <div className="value-small">
            stage {formatStage(gauge.latest_stage_ft)}
          </div>
          <div className="value-sub">
            {formatTimestamp(gauge.latest_discharge_at)}
          </div>
        </div>
      </div>

      <div className="gauge-chart">
        {error && <div className="error-text">Chart failed: {error}</div>}
        {!error && chartData.length === 0 && (
          <div className="muted-text">No discharge history yet.</div>
        )}
        {!error && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 4 }}>
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
                tickFormatter={(v) =>
                  v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`
                }
                width={42}
              />
              <Tooltip
                contentStyle={{
                  background: "#16223a",
                  border: "1px solid #243a5e",
                  fontSize: 12,
                }}
                labelFormatter={(t) => new Date(t).toLocaleString()}
                formatter={(v: number) => [`${numberFmt.format(v)} cfs`, "Discharge"]}
              />
              <Line
                type="monotone"
                dataKey="discharge"
                stroke="#4ea1ff"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export function DischargePanel({ areaSlug }: Props) {
  const [gauges, setGauges] = useState<AreaGauge[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!areaSlug) {
      setGauges(null);
      return;
    }
    setGauges(null);
    setError(null);
    api.getAreaGauges(areaSlug)
      .then(setGauges)
      .catch((e: Error) => setError(e.message));
  }, [areaSlug]);

  if (!areaSlug) {
    return (
      <div className="panel-empty">
        Select an area on the left to see linked river gauges.
      </div>
    );
  }
  if (error) {
    return <div className="error-text">Failed to load gauges: {error}</div>;
  }
  if (gauges === null) {
    return <div className="muted-text">Loading gauges…</div>;
  }
  if (gauges.length === 0) {
    return (
      <div className="panel-empty">
        No USGS gauges linked to this area.
      </div>
    );
  }

  return (
    <div className="discharge-panel">
      <h2>River discharge</h2>
      {gauges.map((g) => (
        <GaugeCard key={g.site_no} gauge={g} />
      ))}
    </div>
  );
}
