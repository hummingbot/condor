import { MODE_STYLES } from "@/components/agent/modeStyles";

// Pill badge for experiment execution modes. Renders nothing for normal
// loop mode (or unknown modes).
export function ModeBadge({ mode }: { mode?: string }) {
  const s = mode ? MODE_STYLES[mode] : undefined;
  if (!s) return null;
  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase ${s.badge}`}>
      {s.label}
    </span>
  );
}
