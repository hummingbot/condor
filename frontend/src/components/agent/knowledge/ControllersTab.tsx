import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useState } from "react";

import { Chip, Empty } from "@/components/agent/knowledge/KnowledgeChrome";
import { useServer } from "@/hooks/useServer";
import {
  api,
  type ControllerActionResult,
  type ControllerCard,
  type ControllerVerdict,
} from "@/lib/api";

/**
 * The controllers an agent owns or inherits, and where each stands on the
 * chat's active server (FEAT-127).
 *
 * The list itself comes off `/brain` (disk only), so it renders with the other
 * sections. Whether the server has each one costs a round-trip per controller,
 * so it is this tab's own fetch, made when the tab opens and again after every
 * action — an unreachable server slows this tab and nothing else.
 *
 * Read plus push: source and styles are read here and never edited — authoring
 * stays with the agent or the operator's editor.
 */
export function ControllersTab({
  slug,
  controllers,
}: {
  slug: string;
  controllers: ControllerCard[];
}) {
  const { server } = useServer();
  const statusKey = ["agent-controllers", slug, server] as const;
  const status = useQuery({
    queryKey: statusKey,
    queryFn: () => api.getAgentControllers(slug, server as string),
    enabled: !!server && controllers.length > 0,
    staleTime: 0,
    retry: false,
  });

  // Controllers overwritten this visit: the server's backtests keep running
  // the class they imported first, and that stays true until the API restarts,
  // so the note stays until the tab is left.
  const [stale, setStale] = useState<string[]>([]);

  const queryClient = useQueryClient();
  const refetch = () =>
    queryClient.invalidateQueries({ queryKey: ["agent-controllers", slug] });

  const verdictOf = (name: string): Badge => {
    if (!server) return "no_server";
    if (status.isError) return "unreachable";
    if (status.isLoading || !status.data) return "checking";
    const row = status.data.controllers.find((c) => c.name === name);
    // A row the server listing did not answer for is not "in sync".
    return row?.server?.verdict ?? "unreachable";
  };
  const detailOf = (name: string): string | undefined => {
    if (status.isError) {
      return status.error instanceof Error ? status.error.message : undefined;
    }
    return status.data?.controllers.find((c) => c.name === name)?.server
      ?.detail;
  };

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Hummingbot controllers this agent carries in its own folder, then the
        shared ones every agent reads. Status is against{" "}
        {server ? (
          <strong className="text-[var(--color-text)]">{server}</strong>
        ) : (
          "the active server (none selected)"
        )}
        . A missing one can be synced; a drifted one is only replaced after
        you have read the diff, and the server copy is backed up first.
      </p>
      {stale.length > 0 && (
        <p
          role="status"
          className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-400"
        >
          Backtests on this server use the old class of{" "}
          {stale.join(", ")} until the API restarts.
        </p>
      )}
      {controllers.length === 0 ? (
        <Empty>No controllers in this agent's folder.</Empty>
      ) : (
        controllers.map((c) => (
          <ControllerItem
            key={c.name}
            slug={slug}
            card={c}
            server={server}
            badge={verdictOf(c.name)}
            detail={detailOf(c.name)}
            onChanged={refetch}
            onStale={(name) =>
              setStale((s) => (s.includes(name) ? s : [...s, name]))
            }
          />
        ))
      )}
    </div>
  );
}

type Badge = ControllerVerdict | "checking" | "no_server";

const BADGES: Record<Badge, { label: string; tone: string; title: string }> = {
  in_sync: {
    label: "in sync",
    tone: "border-emerald-500/40 text-emerald-400",
    title: "The server's copy matches the folder",
  },
  missing: {
    label: "missing",
    tone: "border-sky-500/40 text-sky-400",
    title: "The server does not have this controller",
  },
  drift: {
    label: "drift",
    tone: "border-amber-500/40 text-amber-400",
    title: "The server's copy differs from the folder",
  },
  unreachable: {
    label: "unreachable",
    tone: "border-[var(--color-red)]/40 text-[var(--color-red)]",
    title: "The server did not answer — that is not 'in sync'",
  },
  checking: {
    label: "checking…",
    tone: "border-[var(--color-border)] text-[var(--color-text-muted)]",
    title: "Asking the server",
  },
  no_server: {
    label: "no server",
    tone: "border-[var(--color-border)] text-[var(--color-text-muted)]",
    title: "Select a server to see where this controller stands",
  },
};

function StatusBadge({ badge, detail }: { badge: Badge; detail?: string }) {
  const b = BADGES[badge];
  return (
    <span
      data-testid="controller-status"
      title={detail ? `${b.title} — ${detail}` : b.title}
      className={`shrink-0 rounded border bg-[var(--color-surface)] px-1.5 py-px text-[10px] ${b.tone}`}
    >
      {b.label}
    </span>
  );
}

