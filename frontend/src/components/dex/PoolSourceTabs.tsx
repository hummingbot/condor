import { Search } from "lucide-react";

/**
 * Which pool listing to browse. The two upstreams take genuinely different
 * arguments — GeckoTerminal ranks a *chain*'s pools and searches by token
 * address, Gateway CLMM lists one *connector*'s pools and searches free text —
 * so they are separate tabs rather than one filter row with modes.
 */
export type PoolSource =
  | { kind: "gecko"; view: "trending" | "top" | "new" | "token" }
  | { kind: "gateway"; connector: string };

export const GECKO_TABS = [
  { view: "trending", label: "Trending" },
  { view: "top", label: "Top" },
  { view: "new", label: "New" },
  { view: "token", label: "Token" },
] as const;

/** The CLMM connectors Gateway can list pools for (and open positions in). */
export const GATEWAY_TABS = [
  { connector: "meteora", label: "Meteora" },
  { connector: "orca", label: "Orca" },
] as const;

export function sameSource(a: PoolSource, b: PoolSource): boolean {
  if (a.kind === "gecko" && b.kind === "gecko") return a.view === b.view;
  if (a.kind === "gateway" && b.kind === "gateway")
    return a.connector === b.connector;
  return false;
}

const TAB_BASE =
  "px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-px";

interface Props {
  source: PoolSource;
  onSourceChange: (s: PoolSource) => void;
  /** Gateway networks from `venues` — never a hardcoded chain list. */
  networks: string[];
  network: string;
  onNetworkChange: (n: string) => void;
  query: string;
  onQueryChange: (q: string) => void;
}

export function PoolSourceTabs({
  source,
  onSourceChange,
  networks,
  network,
  onNetworkChange,
  query,
  onQueryChange,
}: Props) {
  const searchLabel =
    source.kind === "gateway"
      ? "Filter pools by name…"
      : "Token address (mint or 0x…)";
  const searchShown = source.kind === "gateway" || source.view === "token";

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] px-4">
      <div className="flex items-center">
        {GECKO_TABS.map((t) => {
          const active = sameSource(source, { kind: "gecko", view: t.view });
          return (
            <button
              key={t.view}
              onClick={() => onSourceChange({ kind: "gecko", view: t.view })}
              className={`${TAB_BASE} ${
                active
                  ? "border-[var(--color-primary)] text-[var(--color-text)]"
                  : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {t.label}
            </button>
          );
        })}
        <span className="mx-2 h-4 w-px bg-[var(--color-border)]" />
        {GATEWAY_TABS.map((t) => {
          const active = sameSource(source, {
            kind: "gateway",
            connector: t.connector,
          });
          return (
            <button
              key={t.connector}
              onClick={() =>
                onSourceChange({ kind: "gateway", connector: t.connector })
              }
              className={`${TAB_BASE} ${
                active
                  ? "border-[var(--color-primary)] text-[var(--color-text)]"
                  : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="ml-auto flex items-center gap-2 py-1.5">
        {source.kind === "gecko" && networks.length > 0 && (
          <select
            value={network}
            onChange={(e) => onNetworkChange(e.target.value)}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs text-[var(--color-text)]"
          >
            {networks.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        )}
        {searchShown && (
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder={searchLabel}
              spellCheck={false}
              className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-1 pl-7 pr-2 text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
            />
          </div>
        )}
      </div>
    </div>
  );
}
