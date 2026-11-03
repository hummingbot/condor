import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Circle, Server, Star } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useServer } from "@/hooks/useServer";
import { api, type ServerInfo } from "@/lib/api";

export function ServerSelector() {
  const { server, setServer } = useServer();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
    refetchInterval: 10000,
  });

  const onlineServers = servers?.filter((s) => s.online) ?? [];
  const offlineServers = servers?.filter((s) => !s.online) ?? [];
  const totalCount = servers?.length ?? 0;

  // Memoize online server names so the useEffect dep is stable between refetches
  const onlineServerNames = useMemo(
    () => onlineServers.map((s) => s.name),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(onlineServers.map((s) => s.name))],
  );

  // The server the backend calls this user's default — the same `chat_defaults`
  // entry Telegram sets. A browser with nothing saved opens on it, so setting a
  // default in either surface is honoured by the other.
  const defaultOnlineName = useMemo(
    () => onlineServers.find((s) => s.is_default)?.name ?? null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(onlineServers.map((s) => [s.name, s.is_default]))],
  );

  // Auto-select only when no server is saved yet: the user's default if it is
  // up, otherwise the first server that is.
  useEffect(() => {
    if (!server && onlineServerNames.length > 0) {
      setServer(defaultOnlineName ?? onlineServerNames[0]);
    }
  }, [server, onlineServerNames, defaultOnlineName, setServer]);

  const current = servers?.find((s) => s.name === server);

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 text-sm hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <span className="truncate max-w-[120px]">{current?.name || "No server"}</span>
        {current?.online && (
          <Circle className="h-1.5 w-1.5 shrink-0 fill-current text-[var(--color-green)]" />
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
          <div className="px-3 py-2 text-xs text-[var(--color-text-muted)]">Loading…</div>
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
                ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
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
