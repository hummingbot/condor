/**
 * What a vault's wallet holds, and where its own token trades.
 *
 * The vault's own token appears here like any other balance, because after
 * graduation that is exactly what it is: the unsold supply sits in the same
 * wallet as the capital, so the runner can market-make their own token — an LP
 * position against its own pool, earning fees for the vault and deepening the
 * market its holders exit through. The one thing the treasury line has to say
 * that a balance row would not is that those tokens are **not circulating**,
 * because that is what keeps a redemption honest.
 */
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";

import { CopyAddress } from "@/components/vaults/shared";
import { api, type VaultHoldings as VaultHoldingsData, type VaultInfo } from "@/lib/api";

/** The Gateway network the DEX workspace addresses Solana pools by. */
const DEX_NETWORK = "solana-mainnet-beta";

/**
 * Gateway answers with a `{mint: amount}` map; a future shape might answer with
 * rows. One flattening here rather than a branch at every use.
 */
function asRows(balances: VaultHoldingsData["balances"]): [string, string][] {
  if (Array.isArray(balances)) {
    return balances.map((row) => [row.mint, String(row.amount)]);
  }
  return Object.entries(balances).map(([mint, amount]) => [mint, String(amount)]);
}

export function VaultHoldings({ vault }: { vault: VaultInfo }) {
  const holdings = useQuery({
    queryKey: ["vault-holdings", vault.account],
    queryFn: () => api.getVaultHoldings(vault.account),
    retry: false,
    staleTime: 15_000,
  });

  const rows = holdings.data?.balances;
  const pool = vault.chain?.damm_pool ?? holdings.data?.damm_pool ?? null;

  return (
    <section className="mb-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        Holdings
      </h2>

      {holdings.isLoading && (
        <p className="text-[12px] text-[var(--color-text-muted)]">Reading the chain…</p>
      )}
      {holdings.error && (
        <p className="text-[12px] text-red-600 dark:text-red-400">
          {(holdings.error as Error).message}
        </p>
      )}

      {rows && (
        <table className="w-full text-[12px]">
          <tbody>
            {asRows(rows).map(([mint, amount]) => (
              <tr key={mint} className="border-b border-[var(--color-border)] last:border-0">
                <td className="py-1.5">
                  <CopyAddress address={mint} />
                  {mint === holdings.data?.mint && (
                    <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">
                      this vault&rsquo;s own token
                    </span>
                  )}
                </td>
                <td className="py-1.5 text-right font-mono">{amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {holdings.data?.treasury && holdings.data.treasury !== "0" && (
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          <span className="font-mono">{holdings.data.treasury}</span> of that is treasury — supply
          that was never sold. It is not circulating and does not dilute a redemption, and it is
          the inventory an LP position on this vault&rsquo;s own pool market-makes with: as buyers
          arrive it becomes quote, in the vault.
        </p>
      )}

      {pool && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Link
            to={`/dex/${DEX_NETWORK}/${pool}`}
            className="inline-flex items-center gap-1 text-[12px] underline"
          >
            Open the pool — chart, stats and an LP executor
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
