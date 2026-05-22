/**
 * API client for communicating with the orchestrator and AI service.
 */

// On client side, use Next.js API routes (proxy)
// On server side, use the service URLs directly
const ORCHESTRATOR_URL =
  typeof window !== "undefined"
    ? "/api/orchestrator"  // Use Next.js proxy route on client
    : process.env.NEXT_PUBLIC_API_URL || "http://orchestrator:8080";

const AI_URL =
  typeof window !== "undefined"
    ? "/api/ai"  // Use Next.js proxy route on client
    : process.env.NEXT_PUBLIC_AI_URL || "http://ai-service:8000";

async function request(
  baseUrl: string,
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const url = `${baseUrl}${path}`;
  console.log('API Request:', url, options.method || 'GET');

  const res = await fetch(url, {
    ...options,
    headers,
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "Unknown error");
    console.error('API Error:', res.status, body);
    throw new Error(`API error ${res.status}: ${body}`);
  }

  return res;
}

export const api = {
  // ── Auth ──────────────────────────────────────
  login: (email: string, password: string) =>
    request(ORCHESTRATOR_URL, "/api/auth/login", {  // Added /api prefix
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then((r) => r.json()),

  // ── Sessions ──────────────────────────────────
  listSessions: () =>
    request(ORCHESTRATOR_URL, "/api/sessions").then((r) => r.json()),  // Added /api prefix

  getSession: (id: string) =>
    request(ORCHESTRATOR_URL, `/api/sessions/${id}`).then((r) => r.json()),  // Added /api prefix

  createSession: (data: { title?: string; templateId?: string; brief?: string }) =>
    request(ORCHESTRATOR_URL, "/api/sessions", {  // Added /api prefix
      method: "POST",
      body: JSON.stringify(data),
    }).then((r) => r.json()),

  deleteSession: (id: string) =>
    request(ORCHESTRATOR_URL, `/api/sessions/${id}`, { method: "DELETE" }),  // Added /api prefix

  // ── Messages ──────────────────────────────────
  sendMessage: (sessionId: string, data: { content: string; attachments?: unknown[] }) =>
    request(ORCHESTRATOR_URL, `/api/sessions/${sessionId}/messages`, {  // Added /api prefix
      method: "POST",
      body: JSON.stringify(data),
    }).then((r) => r.json()),

  // ── Templates ─────────────────────────────────
  listTemplates: () =>
    request(AI_URL, "/templates").then((r) => r.json()),

  uploadTemplate: (formData: FormData) =>
    fetch(`${AI_URL}/templates`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },
      body: formData,
    }).then((r) => r.json()),

  // ── SSE Stream ────────────────────────────────
  sessionEvents: (sessionId: string): EventSource => {
    const token = getToken();
    // Use the proxy URL for EventSource as well - added /api prefix
    const url = `${ORCHESTRATOR_URL}/api/sessions/${sessionId}/events`;
    console.log('Creating EventSource:', url);
    return new EventSource(url);
  },

  // ── Generated Files ───────────────────────────
  downloadFile: (fileId: string) =>
    request(ORCHESTRATOR_URL, `/api/files/${fileId}`).then((r) => r.blob()),  // Added /api prefix
};

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("slideagent_token");
}

export function setToken(token: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem("slideagent_token", token);
  }
}

export function clearToken() {
  if (typeof window !== "undefined") {
    localStorage.removeItem("slideagent_token");
  }
}