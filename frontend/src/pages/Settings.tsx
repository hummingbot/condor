import { useQuery } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { AdminSettings } from "@/components/settings/AdminSettings";
import { ApiKeysSettings } from "@/components/settings/ApiKeysSettings";
import { CustomProvidersSettings } from "@/components/settings/CustomProvidersSettings";
import { GatewaySettings } from "@/components/settings/GatewaySettings";
import { ServersSettings } from "@/components/settings/ServersSettings";
import { VoiceSettings } from "@/components/settings/VoiceSettings";
import { ADMIN_USERS_KEY, adminApi } from "@/lib/admin-api";
import { useAuth } from "@/lib/auth";

const TABS = [
  { key: "servers", label: "Servers" },
  { key: "gateway", label: "Gateway" },
  { key: "keys", label: "Keys and Wallets" },
  { key: "llm", label: "LLM Endpoints" },
  { key: "voice", label: "Voice & AI" },
] as const;

/** Admin-only, appended to TABS when the user turns out to be an admin (ARCH-177). */
const ADMIN_TAB = { key: "admin", label: "Admin" } as const;

type TabKey = (typeof TABS)[number]["key"] | typeof ADMIN_TAB.key;

export function Settings() {
  const [params, setParams] = useSearchParams();
  const { logout } = useAuth();

  // There is no `is_admin` claim on the client, so the admin surface answering
  // at all is the discriminator: `/admin/users` is 403 for everyone else. The
  // panel reuses this query key, so opening the tab costs no second request.
  // Hiding the tab is cosmetic — routes/admin.py re-checks the role every call.
  const { isSuccess: isAdmin } = useQuery({
    queryKey: ADMIN_USERS_KEY,
    queryFn: adminApi.getUsers,
    retry: false,
  });

  const tabs = isAdmin ? [...TABS, ADMIN_TAB] : TABS;
  const requested = (params.get("tab") as TabKey) || "servers";
  // A deep link to ?tab=admin from a seat that is not (or no longer) an admin
  // falls back rather than rendering an empty page.
  const tab = tabs.some((t) => t.key === requested) ? requested : "servers";

  return (
    <div className="mx-auto max-w-3xl">
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
      {tab === "admin" && <AdminSettings />}
    </div>
  );
}
