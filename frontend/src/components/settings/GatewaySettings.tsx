import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Play,
  RefreshCw,
  Square,
} from "lucide-react";
import { useEffect, useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

const IMAGE_OPTIONS = [
  { label: "Latest", value: "hummingbot/gateway:latest" },
  { label: "Development", value: "hummingbot/gateway:development" },
];

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return dateStr;
  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDays = Math.floor(diffHr / 24);
  if (diffDays === 1) return "1 day ago";
  return `${diffDays} days ago`;
}

function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.floor(sec)}s`;
  return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
}

export function GatewaySettings() {
  const { server } = useServer();
  const qc = useQueryClient();
  const [showLogs, setShowLogs] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
  const [showPull, setShowPull] = useState(false);
  const [image, setImage] = useState(IMAGE_OPTIONS[0].value);
  const [customImage, setCustomImage] = useState("");
  const [pullImage, setPullImage] = useState(IMAGE_OPTIONS[0].value);
  const [pullCustomImage, setPullCustomImage] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [port, setPort] = useState(15888);
  const [confirmStop, setConfirmStop] = useState(false);
  const [isPulling, setIsPulling] = useState(false);
  const [pullDone, setPullDone] = useState(false);

  // Poll pull status while pulling
  const { data: pullStatus } = useQuery({
    queryKey: ["gateway-pull-status", server],
    queryFn: () => api.getGatewayPullStatus(server!),
    enabled: !!server && isPulling,
    refetchInterval: 1500,
  });

  // Derive active pull operation from status
  const resolvedPullImage = pullImage === "custom" ? pullCustomImage : pullImage;
  const pullImageName = resolvedPullImage.split(":")[0];
  const activePull = pullStatus?.pull_operations?.[pullImageName];

  // Detect when pull completes
  useEffect(() => {
    if (!isPulling || !activePull) return;
    if (activePull.status === "completed" || activePull.status === "failed") {
      setIsPulling(false);
      if (activePull.status === "completed") {
        setPullDone(true);
        qc.invalidateQueries({ queryKey: ["gateway-status", server] });
        setTimeout(() => setPullDone(false), 4000);
      }
    }
  }, [activePull?.status, isPulling, server, qc]);

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

  const pullMut = useMutation({
    mutationFn: () =>
      api.pullGatewayImage(server!, { image: resolvedPullImage }),
    onSuccess: () => {
      setIsPulling(true);
    },
  });

  if (!server) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Select a server first.
      </p>
    );
  }

  const running = status?.running ?? false;
  const pulling = pullMut.isPending || isPulling;

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

        {/* Container details */}
        {running && (status?.image || status?.created_at) && (
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--color-border)] pt-3">
            {status.image && (
              <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                <span className="text-[var(--color-text-muted)]/60">Image:</span>
                <code className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 font-mono text-[var(--color-text)]">
                  {status.image}
                </code>
              </div>
            )}
            {status.created_at && (
              <div className="text-xs text-[var(--color-text-muted)]">
                <span className="text-[var(--color-text-muted)]/60">Created: </span>
                {formatRelativeTime(status.created_at)}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Pull Image */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
        <button
          onClick={() => setShowPull(!showPull)}
          className="flex w-full items-center justify-between p-3 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
        >
          <div className="flex items-center gap-2">
            <Download className="h-4 w-4" />
            <span className="font-medium">Pull Image</span>
            {pulling && (
              <span className="flex items-center gap-1 text-xs text-[var(--color-primary)]">
                <Loader2 className="h-3 w-3 animate-spin" /> pulling...
              </span>
            )}
          </div>
          {showPull ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        {showPull && (
          <div className="border-t border-[var(--color-border)] p-4 space-y-3">
            <div className="flex flex-wrap items-end gap-2">
              {IMAGE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setPullImage(opt.value)}
                  disabled={pulling}
                  className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                    pullImage === opt.value
                      ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)]"
                  } disabled:opacity-50`}
                >
                  {opt.label}
                </button>
              ))}
              <button
                onClick={() => setPullImage("custom")}
                disabled={pulling}
                className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                  pullImage === "custom"
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)]"
                } disabled:opacity-50`}
              >
                Custom
              </button>
              {pullImage !== "custom" && (
                <button
                  onClick={() => pullMut.mutate()}
                  disabled={pulling}
                  className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                >
                  {pulling ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                  Pull
                </button>
              )}
            </div>
            {pullImage === "custom" && (
              <div className="flex items-center gap-2">
                <input
                  value={pullCustomImage}
                  onChange={(e) => setPullCustomImage(e.target.value)}
                  disabled={pulling}
                  className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-50"
                  placeholder="org/image:tag"
                />
                <button
                  onClick={() => pullMut.mutate()}
                  disabled={pulling || !pullCustomImage}
                  className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                >
                  {pulling ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                  Pull
                </button>
              </div>
            )}

            {/* Pull progress */}
            {isPulling && activePull && (
              <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-[var(--color-primary)]">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Pulling {resolvedPullImage}
                  </span>
                  <span className="text-[var(--color-text-muted)]">
                    {formatDuration(activePull.duration_seconds)}
                  </span>
                </div>
                <p className="text-xs text-[var(--color-text-muted)] font-mono truncate">
                  {activePull.progress}
                </p>
              </div>
            )}
            {isPulling && !activePull && (
              <div className="flex items-center gap-1.5 text-xs text-[var(--color-primary)]">
                <Loader2 className="h-3 w-3 animate-spin" />
                Starting pull...
              </div>
            )}

            {pullDone && (
              <p className="flex items-center gap-1 text-xs text-emerald-400">
                <Check className="h-3 w-3" /> Image pulled successfully.
              </p>
            )}
            {!isPulling && pullMut.error && (
              <p className="text-xs text-[var(--color-red)]">{pullMut.error.message}</p>
            )}
            {!isPulling && activePull?.status === "failed" && (
              <p className="text-xs text-[var(--color-red)]">Pull failed: {activePull.progress}</p>
            )}
          </div>
        )}
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
