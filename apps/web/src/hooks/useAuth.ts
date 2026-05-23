'use client';

import { useState, useEffect, useCallback } from 'react';
import { setToken, clearToken } from '@/lib/api';

interface User {
  id: string;
  email: string;
  display_name: string;
  organisation_id: string | null;
  role: string;
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

  // Hydrate from localStorage on mount
  useEffect(() => {
    const storedToken = localStorage.getItem('auth_token');
    const storedUser = localStorage.getItem('auth_user');
    if (storedToken && storedUser) {
      try {
        setTokenState(storedToken);
        setUser(JSON.parse(storedUser));
      } catch {
        localStorage.removeItem('auth_token');
        localStorage.removeItem('auth_user');
      }
    }
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