const BUTTON =
  "rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-[11px] text-[var(--color-text)] hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)] disabled:opacity-50";
const DANGER =
  "rounded border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-400 hover:bg-amber-500/20 disabled:opacity-50";

/**
 * A destructive action that takes two clicks: the first arms it, the second
 * (`Confirm`) runs it. Inline rather than a dialog, like the unsaved-draft
 * guard — the diff it is about stays on screen beside it.
 */
function OverwritePair({
  label,
  pending,
  onConfirm,
}: {
  label: string;
  pending: boolean;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <button
        type="button"
        className={DANGER}
        disabled={pending}
        onClick={() => setArmed(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        className={DANGER}
        disabled={pending}
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        Confirm
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={pending}
        onClick={() => setArmed(false)}
      >
        Cancel
      </button>
    </span>
  );
}

/** What the server said back — the message, or the refusal and its diff. */
function Outcome({ result }: { result: ControllerActionResult }) {
  return (
    <div className="space-y-1 text-[11px]">
      {result.refused ? (
        <p className="text-amber-400">{result.reason}</p>
      ) : (
        result.message && (
          <p className="text-[var(--color-text-muted)]">{result.message}</p>
        )
      )}
      {result.warning && <p className="text-amber-400">{result.warning}</p>}
      {result.backup && (
        <p className="text-[var(--color-text-muted)]">
          Server copy backed up to{" "}
          <code className="break-all">{result.backup}</code>
        </p>
      )}
      {result.error && (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[var(--color-surface-hover)] p-2 text-[10px] text-[var(--color-red)]">
          {result.error}
        </pre>
      )}
      {result.diff && <Diff text={result.diff} />}
    </div>
  );
}

function Diff({ text }: { text: string }) {
  return (
    <pre
      data-testid="controller-diff"
      className="max-h-72 overflow-auto rounded bg-[var(--color-surface-hover)] p-2 font-mono text-[10px] leading-snug"
    >
      {text.split("\n").map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+") && !line.startsWith("+++")
              ? "text-emerald-400"
              : line.startsWith("-") && !line.startsWith("---")
                ? "text-[var(--color-red)]"
                : "text-[var(--color-text-muted)]"
          }
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

function errorResult(name: string, e: unknown): ControllerActionResult {
  return {
    name,
    refused: true,
    reason: e instanceof Error ? e.message : "The request failed.",
  };
}

function ControllerItem({
  slug,
  card,
  server,
  badge,
  detail,
  onChanged,
  onStale,
}: {
  slug: string;
  card: ControllerCard;
  server: string | null;
  badge: Badge;
  detail?: string;
  onChanged: () => void;
  onStale: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<ControllerActionResult | null>(null);

  const sync = useMutation({
    mutationFn: (overwrite: boolean) =>
      api.syncAgentController(slug, card.name, server as string, overwrite),
    onSuccess: (r) => {
      setResult(r);
      if (r.backtest_cache_stale) onStale(card.name);
    },
    onError: (e) => setResult(errorResult(card.name, e)),
    onSettled: onChanged,
  });

  const canPush = !!server && !!card.controller_type;
  // A refused drift sync carries the diff; only then is Overwrite on offer.
  const reviewed =
    result?.refused && result.verdict === "drift" && !!result.diff;

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="flex items-start gap-2 px-2.5 py-2">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="flex min-w-0 flex-1 items-start gap-1.5 text-left"
        >
          {open ? (
            <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
          ) : (
            <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
          )}
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-1.5">
              <span className="truncate font-mono text-xs text-[var(--color-text)]">
                {card.name}
              </span>
              {card.controller_type ? (
                <Chip title="Controller type">{card.controller_type}</Chip>
              ) : (
                <Chip tone="warn" title={card.type_error}>
                  type unknown
                </Chip>
              )}
              {card.shared && (
                <Chip title="From the shared library every agent reads">
                  shared
                </Chip>
              )}
              {card.styles.length > 0 && (
                <Chip title={card.styles.join(", ")}>
                  {card.styles.length} style
                  {card.styles.length === 1 ? "" : "s"}
                </Chip>
              )}
            </span>
            {card.description && (
              <span className="mt-0.5 block text-[11px] text-[var(--color-text-muted)]">
                {card.description}
              </span>
            )}
          </span>
        </button>
        <StatusBadge badge={badge} detail={detail} />
      </div>

      {(badge === "missing" || badge === "drift" || result) && (
        <div className="space-y-1.5 border-t border-[var(--color-border)] px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {badge === "missing" && canPush && (
              <button
                type="button"
                className={BUTTON}
                disabled={sync.isPending}
                onClick={() => sync.mutate(false)}
              >
                Sync
              </button>
            )}
            {badge === "drift" && canPush && !reviewed && (
              <button
                type="button"
                className={BUTTON}
                disabled={sync.isPending}
                onClick={() => sync.mutate(false)}
              >
                Review diff
              </button>
            )}
            {badge === "drift" && reviewed && (
              <OverwritePair
                label="Overwrite server copy (backup kept)"
                pending={sync.isPending}
                onConfirm={() => sync.mutate(true)}
              />
            )}
            {!card.controller_type && (
              <span className="text-[11px] text-amber-400">
                {card.type_error}
              </span>
            )}
            {sync.isPending && (
              <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />
            )}
          </div>
          {result && <Outcome result={result} />}
        </div>
      )}

      {open && (
        <ControllerReader
          slug={slug}
          card={card}
          server={server}
          onChanged={onChanged}
        />
      )}
    </div>
  );
}

/** The source and the styles, read-only, with Upload beside each style. */
function ControllerReader({
  slug,
  card,
  server,
  onChanged,
}: {
  slug: string;
  card: ControllerCard;
  server: string | null;
  onChanged: () => void;
}) {
  const [style, setStyle] = useState<string | null>(null);
  const source = useQuery({
    queryKey: ["agent-controller-source", slug, card.name],
    queryFn: () => api.getAgentControllerSource(slug, card.name),
    enabled: style === null,
  });

  return (
    <div className="space-y-2 border-t border-[var(--color-border)] px-2.5 py-2">
      {card.styles.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] text-[var(--color-text-muted)]">
            Styles:
          </span>
          <Chip
            tone={style === null ? "accent" : "muted"}
            onClick={() => setStyle(null)}
            title="The controller's source"
          >
            source
          </Chip>
          {card.styles.map((s) => (
            <Chip
              key={s}
              tone={style === s ? "accent" : "muted"}
              onClick={() => setStyle(s)}
              title={`Read the ${s} sample config`}
            >
              {s}
            </Chip>
          ))}
        </div>
      )}
      {style === null ? (
        source.isLoading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-text-muted)]" />
        ) : source.isError ? (
          <p className="text-[11px] text-[var(--color-red)]">
            {source.error instanceof Error
              ? source.error.message
              : "Could not read the source."}
          </p>
        ) : (
          <Code text={source.data?.source ?? ""} label="controller source" />
        )
      ) : (
        <StyleReader
          key={style}
          slug={slug}
          card={card}
          style={style}
          server={server}
          onChanged={onChanged}
        />
      )}
    </div>
  );
}

