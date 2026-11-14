/**
 * The vocabulary of the workspace layout picker (`LayoutPicker`), split out so
 * the page that drives the layout and the picker that draws it share one
 * definition.
 */

/** Chat only, chat + 1, chat + 2 side by side, chat + 2 one above the other. */
export type LayoutChoice = "chat" | "one" | "side" | "stack";
/** What can fill the wide pane (and, stacked, either half). */
export type MainSlot = "agent" | "portfolio" | "execution" | "loops";
/** What can fill the side column. */
export type SideSlot = "tasks" | "routines";
/** Anything the rail opens — what either half of a stacked layout may hold. */
export type SlotId = MainSlot | SideSlot;

/** Tasks and Routines: the panels that live in the side column. */
export function isSide(id: SlotId): id is SideSlot {
  return id === "tasks" || id === "routines";
}
