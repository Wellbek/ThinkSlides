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
 * Parse a JWT token to extract user info (without verification — that's server-side).
 */
export function parseToken(token: string): AuthUser | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;

    const payload = JSON.parse(atob(parts[1]));
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