import { Loader2 } from "lucide-react";

export function FallbackSpinner() {
  return (
    <div className="flex items-center justify-center py-20">
      <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
    </div>
  );
}
