import { useQuery } from "@tanstack/react-query";
import { useRef, useState, useEffect, useCallback } from "react";
import { Loader2, Mic, Send, Square } from "lucide-react";

import { api } from "@/lib/api";

const TOKEN_KEY = "condor_token";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

type RecordingState = "idle" | "recording" | "transcribing";

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [recordingDuration, setRecordingDuration] = useState(0);
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

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const startRecording = useCallback(async () => {
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

  // Global keyboard shortcut: ⌘M to toggle recording
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "m") {
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
    <div className="flex items-end gap-2 border-t border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      {isRecording ? (
        // Recording UI
        <div className="flex flex-1 items-center gap-3 rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2">
          <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
          <span className="text-sm font-medium text-red-400">
            {formatDuration(recordingDuration)}
          </span>
          <span className="flex-1 text-xs text-[var(--color-text-muted)]">
            Recording... <kbd className="ml-1 rounded bg-[var(--color-bg)] px-1 py-0.5 text-[10px] font-mono border border-[var(--color-border)]">⌘M</kbd> to stop
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
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Condor..."
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-50"
        />
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
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--color-border)] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] disabled:opacity-40"
          title="Record voice message (⌘M)"
        >
          <Mic className="h-4 w-4" />
        </button>
      )}

      {/* Send button */}
      <button
        onClick={handleSubmit}
        disabled={disabled || !value.trim() || isRecording || isTranscribing}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-primary)] text-white transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        <Send className="h-4 w-4" />
      </button>
    </div>
  );
}

async function transcribeAudio(blob: Blob): Promise<string> {
  const token = localStorage.getItem(TOKEN_KEY);
  const formData = new FormData();
  formData.append("file", blob, "recording.webm");

  const res = await fetch("/api/v1/transcribe", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Transcription failed: ${res.status}`);
  }

  const data = await res.json();
  return data.text || "";
}
