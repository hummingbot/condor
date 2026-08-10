import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { privateKeyToAccount } from "viem/accounts";

import { api } from "@/lib/api";

export type WalletChain = "solana" | "ethereum";

// Browser wallets (Phantom, MetaMask, Rabby) never expose private keys to websites — that's by
// design, and why this flow is a manual export + paste instead of a one-click "Connect".
const EXPORT_INSTRUCTIONS: Record<WalletChain, { wallet: string; steps: string }[]> = {
  solana: [
    {
      wallet: "Phantom",
      steps: "Settings → Manage Accounts → select account → Show Private Key",
    },
  ],
  ethereum: [
    { wallet: "MetaMask", steps: "⋮ menu → Account details → Show private key" },
    { wallet: "Rabby", steps: "Click address → Address Detail → Backup Private Key" },
  ],
};

const EVM_KEY_RE = /^(0x)?[0-9a-fA-F]{64}$/;
// Phantom exports the keypair as base58, typically 87–88 chars.
const SOLANA_KEY_RE = /^[1-9A-HJ-NP-Za-km-z]{64,120}$/;

export function ImportGatewayWallet({
  server,
  chain,
  onBack,
  onDone,
}: {
  server: string;
  chain: WalletChain;
  onBack: () => void;
  onDone: () => void;
}) {
  const [key, setKey] = useState("");
  const [setDefault, setSetDefault] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importedAddress, setImportedAddress] = useState<string | null>(null);

  const trimmed = key.trim();

  // For Ethereum we can derive the address locally before importing, so the user sees exactly
  // which account the pasted key controls. Solana derivation would need an ed25519 lib — the
  // imported address is shown from Gateway's response instead.
  const derived = useMemo(() => {
    if (chain !== "ethereum" || !EVM_KEY_RE.test(trimmed)) return null;
    try {
      const hex = (trimmed.startsWith("0x") ? trimmed : `0x${trimmed}`) as `0x${string}`;
      return privateKeyToAccount(hex).address;
    } catch {
      return null;
    }
  }, [chain, trimmed]);

  const keyLooksValid =
    chain === "ethereum" ? derived !== null : SOLANA_KEY_RE.test(trimmed);

  async function handleImport() {
    setError(null);
    setSaving(true);
    try {
      const privateKey =
        chain === "ethereum" && !trimmed.startsWith("0x") ? `0x${trimmed}` : trimmed;
      const res = await api.addGatewayWallet(server, {
        chain,
        private_key: privateKey,
        set_default: setDefault,
      });
      setImportedAddress(res.wallet?.address ?? derived ?? "");
      setKey("");
      window.setTimeout(onDone, 1500);
    } catch (e) {
      const err = e as { message?: string };
      setError(err.message || "Failed to import wallet into Gateway.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
        <p className="mb-1.5 text-xs font-medium text-[var(--color-text)]">
          Where to find your private key
        </p>
        <ul className="space-y-1 text-xs text-[var(--color-text-muted)]">
          {EXPORT_INSTRUCTIONS[chain].map(({ wallet, steps }) => (
            <li key={wallet}>
              <span className="text-[var(--color-text)]">{wallet}:</span> {steps}
            </li>
          ))}
        </ul>
      </div>

      {importedAddress !== null ? (
        <div className="flex items-start gap-2 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 p-3 text-sm text-[var(--color-text)]">
          <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          <span>
            Wallet imported into Gateway.
            {importedAddress && (
              <span className="ml-1 break-all font-mono text-xs text-[var(--color-text-muted)]">
                {importedAddress}
              </span>
            )}
          </span>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
              Private key
            </label>
            <input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              disabled={saving}
              placeholder={
                chain === "ethereum" ? "0x… (64 hex characters)" : "Base58 private key from Phantom"
              }
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 font-mono text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-50"
            />
            {derived && (
              <p className="mt-1 break-all font-mono text-xs text-[var(--color-text-muted)]">
                Will import: {derived}
              </p>
            )}
            {trimmed.length > 0 && !keyLooksValid && (
              <p className="mt-1 text-xs text-[var(--color-red)]">
                {chain === "ethereum"
                  ? "Expected a 64-character hex private key (with or without 0x prefix)."
                  : "Expected a base58 private key (no 0x, no brackets)."}
              </p>
            )}
          </div>

          <label className="flex cursor-pointer items-center gap-2 text-xs text-[var(--color-text)]">
            <input
              type="checkbox"
              checked={setDefault}
              onChange={(e) => setSetDefault(e.target.checked)}
              disabled={saving}
              className="h-3.5 w-3.5 accent-[var(--color-primary)]"
            />
            Set as default wallet for this chain
          </label>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-[var(--color-red)]/30 bg-red-500/5 p-2.5 text-xs text-[var(--color-red)]">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={handleImport}
              disabled={saving || !keyLooksValid}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              {saving && <Loader2 className="h-3 w-3 animate-spin" />}
              Import into Gateway
            </button>
            <button
              onClick={onBack}
              disabled={saving}
              className="rounded-md px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
