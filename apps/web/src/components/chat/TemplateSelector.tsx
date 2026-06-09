"use client";

import { useState, useEffect, useRef } from "react";
import { useChatStore } from "@/stores/chat-store";
import { ChevronDown, Presentation, Plus, Loader2, X } from "lucide-react";

export function TemplateSelector() {
  const { templates, activeSessionId, setSessionTemplate, fetchTemplates, uploadTemplate } = useChatStore();
  const activeSession = useChatStore((s) =>
    s.activeSessionId ? s.sessions.find((ses) => ses.id === s.activeSessionId) : null
  );
  const [isOpen, setIsOpen] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [analysisText, setAnalysisText] = useState("");
  const [uploadProgress, setUploadProgress] = useState<{ stage: string; message: string }>({ stage: "idle", message: "" });
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  const currentTemplateId = activeSession?.template_id || null;

  const handleSelect = (templateId: string | null) => {
    if (activeSessionId) {
      setSessionTemplate(activeSessionId, templateId);
    }
    setIsOpen(false);
  };

  const getTemplateDisplay = () => {
    if (!currentTemplateId) return "No template (default theme)";
    const template = templates.find((t) => t.id === currentTemplateId);
    return template ? template.name : "Unknown template";
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.match(/\.(pptx|potx|pdf)$/i)) {
      alert("Only PPTX, POTX, or PDF files are allowed.");
      return;
    }

    setIsOpen(false);
    setUploadModalOpen(true);
    setAnalysisText("");
    setUploadProgress({ stage: "starting", message: "Preparing upload..." });

    const formData = new FormData();
    formData.append("file", file);
    formData.append("name", file.name.replace(/\.[^/.]+$/, ""));

    try {
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const token = localStorage.getItem("auth_token");
      const response = await fetch("/api/ai/v1/templates/upload-stream", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
        signal: controller.signal,
      });

      if (!response.ok) throw new Error(`Upload failed: ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.status === "processing") {
                setUploadProgress({ stage: "processing", message: data.message });
              } else if (data.progress !== undefined) {
                setUploadProgress({ stage: "extracting", message: data.message });
              } else if (data.chunk) {
                setAnalysisText((prev) => prev + data.chunk);
                setUploadProgress({ stage: "analyzing", message: "LLM analyzing template..." });
              } else if (data.template_id) {
                setUploadProgress({ stage: "done", message: "Template saved!" });
                await fetchTemplates();
                if (activeSessionId) {
                  await setSessionTemplate(activeSessionId, data.template_id);
                }
                setTimeout(() => {
                  setUploadModalOpen(false);
                  setAnalysisText("");
                  setUploadProgress({ stage: "idle", message: "" });
                }, 1500);
              } else if (data.error) {
                throw new Error(data.error);
              }
            } catch (err) {
              console.error("Failed to parse SSE data:", err);
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name === "AbortError") {
        console.log("Upload aborted");
      } else {
        console.error("Upload error:", err);
        alert(`Upload failed: ${err.message}`);
        setUploadModalOpen(false);
      }
    } finally {
      setIsUploading(false);
      abortControllerRef.current = null;
    }
  };

  const cancelUpload = () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    setUploadModalOpen(false);
    setAnalysisText("");
    setUploadProgress({ stage: "idle", message: "" });
  };

  return (
    <>
      <div className="relative">
        <div className="flex items-center gap-1">
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="flex items-center gap-2 px-3 py-1.5 bg-surface-50 border border-surface-100 rounded-lg text-xs text-gray-300 hover:border-brand-500/30 transition-colors"
          >
            <Presentation size={14} />
            <span>{getTemplateDisplay()}</span>
            <ChevronDown size={12} />
          </button>
          <button
            onClick={() => document.getElementById("template-file-input")?.click()}
            className="p-1.5 bg-surface-50 border border-surface-100 rounded-lg text-gray-400 hover:text-brand-400 hover:border-brand-500/30 transition-colors"
            title="Upload new template"
          >
            <Plus size={14} />
          </button>
          <input
            id="template-file-input"
            type="file"
            accept=".pptx,.potx,.pdf"
            className="hidden"
            onChange={handleFileUpload}
          />
        </div>

        {isOpen && (
          <div className="absolute right-0 top-full mt-1 w-64 bg-surface-50 border border-surface-100 rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto">
            <button
              onClick={() => handleSelect(null)}
              className="w-full text-left px-3 py-2 text-sm text-gray-300 hover:bg-surface-100 rounded-t-lg"
            >
              No template (default theme)
            </button>
            {templates.map((tpl) => (
              <button
                key={tpl.id}
                onClick={() => handleSelect(tpl.id)}
                className="w-full text-left px-3 py-2 text-sm text-gray-300 hover:bg-surface-100 flex items-center gap-2"
              >
                <Presentation size={12} className="text-brand-400 shrink-0" />
                <div className="min-w-0">
                  <p className="truncate">{tpl.name}</p>
                  <p className="text-xs text-gray-500">
                    v{tpl.version} · {tpl.tags?.join(", ") || "no tags"}
                  </p>
                </div>
              </button>
            ))}
            {templates.length === 0 && (
              <p className="px-3 py-4 text-xs text-gray-500 text-center">
                No templates yet. Click the + button to upload one.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Upload Modal with Streaming Analysis */}
      {uploadModalOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface-50 border border-surface-100 rounded-xl w-full max-w-md p-6 shadow-2xl">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-semibold text-white">Analyzing Template</h3>
              <button onClick={cancelUpload} className="text-gray-400 hover:text-white">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <div className="flex items-center gap-3 text-sm">
                {uploadProgress.stage !== "done" && uploadProgress.stage !== "idle" && (
                  <Loader2 size={16} className="animate-spin text-brand-400" />
                )}
                <span className="text-gray-300">{uploadProgress.message || "Starting..."}</span>
              </div>
              {analysisText && (
                <div className="bg-surface-100 rounded-lg p-3 max-h-60 overflow-y-auto">
                  <p className="text-xs text-gray-300 whitespace-pre-wrap">{analysisText}</p>
                </div>
              )}
              {uploadProgress.stage === "done" && (
                <div className="text-green-400 text-sm">Template successfully added!</div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}