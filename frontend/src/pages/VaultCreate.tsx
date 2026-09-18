/**
 * Create a vault: one signature.
 *
 * What is created here is a **private** vault — a wallet owned by the program,
 * running one strategy, with one person's money in it. There is no token, no
 * symbol and no price, because none of those is a decision anyone has to make
 * yet; tokenizing is a separate, later, one-way step on the vault's own page.
 *
 * The Swig, the delegate that trades it and the strategy it runs go in one
 * transaction, because there is no useful moment between them: a vault with no
 * delegate cannot trade and a vault with no strategy has nothing to trade. The
 * three-prompt version of this page needed a resumable draft to survive a
 * closed tab — machinery for a problem that only existed because there were
 * three.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { WalletGate } from "@/components/vaults/shared";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

/** Wrapped SOL: the quote asset every Condor vault starts with. */
const LAMPORTS_PER_SOL = 1_000_000_000;

export function VaultCreate() {
  const { server } = useServer();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { signAndSubmit } = useWallet();

  const [label, setLabel] = useState("");
  const [fundSol, setFundSol] = useState("0");
  const [agentSlug, setAgentSlug] = useState("");
  const [strategySlug, setStrategySlug] = useState("");
  const [configText, setConfigText] = useState('{\n  "pair": "SOL-USDC"\n}');
  const [error, setError] = useState<string | null>(null);

  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: () => api.getAgents(),
    retry: false,
  });

  const run = useMutation({
    mutationFn: async () => {
      if (!server) throw new Error("no server selected");
      setError(null);
      const fund = Math.round(Number(fundSol) * LAMPORTS_PER_SOL);
      if (!Number.isFinite(fund) || fund < 0) throw new Error("funding must be a number");
      let config: Record<string, unknown>;
      try {
        config = JSON.parse(configText);
      } catch (e) {
        throw new Error(`the config is not valid JSON: ${(e as Error).message}`);
      }
      const created = await api.createVault(server, {
        label: label.trim() || "Untitled vault",
        fund_lamports: fund,
        agent_slug: agentSlug,
        strategy_slug: strategySlug,
        config,
      });
      const signature = await signAndSubmit(server, created.build);
      // The confirm is what promotes the config from staged to stored, and it
      // only does so if the chain carries its hash.
      await api.confirmVaultCreate(created.account, signature);
      return created.account;
    },
    onSuccess: (vaultAccount) => {
      queryClient.invalidateQueries({ queryKey: ["vaults", server] });
      navigate(`/vaults/${vaultAccount}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!server) return <NoServerCard message="Pick a server to create a vault on." />;

  return (
    <div className="mx-auto max-w-2xl">
      <Link
        to="/vaults"
        className="mb-4 inline-flex items-center gap-1 text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-3 w-3" />
        Vaults
      </Link>

      <h1 className="mb-1 text-lg font-semibold">New vault</h1>
      <p className="mb-5 text-[12px] text-[var(--color-text-muted)]">
        It starts private: your money, your strategy, and you can withdraw whenever you like.
        Launching a token is a separate decision later — and it is one way.
      </p>

      <WalletGate>
        <div className="space-y-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <Field label="Name" hint="Only you see this; it is not on chain.">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Momentum LP"
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[13px]"
            />
          </Field>

          <Field
            label="Fund with"
            hint="Moved into the vault's wallet in the same transaction. You can add more, or take it back, at any time while the vault is private."
          >
            <div className="flex items-center gap-2">
              <input
                value={fundSol}
                onChange={(e) => setFundSol(e.target.value)}
                inputMode="decimal"
                className="w-32 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-right font-mono text-[13px]"
              />
              <span className="text-[12px] text-[var(--color-text-muted)]">SOL</span>
            </div>
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent" hint="The public folder the strategy comes from.">
              <select
                value={agentSlug}
                onChange={(e) => setAgentSlug(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-[13px]"
              >
                <option value="">Select…</option>
                {agents.data?.map((agent) => (
                  <option key={agent.slug} value={agent.slug}>
                    {agent.name || agent.slug}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Strategy" hint="Its slug inside that agent.">
              <input
                value={strategySlug}
                onChange={(e) => setStrategySlug(e.target.value)}
                placeholder="wide-band"
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-[13px]"
              />
            </Field>
          </div>

          <Field
            label="Config"
            hint="Private. Only its sha256 goes on chain, and a run checks what it is handed against that hash."
          >
            <textarea
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              rows={7}
              spellCheck={false}
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-[12px]"
            />
          </Field>
        </div>

        {error && (
          <p className="mb-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            {error}
          </p>
        )}

        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={run.isPending || !agentSlug || !strategySlug}
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-accent)] px-4 py-2 text-[13px] font-medium text-white disabled:opacity-50"
        >
          {run.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Create vault
        </button>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          One wallet prompt: the wallet, its delegate and the strategy are one transaction.
        </p>
      </WalletGate>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-[12px] font-medium">{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}
