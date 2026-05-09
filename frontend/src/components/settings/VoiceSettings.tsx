import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Mic, Volume2 } from "lucide-react";
import { useState, useEffect } from "react";

import { api } from "@/lib/api";
import type { VoicePrefs } from "@/lib/api";

export function VoiceSettings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["voice-settings"],
    queryFn: () => api.getVoiceSettings(),
  });

  const [form, setForm] = useState<VoicePrefs>({
    whisper_model: "base",
    language: null,
    auto_send: true,
  });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data?.voice) {
      setForm(data.voice);
    }
  }, [data]);

  const mutation = useMutation({
    mutationFn: (prefs: Partial<VoicePrefs>) => api.updateVoiceSettings(prefs),
    onSuccess: (res) => {
      qc.setQueryData(["voice-settings"], (old: typeof data) =>
        old ? { ...old, voice: res.voice } : old,
      );
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const handleSave = () => {
    mutation.mutate(form);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  const models = data?.available_models ?? {};
  const languages = data?.available_languages ?? {};

  return (
    <div className="space-y-6">
      {/* Whisper Model */}
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">
          <Volume2 className="mr-1.5 inline h-4 w-4" />
          Whisper Model
        </h3>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Larger models are more accurate but slower to transcribe.
        </p>
        <div className="space-y-1">
          {Object.entries(models).map(([key, label]) => (
            <label
              key={key}
              className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors ${
                form.whisper_model === key
                  ? "border-[var(--color-primary)]/50 bg-[var(--color-primary)]/5"
                  : "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <input
                type="radio"
                name="whisper_model"
                value={key}
                checked={form.whisper_model === key}
                onChange={() => setForm({ ...form, whisper_model: key })}
                className="accent-[var(--color-primary)]"
              />
              <span className="text-sm text-[var(--color-text)]">{label}</span>
            </label>
          ))}
        </div>
      </section>

      {/* Language */}
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">
          <Mic className="mr-1.5 inline h-4 w-4" />
          Language
        </h3>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Auto-detect works well for most cases. Fix the language for better accuracy if you always speak the same language.
        </p>
        <select
          value={form.language ?? ""}
          onChange={(e) =>
            setForm({ ...form, language: e.target.value || null })
          }
          className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        >
          {Object.entries(languages).map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
      </section>

      {/* Auto-send */}
      <section>
        <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-[var(--color-border)] px-3 py-3 transition-colors hover:bg-[var(--color-surface-hover)]">
          <input
            type="checkbox"
            checked={form.auto_send}
            onChange={(e) => setForm({ ...form, auto_send: e.target.checked })}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          <div>
            <span className="text-sm font-medium text-[var(--color-text)]">
              Auto-send after transcription
            </span>
            <p className="text-xs text-[var(--color-text-muted)]">
              Send the message immediately after voice transcription completes. When off, the transcribed text is placed in the input for editing.
            </p>
          </div>
        </label>
      </section>

      {/* Shortcut hint */}
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          <span className="font-medium text-[var(--color-text)]">Keyboard shortcut:</span>{" "}
          Press <kbd className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 text-[10px] font-mono border border-[var(--color-border)]">⌘M</kbd> to start/stop voice recording from anywhere in the chat.
        </p>
      </section>

      {/* Save button */}
      <button
        onClick={handleSave}
        disabled={mutation.isPending}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {mutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : saved ? (
          <>
            <Check className="h-4 w-4" />
            Saved
          </>
        ) : (
          "Save Voice Settings"
        )}
      </button>

      {mutation.isError && (
        <p className="text-xs text-[var(--color-red)]">
          Failed to save: {(mutation.error as Error).message}
        </p>
      )}
    </div>
  );
}
