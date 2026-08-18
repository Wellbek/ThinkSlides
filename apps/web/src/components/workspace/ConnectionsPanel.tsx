"use client";

/**
 * The nav rail's Connections panel: what is wired up, and whether it is working.
 *
 * Three states have to be distinguishable at a glance, because they need
 * different actions from the user. Enabled and healthy needs nothing. Disabled
 * is a choice and must not read as a fault. Degraded is the one that wants
 * attention, and it carries the reason - "one connection is degraded" is not
 * something anybody can act on, but "the server refused the credential" is.
 *
 * A connection whose catalogue entry has gone is still listed. A row nobody can
 * see is a row nobody can delete.
 */

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Loader2,
  Plug,
  Plus,
  Power,
  Trash2,
} from "lucide-react";
import {
  useConnectionsStore,
  type McpPreset,
  type StoredConnection,
} from "@/stores/connections-store";

const KIND_LABELS: Record<string, string> = {
  mcp: "MCP server",
  image: "Images",
  figure: "Figures",
  data: "Data",
};

export function ConnectionsPanel() {
  const {
    catalog,
    connections,
    isLoading,
    loadError,
    editing,
    load,
    startAdding,
    startEditing,
    setEnabled,
    remove,
  } = useConnectionsStore();
  const activeCount = useConnectionsStore((s) => s.activeCount());
  const degradedCount = useConnectionsStore((s) => s.degradedCount());
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    load();
  }, [load]);

  // Only the kinds stored here can be added from this panel. Tavily and
  // Firecrawl are listed by the catalogue for one coherent view, but they are
  // configured in Settings, where they have always lived.
  const addable = (catalog?.connections ?? []).filter(
    (spec) => spec.storage === "connections" && spec.available,
  );

  return (
    <div className="ws-nav-panel">
      <div className="ws-nav-panel-head">
        <span className="ws-conn-count">
          <Plug size={12} />
          {activeCount} {activeCount === 1 ? "connection" : "connections"}{" "}
          active
        </span>
      </div>

      {degradedCount > 0 && (
        <div className="ws-conn-warn">
          <AlertTriangle size={12} />
          {degradedCount === 1
            ? "1 connection unreachable"
            : `${degradedCount} connections unreachable`}
        </div>
      )}

      <div className="ws-conn-list">
        {isLoading && connections.length === 0 && (
          <div className="ws-empty-note">
            <Loader2 size={12} className="ws-spin" /> Loading connections...
          </div>
        )}
        {loadError && <div className="ws-conn-error">{loadError}</div>}
        {!isLoading && !loadError && connections.length === 0 && (
          <div className="ws-empty-note">
            Nothing connected yet. Add an MCP server or an image provider to
            give a deck more to draw on.
          </div>
        )}

        {connections.map((connection) => (
          <ConnectionRow
            key={connection.id}
            connection={connection}
            onEdit={() => startEditing(connection.id)}
            onToggle={() => setEnabled(connection.id, !connection.enabled)}
            onRemove={() => remove(connection.id)}
          />
        ))}

        {addable.length > 0 && (
          <div className="ws-conn-add">
            <button
              className="ws-new-chat"
              onClick={() => setAdding((v) => !v)}
            >
              <Plus size={13} /> Add connection
            </button>
            {adding && (
              <div className="ws-conn-choices">
                {addable.map((spec) => (
                  <button
                    key={spec.id}
                    className="ws-conn-choice"
                    onClick={() => {
                      startAdding(spec.id);
                      setAdding(false);
                    }}
                    title={spec.description}
                  >
                    <span className="ws-conn-choice-kind">
                      {KIND_LABELS[spec.kind] ?? spec.kind}
                    </span>
                    {spec.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {editing && <ConnectionEditor />}
    </div>
  );
}

function ConnectionRow({
  connection,
  onEdit,
  onToggle,
  onRemove,
}: {
  connection: StoredConnection;
  onEdit: () => void;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const { status, lastError, lastCheckedAt, toolCount } = connection.health;

  return (
    <div className={`ws-conn-row ${status}`}>
      <div
        className="ws-conn-row-main"
        onClick={onEdit}
        title={connection.label}
      >
        <span className={`ws-conn-dot ${status}`} />
        <span className="ws-conn-label">{connection.label}</span>
        <span className="ws-conn-kind">
          {KIND_LABELS[connection.kind ?? ""] ?? connection.kind ?? "unknown"}
        </span>
      </div>
      <div className="ws-conn-row-meta">
        <span className="ws-conn-status">
          {statusLine(status, toolCount, lastCheckedAt)}
        </span>
        <div className="ws-conn-actions">
          <button
            className="ws-conn-action"
            onClick={onToggle}
            title={connection.enabled ? "Disable" : "Enable"}
          >
            <Power size={11} />
          </button>
          <button
            className="ws-conn-action danger"
            onClick={onRemove}
            title="Remove"
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>
      {status === "degraded" && lastError && (
        <div className="ws-conn-reason">{lastError}</div>
      )}
      {!connection.known && (
        <div className="ws-conn-reason">
          This connection type is no longer offered. Remove it, or upgrade the
          service.
        </div>
      )}
    </div>
  );
}

function statusLine(
  status: string,
  toolCount: number | null,
  lastCheckedAt: string | null,
): string {
  if (status === "disabled") return "Disabled";
  if (status === "unknown") return "Not used yet";
  const when = lastCheckedAt ? relative(lastCheckedAt) : "";
  if (status === "degraded")
    return when ? `Unreachable ${when}` : "Unreachable";
  const tools =
    toolCount === null ? "" : `${toolCount} tool${toolCount === 1 ? "" : "s"}`;
  return [tools, when].filter(Boolean).join(" · ");
}

function relative(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * The form. Rendered from the catalogue's own field list rather than from a
 * hard-coded shape, so a connection added to the catalogue is configurable here
 * without a second change.
 */
function ConnectionEditor() {
  const {
    catalog,
    connections,
    draft,
    editing,
    isSaving,
    saveError,
    updateDraft,
    save,
    cancelEditing,
  } = useConnectionsStore();
  if (!draft) return null;

  const spec = catalog?.connections.find((c) => c.id === draft.connectionId);
  if (!spec) return null;

  // A saved credential can never be read back, so an empty password box is
  // ambiguous: it looks the same whether one is stored or not. The last-four
  // hint in the placeholder is what says "there is one, leave this alone" -
  // the same thing the settings form does, for the same reason.
  const stored = connections.find((c) => c.id === editing);

  const presets = spec.kind === "mcp" ? (catalog?.mcpPresets ?? []) : [];

  return (
    <div className="ws-conn-editor">
      <div className="ws-conn-editor-head">
        <span>{spec.label}</span>
        <button
          className="ws-conn-action"
          onClick={cancelEditing}
          title="Close"
        >
          <ChevronDown size={12} />
        </button>
      </div>

      <div className="ws-conn-editor-body">
        <label className="ws-conn-field">
          <span>Name</span>
          <input
            value={draft.label}
            placeholder={spec.label}
            onChange={(e) => updateDraft({ label: e.target.value })}
          />
        </label>

        {presets.length > 0 && (
          <label className="ws-conn-field">
            <span>Vetted server</span>
            <select
              value={String(draft.config.preset ?? "")}
              onChange={(e) =>
                updateDraft({
                  config: { ...draft.config, preset: e.target.value },
                })
              }
            >
              <option value="">Custom endpoint</option>
              {presets.map((preset) => (
                <option
                  key={preset.id}
                  value={preset.id}
                  disabled={!preset.available}
                >
                  {preset.label}
                  {preset.available ? "" : " (needs sign-in we cannot do yet)"}
                </option>
              ))}
            </select>
            {presetNote(presets, String(draft.config.preset ?? ""))}
          </label>
        )}

        {spec.fields.map((field) => {
          // The endpoint comes from the catalogue when a preset is chosen, so
          // asking for it as well would be asking for something that is ignored.
          if (field.name === "url" && draft.config.preset) return null;
          return (
            <label key={field.name} className="ws-conn-field">
              <span>
                {field.label}
                {!field.required && <em> optional</em>}
              </span>
              {field.options.length > 0 ? (
                <select
                  value={String(draft.config[field.name] ?? "")}
                  onChange={(e) =>
                    updateDraft({
                      config: { ...draft.config, [field.name]: e.target.value },
                    })
                  }
                >
                  <option value="">Default</option>
                  {field.options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={field.secret ? "password" : "text"}
                  value={
                    field.secret
                      ? (draft.secrets[field.name] ?? "")
                      : String(draft.config[field.name] ?? "")
                  }
                  placeholder={
                    field.secret
                      ? (hintPlaceholder(stored?.secrets[field.name]) ??
                        field.placeholder)
                      : field.placeholder
                  }
                  onChange={(e) =>
                    field.secret
                      ? updateDraft({
                          secrets: {
                            ...draft.secrets,
                            [field.name]: e.target.value,
                          },
                        })
                      : updateDraft({
                          config: {
                            ...draft.config,
                            [field.name]: e.target.value,
                          },
                        })
                  }
                />
              )}
              {field.help && <small>{field.help}</small>}
            </label>
          );
        })}

        {saveError && <div className="ws-conn-error">{saveError}</div>}
      </div>

      <div className="ws-conn-editor-foot">
        <button className="ws-conn-cancel" onClick={cancelEditing}>
          Cancel
        </button>
        <button className="ws-conn-save" onClick={save} disabled={isSaving}>
          {isSaving ? (
            <Loader2 size={12} className="ws-spin" />
          ) : (
            <Check size={12} />
          )}
          Save
        </button>
      </div>
    </div>
  );
}

/** "Saved, ending 1234" - present only when there is a stored credential. */
function hintPlaceholder(
  secret: { hasValue: boolean; hint: string | null } | undefined,
): string | null {
  if (!secret?.hasValue) return null;
  const tail = (secret.hint ?? "").replace(/^\*+/, "");
  return tail
    ? `Saved, ending ${tail}. Leave blank to keep it.`
    : "Saved. Leave blank to keep it.";
}

/** The chosen preset's own guidance: which token to make, or why it cannot be used. */
function presetNote(presets: McpPreset[], chosen: string) {
  const preset = presets.find((p) => p.id === chosen);
  if (!preset) return null;
  const note = preset.available ? preset.tokenHelp : preset.unavailableReason;
  return note ? <small>{note}</small> : null;
}
