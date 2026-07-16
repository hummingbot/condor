import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landmark, Loader2, Plus, Star, Trash2, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { FallbackSpinner } from "@/components/ui/FallbackSpinner";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api, type VenueEntry } from "@/lib/api";

function OnboardDialog({
  initialVenueId,
  onClose,
}: {
  initialVenueId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  useEscapeKey(true, onClose);
  const [venueId, setVenueId] = useState(initialVenueId);
  const [name, setName] = useState("");
  const [credentialsText, setCredentialsText] = useState("{\n  \n}");
  const [parseError, setParseError] = useState<string | null>(null);

  const onboardMutation = useMutation({
    mutationFn: () => {
      const credentials = JSON.parse(credentialsText) as Record<string, unknown>;
      return api.onboardVenueAccount(venueId.trim(), { credentials, name: name.trim() });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["venues"] });
      onClose();
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    try {
      const parsed = JSON.parse(credentialsText);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setParseError("Credentials must be a JSON object");
        return;
      }
    } catch (err) {
      setParseError(err instanceof Error ? err.message : "Invalid JSON");
      return;
    }
    setParseError(null);
    onboardMutation.mutate();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <form
        onSubmit={handleSubmit}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md space-y-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">Add Account</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]" title="Close" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Venue
          </label>
          <input
            type="text"
            value={venueId}
            onChange={(e) => setVenueId(e.target.value)}
            placeholder="e.g. solana"
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Display Name (optional)
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. main"
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Credentials (JSON)
          </label>
          <textarea
            value={credentialsText}
            onChange={(e) => setCredentialsText(e.target.value)}
            spellCheck={false}
            rows={6}
            className="w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
          />
          <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">
            The custody address is derived from the credentials and verified with a read-only probe. Secrets are sealed server-side.
          </p>
        </div>

        {(parseError || onboardMutation.isError) && (
          <p className="text-xs text-[var(--color-red)]">
            {parseError ??
              (onboardMutation.error instanceof Error
                ? onboardMutation.error.message
                : "Onboarding failed")}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!venueId.trim() || onboardMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {onboardMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Add Account
          </button>
        </div>
      </form>
    </div>
  );
}

function VenueCard({ venueId, entry }: { venueId: string; entry: VenueEntry }) {
  const qc = useQueryClient();
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [removalWarning, setRemovalWarning] = useState<string | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["venues"] });

  const defaultMutation = useMutation({
    mutationFn: (address: string) => api.setDefaultVenueAccount(venueId, address),
    onSuccess: invalidate,
  });

  const removeMutation = useMutation({
    mutationFn: (address: string) => api.removeVenueAccount(venueId, address),
    onSuccess: (data) => {
      setRemovalWarning(data.warning);
      setConfirmRemove(null);
      invalidate();
    },
  });

  const mutationError =
    (defaultMutation.error as Error | null) ?? (removeMutation.error as Error | null);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="mb-3 flex items-center gap-2">
        <Landmark className="h-4 w-4 text-[var(--color-text-muted)]" />
        <h2 className="font-mono text-sm font-bold text-[var(--color-text)]">{venueId}</h2>
        <span className="text-xs text-[var(--color-text-muted)]">
          {entry.accounts.length} account{entry.accounts.length !== 1 ? "s" : ""}
        </span>
      </div>

      {entry.accounts.length === 0 ? (
        <p className="text-xs text-[var(--color-text-muted)]">No accounts.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">Address</th>
                <th className="px-2 py-1">Default</th>
                <th className="px-2 py-1 w-20" />
              </tr>
            </thead>
            <tbody>
              {entry.accounts.map((acct) => (
                <tr key={acct.address} className="border-t border-[var(--color-border)]/40">
                  <td className="px-2 py-1.5 text-[var(--color-text)]">{acct.name || "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-[var(--color-text-muted)]">{acct.address}</td>
                  <td className="px-2 py-1.5">
                    {acct.is_default ? (
                      <span className="flex items-center gap-1 text-amber-400">
                        <Star className="h-3 w-3 fill-current" /> default
                      </span>
                    ) : (
                      <button
                        onClick={() => defaultMutation.mutate(acct.address)}
                        disabled={defaultMutation.isPending}
                        className="text-[var(--color-text-muted)] underline decoration-dotted hover:text-[var(--color-text)]"
                      >
                        make default
                      </button>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {confirmRemove === acct.address ? (
                      <span className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => removeMutation.mutate(acct.address)}
                          disabled={removeMutation.isPending}
                          className="rounded bg-[var(--color-red)] px-2 py-0.5 text-[10px] font-semibold text-white"
                        >
                          Remove
                        </button>
                        <button
                          onClick={() => setConfirmRemove(null)}
                          className="rounded px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]"
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        onClick={() => setConfirmRemove(acct.address)}
                        className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
                        title="Remove account"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {mutationError && (
        <p className="mt-2 text-xs text-[var(--color-red)]">{mutationError.message}</p>
      )}
      {removalWarning && (
        <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[10px] text-amber-400">
          {removalWarning}
        </p>
      )}
    </div>
  );
}

export function Venues() {
  const [showOnboard, setShowOnboard] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["venues"],
    queryFn: api.getVenues,
  });

  const venues = Object.entries(data?.venues ?? {});

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-[var(--color-text)]">Venues</h1>
        <button
          onClick={() => setShowOnboard("")}
          className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Account
        </button>
      </div>

      {isLoading ? (
        <FallbackSpinner />
      ) : error ? (
        <p className="text-sm text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Failed to load venues"}
        </p>
      ) : venues.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] py-12 text-[var(--color-text-muted)]">
          <Landmark className="h-8 w-8 opacity-40" />
          <p className="text-sm">No venue accounts yet</p>
          <p className="text-xs">Add an account to let Condor manage venue state.</p>
        </div>
      ) : (
        venues.map(([venueId, entry]) => (
          <VenueCard key={venueId} venueId={venueId} entry={entry} />
        ))
      )}

      {showOnboard !== null && (
        <OnboardDialog initialVenueId={showOnboard} onClose={() => setShowOnboard(null)} />
      )}
    </div>
  );
}
