import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Code2,
  Copy,
  FileText,
  FolderClosed,
  FolderOpen,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  SplitSquareHorizontal,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import yaml from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import {
  CloneConfigDialog,
  DeleteConfirmDialog,
  NewConfigDialog,
  UploadDialog,
} from "@/components/editor/EditorDialogs";
import { useServer } from "@/hooks/useServer";
import { api, type ControllerConfigSummary } from "@/lib/api";

// ── Types ──

type FileKind = "controller" | "config";
type ExplorerView = "controllers" | "configs";

interface FileEntry {
  kind: FileKind;
  id: string;
  label: string;
  group: string; // controller_type for controllers, controller_name for configs
  controllerType?: string;
  controllerName?: string;
  configId?: string;
  configSummary?: ControllerConfigSummary;
}

interface OpenTab {
  file: FileEntry;
  content: string;
  originalContent: string;
  language: "python" | "yaml";
  readOnly: boolean;
  loaded: boolean;
  error?: string;
}

// ── Context Menu ──

interface ContextMenuState {
  x: number;
  y: number;
  file: FileEntry;
}

function ContextMenu({
  state,
  onClose,
  onDelete,
  onClone,
  onNewConfig,
}: {
  state: ContextMenuState;
  onClose: () => void;
  onDelete: (file: FileEntry) => void;
  onClone: (file: FileEntry) => void;
  onNewConfig: (file: FileEntry) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="fixed z-50 min-w-[160px] rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl py-1 text-xs"
      style={{ top: state.y, left: state.x }}
    >
      {state.file.kind === "config" && (
        <button
          onClick={() => { onClone(state.file); onClose(); }}
          className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-hover)] text-[var(--color-text)]"
        >
          <Copy className="h-3 w-3" />
          Clone
        </button>
      )}
      {state.file.kind === "controller" && (
        <button
          onClick={() => { onNewConfig(state.file); onClose(); }}
          className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-hover)] text-[var(--color-text)]"
        >
          <Plus className="h-3 w-3" />
          New Config
        </button>
      )}
      <button
        onClick={() => { onDelete(state.file); onClose(); }}
        className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-[var(--color-red)]/10 text-[var(--color-red)]"
      >
        <Trash2 className="h-3 w-3" />
        Delete
      </button>
    </div>
  );
}

// ── File Tree ──

