import { LogOut } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { AdminSettings } from "@/components/settings/AdminSettings";
import { ApiKeysSettings } from "@/components/settings/ApiKeysSettings";
import { CustomProvidersSettings } from "@/components/settings/CustomProvidersSettings";
import { GatewaySettings } from "@/components/settings/GatewaySettings";
import { ServersSettings } from "@/components/settings/ServersSettings";
import { SharingSettings } from "@/components/settings/SharingSettings";
import { TelemetrySettings } from "@/components/settings/TelemetrySettings";
import { UpdatesSettings } from "@/components/settings/UpdatesSettings";
import { VoiceSettings } from "@/components/settings/VoiceSettings";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { useAuth } from "@/lib/auth";

const TABS = [
  { key: "servers", label: "Servers" },
  { key: "gateway", label: "Gateway" },
  { key: "keys", label: "Keys and Wallets" },
  { key: "llm", label: "LLM Endpoints" },
  { key: "voice", label: "Voice & AI" },
  { key: "privacy", label: "Privacy" },
] as const;

/** Admin-only, appended to TABS when the user turns out to be an admin (ARCH-177). */
const ADMIN_TAB = { key: "admin", label: "Admin" } as const;

/** Admin-only too, on the same probe: an update restarts the process (FEAT-071). */
const UPDATES_TAB = { key: "updates", label: "Updates" } as const;

const ADMIN_TABS = [ADMIN_TAB, UPDATES_TAB] as const;

type TabKey =
  | (typeof TABS)[number]["key"]
  | (typeof ADMIN_TABS)[number]["key"];

export function Settings() {
  const [params, setParams] = useSearchParams();
  const { logout } = useAuth();

  // There is no `is_admin` claim on the client, so the admin surface answering
  // at all is the discriminator: `/admin/people` is 403 for everyone else. The
  // panel reuses this query key, so opening the tab costs no second request.
  // Hiding the tab is cosmetic — routes/admin.py re-checks the role every call.
  const isAdmin = useIsAdmin();

  const tabs = isAdmin ? [...TABS, ...ADMIN_TABS] : TABS;
  const requested = (params.get("tab") as TabKey) || "servers";
  // A deep link to ?tab=admin or ?tab=updates from a seat that is not (or no
  // longer) an admin falls back rather than rendering an empty page.
  const tab = tabs.some((t) => t.key === requested) ? requested : "servers";

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-[var(--color-text)]">Settings</h1>
        <button
          onClick={logout}
          className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
        >
          <LogOut className="h-3.5 w-3.5" />
          Logout
        </button>
      </div>

      {/* Tab bar */}
      <div className="mb-6 flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
        {tabs.map((t) => (
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
      {tab === "llm" && <CustomProvidersSettings />}
      {tab === "voice" && <VoiceSettings />}
      {/* Two cards, not one switch. Telemetry is anonymous counts the admin
          consents to install-wide; sharing is content only its author can hand
          over. They are different promises, and merging the controls would
          misrepresent one of them — the divider is where the copy says so. */}
      {tab === "privacy" && (
        <div className="space-y-8">
          <TelemetrySettings />
          <div className="border-t border-[var(--color-border)] pt-8">
            <SharingSettings />
          </div>
        </div>
      )}
      {tab === "admin" && <AdminSettings />}
      {tab === "updates" && <UpdatesSettings />}
    </div>
  );
}
