import { AlertCircle, ArrowLeft, Check, Loader2, Sparkles, Wallet } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { type DiscoveredWallet, connectWallet, discoverWallets } from "@/lib/wallet/evm";
import {
  BUILDER_FEE_BPS,
  type ConnectStep,
  type HyperliquidConnection,
  REFERRAL_CODE,
  REFERRAL_FEE_DISCOUNT,
  buildHyperliquidCredentials,
  connectHyperliquid,
  hasHyperliquidReferrer,
  resolveAgentName,
  setHyperliquidReferrer,
} from "@/lib/wallet/hyperliquid";

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
  "approve-builder": `Sign in your wallet to approve the ${BUILDER_FEE_BPS} bps builder fee — it goes to the not-for-profit Hummingbot Foundation to support Condor's maintenance…`,
};

// Both connectors are saved (in parallel) from the one agent approval. They're shown in this order,
// each flipping to a check as its own save finishes — the spot connector is quicker, the perpetual's
// full bring-up (HIP-3 markets) takes longer, so the user sees real progress instead of one long wait.
type SaveState = "saving" | "done" | "error";
const SAVE_CONNECTORS: { name: string; label: string }[] = [
  { name: "hyperliquid", label: "Hyperliquid (spot)" },
  { name: "hyperliquid_perpetual", label: "Hyperliquid Perpetual" },
];

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
  // Per-connector save status, updated as each addCredential resolves (keyed by connector name).
  const [saveStatus, setSaveStatus] = useState<Record<string, SaveState>>({});

  // Referral linking (offered on the done screen). `conn` retains the agent key needed to sign the
  // setReferrer L1 action after credentials are saved.
  const [conn, setConn] = useState<HyperliquidConnection | null>(null);
  const [referral, setReferral] = useState<"hidden" | "available" | "linking" | "linked">("hidden");
  const [referralError, setReferralError] = useState<string | null>(null);

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
    phase === "saving" ||
    referral === "linking";

  async function handleConnect(wallet: DiscoveredWallet) {
    setError(null);
    setPartial(null);
    setReferralError(null);
    setReferral("hidden");
    setSaveStatus({});
    setPhase("connecting");
    try {
      const mainAddress = await connectWallet(wallet.provider);

      const connection = await connectHyperliquid({
        provider: wallet.provider,
        mainAddress,
        agentName: accountName,
        onStep: (step: ConnectStep) => setPhase(step),
      });
      setConn(connection);

      // One agent + one set of approvals authorizes both connectors. Register them in parallel
      // and tolerate a partial failure — hummingbot-api validates each connector by spinning up a
      // full trading connector, which can take up to a minute and occasionally fails for one side.
      // Each connector's row flips to a check (or error) the moment its own save resolves.
      setPhase("saving");
      const creds = buildHyperliquidCredentials(connection);
      const entries = Object.entries(creds);
      setSaveStatus(Object.fromEntries(entries.map(([name]) => [name, "saving" as SaveState])));
      const results = await Promise.allSettled(
        entries.map(([connectorName, credentials]) =>
          api
            .addCredential(server, { connector_name: connectorName, credentials })
            .then((r) => {
              setSaveStatus((s) => ({ ...s, [connectorName]: "done" }));
              return r;
            })
            .catch((e) => {
              setSaveStatus((s) => ({ ...s, [connectorName]: "error" }));
              throw e;
            }),
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

      // Offer to link the Hummingbot referral code unless the account already has one. If the
      // lookup itself fails we still offer it — setReferrer surfaces a clear error if it's already set.
      let alreadyReferred = false;
      try {
        alreadyReferred = await hasHyperliquidReferrer(connection.mainAddress);
      } catch {
        alreadyReferred = false;
      }
      if (alreadyReferred) {
        window.setTimeout(onDone, failed.length > 0 ? 3500 : 900);
      } else {
        setReferral("available");
      }
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

  async function handleLinkReferral() {
    if (!conn) return;
    setReferralError(null);
    setReferral("linking");
    try {
      await setHyperliquidReferrer(conn);
      setReferral("linked");
      window.setTimeout(onDone, 1500);
    } catch (e) {
      const err = e as { message?: string };
      setReferralError(err.message || "Failed to link the referral code.");
      setReferral("available");
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
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Connect Hyperliquid</h2>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          Authorize a trade-only agent wallet — your private key never leaves your wallet.
          Registers both <span className="text-[var(--color-text)]">hyperliquid_perpetual</span> and{" "}
          <span className="text-[var(--color-text)]">hyperliquid</span>.
        </p>
      </div>

      {/* Account name (used as the on-chain agent wallet name) */}
      <div>
        <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
          Agent name <span className="text-[var(--color-text-muted)]/60">(optional — today's date is appended)</span>
        </label>
        <input
          value={accountName}
          onChange={(e) => setAccountName(e.target.value)}
          disabled={busy}
          placeholder="condor"
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-50"
        />
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          Agent wallet name:{" "}
          <span className="font-mono text-[var(--color-text)]">{resolveAgentName(accountName)}</span>
        </p>
      </div>

      {/* Done state */}
      {phase === "done" ? (
        <div className="space-y-3">
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

          {/* Referral code — link the Hummingbot code for a fee discount (one-time). */}
          {referral === "linked" ? (
            <div className="flex items-start gap-2 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 p-3 text-sm text-[var(--color-text)]">
              <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
              <span>
                Hummingbot referral linked — {REFERRAL_FEE_DISCOUNT} off fees.
              </span>
            </div>
          ) : referral === "available" || referral === "linking" ? (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
              <div className="flex items-start gap-2">
                <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
                <div className="space-y-0.5">
                  <p className="text-sm font-medium text-[var(--color-text)]">
                    Earn {REFERRAL_FEE_DISCOUNT} off fees
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Link the Hummingbot referral code (
                    <span className="text-[var(--color-text)]">{REFERRAL_CODE}</span>) to your
                    Hyperliquid account. One-time, signed by your agent wallet.
                  </p>
                </div>
              </div>
              {referralError && (
                <div className="mt-2 flex items-start gap-2 rounded-md border border-[var(--color-red)]/30 bg-red-500/5 p-2 text-xs text-[var(--color-red)]">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{referralError}</span>
                </div>
              )}
              <div className="mt-3 flex items-center gap-2">
                <button
                  onClick={handleLinkReferral}
                  disabled={referral === "linking"}
                  className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {referral === "linking" ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Linking…
                    </>
                  ) : (
                    "Link code"
                  )}
                </button>
                <button
                  onClick={onDone}
                  disabled={referral === "linking"}
                  className="rounded-md px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                >
                  Skip
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : phase === "saving" ? (
        <div className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm text-[var(--color-text)]">
          <div className="text-xs text-[var(--color-text-muted)]">
            Saving credentials — registering both connectors…
          </div>
          {SAVE_CONNECTORS.map(({ name, label }) => {
            const st = saveStatus[name];
            return (
              <div key={name} className="flex items-center gap-2">
                {st === "done" ? (
                  <Check className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
                ) : st === "error" ? (
                  <AlertCircle className="h-4 w-4 shrink-0 text-[var(--color-red)]" />
                ) : (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--color-primary)]" />
                )}
                <span className={st === "error" ? "text-[var(--color-red)]" : undefined}>{label}</span>
                {st === "error" && (
                  <span className="text-xs text-[var(--color-text-muted)]">failed</span>
                )}
              </div>
            );
          })}
        </div>
      ) : busy ? (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm text-[var(--color-text)]">
          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-primary)]" />
          {phase === "connecting" && "Connecting to your wallet…"}
          {phase === "switch-chain" && STEP_LABEL["switch-chain"]}
          {phase === "approve-agent" && STEP_LABEL["approve-agent"]}
          {phase === "approve-builder" && STEP_LABEL["approve-builder"]}
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
