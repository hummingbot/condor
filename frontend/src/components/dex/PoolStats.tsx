import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";

import type { PoolSummary } from "@/lib/api";

/** The DEX web app URL for a pool — the same mapping Telegram's /lp offers. */
function poolUrl(pool: PoolSummary): string {
  const dex = pool.dex_id.toLowerCase();
  const addr = pool.address;
  if (dex.startsWith("meteora"))
    return `https://app.meteora.ag/dlmm/${addr}?referrer=hummingbot`;
  if (dex.startsWith("raydium")) return `https://raydium.io/clmm/pool/${addr}`;
  if (dex.startsWith("orca")) return `https://www.orca.so/pools/${addr}`;
  // GeckoTerminal indexes every pool it listed, so it is the universal fallback.
  return `https://www.geckoterminal.com/${pool.gecko_network}/pools/${addr}`;
}

function num(v: unknown): number {
  const n = typeof v === "string" ? parseFloat(v) : (v as number);
  return Number.isFinite(n) ? n : 0;
}

function usd(v: unknown): string {
  const n = num(v);
  if (!n) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </span>
      <span className={`text-xs tabular-nums ${tone ?? "text-[var(--color-text)]"}`}>
        {value}
      </span>
    </div>
  );
}

/** The pool's identity and market numbers, above the chart. */
export function PoolStats({ pool }: { pool: PoolSummary }) {
  const [copied, setCopied] = useState(false);
  const change = pool.price_change_24h;

  const copy = () => {
    navigator.clipboard.writeText(pool.address);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
      <Stat label="DEX" value={pool.dex_id} />
      <Stat label="TVL" value={usd(pool.reserve_usd)} />
      <Stat label="Vol 24h" value={usd(pool.volume_24h)} />
      <Stat
        label="24h"
        value={change === null || change === undefined ? "—" : `${change.toFixed(2)}%`}
        tone={
          change === null || change === undefined
            ? undefined
            : change >= 0
              ? "text-[var(--color-green)]"
              : "text-[var(--color-red)]"
        }
      />
      {pool.apr !== null && pool.apr !== undefined && (
        <Stat label="APR" value={`${pool.apr.toFixed(2)}%`} />
      )}
      {pool.bin_step !== null && pool.bin_step !== undefined && (
        <Stat label="Bin step" value={String(pool.bin_step)} />
      )}
      {pool.base_fee_percentage !== null &&
        pool.base_fee_percentage !== undefined && (
          <Stat label="Fee" value={`${pool.base_fee_percentage}%`} />
        )}

      <div className="ml-auto flex items-center gap-1.5">
        <span
          className="font-mono text-[11px] text-[var(--color-text-muted)]"
          title={pool.address}
        >
          {pool.address.slice(0, 6)}…{pool.address.slice(-4)}
        </span>
        <button
          onClick={copy}
          title="Copy pool address"
          className="rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
        </button>
        <a
          href={poolUrl(pool)}
          target="_blank"
          rel="noreferrer"
          title="Open on the DEX"
          className="rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </div>
  );
}
