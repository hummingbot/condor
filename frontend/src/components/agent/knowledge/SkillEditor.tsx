import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import {
  BodyArea,
  EditorShell,
  Field,
} from "@/components/agent/knowledge/KnowledgeChrome";
import { api, type SkillBody } from "@/lib/api";

/**
 * A playbook, written.
 *
 * One form for both doors: with no `existing` it creates in the agent's own
 * library, with one it patches that playbook in place. The `shared` flag is
 * absent by design — publishing to every assistant is Condor's own decision,
 * not a checkbox on a panel.
 */
export function SkillEditor({
  slug,
  existing,
  onDirtyChange,
  onDone,
  onCancel,
}: {
  slug: string;
  existing?: SkillBody;
  onDirtyChange: (dirty: boolean) => void;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [whenToUse, setWhenToUse] = useState(existing?.when_to_use ?? "");
  const [routine, setRoutine] = useState(existing?.references_routine ?? "");
  const [body, setBody] = useState(existing?.body ?? "");

  const touch =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      onDirtyChange(true);
    };

  const save = useMutation({
    // The two calls answer with different shapes and neither is read — the
    // panel refetches the catalog instead.
    mutationFn: (): Promise<unknown> =>
      existing
        ? api.updateAgentSkill(slug, existing.slug, {
            description,
            when_to_use: whenToUse,
            body,
            // Only sent when it actually moved: "" is a request to unlink, and
            // sending it unchanged on every save would be one too.
            ...(routine !== (existing.references_routine ?? "")
              ? { references_routine: routine }
              : {}),
          })
        : api.createAgentSkill(slug, {
            name,
            description,
            when_to_use: whenToUse,
            body,
            references_routine: routine,
          }),
    onSuccess: onDone,
  });

  // The store requires all four on create; an edit may touch one field.
  const canSave = existing
    ? true
    : !!(name.trim() && description.trim() && whenToUse.trim() && body.trim());

  return (
    <EditorShell
      title={existing ? `Editing ${existing.name}` : "New playbook"}
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
          placeholder="e.g. Rebalance an LP position"
          hint="Slugified into the library — this becomes the playbook's id."
          mono
        />
      )}
      <Field
        label="Description"
        value={description}
        onChange={touch(setDescription)}
        placeholder="What this playbook does, in one line"
      />
      <Field
        label="When to use"
        value={whenToUse}
        onChange={touch(setWhenToUse)}
        placeholder="The situation that should make the agent reach for it"
        hint="This line is what the agent sees in its index — it decides whether the playbook gets read at all."
      />
      <Field
        label="Runs routine"
        value={routine}
        onChange={touch(setRoutine)}
        placeholder="optional — a routine name"
        hint="Named here, the agent runs that routine instead of improvising the flow. Empty unlinks it."
        mono
      />
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Body
        </label>
        <BodyArea
          value={body}
          onChange={touch(setBody)}
          placeholder="The steps, in markdown — written for the agent to follow."
        />
      </div>
    </EditorShell>
  );
}
