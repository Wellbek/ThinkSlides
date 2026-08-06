"use client";

/**
 * AI model configuration: the chat model, and the model plus backend that
 * power Deep Thinking.
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
  useSettingsStore,
  type ModelRoleDraft,
  type ProviderSpec,
  type TestResult,
  type TestTarget,
} from "@/stores/settings-store";

export function AISettingsSection() {
  const {
    catalog,
    stored,
    draft,
    isLoading,
    isSaving,
    loadError,
    saveError,
    savedAt,
    testing,
    testResults,
    load,
    updateChat,
    updateDeepSearch,
    updateSearchBackend,
    resetDraft,
    save,
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

  const selectedBackend = useMemo(
    () => backends.find((b) => b.id === draft.searchBackend.backend) ?? null,
    [backends, draft.searchBackend.backend]
  );

  // Native search borrows the deep-search model's credentials, so the note
  // that matters is the one belonging to whichever provider will be used.
  const nativeProvider = useMemo(() => {
    const id = draft.deepSearch.provider || draft.chat.provider;
    return providers.find((p) => p.id === id) ?? null;
  }, [providers, draft.deepSearch.provider, draft.chat.provider]);

  const isDirty = useMemo(() => {
    if (!stored) return false;
    return (
      draft.chat.provider !== (stored.chat.provider ?? "") ||
      draft.chat.model !== (stored.chat.model ?? "") ||
      draft.chat.baseUrl !== (stored.chat.baseUrl ?? "") ||
      draft.chat.apiKey !== "" ||
      draft.deepSearch.provider !== (stored.deepSearch.provider ?? "") ||
      draft.deepSearch.model !== (stored.deepSearch.model ?? "") ||
      draft.deepSearch.baseUrl !== (stored.deepSearch.baseUrl ?? "") ||
      draft.deepSearch.apiKey !== "" ||
      draft.searchBackend.backend !== (stored.searchBackend.backend ?? "") ||
      draft.searchBackend.baseUrl !== (stored.searchBackend.baseUrl ?? "") ||
      draft.searchBackend.apiKey !== ""
    );
  }, [stored, draft]);

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

      {/* ── Chat model ─────────────────────────────────────────── */}
      <section>
        <SectionHeading
          icon={<MessageSquare size={13} />}
          title="Chat model"
          subtitle="Answers every message and writes your slide content."
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
          title="Deep Thinking"
          subtitle="The research mode in the composer. It needs a working search backend; without one the composer falls back to Simple."
        />

        {status && (
          <div className="mb-4">
            {status.deepThinkingAvailable ? (
              <Callout tone="success" icon={<ShieldCheck size={14} />}>
                Deep Thinking is available using{" "}
                <strong className="text-paper">
                  {backends.find((b) => b.id === status.deepSearchBackend)?.label ??
                    status.deepSearchBackend}
                </strong>
                .
              </Callout>
            ) : (
              <Callout tone="warning" icon={<AlertTriangle size={14} />}>
                {status.deepThinkingUnavailableReason ??
                  "No deep-search backend is configured yet."}
              </Callout>
            )}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="field-label">Search backend</label>
            <select
              className="field-input"
              value={draft.searchBackend.backend}
              onChange={(e) => updateSearchBackend({ backend: e.target.value })}
            >
              <option value="">Not configured</option>
              {backends.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.label}
                </option>
              ))}
            </select>
            {selectedBackend && <p className="field-hint">{selectedBackend.description}</p>}
            {selectedBackend?.id === "native" && nativeProvider?.nativeWebSearchNote && (
              <p className="field-hint text-ice/80">{nativeProvider.nativeWebSearchNote}</p>
            )}
          </div>

          {selectedBackend?.requiresApiKey && (
            <ApiKeyField
              label={`${selectedBackend.label} API key`}
              value={draft.searchBackend.apiKey}
              hint={stored?.searchBackend.apiKeyHint ?? null}
              hasKey={stored?.searchBackend.hasApiKey ?? false}
              help={selectedBackend.apiKeyHelp}
              onChange={(apiKey) => updateSearchBackend({ apiKey })}
            />
          )}

          <div>
            <label className="field-label">Deep Thinking model</label>
            <p className="field-hint !mt-0 mb-2">
              Optional. Leave the provider blank to reason with the chat model above. A separate
              model is mainly useful when the backend is the provider&apos;s own search.
            </p>
            <ProviderPicker
              role={draft.deepSearch}
              providers={providers}
              onChange={updateDeepSearch}
              blankLabel="Same as chat model"
            />
          </div>

          {draft.deepSearch.provider && (
            <>
              <ApiKeyField
                label="Deep Thinking API key"
                value={draft.deepSearch.apiKey}
                hint={stored?.deepSearch.apiKeyHint ?? null}
                hasKey={stored?.deepSearch.hasApiKey ?? false}
                help={providers.find((p) => p.id === draft.deepSearch.provider)?.apiKeyHelp}
                onChange={(apiKey) => updateDeepSearch({ apiKey })}
              />

              <Advanced
                open={showDeepAdvanced}
                onToggle={() => setShowDeepAdvanced((v) => !v)}
                placeholder={
                  providers.find((p) => p.id === draft.deepSearch.provider)?.defaultBaseUrl
                }
                value={draft.deepSearch.baseUrl}
                onChange={(baseUrl) => updateDeepSearch({ baseUrl })}
              />

              <TestRow
                label="Test Deep Thinking model"
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
              isDirty
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

      {/* ── Save bar ───────────────────────────────────────────── */}
      <div className="sticky bottom-0 -mx-6 px-6 py-3 bg-charcoal border-t border-iron flex items-center gap-3">
        <button
          className="btn-outlined !py-2 !px-4 !text-caption disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={save}
          disabled={isSaving || !isDirty}
        >
          {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
          {isSaving ? "Saving…" : "Save settings"}
        </button>
        {isDirty && (
          <button className="btn-ghost !text-caption !px-2" onClick={resetDraft} disabled={isSaving}>
            Discard changes
          </button>
        )}
        <div className="flex-1" />
        {saveError ? (
          <span className="text-caption text-error">{saveError}</span>
        ) : savedAt && !isDirty ? (
          <span className="text-caption text-success flex items-center gap-1.5">
            <Check size={12} /> Saved
          </span>
        ) : null}
      </div>
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

function ProviderPicker({
  role,
  providers,
  onChange,
  blankLabel = "Not configured",
}: {
  role: ModelRoleDraft;
  providers: ProviderSpec[];
  onChange: (patch: Partial<ModelRoleDraft>) => void;
  blankLabel?: string;
}) {
  const spec = providers.find((p) => p.id === role.provider) ?? null;
  const listId = `models-${role.provider || "none"}-${blankLabel.replace(/\s/g, "")}`;

  return (
    <div className="grid grid-cols-2 gap-3">
      <div>
        <label className="field-label">Provider</label>
        <select
          className="field-input"
          value={role.provider}
          onChange={(e) => {
            const provider = e.target.value;
            const next = providers.find((p) => p.id === provider);
            // Switching provider carries the old model id over as garbage, so
            // reset to that provider's default.
            onChange({ provider, model: next?.defaultModel ?? "" });
          }}
        >
          <option value="">{blankLabel}</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="field-label">Model</label>
        {/* A datalist rather than a select: providers ship new model ids faster
            than the catalog tracks them, so free text has to stay possible. */}
        <input
          className="field-input"
          list={listId}
          value={role.model}
          placeholder={spec?.defaultModel ?? "Select a provider first"}
          disabled={!role.provider}
          onChange={(e) => onChange({ model: e.target.value })}
        />
        <datalist id={listId}>
          {(spec?.suggestedModels ?? []).map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </div>
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
