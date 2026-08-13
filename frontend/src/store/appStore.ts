import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api } from "@/api/client";
import type { ClusterPublic } from "@/api/types";

interface AppState {
  selectedClusterId: string | null;
  setSelectedClusterId: (id: string | null) => void;
  // The registered-clusters list used to live as separate local state in
  // both App.tsx (sidebar dropdown/status) and ClustersPage.tsx (the
  // table) -- registering/deleting a cluster on one only refreshed the
  // other's copy if you happened to navigate back to it, so the sidebar
  // could show "no cluster selected" / an empty dropdown even while a
  // valid cluster really was selected and other pages were using it fine.
  // Single shared source of truth here fixes that.
  clusters: ClusterPublic[];
  refreshClusters: () => Promise<void>;
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      selectedClusterId: null,
      setSelectedClusterId: (id) => set({ selectedClusterId: id }),
      clusters: [],
      refreshClusters: async () => {
        const list = await api.listClusters();
        const { selectedClusterId } = get();
        // Auto-heal: if nothing is selected yet, or the previously-selected
        // id (possibly restored from a prior session) no longer exists,
        // fall back to the most recently registered cluster rather than
        // silently showing an empty/stale selection.
        const stillExists = list.some((c) => c.cluster_id === selectedClusterId);
        set({
          clusters: list,
          selectedClusterId: stillExists ? selectedClusterId : (list[0]?.cluster_id ?? null),
        });
      },
    }),
    {
      name: "coa-app-store",
      // Only persist the selection across reloads -- the cluster list
      // itself is always re-fetched fresh on load.
      partialize: (state) => ({ selectedClusterId: state.selectedClusterId }),
    },
  ),
);
