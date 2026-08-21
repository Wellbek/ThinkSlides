"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useChatStore } from "@/stores/chat-store";
import { collectPreviewSlides } from "@/lib/slides";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { apiUpload } from "@/lib/api";
import { AssetReplaceDialog, type ReplaceScope } from "./AssetReplaceDialog";
import { FileSpreadsheet, Presentation, Globe, Plus, Monitor, Download, ExternalLink, ChevronDown, Image as ImageIcon } from "lucide-react";

export function DetailsPanel() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const templates = useChatStore((s) => s.templates);
  const activeMessages = useChatStore((s) => s.activeMessages);
  const activeFiles = useChatStore((s) => s.activeFiles);

  const session = sessions.find((s) => s.id === activeSessionId) ?? null;
  const templateName = session?.template_id
    ? templates.find((t) => t.id === session.template_id)?.name
    : null;

  const slides = useMemo(() => collectPreviewSlides(activeMessages), [activeMessages]);
  const documents = useMemo(() => collectDocuments(activeMessages), [activeMessages]);
  const webSources = useMemo(() => collectWebSources(activeMessages), [activeMessages]);
  const generatedFiles = useMemo(
    () =>
      activeFiles
        .filter((f) => !f.file_type || f.file_type === "pptx")
        .map((f) => ({ ...f, downloadUrl: buildDownloadUrl(f.file_url) })),
    [activeFiles]
  );
  const assets = useMemo(() => collectAssets(activeFiles), [activeFiles]);

  const created = session?.created_at ? new Date(session.created_at).toISOString().slice(0, 10) : "-";

  const [dragOverAssetId, setDragOverAssetId] = useState<string | null>(null);
  const [pendingReplace, setPendingReplace] = useState<{ asset: any; file: File } | null>(null);

  const handleAssetDrop = (asset: any, e: React.DragEvent) => {
    e.preventDefault();
    setDragOverAssetId(null);
    const file = e.dataTransfer.files?.[0];
    if (file) setPendingReplace({ asset, file });
  };

  const handleReplaceConfirm = async (scope: ReplaceScope) => {
    if (!pendingReplace || !activeSessionId) return;
    const { asset, file } = pendingReplace;
    const formData = new FormData();
    formData.append("file", file);

    // "in_slide" hits a different endpoint - it resumes the agent rather
    // than just swapping a file, so it can fail in ways the other two
    // scopes can't (most notably: the session's editing state expired -
    // see the AI service's replace-in-slide endpoint). Left uncaught here
    // deliberately: AssetReplaceDialog catches it and keeps itself open
    // with the message shown, instead of the drop silently vanishing.
    //
    // The dialog is still awaiting this same call while it runs, so it
    // must not be torn down (setPendingReplace(null) unmounts it) until
    // everything below has actually settled - closing it early meant a
    // failure in setActiveSession threw into an already-unmounted
    // component: the error never reached the user, and React logged a
    // set-state-on-unmounted warning.
    if (scope === "in_slide") {
      await apiUpload(`/api/orchestrator/sessions/${activeSessionId}/assets/${asset.id}/replace-in-slide`, formData);
      await useChatStore.getState().setActiveSession(activeSessionId);
      setPendingReplace(null);
      return;
    }

    formData.append("scope", scope);
    await apiUpload(`/api/orchestrator/sessions/${activeSessionId}/assets/${asset.id}/replace`, formData);
    await useChatStore.getState().setActiveSession(activeSessionId);
    setPendingReplace(null);
  };

  return (
    <div className="ws-scroll ws-left-pad">
      <div className="ws-issue-icon">
        <Monitor size={22} />
      </div>
      <div className="ws-issue-num">SESSION {session ? `#${session.id.slice(-4)}` : ""}</div>
      <div className="ws-issue-title">{session?.title ?? "Untitled session"}</div>
      <div className="ws-issue-desc">
        {slides.length} slide{slides.length === 1 ? "" : "s"} generated
        {templateName ? ` from the ${templateName} template` : ""}.
      </div>

      <div className="ws-badge-row">
        <span className="ws-badge active"><span className="dot" />Session</span>
        {templateName && <span className="ws-badge high"><span className="dot" />Brand-locked</span>}
      </div>

      <div className="ws-divider" />
      <div className="ws-sect-label">Properties</div>
      <div className="ws-meta-grid">
        <div className="ws-meta-label">Template</div>
        <div className="ws-meta-value"><span className="truncate">{templateName ?? "None"}</span></div>
        <div className="ws-meta-label">Slides</div>
        <div className="ws-meta-value">{slides.length}{session?.slide_count ? ` / ${session.slide_count}` : ""}</div>
        <div className="ws-meta-label">Owner</div>
        <div className="ws-meta-value"><span className="ws-av indigo">WK</span> You</div>
        <div className="ws-meta-label">Created</div>
        <div className="ws-meta-value">{created}</div>
      </div>

      <div className="ws-divider" />
      <div className="ws-sect-label">Slide Outline</div>
      <div className="ws-outline">
        {slides.length === 0 && <div className="ws-empty-note">Slides will appear here as they are generated.</div>}
        {slides.map((s, i) => (
          <div className={`ws-outline-item done ${i === 0 ? "cur" : ""}`} key={s.key}>
            <span className="ws-outline-num">{s.slideIndex + 1}</span>
            <span className="nm">{s.title}</span>
            <span className="st">done</span>
          </div>
        ))}
      </div>

      <div className="ws-divider" />
      <CollapsibleSection title="Output" count={generatedFiles.length}>
        <div className="ws-sources">
          {generatedFiles.length === 0 && <div className="ws-empty-note">Generated files will appear here.</div>}
          {generatedFiles.map((f) => (
            <a className="ws-src ws-src-dl" key={f.id} href={f.downloadUrl} download title={`Download ${f.filename}`}>
              <div className="s-ic pptx"><Presentation size={14} /></div>
              <div className="s-body">
                <div className="s-name">{f.filename}</div>
                <div className="s-meta">
                  {f.slide_count ? `${f.slide_count} slides` : "PPTX"}
                  {f.file_size ? ` - ${formatFileSize(f.file_size)}` : ""}
                </div>
              </div>
              <span className="s-tag"><Download size={13} /></span>
            </a>
          ))}
        </div>
      </CollapsibleSection>

      <div className="ws-divider" />
      <CollapsibleSection title="Assets" count={assets.length}>
        <div className="ws-sources">
          {assets.length === 0 && <div className="ws-empty-note">Charts and images will appear here as they&apos;re generated. Drag a file onto one to replace it.</div>}
          {assets.map((a) => (
            <a
              className={`ws-src ws-src-dl ${dragOverAssetId === a.id ? "dragging" : ""}`}
              key={a.id}
              href={a.downloadUrl}
              download
              title={`Download ${a.filename}`}
              onDragOver={(e) => { e.preventDefault(); setDragOverAssetId(a.id); }}
              onDragLeave={() => setDragOverAssetId((id) => (id === a.id ? null : id))}
              onDrop={(e) => handleAssetDrop(a, e)}
            >
              <AssetThumb src={a.downloadUrl} alt={a.filename} />
              <div className="s-body">
                <div className="s-name">{a.filename}</div>
                <div className="s-meta">
                  {typeof a.slide_index === "number" ? `Slide ${a.slide_index + 1} - ` : ""}
                  {a.file_type}
                  {a.file_size ? ` - ${formatFileSize(a.file_size)}` : ""}
                </div>
              </div>
              <span className="s-tag"><Download size={13} /></span>
              {dragOverAssetId === a.id && <span className="drop-overlay">Drop to replace</span>}
            </a>
          ))}
        </div>
      </CollapsibleSection>

      {pendingReplace && (
        <AssetReplaceDialog
          asset={pendingReplace.asset}
          file={pendingReplace.file}
          onConfirm={handleReplaceConfirm}
          onCancel={() => setPendingReplace(null)}
        />
      )}

      <div className="ws-divider" />
      <CollapsibleSection title="Documents" count={documents.length}>
        <div className="ws-sources">
          {documents.length === 0 && <div className="ws-empty-note">No documents attached yet.</div>}
          {documents.map((doc) => (
            <div className="ws-src" key={doc.key} title={doc.name}>
              <div className={`s-ic ${doc.kind}`}>
                {doc.kind === "xlsx" && <FileSpreadsheet size={14} />}
                {doc.kind === "pptx" && <Presentation size={14} />}
              </div>
              <div className="s-body">
                <div className="s-name">{doc.name}</div>
                <div className="s-meta">{doc.meta}</div>
              </div>
              <span className="s-tag">used</span>
            </div>
          ))}
          <div className="ws-src-add"><Plus size={13} /> Attach doc or data</div>
        </div>
      </CollapsibleSection>

      <div className="ws-divider" />
      <CollapsibleSection title="Web Sources" count={webSources.length}>
        <div className="ws-sources">
          {webSources.length === 0 && <div className="ws-empty-note">No web sources cited yet.</div>}
          {webSources.map((src) => (
            <a className="ws-src" key={src.key} href={src.url} target="_blank" rel="noopener noreferrer" title={src.url}>
              <WebSourceIcon url={src.url} />
              <div className="s-body">
                <div className="s-name">{src.name}</div>
                <div className="s-meta">{src.meta}</div>
              </div>
              <span className="s-tag"><ExternalLink size={13} /></span>
            </a>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  );
}

interface DocumentRow { key: string; name: string; meta: string; kind: "xlsx" | "pptx" }
interface WebSourceRow { key: string; name: string; meta: string; url: string }

function collectDocuments(messages: any[]): DocumentRow[] {
  const rows: DocumentRow[] = [];
  const seen = new Set<string>();

  // Attached files from user messages
  for (const m of messages) {
    if (m.role !== "user") continue;
    for (const name of m.attachments || []) {
      if (seen.has(name)) continue;
      seen.add(name);
      rows.push({ key: `att-${name}`, name, meta: "attachment", kind: extKind(name) });
    }
  }
  return rows;
}

/**
 * The asset itself, at row size.
 *
 * The panel showed a generic image glyph and a filename, so the only way
 * to tell whether `slide-4-chart-1.png` was the chart you meant was to
 * download it - on a panel whose whole purpose is deciding which asset to
 * replace. The file endpoint takes the same `?token=` the download link
 * does, so the row can just show it.
 *
 * Falls back to the glyph if the image will not load: a row that renders
 * a broken-image icon is worse than the one it replaced.
 */
function AssetThumb({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <div className="s-ic img"><ImageIcon size={14} /></div>;
  return (
    <div className="s-ic img ws-asset-thumb">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
    </div>
  );
}

function collectAssets(files: any[]): (any & { downloadUrl: string })[] {
  // Individual chart/image assets (#187) share the generated_files table
  // with the whole-deck pptx row - anything that isn't "pptx" belongs here,
  // not in Output.
  return files
    .filter((f) => f.file_type === "image" || f.file_type === "chart")
    .map((f) => ({ ...f, downloadUrl: buildDownloadUrl(f.file_url) }));
}

function collectWebSources(messages: any[]): WebSourceRow[] {
  const rows: WebSourceRow[] = [];
  const seen = new Set<string>();

  // Cited web sources - the structured `sources` array the agent loop
  // attaches to web_search tool_result events (title + url). Aggregated
  // across the whole session and de-duplicated by URL. Replaces the old
  // fragile regex scrape of event text, which never saw web_search results
  // (their `result` is an object, not a string) and produced host-only names.
  for (const m of messages) {
    for (const ev of m.agentEventStream || []) {
      if (ev.type !== "tool_result" || !Array.isArray(ev.sources)) continue;
      for (const s of ev.sources) {
        if (!s || typeof s.url !== "string" || seen.has(s.url)) continue;
        seen.add(s.url);
        let host = s.url;
        try { host = new URL(s.url).hostname.replace(/^www\./, ""); } catch { /* keep raw */ }
        rows.push({ key: `web-${s.url}`, name: s.title || host, meta: host, url: s.url });
      }
    }
  }
  return rows;
}

function extKind(name: string): "xlsx" | "pptx" {
  const n = name.toLowerCase();
  if (n.endsWith(".xlsx") || n.endsWith(".xls") || n.endsWith(".csv")) return "xlsx";
  return "pptx";
}

function CollapsibleSection({
  title,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  count: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <>
      <button type="button" className="ws-sect-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="ws-sect-label">{title}</span>
        <span className={`ws-sect-count ${count > 0 ? "has-items" : ""}`}>{count}</span>
        <ChevronDown size={13} className={`ws-sect-chev ${open ? "open" : ""}`} />
      </button>
      {open && children}
    </>
  );
}

function WebSourceIcon({ url }: { url: string }) {
  const [failed, setFailed] = useState(false);
  const faviconUrl = useMemo(() => {
    try {
      return `https://www.google.com/s2/favicons?domain=${new URL(url).hostname}&sz=32`;
    } catch {
      return null;
    }
  }, [url]);

  return (
    <div className="s-ic web">
      {faviconUrl && !failed ? (
        <img src={faviconUrl} alt="" className="s-ic-fav" onError={() => setFailed(true)} />
      ) : (
        <Globe size={14} />
      )}
    </div>
  );
}
