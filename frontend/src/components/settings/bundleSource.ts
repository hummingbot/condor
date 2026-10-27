/**
 * Where a bundle comes from, as the calls the review dialog makes.
 *
 * Its own module rather than a pair of exports on `ImportAgentDialog`, for a
 * reason the lint gate states better than prose could: a file that exports
 * both a component and a plain function loses React Fast Refresh for that
 * component (`react-refresh/only-export-components`). The split is the right
 * shape anyway — a source is a transport, and the dialog is a screen.
 */

import {
  api,
  type AgentImportPlan,
  type AgentUpdatePlan,
} from "@/lib/api";

/**
 * Which verb a preview turned out to be.
 *
 * The server decides, never a client comparing two uids: an `install` carries
 * FEAT-133's import plan and an `update` FEAT-145's diff, and the dialog puts
 * each into the state that renders it.
 */
export type BundlePreview =
  | { verb: "install"; plan: AgentImportPlan }
  | { verb: "update"; plan: AgentUpdatePlan };

/**
 * Where a bundle comes from, as the four calls this dialog makes.
 *
 * It exists so the hub (FEAT-146) reuses this dialog rather than growing a
 * second review screen. That is not tidiness: the review *is* the sandbox, so
 * two screens would be two chances to omit the executables list, the findings
 * or the muted-on-arrival sentence — and the one that got it wrong would be
 * the one nobody opened. A source is the only thing that differs between a
 * file on disk and a listing on the hub; everything below this line is shared
 * byte for byte.
 */
export interface BundleSource {
  /** Identity of this source. A change re-previews from scratch. */
  key: string;
  preview(asSlug: string): Promise<BundlePreview>;
  /** `agentKey`: the model to run it on when its own can't run here. */
  install(asSlug: string, agentKey?: string): Promise<AgentImportPlan>;
  previewUpdate(slug: string): Promise<AgentUpdatePlan>;
  applyUpdate(slug: string): Promise<AgentUpdatePlan>;
}

/** A bundle the operator picked off their own disk (FEAT-133). */
export function fileSource(file: File): BundleSource {
  return {
    key: `file:${file.name}:${file.size}:${file.lastModified}`,
    preview: async (asSlug) => ({
      verb: "install",
      plan: await api.previewAgentImport(file, asSlug),
    }),
    install: (asSlug, agentKey) => api.importAgent(file, asSlug, agentKey),
    previewUpdate: (slug) => api.previewAgentUpdate(slug, file),
    applyUpdate: (slug) => api.updateAgentFromBundle(slug, file),
  };
}
