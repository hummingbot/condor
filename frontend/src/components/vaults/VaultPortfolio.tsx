/**
 * A vault's portfolio: what its wallet holds, and what it is in.
 *
 * The same two questions the Portfolio page asks about a Condor account, asked
 * about one wallet — because that is what a vault is. **Assets** is the wallet's
 * balances; **LP positions** is the liquidity it still has out in pools, which
 * is where a running LP strategy keeps most of its money and which a balance
 * list does not show at all. A vault whose assets read near zero while three
 * positions are open is the ordinary case, not an alarming one, and a page that
 * only had the first half said the opposite.
 *
 * Public, like the rest of the vault page: a vault is an account on a public
 * chain, and its balances are the most public thing about it. Nothing here
 * needs a signature, so nothing here waits for one.
 *
 * No chain grouping and no dropdowns: a vault's wallet is one Solana address.
 * The Portfolio page groups by connector because an account has many; here
 * that would be one group with everything in it.
 */
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeftRight, Coins, Droplets, ExternalLink, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { isTokenized } from "@/components/vaults/format";
import { CopyAddress } from "@/components/vaults/shared";
import { TransferDrawer, type TransferAsset } from "@/components/vaults/TransferDrawer";
import { useCanSign } from "@/hooks/useCanSign";
import { api, type VaultInfo, type VaultLpPosition } from "@/lib/api";

/** The Gateway network the DEX workspace addresses Solana pools by. */
const DEX_NETWORK = "solana-mainnet-beta";

type Tab = "assets" | "lp";

/** Gateway answers balances as `{mint: amount}`; a future shape might answer
 *  with rows. One flattening here rather than a branch at every use. */
function assetRows(
  balances: undefined | Record<string, number> | { mint: string; amount: string }[],
): { token: string; amount: number }[] {
  if (!balances) return [];
  const rows = Array.isArray(balances)
    ? balances.map((row) => ({ token: row.mint, amount: Number(row.amount) }))
    : Object.entries(balances).map(([token, amount]) => ({ token, amount: Number(amount) }));
  return rows.filter((row) => Number.isFinite(row.amount) && row.amount > 0);
}

const num = (value: string | number | undefined, max = 6): string => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: max });
};

const short = (value: string) => `${value.slice(0, 4)}…${value.slice(-4)}`;

/** Native SOL's mint. A vault quoted in it is funded with the chain's own
 *  money, which the deposit route takes as "no mint at all". */
const SOL_MINT = "So11111111111111111111111111111111111111112";

/** A position's pair, however its protocol names it. Mints are unreadable, so
 *  a `trading_pair` is preferred and a mint is truncated rather than shown
 *  whole — the copyable address is beside it either way. */
function pairLabel(position: VaultLpPosition): string {
  if (position.trading_pair) {
    return position.trading_pair
      .split("-")
      .map((part) => (part.length > 12 ? short(part) : part))
      .join(" / ");
  }
  const base = position.base_token ?? position.base_token_address;
  const quote = position.quote_token ?? position.quote_token_address;
  if (!base || !quote) return short(position.pool_address);
  return `${base.length > 12 ? short(base) : base} / ${quote.length > 12 ? short(quote) : quote}`;
}

