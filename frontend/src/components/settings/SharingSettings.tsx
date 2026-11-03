import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Check,
  Loader2,
  Lock,
  MessageSquare,
  Share2,
  ShieldOff,
  Trash2,
} from "lucide-react";

import { api, type SharingState } from "@/lib/api";

const SHARES_KEY = ["shared-conversations"] as const;
const SETTINGS_KEY = ["sharing-settings"] as const;
const PREFERENCE_KEY = ["sharing-preference"] as const;

/**
 * The three answers, with the copy that makes each one mean what it does.
 *
 * Always is described in full rather than summarised, because it is the only
 * one where nobody reads the payload before it leaves. Every limit on it —
 * idle, forward-only, single-author — is stated here, at the moment of the
 * choice, and not left to be discovered in `PRIVACY.md` afterwards.
 */
const CHOICES: { value: SharingState; label: string; detail: string }[] = [
  {
    value: "off",
    label: "Off",
    detail:
      "Nothing is shared. The share button stays available if you want it.",
  },
  {
    value: "explicit",
    label: "Ask me",
    detail:
      "Share one conversation at a time, with the button, after reading the " +
      "redacted transcript. This is what everyone gets by default.",
  },
  {
    value: "always",
    label: "Always",
    detail:
      "Share my conversations automatically once they have been idle for half " +
      "an hour — redacted the same way, but without showing me first. Only " +
      "chats started after I choose this, only ones where I am the only person " +
      "talking, and never one I have excluded.",
  },
];

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
  const [confirmingPurge, setConfirmingPurge] = useState(false);

  const { data: settings } = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: api.getSharingSettings,
    retry: false,
  });

  const { data: preference } = useQuery({
    queryKey: PREFERENCE_KEY,
    queryFn: api.getSharingPreference,
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
      // Same reason the purge below does it: unsharing one row also excludes
      // that conversation, so an open chat header must stop saying it is
      // shared without waiting for a reload.
      queryClient.invalidateQueries({ queryKey: ["conversation-sharing"] });
    },
  });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.setSharingEnabled(enabled),
    onSuccess: (next) => queryClient.setQueryData(SETTINGS_KEY, next),
  });

  const choose = useMutation({
    mutationFn: (state: SharingState) => api.setSharingPreference(state),
    onSuccess: (next) => {
      queryClient.setQueryData(PREFERENCE_KEY, next);
      // Every chat header reads this; a stale one would keep promising that a
      // conversation is about to be shared after the user said stop.
      queryClient.invalidateQueries({ queryKey: ["conversation-sharing"] });
    },
  });

  const purge = useMutation({
    mutationFn: () => api.unshareEverything(),
    onSuccess: () => {
      setConfirmingPurge(false);
      queryClient.invalidateQueries({ queryKey: SHARES_KEY });
      queryClient.invalidateQueries({ queryKey: PREFERENCE_KEY });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["conversation-sharing"] });
    },
  });

  const state = preference?.state ?? "off";

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
            can share one of yours, and by default only after reading the
            transcript we would send, with its sensitive content redacted first.
            You choose below whether that stays one button press at a time.
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
          Share my conversations with Condor
        </h4>

        <div className="space-y-1">
          {CHOICES.map((choice) => {
            const selected = state === choice.value;
            // Always is the only answer that needs the install's permission:
            // it is the only one that sends anything on its own.
            const blocked =
              choice.value === "always" && preference?.allowed === false;
            return (
              <button
                key={choice.value}
                disabled={blocked || choose.isPending}
                onClick={() => choose.mutate(choice.value)}
                className={`flex w-full items-start gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                  selected
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10"
                    : "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
                } ${blocked ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}
              >
                <span
                  className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                    selected
                      ? "border-[var(--color-primary)] bg-[var(--color-primary)]"
                      : "border-[var(--color-border)]"
                  }`}
                >
                  {selected && <Check className="h-2.5 w-2.5 text-white" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-[var(--color-text)]">
                    {choice.label}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-[var(--color-text-muted)]">
                    {choice.detail}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        {state === "always" && preference?.sweeping && (
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            Every conversation this covers says so in its own header, with one
            click to leave it out. Turning this off deletes anything queued but
            not yet sent.
          </p>
        )}
        {state === "always" && preference?.allowed === false && (
          <p className="mt-2 text-xs text-[var(--color-yellow)]">
            Your answer is Always, but sharing is turned off for this install,
            so nothing is going out.
          </p>
        )}
        {choose.isError && (
          <p className="mt-2 text-xs text-[var(--color-red)]">
            Could not save that. {(choose.error as Error).message}
          </p>
        )}
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
            Unsharing deletes the copy on our side, and takes that chat out of
            automatic sharing for good — it will not be sent again on its own,
            however much it grows. To put one back, open its share dialog from
            the conversation list and use{" "}
            <span className="text-[var(--color-text)]">Include it</span>.
            Deleting a conversation here unshares it first, so it never outlives
            the chat it came from.
          </p>
        )}
        {unshare.isError && (
          <p className="mt-2 text-xs text-[var(--color-red)]">
            Could not unshare that. {(unshare.error as Error).message}
          </p>
        )}

        {/* The back catalogue, as one button. Deliberately separate from the
            Off choice above: turning off future sharing and withdrawing what
            you already gave are two different decisions, and neither is the
            default for the other. */}
        {shares.length > 0 &&
          (confirmingPurge ? (
            <div className="mt-3 rounded-lg border border-[var(--color-red)]/40 bg-[var(--color-red)]/5 px-3 py-2">
              <p className="text-xs text-[var(--color-text)]">
                Unshare all {shares.length} conversations? Every copy we hold is
                deleted. The chats themselves stay here, untouched.
              </p>
              {/* The caveat this button earns: each one is also excluded from
                  automatic sharing, and there is no bulk way back. Somebody who
                  later wants Always to cover their archive again has to include
                  the chats one at a time, so they are told before, not after. */}
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                {state === "always"
                  ? "All of them also stop being shared automatically, permanently. Turning Always back on will not cover them again — each one has to be included from its own share dialog."
                  : "All of them are also taken out of automatic sharing, so turning Always on later will not cover them — each one has to be included from its own share dialog."}
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => purge.mutate()}
                  disabled={purge.isPending}
                  className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
                >
                  {purge.isPending && (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  )}
                  Yes, delete everything I've shared
                </button>
                <button
                  onClick={() => setConfirmingPurge(false)}
                  className="rounded-md px-3 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setConfirmingPurge(true)}
              className="mt-3 flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-red)] hover:bg-[var(--color-surface-hover)]"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete everything I've shared
            </button>
          ))}
        {purge.isError && (
          <p className="mt-2 text-xs text-[var(--color-red)]">
            Could not unshare everything. {(purge.error as Error).message}
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
