import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  Chip,
  EditButton,
} from "@/components/agent/knowledge/KnowledgeChrome";
import { MemoryEditor } from "@/components/agent/knowledge/MemoryEditor";
import { SkillEditor } from "@/components/agent/knowledge/SkillEditor";
import {
  api,
  type MemoryCard,
  type SkillBody,
  type SkillCard,
} from "@/lib/api";

/** What the reader drilled into, if anything — a playbook or a memory. */
export type Reading =
  { kind: "skill"; card: SkillCard } | { kind: "memory"; card: MemoryCard };

/** What `getAgentMemory` returns — a name and the body, nothing else. */
type MemoryBody = { name: string; body: string };

/**
 * One playbook or memory — read in full, or edited in place.
 *
 * Read mode shows the same text the agent gets back from `manage_skill` /
 * `manage_memory`. Edit mode is the same body plus the metadata that decides
 * whether the agent ever reaches for it, which is why the two live at one
 * address rather than in separate places.
 */
export function BodyReader({
  slug,
  reading,
  editing,
  onEdit,
  onDirtyChange,
  onSaved,
  onCancelEdit,
  onBack,
}: {
  slug: string;
  reading: Reading;
  editing: boolean;
  onEdit: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void;
  onCancelEdit: () => void;
  onBack: () => void;
}) {
  const name = reading.kind === "skill" ? reading.card.slug : reading.card.name;

  const { data, isLoading, error } = useQuery<MemoryBody | SkillBody>({
    queryKey: ["agent-brain-body", slug, reading.kind, name],
    queryFn: () =>
      reading.kind === "skill"
        ? api.getAgentSkill(slug, name)
        : api.getAgentMemory(slug, name),
    staleTime: 30_000,
  });

  // The kind is in the query key, so the shape follows it — a memory's payload
  // can never be sitting under a skill's key.
  const skill =
    reading.kind === "skill" ? (data as SkillBody | undefined) : undefined;

  const back = (
    <button
      onClick={onBack}
      className="mb-3 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
    >
      <ArrowLeft className="h-3 w-3" />
      Back to {reading.kind === "skill" ? "skills" : "memories"}
    </button>
  );

  // An editor needs the body, so editing waits for the same fetch reading does.
  if (editing) {
    if (isLoading || !data)
      return (
        <div>
          {back}
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
          </div>
        </div>
      );
    return (
      <div>
        {back}
        {reading.kind === "skill" ? (
          <SkillEditor
            slug={slug}
            existing={data as SkillBody}
            onDirtyChange={onDirtyChange}
            onDone={onSaved}
            onCancel={onCancelEdit}
          />
        ) : (
          <MemoryEditor
            slug={slug}
            existing={reading.card}
            body={data.body}
            onDirtyChange={onDirtyChange}
            onDone={onSaved}
            onCancel={onCancelEdit}
          />
        )}
      </div>
    );
  }

  const writable = reading.kind === "memory" || !reading.card.inherited;

  return (
    <div>
      {back}

      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          {reading.card.name}
        </h3>
        {writable && <EditButton onClick={onEdit} />}
      </div>
      {skill?.when_to_use && (
        <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
          {skill.when_to_use}
        </p>
      )}
      {reading.kind === "skill" && reading.card.inherited && (
        <p className="mt-1 text-[11px] text-amber-400">
          From the shared library — read-only here. Create a playbook with the
          same name to specialize it.
        </p>
      )}
      {skill?.files && skill.files.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {skill.files.map((f) => (
            <Chip key={f} title="Companion file the agent can pull on demand">
              {f}
            </Chip>
          ))}
        </div>
      )}

      <div className="mt-3">
        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
          </div>
        )}
        {error && (
          <p className="text-xs text-[var(--color-red)]">
            {error instanceof Error ? error.message : "Could not read this."}
          </p>
        )}
        {data && (
          <div className="chat-markdown text-xs text-[var(--color-text)]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {data.body}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
