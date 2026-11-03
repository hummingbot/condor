import { Brain, ChevronDown, Lock, Server } from "lucide-react";
import { useState } from "react";

import {
  BrainMenuBody,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ServerMenuBody } from "@/components/chat/ServerMenu";
import {
  BUSY_MODEL,
  BUSY_SERVER,
  wiring,
} from "@/components/chat/sessionWiring";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useBrainLabel } from "@/hooks/useBrainLabel";
import type { ChatSlot } from "@/hooks/useChatSocket";
import type {
  AgentBindingOption,
  ChatAgentOption,
  CustomProvider,
} from "@/lib/api";

/**
 * What the conversation runs on: the model that answers, and the server it
 * trades against.
 *
 * These are the *wiring*, not the agent — which is why they sit in the agent
 * panel's own bar rather than in the dock card that opens it. The card names
 * one thing, the agent, in one line; everything else about that agent is a
 * click away, and this pair is the first thing waiting on the other side of
 * the click, at the top of the panel it opens.
 *
 * They lived in the dock card until the card was cut back to the name alone.
 * The card could not both be a one-line identity and carry two switches, and
 * of the two the switches are the ones that already have somewhere to be: the
 * panel is on screen for exactly as long as anyone is thinking about what this
 * agent is made of.
 */
export function AgentWiring({
  slot,
  pendingAgentKey,
  ambientServer,
  agents,
  customProviders,
  agentBindings,
  isStreaming,
  onSelectBrain,
  onSelectServer,
}: {
  slot: ChatSlot | null;
  /** The model the next conversation starts on, before there is one. */
  pendingAgentKey: string;
  /** The page's ambient server selection, before there is a session. */
  ambientServer: string;
  agents: ChatAgentOption[];
  customProviders: CustomProvider[];
  agentBindings: AgentBindingOption[];
  isStreaming: boolean;
  onSelectBrain: (selection: BrainSelection) => void;
  onSelectServer: (serverName: string) => void;
}) {
  // Streaming or still spawning: both switches respawn the session
  // underneath, which would drop the turn in flight.
  const busy = !!slot && (slot.pending || isStreaming);
  const { agentKey, serverName, pinned, pinnedBy } = wiring(
    slot,
    pendingAgentKey,
    ambientServer,
  );
  const { model, short } = useBrainLabel({
    agents,
    agentBindings,
    selectedAgentKey: agentKey,
    selectedAgentSlug: slot?.info.agent_slug || "",
  });

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <PickerField
        label="Model"
        icon={<Brain className="h-3 w-3 shrink-0" />}
        value={short}
        full={model}
        hint={slot ? undefined : "The model your next conversation starts on"}
        disabled={busy}
        disabledReason={BUSY_MODEL}
        menu={(close) => (
          <BrainMenuBody
            agents={agents}
            customProviders={customProviders}
            agentBindings={agentBindings}
            selectedAgentKey={agentKey}
            selectedAgentSlug={slot?.info.agent_slug || ""}
            onSelect={onSelectBrain}
            onClose={close}
          />
        )}
      />
      {/* A pinned server is the Agent's decision, not the session's: there is
          nothing to pick, only somewhere to go and change it. */}
      {pinned ? (
        <PickerField
          label="Server"
          icon={<Lock className="h-3 w-3 shrink-0" />}
          value={serverName}
          tone="pinned"
          hint={
            pinnedBy
              ? `Pinned by ${pinnedBy} — change it on the agent's page`
              : "Pinned by this agent's front matter"
          }
        />
      ) : (
        <PickerField
          label="Server"
          icon={<Server className="h-3 w-3 shrink-0" />}
          value={serverName || "None"}
          full={serverName}
          hint={slot ? undefined : "The server your next conversation starts on"}
          disabled={busy}
          disabledReason={BUSY_SERVER}
          // Nothing to respawn before a session exists, and the top-right
          // selector already owns the ambient choice — so it is read here,
          // not set. No dead control, and no second door to one field.
          menu={
            slot
              ? (close) => (
                  <ServerMenuBody
                    serverName={serverName}
                    onSelect={onSelectServer}
                    onClose={close}
                  />
                )
              : undefined
          }
        />
      )}
    </div>
  );
}

/**
 * One half of the pair: an icon, the value, and — when there is something to
 * pick — a chevron and the list hung off the field itself.
 *
 * A field with no `menu` keeps the frame but drops the chevron: a pinned server
 * and a chat that has not started yet both have an honest answer and nothing
 * here to change it with, and a chevron over either would be a lie the reader
 * only discovers by clicking.
 */
function PickerField({
  label,
  icon,
  value,
  full,
  hint,
  tone = "normal",
  disabled = false,
  disabledReason,
  menu,
}: {
  label: string;
  icon: React.ReactNode;
  value: string;
  /** The untruncated value, for the tooltip — the bar is narrow. */
  full?: string;
  /** Why this field cannot be changed, or what it will apply to. */
  hint?: string;
  tone?: "normal" | "pinned";
  disabled?: boolean;
  disabledReason?: string;
  menu?: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const title = disabled
    ? disabledReason
    : hint || `${label} · ${full || value}`;
  const shared = `flex min-w-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-1 text-[11px] ${
    tone === "pinned" ? "text-emerald-400" : "text-[var(--color-text)]"
  }`;

  // The cap is on the value, not on the field: a `max-w` around the whole row
  // spends its budget on the icon and the chevron first and cuts the only part
  // worth reading — which is how a 9-character server name became "briga…".
  const body = (
    <>
      <span className="shrink-0 text-[var(--color-text-muted)]">{icon}</span>
      <span className="min-w-0 max-w-[14ch] truncate text-left">{value}</span>
    </>
  );

  if (!menu) {
    return (
      <div
        className={shared}
        title={title}
        aria-label={`${label}: ${value}`}
        data-session-row={label.toLowerCase()}
      >
        {body}
      </div>
    );
  }

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${value}`}
        title={title}
        data-session-row={label.toLowerCase()}
        className={`${shared} transition-colors hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-[var(--color-border)] disabled:hover:bg-[var(--color-surface)]`}
      >
        {body}
        <ChevronDown className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
      </button>
      {/* Portalled, like every menu in this workspace: the pane and the bar
          above it both clip, and a list that opens into a scroll container
          with no overflow region is a list nobody can reach. */}
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={() => setOpen(false)}
        align="right"
        matchAnchorWidth="min"
        maxHeight={288}
        role="listbox"
        className="flex flex-col py-0.5"
      >
        {menu(() => setOpen(false))}
      </AnchoredMenu>
    </>
  );
}
