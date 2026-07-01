import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  ExternalLink,
  Key,
  Loader2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useServer } from "@/hooks/useServer";
import { type ConnectorInfo, type CredentialInfo, api } from "@/lib/api";
import { ConnectHyperliquid } from "./ConnectHyperliquid";

type Step = "list" | "select-type" | "select-exchange" | "fill-fields" | "connect-hyperliquid";

const isHyperliquid = (name: string) => name.startsWith("hyperliquid");

interface AddFlowState {
  step: Step;
  connectorType: string;
  connectorName: string;
  fields: Record<string, unknown>;
  values: Record<string, string>;
}

const INITIAL_FLOW: AddFlowState = {
  step: "list",
  connectorType: "",
  connectorName: "",
  fields: {},
  values: {},
};

// Substrings that mark a connector config field as a sensitive credential.
// Connector keys vary (api_key, secret_key, passphrase, private_key, api_token,
// mnemonic, seed, ...), so we match by substring on both the field name and type
// rather than the few exact names ("secret"/"password") covered before.
const CREDENTIAL_FIELD_PATTERNS = [
  "secret",
  "password",
  "passphrase",
  "key",
  "token",
  "private",
  "mnemonic",
  "seed",
];

function isCredentialField(key: string, type?: string): boolean {
  const haystack = `${key} ${type ?? ""}`.toLowerCase();
  return CREDENTIAL_FIELD_PATTERNS.some((p) => haystack.includes(p));
}

