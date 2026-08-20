/**
 * Copy text to the clipboard, working in both secure and insecure contexts.
 *
 * `navigator.clipboard` only exists in secure contexts (https/localhost); the
 * self-hosted dashboard is often served over plain http on a LAN/VPS IP, where
 * it is undefined. In that case fall back to a hidden textarea +
 * `document.execCommand("copy")`.
 *
 * Resolves `true` when the text was copied, `false` on failure — never throws.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission denied or transient failure — try the legacy path below.
    }
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}
