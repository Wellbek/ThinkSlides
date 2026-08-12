'use client';

import { useState, useEffect, useCallback } from 'react';
import { setToken, clearToken } from '@/lib/api';
import { parseToken } from '@/lib/auth';

interface User {
  id: string;
  email: string;
  display_name: string;
  organisation_id: string | null;
  role: string;
}

/** The stored profile, if it is present and still readable. */
function readStoredUser(): User | null {
  try {
    const raw = localStorage.getItem('auth_user');
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as User) : null;
  } catch {
    return null;
  }
}

/**
 * The profile the token itself carries. The token has no `display_name` claim,
 * so fall back to the local part of the email for something to show.
 */
function userFromToken(token: string): User | null {
  const claims = parseToken(token);
  if (!claims) return null;
  return {
    id: claims.id,
    email: claims.email,
    display_name: claims.displayName || claims.email.split('@')[0] || 'Account',
    organisation_id: claims.organisationId,
    role: claims.role,
  };
}

interface UseAuthReturn {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  devLogin: () => Promise<void>;
  logout: () => void;
}

export function useAuth(): UseAuthReturn {
  const [user, setUser] = useState<User | null>(null);
  const [tokenState, setTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Hydrate from localStorage on mount. The token is what authenticates the
  // session; `auth_user` is only a convenience copy of the profile, so a
  // missing or corrupt one is recovered from the token's own claims rather
  // than being treated as a logout.
  useEffect(() => {
    const storedToken = localStorage.getItem('auth_token');
    if (!storedToken) {
      setIsLoading(false);
      return;
    }

    const fromToken = userFromToken(storedToken);
    if (!fromToken) {
      // Malformed or expired - nothing here can authenticate a request.
      clearToken();
      setIsLoading(false);
      return;
    }

    setTokenState(storedToken);
    setUser(readStoredUser() ?? fromToken);
    setIsLoading(false);
  }, []);

  // Auto dev-login via ?dev=1 query param
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (process.env.NEXT_PUBLIC_DEV_MODE !== 'true') return;
    if (tokenState) return; // already authenticated

    const params = new URLSearchParams(window.location.search);
    if (params.get('dev') !== '1') return;

    const doDevLogin = async () => {
      try {
        const res = await fetch('/api/orchestrator/auth/dev-login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        if (res.ok) {
          const data = await res.json();
          setToken(data.token);
          localStorage.setItem('auth_user', JSON.stringify(data.user));
          setTokenState(data.token);
          setUser(data.user);
          // Clean up the URL
          params.delete('dev');
          const newUrl = `${window.location.pathname}${params.toString() ? '?' + params.toString() : ''}`;
          window.history.replaceState({}, '', newUrl);
        }
      } catch {
        // Silently fail — user can click the dev login button instead
      }
    };
    doDevLogin();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch('/api/orchestrator/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Login failed');
    }

    const data = await res.json();
    setToken(data.token);
    localStorage.setItem('auth_user', JSON.stringify(data.user));
    setTokenState(data.token);
    setUser(data.user);
  }, []);

  const register = useCallback(async (email: string, password: string, displayName?: string) => {
    const res = await fetch('/api/orchestrator/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, display_name: displayName || '' }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Registration failed');
    }

    const data = await res.json();
    setToken(data.token);
    localStorage.setItem('auth_user', JSON.stringify(data.user));
    setTokenState(data.token);
    setUser(data.user);
  }, []);

  const devLogin = useCallback(async () => {
    const res = await fetch('/api/orchestrator/auth/dev-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Dev login failed');
    }

    const data = await res.json();
    setToken(data.token);
    localStorage.setItem('auth_user', JSON.stringify(data.user));
    setTokenState(data.token);
    setUser(data.user);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
    window.location.href = '/auth/login';
  }, []);

  return {
    user,
    token: tokenState,
    isLoading,
    isAuthenticated: !!tokenState && !!user,
    login,
    register,
    devLogin,
    logout,
  };
}