function FileTree({
  files,
  activeFileId,
  onSelect,
  onContextMenu,
}: {
  files: FileEntry[];
  activeFileId: string | null;
  onSelect: (file: FileEntry) => void;
  onContextMenu: (e: React.MouseEvent, file: FileEntry) => void;
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [searchTerm, setSearchTerm] = useState("");

  const grouped = useMemo(() => {
    const q = searchTerm.toLowerCase();
    const filtered = q
      ? files.filter((f) => f.label.toLowerCase().includes(q) || f.group.toLowerCase().includes(q))
      : files;

    const sections = new Map<string, FileEntry[]>();
    for (const f of filtered) {
      let list = sections.get(f.group);
      if (!list) {
        list = [];
        sections.set(f.group, list);
      }
      list.push(f);
    }
    return sections;
  }, [files, searchTerm]);

  const toggle = (key: string) =>
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));

  const icon = files[0]?.kind === "controller"
    ? <Code2 className="h-3 w-3 text-blue-400 shrink-0" />
    : <FileText className="h-3 w-3 text-yellow-400 shrink-0" />;

  return (
    <div className="flex flex-col h-full">
      <div className="px-2 pt-2 pb-1">
        <input
          type="text"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Search files..."
          className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none focus:border-[var(--color-primary)]"
        />
      </div>

      <div className="flex-1 overflow-y-auto px-1 py-1 text-xs select-none">
        {Array.from(grouped.entries()).map(([group, items]) => {
          const isCollapsed = collapsed[group];
          return (
            <div key={group} className="mb-1">
              <button
                onClick={() => toggle(group)}
                className="flex items-center gap-1 w-full px-1 py-0.5 rounded hover:bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] font-medium uppercase tracking-wider"
                style={{ fontSize: "10px" }}
              >
                {isCollapsed ? (
                  <FolderClosed className="h-3 w-3 shrink-0" />
                ) : (
                  <FolderOpen className="h-3 w-3 shrink-0" />
                )}
                {group}
                <span className="ml-auto opacity-60">{items.length}</span>
              </button>

              {!isCollapsed &&
                items.map((f) => (
                  <button
                    key={f.id}
                    onClick={() => onSelect(f)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      onContextMenu(e, f);
                    }}
                    className={`flex items-center gap-1.5 w-full px-2 py-0.5 ml-3 rounded truncate transition-colors ${
                      activeFileId === f.id
                        ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                        : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                    }`}
                  >
                    {icon}
                    <span className="truncate" style={{ fontSize: "11px" }}>
                      {f.label}
                      {f.kind === "config" ? ".yml" : ".py"}
                    </span>
                  </button>
                ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Tab Bar ──

function EditorTabBar({
  tabs,
  activeTabId,
  onSelect,
  onClose,
}: {
  tabs: OpenTab[];
  activeTabId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-0 border-b border-[var(--color-border)] bg-[var(--color-surface)] overflow-x-auto shrink-0">
      {tabs.map((tab) => {
        const isActive = tab.file.id === activeTabId;
        const isDirty = tab.content !== tab.originalContent;
        return (
          <div
            key={tab.file.id}
            className={`group flex items-center gap-1.5 px-3 py-1.5 border-r border-[var(--color-border)]/50 cursor-pointer transition-colors shrink-0 ${
              isActive
                ? "bg-[var(--color-bg)] text-[var(--color-text)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg)]/50"
            }`}
            onClick={() => onSelect(tab.file.id)}
          >
            {tab.file.kind === "controller" ? (
              <Code2 className="h-3 w-3 text-blue-400 shrink-0" />
            ) : (
              <FileText className="h-3 w-3 text-yellow-400 shrink-0" />
            )}
            <span className="text-xs truncate max-w-[140px]">
              {tab.file.label}
              {tab.file.kind === "config" ? ".yml" : ".py"}
            </span>
            {isDirty && (
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)] shrink-0" />
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onClose(tab.file.id);
              }}
              className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-[var(--color-surface-hover)] transition-opacity shrink-0"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ── Editor Pane ──

function EditorPane({
  tab,
  server,
  onContentChange,
  onSaved,
  onDelete,
}: {
  tab: OpenTab;
  server: string;
  onContentChange: (id: string, content: string) => void;
  onSaved: (id: string, newOriginal: string) => void;
  onDelete: (file: FileEntry) => void;
}) {
  const queryClient = useQueryClient();
  const isDirty = tab.content !== tab.originalContent;
  const [yamlError, setYamlError] = useState<string | null>(null);

  const handleChange = useCallback(
    (val: string) => {
      onContentChange(tab.file.id, val);
      if (tab.language === "yaml") {
        try {
          const parsed = yaml.load(val);
          if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            setYamlError("YAML must be a mapping (key: value)");
          } else {
            setYamlError(null);
          }
        } catch (e) {
          setYamlError(e instanceof Error ? e.message : "Invalid YAML");
        }
      }
    },
    [tab.file.id, tab.language, onContentChange],
  );

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (tab.file.kind === "config" && tab.file.configId) {
        await api.updateConfigYaml(server, tab.file.configId, tab.content);
      } else if (tab.file.kind === "controller" && tab.file.controllerType && tab.file.controllerName) {
        await api.updateControllerSource(server, tab.file.controllerType, tab.file.controllerName, tab.content);
      }
    },
    onSuccess: () => {
      onSaved(tab.file.id, tab.content);
      if (tab.file.kind === "config") {
        queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
        queryClient.invalidateQueries({ queryKey: ["config-detail", server, tab.file.configId] });
      }
    },
  });

  const handleSave = () => {
    if (isDirty && !yamlError) saveMutation.mutate();
  };

  const handleReset = () => {
    onContentChange(tab.file.id, tab.originalContent);
    setYamlError(null);
  };

  const containerRef = useRef<HTMLDivElement>(null);

  if (!tab.loaded) {
    return (
      <div className="flex items-center justify-center h-full text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (tab.error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-4 py-3 text-sm text-[var(--color-red)]">
          {tab.error}
        </div>
      </div>
    );
  }

  const canSave = isDirty && !yamlError && !tab.readOnly;

  return (
    <div
      ref={containerRef}
      className="flex flex-col h-full"
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "s") {
          e.preventDefault();
          handleSave();
        }
      }}
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)]/50 bg-[var(--color-surface)]/50 shrink-0">
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <span className="font-mono">
            {tab.file.group}/{tab.file.label}
            {tab.file.kind === "config" ? ".yml" : ".py"}
          </span>
          {tab.readOnly && (
            <span className="rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] uppercase tracking-wider">
              read-only
            </span>
          )}
          {isDirty && (
            <span className="text-[var(--color-warning)] text-[10px] uppercase tracking-wider font-medium">
              modified
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {yamlError && (
            <span className="flex items-center gap-1 text-xs text-[var(--color-red)]">
              <AlertTriangle className="h-3 w-3" />
              YAML error
            </span>
          )}
          {saveMutation.isError && (
            <span className="text-xs text-[var(--color-red)]">
              {saveMutation.error instanceof Error ? saveMutation.error.message : "Save failed"}
            </span>
          )}
          <button
            onClick={() => onDelete(tab.file)}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-red)] hover:bg-[var(--color-red)]/10 transition-colors"
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
          {isDirty && !tab.readOnly && (
            <button
              onClick={handleReset}
              className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              <RotateCcw className="h-3 w-3" />
              Reset
            </button>
          )}
          {!tab.readOnly && (
            <button
              onClick={handleSave}
              disabled={!canSave || saveMutation.isPending}
              className="flex items-center gap-1 rounded bg-[var(--color-primary)] px-2.5 py-1 text-xs font-medium text-white transition-opacity disabled:opacity-40"
            >
              {saveMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : saveMutation.isSuccess && !isDirty ? (
                <Check className="h-3 w-3" />
              ) : (
                <Save className="h-3 w-3" />
              )}
              {saveMutation.isPending ? "Saving..." : saveMutation.isSuccess && !isDirty ? "Saved" : "Save"}
            </button>
          )}
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 min-h-0">
        <CodeEditor
          value={tab.content}
          onChange={tab.readOnly ? undefined : handleChange}
          language={tab.language}
          readOnly={tab.readOnly}
          height="100%"
        />
      </div>
    </div>
  );
}

// ── Main Export ──

export function EditorTab() {
  const { server } = useServer();
  const queryClient = useQueryClient();
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [splitMode, setSplitMode] = useState(false);
  const [splitTabId, setSplitTabId] = useState<string | null>(null);
  const [explorerView, setExplorerView] = useState<ExplorerView>("controllers");

  // Dialog state
  const [deleteTarget, setDeleteTarget] = useState<FileEntry | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [cloneTarget, setCloneTarget] = useState<ControllerConfigSummary | null>(null);
  const [newConfigTarget, setNewConfigTarget] = useState<{ type?: string; name?: string } | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  // Fetch available controllers + configs
  const { data, isLoading } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server!),
    enabled: !!server,
  });

  const controllerTypes = data?.controller_types ?? {};

  // Build file entries for the active view
  const controllerFiles = useMemo<FileEntry[]>(() => {
    if (!data) return [];
    const entries: FileEntry[] = [];
    for (const [type, names] of Object.entries(data.controller_types ?? {})) {
      for (const name of names) {
        entries.push({
          kind: "controller",
          id: `ctrl:${type}:${name}`,
          label: name,
          group: type,
          controllerType: type,
          controllerName: name,
        });
      }
    }
    return entries;
  }, [data]);

  const configFiles = useMemo<FileEntry[]>(() => {
    if (!data) return [];
    return (data.configs ?? []).map((cfg) => ({
      kind: "config" as const,
      id: `cfg:${cfg.id}`,
      label: cfg.id,
      group: cfg.controller_name || "other",
      configId: cfg.id,
      configSummary: cfg,
    }));
  }, [data]);

  const visibleFiles = explorerView === "controllers" ? controllerFiles : configFiles;

  // Open a file tab (or activate existing)
  const openFile = useCallback(
    (file: FileEntry) => {
      setOpenTabs((prev) => {
        const existing = prev.find((t) => t.file.id === file.id);
        if (existing) {
          setActiveTabId(file.id);
          return prev;
        }
        const newTab: OpenTab = {
          file,
          content: "",
          originalContent: "",
          language: file.kind === "controller" ? "python" : "yaml",
          readOnly: false,
          loaded: false,
        };
        setActiveTabId(file.id);
        return [...prev, newTab];
      });
    },
    [],
  );

  // Close a tab
  const closeTab = useCallback(
    (id: string) => {
      setOpenTabs((prev) => {
        const idx = prev.findIndex((t) => t.file.id === id);
        const next = prev.filter((t) => t.file.id !== id);
        if (activeTabId === id) {
          const newIdx = Math.min(idx, next.length - 1);
          setActiveTabId(next[newIdx]?.file.id ?? null);
        }
        if (splitTabId === id) {
          setSplitTabId(null);
          setSplitMode(false);
        }
        return next;
      });
    },
    [activeTabId, splitTabId],
  );

  const updateContent = useCallback((id: string, content: string) => {
    setOpenTabs((prev) =>
      prev.map((t) => (t.file.id === id ? { ...t, content } : t)),
    );
  }, []);

  const markSaved = useCallback((id: string, newOriginal: string) => {
    setOpenTabs((prev) =>
      prev.map((t) =>
        t.file.id === id ? { ...t, originalContent: newOriginal, content: newOriginal } : t,
      ),
    );
  }, []);

  const handleDeleteRequest = useCallback((file: FileEntry) => {
    setDeleteTarget(file);
  }, []);

  const handleDeleted = useCallback(() => {
    if (deleteTarget) {
      closeTab(deleteTarget.id);
    }
    queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
  }, [deleteTarget, closeTab, queryClient, server]);

  const handleContextMenu = useCallback((e: React.MouseEvent, file: FileEntry) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, file });
  }, []);

  const handleCloneFromMenu = useCallback((file: FileEntry) => {
    if (file.kind === "config" && file.configSummary) {
      setCloneTarget(file.configSummary);
    }
  }, []);

  const handleNewConfigFromMenu = useCallback((file: FileEntry) => {
    if (file.kind === "controller" && file.controllerType && file.controllerName) {
      setNewConfigTarget({ type: file.controllerType, name: file.controllerName });
    }
  }, []);

  const activeTab = openTabs.find((t) => t.file.id === activeTabId);
  const splitTab = splitMode ? openTabs.find((t) => t.file.id === splitTabId) : null;
  const tabsToLoad = openTabs.filter((t) => !t.loaded && !t.error);

  if (!server) {
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin rounded-full" />
      </div>
    );
  }

  const hasDirtyTabs = openTabs.some((t) => t.content !== t.originalContent);

  return (
    <div className="flex rounded-lg border border-[var(--color-border)] overflow-hidden" style={{ height: "calc(100vh - 180px)" }}>
      {/* Sidebar */}
      <div className="w-56 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden flex flex-col">
        {/* Header with view toggle + actions */}
        <div className="px-2 py-2 border-b border-[var(--color-border)]/50 space-y-1.5">
          <div className="flex items-center justify-between px-1">
            <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider flex items-center gap-1">
              Explorer
              {hasDirtyTabs && (
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)]" />
              )}
            </span>
            <div className="flex items-center gap-0.5">
              <button
                onClick={() => setShowUpload(true)}
                className="p-1 rounded hover:bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                title="Upload file"
              >
                <Upload className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => setNewConfigTarget({})}
                className="p-1 rounded hover:bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                title="New config from template"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          {/* View toggle */}
          <div className="flex items-center gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
            <button
              onClick={() => setExplorerView("controllers")}
              className={`flex items-center gap-1 flex-1 justify-center rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                explorerView === "controllers"
                  ? "bg-[var(--color-surface)] text-[var(--color-text)] shadow-sm"
                  : "text-[var(--color-text-muted)]"
              }`}
            >
              <Code2 className="h-3 w-3" />
              Controllers
            </button>
            <button
              onClick={() => setExplorerView("configs")}
              className={`flex items-center gap-1 flex-1 justify-center rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                explorerView === "configs"
                  ? "bg-[var(--color-surface)] text-[var(--color-text)] shadow-sm"
                  : "text-[var(--color-text-muted)]"
              }`}
            >
              <FileText className="h-3 w-3" />
              Configs
            </button>
          </div>
        </div>
        <FileTree
          files={visibleFiles}
          activeFileId={activeTabId}
          onSelect={openFile}
          onContextMenu={handleContextMenu}
        />
      </div>

      {/* Editor area */}
      <div className="flex-1 flex flex-col min-w-0 bg-[var(--color-bg)]">
        {openTabs.length > 0 ? (
          <>
            <div className="flex items-center shrink-0">
              <div className="flex-1 min-w-0">
                <EditorTabBar
                  tabs={openTabs}
                  activeTabId={activeTabId}
                  onSelect={setActiveTabId}
                  onClose={closeTab}
                />
              </div>
              {openTabs.length >= 2 && (
                <button
                  onClick={() => {
                    if (splitMode) {
                      setSplitMode(false);
                      setSplitTabId(null);
                    } else {
                      const other = openTabs.find((t) => t.file.id !== activeTabId);
                      if (other) {
                        setSplitMode(true);
                        setSplitTabId(other.file.id);
                      }
                    }
                  }}
                  className={`px-2 py-1.5 border-b border-l border-[var(--color-border)] transition-colors ${
                    splitMode
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  }`}
                  title="Toggle split view"
                >
                  <SplitSquareHorizontal className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            <div className={`flex-1 min-h-0 ${splitMode ? "flex" : ""}`}>
              {activeTab && (
                <div className={splitMode ? "flex-1 border-r border-[var(--color-border)]" : "h-full"}>
                  <EditorPane
                    tab={activeTab}
                    server={server}
                    onContentChange={updateContent}
                    onSaved={markSaved}
                    onDelete={handleDeleteRequest}
                  />
                </div>
              )}
              {splitMode && splitTab && (
                <div className="flex-1">
                  <EditorPane
                    tab={splitTab}
                    server={server}
                    onContentChange={updateContent}
                    onSaved={markSaved}
                    onDelete={handleDeleteRequest}
                  />
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-[var(--color-text-muted)]">
            <div className="text-center space-y-2">
              <Code2 className="h-10 w-10 mx-auto opacity-40" />
              <p className="text-sm">Select a file from the explorer</p>
              <p className="text-xs opacity-60">
                Controllers (.py) and Configs (.yml)
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Hidden content loaders */}
      {tabsToLoad.map((tab) => (
        <FileContentLoader
          key={tab.file.id}
          tab={tab}
          server={server}
          onLoaded={(content, readOnly) => {
            setOpenTabs((prev) =>
              prev.map((t) =>
                t.file.id === tab.file.id
                  ? { ...t, content, originalContent: content, loaded: true, readOnly }
                  : t,
              ),
            );
          }}
          onError={(error) => {
            setOpenTabs((prev) =>
              prev.map((t) =>
                t.file.id === tab.file.id ? { ...t, loaded: true, error } : t,
              ),
            );
          }}
        />
      ))}

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          state={contextMenu}
          onClose={() => setContextMenu(null)}
          onDelete={handleDeleteRequest}
          onClone={handleCloneFromMenu}
          onNewConfig={handleNewConfigFromMenu}
        />
      )}

      {/* Dialogs */}
      {deleteTarget && (
        <DeleteConfirmDialog
          server={server}
          target={
            deleteTarget.kind === "config"
              ? { kind: "config", configId: deleteTarget.configId! }
              : { kind: "controller", controllerType: deleteTarget.controllerType!, controllerName: deleteTarget.controllerName! }
          }
          onClose={() => setDeleteTarget(null)}
          onDeleted={handleDeleted}
        />
      )}
      {showUpload && (
        <UploadDialog
          server={server}
          controllerTypes={controllerTypes}
          onClose={() => setShowUpload(false)}
        />
      )}
      {cloneTarget && (
        <CloneConfigDialog
          server={server}
          sourceConfig={cloneTarget}
          onClose={() => setCloneTarget(null)}
        />
      )}
      {newConfigTarget && (
        <NewConfigDialog
          server={server}
          controllerTypes={controllerTypes}
          initialControllerType={newConfigTarget.type}
          initialControllerName={newConfigTarget.name}
          onClose={() => setNewConfigTarget(null)}
        />
      )}
    </div>
  );
}

// ── Content Loader ──

function FileContentLoader({
  tab,
  server,
  onLoaded,
  onError,
}: {
  tab: OpenTab;
  server: string;
  onLoaded: (content: string, readOnly: boolean) => void;
  onError: (error: string) => void;
}) {
  const loadedRef = useRef(false);

  const controllerQuery = useQuery({
    queryKey: ["controller-source", server, tab.file.controllerType, tab.file.controllerName],
    queryFn: () => api.getControllerSource(server, tab.file.controllerType!, tab.file.controllerName!),
    enabled: tab.file.kind === "controller" && !tab.loaded && !loadedRef.current,
  });

  const configQuery = useQuery({
    queryKey: ["config-detail", server, tab.file.configId],
    queryFn: () => api.getConfigDetail(server, tab.file.configId!),
    enabled: tab.file.kind === "config" && !tab.loaded && !loadedRef.current,
  });

  if (tab.file.kind === "controller" && controllerQuery.data && !loadedRef.current) {
    loadedRef.current = true;
    onLoaded(controllerQuery.data.source ?? "", false);
  }
  if (tab.file.kind === "controller" && controllerQuery.isError && !loadedRef.current) {
    loadedRef.current = true;
    onError(controllerQuery.error instanceof Error ? controllerQuery.error.message : "Failed to load");
  }

  if (tab.file.kind === "config" && configQuery.data && !loadedRef.current) {
    loadedRef.current = true;
    const filtered = Object.fromEntries(
      Object.entries(configQuery.data.config).filter(([k]) => k !== "id"),
    );
    const dumped = yaml.dump(filtered, { sortKeys: false, lineWidth: -1 });
    onLoaded(dumped, false);
  }
  if (tab.file.kind === "config" && configQuery.isError && !loadedRef.current) {
    loadedRef.current = true;
    onError(configQuery.error instanceof Error ? configQuery.error.message : "Failed to load");
  }

  return null;
}
