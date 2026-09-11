import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { OnchainEvidence } from "@/components/executor/OnchainEvidence";
import { LendingPositions } from "@/components/executor/LendingPositions";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { BASE_USDC, lendingConfig } from "@/lib/lending";

export function Lending() {
  const { server } = useServer();
  return server ? <LendingForm key={server} server={server} /> : <NoServerCard message="Select a server to manage on-chain lending." />;
}

function LendingForm({ server }: { server: string }) {
  const [wallet, setWallet] = useState("");
  const [amount, setAmount] = useState("");
  const [action, setAction] = useState<"supply" | "withdraw">("supply");
  const [gas, setGas] = useState("1");
  const [plan, setPlan] = useState<ReturnType<typeof lendingConfig> | null>(null);
  const [executorId, setExecutorId] = useState("");
  const [committing, setCommitting] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const sending = useRef(false);
  const commitSent = useRef(false);
  const detail = useQuery({
    queryKey: ["lending-executor", server, executorId],
    queryFn: () => api.getExecutor(server, executorId),
    enabled: !!executorId,
    refetchInterval: (query) => query.state.data?.status === "terminated" ? false : 1500,
    retry: 2,
  });
  const executor = detail.data;
  const previewPassed = !!executor && executor.status === "terminated" &&
    executor.close_type === "completed" && executor.config?.commit === false &&
    executor.custom_info?.simulation_passed === true && !executor.custom_info?.error;
  const locked = pending || (!!executorId && executor?.status !== "terminated") || committing;

  async function submit(commit: boolean) {
    if (sending.current || (commit && commitSent.current)) return;
    setError("");
    try {
      const config = commit ? plan : lendingConfig(wallet.trim(), amount.trim(), action, gas.trim());
      if (!config) return;
      if (commit) {
        const expires = executor?.custom_info?.build_expires_at;
        if (!previewPassed || typeof expires !== "number" || expires * 1000 <= Date.now()) {
          throw new Error("Run a fresh successful preview before executing.");
        }
        commitSent.current = true;
        setCommitting(true);
      }
      sending.current = true;
      setPending(true);
      setExecutorId("");
      setPlan(config);
      const result = await api.createExecutor(server, {
        executor_type: "onchain_executor", config: { ...config, commit },
      });
      if (!result.executor_id) throw new Error("The server did not return an executor ID.");
      setExecutorId(result.executor_id);
    } catch (e) {
      setError(`${e instanceof Error ? e.message : "Request failed."}${commitSent.current ? " Check Executors before creating another action; the request may already have been accepted." : ""}`);
    } finally {
      sending.current = false;
      setPending(false);
    }
  }

  function edit() {
    setExecutorId("");
    setPlan(null);
    setError("");
  }

  const inputClass = "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm";
  return <div className="max-w-5xl mx-auto p-6 space-y-6">
    <Link to="/bots?population=terminated&group=type" className="text-sm text-[var(--color-text-muted)]">← Executors</Link>
    <header><h1 className="text-2xl font-semibold">Put idle reserves to work</h1>
      <p className="mt-2 text-[var(--color-text-muted)]">Supply USDC to Aave V3 on Base, or withdraw it to your signing wallet when you need trading liquidity.</p>
    </header>
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="rounded-xl border border-[var(--color-border)] p-5 space-y-4">
        <h2 className="font-semibold">USDC · Aave V3 · Base</h2>
        <p className="text-xs text-[var(--color-text-muted)] break-all">Pool {BASE_USDC.pool}<br />Asset {BASE_USDC.asset}</p>
        <fieldset disabled={locked || !!plan} className="space-y-4 disabled:opacity-60">
          <label className="block text-sm">Action<select aria-label="Action" value={action} onChange={(e) => setAction(e.target.value as "supply" | "withdraw")} className={inputClass}>
            <option value="supply">Supply reserves</option><option value="withdraw">Withdraw to wallet</option>
          </select></label>
          <label className="block text-sm">Aomi signing wallet<input value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="0x…" className={inputClass} /></label>
          <label className="block text-sm">Amount (USDC)<input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="100" className={inputClass} /></label>
          <label className="block text-sm">Maximum estimated execution gas (USDT)<input value={gas} onChange={(e) => setGas(e.target.value)} inputMode="decimal" className={inputClass} /></label>
        </fieldset>
        <p className="text-xs text-[var(--color-text-muted)]">The server selects the signer; this address must match it. Supply approves only the entered amount. Gas is an estimate and excludes Base data fees and provider surcharges.</p>
        {!plan && <button disabled={pending} onClick={() => void submit(false)} className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium disabled:opacity-50">Preview movements and approval</button>}
        {plan && !committing && <div className="flex flex-wrap gap-3">
          <button disabled={!previewPassed || pending} onClick={() => void submit(true)} className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium disabled:opacity-50">Confirm {plan.lending.action}</button>
          <button disabled={locked} onClick={edit} className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm disabled:opacity-50">Edit / refresh preview</button>
        </div>}
        {plan && <p className="text-xs">Execution uses the reviewed amount, pool and recipient with a fresh simulation. Market state can change after preview.</p>}
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
      </section>
      <div className="space-y-4">
        {executor ? <OnchainEvidence executor={executor} /> : <section className="rounded-xl border border-[var(--color-border)] p-5 text-sm text-[var(--color-text-muted)]">
          {pending || executorId ? "Waiting for the executor’s simulation and result…" : "Preview first. Review the wallet, asset movements, approval and estimated gas here before confirming."}
        </section>}
        {detail.isError && <p role="alert" className="text-sm">Could not refresh this executor. <Link to="/bots?population=terminated&group=type" className="underline">Check Executors</Link> before submitting again.</p>}
        {executorId && <p className="text-xs break-all">Executor: {executorId}</p>}
        <p className="text-xs text-[var(--color-text-muted)]">Lending rates vary. Withdrawals depend on available liquidity and your collateral obligations. Keep reserves for trading and fees; a completed supply is an open lending position, not realized profit.</p>
      </div>
    </div>
    <LendingPositions server={server} />
  </div>;
}
