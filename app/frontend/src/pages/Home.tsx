import { useEffect, useState } from "react";
import { LayerToggle } from "../components/Map/LayerToggle";
import { MapView } from "../components/Map/MapView";
import { BriefingColumn } from "../components/Briefing/BriefingColumn";
import { VERDICT_STYLES } from "../components/Briefing/verdictStyle";
import { api, type BriefingMap, type Variable, type Verdict } from "../api/client";

const LEGEND: Verdict[] = ["good", "caution", "poor", "unknown"];

export default function Home() {
  const [selected, setSelected] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<Variable | null>(null);
  const [briefings, setBriefings] = useState<BriefingMap | null>(null);

  useEffect(() => {
    api.getBriefings()
      .then(setBriefings)
      .catch(() => setBriefings({}));
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>🦪</span>
          <div>
            <h1>OysterHealth</h1>
            <span className="brand-sub">
              Coastal water intelligence for shellfish harvesters
            </span>
          </div>
        </div>
        <LayerToggle active={overlay} onChange={setOverlay} />
      </header>

      <div className="workspace">
        <div className="map-pane">
          <MapView
            variable={overlay}
            selectedSlug={selected}
            onSelectArea={setSelected}
            briefings={briefings}
          />
          <div className="map-legend">
            <span className="map-legend-title">Lease condition</span>
            {LEGEND.map((v) => (
              <span key={v} className="legend-item">
                <span
                  className="legend-dot"
                  style={{ background: VERDICT_STYLES[v].color }}
                />
                {VERDICT_STYLES[v].label}
              </span>
            ))}
          </div>
        </div>

        <BriefingColumn
          selectedSlug={selected}
          briefings={briefings}
          onSelect={setSelected}
          onClear={() => setSelected(null)}
        />
      </div>
    </div>
  );
}
