import { useQuery } from "@tanstack/react-query";
import { useRef, useState, useEffect, useCallback } from "react";
import { Loader2, Mic, Paperclip, Send, Square, X } from "lucide-react";

import { api } from "@/lib/api";
import { authFetch } from "@/lib/auth-token";

/** What the composer will accept, and what the store will keep. Kept in step
 *  with `condor/runtime/attachments.py` — the backend refuses anything else. */
const ACCEPT = "image/png,image/jpeg,image/gif,image/webp";
const MAX_FILES = 4;
const MAX_BYTES = 5 * 1024 * 1024;

/** A file the user has handed over but not sent yet.
 *
 *  It holds the browser's own `File` and a local object URL: the preview costs
 *  no round trip, because the bytes are already here. The upload happens at send
 *  time (FEAT-098), so a file picked and then removed never touches the disk. */
interface PendingFile {
  key: string;
  file: File;
  url: string;
}

let pendingKey = 0;

interface ChatInputProps {
  /**
   * `files` is present only when the user attached something. It is optional so
   * the call sites that can only ever send text — the starters, the bubble's
   * `ask`, the hero — are untouched by this existing.
   */
  onSend: (text: string, files?: File[]) => void;
  disabled?: boolean;
  isStreaming?: boolean;
  onAbort?: () => void;
  /** Take the caret on mount — the workspace lands the user in a composer. */
  autoFocus?: boolean;
  /** Who the user is writing to. Defaults to Condor, the chat assistant. */
  placeholder?: string;
  /**
   * Controls for *this* conversation, at the left edge of the box, before the
   * field. The composer is the one piece of chrome that unambiguously belongs
   * to the chat on screen, so an action on the chat itself — sharing it — reads
   * here and nowhere else; the bar above the transcript is about which session
   * you are in, not what to do with it.
   *
   * Rendered inside the box rather than beside it so it sits on the same
   * baseline as the mic and Send. Nothing is rendered when the surface has
   * nothing to put here (the hero's composer, the bubble's), and the box is
   * then exactly what it was.
   */
  leading?: React.ReactNode;
}

type RecordingState = "idle" | "recording" | "transcribing";

