/**
 * Authentication utilities for JWT token management.
 */

import { setToken, clearToken } from "./api";

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  organisationId: string;
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
    return {
      id: payload.user_id || "",
      email: payload.email || "",
      displayName: payload.display_name || "",
      organisationId: payload.org_id || "",
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

  const token = localStorage.getItem("slideagent_token");
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