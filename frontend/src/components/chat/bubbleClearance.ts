/**
 * Right padding for a form footer that sits in the page's bottom-right corner.
 *
 * The quick-chat bubble is fixed at `bottom-4 right-4` and 48px wide, which is
 * exactly where the Trade and DEX pool forms put their submit button; without
 * this the bubble hides the right end of "Place order". 4.5rem clears the
 * bubble (16px inset + 48px) with a small gap.
 */
export const CHAT_BUBBLE_CLEARANCE = "pr-[4.5rem]";
