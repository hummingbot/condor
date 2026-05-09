import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Edit2,
  Loader2,
  Plus,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";

import { type ServerInfo, api } from "@/lib/api";

interface ServerForm {
  name: string;
  host: string;
  port: number;
  username: string;
  password: string;
}

const EMPTY_FORM: ServerForm = { name: "", host: "", port: 443, username: "", password: "" };

export function ServersSettings() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<ServerForm>(EMPTY_FORM);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const { data: servers = [], isLoading } = useQuery({
    queryKey: ["settings-servers"],
    queryFn: api.getSettingsServers,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["settings-servers"] });
    qc.invalidateQueries({ queryKey: ["servers"] });
  };

  const addMut = useMutation({
    mutationFn: (data: ServerForm) => api.addServer(data),
    onSuccess: () => { invalidate(); setAdding(false); setForm(EMPTY_FORM); },
  });

  const updateMut = useMutation({
    mutationFn: ({ name, ...data }: ServerForm) => api.updateServer(name, data),
    onSuccess: () => { invalidate(); setEditing(null); setForm(EMPTY_FORM); },
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => api.deleteServer(name),
    onSuccess: () => { invalidate(); setConfirmDelete(null); },
  });

  const defaultMut = useMutation({
    mutationFn: (name: string) => api.setDefaultServer(name),
    onSuccess: invalidate,
  });

  const startEdit = (s: ServerInfo) => {
    setEditing(s.name);
    setForm({ name: s.name, host: s.host, port: s.port, username: "", password: "" });
  };

  const submitForm = () => {
    if (editing) {
      updateMut.mutate(form);
    } else {
      addMut.mutate(form);
    }
  };

  const isOwner = (s: ServerInfo) => s.permission === "owner";

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-text-muted)]">
          {servers.length} server{servers.length !== 1 ? "s" : ""} configured
        </p>
        {!adding && (
          <button
            onClick={() => { setAdding(true); setEditing(null); setForm(EMPTY_FORM); }}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80"
          >
            <Plus className="h-3.5 w-3.5" /> Add Server
          </button>
        )}
      </div>

      {/* Add / Edit form */}
      {(adding || editing) && (
        <div className="rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-sm font-semibold text-[var(--color-text)]">
            {editing ? `Edit ${editing}` : "Add Server"}
          </h3>
          <div className="grid grid-cols-2 gap-3">
            {!editing && (
              <div className="col-span-2">
                <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Name</label>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                  placeholder="my-server"
                />
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Host</label>
              <input
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                placeholder="192.168.1.100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Port</label>
              <input
                type="number"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 0 })}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Username</label>
              <input
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                placeholder="admin"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Password</label>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                placeholder={editing ? "(unchanged)" : ""}
              />
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={submitForm}
              disabled={addMut.isPending || updateMut.isPending || (!editing && !form.name)}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              {(addMut.isPending || updateMut.isPending) && <Loader2 className="h-3 w-3 animate-spin" />}
              {editing ? "Save" : "Add"}
            </button>
            <button
              onClick={() => { setAdding(false); setEditing(null); setForm(EMPTY_FORM); }}
              className="rounded-md px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            >
              Cancel
            </button>
          </div>
          {(addMut.error || updateMut.error) && (
            <p className="mt-2 text-xs text-[var(--color-red)]">
              {(addMut.error || updateMut.error)?.message}
            </p>
          )}
        </div>
      )}

      {/* Server list */}
      <div className="space-y-2">
        {servers.map((s) => (
          <div
            key={s.name}
            className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 transition-colors hover:border-[var(--color-border-hover)]"
          >
            <div className="flex items-center gap-3">
              <span
                className={`h-2.5 w-2.5 rounded-full ${
                  s.online
                    ? "bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]"
                    : "bg-[var(--color-text-muted)]/40"
                }`}
                title={s.online ? "Online" : "Offline"}
              />
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-[var(--color-text)]">{s.name}</span>
                  <span className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                    {s.permission}
                  </span>
                </div>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {s.host}:{s.port}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-1">
              <button
                onClick={() => defaultMut.mutate(s.name)}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-amber-400"
                title="Set as default"
              >
                <Star className="h-3.5 w-3.5" />
              </button>
              {isOwner(s) && (
                <>
                  <button
                    onClick={() => startEdit(s)}
                    className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
                    title="Edit"
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  {confirmDelete === s.name ? (
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => deleteMut.mutate(s.name)}
                        className="rounded p-1.5 text-[var(--color-red)] hover:bg-red-500/10"
                        title="Confirm delete"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={() => setConfirmDelete(null)}
                        className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmDelete(s.name)}
                      className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
                      title="Delete"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        ))}

        {servers.length === 0 && (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            No servers configured. Add one to get started.
          </p>
        )}
      </div>
    </div>
  );
}
