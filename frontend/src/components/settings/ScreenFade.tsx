import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { usePrefersReducedMotion } from "@/hooks/usePrefersReducedMotion";

/**
 * Cross-fades + slides into whatever `children` is, each time `phaseKey`
 * changes, instead of letting the new screen pop in at its own height and
 * yank the modal shell with it.
 *
 * `phaseKey` is a coarse label ("review", "success", ...) rather than the
 * content itself: swapping `children` while `phaseKey` stays the same (the
 * review re-previewing under a new name, say) renders instantly, with no
 * animation — only an actual screen change animates.
 *
 * The height it morphs between is read off the DOM, not computed, because
 * the content is markup the caller owns. Two reads a commit apart: the
 * outer box's real height is recorded on every settled render, and a
 * `phaseKey` change freezes to *that* recorded value before the new
 * content's own height takes over — never to the box's height in the same
 * commit the new content already painted into, which would already be the
 * new height and defeat the freeze.
 */
export function ScreenFade({
  phaseKey,
  children,
}: {
  phaseKey: string;
  children: ReactNode;
}) {
  const reducedMotion = usePrefersReducedMotion();
  const outerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const prevPhase = useRef(phaseKey);
  const lastHeight = useRef<number | null>(null);
  const [animClass, setAnimClass] = useState("");
  const [height, setHeight] = useState<number | "auto">("auto");

  // Declared before the always-run recorder below, so within the commit a
  // transition starts, this still reads the *previous* commit's recorded
  // height rather than one the recorder already overwrote this same commit.
  useLayoutEffect(() => {
    if (reducedMotion || phaseKey === prevPhase.current) {
      prevPhase.current = phaseKey;
      return;
    }
    prevPhase.current = phaseKey;
    if (lastHeight.current) setHeight(lastHeight.current);
    setAnimClass("opacity-0 translate-y-1");
    const raf = requestAnimationFrame(() => {
      const target = innerRef.current?.scrollHeight;
      if (target) setHeight(target);
      setAnimClass("opacity-100 translate-y-0");
    });
    const settle = window.setTimeout(() => setHeight("auto"), 320);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
    };
  }, [phaseKey, reducedMotion]);

  useLayoutEffect(() => {
    const measured = outerRef.current?.getBoundingClientRect().height;
    if (measured) lastHeight.current = measured;
  });

  if (reducedMotion) {
    return <div ref={outerRef}>{children}</div>;
  }

  return (
    <div
      ref={outerRef}
      style={{ height }}
      className="overflow-hidden transition-[height] duration-300 ease-out"
    >
      <div
        ref={innerRef}
        className={`transition-all duration-300 ease-out ${animClass}`}
      >
        {children}
      </div>
    </div>
  );
}
