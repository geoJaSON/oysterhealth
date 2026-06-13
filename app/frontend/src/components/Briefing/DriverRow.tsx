import type { BriefingDriver } from "../../api/client";
import { VERDICT_STYLES } from "./verdictStyle";

const ARROW: Record<"up" | "down" | "steady", string> = {
  up: "↑",
  down: "↓",
  steady: "→",
};

export function DriverRow({ driver }: { driver: BriefingDriver }) {
  const s = VERDICT_STYLES[driver.status];
  return (
    <div className="driver-row">
      <span className="driver-dot" style={{ background: s.color }} aria-hidden />
      <div className="driver-body">
        <div className="driver-head">
          <span className="driver-label">{driver.label}</span>
          <span className="driver-value" style={{ color: s.color }}>
            {driver.direction && driver.direction !== "steady" && (
              <span className="driver-arrow">{ARROW[driver.direction]}</span>
            )}
            {driver.headline}
            {driver.confidence === "estimated" && (
              <span
                className="driver-est"
                title="Estimated — nearest in-situ station is over 15 km away"
              >
                est
              </span>
            )}
            {driver.confidence === "modeled" && (
              <span
                className="driver-est"
                title="Modeled — from CMEMS satellite/model, not an in-situ reading; coarse in estuaries"
              >
                model
              </span>
            )}
          </span>
        </div>
        <div className="driver-detail">{driver.detail}</div>
      </div>
    </div>
  );
}
