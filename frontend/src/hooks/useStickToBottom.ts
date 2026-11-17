import { useCallback, useEffect, useRef, useState } from "react";

/** How close to the end still counts as "following the answer", in pixels. */
export const NEAR_BOTTOM_PX = 80;

/** The three numbers a scroll container says about itself. */
export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** Is the viewport at, or within reach of, the end of the content? */
export function isNearBottom(
  m: ScrollMetrics,
  threshold: number = NEAR_BOTTOM_PX,
): boolean {
  return m.scrollHeight - m.scrollTop - m.clientHeight <= threshold;
}

/**
 * Is the reader still following, after one `scroll` event?
 *
 * Decided here, from the scroll itself, rather than by measuring the distance
 * to the end after new content has been laid out. That later measurement
 * cannot tell "the user scrolled away" from "the answer just grew by a table":
 * anything taller than the threshold read a bottom-pinned reader as gone, and
 * following stopped mid-answer.
 *
 * - Near the end: following, however the viewport got there.
 * - Away from the end and the viewport moved *up*: the reader left.
 * - Away from the end otherwise — content grew, or a scroll down that has not
 *   arrived yet (a smooth scroll in flight): no change. Growth is not a
 *   decision the reader made.
 */
export function nextPinned(
  pinned: boolean,
  prevScrollTop: number,
  m: ScrollMetrics,
  threshold: number = NEAR_BOTTOM_PX,
): boolean {
  if (isNearBottom(m, threshold)) return true;
  if (m.scrollTop < prevScrollTop) return false;
  return pinned;
}

/**
 * What a change to the transcript does to the viewport.
 *
 * - `jump`: a different conversation — open at its end, without animating
 *   through everything above it.
 * - `own`: the user just said something — always show it.
 * - `follow`: anything else. Scrolls only for a reader who is following.
 *
 * Deliberately *not* "the message count moved": a routine's outcome, a
 * delegation's note and a notification all change the count mid-answer, and
 * forcing on that pulled a reader who had scrolled back down to the end.
 */
export function scrollIntent(change: {
  slotChanged: boolean;
  ownMessage: boolean;
}): "jump" | "own" | "follow" {
  if (change.slotChanged) return "jump";
  if (change.ownMessage) return "own";
  return "follow";
}

/**
 * Where the reader is relative to the end: `pinned` (following), `away`
 * (scrolled back, nothing new since) or `behind` (scrolled back and the
 * transcript has grown underneath).
 */
export type StickStatus = "pinned" | "away" | "behind";

/**
 * Keep a transcript's viewport on its end for a reader who is following it,
 * and leave it alone for one who is not (FU-07).
 *
 * Attach `scrollRef` to the scrolling element and `contentRef` to the column
 * inside it. `content` is whatever changes when the transcript does (the
 * messages array); the content column is also watched with a
 * `ResizeObserver`, because a chart or an authed image finishing its load
 * grows the transcript without touching the messages at all.
 */
export function useStickToBottom({
  slotId,
  ownMessageId,
  content,
}: {
  /** The conversation on screen; a change opens the new one at its end. */
  slotId: string | undefined;
  /** The id of the transcript's last message when the user wrote it. */
  ownMessageId: string | undefined;
  content: unknown;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const lastTopRef = useRef(0);
  const lastHeightRef = useRef(0);
  const frameRef = useRef<number | null>(null);
  const [status, setStatus] = useState<StickStatus>("pinned");

  /**
   * Bring the viewport in line with the content, at most once per animation
   * frame: scroll to the end for a reader who is following, or note that the
   * transcript grew underneath one who is not.
   *
   * Streaming replaces the messages array on every chunk, so an ungated
   * scroll runs once per token and forces a synchronous layout of the whole
   * transcript each time, which is what makes a long answer stutter. Holding
   * a single frame handle collapses a burst of chunks into the one scroll
   * that matters.
   */
  const sync = useCallback((behavior: ScrollBehavior) => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const el = scrollRef.current;
      if (!el) return;
      const grew = el.scrollHeight > lastHeightRef.current;
      lastHeightRef.current = el.scrollHeight;
      if (!pinnedRef.current) {
        if (grew) setStatus("behind");
        return;
      }
      // jsdom has no `Element.scrollTo`; the assignment is the same scroll.
      if (typeof el.scrollTo === "function") {
        el.scrollTo({ top: el.scrollHeight, behavior });
      } else {
        el.scrollTop = el.scrollHeight;
      }
      // Where this scroll left the viewport is the baseline the reader's next
      // move is judged against — the `scroll` event for it arrives later.
      lastTopRef.current = el.scrollTop;
      setStatus("pinned");
    });
  }, []);

  /** The content changed: follow it, if the reader is following. */
  const follow = useCallback(() => sync("auto"), [sync]);

  // Pinned state, from the reader's own scrolling.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const pinned = nextPinned(pinnedRef.current, lastTopRef.current, el);
      lastTopRef.current = el.scrollTop;
      if (pinned === pinnedRef.current) return;
      pinnedRef.current = pinned;
      lastHeightRef.current = el.scrollHeight;
      setStatus(pinned ? "pinned" : "away");
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Height changes that never touch the messages — an image or a chart
  // finishing — still move the end of the transcript.
  useEffect(() => {
    const el = contentRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => follow());
    observer.observe(el);
    return () => observer.disconnect();
  }, [follow]);

  // Seeded empty rather than with the current values, so the first run after
  // mount counts as a slot change: a transcript that is already on screen when
  // this mounts still opens at its end.
  const prevSlotIdRef = useRef<string | undefined>(undefined);
  const prevOwnIdRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    const intent = scrollIntent({
      slotChanged: prevSlotIdRef.current !== slotId,
      ownMessage: !!ownMessageId && prevOwnIdRef.current !== ownMessageId,
    });
    prevSlotIdRef.current = slotId;
    prevOwnIdRef.current = ownMessageId;
    if (intent === "follow") {
      follow();
      return;
    }
    pinnedRef.current = true;
    sync(intent === "jump" ? "auto" : "smooth");
  }, [slotId, ownMessageId, content, follow, sync]);

  useEffect(() => {
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
  }, []);

  /** The pill's gesture: back to the end, and following again. */
  const jumpToLatest = useCallback(() => {
    pinnedRef.current = true;
    sync("smooth");
  }, [sync]);

  return { scrollRef, contentRef, status, jumpToLatest };
}
