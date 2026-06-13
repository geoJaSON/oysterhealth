import type { Variable } from "../../api/client";

interface Props {
  active: Variable | null;
  onChange: (v: Variable | null) => void;
}

const OPTIONS: Array<{ key: Variable | null; label: string }> = [
  { key: null,          label: "Off" },
  { key: "sst",         label: "SST" },
  { key: "chlorophyll", label: "Chlorophyll" },
  { key: "turbidity",   label: "Turbidity" },
];

export function LayerToggle({ active, onChange }: Props) {
  return (
    <div className="layer-toggle">
      <span className="layer-toggle-label">Overlay:</span>
      {OPTIONS.map((opt) => (
        <button
          key={String(opt.key)}
          type="button"
          className={`layer-toggle-btn${active === opt.key ? " active" : ""}`}
          onClick={() => onChange(opt.key)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
