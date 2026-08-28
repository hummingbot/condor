import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronDown, ChevronLeft, ChevronRight, Loader2, Search } from "lucide-react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
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
 *
 * `BrainPicker` only ever sets `agentKey`; `agentSlug` stays on the type
 * because the switch endpoint takes both, and rebinding is still what the
 * rail's rows do when they start a conversation.
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
 * Trim a model label down to what distinguishes it, for the button.
 *
 * The catalogue names models "<provider> — <model>", and next to a bound
 * Agent's name only the model half earns the width. "Ollama — Default Model"
 * is the exception the split gets wrong: there the provider *is* the answer.
 */
function shortModelLabel(label: string): string {
  const [provider, model] = label.split(" — ");
  if (!model) return label;
  return model.startsWith("Default") ? provider : model;
}

/**
 * "What's answering" — the model, and only the model.
 *
 * This picks the LLM, which for a bound Agent *is* its `agent_key` in AGENT.md:
 * the backend persists the pick there, so it reaches consult, delegate and its
 * loops too. It does not bind or unbind an Agent — that is a different question
 * with a different answer ("who am I talking to"), and it belongs to the rail,
 * which can also switch conversations and start one. Offering it here as well
 * gave two doors to one thing and neither of them the full gesture.
 *
 * Picker sentinels ("openrouter:", "custom:") are drill-downs, never selectable
 * keys — the backend flags them, since the key's shape does not tell you
 * ("ollama:" also ends in a colon and is a real key meaning "that backend's
 * default model").
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
  // State, not a ref: the portalled panel only gets coordinates once a render
  // has handed it the resolved trigger element.
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
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
  // An empty key means "whatever the bound Agent runs on", so the picker has to
  // resolve it the same way the backend does — otherwise the button would name
  // an Agent without saying what will actually answer.
  const effectiveKey = selectedAgentKey || boundAgent?.agent_key || "";
  const model = modelLabel(effectiveKey, agents, models);
  // Who answers *and* on what: the Agent is the identity, the model is not a
  // detail the user can be left guessing at, since picking one moves the Agent.
  const label = boundAgent
    ? `${boundAgent.name} · ${shortModelLabel(model)}`
    : model;
  const activeCustom = parseCustomAgentKey(effectiveKey);

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

  const closeAll = useCallback(() => {
    setOpen(false);
    setSubmenu(null);
    setQuery("");
  }, []);

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
    `flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
      active ? "font-medium text-[var(--color-primary)]" : "text-[var(--color-text)]"
    }`;

  /** Marks where the bound Agent sits, so "back to its default" is findable. */
  const defaultTag = (key: string) =>
    boundAgent && key && key === boundAgent.agent_key ? (
      <span className="ml-auto shrink-0 rounded bg-[var(--color-surface-hover)] px-1 py-px text-[9px] font-normal uppercase tracking-wide text-[var(--color-text-muted)]">
        default
      </span>
    ) : null;

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => (open ? closeAll() : setOpen(true))}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
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
      {/* Portalled, not `absolute`: the panel used to be a descendant of the
          trigger, so `main`'s `overflow-auto` on /agents/:slug clipped it — and
          a right-aligned 256px panel grows *leftward*, where a scroll container
          has no overflow region to scroll back into, so those pixels were gone
          for good. `maxHeight` travels as a prop because a Tailwind `max-h-*`
          in `className` loses to the inline height the portalled panel sets. */}
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={closeAll}
        align="left"
        maxHeight={288}
        matchAnchorWidth={variant === "block" ? "exact" : undefined}
        className={`flex flex-col py-0.5 ${variant === "block" ? "" : "w-64"}`}
      >
        {submenu === null ? (
          <>
            {/* An Agent's model is global — it reaches consult, delegate
                and its loops — so the consequence of picking one here is
                stated, never a silent side effect. */}
            {boundAgent &&
              sectionHeader(
                `Model — also becomes ${boundAgent.name}'s default`,
                true,
              )}
            {directAgents.map((a) => (
              <button
                key={a.key}
                onClick={() => pick({ agentKey: a.key })}
                className={rowClass(a.key === effectiveKey)}
              >
                <span className="truncate">{a.label}</span>
                {defaultTag(a.key)}
              </button>
            ))}
            {hasOpenRouter && (
              <button
                onClick={() => {
                  setSubmenu({ kind: "openrouter" });
                  loadModels();
                }}
                className={`flex w-full items-center justify-between px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                  effectiveKey.startsWith("openrouter:")
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
                  className={rowClass(key === effectiveKey)}
                >
                  <span className="truncate">{model}</span>
                  {defaultTag(key)}
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
                  effectiveKey === `openrouter:${m.slug}`
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
      </AnchoredMenu>
    </>
  );
}
