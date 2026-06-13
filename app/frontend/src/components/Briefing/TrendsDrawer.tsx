import { useState } from "react";
import { DischargePanel } from "../Stations/DischargePanel";
import { StationsPanel } from "../Stations/StationsPanel";
import { VariablesPanel } from "../Indicators/VariablesPanel";

/**
 * The supporting evidence behind a briefing — deliberately collapsed by
 * default. The verdict + drivers answer "so what?"; this is for the user who
 * wants to see the raw trends that produced it. Reuses the existing chart
 * panels rather than re-implementing them.
 */
export function TrendsDrawer({ areaSlug }: { areaSlug: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="trends-drawer">
      <button
        type="button"
        className="trends-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="trends-caret">{open ? "▾" : "▸"}</span>
        Trends &amp; supporting data
      </button>
      {open && (
        <div className="trends-body">
          <VariablesPanel areaSlug={areaSlug} />
          <StationsPanel areaSlug={areaSlug} />
          <DischargePanel areaSlug={areaSlug} />
        </div>
      )}
    </div>
  );
}