export function ApiKeysSettings() {
  const { server } = useServer();
  const qc = useQueryClient();
  const [flow, setFlow] = useState<AddFlowState>(INITIAL_FLOW);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const { data: credsData, isLoading: loadingCreds } = useQuery({
    queryKey: ["settings-credentials", server],
    queryFn: () => api.getCredentials(server!),
    enabled: !!server,
  });

  const { data: connectorsData, isLoading: loadingConnectors } = useQuery({
    queryKey: ["settings-connectors", server, flow.connectorType],
    queryFn: () => api.getAvailableConnectors(server!, flow.connectorType || undefined),
    enabled: !!server && !!flow.connectorType && flow.step === "select-exchange",
    staleTime: 5 * 60 * 1000,
  });

  const { data: configMapData, isLoading: loadingConfigMap } = useQuery({
    queryKey: ["settings-config-map", server, flow.connectorName],
    queryFn: () => api.getConnectorConfigMap(server!, flow.connectorName),
    enabled: !!server && !!flow.connectorName && flow.step === "fill-fields",
    staleTime: 30 * 60 * 1000,
  });

  // Prefetch config-maps for all connectors when the exchange list loads
  useEffect(() => {
    const connectors: ConnectorInfo[] = connectorsData?.connectors ?? [];
    if (!server || connectors.length === 0) return;
    for (const c of connectors) {
      qc.prefetchQuery({
        queryKey: ["settings-config-map", server, c.name],
        queryFn: () => api.getConnectorConfigMap(server, c.name),
        staleTime: 30 * 60 * 1000,
      });
    }
  }, [connectorsData, server, qc]);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["settings-credentials", server] });

  const addMut = useMutation({
    mutationFn: () =>
      api.addCredential(server!, {
        connector_name: flow.connectorName,
        credentials: flow.values,
      }),
    onSuccess: () => { invalidate(); setFlow(INITIAL_FLOW); },
  });

  const deleteMut = useMutation({
    mutationFn: (connector: string) => api.deleteCredential(server!, connector),
    onSuccess: () => { invalidate(); setConfirmDelete(null); },
  });

  // Normalize credentials — API may return strings or objects
  const credentials: CredentialInfo[] = useMemo(() => {
    const raw = credsData?.credentials ?? [];
    return raw.map((item: unknown) => {
      if (typeof item === "string") {
        return { connector_name: item, connector_type: "" };
      }
      const obj = item as CredentialInfo;
      return { connector_name: obj.connector_name || "", connector_type: obj.connector_type || "" };
    });
  }, [credsData]);

  const grouped = useMemo(() => {
    const map: Record<string, CredentialInfo[]> = {};
    for (const c of credentials) {
      const type = c.connector_type || "other";
      if (!map[type]) map[type] = [];
      map[type].push(c);
    }
    // Show connectors alphabetically within each group.
    for (const list of Object.values(map)) {
      list.sort((a, b) => a.connector_name.localeCompare(b.connector_name));
    }
    return map;
  }, [credentials]);

  // Only treat Hyperliquid as connected once BOTH the spot and perpetual credentials exist. If only
  // one is present (e.g. a partial-save failure), keep the connect flow available to add the other.
  const hyperliquidConnected = useMemo(() => {
    const names = new Set(credentials.map((c) => c.connector_name));
    return names.has("hyperliquid") && names.has("hyperliquid_perpetual");
  }, [credentials]);

  // Parse config map fields
  const configFields = useMemo(() => {
    if (!configMapData?.config_map) return [];
    const cm = configMapData.config_map;
    return Object.entries(cm).map(([key, val]) => {
      const v = val as Record<string, unknown>;
      return {
        key,
        type: (v.type as string) || "string",
        required: v.required !== false,
        description: (v.description as string) || "",
        isSecret: isCredentialField(key, v.type as string | undefined),
      };
    });
  }, [configMapData]);

  if (!server) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Select a server first.
      </p>
    );
  }

  // ── Add credential flow ──

  if (flow.step === "select-type") {
    return (
      <div className="space-y-4">
        <button
          onClick={() => setFlow(INITIAL_FLOW)}
          className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back
        </button>
        <h3 className="text-sm font-semibold text-[var(--color-text)]">Select Connector Type</h3>
        <div className="grid grid-cols-2 gap-3">
          {["spot", "perpetual"].map((type) => (
            <button
              key={type}
              onClick={() => setFlow({ ...flow, step: "select-exchange", connectorType: type })}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-left transition-colors hover:border-[var(--color-border-hover)]"
            >
              <span className="text-sm font-medium capitalize text-[var(--color-text)]">{type}</span>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                {type === "spot" ? "Spot exchange connectors" : "Perpetual/futures connectors"}
              </p>
            </button>
          ))}
        </div>

        <button
          disabled={hyperliquidConnected}
          onClick={() =>
            setFlow({ ...INITIAL_FLOW, step: "connect-hyperliquid", connectorName: "hyperliquid_perpetual" })
          }
          className="flex w-full items-center justify-between rounded-lg border border-[#5ce0c6]/40 bg-[#5ce0c6]/5 p-4 text-left transition-colors hover:border-[var(--color-border-hover)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-[#5ce0c6]/40"
        >
          <span>
            <span className="text-sm font-medium text-[var(--color-text)]">Connect Hyperliquid</span>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              {hyperliquidConnected
                ? "Already connected — remove the existing Hyperliquid keys to reconnect."
                : "Connect wallet to Hyperliquid (spot + perpetual)"}
            </p>
          </span>
          {hyperliquidConnected ? (
            <Check className="h-7 w-7 shrink-0 text-[var(--color-primary)]" />
          ) : (
            <img src="/hyperliquid.png" alt="Hyperliquid" className="h-7 w-7 shrink-0 rounded-full" />
          )}
        </button>
      </div>
    );
  }

  if (flow.step === "connect-hyperliquid") {
    return (
      <ConnectHyperliquid
        server={server}
        onBack={() => setFlow({ ...INITIAL_FLOW, step: "select-type" })}
        onDone={() => {
          invalidate();
          setFlow(INITIAL_FLOW);
        }}
      />
    );
  }

  if (flow.step === "select-exchange") {
    const connectors: ConnectorInfo[] = [...(connectorsData?.connectors ?? [])].sort((a, b) =>
      a.name.localeCompare(b.name),
    );
    const configuredNames = new Set(credentials.map((c) => c.connector_name));
    return (
      <div className="space-y-4">
        <button
          onClick={() => setFlow({ ...flow, step: "select-type", connectorType: "" })}
          className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back
        </button>
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Select {flow.connectorType} Exchange
        </h3>
        {loadingConnectors ? (
          <div className="flex items-center gap-2 py-4 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading connectors...
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {connectors.map((c) => {
              const alreadyConnected = configuredNames.has(c.name);
              return (
                <button
                  key={c.name}
                  disabled={alreadyConnected}
                  onClick={() =>
                    setFlow({
                      ...flow,
                      step: isHyperliquid(c.name) ? "connect-hyperliquid" : "fill-fields",
                      connectorName: c.name,
                      values: {},
                    })
                  }
                  className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                    alreadyConnected
                      ? "border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 text-[var(--color-text-muted)] cursor-default"
                      : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:border-[var(--color-border-hover)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    {c.name}
                    {alreadyConnected && <Check className="h-3 w-3 text-[var(--color-primary)]" />}
                  </span>
                </button>
              );
            })}
            {connectors.length === 0 && (
              <p className="col-span-full py-4 text-center text-xs text-[var(--color-text-muted)]">
                No {flow.connectorType} connectors available.
              </p>
            )}
          </div>
        )}
      </div>
    );
  }

  if (flow.step === "fill-fields") {
    return (
      <div className="space-y-4">
        <button
          onClick={() => setFlow({ ...flow, step: "select-exchange", connectorName: "", values: {} })}
          className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back
        </button>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
          Configure {flow.connectorName}
          <a
            href={`https://hummingbot.org/exchanges/${flow.connectorName.replace(/_(perpetual|spot)$/, "")}/#how-to-connect`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs font-normal text-[var(--color-primary)] hover:underline"
          >
            How to connect <ExternalLink className="h-3 w-3" />
          </a>
        </h3>
        {loadingConfigMap ? (
          <div className="flex items-center gap-2 py-4 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading fields...
          </div>
        ) : (
          <div className="space-y-3">
            {configFields.map((f) => (
              <div key={f.key}>
                <label className="mb-1 flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
                  {f.key}
                  {f.required && <span className="text-[var(--color-red)]">*</span>}
                </label>
                {f.description && (
                  <p className="mb-1 text-[10px] text-[var(--color-text-muted)]/60">{f.description}</p>
                )}
                <input
                  type={f.isSecret ? "password" : "text"}
                  autoComplete={f.isSecret ? "new-password" : "off"}
                  value={flow.values[f.key] || ""}
                  onChange={(e) =>
                    setFlow({ ...flow, values: { ...flow.values, [f.key]: e.target.value } })
                  }
                  className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                  placeholder={f.isSecret ? "********" : f.key}
                />
              </div>
            ))}

            {configFields.length === 0 && (
              <p className="text-xs text-[var(--color-text-muted)]">
                No configuration fields found for this connector.
              </p>
            )}

            <div className="flex items-center gap-2 pt-2">
              <button
                onClick={() => addMut.mutate()}
                disabled={addMut.isPending}
                className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {addMut.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                Add Credential
              </button>
              <button
                onClick={() => setFlow(INITIAL_FLOW)}
                className="rounded-md px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                Cancel
              </button>
            </div>

            {addMut.error && (
              <p className="text-xs text-[var(--color-red)]">{addMut.error.message}</p>
            )}
          </div>
        )}
      </div>
    );
  }

  // ── Main list ──

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-text-muted)]">
          {credentials.length} credential{credentials.length !== 1 ? "s" : ""} configured
        </p>
        <button
          onClick={() => setFlow({ ...INITIAL_FLOW, step: "select-type" })}
          className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80"
        >
          <Plus className="h-3.5 w-3.5" /> Add API Key
        </button>
      </div>

      {loadingCreds ? (
        <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : credentials.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
          No API keys configured. Add one to start trading.
        </p>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([type, creds]) => (
            <div key={type}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                {type}
              </h3>
              <div className="space-y-2">
                {creds.map((c) => (
                  <div
                    key={c.connector_name}
                    className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 transition-colors hover:border-[var(--color-border-hover)]"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]">
                        <Key className="h-4 w-4" />
                      </div>
                      <span className="text-sm font-medium text-[var(--color-text)]">
                        {c.connector_name}
                      </span>
                    </div>

                    {confirmDelete === c.connector_name ? (
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => deleteMut.mutate(c.connector_name)}
                          disabled={deleteMut.isPending}
                          className="rounded p-1.5 text-[var(--color-red)] hover:bg-red-500/10"
                          title="Confirm delete"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setConfirmDelete(null)}
                          className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmDelete(c.connector_name)}
                        className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
                        title="Delete credential"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
