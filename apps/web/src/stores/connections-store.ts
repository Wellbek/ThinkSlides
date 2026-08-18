/**
 * External connections: MCP servers, image and figure renderers, data sources.
 *
 * Credentials are write-only, exactly as in the settings store and for the same
 * reason: the server never returns credential material, only a last-four hint
 * and a boolean. So a field left blank on an edit means "keep what is stored",
 * never "clear it" - the tri-state the connections API defines.
 *
 * The catalogue comes from `/settings/catalog`, which serves both catalogues in
 * one response. That is deliberate: a user configuring a workspace does not care
 * which service module a choice came from, and two endpoints is two chances to
 * be out of sync.
 */

import { create } from 'zustand';
import { apiDelete, apiGet, apiPost, apiPut } from '@/lib/api';

const CONNECTIONS_BASE = '/api/orchestrator/connections';
const CATALOG_URL = '/api/orchestrator/settings/catalog';

export type ConnectionKind = 'mcp' | 'image' | 'figure' | 'data';

/** Never tried, working, reachable-before-but-not-now, or turned off. */
export type HealthStatus = 'unknown' | 'healthy' | 'degraded' | 'disabled';

export interface CredentialField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
  help: string;
  /** A closed set of values, rendered as a choice rather than a text box. */
  options: string[];
}

export interface ConnectionSpec {
  id: string;
  kind: ConnectionKind;
  label: string;
  description: string;
  fields: CredentialField[];
  docsUrl: string;
  unlocks: string;
  storage: string;
  available: boolean;
  unavailableReason: string;
}

export interface McpPreset {
  id: string;
  label: string;
  url: string;
  transport: string;
  auth: 'bearer' | 'oauth';
  description: string;
  docsUrl: string;
  tokenHelp: string;
  available: boolean;
  unavailableReason: string;
}

export interface ConnectionHealth {
  status: HealthStatus;
  lastCheckedAt: string | null;
  lastError: string | null;
  toolCount: number | null;
}

/** A configured connection as the server reports it - never includes secrets. */
export interface StoredConnection {
  id: string;
  connectionId: string;
  kind: ConnectionKind | null;
  slug: string;
  label: string;
  enabled: boolean;
  config: Record<string, unknown>;
  secrets: Record<string, { hasValue: boolean; hint: string | null }>;
  health: ConnectionHealth;
  /** False when the catalogue no longer describes it - shown so it can be removed. */
  known: boolean;
}

export interface ConnectionCatalog {
  connections: ConnectionSpec[];
  connectionKinds: ConnectionKind[];
  mcpPresets: McpPreset[];
}

export interface ConnectionDraft {
  connectionId: string;
  label: string;
  config: Record<string, string>;
  /** Only what the user typed. An absent field keeps the stored credential. */
  secrets: Record<string, string>;
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

interface ConnectionsState {
  catalog: ConnectionCatalog | null;
  connections: StoredConnection[];
  isLoading: boolean;
  isSaving: boolean;
  loadError: string | null;
  saveError: string | null;
  /** The row being edited, or 'new' for one being added, or null. */
  editing: string | null;
  draft: ConnectionDraft | null;

  load: () => Promise<void>;
  refresh: () => Promise<void>;
  startAdding: (connectionId: string) => void;
  startEditing: (id: string) => void;
  cancelEditing: () => void;
  updateDraft: (patch: Partial<ConnectionDraft>) => void;
  save: () => Promise<boolean>;
  setEnabled: (id: string, enabled: boolean) => Promise<void>;
  remove: (id: string) => Promise<void>;

  /** Enabled connections - the number the nav rail shows. */
  activeCount: () => number;
  degradedCount: () => number;
  specFor: (connectionId: string) => ConnectionSpec | null;
}

const EMPTY_DRAFT = (connectionId: string): ConnectionDraft => ({
  connectionId,
  label: '',
  config: {},
  secrets: {},
});

export const useConnectionsStore = create<ConnectionsState>((set, get) => ({
  catalog: null,
  connections: [],
  isLoading: false,
  isSaving: false,
  loadError: null,
  saveError: null,
  editing: null,
  draft: null,

  load: async () => {
    set({ isLoading: true, loadError: null });
    try {
      const [catalog, listing] = await Promise.all([
        apiGet<ConnectionCatalog>(CATALOG_URL),
        apiGet<{ connections: StoredConnection[] }>(CONNECTIONS_BASE),
      ]);
      set({ catalog, connections: listing.connections, isLoading: false });
    } catch (e) {
      set({ loadError: errorMessage(e), isLoading: false });
    }
  },

  refresh: async () => {
    try {
      const listing = await apiGet<{ connections: StoredConnection[] }>(CONNECTIONS_BASE);
      set({ connections: listing.connections });
    } catch {
      // A failed refresh must not break the panel; it keeps showing what it
      // last knew, which is more useful than an empty list.
    }
  },

  startAdding: (connectionId) => set({ editing: 'new', draft: EMPTY_DRAFT(connectionId), saveError: null }),

  startEditing: (id) => {
    const existing = get().connections.find((c) => c.id === id);
    if (!existing) return;
    set({
      editing: id,
      saveError: null,
      draft: {
        connectionId: existing.connectionId,
        label: existing.label,
        // Config is readable, so it is prefilled. Secrets are not, so they
        // start empty and stay stored unless the user types a new one.
        config: Object.fromEntries(
          Object.entries(existing.config).map(([k, v]) => [k, String(v ?? '')])
        ),
        secrets: {},
      },
    });
  },

  cancelEditing: () => set({ editing: null, draft: null, saveError: null }),

  updateDraft: (patch) =>
    set((s) => (s.draft ? { draft: { ...s.draft, ...patch } } : {})),

  save: async () => {
    const { draft, editing } = get();
    if (!draft || !editing) return false;
    set({ isSaving: true, saveError: null });
    try {
      const body = {
        connectionId: draft.connectionId,
        label: draft.label,
        config: draft.config,
        // Only the fields the user actually typed into. An omitted field is
        // what tells the server to keep the credential it already holds.
        secrets: Object.fromEntries(
          Object.entries(draft.secrets).filter(([, v]) => v !== '')
        ),
      };
      if (editing === 'new') {
        await apiPost<StoredConnection>(CONNECTIONS_BASE, body);
      } else {
        await apiPut<StoredConnection>(`${CONNECTIONS_BASE}/${editing}`, body);
      }
      await get().refresh();
      set({ isSaving: false, editing: null, draft: null });
      return true;
    } catch (e) {
      set({ saveError: errorMessage(e), isSaving: false });
      return false;
    }
  },

  setEnabled: async (id, enabled) => {
    const existing = get().connections.find((c) => c.id === id);
    if (!existing) return;
    try {
      await apiPut<StoredConnection>(`${CONNECTIONS_BASE}/${id}`, {
        connectionId: existing.connectionId,
        label: existing.label,
        enabled,
      });
      await get().refresh();
    } catch (e) {
      set({ saveError: errorMessage(e) });
    }
  },

  remove: async (id) => {
    try {
      await apiDelete(`${CONNECTIONS_BASE}/${id}`);
      await get().refresh();
    } catch (e) {
      set({ saveError: errorMessage(e) });
    }
  },

  activeCount: () => get().connections.filter((c) => c.enabled).length,

  degradedCount: () =>
    get().connections.filter((c) => c.health.status === 'degraded').length,

  specFor: (connectionId) =>
    get().catalog?.connections.find((c) => c.id === connectionId) ?? null,
}));
