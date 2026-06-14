export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

export interface Area {
  id: string;
  name: string;
  slug: string;
  region: "gulf" | "east_coast";
  area_type: "predefined" | "custom";
  description: string | null;
  linked_gauges: string[];
}

export interface AreaGauge {
  site_no: string;
  name: string;
  river: string | null;
  lat: number;
  lon: number;
  latest_discharge_cfs: number | null;
  latest_discharge_at: string | null;
  latest_stage_ft: number | null;
  latest_stage_at: string | null;
}

export interface TimeseriesPoint {
  t: string;
  discharge_cfs: number | null;
  stage_ft: number | null;
}

export interface GaugeTimeseries {
  site_no: string;
  name: string;
  river: string | null;
  days: number;
  points: TimeseriesPoint[];
}

export type Variable = "sst" | "chlorophyll" | "turbidity" | "cdom" | "salinity";

export interface VariableSnapshot {
  captured_at: string;
  value_mean: number | null;
  value_min: number | null;
  value_max: number | null;
  units: string | null;
  source: string;
  // Anomaly fields (added by the snapshot endpoint)
  baseline_mean: number | null;
  baseline_std: number | null;
  baseline_n: number;
  z_score: number | null;
  is_anomaly: boolean;
  anomaly_direction: "high" | "low" | null;
  anomaly_threshold_z: number;
}

export interface AreaSnapshot {
  slug: string;
  variables: Partial<Record<Variable, VariableSnapshot>>;
}

export interface VariableTimeseriesPoint {
  t: string;
  value_mean: number | null;
  value_min: number | null;
  value_max: number | null;
}

export interface VariableTimeseries {
  slug: string;
  variable: Variable;
  units: string | null;
  days: number;
  points: VariableTimeseriesPoint[];
}

export type StationVariable = "water_temperature" | "salinity" | "water_level";

export type FreshwaterIntrusionStatus =
  | "active_intrusion"
  | "receding"
  | "normal"
  | "drought"
  | "unknown";

export interface FreshwaterIntrusion {
  status: FreshwaterIntrusionStatus;
  score: number | null;
  computed_at: string | null;
}

export type FreshwaterIntrusionMap = Record<string, FreshwaterIntrusion>;

export interface AreaIndicator {
  indicator: string;
  status: string;
  score: number | null;
  computed_at: string;
  components: Record<string, unknown>;
}

export interface AreaIndicators {
  slug: string;
  indicators: AreaIndicator[];
}

export type HabLevel = "watch" | "warning" | "closed";

export interface HabFeatureProperties {
  id: string;
  region: string;
  alert_level: HabLevel;
  species: string | null;
  description: string | null;
  issued_at: string | null;
  expires_at: string | null;
}

export interface HabFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Polygon"; coordinates: number[][][] };
    properties: HabFeatureProperties;
  }>;
}

export interface AreaHabAlert extends HabFeatureProperties {
  geometry: { type: "Polygon"; coordinates: number[][][] };
}

export interface AreaHab {
  slug: string;
  alerts: AreaHabAlert[];
}

export interface StationLatest {
  value: number;
  recorded_at: string;
  unit: string;
}

export interface AreaStation {
  station_id: string;
  name: string;
  lat: number;
  lon: number;
  variables: string[];
  distance_m: number;
  distance_warning: boolean;
  latest: Partial<Record<StationVariable, StationLatest>>;
}

export interface StationTimeseries {
  station_id: string;
  name: string;
  variable: StationVariable;
  units: string | null;
  days: number;
  points: Array<{ t: string; value: number }>;
}

export interface AreaFeatureProperties {
  slug: string;
  name: string;
  region: "gulf" | "east_coast";
  area_type: "predefined" | "custom";
  bbox: [number, number, number, number];   // [west, south, east, north]
}

export interface AreasFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Polygon"; coordinates: number[][][] };
    properties: AreaFeatureProperties;
  }>;
}

// --- Briefings (synthesized "lease condition") ---

export type Verdict = "good" | "caution" | "poor" | "unknown";

export interface BriefingDriver {
  key: string;
  label: string;
  status: Verdict;
  headline: string;
  detail: string;
  direction: "up" | "down" | "steady" | null;
  value: number | null;
  units: string | null;
  confidence: "measured" | "estimated" | null;
}

export interface Coverage {
  available: number;
  total: number;
}

export interface AreaBriefing {
  slug: string;
  name: string;
  region: "gulf" | "east_coast";
  verdict: Verdict;
  headline: string;
  recommendation: string;
  coverage: Coverage;
  drivers: BriefingDriver[];
  // Forward-looking freshwater "Outlook" (NWM forecast). Null when the area has
  // no linked gauge / forecast. Deliberately NOT part of `drivers` — it never
  // affects the current verdict.
  forecast: BriefingDriver | null;
  computed_at: string | null;
}

export interface BriefingSummary {
  name: string;
  region: "gulf" | "east_coast";
  verdict: Verdict;
  headline: string | null;
  recommendation: string | null;
  coverage: Coverage | null;
  computed_at: string | null;
}

export type BriefingMap = Record<string, BriefingSummary>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listAreas: () => request<Area[]>("/areas"),
  getArea: (slug: string) => request<Area>(`/areas/${slug}`),
  getAreaGauges: (slug: string) => request<AreaGauge[]>(`/areas/${slug}/gauges`),
  getGaugeTimeseries: (siteNo: string, days = 30) =>
    request<GaugeTimeseries>(`/gauges/${siteNo}/timeseries?days=${days}`),
  getAreaSnapshot: (slug: string) => request<AreaSnapshot>(`/areas/${slug}/snapshot`),
  getAreaTimeseries: (slug: string, variable: Variable, days = 30) =>
    request<VariableTimeseries>(
      `/areas/${slug}/timeseries?variable=${variable}&days=${days}`,
    ),
  getAreasGeoJSON: () => request<AreasFeatureCollection>("/areas/geojson"),
  getAreaStations: (slug: string) =>
    request<AreaStation[]>(`/areas/${slug}/stations`),
  getStationTimeseries: (
    stationId: string,
    variable: StationVariable,
    days = 30,
  ) =>
    request<StationTimeseries>(
      `/stations/${stationId}/timeseries?variable=${variable}&days=${days}`,
    ),
  getFreshwaterIntrusionAll: () =>
    request<FreshwaterIntrusionMap>("/indicators/freshwater-intrusion"),
  getAreaIndicators: (slug: string) =>
    request<AreaIndicators>(`/areas/${slug}/indicators`),
  getHabAlerts: () => request<HabFeatureCollection>("/alerts/hab"),
  getAreaHab: (slug: string) => request<AreaHab>(`/areas/${slug}/hab`),
  getBriefings: () => request<BriefingMap>("/briefings"),
  getAreaBriefing: (slug: string) => request<AreaBriefing>(`/areas/${slug}/briefing`),
};
