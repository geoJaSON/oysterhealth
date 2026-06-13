import type { Verdict } from "../../api/client";

export interface VerdictStyle {
  label: string;
  color: string; // accent / polygon stroke + fill hue
  text: string;  // foreground for the filled badge
}

export const VERDICT_STYLES: Record<Verdict, VerdictStyle> = {
  good:    { label: "Good",    color: "#2fb170", text: "#04150d" },
  caution: { label: "Caution", color: "#e6a23c", text: "#1a1101" },
  poor:    { label: "Poor",    color: "#e2533f", text: "#1a0603" },
  unknown: { label: "No data", color: "#64748b", text: "#070b12" },
};

// Sort order for the overview list: worst conditions surface first.
export const VERDICT_ORDER: Record<Verdict, number> = {
  poor: 0,
  caution: 1,
  good: 2,
  unknown: 3,
};
