'use client';

import { useEffect } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useChatStore } from '@/stores/chat-store';
import { AppShell } from '@/components/layout/AppShell';

export default function HomePage() {
  const { isAuthenticated, isLoading: authLoading } = useAuth();
  const fetchSessions = useChatStore((s) => s.fetchSessions);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSessions();
    }
  }, [isAuthenticated, fetchSessions]);

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-gray-500">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    if (typeof window !== 'undefined') {
      window.location.href = '/auth/login';
    }
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-gray-500">Redirecting to login...</div>
      </div>
    );
  }

  return <AppShell />;
}