export function ChatInput({
  onSend,
  disabled,
  isStreaming,
  onAbort,
  autoFocus,
  placeholder = "Ask Condor...",
  leading,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // The box around the textarea is the affordance, so it has to know when the
  // textarea has the caret. `:focus-within` would do it without state, but the
  // recording and transcribing branches replace the textarea entirely.
  const [focused, setFocused] = useState(false);

  // Held as `File`s, not as ids: nothing is uploaded until the message is
  // actually sent, so there is no orphan for a picked-then-removed file.
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Voice preferences (auto_send default true)
  const { data: voiceSettings } = useQuery({
    queryKey: ["voice-settings"],
    queryFn: () => api.getVoiceSettings(),
    staleTime: 5 * 60 * 1000,
  });

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 120) + "px";
    }
  }, [value]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Every object URL this composer minted, revoked when it goes away. Kept in a
  // ref rather than in the effect's deps so removing one chip does not revoke
  // the URLs of the chips beside it.
  const filesRef = useRef<PendingFile[]>([]);
  filesRef.current = files;
  useEffect(() => {
    return () => {
      filesRef.current.forEach((f) => URL.revokeObjectURL(f.url));
    };
  }, []);

  /**
   * Take whatever was pasted, dropped or picked.
   *
   * The two limits are checked here as well as on the server. Not because the
   * server's check is in doubt — it is the one that counts — but because a chip
   * that never appears explains itself, while an upload that 413s explains
   * itself only after the user has pressed Send.
   */
  const acceptFiles = useCallback((incoming: FileList | File[] | null) => {
    const images = Array.from(incoming || []).filter((f) =>
      ACCEPT.includes(f.type),
    );
    if (images.length === 0) return;

    const tooBig = images.filter((f) => f.size > MAX_BYTES);
    const usable = images.filter((f) => f.size <= MAX_BYTES);

    setFiles((prev) => {
      const room = MAX_FILES - prev.length;
      if (room <= 0) {
        setFileError(`At most ${MAX_FILES} images per message.`);
        return prev;
      }
      setFileError(
        tooBig.length
          ? `${tooBig.length === 1 ? "That image is" : "Those images are"} over 5 MB.`
          : usable.length > room
            ? `At most ${MAX_FILES} images per message.`
            : null,
      );
      return [
        ...prev,
        ...usable.slice(0, room).map((file) => ({
          key: `att_${++pendingKey}`,
          file,
          url: URL.createObjectURL(file),
        })),
      ];
    });
  }, []);

  const removeFile = useCallback((key: string) => {
    setFileError(null);
    setFiles((prev) => {
      prev
        .filter((f) => f.key === key)
        .forEach((f) => URL.revokeObjectURL(f.url));
      return prev.filter((f) => f.key !== key);
    });
  }, []);

  const handleSubmit = () => {
    const trimmed = value.trim();
    // An image with no words is a complete message — the backend stopped
    // refusing one, and this is the other half of that.
    if ((!trimmed && files.length === 0) || disabled) return;
    onSend(trimmed, files.length ? files.map((f) => f.file) : undefined);
    setValue("");
    // The object URLs are handed to the transcript's optimistic bubble, which
    // renders them for the life of the session, so they are dropped here rather
    // than revoked: revoking would blank the picture the user just sent.
    setFiles([]);
    setFileError(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    if (e.key === "Escape" && isStreaming && onAbort) {
      e.preventDefault();
      onAbort();
    }
  };

  const startRecording = useCallback(async () => {
    setVoiceError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Prefer webm/opus, fallback to whatever is available
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "audio/mp4";

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        // Stop all tracks
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;

        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }

        const blob = new Blob(chunksRef.current, { type: mimeType });
        chunksRef.current = [];

        if (blob.size === 0) {
          setRecordingState("idle");
          return;
        }

        // Transcribe
        setRecordingState("transcribing");
        try {
          const text = await transcribeAudio(blob);
          if (text) {
            // Check auto_send preference from the latest query data
            const latestSettings = voiceSettingsRef.current;
            const shouldAutoSend = latestSettings?.voice?.auto_send ?? true;

            if (shouldAutoSend) {
              // Send immediately
              onSend(text);
            } else {
              // Append to textarea for editing
              setValue((prev) => (prev ? `${prev} ${text}` : text));
              setTimeout(() => textareaRef.current?.focus(), 50);
            }
          }
        } catch (err) {
          console.error("Transcription failed:", err);
          setVoiceError(
            err instanceof Error ? err.message : "Transcription failed",
          );
        }
        setRecordingState("idle");
        setRecordingDuration(0);
      };

      recorder.start(250); // collect data every 250ms
      setRecordingState("recording");
      setRecordingDuration(0);

      timerRef.current = setInterval(() => {
        setRecordingDuration((d) => d + 1);
      }, 1000);
    } catch (err) {
      console.error("Microphone access denied:", err);
      setVoiceError("Microphone access denied");
      setRecordingState("idle");
    }
  }, [onSend]);

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
  }, []);

  // Keep a ref to voice settings so the onstop callback can read latest value
  const voiceSettingsRef = useRef(voiceSettings);
  useEffect(() => {
    voiceSettingsRef.current = voiceSettings;
  }, [voiceSettings]);

  // Global ESC to abort streaming
  useEffect(() => {
    if (!isStreaming || !onAbort) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onAbort();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isStreaming, onAbort]);

  // Global keyboard shortcut: ⌘⇧M to toggle recording
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "m") {
        e.preventDefault();
        if (recordingState === "recording") {
          stopRecording();
        } else if (recordingState === "idle") {
          startRecording();
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [recordingState, startRecording, stopRecording]);

  const formatDuration = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const isRecording = recordingState === "recording";
  const isTranscribing = recordingState === "transcribing";

  return (
    // The composer is a deck the transcript sits on, not a card floating over
    // it: a hairline the width of the column, its own surface below it, and the
    // field recessed into that surface. Only the ring is gold, and only on
    // focus.
    <div className="border-t border-[var(--chat-rule)] bg-[var(--color-surface)] p-3">
      {voiceError && <p className="mb-2 text-xs text-red-400">{voiceError}</p>}
      {fileError && <p className="mb-2 text-xs text-red-400">{fileError}</p>}
      {/* One composer chrome, owned here — the hero and the thread both get this
          box, so they cannot drift into two shapes again.

          It is also the drop target, and the focus ring is reused as the
          affordance: a second highlight for dragging would be a second thing to
          keep in step with the first. */}
      <div
        onDragOver={(e) => {
          if (disabled) return;
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(e) => {
          // Only when the pointer leaves the box itself — moving between the
          // chips and the textarea fires this for every child otherwise.
          if (e.currentTarget.contains(e.relatedTarget as Node)) return;
          setDragging(false);
        }}
        onDrop={(e) => {
          if (disabled) return;
          e.preventDefault();
          setDragging(false);
          acceptFiles(e.dataTransfer?.files ?? null);
        }}
        data-testid="composer-box"
        className={`flex flex-col gap-1.5 rounded-xl border bg-[var(--chat-inset)] px-2 py-1.5 transition-colors ${
          focused || dragging
            ? "border-[var(--color-primary)]/40 ring-1 ring-[var(--color-primary)]/20"
            : "border-[var(--color-border)]"
        }`}
      >
        {/* Inside the box, above the field, so the composer stays the one piece
            of chrome it already is rather than growing a tray beside it. */}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1 pt-1">
            {files.map((f) => (
              <div
                key={f.key}
                className="group/chip relative h-14 w-14 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]"
              >
                <img
                  src={f.url}
                  alt={f.file.name}
                  title={f.file.name}
                  className="h-full w-full object-cover"
                />
                <button
                  onClick={() => removeFile(f.key)}
                  aria-label={`Remove ${f.file.name}`}
                  className="absolute right-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover/chip:opacity-100 focus:opacity-100"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2">
          {leading}

          {isRecording ? (
            // Recording UI
            <div className="flex flex-1 items-center gap-3 rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2">
              <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
              <span className="text-sm font-medium text-red-400">
                {formatDuration(recordingDuration)}
              </span>
              <span className="flex-1 text-xs text-[var(--color-text-muted)]">
                Recording...{" "}
                <kbd className="ml-1 rounded bg-[var(--color-bg)] px-1 py-0.5 text-[10px] font-mono border border-[var(--color-border)]">
                  ⌘M
                </kbd>{" "}
                to stop
              </span>
            </div>
          ) : isTranscribing ? (
            // Transcribing UI
            <div className="flex flex-1 items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2">
              <Loader2 className="h-4 w-4 animate-spin text-[var(--color-primary)]" />
              <span className="text-sm text-[var(--color-text-muted)]">
                Transcribing audio...
              </span>
            </div>
          ) : (
            // Normal text input
            <textarea
              ref={textareaRef}
              autoFocus={autoFocus}
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                if (voiceError) setVoiceError(null);
              }}
              onKeyDown={handleKeyDown}
              // A screenshot arrives on the clipboard as a file, so ⌘V and the
              // picker are the same code path and there is only one to get right.
              onPaste={(e) => {
                const pasted = e.clipboardData?.files;
                if (pasted && pasted.length > 0) {
                  e.preventDefault();
                  acceptFiles(pasted);
                }
              }}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder={placeholder}
              disabled={disabled}
              rows={1}
              className="flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none disabled:opacity-50"
            />
          )}

          {/* The picker, beside the mic: both are ways of saying something the
            keyboard cannot. Hidden input rather than a styled one — the button
            is the affordance and the input is plumbing. */}
          {!isRecording && !isTranscribing && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT}
                multiple
                hidden
                data-testid="attach-input"
                onChange={(e) => {
                  acceptFiles(e.target.files);
                  // Cleared so picking the same file twice in a row still fires.
                  e.target.value = "";
                }}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={disabled}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-40"
                title="Attach an image"
                aria-label="Attach an image"
              >
                <Paperclip className="h-4 w-4" />
              </button>
            </>
          )}

          {/* Mic / Stop button */}
          {isRecording ? (
            <button
              onClick={stopRecording}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-red-500 text-white transition-opacity hover:opacity-90"
              title="Stop recording (⌘M)"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          ) : isTranscribing ? (
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-surface-hover)] opacity-50">
              <Mic className="h-4 w-4 text-[var(--color-text-muted)]" />
            </div>
          ) : (
            <button
              onClick={startRecording}
              disabled={disabled}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-40"
              title="Record voice message (⌘M)"
            >
              <Mic className="h-4 w-4" />
            </button>
          )}

          {/* Stop — only while an answer is in flight. It stays even though the
            composer is now live, because stopping without redirecting is still
            a thing users want (and Esc does the same). */}
          {isStreaming && (
            <button
              onClick={onAbort}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-red-500 text-white transition-opacity hover:opacity-90"
              title="Stop generation (Esc)"
              aria-label="Stop generation"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          )}

          {/* Send — enabled mid-answer, because that is the whole feature. The
            tooltip says what it will do before the user finds out: sending
            discards the answer in flight and redirects the same session. */}
          <button
            onClick={handleSubmit}
            disabled={
              disabled ||
              (!value.trim() && files.length === 0) ||
              isRecording ||
              isTranscribing
            }
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-primary)] text-[var(--on-primary)] transition-opacity hover:opacity-90 disabled:opacity-40"
            title={
              isStreaming
                ? "Send — interrupts the current answer"
                : "Send message"
            }
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

async function transcribeAudio(blob: Blob): Promise<string> {
  const formData = new FormData();
  formData.append("file", blob, "recording.webm");

  const res = await authFetch("/api/v1/transcribe", {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Transcription failed: ${res.status}`);
  }

  const data = await res.json();
  return data.text || "";
}
