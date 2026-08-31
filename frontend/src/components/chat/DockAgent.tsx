import { Bot, Brain, ChevronDown, ChevronRight, Lock, Server } from "lucide-react";
import { useState } from "react";

import {
  BrainMenuBody,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ServerMenuBody } from "@/components/chat/ServerMenu";
import { BUSY_MODEL, BUSY_SERVER, wiring } from "@/components/chat/sessionWiring";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useBrainLabel } from "@/hooks/useBrainLabel";
import type { ChatSlot } from "@/hooks/useChatSocket";
import type {
  AgentBindingOption,
  ChatAgentOption,
  CustomProvider,
} from "@/lib/api";

/**
 * Who is answering this conversation, at the top of the column that already
 * says what the conversation is doing.
 *
 * It sits with Tasks and Routines rather than inside the panel it opens, so
 * every door in this workspace is on the same side of the window: you click a
 * routine here and its report opens in the pane to the left, and now you click
 * the agent here and the agent opens the same way. The panel used to be
 * reached from a button in the chat header, which meant clicking left to make
 * something appear on the right — the one transition the reader had to learn
 * twice.
 *
 * The card is also the *only* place the two switches live. The panel beside it
 * is what the agent knows; this is what the conversation runs on, and it stays
 * on screen while the panel is open (see `ContextDock`'s `borrowable`) so the
 * model can be changed while reading what the model is.
 */
export function DockAgentCard({
  name,
  description,
  slot,
  pendingAgentKey,
  ambientServer,
  agents,
  customProviders,
  agentBindings,
  isStreaming,
  open,
  onOpen,
  onSelectBrain,
  onSelectServer,
}: {
  /** Who is on the other end — the bound Agent, or Condor. */
  name: string;
  description?: string;
  slot: ChatSlot | null;
  /** The model the next conversation starts on, before there is one. */
  pendingAgentKey: string;
  /** The page's ambient server selection, before there is a session. */
  ambientServer: string;
  agents: ChatAgentOption[];
  customProviders: CustomProvider[];
  agentBindings: AgentBindingOption[];
  isStreaming: boolean;
  /** Whether the agent's panel is the one in the pane. */
  open: boolean;
  onOpen: () => void;
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
    <div className="shrink-0 border-b border-[var(--color-border)] px-2.5 py-2">
      {/* The whole identity is the door: the name of the agent is also the way
          to read it, the way a routine's row is the way to read its report. */}
      <button
        onClick={onOpen}
        aria-pressed={open}
        title={`${name} — read and change what this agent is`}
        className={`group flex w-full items-start gap-1.5 rounded px-1 py-1 text-left transition-colors hover:bg-[var(--color-surface-hover)] ${
          open ? "bg-[var(--color-surface-hover)]" : ""
        }`}
      >
        <Bot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-accent)]" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-semibold text-[var(--color-text)]">
            {name}
          </span>
          {/* No `block` beside the clamp: `line-clamp` is a display of its own,
              and whichever Tailwind emits last wins — the clamp lost silently. */}
          {description && (
            <span className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-[var(--color-text-muted)]">
              {description}
            </span>
          )}
        </span>
        <ChevronRight
          className={`mt-0.5 h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
      </button>

      {/* One row, because they are one question: what answers, and where it
          trades. Two stacked rows of `LABEL  value` read as a spec sheet; two
          fields side by side read as the pair of pickers they are. */}
      <div className="mt-1.5 flex items-stretch gap-1.5">
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
  /** The untruncated value, for the tooltip — a button is 100px wide here. */
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
  const shared = `flex min-w-0 flex-1 basis-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-1 text-[11px] ${
    tone === "pinned"
      ? "text-emerald-400"
      : "text-[var(--color-text)]"
  }`;

  const body = (
    <>
      <span className="shrink-0 text-[var(--color-text-muted)]">{icon}</span>
      <span className="min-w-0 flex-1 truncate text-left">{value}</span>
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
      {/* Portalled, like every menu in this workspace: the dock and `main`
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
