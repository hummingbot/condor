import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Clock,
  FlaskConical,
  ScrollText,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { ExperimentsTab } from "@/components/agent/AgentExperimentsTab";
import { OverviewTab } from "@/components/agent/AgentOverviewTab";
import { AgentRoutinesTab } from "@/components/agent/AgentRoutinesTab";
import { SessionsTab } from "@/components/agent/AgentSessionsTab";
import { api } from "@/lib/api";

// ── Tabs ──

const TABS = [
  { id: "overview", label: "Overview", icon: Zap },
  { id: "sessions", label: "Sessions", icon: Clock },
  { id: "routines", label: "Routines", icon: ScrollText },
  { id: "experiments", label: "Dry-Run", icon: FlaskConical },
] as const;

type TabId = (typeof TABS)[number]["id"];

// ── Main Page ──

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabId>("overview");

  const { data: agent, isLoading } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug!),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // Derive controller IDs from active instances for WS executor streaming
  const controllerIds = useMemo(
    () => (agent?.instances || []).map((inst) => inst.agent_id).filter(Boolean),
    [agent?.instances],
  );

  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const serverName = (agent.config.server_name as string) || "";

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => navigate("/agents")}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
        </button>

        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[var(--color-text)]">{agent.name}</h1>
            {agent.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{agent.description}</p>
            )}
          </div>
          <AgentControls
            slug={slug!}
            status={agent.status}
            defaultContext={agent.default_trading_context || (agent.config.trading_context as string) || ""}
            agentConfig={agent.config}
          />
        </div>
      </div>

      {/* Tab bar */}
      <div className="mb-6 flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
              activeTab === id
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "overview" && <OverviewTab agent={agent} />}
      {activeTab === "sessions" && (
        <SessionsTab
          slug={slug!}
          sessions={agent.sessions}
          serverName={serverName}
          controllerIds={controllerIds}
        />
      )}
      {activeTab === "routines" && <AgentRoutinesTab slug={slug!} />}
      {activeTab === "experiments" && <ExperimentsTab slug={slug!} experiments={agent.experiments || []} />}
    </div>
  );
}
