import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ViewMode } from '../types';
import { appStoreLogger } from '../hooks/useLogger';

interface AppState {
  // UI State
  viewMode: ViewMode;
  sidebarCollapsed: boolean;
  theme: 'light' | 'dark';

  // Connection
  wsConnected: boolean;

  // Actions
  setViewMode: (mode: ViewMode) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleTheme: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
  setWsConnected: (connected: boolean) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      viewMode: 'monitor',
      sidebarCollapsed: false,
      theme: 'light',
      wsConnected: false,

      setViewMode: (mode) => {
        appStoreLogger.debug('[setViewMode] View mode changed', { mode });
        set({ viewMode: mode });
      },

      toggleSidebar: () => {
        appStoreLogger.debug('[toggleSidebar] Sidebar toggled');
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed }));
      },

      setSidebarCollapsed: (collapsed) => {
        appStoreLogger.debug('[setSidebarCollapsed] Sidebar collapsed set', { collapsed });
        set({ sidebarCollapsed: collapsed });
      },

      toggleTheme: () => {
        appStoreLogger.debug('[toggleTheme] Theme toggled');
        set((state) => ({
          theme: state.theme === 'light' ? 'dark' : 'light',
        }));
      },

      setTheme: (theme) => {
        appStoreLogger.debug('[setTheme] Theme set', { theme });
        set({ theme });
      },

      setWsConnected: (connected) => {
        if (connected) {
          appStoreLogger.info('[setWsConnected] WebSocket connected');
        } else {
          appStoreLogger.warn('[setWsConnected] WebSocket disconnected');
        }
        set({ wsConnected: connected });
      },
    }),
    {
      name: 'app-storage',
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        theme: state.theme,
      }),
    }
  )
);
