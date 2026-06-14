import { useEffect, useState } from "react";
import {
  api,
  API_BASE_URL,
  type AreaBriefing,
  type BriefingMap,
  type Verdict,
} from "../../api/client";
import { VERDICT_ORDER, VERDICT_STYLES } from "./verdictStyle";
import { DriverRow } from "./DriverRow";
import { TrendsDrawer } from "./TrendsDrawer";

interface Props {
  selectedSlug: string | null;
  briefings: BriefingMap | null;
  onSelect: (slug: string) => void;
  onClear: () => void;
}

const REGION_LABEL: Record<string, string> = {
  gulf: "Gulf of Mexico",
  east_coast: "US East Coast",
};

function VerdictBadge({ verdict, big }: { verdict: Verdict; big?: boolean }) {
  const s = VERDICT_STYLES[verdict];
  return (
    <span
      className={`verdict-badge${big ? " verdict-badge-lg" : ""}`}
      style={{ background: s.color, color: s.text }}
    >
      {s.label}
    </span>
  );
}

// Try the server-rendered PDF; if the host can't render it (no WeasyPrint/GTK,
// returns 503) fall back to the browser's print-to-PDF of the briefing.
async function exportPdf(slug: string): Promise<void> {
  try {
    const res = await fetch(`${API_BASE_URL}/areas/${slug}/report.pdf`);
    if (!res.ok) throw new Error(`report ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch {
    window.print();
  }
}

function relativeTime(iso: string | null): string {
  if (!iso) return "not computed yet";
  const then = new Date(iso).getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} h ago`;
  return `${Math.round(hrs / 24)} d ago`;
}

// ---------------------------------------------------------------------------
// Overview (no area selected) — rank every area worst-first.
// ---------------------------------------------------------------------------

function Overview({
  briefings,
  onSelect,
}: {
  briefings: BriefingMap;
  onSelect: (slug: string) => void;
}) {
  const entries = Object.entries(briefings).sort(
    (a, b) =>
      VERDICT_ORDER[a[1].verdict] - VERDICT_ORDER[b[1].verdict] ||
      a[1].name.localeCompare(b[1].name),
  );
  const attention = entries.filter(
    ([, b]) => b.verdict === "poor" || b.verdict === "caution",
  ).length;

  return (
    <div className="briefing">
      <div className="briefing-overview-head">
        <h2>Coastal conditions</h2>
        <p className="briefing-overview-sub">
          {entries.length === 0
            ? "No areas loaded yet."
            : attention > 0
              ? `${attention} of ${entries.length} areas need attention. Worst first.`
              : `All ${entries.length} areas look clear. Pick one for the full briefing.`}
        </p>
      </div>
      <ul className="overview-list">
        {entries.map(([slug, b]) => (
          <li key={slug}>
            <button
              type="button"
              className="overview-item"
              onClick={() => onSelect(slug)}
            >
              <span
                className="overview-dot"
                style={{ background: VERDICT_STYLES[b.verdict].color }}
                aria-hidden
              />
              <span className="overview-text">
                <span className="overview-name">{b.name}</span>
                <span className="overview-headline">
                  {b.headline ?? "No briefing computed yet."}
                </span>
              </span>
              <span
                className="overview-verdict"
                style={{ color: VERDICT_STYLES[b.verdict].color }}
              >
                {VERDICT_STYLES[b.verdict].label}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail (an area is selected)
// ---------------------------------------------------------------------------

function Detail({
  slug,
  onClear,
}: {
  slug: string;
  onClear: () => void;
}) {
  const [briefing, setBriefing] = useState<AreaBriefing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBriefing(null);
    setError(null);
    api.getAreaBriefing(slug)
      .then(setBriefing)
      .catch((e: Error) => setError(e.message));
  }, [slug]);

  return (
    <div className="briefing">
      <button type="button" className="briefing-back" onClick={onClear}>
        ← All areas
      </button>

      {error && <div className="error-text">Failed to load briefing: {error}</div>}
      {!error && briefing === null && (
        <div className="muted-text">Loading briefing…</div>
      )}

      {briefing && (
        <>
          <div className="briefing-region">
            {REGION_LABEL[briefing.region] ?? briefing.region}
          </div>
          <h2 className="briefing-name">{briefing.name}</h2>

          <div className="briefing-verdict-row">
            <VerdictBadge verdict={briefing.verdict} big />
            <span className="briefing-updated">
              Updated {relativeTime(briefing.computed_at)}
            </span>
          </div>

          <p className="briefing-headline">{briefing.headline}</p>

          {briefing.recommendation && (
            <div className="briefing-rec">
              <span className="briefing-rec-label">What to do</span>
              <span className="briefing-rec-text">{briefing.recommendation}</span>
            </div>
          )}

          <h3 className="briefing-section">What's driving this</h3>
          <div className="driver-list">
            {briefing.drivers.map((d) => (
              <DriverRow key={d.key} driver={d} />
            ))}
          </div>

          {briefing.forecast && (
            <>
              <h3 className="briefing-section">
                Outlook <span className="briefing-section-sub">· next 10 days</span>
              </h3>
              <div className="driver-list">
                <DriverRow driver={briefing.forecast} />
              </div>
            </>
          )}

          {briefing.coverage && (
            <p className="briefing-coverage">
              Based on {briefing.coverage.available} of {briefing.coverage.total}{" "}
              data sources reporting. Drill risk is provisional until modeled
              salinity is added.
            </p>
          )}

          <TrendsDrawer areaSlug={slug} />

          <button
            type="button"
            className="briefing-pdf"
            onClick={() => exportPdf(slug)}
          >
            Export PDF report
          </button>
        </>
      )}
    </div>
  );
}

export function BriefingColumn({ selectedSlug, briefings, onSelect, onClear }: Props) {
  return (
    <aside className="briefing-column">
      {selectedSlug ? (
        <Detail slug={selectedSlug} onClear={onClear} />
      ) : briefings === null ? (
        <div className="briefing">
          <div className="muted-text">Loading conditions…</div>
        </div>
      ) : (
        <Overview briefings={briefings} onSelect={onSelect} />
      )}
    </aside>
  );
}
