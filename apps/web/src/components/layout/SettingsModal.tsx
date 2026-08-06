"use client";

import { useEffect, useCallback, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Settings, Trash2, Upload, Cpu, LayoutTemplate } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { AISettingsSection, AISettingsFooter } from "@/components/settings/AISettingsSection";

type Tab = "models" | "templates";

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "models", label: "AI models", icon: <Cpu size={13} /> },
  { id: "templates", label: "Templates", icon: <LayoutTemplate size={13} /> },
];

export function SettingsModal() {
  const { settingsOpen, setSettingsOpen, templates, fetchTemplates, deleteTemplate, uploadTemplate } = useChatStore();
  const [tab, setTab] = useState<Tab>("models");

  useEffect(() => {
    if (settingsOpen) {
      fetchTemplates();
    }
  }, [settingsOpen, fetchTemplates]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && settingsOpen) {
        setSettingsOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [settingsOpen, setSettingsOpen]);

  const handleBackdropClick = useCallback(() => {
    setSettingsOpen(false);
  }, [setSettingsOpen]);

  const handleDelete = async (id: string) => {
    if (confirm("Delete this template?")) {
      await deleteTemplate(id);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.match(/\.(pptx|potx|pdf)$/i)) {
      alert("Only PPTX, POTX, or PDF files are allowed.");
      return;
    }
    await uploadTemplate(file, file.name.replace(/\.[^/.]+$/, ""));
    e.target.value = "";
  };

  return (
    <AnimatePresence>
      {settingsOpen && (
        <>
          {/* Backdrop overlay */}
          <motion.div
            key="settings-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
            onClick={handleBackdropClick}
          />

          {/* Modal */}
          <motion.div
            key="settings-modal"
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
          >
            <div
              className="bg-charcoal border border-iron rounded-xl shadow-subtle-2 w-full max-w-2xl max-h-[85vh] flex flex-col pointer-events-auto"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-center justify-between px-6 py-4 border-b border-iron">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-ice/10 flex items-center justify-center">
                    <Settings size={16} className="text-ice" />
                  </div>
                  <h2 className="text-heading-sm font-medium text-paper">Settings</h2>
                </div>
                <button
                  onClick={() => setSettingsOpen(false)}
                  className="p-1.5 rounded-md hover:bg-graphite text-fog hover:text-paper transition-colors"
                  title="Close settings"
                >
                  <X size={18} />
                </button>
              </div>

              {/* Tabs */}
              <div className="flex items-center gap-1 px-6 pt-3 border-b border-iron">
                {TABS.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setTab(t.id)}
                    className={`flex items-center gap-1.5 px-3 py-2 -mb-px text-caption font-medium border-b-2 transition-colors ${
                      tab === t.id
                        ? "border-ice text-paper"
                        : "border-transparent text-fog hover:text-paper"
                    }`}
                  >
                    {t.icon}
                    {t.label}
                  </button>
                ))}
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto px-6 py-5">
                {tab === "models" ? (
                  <AISettingsSection />
                ) : (
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-body font-medium text-paper">Templates</h3>
                      <button
                        onClick={() => document.getElementById("settings-template-upload")?.click()}
                        className="btn-outlined !py-1.5 !px-3 !text-caption"
                      >
                        <Upload size={12} />
                        Upload
                      </button>
                      <input
                        id="settings-template-upload"
                        type="file"
                        accept=".pptx,.potx,.pdf"
                        className="hidden"
                        onChange={handleFileUpload}
                      />
                    </div>

                    <p className="text-caption text-fog mb-3">
                      Templates define the visual style and layout of generated presentations. Upload a PPTX or POTX file to create a new template.
                    </p>

                    {templates.length === 0 ? (
                      <div className="border border-dashed border-iron rounded-lg py-8 flex flex-col items-center gap-2 bg-charcoal">
                        <Upload size={20} className="text-fog" />
                        <p className="text-caption text-fog">No templates yet</p>
                        <p className="text-caption text-fog/60">Upload a PPTX file to get started</p>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {templates.map((tpl) => (
                          <div
                            key={tpl.id}
                            className="flex items-center justify-between p-3 bg-graphite/50 rounded-lg border border-iron hover:border-iron/80 transition-colors"
                          >
                            <div className="min-w-0 flex-1">
                              <p className="text-body text-paper truncate">{tpl.name}</p>
                              <p className="text-caption text-fog font-mono">
                                v{tpl.version} · {tpl.tags?.join(", ") || "no tags"}
                              </p>
                            </div>
                            <button
                              onClick={() => handleDelete(tpl.id)}
                              className="p-1.5 rounded-md hover:bg-error/20 text-fog hover:text-error transition-colors shrink-0 ml-2"
                              title="Delete template"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Anchored to the modal, outside the scrolling body, so content
                  cannot slide underneath it. */}
              {tab === "models" && <AISettingsFooter />}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
