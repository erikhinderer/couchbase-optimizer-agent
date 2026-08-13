import type {
  AnalysisRunSummary, ChatResponse, ClusterCreate, ClusterPublic,
  ClusterSnapshot, Finding, MemoryItem,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || "ws://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

// Deliberately no Content-Type here -- the browser sets
// multipart/form-data with the correct boundary itself when the body is a
// FormData instance. Used for the support-bundle upload, which is a file
// plus a couple of form fields rather than a JSON payload.
async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, { method: "POST", body: formData });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export const api = {
  // clusters
  listClusters: () => request<ClusterPublic[]>("/api/clusters"),
  getCluster: (id: string) => request<ClusterPublic>(`/api/clusters/${id}`),
  registerCluster: (payload: ClusterCreate) =>
    request<ClusterPublic>("/api/clusters", { method: "POST", body: JSON.stringify(payload) }),
  testConnection: (id: string) => request<ClusterPublic>(`/api/clusters/${id}/test-connection`, { method: "POST" }),
  deleteCluster: (id: string) => request<{ deleted: string }>(`/api/clusters/${id}`, { method: "DELETE" }),
  uploadSupportBundle: (name: string, kind: string, file: File) => {
    const formData = new FormData();
    formData.append("name", name);
    formData.append("kind", kind);
    formData.append("file", file);
    return requestForm<ClusterPublic>("/api/clusters/upload-bundle", formData);
  },

  // analysis
  runAnalysis: (clusterId: string) =>
    request<AnalysisRunSummary>(`/api/analysis/${clusterId}/run`, { method: "POST" }),
  snapshot: (clusterId: string) => request<ClusterSnapshot>(`/api/analysis/${clusterId}/snapshot`),

  // findings
  listFindings: (clusterId?: string) =>
    request<Finding[]>(`/api/findings${clusterId ? `?cluster_id=${clusterId}` : ""}`),
  getFinding: (id: string) => request<Finding>(`/api/findings/${id}`),
  approveFinding: (id: string, approved_by: string, note?: string) =>
    request<Finding>(`/api/findings/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ finding_id: id, approved_by, note, confirm_reviewed: true }),
    }),
  rejectFinding: (id: string, rejected_by: string, reason?: string) =>
    request<Finding>(`/api/findings/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ finding_id: id, rejected_by, reason }),
    }),
  applyFinding: (id: string) => request<Finding>(`/api/findings/${id}/apply`, { method: "POST" }),

  // chat
  chat: (message: string, clusterId?: string) =>
    request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify({ message, cluster_id: clusterId }) }),

  // memory
  listMemory: (tier: string, clusterId?: string) =>
    request<MemoryItem[]>(`/api/memory?tier=${tier}${clusterId ? `&cluster_id=${clusterId}` : ""}`),

  // health
  health: () => request<{ status: string; app: string }>("/api/health"),
};
