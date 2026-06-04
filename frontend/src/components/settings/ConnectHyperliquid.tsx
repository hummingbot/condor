import { AlertCircle, ArrowLeft, Check, Loader2, Wallet } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import {
  BUILDER_FEE_BPS,
  type ConnectStep,
  type DiscoveredWallet,
  buildHyperliquidCredentials,
  connectHyperliquid,
  connectWallet,
  discoverWallets,
} from "@/lib/hyperliquid";

type Phase =
  | "select-wallet"
  | "connecting"
  | "switch-chain"
  | "approve-agent"
  | "approve-builder"
  | "saving"
  | "done";

const STEP_LABEL: Record<ConnectStep, string> = {
  "switch-chain": "Approve the switch to Arbitrum One in your wallet…",
  "approve-agent": "Sign in your wallet to authorize the Condor agent wallet…",
  "approve-builder": `Sign in your wallet to approve the Condor builder code (${BUILDER_FEE_BPS} bps)…`,
};

export function ConnectHyperliquid({
  server,
  onBack,
  onDone,
}: {
  server: string;
  onBack: () => void;
  onDone: () => void;
}) {
  const [wallets, setWallets] = useState<DiscoveredWallet[]>([]);
  const [scanning, setScanning] = useState(true);
  const [accountName, setAccountName] = useState("");
  const [phase, setPhase] = useState<Phase>("select-wallet");
  const [error, setError] = useState<string | null>(null);
  const [partial, setPartial] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    discoverWallets().then((found) => {
      if (!active) return;
      setWallets(found);
      setScanning(false);
    });
    return () => {
      active = false;
    };
  }, []);

  const busy =
    phase === "connecting" ||
    phase === "switch-chain" ||
    phase === "approve-agent" ||
    phase === "approve-builder" ||
    phase === "saving";

  async function handleConnect(wallet: DiscoveredWallet) {
    setError(null);
    setPartial(null);
    setPhase("connecting");
    try {
      const mainAddress = await connectWallet(wallet.provider);

      const conn = await connectHyperliquid({
        provider: wallet.provider,
        mainAddress,
        agentName: accountName,
        onStep: (step: ConnectStep) => setPhase(step),
      });

      // One agent + one set of approvals authorizes both connectors. Register them in parallel
      // and tolerate a partial failure — hummingbot-api validates each connector by spinning up a
      // full trading connector, which can take up to a minute and occasionally fails for one side.
      setPhase("saving");
      const entries = Object.entries(buildHyperliquidCredentials(conn));
      const results = await Promise.allSettled(
        entries.map(([connectorName, credentials]) =>
          api.addCredential(server, { connector_name: connectorName, credentials }),
        ),
      );
      const failed = entries
        .map(([name], i) => ({ name, result: results[i] }))
        .filter((x) => x.result.status === "rejected");

      if (failed.length === entries.length) {
        const reason = (failed[0].result as PromiseRejectedResult).reason as { message?: string };
        throw new Error(reason?.message || "Failed to save Hyperliquid credentials.");
      }
      if (failed.length > 0) {
        const reason = (failed[0].result as PromiseRejectedResult).reason as { message?: string };
        setPartial(
          `Connected, but ${failed.map((f) => f.name).join(", ")} could not be saved ` +
            `(${reason?.message || "validation failed"}). Retry it from the API Keys list — no re-signing needed.`,
        );
      }

      setPhase("done");
      window.setTimeout(onDone, failed.length > 0 ? 3500 : 900);
    } catch (e) {
      const err = e as { code?: number; message?: string };
      setError(
        err.code === 4001
          ? "Signature request was rejected in your wallet."
          : err.message || "Failed to connect Hyperliquid.",
      );
      setPhase("select-wallet");
    }
  }

  return (
    <div className="space-y-4">
      <button
        onClick={onBack}
        disabled={busy}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back
      </button>

      <div>
        <h3 className="text-sm font-semibold text-[var(--color-text)]">Connect Hyperliquid</h3>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          Authorize a trade-only agent wallet — your private key never leaves your wallet.
          Registers both <span className="text-[var(--color-text)]">hyperliquid_perpetual</span> and{" "}
          <span className="text-[var(--color-text)]">hyperliquid</span>.
        </p>
      </div>

      {/* Account name (used as the on-chain agent wallet name) */}
      <div>
        <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
          Agent name <span className="text-[var(--color-text-muted)]/60">(optional — the date is appended, e.g. condor-20260604)</span>
        </label>
        <input
          value={accountName}
          onChange={(e) => setAccountName(e.target.value)}
          disabled={busy}
          placeholder="condor"
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-50"
        />
      </div>

      {/* Done state */}
      {phase === "done" ? (
        <div
          className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
            partial
              ? "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)]"
              : "border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 text-[var(--color-text)]"
          }`}
        >
          <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          <span>{partial ?? "Hyperliquid connected."}</span>
        </div>
      ) : busy ? (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm text-[var(--color-text)]">
          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-primary)]" />
          {phase === "connecting" && "Connecting to your wallet…"}
          {phase === "switch-chain" && STEP_LABEL["switch-chain"]}
          {phase === "approve-agent" && STEP_LABEL["approve-agent"]}
          {phase === "approve-builder" && STEP_LABEL["approve-builder"]}
          {phase === "saving" && "Saving credentials…"}
        </div>
      ) : (
        <div className="space-y-2">
          <label className="block text-xs text-[var(--color-text-muted)]">Connect wallet</label>
          {scanning ? (
            <div className="flex items-center gap-2 py-3 text-xs text-[var(--color-text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" /> Detecting wallets…
            </div>
          ) : wallets.length === 0 ? (
            <p className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text-muted)]">
              No browser wallet detected. Install Rabby or MetaMask, then reload.
            </p>
          ) : (
            <div className="space-y-2">
              {wallets.map((w) => (
                <button
                  key={w.uuid}
                  onClick={() => handleConnect(w)}
                  className="flex w-full items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left transition-colors hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)]"
                >
                  {w.icon ? (
                    <img src={w.icon} alt="" className="h-7 w-7 rounded-md" />
                  ) : (
                    <span className="flex h-7 w-7 items-center justify-center rounded-md bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]">
                      <Wallet className="h-4 w-4" />
                    </span>
                  )}
                  <span className="text-sm font-medium text-[var(--color-text)]">{w.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-[var(--color-red)]/30 bg-red-500/5 p-2.5 text-xs text-[var(--color-red)]">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {error}
            {/funded on Hyperliquid/i.test(error) && (
              <>
                {" "}
                <a
                  href="https://app.hyperliquid.xyz/trade"
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-[var(--color-primary)] underline"
                >
                  Deposit on Hyperliquid →
                </a>
              </>
            )}
          </span>
        </div>
      )}

      <p className="text-[10px] text-[var(--color-text-muted)]/60">
        Don't have a Hyperliquid account?{" "}
        <a
          href="https://app.hyperliquid.xyz"
          target="_blank"
          rel="noreferrer"
          className="text-[var(--color-primary)] hover:underline"
        >
          Sign up here
        </a>
        .
      </p>
    </div>
  );
}
