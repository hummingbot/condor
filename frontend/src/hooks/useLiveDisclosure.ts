import { useCallback, useState } from "react";

/**
 * A disclosure that opens itself while its content is being produced.
 *
 * Thinking and tool-call blocks are worth reading exactly while they are
 * growing — that is the only moment the bubble has nothing else in it — and
 * stop being the interesting part the instant the answer lands. So `expanded`
 * tracks `live` on its own rather than starting collapsed and waiting for a
 * click on a block that is moving under the cursor.
 *
 * The auto behaviour lasts only until the user disagrees with it. Once
 * `toggle()` has been called the block holds that choice for the rest of its
 * life: a block collapsed mid-stream stays collapsed as the next chunks
 * arrive, and one opened after the fact to re-read the reasoning is not
 * snapped shut again. Without that latch the automation would be fighting the
 * user on their very next chunk.
 */
export function useLiveDisclosure(live: boolean): {
  expanded: boolean;
  toggle: () => void;
} {
  // `null` is "the user has not weighed in", which is what lets the default
  // keep following `live`. Any boolean here is an explicit decision and wins
  // from then on.
  const [choice, setChoice] = useState<boolean | null>(null);
  const expanded = choice ?? live;
  const toggle = useCallback(() => setChoice(!expanded), [expanded]);
  return { expanded, toggle };
}
