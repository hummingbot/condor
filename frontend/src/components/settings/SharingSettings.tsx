import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Lock,
  MessageSquare,
  Share2,
  ShieldOff,
  Trash2,
} from "lucide-react";

import { api } from "@/lib/api";

const SHARES_KEY = ["shared-conversations"] as const;
const SETTINGS_KEY = ["sharing-settings"] as const;

function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * What this user has handed to the project, and the admin's switch over it.
 *
 * It sits under Settings → Privacy beside the telemetry card, and the copy has
 * to keep the two apart, because they are genuinely different promises:
 * telemetry is anonymous counts the *admin* consents to once for the whole
 * install; this is *content*, and only the person who said it can hand it over,
 * one conversation at a time, after seeing exactly what would be sent.
 *
 * Collapsing them into one switch would misrepresent one of them, so they are
 * two cards with the distinction written down.
 */
export function SharingSettings() {
  const queryClient = useQueryClient();

  const { data: settings } = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: api.getSharingSettings,
    retry: false,
  });

  const { data: shares = [], isLoading } = useQuery({
    queryKey: SHARES_KEY,
    queryFn: api.listSharedConversations,
    retry: false,
  });

  const unshare = useMutation({
    mutationFn: (id: string) => api.unshareConversation(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SHARES_KEY });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.setSharingEnabled(enabled),
    onSuccess: (next) => queryClient.setQueryData(SETTINGS_KEY, next),
  });

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">
          <Share2 className="mr-1.5 inline h-4 w-4" />
          Conversations you have shared
        </h3>
        <div className="space-y-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
          <p>
            Separate from usage telemetry above, and it works differently.
            Telemetry is anonymous counts the admin turns on once for this whole
            install. A shared conversation is <strong>content</strong>: only you
            can share one of yours, one at a time, and only after reading the
            redacted transcript we would send.
          </p>
          <p>
            Keys, wallet addresses, server names, accounts and file paths are
            replaced before anything leaves. Amounts and prices are kept — a
            transcript without them cannot show whether the agent was right.
          </p>
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Currently shared
        </h4>

        {isLoading && (
          <div className="flex items-center gap-2 py-4 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading…
          </div>
        )}

        {!isLoading && shares.length === 0 && (
          <p className="rounded-lg border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
            Nothing shared. Use the share button on a conversation in the chat
            rail to hand one over.
          </p>
        )}

        <div className="space-y-1">
          {shares.map((item) => (
            <div
              key={item.share_id}
              className="flex items-center gap-3 rounded-lg border border-[var(--color-border)] px-3 py-2"
            >
              <MessageSquare className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-[var(--color-text)]">
                  {item.title || "Untitled chat"}
                </p>
                <p className="text-[11px] text-[var(--color-text-muted)]">
                  {item.turn_count} turns · shared {when(item.shared_at)}
                  {item.revision > 1 ? ` · revision ${item.revision}` : ""}
                </p>
              </div>
              <button
                onClick={() => unshare.mutate(item.conversation_id)}
                disabled={unshare.isPending}
                className="flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-red)] hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
              >
                <Trash2 className="h-3 w-3" />
                Unshare
              </button>
            </div>
          ))}
        </div>

        {shares.length > 0 && (
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            Unsharing deletes the copy on our side. Deleting a conversation here
            unshares it first, so it never outlives the chat it came from.
          </p>
        )}
        {unshare.isError && (
          <p className="mt-2 text-xs text-[var(--color-red)]">
            Could not unshare that. {(unshare.error as Error).message}
          </p>
        )}
      </section>

      {settings?.can_change && (
        <section>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            This install
          </h4>
          <button
            disabled={settings.env_overridden || toggle.isPending}
            onClick={() => toggle.mutate(!settings.enabled)}
            className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
              settings.enabled
                ? "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
                : "border-[var(--color-primary)] bg-[var(--color-primary)]/10"
            } ${settings.env_overridden ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
          >
            <ShieldOff
              className={`h-4 w-4 shrink-0 ${
                settings.enabled
                  ? "text-[var(--color-text-muted)]"
                  : "text-[var(--color-primary)]"
              }`}
            />
            <span className="flex-1 text-sm text-[var(--color-text)]">
              {settings.enabled
                ? "Turn off conversation sharing for everyone on this install"
                : "Conversation sharing is off for everyone on this install"}
            </span>
          </button>
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            An install-wide veto, so only you can set it. Turning it off hides
            the share button for every user here; anything already shared can
            still be unshared.
          </p>
        </section>
      )}

      {settings?.env_overridden && (
        <p className="flex items-start gap-1.5 text-xs text-[var(--color-text-muted)]">
          <Lock className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            <code>CONDOR_SHARING</code> is set in this install's environment and
            overrides anything chosen here. Nothing can be shared.
          </span>
        </p>
      )}
    </div>
  );
}
