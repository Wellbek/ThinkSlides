"use client";

import { useMemo } from "react";
import { useChatStore, type GeneratedFile } from "@/stores/chat-store";
import { collectPreviewSlides } from "@/lib/slides";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { FileSpreadsheet, Presentation, Globe, Plus, Monitor, Download } from "lucide-react";

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
  const sources = useMemo(() => collectSources(activeMessages, activeFiles), [activeMessages, activeFiles]);
  const generatedFiles = useMemo(
    () => activeFiles.map((f) => ({ ...f, downloadUrl: buildDownloadUrl(f.file_url) })),
    [activeFiles]
  );

  const created = session?.created_at ? new Date(session.created_at).toISOString().slice(0, 10) : "-";

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
      <div className="ws-sect-label">Sources</div>
      <div className="ws-sources">
        {sources.length === 0 && <div className="ws-empty-note">No sources attached yet.</div>}
        {sources.map((src) => (
          <div className="ws-src" key={src.key} title={src.name}>
            <div className={`s-ic ${src.kind}`}>
              {src.kind === "xlsx" && <FileSpreadsheet size={14} />}
              {src.kind === "pptx" && <Presentation size={14} />}
              {src.kind === "web" && <Globe size={14} />}
            </div>
            <div className="s-body">
              <div className="s-name">{src.name}</div>
              <div className="s-meta">{src.meta}</div>
            </div>
            <span className="s-tag">used</span>
          </div>
        ))}
        <div className="ws-src-add"><Plus size={13} /> Attach doc or data</div>
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

      {generatedFiles.length > 0 && (
        <>
          <div className="ws-divider" />
          <div className="ws-sect-label">Downloads</div>
          <div className="ws-sources">
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
        </>
      )}
    </div>
  );
}

interface SourceRow { key: string; name: string; meta: string; kind: "xlsx" | "pptx" | "web" }

function collectSources(messages: any[], files: GeneratedFile[]): SourceRow[] {
  const rows: SourceRow[] = [];
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
  // Generated files
  for (const f of files) {
    if (seen.has(f.filename)) continue;
    seen.add(f.filename);
    rows.push({
      key: `file-${f.id}`,
      name: f.filename,
      meta: f.slide_count ? `${f.slide_count} slides` : "generated",
      kind: extKind(f.filename),
    });
  }
  // Website URLs referenced in agent output
  const urlRe = /https?:\/\/[^\s"'<>)\]]+/g;
  for (const m of messages) {
    for (const ev of m.agentEventStream || []) {
      const text = typeof ev.content === "string" ? ev.content
        : typeof ev.message === "string" ? ev.message
        : typeof ev.result === "string" ? ev.result : "";
      let match: RegExpExecArray | null;
      while ((match = urlRe.exec(text)) !== null) {
        const url = match[0];
        if (seen.has(url)) continue;
        seen.add(url);
        let host = url;
        try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { /* keep raw */ }
        rows.push({ key: `web-${url}`, name: host, meta: url.length > 60 ? url.slice(0, 60) + "..." : url, kind: "web" });
      }
    }
  }
  return rows;
}

function extKind(name: string): "xlsx" | "pptx" | "web" {
  const n = name.toLowerCase();
  if (n.endsWith(".xlsx") || n.endsWith(".xls") || n.endsWith(".csv")) return "xlsx";
  if (n.endsWith(".pptx") || n.endsWith(".ppt")) return "pptx";
  return "pptx";
}
