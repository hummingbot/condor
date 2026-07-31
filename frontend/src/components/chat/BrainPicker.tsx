import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronDown, ChevronLeft, ChevronRight, Loader2, Search } from "lucide-react";

import {
  api,
  customAgentKey,
  parseCustomAgentKey,
  type AgentBindingOption,
  type ChatAgentOption,
  type CustomProvider,
  type OpenRouterModelOption,
} from "@/lib/api";

/**
 * Who is answering. The two fields are orthogonal — a bound Agent brings its
 * own model, and picking a model while one is bound overrides just the model —
 * so a selection names whichever one the user actually changed.
 */
export interface BrainSelection {
  /** Bind a domain Agent. `""` unbinds, back to the plain assistant. */
  agentSlug?: string;
  /** The model. Leaves any bound Agent alone. */
  agentKey?: string;
}

/** Resolve the button label for the current selection, incl. openrouter:<slug>. */
function modelLabel(
  agentKey: string,
  agents: ChatAgentOption[],
  orModels?: OpenRouterModelOption[] | null,
): string {
  const match = agents.find((a) => a.key === agentKey);
  if (match) return match.label;
  if (agentKey.startsWith("openrouter:")) {
    const slug = agentKey.slice("openrouter:".length);
    const model = orModels?.find((m) => m.slug === slug);
    return `OpenRouter: ${model?.name || slug}`;
  }
  // Name the endpoint, so a user with several can tell which one is live
  const custom = parseCustomAgentKey(agentKey);
  if (custom) return `${custom.provider}: ${custom.model}`;
  return agentKey;
}

/**
 * "Who's answering" — one picker over two sections.
 *
 * **Agents** binds a domain Agent (its identity, tools, memory scope);
 * **Models** picks the LLM. They are deliberately one list rather than two
 * dropdowns, because the user is asking one question. Picker sentinels
 * ("openrouter:", "custom:") are drill-downs, never selectable keys — the
 * backend flags them, since the key's shape does not tell you ("ollama:" also
 * ends in a colon and is a real key meaning "that backend's default model").
 */
