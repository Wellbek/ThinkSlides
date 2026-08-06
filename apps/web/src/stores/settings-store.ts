/**
 * Organisation AI settings: which models answer chat, which model and backend
 * power Deep Thinking, and whether either is configured yet.
 *
 * API keys are write-only. The server never returns key material, only a
 * last-four hint and a boolean, so this store deliberately holds the saved
 * settings and the in-progress draft separately: the draft's `apiKey` is empty
 * unless the user is typing a new one, and an empty draft key means "keep what
 * is stored" rather than "clear it".
 */

import { create } from 'zustand';
import { apiGet, apiPost, apiPut } from '@/lib/api';

const SETTINGS_BASE = '/api/orchestrator/settings';

export interface ProviderSpec {
  id: string;
  label: string;
  defaultModel: string;
  suggestedModels: string[];
  defaultBaseUrl: string;
  nativeWebSearch: boolean;
  nativeWebSearchNote: string;
  nativeWebSearchModels: string[];
  /** Search-only models: valid for Deep Thinking, never for chat. */
  noToolCallingModels: string[];
  apiKeyHelp: string;
}

export interface SearchBackendSpec {
  id: string;
  label: string;
  requiresApiKey: boolean;
  description: string;
  apiKeyHelp: string;
}

export interface Catalog {
  providers: ProviderSpec[];
  searchBackends: SearchBackendSpec[];
}

/** A saved model role as the server reports it - never includes the key. */
export interface StoredModelRole {
  provider: string | null;
  model: string | null;
  baseUrl: string | null;
  apiKeyHint: string | null;
  hasApiKey: boolean;
}

export interface StoredSearchBackend {
  backend: string | null;
  baseUrl: string | null;
  apiKeyHint: string | null;
  hasApiKey: boolean;
}

export interface ConfigurationStatus {
  chatConfigured: boolean;
  chatProvider: string | null;
  chatModel: string | null;
  deepThinkingAvailable: boolean;
  deepSearchBackend: string | null;
  deepThinkingUnavailableReason: string | null;
  encryptionAvailable: boolean;
}

export interface StoredSettings {
  chat: StoredModelRole;
  deepSearch: StoredModelRole;
  searchBackend: StoredSearchBackend;
  status: ConfigurationStatus;
}

/** The editable form state for one model role. */
export interface ModelRoleDraft {
  provider: string;
  model: string;
  baseUrl: string;
  /** Empty means "leave the stored key alone". */
  apiKey: string;
}

export interface SearchBackendDraft {
  backend: string;
  baseUrl: string;
  apiKey: string;
}

export interface Draft {
  chat: ModelRoleDraft;
  deepSearch: ModelRoleDraft;
  searchBackend: SearchBackendDraft;
}

export interface TestResult {
  ok: boolean;
  error?: string;
  provider?: string;
  model?: string;
  backend?: string;
  reply?: string;
  /** Set when the call succeeded but the outcome needs explaining. */
  note?: string;
  latencyMs?: number;
  query?: string;
  resultCount?: number;
  sources?: { title: string; url: string; source: string }[];
}

export type TestTarget = 'chat' | 'deepSearch' | 'search';

const EMPTY_ROLE: ModelRoleDraft = { provider: '', model: '', baseUrl: '', apiKey: '' };

/** What to assume before the settings have loaded: nothing is configured. */
export const UNCONFIGURED_STATUS: ConfigurationStatus = {
  chatConfigured: false,
  chatProvider: null,
  chatModel: null,
  deepThinkingAvailable: false,
  deepSearchBackend: null,
  deepThinkingUnavailableReason: null,
  encryptionAvailable: true,
};

function draftFromStored(stored: StoredSettings): Draft {
  return {
    chat: {
      provider: stored.chat.provider ?? '',
      model: stored.chat.model ?? '',
      baseUrl: stored.chat.baseUrl ?? '',
      apiKey: '',
    },
    deepSearch: {
      provider: stored.deepSearch.provider ?? '',
      model: stored.deepSearch.model ?? '',
      baseUrl: stored.deepSearch.baseUrl ?? '',
      apiKey: '',
    },
    searchBackend: {
      backend: stored.searchBackend.backend ?? '',
      baseUrl: stored.searchBackend.baseUrl ?? '',
      apiKey: '',
    },
  };
}

/**
 * Build the PUT body. Fields the user left blank are sent as null so the
 * server clears them, except `apiKey`, which is omitted entirely when empty -
 * the tri-state the API defines as "keep the stored key".
 */
