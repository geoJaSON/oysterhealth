import type { FreshwaterIntrusionStatus } from "../../api/client";

export interface StatusStyle {
  label: string;
  color: string;        // border + accent
  fill: string;         // polygon fill (often same hue with lower alpha)
  description: string;  // one-liner shown in the badge tooltip
}

export const FRESHWATER_INTRUSION_STYLES: Record<FreshwaterIntrusionStatus, StatusStyle> = {
  active_intrusion: {
    label: "Active intrusion",
    color: "#4ea1ff",     // bright blue — fresh water is dominant
    fill:  "#4ea1ff",
    description: "Upstream discharge >150% of 30-day mean — freshwater pulse reaching this area.",
  },
  receding: {
    label: "Receding",
    color: "#7fd97a",     // green — coming back to baseline
    fill:  "#7fd97a",
    description: "Discharge back near baseline after a recent active intrusion episode.",
  },
  normal: {
    label: "Normal",
    color: "#9aaccc",     // muted — nothing notable
    fill:  "#9aaccc",
    description: "Discharge within 80–120% of 30-day baseline; no recent freshwater pulse.",
  },
  drought: {
    label: "Drought",
    color: "#ff9461",     // warm tan/orange — low flow, hypersaline risk
    fill:  "#ff9461",
    description: "Upstream discharge <50% of 30-day mean — low-flow regime, possible saltwater push.",
  },
  unknown: {
    label: "Unknown",
    color: "#5a6b85",     // dark gray
    fill:  "#5a6b85",
    description: "No linked upstream gauge or insufficient history to compute.",
  },
};