export function BrainPicker({
  agents,
  customProviders = [],
  agentBindings = [],
  selectedAgentKey,
  selectedAgentSlug = "",
  onSelect,
  variant,
  disabled = false,
}: {
  agents: ChatAgentOption[];
  customProviders?: CustomProvider[];
  agentBindings?: AgentBindingOption[];
  selectedAgentKey: string;
  selectedAgentSlug?: string;
  onSelect: (selection: BrainSelection) => void;
  variant: "block" | "inline";
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // null = top level, otherwise the submenu we drilled into
  const [submenu, setSubmenu] = useState<
    { kind: "openrouter" } | { kind: "custom"; name: string } | null
  >(null);
  const [models, setModels] = useState<OpenRouterModelOption[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  // Direct picks are everything the backend didn't flag as a picker.
  const directAgents = agents.filter((a) => !a.picker);
  const hasOpenRouter = agents.some((a) => a.key === "openrouter:" && a.picker);
  const boundAgent = agentBindings.find((a) => a.slug === selectedAgentSlug);
  // A bound Agent *is* the answer to "who's answering"; the model it runs on is
  // a detail, so it only names the button when nothing is bound.
  const label = boundAgent?.name || modelLabel(selectedAgentKey, agents, models);
  const activeCustom = parseCustomAgentKey(selectedAgentKey);

  // Custom endpoint models, fetched per endpoint on open
  const customModels = useQuery({
    queryKey: ["custom-provider-models", submenu?.kind === "custom" ? submenu.name : null],
    queryFn: () => api.getCustomProviderModels((submenu as { name: string }).name),
    enabled: submenu?.kind === "custom",
    staleTime: 5 * 60 * 1000,
  });

  const loadModels = () => {
    if (models || loading) return;
    setLoading(true);
    setError(null);
    api
      .getOpenRouterModels()
      .then((res) => setModels(res.models))
      .catch(() => setError("Failed to load OpenRouter models"))
      .finally(() => setLoading(false));
  };

  const closeAll = () => {
    setOpen(false);
    setSubmenu(null);
    setQuery("");
  };

  const pick = (selection: BrainSelection) => {
    onSelect(selection);
    closeAll();
  };

  const q = query.trim().toLowerCase();
  const filtered = (models || []).filter(
    (m) => !q || m.slug.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
  );

  const sectionHeader = (text: string, first = false) => (
    <div
      className={`px-2.5 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] ${
        first ? "" : "mt-0.5 border-t border-[var(--color-border)]"
      }`}
    >
      {text}
    </div>
  );

  const rowClass = (active: boolean) =>
    `flex w-full items-center px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
      active ? "font-medium text-[var(--color-primary)]" : "text-[var(--color-text)]"
    }`;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        title={boundAgent ? boundAgent.description || boundAgent.name : label}
        className={`${
          variant === "block"
            ? "flex w-full items-center justify-between rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 text-left text-xs text-[var(--color-text)] hover:border-[var(--color-primary)]/40"
            : "flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs text-[var(--color-text)] hover:border-[var(--color-primary)]/40"
        } disabled:cursor-not-allowed disabled:opacity-50`}
      >
        {boundAgent && <Bot className="h-3 w-3 shrink-0 text-[var(--color-accent)]" />}
        <span className="truncate">{label}</span>
        <ChevronDown className="ml-1 h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={closeAll} />
          <div
            className={`absolute top-full z-50 mt-1 flex max-h-72 flex-col overflow-y-auto rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 shadow-lg ${
              variant === "block" ? "left-0 w-full" : "right-0 w-64"
            }`}
          >
            {submenu === null ? (
              <>
                {agentBindings.length > 0 && (
                  <>
                    {sectionHeader("Agents", true)}
                    <button
                      onClick={() => pick({ agentSlug: "" })}
                      className={rowClass(!selectedAgentSlug)}
                    >
                      Condor assistant
                    </button>
                    {agentBindings.map((a) => (
                      <button
                        key={a.slug}
                        onClick={() => pick({ agentSlug: a.slug })}
                        title={a.when_to_consult || a.description}
                        className={`flex w-full flex-col items-start px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                          a.slug === selectedAgentSlug
                            ? "font-medium text-[var(--color-primary)]"
                            : "text-[var(--color-text)]"
                        }`}
                      >
                        <span className="w-full truncate">{a.name}</span>
                        {(a.when_to_consult || a.description) && (
                          <span className="w-full truncate text-[10px] font-normal text-[var(--color-text-muted)]">
                            {a.when_to_consult || a.description}
                          </span>
                        )}
                      </button>
                    ))}
                    {sectionHeader("Models")}
                  </>
                )}
                {directAgents.map((a) => (
                  <button
                    key={a.key}
                    onClick={() => pick({ agentKey: a.key })}
                    className={rowClass(a.key === selectedAgentKey)}
                  >
                    {a.label}
                  </button>
                ))}
                {hasOpenRouter && (
                  <button
                    onClick={() => {
                      setSubmenu({ kind: "openrouter" });
                      loadModels();
                    }}
                    className={`flex w-full items-center justify-between px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                      selectedAgentKey.startsWith("openrouter:")
                        ? "font-medium text-[var(--color-primary)]"
                        : "text-[var(--color-text)]"
                    }`}
                  >
                    <span>OpenRouter — Pick Model</span>
                    <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                  </button>
                )}
                {customProviders.length > 0 && sectionHeader("Custom endpoints")}
                {customProviders.map((p) => (
                  <button
                    key={p.name}
                    onClick={() => setSubmenu({ kind: "custom", name: p.name })}
                    className={`flex w-full items-center justify-between gap-1 px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                      activeCustom?.provider === p.name
                        ? "font-medium text-[var(--color-primary)]"
                        : "text-[var(--color-text)]"
                    }`}
                  >
                    <span className="truncate">
                      {p.name}
                      {activeCustom?.provider === p.name && (
                        <span className="text-[var(--color-text-muted)]">
                          {" "}
                          — {activeCustom.model}
                        </span>
                      )}
                    </span>
                    <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                  </button>
                ))}
              </>
            ) : submenu.kind === "custom" ? (
              <>
                <button
                  onClick={() => setSubmenu(null)}
                  className="flex items-center gap-1 border-b border-[var(--color-border)] px-2.5 py-1.5 text-left text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  <ChevronLeft className="h-3 w-3" /> {submenu.name}
                </button>
                {customModels.isLoading && (
                  <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-[var(--color-text-muted)]">
                    <Loader2 className="h-3 w-3 animate-spin" /> Loading models…
                  </div>
                )}
                {customModels.isError && (
                  <div className="px-2.5 py-2 text-xs text-red-400">
                    {(customModels.error as Error).message}
                  </div>
                )}
                {customModels.data?.models.map((model) => {
                  const key = customAgentKey(submenu.name, model);
                  return (
                    <button
                      key={model}
                      title={model}
                      onClick={() => pick({ agentKey: key })}
                      className={rowClass(key === selectedAgentKey)}
                    >
                      <span className="w-full truncate">{model}</span>
                    </button>
                  );
                })}
                {customModels.data?.models.length === 0 && (
                  <div className="px-2.5 py-2 text-xs text-[var(--color-text-muted)]">
                    No chat models available
                  </div>
                )}
              </>
            ) : (
              <>
                <button
                  onClick={() => setSubmenu(null)}
                  className="flex items-center gap-1 border-b border-[var(--color-border)] px-2.5 py-1.5 text-left text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  <ChevronLeft className="h-3 w-3" /> Back
                </button>
                <div className="sticky top-0 flex items-center gap-1 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5">
                  <Search className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                  <input
                    autoFocus
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search models..."
                    className="w-full bg-transparent text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
                  />
                </div>
                {loading && (
                  <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-[var(--color-text-muted)]">
                    <Loader2 className="h-3 w-3 animate-spin" /> Loading models…
                  </div>
                )}
                {error && <div className="px-2.5 py-2 text-xs text-red-400">{error}</div>}
                {!loading && !error && filtered.length === 0 && (
                  <div className="px-2.5 py-2 text-xs text-[var(--color-text-muted)]">
                    No models found
                  </div>
                )}
                {filtered.map((m) => (
                  <button
                    key={m.slug}
                    title={m.slug}
                    onClick={() => pick({ agentKey: `openrouter:${m.slug}` })}
                    className={`flex w-full flex-col items-start px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                      selectedAgentKey === `openrouter:${m.slug}`
                        ? "font-medium text-[var(--color-primary)]"
                        : "text-[var(--color-text)]"
                    }`}
                  >
                    <span className="w-full truncate">{m.name}</span>
                    <span className="w-full truncate text-[10px] text-[var(--color-text-muted)]">
                      {m.slug}
                      {m.prompt_price > 0 ? ` · $${m.prompt_price.toFixed(2)}/M in` : " · free"}
                    </span>
                  </button>
                ))}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
