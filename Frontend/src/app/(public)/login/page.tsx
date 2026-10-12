'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@/contexts/AuthContext';
import { ShieldCheck, Mail, Lock, LogIn, Loader2 } from 'lucide-react';
import { ApiError } from '@/lib/api/client';

export default function LoginPage() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      await signIn(email, password);
      // signIn routes by role; pending/rejected accounts go to /pending.
    } catch (err) {
      if (err instanceof ApiError && err.code === 'AUTH_NOT_CONFIGURED') {
        setError('Sign-in is unavailable: the server authentication is not configured.');
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Could not sign in. Please try again.');
      }
      setIsSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen w-full bg-white">
      {/* Left side: image and branding overlay (local asset, no hot-linking) */}
      <div className="relative hidden w-0 flex-1 lg:block">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          className="absolute inset-0 h-full w-full object-cover"
          src="/images/nitttr-campus.jpg"
          alt="NITTTR Chennai Campus"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-slate-900/90 via-slate-900/60 to-slate-900/30 mix-blend-multiply" />

        <div className="absolute bottom-0 left-0 p-16 text-white max-w-2xl">
          <div className="mb-6 flex gap-3 items-center">
            <div className="p-2.5 bg-blue-600/20 backdrop-blur-md rounded-xl border border-blue-400/30">
              <ShieldCheck size={32} className="text-blue-400" />
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight">NITTTR Chennai</h1>
          </div>
          <p className="text-4xl font-bold leading-tight mb-4 text-white">
            Smart Campus Asset & Command Center
          </p>
          <p className="text-lg text-slate-200">
            Welcome to the National Institute of Technical Teachers Training and Research unified command interface. Manage facilities, track maintenance, and oversee equipment with real-time intelligence.
          </p>
        </div>
      </div>

      {/* Right side: Login form */}
      <div className="flex flex-1 flex-col justify-center px-4 py-12 sm:px-6 lg:flex-none lg:px-20 xl:px-24">
        <div className="mx-auto w-full max-w-md">

          <div className="lg:hidden mb-10 flex flex-col items-center">
            <div className="w-14 h-14 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center border border-blue-100 shadow-sm mb-4">
              <ShieldCheck size={32} />
            </div>
            <h2 className="text-2xl font-bold tracking-tight text-slate-900">NITTTR Command Center</h2>
          </div>

          <div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">
              Sign in
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              Please enter your credentials to access your dashboard.
            </p>
          </div>

          <div className="mt-8">
            <div className="mt-6">
              <form onSubmit={handleLogin} className="space-y-6">
                {error && (
                  <div className="bg-red-50 text-red-600 text-sm p-4 rounded-xl border border-red-200 text-center font-medium shadow-sm">
                    {error}
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium text-slate-900 mb-1">
                    Email address
                  </label>
                  <div className="relative rounded-xl shadow-sm">
                    <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
                      <Mail className="h-5 w-5 text-slate-400" />
                    </div>
                    <input
                      type="email"
                      value={email}
                      onChange={e => setEmail(e.target.value)}
                      placeholder="Enter your email"
                      autoComplete="email"
                      className="block w-full rounded-xl border-0 py-2.5 pl-11 text-slate-900 ring-1 ring-inset ring-slate-200 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-blue-600 sm:text-sm bg-slate-50 focus:bg-white transition-all shadow-sm"
                      required
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="block text-sm font-medium text-slate-900 mb-1">
                    Password
                  </label>
                  <div className="relative rounded-xl shadow-sm">
                    <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
                      <Lock className="h-5 w-5 text-slate-400" />
                    </div>
                    <input
                      type="password"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="••••••••"
                      autoComplete="current-password"
                      className="block w-full rounded-xl border-0 py-2.5 pl-11 text-slate-900 ring-1 ring-inset ring-slate-200 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-blue-600 sm:text-sm bg-slate-50 focus:bg-white transition-all shadow-sm"
                      required
                    />
                  </div>
                </div>

                <div>
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="flex w-full justify-center items-center rounded-xl bg-blue-600 px-3 py-3 text-sm font-bold text-white shadow-md hover:bg-blue-700 hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 transition-all active:scale-[0.98] disabled:cursor-wait disabled:opacity-70"
                  >
                    {isSubmitting ? <Loader2 className="w-5 h-5 mr-2 animate-spin" /> : <LogIn className="w-5 h-5 mr-2" />}
                    {isSubmitting ? 'Opening dashboard...' : 'Sign in to Dashboard'}
                  </button>
                </div>
              </form>

              <p className="mt-5 text-center text-sm text-slate-500">
                Need an account? <Link href="/signup" className="font-semibold text-blue-600 hover:text-blue-700">Create an account</Link>
              </p>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
