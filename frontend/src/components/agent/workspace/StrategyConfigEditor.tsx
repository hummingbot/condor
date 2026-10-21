import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { api } from "@/lib/api";
import { configToYaml, parseYamlMapping } from "@/lib/configYaml";

/**
 * What a buffer that dropped top-level keys is refused with.
 *
 * `PUT /{slug}/strategies/{sslug}/config` merges rather than replaces, so a key
 * deleted from the buffer is not removed — it keeps whatever is stored, and the
 * save reads as having done something it did not. This is the one thing the
 * editor has to say that the route does not, so it is said here once.
 */
function droppedKeysMessage(keys: readonly string[]): string {
  return (
    `Deleting a line cannot remove a setting: the save merges, so ` +
    `${keys.join(", ")} would keep ${keys.length === 1 ? "its" : "their"} stored ` +
    `value. Put the key back and set its off value instead — \`-1\` for a limit ` +
    `that reads a negative as "no limit", \`""\` for a name.`
  );
}

/** The top-level keys in `stored` that `next` no longer has. */
function droppedKeys(
  stored: readonly string[],
  next: Record<string, unknown>,
): string[] {
  return stored.filter((key) => !Object.hasOwn(next, key));
}

/**
 * A strategy's stored config as editable YAML — the durable edit that existed
 * nowhere in the product (FEAT-142).
 *
 * `YamlConfigEditor` one route over, as its own module for the reason ARCH-300
 * gave for lifting that one out of a 2,700-line file: it depends on nothing in
 * `PlaybookView` but its props.
 *
 * YAML rather than typed fields because the key set is open-ended by design —
 * `ConfigCard`'s `Other` group exists because a config the reader cannot see is
 * one they cannot debug — and because `risk_limits` is a nested mapping that
 * YAML shows plainly and no flat form does.
 */
export function StrategyConfigEditor({
  slug,
  sslug,
  config,
  onDirtyChange,
}: {
  slug: string;
  sslug: string;
  /** The stored config, as the strategy query already resolved it. */
  config: Record<string, unknown>;
  /** Notifies the host card so closing edit mode can confirm a discard. */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();

  // Memoized so typing does not re-run `yaml.dump`, and so a refetch that
  // re-creates `config` with the SAME content leaves this string equal — the
  // re-seed below is keyed on the value, so unsaved edits survive a poll.
  const originalYaml = useMemo(() => configToYaml(config), [config]);
  const [buffer, setBuffer] = useState(originalYaml);
  // Re-seeded during render rather than from an effect, so the card never
  // paints one config's YAML under another's: the strategy query polls, and a
  // frame of the old buffer over the new file is a frame the operator could
  // save. `seed` is the content the buffer came from, so the comparison is by
  // value — a poll that re-creates `config` unchanged is not a re-seed, and
  // unsaved edits survive it.
  const [seed, setSeed] = useState(originalYaml);
  if (seed !== originalYaml) {
    setSeed(originalYaml);
    setBuffer(originalYaml);
  }

  // The baseline for the dropped-key check is the buffer's own starting point,
  // not `config`: whatever `configToYaml` filters is absent from both sides.
  const storedKeys = useMemo(() => {
    const base = parseYamlMapping(originalYaml);
    return base.ok ? Object.keys(base.value) : [];
  }, [originalYaml]);

  // Derived, not state — this is the per-keystroke validation the editors share.
  const parsed = useMemo(() => parseYamlMapping(buffer), [buffer]);
  const dropped = useMemo(
    () => (parsed.ok ? droppedKeys(storedKeys, parsed.value) : []),
    [parsed, storedKeys],
  );
  const blocked = !parsed.ok
    ? parsed.error
    : dropped.length
      ? droppedKeysMessage(dropped)
      : null;

  const isDirty = buffer !== originalYaml;
  useEffect(() => onDirtyChange?.(isDirty), [isDirty, onDirtyChange]);

  const saveMutation = useMutation({
    mutationFn: (text: string) => {
      const result = parseYamlMapping(text);
      if (!result.ok) throw new Error(result.error);
      return api.updateStrategyConfig(slug, sslug, result.value);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
    },
  });

  // A verdict is about the document that produced it, so editing retires it —
  // but a *re-seed* must not. The save invalidates the strategy query, so the
  // refetch it asked for lands a new `config` moments later; tying the banner
  // to the buffer's current text would make the one sentence the operator
  // needs appear and then vanish on its own refetch.
  const edit = (next: string) => {
    setBuffer(next);
    saveMutation.reset();
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {isDirty && (
          <button
            type="button"
            onClick={() => edit(originalYaml)}
            className="flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <RotateCcw className="h-3 w-3" /> Reset
          </button>
        )}
        <button
          type="button"
          onClick={() => saveMutation.mutate(buffer)}
          disabled={!isDirty || !!blocked || saveMutation.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
        >
          {saveMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {saveMutation.isPending ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Why this buffer cannot be sent — an unparseable document, or keys it
          dropped that the merge would silently keep. */}
      {blocked && (
        <div
          role="alert"
          data-config-blocked
          className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs leading-relaxed text-[var(--color-red)]"
        >
          {blocked}
        </div>
      )}

      {/* A refusal from either server-side gate, printed rather than swallowed:
          a Save that appears to do nothing reads as a click that did not land. */}
      {saveMutation.isError && (
        <div
          role="alert"
          data-config-error
          className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs leading-relaxed text-[var(--color-red)]"
        >
          {saveMutation.error instanceof Error
            ? saveMutation.error.message
            : "Save failed"}
        </div>
      )}

      {/* The route's own sentence, verbatim: it is the only thing that knows how
          many running sessions still hold the config this just replaced. */}
      {saveMutation.isSuccess && (
        <div
          data-config-saved
          className="rounded-md border border-[var(--color-green)]/40 bg-[var(--color-green)]/10 px-3 py-2 text-xs leading-relaxed text-[var(--color-green)]"
        >
          {saveMutation.data.message}
        </div>
      )}

      <CodeEditor value={buffer} onChange={edit} language="yaml" height="340px" />
    </div>
  );
}