function Code({ text, label }: { text: string; label: string }) {
  return (
    <pre
      aria-label={label}
      className="max-h-96 overflow-auto rounded bg-[var(--color-surface-hover)] p-2 font-mono text-[10px] leading-snug text-[var(--color-text)]"
    >
      {text}
    </pre>
  );
}

function StyleReader({
  slug,
  card,
  style,
  server,
  onChanged,
}: {
  slug: string;
  card: ControllerCard;
  style: string;
  server: string | null;
  onChanged: () => void;
}) {
  const [result, setResult] = useState<ControllerActionResult | null>(null);
  const sample = useQuery({
    queryKey: ["agent-controller-sample", slug, card.name, style],
    queryFn: () => api.getAgentControllerSample(slug, card.name, style),
  });
  const upload = useMutation({
    mutationFn: (overwrite: boolean) =>
      api.uploadAgentControllerConfig(
        slug,
        card.name,
        style,
        server as string,
        overwrite,
      ),
    onSuccess: setResult,
    onError: (e) => setResult(errorResult(card.name, e)),
    onSettled: onChanged,
  });
  const configName = `${card.name}__${style}`;
  // Only a refusal that carries a diff is a differing config to replace; a
  // missing controller or a silent server is not something overwrite fixes.
  const canOverwrite = !!result?.refused && !!result.diff;

  return (
    <div className="space-y-1.5">
      {sample.isLoading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-text-muted)]" />
      ) : sample.isError ? (
        <p className="text-[11px] text-[var(--color-red)]">
          {sample.error instanceof Error
            ? sample.error.message
            : "Could not read this style."}
        </p>
      ) : (
        <Code text={sample.data?.yaml ?? ""} label={`${style} sample config`} />
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        {server && card.controller_type ? (
          <button
            type="button"
            className={BUTTON}
            disabled={upload.isPending}
            onClick={() => upload.mutate(false)}
          >
            Upload as {configName}
          </button>
        ) : (
          <span className="text-[11px] text-[var(--color-text-muted)]">
            {server
              ? "Upload needs a known controller type."
              : "Select a server to upload this style."}
          </span>
        )}
        {canOverwrite && (
          <OverwritePair
            label={`Overwrite ${configName}`}
            pending={upload.isPending}
            onConfirm={() => upload.mutate(true)}
          />
        )}
        {upload.isPending && (
          <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />
        )}
      </div>
      {result && <Outcome result={result} />}
    </div>
  );
}
