import { useQuery } from "@tanstack/react-query";
import { Circle, Lock, Server } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, type ServerInfo } from "@/lib/api";

/**
 * Which account this conversation trades on — and whether that is yours to change.
 *
 * The old chip showed a server name and nothing else, which hid two different
 * facts behind one word:
 *
 * - **ambient** — the conversation inherited the top-right `ServerSelector` at
 *   birth. Flipping that selector afterwards does nothing to a live chat, so
 *   the only honest control is one that acts on *this* conversation: picking a
 *   server here respawns it against that server and keeps the transcript.
 * - **pinned** — the bound Agent's front matter names the server and wins over
 *   any ambient choice, so there is nothing to pick. The chip says who pinned
 *   it and links to the one page that owns the decision.
 *
 * Lives here rather than inline so the workspace tab and the overlay panel
 * cannot drift apart on what the chip means.
 */
export function SessionServerChip({
  serverName,
  pinned = false,
  agentSlug = "",
  label = "",
  disabled = false,
  onSelect,
}: {
  serverName?: string;
  pinned?: boolean;
  /** The Agent that pinned it — the chip links to its page. */
  agentSlug?: string;
  /** That Agent's display name, for the tooltip. */
  label?: string;
  /** Streaming or still spawning: a respawn now would drop work in flight. */
  disabled?: boolean;
  onSelect: (serverName: string) => void;
}) {
  const [open, setOpen] = useState(false);

  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
    // Only while the menu is open: the chip is not a monitor, and the page's
    // own ServerSelector already keeps this cache warm.
    enabled: open,
  });

  if (!serverName) return null;

  const chipBase =
    "flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] transition-colors";

  if (pinned) {
    const pinnedBy = label || agentSlug;
    const chip = (
      <span
        className={`${chipBase} border-emerald-500/30 bg-emerald-500/10 text-emerald-400`}
        title={
          pinnedBy
            ? `Pinned by ${pinnedBy} — change it on the agent's page`
            : "Pinned server"
        }
      >
        <Lock className="h-2.5 w-2.5 shrink-0" />
        <span className="max-w-[120px] truncate">{serverName}</span>
      </span>
    );
    // Only linkable when we know which agent owns the pin; the chip is still
    // worth showing without it.
    return agentSlug ? (
      <Link to={`/agents/${agentSlug}`} className="shrink-0">
        {chip}
      </Link>
    ) : (
      chip
    );
  }

  const online = servers?.filter((s) => s.online) ?? [];
  const offline = servers?.filter((s) => !s.online) ?? [];

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={
          disabled
            ? "Finish this turn before switching server"
            : `Running against ${serverName} — click to switch this conversation`
        }
        className={`${chipBase} border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/40 hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50`}
      >
        <Server className="h-2.5 w-2.5 shrink-0" />
        <span className="max-w-[120px] truncate">{serverName}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            role="listbox"
            className="absolute right-0 top-full z-50 mt-1 min-w-[200px] rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 shadow-lg"
          >
            <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Move this chat to
            </div>
            {online.map((s: ServerInfo) => (
              <button
                key={s.name}
                onClick={() => {
                  setOpen(false);
                  if (s.name !== serverName) onSelect(s.name);
                }}
                className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                  s.name === serverName
                    ? "font-medium text-[var(--color-primary)]"
                    : "text-[var(--color-text)]"
                }`}
              >
                <Circle className="h-2 w-2 shrink-0 fill-current text-[var(--color-green)]" />
                <span className="truncate">{s.name}</span>
              </button>
            ))}
            {/* An offline server would spawn a subprocess with nothing to talk
                to, so it is listed for orientation but not selectable. */}
            {offline.map((s: ServerInfo) => (
              <div
                key={s.name}
                className="flex w-full cursor-not-allowed items-center gap-2 px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] opacity-50"
                title="Offline"
              >
                <Circle className="h-2 w-2 shrink-0 fill-current text-[var(--color-text-muted)]" />
                <span className="truncate">{s.name}</span>
              </div>
            ))}
            {servers && servers.length === 0 && (
              <div className="px-2.5 py-1.5 text-xs text-[var(--color-text-muted)]">
                No servers configured
              </div>
            )}
            <div className="mt-0.5 border-t border-[var(--color-border)] px-2.5 py-1.5 text-[10px] leading-snug text-[var(--color-text-muted)]">
              Switching restarts the agent's tools on the new server. The
              conversation is kept.
            </div>
          </div>
        </>
      )}
    </div>
  );
}
