import { useQuery } from "@tanstack/react-query";
import { Circle } from "lucide-react";

import { api, type ServerInfo } from "@/lib/api";

/**
 * Where this conversation trades — the list, without a trigger.
 *
 * Split out of `SessionServerChip` so the agent panel's Server row can hang
 * the same list off itself (FEAT-081). It is the *session's* server, not the
 * Agent's: flipping the page's ambient `ServerSelector` does nothing to a chat
 * that is already alive, so the only honest control is one that acts on this
 * conversation and respawns it, keeping the transcript.
 *
 * Mounted only while its menu is open — the query is unconditional here for
 * that reason, and the page's own `ServerSelector` keeps the cache warm anyway.
 */
export function ServerMenuBody({
  serverName,
  onSelect,
  onClose,
}: {
  /** The server the conversation is on now, marked in the list. */
  serverName: string;
  onSelect: (serverName: string) => void;
  onClose: () => void;
}) {
  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
  });

  const online = servers?.filter((s) => s.online) ?? [];
  const offline = servers?.filter((s) => !s.online) ?? [];

  return (
    <>
      <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        Move this chat to
      </div>
      {online.map((s: ServerInfo) => (
        <button
          key={s.name}
          onClick={() => {
            onClose();
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
    </>
  );
}
