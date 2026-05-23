'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks/useAuth';

export default function LoginPage() {
  const { login, register, devLogin } = useAuth();
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      if (isRegister) {
        await register(email, password, displayName || undefined);
      } else {
        await login(email, password);
      }
      // On success the auth hook updates state and the app redirects
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  const handleDevLogin = async () => {
    setError('');
    setLoading(true);
    try {
      await devLogin();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Dev login failed');
    } finally {
      setLoading(false);
    }
  };

  const devModeEnabled = process.env.NEXT_PUBLIC_DEV_MODE === 'true';

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0a0a0f] px-4">
      <div className="w-full max-w-sm space-y-6">
        {/* Logo / Brand */}
        <div className="text-center">
          <h1 className="text-2xl font-bold text-white">ThinkSlides</h1>
          <p className="mt-1 text-sm text-gray-400">AI-powered presentations</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {isRegister && (
            <div>
              <label htmlFor="displayName" className="block text-sm font-medium text-gray-300 mb-1">
                Display name <span className="text-gray-500">(optional)</span>
              </label>
              <input
                id="displayName"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your name"
                className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          )}

          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-1">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-300 mb-1">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              placeholder="••••••••"
              className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            {isRegister && (
              <p className="mt-1 text-xs text-gray-500">Minimum 8 characters</p>
            )}
          </div>

          {error && (
            <div className="rounded-md bg-red-950/30 border border-red-500/20 px-3 py-2 text-sm text-red-300">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? 'Please wait…' : isRegister ? 'Create account' : 'Sign in'}
          </button>
        </form>

        {/* Toggle register / login */}
        <p className="text-center text-sm text-gray-400">
          {isRegister ? (
            <>
              Already have an account?{' '}
              <button
                onClick={() => { setIsRegister(false); setError(''); }}
                className="text-indigo-400 hover:text-indigo-300 underline"
              >
                Sign in
              </button>
            </>
          ) : (
            <>
              Don&apos;t have an account?{' '}
              <button
                onClick={() => { setIsRegister(true); setError(''); }}
                className="text-indigo-400 hover:text-indigo-300 underline"
              >
                Create one
              </button>
            </>
          )}
        </p>

        {/* Dev login shortcut — only shown when NEXT_PUBLIC_DEV_MODE=true */}
        {devModeEnabled && (
          <div className="border-t border-gray-700 pt-4">
            <button
              onClick={handleDevLogin}
              disabled={loading}
              className="w-full rounded-md border border-yellow-500/30 bg-yellow-950/20 px-4 py-2 text-sm font-medium text-yellow-300 hover:bg-yellow-950/30 disabled:opacity-50 transition-colors"
            >
              ⚡ Dev Login (dev@thinkslices.local)
            </button>
            <p className="mt-1 text-center text-xs text-gray-500">
              Auto-creates a dev account. Bookmark{' '}
              <code className="text-yellow-400/70">?dev=1</code> to auto-login.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}