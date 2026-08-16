"use client";

/**
 * AI model configuration: the reasoning model, and the web search behind Deep
 * Thinking.
 *
 * Two decisions, because there are two. Deep Thinking *is* web search - there
 * is no third mode where it reasons harder without searching - so it is not
 * asked about as a backend and a model separately. One picker names where the
 * results come from, and the fields under it are whatever that source needs:
 * a model and a key for a provider searching with its own index, a key alone
 * for an external search API.
 *
 * API keys only ever travel one way. A saved key comes back as a last-four
 * hint shown in the placeholder, so leaving the field blank means "keep the
 * stored key" - the same tri-state the settings API defines.
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Globe,
  Loader2,
  MessageSquare,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  NATIVE_BACKEND,
  SEARCH_SOURCE_SAME_AS_CHAT,
  backendSource,
  parseSearchSource,
  providerSource,
  useSettingsStore,
  type ModelRoleDraft,
  type ProviderSpec,
  type SearchBackendSpec,
  type StoredSettings,
  type TestResult,
  type TestTarget,
} from "@/stores/settings-store";

export function AISettingsSection() {
  const {
    catalog,
    stored,
    draft,
    isLoading,
    loadError,
    testing,
    testResults,
    load,
    updateChat,
    updateDeepSearch,
    updateSearchBackend,
    searchSource,
    setSearchSource,
    isDirty,
    test,
  } = useSettingsStore();

  const [showChatAdvanced, setShowChatAdvanced] = useState(false);
  const [showDeepAdvanced, setShowDeepAdvanced] = useState(false);

  useEffect(() => {
    load();
  }, [load]);

  // Memoised because the fallback array is a fresh reference each render,
  // which would otherwise re-run every dependent useMemo below.
  const providers = useMemo(() => catalog?.providers ?? [], [catalog]);
  const backends = useMemo(() => catalog?.searchBackends ?? [], [catalog]);
  const status = stored?.status;

  // Every place Deep Thinking's results can come from, as one list. "native"
  // is not among them: it is not a source, it is what a model provider
  // searching with its own index is called in storage.
  const externalBackends = useMemo(
    () => backends.filter((b) => b.id !== NATIVE_BACKEND),
    [backends]
  );
  const searchProviders = useMemo(
    () => providers.filter((p) => p.nativeWebSearch),
    [providers]
  );

  const source = searchSource();
  const parsedSource = parseSearchSource(source);
  const sourceBackend =
    parsedSource.kind === "backend"
      ? backends.find((b) => b.id === parsedSource.id) ?? null
      : null;
  // Which provider actually does the searching - the one chosen here, or the
  // reasoning model's when the source is "same as".
  const sourceProvider = useMemo(() => {
    if (parsedSource.kind === "backend") return null;
    const id = parsedSource.kind === "provider" ? parsedSource.id : draft.chat.provider;
    return providers.find((p) => p.id === id) ?? null;
  }, [providers, parsedSource, draft.chat.provider]);

  // Recomputed on every render rather than memoised: it reads the draft, which
  // is exactly what changes on every keystroke.
  const dirty = isDirty();

  if (isLoading && !stored) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-fog">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-caption">Loading settings…</span>
      </div>
    );
  }

  if (loadError && !stored) {
    return (
      <div className="error-block">
        Could not load AI settings: {loadError}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {status && !status.encryptionAvailable && (
        <Callout tone="warning" icon={<AlertTriangle size={14} />}>
          <strong className="text-paper">Credential encryption is not configured.</strong>{" "}
          Set <code className="font-mono text-ice">SETTINGS_ENCRYPTION_KEY</code> on the AI service
          before saving - API keys are never stored unencrypted, so saving will be refused until
          it is set.
        </Callout>
      )}

      {/* ── Reasoning model ────────────────────────────────────── */}
      <section>
        <SectionHeading
          icon={<MessageSquare size={13} />}
          title="Reasoning model"
          subtitle="Answers every message, plans the deck, and writes your slide content."
        />

        <div className="space-y-3">
          <ProviderPicker
            role={draft.chat}
            providers={providers}
            onChange={updateChat}
          />

          <ApiKeyField
            label="API key"
            value={draft.chat.apiKey}
            hint={stored?.chat.apiKeyHint ?? null}
            hasKey={stored?.chat.hasApiKey ?? false}
            help={providers.find((p) => p.id === draft.chat.provider)?.apiKeyHelp}
            onChange={(apiKey) => updateChat({ apiKey })}
          />

          <Advanced
            open={showChatAdvanced}
            onToggle={() => setShowChatAdvanced((v) => !v)}
            placeholder={providers.find((p) => p.id === draft.chat.provider)?.defaultBaseUrl}
            value={draft.chat.baseUrl}
            onChange={(baseUrl) => updateChat({ baseUrl })}
          />

          <TestRow
            label="Test credentials"
            description="Sends a one-word prompt to check the key, model, and endpoint."
            target="chat"
            testing={testing}
            result={testResults.chat}
            onTest={() => test("chat")}
          />
        </div>
      </section>

      <Divider />

      {/* ── Deep Thinking ──────────────────────────────────────── */}
      <section>
        <SectionHeading
          icon={<Globe size={13} />}
          title="Deep Thinking (web search)"
          subtitle="The research mode in the composer: it searches the web and cites what it finds. Without a working search source the composer falls back to Simple."
        />

        {status && (
          <div className="mb-4">
            {status.deepThinkingAvailable ? (
              <Callout tone="success" icon={<ShieldCheck size={14} />}>
                Deep Thinking is available, searching with{" "}
                <strong className="text-paper">{savedSourceLabel(stored, providers, backends)}</strong>
                .
              </Callout>
            ) : (
              <Callout tone="warning" icon={<AlertTriangle size={14} />}>
                {status.deepThinkingUnavailableReason ??
                  "No web search source is configured yet."}
              </Callout>
            )}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="field-label">Web search source</label>
            <select
              className="field-input"
              value={source}
              onChange={(e) => setSearchSource(e.target.value, catalog)}
            >
              <option value={SEARCH_SOURCE_SAME_AS_CHAT}>
                The reasoning model&apos;s own search
              </option>
              {searchProviders.map((p) => (
                <option key={p.id} value={providerSource(p.id)}>
                  {p.label} - its own search
                </option>
              ))}
              {externalBackends.map((b) => (
                <option key={b.id} value={backendSource(b.id)}>
                  {b.label}
                </option>
              ))}
            </select>
            <p className="field-hint">
              {sourceBackend?.description ??
                (parsedSource.kind === "provider"
                  ? "Searches with this provider's own index, using its own key - separate from the reasoning model."
                  : "Searches with the reasoning model's provider, using the key above. Nothing else to set up.")}
            </p>
            {sourceProvider?.nativeWebSearchNote && (
              <p className="field-hint text-ice/80">{sourceProvider.nativeWebSearchNote}</p>
            )}
          </div>

          {/* An external search API needs one thing: its key. */}
          {sourceBackend?.requiresApiKey && (
            <ApiKeyField
              label={`${sourceBackend.label} API key`}
              value={draft.searchBackend.apiKey}
              hint={stored?.searchBackend.apiKeyHint ?? null}
              hasKey={stored?.searchBackend.hasApiKey ?? false}
              help={sourceBackend.apiKeyHelp}
              onChange={(apiKey) => updateSearchBackend({ apiKey })}
            />
          )}

          {/* A provider searching with its own index needs a model and a key.
              The provider itself is not asked for again - the source picker
              above already is that question. */}
          {parsedSource.kind === "provider" && (
            <>
              <ModelField
                spec={sourceProvider}
                value={draft.deepSearch.model}
                onChange={(model) => updateDeepSearch({ model })}
              />

              <ApiKeyField
                label={`${sourceProvider?.label ?? "Search"} API key`}
                value={draft.deepSearch.apiKey}
                hint={stored?.deepSearch.apiKeyHint ?? null}
                hasKey={stored?.deepSearch.hasApiKey ?? false}
                help={sourceProvider?.apiKeyHelp}
                onChange={(apiKey) => updateDeepSearch({ apiKey })}
              />

              <Advanced
                open={showDeepAdvanced}
                onToggle={() => setShowDeepAdvanced((v) => !v)}
                placeholder={sourceProvider?.defaultBaseUrl}
                value={draft.deepSearch.baseUrl}
                onChange={(baseUrl) => updateDeepSearch({ baseUrl })}
              />

              <TestRow
                label="Test search model"
                description="Checks the model answers before it is used for research."
                target="deepSearch"
                testing={testing}
                result={testResults.deepSearch}
                onTest={() => test("deepSearch")}
              />
            </>
          )}

          <TestRow
            label="Test web search"
            description={
              dirty
                ? "Runs against your saved settings - save first to test what you just changed."
                : "Runs a known-answer query so you can see what sources come back."
            }
            target="search"
            testing={testing}
            result={testResults.search}
            onTest={() => test("search")}
          />
        </div>
      </section>

    </div>
  );
}

