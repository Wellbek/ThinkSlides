/**
 * Build an authenticated download URL for a generated file.
 *
 * Generated files are served by the orchestrator behind JWT auth. Browser
 * navigations (<a download>, window.open) cannot set an Authorization
 * header, so the JWT is appended as a `?token=` query param - the same
 * fallback the Go SSE middleware uses for EventSource connections.
 */
export function buildDownloadUrl(fileUrl: string | undefined): string {
  if (!fileUrl) return "#";
  const token =
    typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
  if (!token) return fileUrl;
  const sep = fileUrl.includes("?") ? "&" : "?";
  return `${fileUrl}${sep}token=${encodeURIComponent(token)}`;
}

export function formatFileSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
