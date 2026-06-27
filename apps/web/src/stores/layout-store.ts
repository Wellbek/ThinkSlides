// stores/layout-store.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

export const COLLAPSED_WIDTH = 48;

interface LayoutState {
  sidebarWidth: number;
  sidebarCollapsed: boolean;
  previewWidth: number;
  previewCollapsed: boolean;
  chatCollapsed: boolean;
  setSidebarWidth: (width: number) => void;
  toggleSidebar: () => void;
  setPreviewWidth: (width: number) => void;
  togglePreview: () => void;
  toggleChat: () => void;
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      sidebarWidth: 280,
      sidebarCollapsed: false,
      previewWidth: 320,
      previewCollapsed: false,
      chatCollapsed: false,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setPreviewWidth: (width) => set({ previewWidth: width }),
      togglePreview: () => set((state) => ({ previewCollapsed: !state.previewCollapsed })),
      toggleChat: () => set((state) => ({ chatCollapsed: !state.chatCollapsed })),
    }),
    {
      name: "layout-storage",
      // Add migrate to silence warning (even if no version change)
      migrate: (persistedState, version) => persistedState,
    }
  )
);