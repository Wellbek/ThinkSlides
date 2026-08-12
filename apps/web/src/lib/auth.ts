/**
 * Authentication utilities for JWT token management.
 */

import { setToken, clearToken } from "./api";

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  organisationId: string | null; // nullable — user may not have an org yet
  role: string;
}

export interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;
}

/**
 * Decode one base64url JWT segment.
 *
 * `atob` speaks standard base64 and throws on the `-` and `_` that base64url
 * substitutes for `+` and `/`. Those turn up in most real payloads, so decoding
 * a segment directly with `atob` works only by luck. It also returns one
 * latin1 char per byte, which mangles any non-ASCII claim, hence the explicit
 * UTF-8 decode.
 */
function decodeBase64Url(segment: string): string {
  const base64 = segment.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/**
 * Parse a JWT token to extract user info (without verification — that's server-side).
 *
 * Returns null for a token that is malformed or past its `exp`. Signature
 * verification stays server-side, but an expired token cannot identify anyone,
 * and callers use this result to decide whether a stored session is still live.
 */
export function parseToken(token: string): AuthUser | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;

    const payload = JSON.parse(decodeBase64Url(parts[1]));

    if (typeof payload.exp === "number" && payload.exp * 1000 <= Date.now()) {
      return null;
    }

    const orgId = payload.org_id || null;
    return {
      id: payload.user_id || "",
      email: payload.email || "",
      displayName: payload.display_name || "",
      organisationId: orgId === "" ? null : orgId,
      role: payload.role || "",
    };
  } catch {
    return null;
  }
}

/**
 * Initialize auth state from localStorage token.
 */
export function initAuth(): AuthState {
  if (typeof window === "undefined") {
    return { user: null, isAuthenticated: false };
  }

  const token = localStorage.getItem("auth_token");
  if (!token) {
    return { user: null, isAuthenticated: false };
  }

  const user = parseToken(token);
  if (!user) {
    clearToken();
    return { user: null, isAuthenticated: false };
  }

  return { user, isAuthenticated: true };
}

/**
 * Log out by clearing the stored token.
 */
export function logout(): void {
  clearToken();
  if (typeof window !== "undefined") {
    window.location.href = "/auth/login";
  }
}

/**
 * Check if dev mode is enabled (via NEXT_PUBLIC_DEV_MODE env var).
 */
export function isDevMode(): boolean {
  return process.env.NEXT_PUBLIC_DEV_MODE === "true";
}