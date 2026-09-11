import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { NoServerCard } from "@/components/NoServerCard";
import { OnchainEvidence } from "@/components/executor/OnchainEvidence";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { displayUnits, networkFeeLimit, rawUnits, spendingPolicy, type DefiResponse } from "@/lib/universal";

export function UniversalExecution() {
  const { server } = useServer();
  return server ? <ExecutionForm key={server} server={server} /> : <NoServerCard message="Select a server to use Aomi execution." />;
}

function ExecutionForm({ server }: { server: string }) {
  const catalog = useQuery({ queryKey: ["aomi-venues", server], queryFn: () => api.prepareOnchain(server, "venues"), refetchOnWindowFocus: false, refetchOnReconnect: false, retry: false });
  const [venue, setVenue] = useState("jupiter-lend");
  const [market, setMarket] = useState("");
  const [target, setTarget] = useState<{ venue: string; market: string } | null>(null);
  const [action, setAction] = useState<"deposit" | "withdraw">("deposit");
  const [amount, setAmount] = useState("2");
  const [fee, setFee] = useState("0.00001");
  const [assetBudget, setAssetBudget] = useState("2.1");
  const [nativeBudget, setNativeBudget] = useState("0.03");
  const [baseBudget, setBaseBudget] = useState("0");
  const [plan, setPlan] = useState<{ response: DefiResponse; config: Record<string, unknown> } | null>(null);
  const [executorId, setExecutorId] = useState("");
  const [pending, setPending] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState("");
  const sending = useRef(false);
  const commitSent = useRef(false);
  const selected = catalog.data?.result.venues?.find(item => item.id === venue);
  const effectiveMarket = market.trim() || selected?.example_market || "";
  const inspection = useQuery({ queryKey: ["aomi-market", server, target],
    queryFn: () => api.prepareOnchain(server, "market", { ...target }), enabled: !!target, refetchOnWindowFocus: false, refetchOnReconnect: false, retry: false });
  const detail = useQuery({ queryKey: ["aomi-execution", server, executorId],
    queryFn: () => api.getExecutor(server, executorId), enabled: !!executorId,
    refetchInterval: q => q.state.data?.status === "terminated" ? false : 1500, retry: 2 });
  const executor = detail.data;
  const previewPassed = !!executor && executor.status === "terminated" && executor.close_type === "completed" &&
    executor.config?.commit === false && executor.custom_info?.simulation_passed === true &&
    typeof executor.custom_info?.svm_plan_hash === "string" && !executor.custom_info?.error;
  const settled = executor?.status === "terminated" &&
    (executor.custom_info?.committed === true || executor.custom_info?.commit_attempted === false);
  const locked = pending || (!!executorId && executor?.status !== "terminated") || committing;
  const inspected = target?.venue === venue && target?.market === effectiveMarket ? inspection.data : undefined;
  const display = plan?.response ?? inspected;
  const marketInfo = display?.result.market;
  const positions = useQuery({ queryKey: ["aomi-position", server, target, executor?.custom_info?.committed === true],
    queryFn: () => api.prepareOnchain(server, "position", { ...target }),
    enabled: !!target && executor?.custom_info?.committed === true, refetchOnWindowFocus: false, refetchOnReconnect: false, retry: false });
  const position = positions.data?.result.position ?? display?.result.position;

  async function preview() {
    if (sending.current || !inspected?.result.market || !target) return;
    sending.current = true; setPending(true); setError("");
    try {
      const amountRaw = action === "deposit" ? rawUnits(amount.trim(), inspected.result.market.decimals) : undefined;
      if (amountRaw === "0") throw new Error("Deposit amount must be greater than zero.");
      const limit = networkFeeLimit(fee.trim());
      const response = await api.prepareOnchain(server, "prepare", { ...target, action,
        ...(action === "deposit" ? { amount_raw: amountRaw } : { withdraw_all: true }), slippage_bps: 50 });
      if (!response.result.instructions?.length || response.wallet !== inspected.wallet || response.cluster !== inspected.cluster) {
        throw new Error("Wallet or network changed during preparation. Inspect the market again.");
      }
      if (response.result.market?.address !== target.market || response.result.market.program_id !== inspected.result.market.program_id) {
        throw new Error("Prepared venue differs from the inspected market.");
      }
      const config = { chain: "svm", chain_id: 1, cluster: response.cluster, mode: "instructions",
        instructions: response.result.instructions, max_svm_network_fee_lamports: limit,
        svm_spending_policy: spendingPolicy(response, action, { asset: assetBudget.trim(), base: baseBudget.trim(), native: nativeBudget.trim() }),
        commit: false };
      setPlan({ response, config });
      const created = await api.createExecutor(server, { executor_type: "onchain_executor", config });
      if (!created.executor_id) throw new Error("The server did not return an executor ID.");
      setExecutorId(created.executor_id);
    } catch (e) { setError(e instanceof Error ? e.message : "Preparation failed."); }
    finally { sending.current = false; setPending(false); }
  }

  async function confirm() {
    if (sending.current || commitSent.current || !plan || !previewPassed) return;
    const expires = executor?.custom_info?.build_expires_at;
    if (typeof expires !== "number" || expires * 1000 <= Date.now() || (plan.response.result.expires_at ?? 0) * 1000 <= Date.now()) {
      setError("Preview expired. Refresh it before confirming."); return;
    }
    sending.current = true; commitSent.current = true; setPending(true); setCommitting(true); setError("");
    try {
      const result = await api.createExecutor(server, { executor_type: "onchain_executor", config: {
        ...plan.config, commit: true, reviewed_svm_plan_hash: executor?.custom_info?.svm_plan_hash,
      } });
      if (!result.executor_id) throw new Error("No executor ID returned.");
      setExecutorId(result.executor_id);
    } catch (e) {
      setError(`${e instanceof Error ? e.message : "Request failed."} Check Executors before retrying; this action may have been accepted.`);
    } finally { sending.current = false; setPending(false); }
  }

  function reset() {
    if (committing && !settled) return;
    setPlan(null); setExecutorId(""); setCommitting(false); setError(""); commitSent.current = false;
    void inspection.refetch();
  }

  const inputClass = "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm";
  return <div className="max-w-6xl mx-auto p-6 space-y-6">
    <Link to="/bots" className="text-sm text-[var(--color-text-muted)]">← Executors</Link>
    <header><h1 className="text-2xl font-semibold">Universal on-chain execution</h1>
      <p className="mt-2 text-[var(--color-text-muted)]">Choose a venue. Aomi prepares the protocol calls; Hummingbot runs the same executor lifecycle.</p>
    </header>
    {display?.result.local_mirror && <p role="note" className="rounded-lg border border-amber-500 p-3 text-sm">Local Solana mirror · test-wallet demonstration · no production signing claim</p>}
    {catalog.isError && <p role="alert">Aomi preparation is unavailable. Check the server’s Aomi app and preparation-service configuration.</p>}
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="rounded-xl border border-[var(--color-border)] p-5 space-y-4">
        <fieldset disabled={locked || !!plan} className="space-y-4 disabled:opacity-60">
          <label className="block text-sm">Venue<select aria-label="Venue" value={venue} onChange={e => { setVenue(e.target.value); setMarket(""); setTarget(null); }} className={inputClass}>
            {catalog.data?.result.venues?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>
          <label className="block text-sm">Market or vault address<input aria-label="Market address" value={effectiveMarket} onChange={e => { setMarket(e.target.value); setTarget(null); }} className={inputClass} /></label>
          <p className="text-xs text-[var(--color-text-muted)]">{selected?.example_label}. You can inspect another market belonging to this protocol.</p>
          <button disabled={!effectiveMarket || inspection.isFetching} onClick={() => { setError(""); setTarget({ venue, market: effectiveMarket }); }} className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm">Inspect market</button>
          {inspection.isError && <p role="alert" className="text-sm">Could not inspect this market. Verify its address and protocol.</p>}
          <label className="block text-sm">Action<select value={action} onChange={e => setAction(e.target.value as "deposit" | "withdraw")} className={inputClass}>
            <option value="deposit">{venue === "pumpswap" ? "Add liquidity" : "Supply reserves"}</option>
            <option value="withdraw">{venue === "pumpswap" ? "Remove all wallet LP shares" : "Withdraw all wallet shares"}</option>
          </select></label>
          {action === "deposit" && <label className="block text-sm">{venue === "pumpswap" ? "Target quote-token contribution" : "Asset amount"}<input aria-label="Amount" value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" className={inputClass} /></label>}
          <label className="block text-sm">Maximum estimated network fee (SOL)<input aria-label="Network fee limit" value={fee} onChange={e => setFee(e.target.value)} inputMode="decimal" className={inputClass} /></label>
          <label className="block text-sm">Maximum simulated SOL debit, including fees and account funding<input aria-label="SOL debit limit" value={nativeBudget} onChange={e => setNativeBudget(e.target.value)} inputMode="decimal" className={inputClass} /></label>
          {action === "deposit" && <label className="block text-sm">Maximum simulated {venue === "pumpswap" ? "quote-token" : "asset"} debit<input aria-label="Asset debit limit" value={assetBudget} onChange={e => setAssetBudget(e.target.value)} inputMode="decimal" className={inputClass} /></label>}
          {action === "deposit" && inspected?.result.market?.base_asset && <label className="block text-sm">Maximum simulated base-token debit{inspected.result.market.base_asset === "So11111111111111111111111111111111111111112" ? " (existing wrapped SOL)" : ""}<input aria-label="Base token debit limit" value={baseBudget} onChange={e => setBaseBudget(e.target.value)} inputMode="decimal" className={inputClass} /></label>}
        </fieldset>
        {!plan && <button disabled={!inspected || pending} onClick={() => void preview()} className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm disabled:opacity-50">Prepare and preview</button>}
        {plan && !committing && <div className="flex gap-3"><button disabled={!previewPassed || pending} onClick={() => void confirm()} className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm disabled:opacity-50">Confirm execution</button>
          <button disabled={locked} onClick={reset} className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm">Edit / refresh</button></div>}
        {committing && settled && <button onClick={reset} className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm">Start another action</button>}
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        <p className="text-xs text-[var(--color-text-muted)]">Confirmation binds the reviewed wallet, accounts and instructions. Network fees exclude rent, protocol and signing-provider charges. Position values and liquidity can change.</p>
        <p className="text-xs text-[var(--color-text-muted)]">Asset limits check simulated wallet debits and refuse incomplete evidence. Withdrawals permit the prepared wallet share balance. These are preflight checks, not an on-chain spending grant or a guarantee against later state changes.</p>
      </section>
      <div className="space-y-4">
        {marketInfo && <section aria-label="Market and wallet position" className="rounded-xl border border-[var(--color-border)] p-5 space-y-3 text-sm">
          <h2 className="font-semibold">{marketInfo.label}</h2>
          <p className="text-xs break-all">Wallet: {display?.wallet}<br />Network: {display?.cluster}<br />Market: {marketInfo.address}<br />Asset: {marketInfo.asset}<br />Program: {marketInfo.program_id}</p>
          {marketInfo.created_at && <p>Vault created: {new Date(Number(marketInfo.created_at) * 1000).toLocaleDateString()}</p>}
          {marketInfo.fees && <div className="space-y-1 text-xs">
            <p>Management fee: {Number(marketInfo.fees.management_bps) / 100}% · Performance fee: {Number(marketInfo.fees.performance_bps) / 100}%</p>
            <p>Vault withdrawal charge: {displayUnits(marketInfo.fees.withdrawal_raw, marketInfo.decimals)} asset units + {Number(marketInfo.fees.withdrawal_bps) / 100}%</p>
            <p>Global withdrawal charge: {displayUnits(marketInfo.fees.global_withdrawal_raw, marketInfo.decimals)} asset units + {Number(marketInfo.fees.global_withdrawal_bps) / 100}%</p>
          </div>}
          {position && <div className="space-y-1"><h3 className="font-medium">Wallet position</h3>
            {position.shares_raw !== undefined && <p>Shares: {marketInfo.share_decimals !== undefined ? displayUnits(position.shares_raw, marketInfo.share_decimals) : `${position.shares_raw} raw units`}</p>}
            {position.staked_shares !== undefined && <p>Staked shares: {position.staked_shares} · Unstaked shares: {position.unstaked_shares}</p>}
            {position.underlying !== undefined && <p>Estimated underlying: {position.underlying}</p>}
            {position.underlying_raw !== undefined && <p>Estimated underlying: {displayUnits(position.underlying_raw, marketInfo.decimals)}</p>}
            <p className="text-xs text-[var(--color-text-muted)]">Wallet-wide holdings include positions acquired elsewhere. They are not trading profit.</p>
          </div>}
          {(plan?.response.result.maximum_inputs ?? []).map(row => <p key={row.asset} className="text-xs break-all">Maximum input: {displayUnits(row.amount_raw, row.decimals)} · {row.asset}</p>)}
          {(plan?.response.result.minimum_outputs ?? []).map(row => <p key={row.asset} className="text-xs break-all">Minimum output: {displayUnits(row.amount_raw, row.decimals)} · {row.asset}</p>)}
          {display?.result.warnings?.map((warning, i) => <p key={i} className="text-xs text-[var(--color-text-muted)]">{warning}</p>)}
          <button disabled={positions.isFetching || !target} onClick={() => void positions.refetch()} className="rounded-lg border border-[var(--color-border)] px-3 py-1 text-xs">Refresh wallet position</button>
          {positions.isError && <p role="alert">Position refresh failed; displayed holdings may be stale.</p>}
        </section>}
        {executor ? <OnchainEvidence executor={executor} walletOnly assetLabels={marketInfo ? {
          [marketInfo.asset]: marketInfo.asset === "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" ? "USDC" : "Underlying asset",
          [marketInfo.share_mint]: "Position shares",
        } : {}} /> : <p className="text-sm text-[var(--color-text-muted)]">{pending ? "Preparing the action and waiting for simulation…" : "Inspect a market, then preview before confirming."}</p>}
        {detail.isError && <p role="alert">Unable to refresh the executor. Check Executors before submitting again.</p>}
        {executorId && <p className="text-xs break-all">Executor: {executorId}</p>}
      </div>
    </div>
  </div>;
}
