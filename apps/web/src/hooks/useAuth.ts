"use client";

import { useState, useEffect, useCallback } from "react";
import { AuthUser, AuthState, initAuth, logout as authLogout } from "@/lib/auth";
import { api, setToken } from "@/lib/api";

export function useAuth() {
  const [authState, setAuthState] = useState<AuthState>({
    user: null,
    isAuthenticated: false,
  });

  useEffect(() => {
    const state = initAuth();
    setAuthState(state);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    setToken(result.token);
    setAuthState({
      user: result.user as AuthUser,
      isAuthenticated: true,
    });
    return result;
  }, []);

  const logout = useCallback(() => {
    authLogout();
    setAuthState({ user: null, isAuthenticated: false });
  }, []);

  return {
    user: authState.user,
    isAuthenticated: authState.isAuthenticated,
    login,
    logout,
  };
}