function rolePayload(draft: ModelRoleDraft): Record<string, string | null> {
  const payload: Record<string, string | null> = {
    provider: draft.provider || null,
    model: draft.model || null,
    baseUrl: draft.baseUrl || null,
  };
  if (draft.apiKey) payload.apiKey = draft.apiKey;
  return payload;
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** De-duplicates concurrent readiness fetches (see refreshStatus). */
let inFlightStatus: Promise<void> | null = null;

interface SettingsState {
  catalog: Catalog | null;
  stored: StoredSettings | null;
  draft: Draft;
  isLoading: boolean;
  isSaving: boolean;
  loadError: string | null;
  saveError: string | null;
  savedAt: number | null;
  testing: TestTarget | null;
  testResults: Partial<Record<TestTarget, TestResult>>;

  /** Fetch the catalog and the current settings. Safe to call repeatedly. */
  load: () => Promise<void>;
  /** Refresh only the readiness summary - cheap enough for a page load. */
  refreshStatus: () => Promise<void>;
  updateChat: (patch: Partial<ModelRoleDraft>) => void;
  updateDeepSearch: (patch: Partial<ModelRoleDraft>) => void;
  updateSearchBackend: (patch: Partial<SearchBackendDraft>) => void;
  resetDraft: () => void;
  save: () => Promise<boolean>;
  test: (target: TestTarget) => Promise<void>;
  clearTestResult: (target: TestTarget) => void;

  status: () => ConfigurationStatus;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  catalog: null,
  stored: null,
  draft: { chat: { ...EMPTY_ROLE }, deepSearch: { ...EMPTY_ROLE }, searchBackend: { backend: '', baseUrl: '', apiKey: '' } },
  isLoading: false,
  isSaving: false,
  loadError: null,
  saveError: null,
  savedAt: null,
  testing: null,
  testResults: {},

  load: async () => {
    set({ isLoading: true, loadError: null });
    try {
      const [catalog, stored] = await Promise.all([
        apiGet<Catalog>(`${SETTINGS_BASE}/catalog`),
        apiGet<StoredSettings>(`${SETTINGS_BASE}/ai`),
      ]);
      set({ catalog, stored, draft: draftFromStored(stored), isLoading: false });
    } catch (e) {
      set({ loadError: errorMessage(e), isLoading: false });
    }
  },

  refreshStatus: async () => {
    // Several components want the readiness summary on first paint (the setup
    // banner, both effort selectors). Collapse those into one request.
    if (inFlightStatus) return inFlightStatus;
    inFlightStatus = (async () => {
      try {
        const stored = await apiGet<StoredSettings>(`${SETTINGS_BASE}/ai`);
        // Only adopt the draft when there is not one already, so a background
        // refresh never overwrites what the user is typing.
        const hasDraft = get().stored !== null;
        set(hasDraft ? { stored } : { stored, draft: draftFromStored(stored) });
      } catch {
        // A failed status refresh must not break the app; the banner simply
        // keeps showing whatever it last knew.
      } finally {
        inFlightStatus = null;
      }
    })();
    return inFlightStatus;
  },

  updateChat: (patch) => set((s) => ({ draft: { ...s.draft, chat: { ...s.draft.chat, ...patch } } })),
  updateDeepSearch: (patch) =>
    set((s) => ({ draft: { ...s.draft, deepSearch: { ...s.draft.deepSearch, ...patch } } })),
  updateSearchBackend: (patch) =>
    set((s) => ({ draft: { ...s.draft, searchBackend: { ...s.draft.searchBackend, ...patch } } })),

  resetDraft: () => {
    const stored = get().stored;
    if (stored) set({ draft: draftFromStored(stored), saveError: null });
  },

  save: async () => {
    const { draft } = get();
    set({ isSaving: true, saveError: null });
    try {
      const backendPayload: Record<string, string | null> = {
        backend: draft.searchBackend.backend || null,
        baseUrl: draft.searchBackend.baseUrl || null,
      };
      if (draft.searchBackend.apiKey) backendPayload.apiKey = draft.searchBackend.apiKey;

      const stored = await apiPut<StoredSettings>(`${SETTINGS_BASE}/ai`, {
        chat: rolePayload(draft.chat),
        deepSearch: rolePayload(draft.deepSearch),
        searchBackend: backendPayload,
      });
      // Re-derive the draft so the just-entered keys are dropped from memory
      // and the fields fall back to showing the stored hints.
      set({ stored, draft: draftFromStored(stored), isSaving: false, savedAt: Date.now() });
      return true;
    } catch (e) {
      set({ saveError: errorMessage(e), isSaving: false });
      return false;
    }
  },

  test: async (target) => {
    const { draft } = get();
    set({ testing: target, testResults: { ...get().testResults, [target]: undefined } });
    try {
      let result: TestResult;
      if (target === 'search') {
        // The search test always runs against saved settings - the backend
        // needs a stored, decryptable key to call out with.
        result = await apiPost<TestResult>(`${SETTINGS_BASE}/ai/test-search`, {});
      } else {
        const role = target === 'chat' ? draft.chat : draft.deepSearch;
        result = await apiPost<TestResult>(`${SETTINGS_BASE}/ai/test-chat`, {
          provider: role.provider || null,
          model: role.model || null,
          baseUrl: role.baseUrl || null,
          apiKey: role.apiKey || null,
          role: target,
        });
      }
      set({ testing: null, testResults: { ...get().testResults, [target]: result } });
    } catch (e) {
      set({
        testing: null,
        testResults: { ...get().testResults, [target]: { ok: false, error: errorMessage(e) } },
      });
    }
  },

  clearTestResult: (target) =>
    set((s) => ({ testResults: { ...s.testResults, [target]: undefined } })),

  status: () => get().stored?.status ?? UNCONFIGURED_STATUS,
}));
