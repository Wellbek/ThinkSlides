"use client";

import { useEffect, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Trash2, Upload, Plus, Loader2 } from "lucide-react";

export default function SettingsPage() {
  const { templates, fetchTemplates, deleteTemplate, uploadTemplate } = useChatStore();
  const [isUploading, setIsUploading] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [analysisText, setAnalysisText] = useState("");
  const [uploadProgress, setUploadProgress] = useState("");
  const fileInputRef = useState<HTMLInputElement | null>(null);

  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.match(/\.(pptx|potx|pdf)$/i)) {
      alert("Only PPTX, POTX, or PDF files are allowed.");
      return;
    }

    setUploadModalOpen(true);
    setAnalysisText("");
    setUploadProgress("Starting upload...");

    const formData = new FormData();
    formData.append("file", file);
    formData.append("name", file.name.replace(/\.[^/.]+$/, ""));

    try {
      const token = localStorage.getItem("auth_token");
      const response = await fetch("/api/ai/v1/templates/upload-stream", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
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
            const data = JSON.parse(line.slice(6));
            if (data.status) setUploadProgress(data.message);
            else if (data.progress !== undefined) setUploadProgress(data.message);
            else if (data.chunk) setAnalysisText((prev) => prev + data.chunk);
            else if (data.template_id) {
              setUploadProgress("Template saved! Refreshing list...");
              await fetchTemplates();
              setTimeout(() => setUploadModalOpen(false), 1500);
            } else if (data.error) throw new Error(data.error);
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      alert(`Upload failed: ${err.message}`);
      setUploadModalOpen(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (confirm("Delete this template?")) {
      await deleteTemplate(id);
    }
  };

  return (
    <div className="max-w-4xl mx-auto py-8 px-4">
      <h1 className="text-heading-lg font-medium text-paper mb-6">Settings</h1>

      <section className="bg-charcoal border border-iron rounded-lg p-6 shadow-subtle">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-heading-sm font-medium text-paper">Templates</h2>
          <button
            onClick={() => document.getElementById("template-upload")?.click()}
            className="btn-outlined"
          >
            <Upload size={14} />
            Upload Template
          </button>
          <input
            id="template-upload"
            type="file"
            accept=".pptx,.potx,.pdf"
            className="hidden"
            onChange={handleFileUpload}
          />
        </div>

        {templates.length === 0 ? (
          <p className="text-fog text-body">No templates yet. Upload one above.</p>
        ) : (
          <div className="space-y-2">
            {templates.map((tpl) => (
              <div key={tpl.id} className="flex items-center justify-between p-3 bg-graphite rounded-lg border border-iron">
                <div>
                  <p className="font-medium text-paper text-body">{tpl.name}</p>
                  <p className="text-caption text-fog font-mono">v{tpl.version} · {tpl.tags?.join(", ") || "no tags"}</p>
                </div>
                <button
                  onClick={() => handleDelete(tpl.id)}
                  className="p-1.5 rounded-md hover:bg-error/20 text-fog hover:text-error transition-colors"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Upload Modal (same as in TemplateSelector) */}
      {uploadModalOpen && (
        <div className="fixed inset-0 bg-void/50 flex items-center justify-center z-50 backdrop-blur-sm">
          <div className="bg-charcoal border border-iron rounded-lg w-full max-w-md p-6 shadow-subtle">
            <h3 className="text-heading-sm font-medium text-paper mb-4">Analyzing Template</h3>
            <div className="space-y-4">
              <div className="flex items-center gap-3 text-sm">
                <Loader2 size={16} className="animate-spin text-ice" />
                <span className="text-paper">{uploadProgress}</span>
              </div>
              {analysisText && (
                <div className="bg-graphite rounded-lg p-3 max-h-60 overflow-y-auto">
                  <p className="text-caption text-paper whitespace-pre-wrap font-mono">{analysisText}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
