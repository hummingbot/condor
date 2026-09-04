import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import {
  BodyArea,
  EditorShell,
  Field,
} from "@/components/agent/knowledge/KnowledgeChrome";
import { api, type MemoryCard } from "@/lib/api";

/** The taxonomy `MemoryStore` validates against — anything else falls back to `fact`. */
const MEMORY_TYPES = ["preference", "fact", "feedback", "reference"] as const;

/**
 * A memory, written by hand.
 *
 * Scoped to you and this agent, the same as the ones it writes itself: what you
 * put here reaches its prompt exactly the way `manage_memory` would have.
 */
export function MemoryEditor({
  slug,
  existing,
  body: initialBody,
  onDirtyChange,
  onDone,
  onCancel,
}: {
  slug: string;
  existing?: MemoryCard;
  body?: string;
  onDirtyChange: (dirty: boolean) => void;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [type, setType] = useState(existing?.type ?? "fact");
  const [body, setBody] = useState(initialBody ?? "");

  const touch =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      onDirtyChange(true);
    };

  const save = useMutation({
    mutationFn: () =>
      api.saveAgentMemory(slug, existing?.name || name, {
        content: body,
        description,
        type,
      }),
    onSuccess: onDone,
  });

  const canSave = !!(
    (existing?.name || name.trim()) &&
    description.trim() &&
    body.trim()
  );

  return (
    <EditorShell
      title={existing ? `Editing ${existing.name}` : "New memory"}
      canSave={canSave}
      isPending={save.isPending}
      error={save.error}
      onSave={() => save.mutate()}
      onCancel={onCancel}
    >
      {!existing && (
        <Field
          label="Name"
          value={name}
          onChange={touch(setName)}
          placeholder="e.g. favourite-pair"
          hint="Slugified — writing an existing name overwrites that memory."
          mono
        />
      )}
      <Field
        label="Description"
        value={description}
        onChange={touch(setDescription)}
        placeholder="One line — this is what the agent scans to decide it is relevant"
      />
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Type
        </label>
        <select
          value={type}
          onChange={(e) => touch(setType)(e.target.value)}
          className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
        >
          {MEMORY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Body
        </label>
        <BodyArea
          value={body}
          onChange={touch(setBody)}
          placeholder="The fact itself."
        />
      </div>
    </EditorShell>
  );
}
