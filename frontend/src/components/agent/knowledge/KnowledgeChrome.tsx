import { Pencil, Plus, Save, Trash2, Wand2, X } from "lucide-react";

// ── Shared bits ──
//
// The small pieces every knowledge section is built from: the list row and its
// badges, the buttons that open an editor, and the form chrome those editors
// share. They sit beside the panel rather than inside it because the editors
// and the drill-down are their own modules now — this is the only thing all
// three still have in common, and a module that exports nothing but components
// is one Fast Refresh can reload on its own.

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="py-8 text-center text-xs text-[var(--color-text-muted)]">
      {children}
    </p>
  );
}

export function Chip({
  children,
  title,
  tone = "muted",
  onClick,
}: {
  children: React.ReactNode;
  title?: string;
  tone?: "muted" | "accent" | "warn";
  onClick?: () => void;
}) {
  const tones = {
    muted: "border-[var(--color-border)] text-[var(--color-text-muted)]",
    accent: "border-[var(--color-primary)]/40 text-[var(--color-primary)]",
    warn: "border-amber-500/40 text-amber-400",
  };
  const cls = `shrink-0 rounded border bg-[var(--color-surface)] px-1.5 py-px text-[10px] ${tones[tone]}`;
  if (onClick) {
    return (
      <button
        type="button"
        title={title}
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        className={`${cls} cursor-pointer hover:brightness-125`}
      >
        {children}
      </button>
    );
  }
  return (
    <span title={title} className={cls}>
      {children}
    </span>
  );
}

/**
 * The small on/off switch a row pins left of its edit and delete buttons.
 *
 * `on` is "the agent gets this", not "this is muted": the switch reads the way
 * every switch reads, and the caller inverts once at the seam rather than the
 * eye inverting on every row.
 */
export function Switch({
  on,
  onChange,
  title,
}: {
  on: boolean;
  onChange: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      title={title}
      aria-label={title}
      onClick={(e) => {
        e.stopPropagation();
        onChange();
      }}
      className={`mt-0.5 flex h-3.5 w-6 shrink-0 items-center rounded-full border p-px transition-colors ${
        on
          ? "border-[var(--color-primary)]/50 bg-[var(--color-primary)]/30"
          : "border-[var(--color-border)] bg-[var(--color-surface-hover)]"
      }`}
    >
      <span
        className={`h-2.5 w-2.5 rounded-full transition-transform ${
          on
            ? "translate-x-[10px] bg-[var(--color-primary)]"
            : "translate-x-0 bg-[var(--color-text-muted)]"
        }`}
      />
    </button>
  );
}

/**
 * A list row: a title line, badges, prose, and — when the thing is writable —
 * edit and delete beside it.
 *
 * The row's own click opens it for reading, so the actions are siblings of that
 * button rather than nested inside it.
 *
 * `toggle` is the mute switch (FEAT-090). Unlike edit and delete it is always
 * visible rather than hover-revealed, because it renders *state* and not an
 * action: a row you cannot see the switch on cannot tell you it is off.
 *
 * `onAsk` is the third action (FEAT-092) and rides in the same hover cluster,
 * left of edit so the destructive one stays rightmost. It hands the row to the
 * agent that owns it instead of to a form: some of these rows have no body to
 * edit at all, and the ones that do are usually better revised by the thing
 * that reads them.
 */
export function Row({
  title,
  badges,
  subtitle,
  onClick,
  onAsk,
  onEdit,
  onDelete,
  askTitle,
  editTitle,
  deleteTitle,
  toggle,
  dimmed,
}: {
  title: string;
  badges?: React.ReactNode;
  subtitle?: string;
  onClick?: () => void;
  onAsk?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  askTitle?: string;
  editTitle?: string;
  deleteTitle?: string;
  toggle?: { on: boolean; onChange: () => void; title: string };
  /** Render the row's content faded — it is off, not gone. */
  dimmed?: boolean;
}) {
  const inner = (
    <>
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 truncate text-xs font-medium text-[var(--color-text)]">
          {title}
        </span>
        {badges}
      </div>
      {subtitle && (
        <p className="mt-0.5 text-[11px] leading-snug text-[var(--color-text-muted)]">
          {subtitle}
        </p>
      )}
    </>
  );

  return (
    <div className="group flex items-start gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 transition-colors focus-within:border-[var(--color-primary)]/40 hover:border-[var(--color-primary)]/40">
      {onClick ? (
        <button
          onClick={onClick}
          className={`min-w-0 flex-1 text-left ${dimmed ? "opacity-60" : ""}`}
        >
          {inner}
        </button>
      ) : (
        <div className={`min-w-0 flex-1 ${dimmed ? "opacity-60" : ""}`}>
          {inner}
        </div>
      )}
      {toggle && <Switch {...toggle} />}
      {(onAsk || onEdit || onDelete) && (
        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          {onAsk && (
            <button
              onClick={onAsk}
              title={askTitle || "Ask the agent about this"}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
            >
              <Wand2 className="h-3 w-3" />
            </button>
          )}
          {onEdit && (
            <button
              onClick={onEdit}
              title={editTitle || "Edit"}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            >
              <Pencil className="h-3 w-3" />
            </button>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              title={deleteTitle || "Delete"}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** The button that turns a rendered section into an editable one. */
export function EditButton({
  onClick,
  label = "Edit",
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
    >
      <Pencil className="h-3 w-3" /> {label}
    </button>
  );
}

/** The `+ New …` button above a library. */
export function AddButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className="flex shrink-0 items-center gap-1 rounded border border-dashed border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
    >
      <Plus className="h-3 w-3" /> {label}
    </button>
  );
}

/** One labelled single-line input, for a playbook's or memory's metadata. */
export function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
  mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  mono?: boolean;
}) {
  return (
    <div>
      <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        className={`w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)] ${
          mono ? "font-mono" : ""
        }`}
      />
      {hint && (
        <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
          {hint}
        </p>
      )}
    </div>
  );
}

/**
 * The chrome every entity editor shares: a title, Save/Cancel, and the error a
 * refused write comes back with.
 *
 * The stores answer a refusal — an inherited playbook, a missing required field
 * — as a 400 carrying their own message, so this surfaces the server's sentence
 * rather than inventing one.
 */
export function EditorShell({
  title,
  canSave,
  isPending,
  error,
  onSave,
  onCancel,
  children,
}: {
  title: string;
  canSave: boolean;
  isPending: boolean;
  error: unknown;
  onSave: () => void;
  onCancel: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          {title}
        </h3>
        <div className="flex items-center gap-2">
          <button
            onClick={onCancel}
            className="flex items-center gap-1 rounded px-2 py-1.5 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <X className="h-3 w-3" /> Cancel
          </button>
          <button
            onClick={onSave}
            disabled={!canSave || isPending}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
          >
            <Save className="h-3.5 w-3.5" />
            {isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
      {error != null && (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Save failed"}
        </div>
      )}
      {children}
    </div>
  );
}

/** The body textarea shared by the skill and memory editors. */
export function BodyArea({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      className="min-h-[320px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 font-mono text-xs leading-relaxed text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]/50"
    />
  );
}