/** What the *saved* settings actually search with, for the status callout. */
function savedSourceLabel(
  stored: StoredSettings | null,
  providers: ProviderSpec[],
  backends: SearchBackendSpec[]
): string {
  const backend = stored?.status.deepSearchBackend;
  if (backend && backend !== NATIVE_BACKEND) {
    return backends.find((b) => b.id === backend)?.label ?? backend;
  }
  const providerId = stored?.deepSearch.provider || stored?.chat.provider;
  const label = providers.find((p) => p.id === providerId)?.label;
  return label ? `${label}'s own search` : "the reasoning model's own search";
}

/**
 * The save / discard bar.
 *
 * Rendered by the modal as a real footer rather than inside the scrolling
 * body, so content cannot slide underneath it.
 */
export function AISettingsFooter() {
  const { isSaving, saveError, savedAt, save, resetDraft, isDirty, stored } =
    useSettingsStore();

  if (!stored) return null;
  const dirty = isDirty();

  return (
    <div className="px-6 py-3 border-t border-iron bg-charcoal rounded-b-xl flex items-center gap-3">
      <button
        className="btn-outlined !py-2 !px-4 !text-caption disabled:opacity-50 disabled:cursor-not-allowed"
        onClick={save}
        disabled={isSaving || !dirty}
      >
        {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
        {isSaving ? "Saving…" : "Save settings"}
      </button>
      {dirty && (
        <button className="btn-ghost !text-caption !px-2" onClick={resetDraft} disabled={isSaving}>
          Discard changes
        </button>
      )}
      <div className="flex-1" />
      {saveError ? (
        <span className="text-caption text-error">{saveError}</span>
      ) : savedAt && !dirty ? (
        <span className="text-caption text-success flex items-center gap-1.5">
          <Check size={12} /> Saved
        </span>
      ) : null}
    </div>
  );
}

// ── Building blocks ──────────────────────────────────────────────────

function SectionHeading({
  icon,
  title,
  subtitle,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="w-6 h-6 rounded-md bg-ice/10 text-ice flex items-center justify-center shrink-0">
          {icon}
        </span>
        <h3 className="text-body font-medium text-paper">{title}</h3>
      </div>
      <p className="text-caption text-fog">{subtitle}</p>
    </div>
  );
}

function Divider() {
  return <div className="border-t border-iron" />;
}

function Callout({
  tone,
  icon,
  children,
}: {
  tone: "warning" | "success";
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  const toneClass =
    tone === "warning"
      ? "bg-error/8 border-error/25 text-error"
      : "bg-success/8 border-success/25 text-success";
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 ${toneClass}`}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <p className="text-caption leading-relaxed text-fog">{children}</p>
    </div>
  );
}

/**
 * Provider and model for the reasoning role.
 *
 * Only the reasoning role asks for a provider now: Deep Thinking's provider
 * *is* its search source, chosen once in the picker above it.
 */
function ProviderPicker({
  role,
  providers,
  onChange,
}: {
  role: ModelRoleDraft;
  providers: ProviderSpec[];
  onChange: (patch: Partial<ModelRoleDraft>) => void;
}) {
  const spec = providers.find((p) => p.id === role.provider) ?? null;

  return (
    <div className="grid grid-cols-2 gap-3">
      <div>
        <label className="field-label">Provider</label>
        <select
          className="field-input"
          value={role.provider}
          onChange={(e) => {
            const provider = e.target.value;
            // Switching provider carries the old model id over as garbage, so
            // reset to that provider's default.
            const next = providers.find((p) => p.id === provider);
            onChange({ provider, model: next?.defaultModel ?? "" });
          }}
        >
          <option value="">Not configured</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>
      <ModelField spec={spec} value={role.model} purpose="chat" onChange={(model) => onChange({ model })} />
    </div>
  );
}

/**
 * One model id, with the provider's own suggestions behind it.
 *
 * A datalist rather than a select: providers ship new model ids faster than
 * the catalog tracks them, so free text has to stay possible.
 */
function ModelField({
  spec,
  value,
  onChange,
  purpose = "search",
}: {
  spec: ProviderSpec | null;
  value: string;
  onChange: (model: string) => void;
  /** The reasoning model must support tool calling; a search model need not. */
  purpose?: "chat" | "search";
}) {
  const listId = `models-${purpose}-${spec?.id ?? "none"}`;
  const noToolCalling = spec?.noToolCallingModels ?? [];

  // Search-only models are offered for search and kept out of the reasoning
  // suggestions, because one of them in that role fails every generation.
  const suggestions = (spec?.suggestedModels ?? []).filter(
    (m) => purpose === "search" || !noToolCalling.includes(m)
  );
  const searchOnly = purpose === "search" ? spec?.nativeWebSearchModels ?? [] : [];
  const options = Array.from(new Set([...searchOnly, ...suggestions]));
  const isSearchOnly = purpose === "chat" && !!value && noToolCalling.includes(value);

  return (
    <div>
      <label className="field-label">Model</label>
      <input
        className="field-input"
        list={listId}
        value={value}
        placeholder={
          (purpose === "search" ? spec?.nativeWebSearchModels?.[0] : undefined) ??
          spec?.defaultModel ??
          "Select a provider first"
        }
        disabled={!spec}
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {options.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      {isSearchOnly && (
        <p className="field-hint text-error">
          {value} is a search-only model and cannot drive the agent. Use it as the Deep
          Thinking search model below and pick a tool-calling model here.
        </p>
      )}
    </div>
  );
}

function ApiKeyField({
  label,
  value,
  hint,
  hasKey,
  help,
  onChange,
}: {
  label: string;
  value: string;
  hint: string | null;
  hasKey: boolean;
  help?: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className="field-label">{label}</label>
      <input
        type="password"
        className="field-input font-mono"
        value={value}
        autoComplete="off"
        placeholder={hasKey ? `Saved key ending ${hint ?? "••••"} - leave blank to keep` : "Paste your API key"}
        onChange={(e) => onChange(e.target.value)}
      />
      {help && <p className="field-hint">{help}</p>}
    </div>
  );
}

function Advanced({
  open,
  onToggle,
  value,
  placeholder,
  onChange,
}: {
  open: boolean;
  onToggle: () => void;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <button
        className="flex items-center gap-1 text-caption text-fog hover:text-paper transition-colors"
        onClick={onToggle}
        type="button"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Advanced
      </button>
      {open && (
        <div className="mt-2">
          <label className="field-label">Base URL</label>
          <input
            className="field-input font-mono"
            value={value}
            placeholder={placeholder ?? "Provider default"}
            onChange={(e) => onChange(e.target.value)}
          />
          <p className="field-hint">
            Only needed for a proxy, a gateway, or a self-hosted endpoint.
          </p>
        </div>
      )}
    </div>
  );
}

function TestRow({
  label,
  description,
  target,
  testing,
  result,
  onTest,
}: {
  label: string;
  description: string;
  target: TestTarget;
  testing: TestTarget | null;
  result?: TestResult;
  onTest: () => void;
}) {
  const busy = testing === target;
  return (
    <div className="rounded-lg border border-iron bg-graphite/30 p-3">
      <div className="flex items-center gap-3">
        <button
          className="btn-outlined !py-1.5 !px-3 !text-caption shrink-0 disabled:opacity-50"
          onClick={onTest}
          disabled={busy}
          type="button"
        >
          {busy && <Loader2 size={12} className="animate-spin" />}
          {busy ? "Testing…" : label}
        </button>
        <p className="text-caption text-fog">{description}</p>
      </div>

      {result && (
        <div className="mt-3 pt-3 border-t border-iron">
          {result.ok ? (
            <div className="text-caption text-success flex items-start gap-1.5">
              <Check size={12} className="mt-0.5 shrink-0" />
              <div className="min-w-0">
                <span>
                  Success
                  {result.latencyMs != null && ` in ${result.latencyMs} ms`}
                  {result.model && ` · ${result.model}`}
                  {result.backend && ` · ${result.backend}`}
                </span>
                {result.reply && (
                  <p className="text-fog font-mono mt-1 truncate">{result.reply}</p>
                )}
                {result.note && <p className="text-fog mt-1">{result.note}</p>}
                {result.sources && result.sources.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {result.sources.map((s) => (
                      <li key={s.url} className="truncate">
                        <a
                          href={s.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-ice hover:underline"
                        >
                          {s.title}
                        </a>
                        <span className="text-fog"> · {s.source}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ) : (
            <div className="text-caption text-error flex items-start gap-1.5">
              <X size={12} className="mt-0.5 shrink-0" />
              <span className="min-w-0 break-words">{result.error}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
