import { forwardRef, type ButtonHTMLAttributes } from "react";

import { buttonClass, type ButtonSize, type ButtonVariant } from "./buttonClass";

export type { ButtonSize, ButtonVariant };

/**
 * The one button. Every action in the dashboard should come from here so a
 * primary looks like a primary on every page.
 *
 * - `primary`   solid gold: the single main action of a view.
 * - `secondary` outlined surface: alternatives next to a primary.
 * - `soft`      gold tint: secondary emphasis inside panels/toolbars.
 * - `ghost`     text-only: low-emphasis toolbar actions.
 * - `danger`    destructive.
 *
 * Text on gold is `--on-primary`, never white: white on the gold measures
 * 2.22:1 (dark) / 3.26:1 (light), under the 4.5:1 floor (see index.css).
 */
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className = "", type = "button", ...rest },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...rest} />;
});
