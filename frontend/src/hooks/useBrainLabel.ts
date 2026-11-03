import { useMemo } from "react";

import {
  parseCustomAgentKey,
  type AgentBindingOption,
  type ChatAgentOption,
  type OpenRouterModelOption,
} from "@/lib/api";

/** Stable empty, so the memo below is not rebuilt by a caller's default literal. */
const NO_BINDINGS: AgentBindingOption[] = [];

/** Resolve the full label for a selection, incl. openrouter:<slug>. */
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
 * Trim a model label down to what distinguishes it, for a button.
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
 * What the current selection is called, in the pieces a trigger needs.
 *
 * Shared rather than inlined in each button, because `BrainPicker` is no
 * longer the only thing that names this: the agent panel's header button says
 * the same thing about the same session (FEAT-081), and two buttons that
 * resolve "what is answering" separately are two buttons that will eventually
 * disagree — an empty `agent_key` means "whatever the bound Agent runs on",
 * which only this resolution knows.
 */
export function useBrainLabel({
  agents,
  agentBindings = NO_BINDINGS,
  selectedAgentKey,
  selectedAgentSlug = "",
  orModels,
}: {
  agents: ChatAgentOption[];
  agentBindings?: AgentBindingOption[];
  selectedAgentKey: string;
  selectedAgentSlug?: string;
  /** OpenRouter's catalogue, when the caller has it — it names the slug. */
  orModels?: OpenRouterModelOption[] | null;
}) {
  return useMemo(() => {
    const boundAgent = agentBindings.find((a) => a.slug === selectedAgentSlug);
    // An empty key means "whatever the bound Agent runs on", so the label has
    // to resolve it the same way the backend does — otherwise it would name an
    // Agent without saying what will actually answer.
    const effectiveKey = selectedAgentKey || boundAgent?.agent_key || "";
    const model = modelLabel(effectiveKey, agents, orModels);
    const short = shortModelLabel(model);
    return {
      boundAgent,
      effectiveKey,
      /** The model's full catalogue name, for a tooltip. */
      model,
      /** Just what distinguishes it, for a button beside other facts. */
      short,
      /** Who answers *and* on what — the picker's own button. */
      label: boundAgent ? `${boundAgent.name} · ${short}` : model,
    };
  }, [agents, agentBindings, selectedAgentKey, selectedAgentSlug, orModels]);
}