export function VaultPortfolio({ vault }: { vault: VaultInfo }) {
  const [tab, setTab] = useState<Tab>("assets");
  const [moving, setMoving] = useState<TransferAsset | null>(null);
  const { canSign } = useCanSign();
  // Only a private vault's assets are its creator's to move. Once it has
  // holders the way out is a redemption after a wind-down, and a Transfer
  // button would be offering something the program refuses.
  const movable = canSign && !isTokenized(vault);

  const holdings = useQuery({
    queryKey: ["vault-holdings", vault.server, vault.account],
    queryFn: () => api.getVaultHoldings(vault.account, vault.server),
    retry: false,
    staleTime: 15_000,
  });

  const lp = useQuery({
    queryKey: ["vault-lp-positions", vault.server, vault.account],
    queryFn: () => api.getVaultLpPositions(vault.account, vault.server),
    retry: false,
    staleTime: 15_000,
  });

  const assets = assetRows(holdings.data?.balances);
  const positions = lp.data?.positions ?? [];
  const retainedSupply = holdings.data?.retained_supply;
  // Where this vault's own token trades, once its curve has graduated.
  const pool = vault.chain?.damm_pool ?? holdings.data?.damm_pool ?? null;

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
        <div>
          <p className="text-[11px] text-[var(--color-text-muted)]">The vault&rsquo;s wallet</p>
          <CopyAddress address={vault.treasury_address} label="Treasury" />
        </div>
        <nav className="flex gap-1">
          {(
            [
              { key: "assets" as const, label: "Assets", icon: Coins, count: assets.length },
              {
                key: "lp" as const,
                label: "LP positions",
                icon: Droplets,
                count: positions.length,
              },
            ] satisfies { key: Tab; label: string; icon: typeof Coins; count: number }[]
          ).map(({ key, label, icon: Icon, count }) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              aria-current={tab === key ? "page" : undefined}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors ${
                tab === key
                  ? "bg-[var(--color-surface-hover)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              <span className="text-[11px] text-[var(--color-text-muted)]">{count}</span>
            </button>
          ))}
        </nav>
      </header>

      {tab === "assets" ? (
        <Assets
          rows={assets}
          loading={holdings.isLoading}
          error={holdings.error as Error | null}
          retainedSupply={retainedSupply}
          mint={holdings.data?.mint ?? null}
          onMove={movable ? setMoving : undefined}
          quoteMint={vault.quote_mint}
        />
      ) : (
        <Positions
          positions={positions}
          loading={lp.isLoading}
          error={lp.error as Error | null}
          errors={lp.data?.errors ?? []}
        />
      )}

      {moving && (
        <TransferDrawer
          key={moving.mint ?? moving.symbol}
          vault={vault}
          asset={moving}
          onClose={() => setMoving(null)}
        />
      )}

      {pool && (
        <div className="flex flex-wrap items-center gap-3 border-t border-[var(--color-border)] px-4 py-2.5">
          <Link to={`/dex/${DEX_NETWORK}/${pool}`} className="text-[12px] underline">
            Open its pool — chart, stats and an LP executor
          </Link>
          <a
            href={`https://app.meteora.ag/damm/${pool}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[12px] text-[var(--color-text-muted)] underline"
          >
            Meteora <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      )}
    </section>
  );
}

function Assets({
  rows,
  loading,
  error,
  retainedSupply,
  mint,
  onMove,
  quoteMint,
}: {
  rows: { token: string; amount: number }[];
  loading: boolean;
  error: Error | null;
  retainedSupply: string | null | undefined;
  mint: string | null;
  /** Absent when this vault's assets are not the viewer's to move. */
  onMove?: (asset: TransferAsset) => void;
  quoteMint: string | null;
}) {
  if (loading) return <Note>Reading the chain…</Note>;
  if (error) return <Problem>{error.message}</Problem>;

  // An empty vault is the ordinary first state, not a failure: a vault is
  // created before it is funded. So it says what to do rather than reporting
  // a zero, and offers the one action that changes it.
  if (rows.length === 0) {
    return (
      <div className="px-4 py-8 text-center">
        <Coins className="mx-auto mb-3 h-7 w-7 text-[var(--color-text-muted)]" />
        <p className="text-[13px] font-medium">This vault is empty</p>
        <p className="mx-auto mt-1 mb-3 max-w-xs text-[12px] text-[var(--color-text-muted)]">
          It holds nothing yet. Fund it and its strategy has something to trade — you can take it
          back out at any time while the vault is private.
        </p>
        {onMove && (
          <button
            type="button"
            onClick={() =>
              onMove({
                symbol: quoteMint && quoteMint !== SOL_MINT ? "the quote asset" : "SOL",
                mint: quoteMint && quoteMint !== SOL_MINT ? quoteMint : undefined,
                vaultAmount: 0,
              })
            }
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-[12px] font-medium text-white"
          >
            <Plus className="h-3.5 w-3.5" />
            Fund this vault
          </button>
        )}
      </div>
    );
  }

  return (
    <>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-[11px] text-[var(--color-text-muted)]">
            <th className="px-4 py-2 text-left font-medium">Token</th>
            <th className="px-4 py-2 text-right font-medium">Amount</th>
            {onMove && <th className="w-10 px-4 py-2" />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.token} className="border-t border-[var(--color-border)]">
              <td className="px-4 py-2">
                {row.token.length > 12 ? (
                  <CopyAddress address={row.token} />
                ) : (
                  <span className="font-medium">{row.token}</span>
                )}
                {mint && row.token === mint && (
                  <span className="ml-2 rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]">
                    its own token
                  </span>
                )}
              </td>
              <td className="px-4 py-2 text-right font-mono">{num(row.amount)}</td>
              {onMove && (
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    title={`Move ${row.token} in or out`}
                    onClick={() =>
                      onMove({
                        symbol: row.token.length > 12 ? short(row.token) : row.token,
                        // Gateway reads the holdings back by symbol for SOL and
                        // by mint for everything else; "SOL" here means the
                        // chain's own money, which takes no mint.
                        mint: row.token === "SOL" ? undefined : row.token,
                        vaultAmount: row.amount,
                      })
                    }
                    className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
                  >
                    <ArrowLeftRight className="h-3.5 w-3.5" />
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {retainedSupply && Number(retainedSupply) > 0 && (
        <p className="border-t border-[var(--color-border)] px-4 py-2 text-[11px] text-[var(--color-text-muted)]">
          {num(retainedSupply)} of its own token is retained supply — held by the vault, not circulating,
          and left out of what a redemption divides by.
        </p>
      )}
    </>
  );
}

function Positions({
  positions,
  loading,
  error,
  errors,
}: {
  positions: VaultLpPosition[];
  loading: boolean;
  error: Error | null;
  errors: string[];
}) {
  if (loading) return <Note>Asking each protocol…</Note>;
  if (error) return <Problem>{error.message}</Problem>;

  return (
    <>
      {positions.length === 0 ? (
        <Note>No open liquidity positions.</Note>
      ) : (
        <div className="divide-y divide-[var(--color-border)]">
          {positions.map((position) => (
            <article
              key={position.position_address ?? `${position.protocol}:${position.pool_address}`}
              className="px-4 py-3"
            >
              <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[13px] font-medium">{pairLabel(position)}</span>
                <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-muted)]">
                  <span className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 uppercase">
                    {position.protocol} {position.kind}
                  </span>
                  <CopyAddress address={position.pool_address} label="Pool" />
                </span>
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] sm:grid-cols-4">
                <Field label="Base">{num(position.base_token_amount)}</Field>
                <Field label="Quote">{num(position.quote_token_amount)}</Field>
                {position.lower_price !== undefined && (
                  <Field label="Range">
                    {num(position.lower_price, 8)} – {num(position.upper_price, 8)}
                  </Field>
                )}
                {(position.base_fee_amount !== undefined ||
                  position.quote_fee_amount !== undefined) && (
                  <Field label="Uncollected fees">
                    {num(position.base_fee_amount)} / {num(position.quote_fee_amount)}
                  </Field>
                )}
              </dl>
            </article>
          ))}
        </div>
      )}

      {/* A protocol that refused to answer is not the same as a protocol with
          nothing in it, and only one of those is good news. Meteora's DAMM v2
          is the only AMM that can enumerate a wallet's positions at all — the
          fungible-LP ones say so here rather than reading as empty. */}
      {errors.length > 0 && (
        <details className="border-t border-[var(--color-border)] px-4 py-2">
          <summary className="flex cursor-pointer items-center gap-1.5 text-[11px] text-[var(--color-text-muted)]">
            <AlertTriangle className="h-3 w-3" />
            {errors.length} protocol{errors.length === 1 ? "" : "s"} could not be read
          </summary>
          <ul className="mt-1.5 space-y-0.5">
            {errors.map((message) => (
              <li key={message} className="text-[11px] text-[var(--color-text-muted)]">
                {message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[var(--color-text-muted)]">{label}</dt>
      <dd className="font-mono text-[var(--color-text)]">{children}</dd>
    </div>
  );
}

const Note = ({ children }: { children: React.ReactNode }) => (
  <p className="px-4 py-6 text-center text-[12px] text-[var(--color-text-muted)]">{children}</p>
);

const Problem = ({ children }: { children: React.ReactNode }) => (
  <p className="px-4 py-4 text-[12px] text-red-600 dark:text-red-400">{children}</p>
);
