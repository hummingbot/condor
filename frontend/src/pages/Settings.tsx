import {
  Bell,
  Download,
  KeyRound,
  LogOut,
  Mic,
  Network,
  Server,
  Shield,
  ShieldCheck,
  Palette,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { AdminSettings } from "@/components/settings/AdminSettings";
import { ApiKeysSettings } from "@/components/settings/ApiKeysSettings";
import { CustomProvidersSettings } from "@/components/settings/CustomProvidersSettings";
import { GatewaySettings } from "@/components/settings/GatewaySettings";
import { NotificationsSettings } from "@/components/settings/NotificationsSettings";
import { ServersSettings } from "@/components/settings/ServersSettings";
import { SharingSettings } from "@/components/settings/SharingSettings";
import { TelemetrySettings } from "@/components/settings/TelemetrySettings";
import { ThemeSettings } from "@/components/settings/ThemeSettings";
import { UpdatesSettings } from "@/components/settings/UpdatesSettings";
import { VoiceSettings } from "@/components/settings/VoiceSettings";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { useServer } from "@/hooks/useServer";
import { useAuth } from "@/lib/auth";

type TabKey =
  | "gateway"
  | "keys"
  | "servers"
  | "llm"
  | "voice"
  | "notifications"
  | "privacy"
  | "theme"
  | "admin"
  | "updates";

interface TabDef {
  key: TabKey;
  label: string;
  icon: LucideIcon;
}

interface TabGroup {
  label: string;
  tabs: readonly TabDef[];
}

/**
 * The first group acts on whichever server the navbar selector points at, so it
 * is titled with that server — there is no second picker in here. Everything
 * after it belongs to this Condor install or to the signed-in person.
 */
function buildGroups(server: string | null, isAdmin: boolean): TabGroup[] {
  const groups: TabGroup[] = [
    {
      label: server ? `Server · ${server}` : "Server",
      tabs: [
        { key: "gateway", label: "Gateway", icon: Network },
        { key: "keys", label: "Keys and Wallets", icon: KeyRound },
      ],
    },
    {
      label: "Condor",
      tabs: [
        { key: "servers", label: "Servers", icon: Server },
        { key: "llm", label: "LLM Endpoints", icon: Sparkles },
        { key: "voice", label: "Voice & AI", icon: Mic },
      ],
    },
    {
      label: "Account",
      tabs: [
        { key: "theme", label: "Theme", icon: Palette },
        { key: "notifications", label: "Notifications", icon: Bell },
        { key: "privacy", label: "Privacy", icon: Shield },
      ],
    },
  ];
  // Admin-only (ARCH-177), and Updates on the same probe since an update
  // restarts the process (FEAT-071).
  if (isAdmin) {
    groups.push({
      label: "Admin",
      tabs: [
        { key: "admin", label: "Admin", icon: ShieldCheck },
        { key: "updates", label: "Updates", icon: Download },
      ],
    });
  }
  return groups;
}

export function Settings() {
  const [params, setParams] = useSearchParams();
  const { logout } = useAuth();
  const { server } = useServer();

  // There is no `is_admin` claim on the client, so the admin surface answering
  // at all is the discriminator: `/admin/people` is 403 for everyone else. The
  // panel reuses this query key, so opening the tab costs no second request.
  // Hiding the tab is cosmetic — routes/admin.py re-checks the role every call.
  const isAdmin = useIsAdmin();

  const groups = buildGroups(server, isAdmin);
  const tabs = groups.flatMap((g) => g.tabs);
  const requested = (params.get("tab") as TabKey) || "servers";
  // A deep link to ?tab=admin or ?tab=updates from a seat that is not (or no
  // longer) an admin falls back rather than rendering an empty page.
  const tab = tabs.some((t) => t.key === requested) ? requested : "servers";

  return (
    <div className="mx-auto max-w-6xl">
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

      {/* Narrow screens: one select, grouped the same way as the sidebar. */}
      <select
        aria-label="Settings section"
        value={tab}
        onChange={(e) => setParams({ tab: e.target.value })}
        className="mb-4 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] md:hidden"
      >
        {groups.map((g) => (
          <optgroup key={g.label} label={g.label}>
            {g.tabs.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      <div className="flex gap-8">
        <nav
          aria-label="Settings sections"
          className="hidden w-52 shrink-0 space-y-5 md:block"
        >
          {groups.map((g) => (
            <div key={g.label}>
              <div
                className="mb-1.5 truncate px-3 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]"
                title={g.label}
              >
                {g.label}
              </div>
              <div className="space-y-0.5">
                {g.tabs.map((t) => {
                  const Icon = t.icon;
                  const active = tab === t.key;
                  return (
                    <button
                      key={t.key}
                      onClick={() => setParams({ tab: t.key })}
                      aria-current={active ? "page" : undefined}
                      className={`flex w-full items-center gap-2.5 rounded-md px-3 py-1.5 text-left text-sm font-medium transition-colors ${
                        active
                          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                      }`}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {t.label}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="min-w-0 flex-1">
          {/* Tab content */}
          {tab === "servers" && <ServersSettings />}
          {tab === "gateway" && <GatewaySettings />}
          {tab === "keys" && <ApiKeysSettings />}
          {tab === "llm" && <CustomProvidersSettings />}
          {tab === "voice" && <VoiceSettings />}
          {tab === "theme" && <ThemeSettings />}
          {tab === "notifications" && <NotificationsSettings />}
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
      </div>
    </div>
  );
}
