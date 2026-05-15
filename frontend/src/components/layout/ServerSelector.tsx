import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Circle, Server } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api, type ServerInfo } from "@/lib/api";

export function ServerSelector() {
  const { server, setServer } = useServer();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

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

  // Auto-select first online server only when no server is saved yet
  useEffect(() => {
    if (!server && onlineServerNames.length > 0) {
      setServer(onlineServerNames[0]);
    }
  }, [server, onlineServerNames, setServer]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const current = servers?.find((s) => s.name === server);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 text-sm hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <span className="truncate max-w-[120px]">{current?.name || "No server"}</span>
        {current?.online && (
          <Circle className="h-1.5 w-1.5 shrink-0 fill-current text-[var(--color-green)]" />
        )}
        <ChevronDown className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && servers && (
        <div className="absolute right-0 top-full z-50 mt-1.5 min-w-[220px] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-xl">
          <div className="px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            API Servers ({onlineServers.length}/{totalCount} online)
          </div>
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
        </div>
      )}
    </div>
  );
}
