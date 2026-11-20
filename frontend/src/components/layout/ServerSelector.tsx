import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Circle, Server, Star } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { fallbackServer, useServer } from "@/hooks/useServer";
import { api, type ServerInfo } from "@/lib/api";

export function ServerSelector() {
  const { server, setServer } = useServer();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  const { data: servers, dataUpdatedAt, isError } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
    refetchInterval: 10000,
  });

  const onlineServers = servers?.filter((s) => s.online) ?? [];
  const offlineServers = servers?.filter((s) => !s.online) ?? [];
  const totalCount = servers?.length ?? 0;

  // Where to land when nothing usable is selected. The backend's default is
  // the same `chat_defaults` entry Telegram sets, so setting a default in
  // either surface is honoured by the other.
  const fallback = servers ? fallbackServer(servers) : null;
  const known = !!servers;
  const exists = servers?.some((s) => s.name === server) ?? false;

  // When the current selection was made. A name is only called missing by a
  // list fetched *after* it was selected: a server picked a moment after it was
  // added can be newer than the cached list, and must not be thrown out by it.
  const selectedAt = useRef(0);
  useEffect(() => {
    selectedAt.current = Date.now();
  }, [server]);

  // Auto-select when no server is saved yet — and when the saved one is gone.
  // A persisted name that was deleted, renamed or unshared used to stay
  // selected for good: the header said "No server" while every page kept
  // querying the dead name (FU-09). A server that is merely offline still
  // exists and stays selected; that is for the user to see, not to undo.
  useEffect(() => {
    if (!known || exists) return;
    if (server && dataUpdatedAt < selectedAt.current) return;
    if (fallback !== server) setServer(fallback);
  }, [known, exists, server, fallback, dataUpdatedAt, setServer]);

  const current = servers?.find((s) => s.name === server);

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        title={current && !current.online ? `${current.name} is offline` : undefined}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 text-sm hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <span className="truncate max-w-[120px]">{current?.name || "No server"}</span>
        {current && (
          <Circle
            className={`h-1.5 w-1.5 shrink-0 fill-current ${
              current.online ? "text-[var(--color-green)]" : "text-[var(--color-red)]"
            }`}
            aria-label={current.online ? "Online" : "Offline"}
          />
        )}
        <ChevronDown className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        align="right"
        className="min-w-[220px] py-1"
      >
        <div className="px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          {servers ? `API Servers (${onlineServers.length}/${totalCount} online)` : "API Servers"}
        </div>
        {!servers && (
          <div className="px-3 py-2 text-xs text-[var(--color-text-muted)]">
            {isError ? "Could not load servers" : "Loading…"}
          </div>
        )}
        {onlineServers.map((s: ServerInfo) => (
          <button
            key={s.name}
            onClick={() => {
              setServer(s.name);
              setOpen(false);
            }}
            className={`flex w-full items-center gap-2.5 px-3 py-2 text-sm transition-colors hover:bg-[var(--color-surface-hover)] ${
              s.name === server
                ? "bg-[var(--color-primary-fill)]/10 text-[var(--color-primary)]"
                : ""
            }`}
          >
            <Circle className="h-2 w-2 shrink-0 fill-current text-[var(--color-green)]" />
            <span className="truncate">{s.name}</span>
            {s.is_default && (
              <Star
                className="h-2.5 w-2.5 shrink-0 fill-current text-amber-400"
                aria-label="Default server"
              />
            )}
            {s.name === server && (
              <span className="ml-auto text-[10px] font-medium uppercase text-[var(--color-primary)]">Active</span>
            )}
          </button>
        ))}
        {offlineServers.length > 0 && onlineServers.length > 0 && (
          <div className="mx-3 my-1 border-t border-[var(--color-border)]" />
        )}
        {offlineServers.length > 0 && (
          <div className="px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Offline
          </div>
        )}
        {offlineServers.map((s: ServerInfo) => (
          <div
            key={s.name}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-sm text-[var(--color-text-muted)] opacity-50 cursor-not-allowed"
          >
            <Circle className="h-2 w-2 shrink-0 fill-current text-[var(--color-text-muted)]" />
            <span className="truncate">{s.name}</span>
          </div>
        ))}
      </AnchoredMenu>
    </>
  );
}
