import { useQuery } from "@tanstack/react-query";
import { formatUnits } from "viem";
import { api } from "@/lib/api";

function amount(raw: string | undefined, decimals: number | undefined): string {
  if (raw === undefined || !/^\d+$/.test(raw)) return "Unavailable";
  return decimals === undefined ? `${raw} raw units` : formatUnits(BigInt(raw), decimals);
}

export function LendingPositions({ server }: { server: string }) {
  const positions = useQuery({
    queryKey: ["lending-positions", server],
    queryFn: () => api.getLendingPositions(server),
    refetchInterval: 10000,
    retry: 1,
  });
  return <section className="rounded-xl border border-[var(--color-border)] p-5 space-y-3" aria-label="Lending positions">
    <div className="flex items-center justify-between"><h2 className="font-semibold">Lending positions</h2>
      <button onClick={() => void positions.refetch()} className="text-xs underline">Refresh</button>
    </div>
    <p className="text-xs text-[var(--color-text-muted)]">Completed supplies remain in this ledger. Contributions belong to the named controller; receipt-token balances cover the entire wallet and include other activity and interest.</p>
    {positions.isError ? <p role="alert" className="text-sm">Position history or the live balance is unavailable. Do not treat this as an empty position.</p> : positions.isPending ? <p className="text-sm">Reading positions…</p> : positions.data.positions.length === 0 ? <p className="text-sm">No lending contributions recorded.</p> : <ul className="space-y-4">{positions.data.positions.map((p) => <li key={[p.account_name, p.controller_id, p.chain_id, p.wallet, p.pool, p.asset].join(":")} className="border-t border-[var(--color-border)] pt-3 text-sm space-y-1">
      <p className="font-medium">{p.account_name} · {p.controller_id} · Chain {p.chain_id}</p>
      <p>Net contributions: {amount(p.net_contributed_raw, p.decimals)} {p.symbol}</p>
      <p>Wallet receipt-token balance: {amount(p.wallet_receipt_balance_raw, p.decimals)} {p.symbol}</p>
      <p className="break-all font-mono text-xs">Wallet {p.wallet}<br />Pool {p.pool}<br />Asset {p.asset}</p>
      {p.unresolved_executor_ids.length > 0 && <p role="alert" className="text-xs">{p.unresolved_executor_ids.length} unresolved action(s). Pending supply: {amount(p.pending_supply_raw, p.decimals)}; pending withdrawal: {amount(p.pending_withdraw_raw, p.decimals)}. Reconcile receipts before allocating these funds.</p>}
      {p.balance_status !== "verified_wallet_balance" && <p className="text-xs">This market’s live balance is not supported yet.</p>}
    </li>)}</ul>}
  </section>;
}
