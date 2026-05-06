import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  RefreshCw,
  Square,
} from "lucide-react";
import { useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

const IMAGE_OPTIONS = [
  { label: "Latest", value: "hummingbot/gateway:latest" },
  { label: "Development", value: "hummingbot/gateway:development" },
];

export function GatewaySettings() {
  const { server } = useServer();
  const qc = useQueryClient();
  const [showLogs, setShowLogs] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
  const [image, setImage] = useState(IMAGE_OPTIONS[0].value);
  const [customImage, setCustomImage] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [port, setPort] = useState(15888);
  const [confirmStop, setConfirmStop] = useState(false);

  const { data: status, isLoading } = useQuery({
    queryKey: ["gateway-status", server],
    queryFn: () => api.getGatewayStatus(server!),
    enabled: !!server,
    refetchInterval: 10000,
  });

  const { data: logsData, isFetching: fetchingLogs } = useQuery({
    queryKey: ["gateway-logs", server],
    queryFn: () => api.getGatewayLogs(server!),
    enabled: !!server && showLogs,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["gateway-status", server] });

  const startMut = useMutation({
    mutationFn: () =>
      api.startGateway(server!, {
        image: image === "custom" ? customImage : image,
        passphrase,
        port,
        dev_mode: true,
      }),
    onSuccess: () => { invalidate(); setShowDeploy(false); setPassphrase(""); },
  });

  const stopMut = useMutation({
    mutationFn: () => api.stopGateway(server!),
    onSuccess: () => { invalidate(); setConfirmStop(false); },
  });

  const restartMut = useMutation({
    mutationFn: () => api.restartGateway(server!),
    onSuccess: invalidate,
  });

  if (!server) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Select a server first.
      </p>
    );
  }

  const running = status?.running ?? false;

  return (
    <div className="space-y-4">
      {/* Status */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              className={`h-3 w-3 rounded-full ${
                isLoading
                  ? "bg-[var(--color-text-muted)]/30 animate-pulse"
                  : running
                    ? "bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]"
                    : "bg-red-400/60"
              }`}
            />
            <div>
              <span className="text-sm font-medium text-[var(--color-text)]">
                Gateway {isLoading ? "..." : running ? "Running" : "Stopped"}
              </span>
              <p className="text-xs text-[var(--color-text-muted)]">
                Server: {server}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {running ? (
              <>
                <button
                  onClick={() => restartMut.mutate()}
                  disabled={restartMut.isPending}
                  className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
                >
                  {restartMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                  Restart
                </button>
                {confirmStop ? (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => stopMut.mutate()}
                      disabled={stopMut.isPending}
                      className="rounded-md bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-red)]/80"
                    >
                      {stopMut.isPending ? "Stopping..." : "Confirm Stop"}
                    </button>
                    <button
                      onClick={() => setConfirmStop(false)}
                      className="rounded-md px-2 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmStop(true)}
                    className="flex items-center gap-1.5 rounded-md border border-red-500/30 px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-red-500/10"
                  >
                    <Square className="h-3 w-3" /> Stop
                  </button>
                )}
              </>
            ) : (
              <button
                onClick={() => setShowDeploy(!showDeploy)}
                className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80"
              >
                <Play className="h-3 w-3" /> Deploy
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Deploy form */}
      {showDeploy && !running && (
        <div className="rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-sm font-semibold text-[var(--color-text)]">Deploy Gateway</h3>
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Image</label>
              <div className="flex flex-wrap gap-2">
                {IMAGE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setImage(opt.value)}
                    className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                      image === opt.value
                        ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                        : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)]"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
                <button
                  onClick={() => setImage("custom")}
                  className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                    image === "custom"
                      ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)]"
                  }`}
                >
                  Custom
                </button>
              </div>
              {image === "custom" && (
                <input
                  value={customImage}
                  onChange={(e) => setCustomImage(e.target.value)}
                  className="mt-2 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                  placeholder="org/image:tag"
                />
              )}
            </div>

            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Passphrase</label>
              <input
                type="password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                placeholder="Gateway passphrase"
              />
            </div>

            <div className="w-32">
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Port</label>
              <input
                type="number"
                value={port}
                onChange={(e) => setPort(parseInt(e.target.value) || 15888)}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
              />
            </div>

            <button
              onClick={() => startMut.mutate()}
              disabled={startMut.isPending || !passphrase}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              {startMut.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              Start Gateway
            </button>

            {startMut.error && (
              <p className="text-xs text-[var(--color-red)]">{startMut.error.message}</p>
            )}
          </div>
        </div>
      )}

      {/* Logs */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
        <button
          onClick={() => setShowLogs(!showLogs)}
          className="flex w-full items-center justify-between p-3 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
        >
          <span className="font-medium">Logs</span>
          {showLogs ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        {showLogs && (
          <div className="border-t border-[var(--color-border)] p-3">
            {fetchingLogs ? (
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading logs...
              </div>
            ) : (
              <pre className="max-h-64 overflow-auto rounded bg-[var(--color-bg)] p-3 text-xs text-[var(--color-text-muted)] font-mono leading-relaxed">
                {typeof logsData?.logs === "string"
                  ? logsData.logs || "No logs available"
                  : JSON.stringify(logsData?.logs, null, 2) || "No logs available"}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
