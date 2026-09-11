import type { ExecutorInfo } from "@/lib/api";

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> =>
    row !== null && typeof row === "object" && !Array.isArray(row)) : [];
}

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "Unknown";
}

function assetAmount(row: Record<string, unknown>): string {
  const amount = text(row.amount);
  const decimals = row.decimals;
  if (!/^-?\d+$/.test(amount) || typeof decimals !== "number" ||
      !Number.isInteger(decimals) || decimals < 0 || decimals > 255) {
    return `${amount} raw units`;
  }
  const negative = amount.startsWith("-");
  const digits = amount.replace(/^-/, "").padStart(decimals + 1, "0");
  const fraction = decimals ? digits.slice(-decimals).replace(/0+$/, "") : "";
  return `${negative ? "-" : ""}${decimals ? digits.slice(0, -decimals) : digits}${fraction ? `.${fraction}` : ""}`;
}

export function OnchainEvidence({ executor }: { executor: ExecutorInfo }) {
  const info = executor.custom_info || {};
  const changes = records(info.balance_changes);
  const approvals = records(info.approvals);
  const hashes = Array.isArray(info.tx_hashes) ? info.tx_hashes.filter((hash): hash is string => typeof hash === "string") : [];
  const warnings = Array.isArray(info.simulation_warnings) ? info.simulation_warnings.filter((warning): warning is string => typeof warning === "string") : [];
  const error = records([info.error])[0];
  const dryRun = executor.config?.commit === false;
  const result = info.committed === true ? "Transaction confirmed" : error ? "Action stopped" : dryRun && info.simulation_passed === true
    ? "Simulation only — no funds moved" : "Awaiting execution";

  return <section aria-label="On-chain execution evidence" className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-4 text-sm">
    <div>
      <h3 className="font-semibold">{result}</h3>
      <p className="text-xs text-[var(--color-text-muted)] mt-1">{info.committed === true
        ? "Execution confirmed. Deposits and withdrawals are position changes, not trading profit."
        : "Execution is not confirmed. A simulation alone does not move funds."}</p>
    </div>
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-xs">
      <dt>Network</dt><dd>{text(info.chain)} · {text(info.chain_id)}</dd>
      <dt>Wallet</dt><dd className="break-all font-mono">{text(info.wallet_address)}</dd>
      <dt>Simulation</dt><dd>{info.simulation_passed === true ? "Passed" : info.simulation_passed === false ? "Failed" : "Not available"}</dd>
      <dt>Estimated gas</dt><dd>{info.estimated_gas_quote != null ? `${info.estimated_gas_quote} USDT` : "Not priced"}</dd>
      <dt>Plan digest</dt><dd className="break-all font-mono">{text(info.digest)}</dd>
    </dl>
    {changes.length > 0 && <div><h4 className="font-medium mb-2">Simulated asset movements</h4><ul className="space-y-2">{changes.map((row, index) => <li key={index} className="text-xs">
      <span>{row.direction === "out" ? "Out" : row.direction === "in" ? "In" : "Unknown direction"}: {assetAmount(row)} {text(row.symbol ?? row.asset)}</span>
      <div className="break-all text-[var(--color-text-muted)]">Asset: {text(row.asset)} · Chain: {text(row.chain_id)}</div>
    </li>)}</ul></div>}
    {approvals.length > 0 && <div><h4 className="font-medium mb-2">Approval authority</h4><ul className="space-y-2">{approvals.map((row, index) => <li key={index} className="text-xs break-all">
      <strong>{row.unlimited === true ? "Unlimited allowance" : row.kind === "operator" ? row.approved === false ? "Operator revoked" : "Operator authority" : assetAmount(row)}</strong>
      <div>Asset: {text(row.asset)}</div><div>Spender: {text(row.spender)}</div>
    </li>)}</ul></div>}
    {hashes.length > 0 && <div><h4 className="font-medium mb-2">Transaction receipts</h4>{hashes.map((hash) => <p key={hash} className="break-all font-mono text-xs">{hash}</p>)}</div>}
    {warnings.length > 0 && <ul aria-label="Simulation warnings" className="text-xs space-y-1">{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    {error && <p role="alert" className="text-xs">{text(error.message ?? error.reason)}</p>}
  </section>;
}
