import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BellOff, Loader2, Lock, Monitor, ShieldAlert, Trash2 } from "lucide-react";

import { api, type PushSubscriptionRow } from "@/lib/api";
import { formatRelativeTime } from "@/lib/formatters";
import {
  currentOrigin,
  currentSubscription,
  PermissionDeniedError,
  pushSupport,
  subscribe,
  unsubscribe,
} from "@/lib/push";

const SUBSCRIPTIONS_KEY = ["push", "subscriptions"] as const;

/**
 * Desktop notifications, per device (FEAT-083).
 *
 * The bell already stores everything Condor has to say; this is the switch that
 * makes it *arrive* — as a real OS notification, with no Condor window open and
 * no Telegram. It is per browser, per device, because a Web Push subscription
 * is: turning it on here says nothing about the phone.
 *
 * The three states below are the whole reason this pane exists rather than a
 * toast somewhere. Web Push needs a secure context, and Condor is commonly
 * reached over a tailnet at `http://host:8088`, where the API is not restricted
 * but absent. Without somewhere to say that, the feature is invisible on
 * exactly the deployment the README recommends and the user never learns why.
 */
export function NotificationsSettings() {
  const support = pushSupport();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  // Whether *this* browser holds a subscription — not the same question as
  // "are there rows", which is about every device the account has.
  const [thisDevice, setThisDevice] = useState<string | null>(null);
  const [checking, setChecking] = useState(support === "supported");

  const { data, isLoading } = useQuery({
    queryKey: SUBSCRIPTIONS_KEY,
    queryFn: () => api.getPushSubscriptions(),
    enabled: support === "supported",
  });

  useEffect(() => {
    if (support !== "supported") return;
    let live = true;
    currentSubscription()
      .then((sub) => live && setThisDevice(sub?.endpoint ?? null))
      .catch(() => live && setThisDevice(null))
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
  }, [support]);

  const refresh = async () => {
    const sub = await currentSubscription().catch(() => null);
    setThisDevice(sub?.endpoint ?? null);
    await queryClient.invalidateQueries({ queryKey: SUBSCRIPTIONS_KEY });
  };

  const toggle = useMutation({
    mutationFn: async (on: boolean) => (on ? subscribe() : unsubscribe()),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (err: Error) =>
      setError(
        err instanceof PermissionDeniedError
          ? "This browser is blocking notifications for Condor. Only the browser can undo that — open the padlock in the address bar and allow notifications, then try again."
          : err.message,
      ),
  });

  const forget = useMutation({
    mutationFn: (endpoint: string) => api.pushUnsubscribe(endpoint),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (err: Error) => setError(err.message),
  });

  if (support === "insecure-context") {
    return (
      <Frame>
        <div className="flex items-start gap-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <Lock className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
          <div className="space-y-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
            <p>
              Desktop notifications need a secure connection. You are on{" "}
              <code className="text-[var(--color-text)]">{currentOrigin()}</code>.
            </p>
            <p>
              Serve the dashboard over HTTPS —{" "}
              <code className="text-[var(--color-text)]">tailscale serve</code> gives
              you one on your tailnet — and this switch appears. Nothing else about
              Condor changes; the bell above keeps working exactly as it does now.
            </p>
          </div>
        </div>
      </Frame>
    );
  }

  if (support === "unsupported") {
    return (
      <Frame>
        <div className="flex items-start gap-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
          <p className="text-xs leading-relaxed text-[var(--color-text-muted)]">
            This browser has no Web Push support, so there is nothing to turn on
            here. Chrome, Edge, Firefox and Safari 16.1+ all have it; on iOS the
            app has to be added to the Home Screen first.
          </p>
        </div>
      </Frame>
    );
  }

  const items: PushSubscriptionRow[] = data?.items ?? [];
  const on = !!thisDevice;
  const busy = toggle.isPending || checking;

  return (
    <Frame>
      <button
        disabled={busy}
        onClick={() => toggle.mutate(!on)}
        className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors ${
          on
            ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10"
            : "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
        } ${busy ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
      >
        {busy ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--color-text-muted)]" />
        ) : on ? (
          <Bell className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
        ) : (
          <BellOff className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
        )}
        <span className="flex-1">
          <span className="block text-sm text-[var(--color-text)]">
            {on ? "On for this device" : "Turn on for this device"}
          </span>
          <span className="block text-xs text-[var(--color-text-muted)]">
            {on
              ? "Anything that lands in the bell also lands in Notification Center."
              : "Ring this machine even when Condor is closed."}
          </span>
        </span>
      </button>

      {error && (
        <p className="text-xs leading-relaxed text-[var(--color-red)]">{error}</p>
      )}

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Devices
        </h4>
        {isLoading ? (
          <div className="flex items-center justify-center py-6 text-[var(--color-text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)]">
            No device is subscribed yet.
          </p>
        ) : (
          <ul className="space-y-1">
            {items.map((row) => (
              <li
                key={row.endpoint}
                className="flex items-center gap-2.5 rounded-lg border border-[var(--color-border)] px-3 py-2"
              >
                <Monitor className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-[var(--color-text)]">
                    {row.label || "Unnamed device"}
                    {row.endpoint === thisDevice && (
                      <span className="ml-1.5 text-[var(--color-text-muted)]">
                        (this one)
                      </span>
                    )}
                  </span>
                  <span className="block text-[10px] text-[var(--color-text-muted)]">
                    Added {formatRelativeTime(row.created)}
                  </span>
                </span>
                <button
                  disabled={forget.isPending}
                  onClick={() =>
                    row.endpoint === thisDevice
                      ? toggle.mutate(false)
                      : forget.mutate(row.endpoint)
                  }
                  title="Stop notifying this device"
                  className="shrink-0 rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs leading-relaxed text-[var(--color-text-muted)]">
        Notification text is encrypted to this browser's own key before it
        leaves the machine, so the push service that relays it cannot read it.
        If you also use Telegram you will get both — turn this off on a device
        where the second buzz is not worth it.
      </p>
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">
          <Bell className="mr-1.5 inline h-4 w-4" />
          Desktop notifications
        </h3>
        <p className="text-xs leading-relaxed text-[var(--color-text-muted)]">
          A routine that finishes at 3am, a delegation that completes while the
          laptop is asleep — these ring on this machine with no Condor window
          open. Clicking one opens Condor on the thing that happened.
        </p>
      </section>
      {children}
    </div>
  );
}
