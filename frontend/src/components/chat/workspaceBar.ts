/**
 * The one bar across the top of the workspace.
 *
 * Four columns can be on screen at once — the rail, the conversation, the pane
 * a sheet opens in, and the dock — and each of them used to size its own top
 * bar from whatever it happened to put in it: a 32 px strip beside a 46 px tab
 * row beside a 40 px sheet header beside a 38 px dock header. Four borders at
 * four heights read as a broken window rather than as one bar, and the seam is
 * visible in every screenshot the moment two columns sit side by side.
 *
 * So the height is a decision made once, here, and every column tops itself
 * with it. 40 px is what the tallest thing in any of them needs — a session
 * tab with its underline, or an icon button at `p-1` around a 16 px glyph —
 * and nothing in these bars ever wants more, because anything that does
 * belongs in the body below it.
 *
 * Horizontal padding is deliberately *not* here: a sheet at full screen and a
 * 300 px dock column want different insets, and the seam nobody can see is the
 * one that does not matter. Only the height and the surface are shared.
 */
export const WORKSPACE_BAR =
  "flex h-10 shrink-0 items-center border-b border-[var(--color-border)] bg-[var(--color-surface)]";

/** The bar's height on its own, for anything that has to reserve the space. */
export const WORKSPACE_BAR_H = "h-10";
