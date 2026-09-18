/**
 * The vaults this account runs, in Portfolio.
 *
 * A vault's assets are not in any hummingbot-api account: they live in a wallet
 * wallet owned by the vault program, which is why they are read here from the
 * chain rather than folded into the accounts table above. Keeping them a
 * separate section is also the honest presentation — money in a tokenized vault
 * is not the creator's any more, and adding it to a personal total would say
 * otherwise.
 */
import { useQueries, useQuery } from "@tanstack/react-query";
import { Vault as VaultIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { PhaseBadge, StateBadge } from "@/components/vaults/shared";
import { api, type VaultInfo } from "@/lib/api";

export function VaultsSection({ server }: { server: string }) {
  const vaults = useQuery({
    queryKey: ["vaults", server],
    queryFn: () => api.listVaults(server),
    retry: false,
    // A wallet that has never been attached answers 409; that is not an error
    // worth showing on a page about something else.
    throwOnError: false,
  });

  const rows = vaults.data ?? [];
  const holdings = useQueries({
    queries: rows.map((vault) => ({
      queryKey: ["vault-holdings", vault.server, vault.account],
      queryFn: () => api.getVaultHoldings(vault.account, vault.server),
      retry: false,
      staleTime: 30_000,
    })),
  });

  if (!rows.length) return null;

  return (
    <section className="mt-6">
      <div className="mb-2 flex items-center gap-2">
        <VaultIcon className="h-4 w-4 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold">Vaults</h2>
        <span className="text-[11px] text-[var(--color-text-muted)]">
          held by the program, not by this account
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
        <table className="w-full text-[12px]">
          <thead className="bg-[var(--color-surface)] text-left text-[11px] text-[var(--color-text-muted)]">
            <tr>
              <th className="px-3 py-2 font-medium">Vault</th>
              <th className="px-3 py-2 font-medium">Phase</th>
              <th className="px-3 py-2 font-medium">State</th>
              <th className="px-3 py-2 text-right font-medium">Assets</th>
              <th className="px-3 py-2 text-right font-medium">Retained supply</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((vault: VaultInfo, index) => {
              const held = holdings[index]?.data;
              const balances = held?.balances;
              const assetCount = balances
                ? Array.isArray(balances)
                  ? balances.length
                  : Object.keys(balances).length
                : null;
              return (
                <tr
                  key={vault.account}
                  className="border-t border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
                >
                  <td className="px-3 py-2">
                    <Link to={`/vaults/${vault.account}`} className="font-medium hover:underline">
                      {vault.label || "Untitled vault"}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <PhaseBadge vault={vault} />
                  </td>
                  <td className="px-3 py-2">
                    <StateBadge vault={vault} />
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {assetCount === null ? "—" : assetCount}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono"
                    title="Unsold supply of the vault's own token. Not circulating; it is the inventory an LP position would market-make with."
                  >
                    {held?.retained_supply && held.retained_supply !== "0" ? held.retained_supply : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
