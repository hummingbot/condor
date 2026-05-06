import { useSearchParams } from "react-router-dom";

import { ApiKeysSettings } from "@/components/settings/ApiKeysSettings";
import { GatewaySettings } from "@/components/settings/GatewaySettings";
import { ServersSettings } from "@/components/settings/ServersSettings";

const TABS = [
  { key: "servers", label: "Servers" },
  { key: "gateway", label: "Gateway" },
  { key: "keys", label: "API Keys" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as TabKey) || "servers";

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-4 text-lg font-bold text-[var(--color-text)]">Settings</h1>

      {/* Tab bar */}
      <div className="mb-6 flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setParams({ tab: t.key })}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === t.key
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "servers" && <ServersSettings />}
      {tab === "gateway" && <GatewaySettings />}
      {tab === "keys" && <ApiKeysSettings />}
    </div>
  );
}
