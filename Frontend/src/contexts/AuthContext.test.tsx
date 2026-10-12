/**
 * Regression test for duplicate GET /api/v1/me calls (issue-2, Task A;
 * hardened by §14 sign-in/event coordination).
 *
 * Three triggers race in real sessions: React Strict Mode's double-mounted
 * effect (dev), the SIGNED_IN event fired while signIn()'s explicit
 * loadProfile() is still running — and worse, AFTER it has finished, when
 * the shared-promise guard has already cleared. Before the fix each caller
 * issued its own request — a full JWT + profile-lookup round trip apiece
 * (observed as a stream of /me calls climbing to 5234 ms while queued
 * behind the dashboard's concurrent queries).
 *
 * Under test:
 *   1. concurrent triggers share ONE in-flight request;
 *   2. signIn()'s explicit load coordinates with its own SIGNED_IN event
 *      (same session): exactly ONE /me per login;
 *   3. a TOKEN_REFRESHED with a NEW token during a pending load still
 *      reaches the server (no stale-session join);
 *   4. a later, sequential refresh still hits the server (no client-side
 *      profile caching — role/status always come from a fresh response).
 */
import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider, useAuth } from './AuthContext';

const shared = vi.hoisted(() => ({
  /** The onAuthStateChange callback registered by AuthProvider. */
  authCallback: null as ((event: string, session: unknown) => void) | null,
  /** Session returned by getSession (null = signed out, e.g. login page). */
  session: { access_token: 'tok' } as { access_token: string; user?: unknown } | null,
  /** Session returned by signInWithPassword. */
  signInSession: { access_token: 'tok-login' } as { access_token: string; user?: unknown } | null,
  /** Queued /me payloads (a test pushes role-null first, then PENDING). */
  nextMe: [] as unknown[],
  router: { push: vi.fn(), replace: vi.fn(), refresh: vi.fn() },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => shared.router,
}));

vi.mock('@/lib/supabase/client', () => ({
  getSupabaseBrowserClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: shared.session } }),
      refreshSession: async () => ({ data: { session: null } }),
      // Like the real client, a successful sign-in establishes the session
      // that later getSession() calls observe.
      signInWithPassword: async () => {
        shared.session = shared.signInSession;
        return { data: { session: shared.signInSession }, error: null };
      },
      signOut: vi.fn(async () => ({ error: null })),
      onAuthStateChange: (cb: (event: string, session: unknown) => void) => {
        shared.authCallback = cb;
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      },
    },
  }),
}));

const PROFILE = {
  id: '11111111-2222-3333-4444-555555555555',
  email: 'admin@example.test',
  name: 'Admin',
  role: 'SUPER_ADMIN',
  status: 'APPROVED',
};

/** A server profile row for a freshly self-registered signup. */
const PENDING_PROFILE = {
  id: '22222222-3333-4444-5555-666666666666',
  email: 'newbie@example.test',
  name: 'Newbie',
  role: 'VIEWER',
  status: 'PENDING',
};

/** Deferred /me responses: each fetch parks here until the test resolves it. */
const meResolvers: Array<(r: Response) => void> = [];
const meUrls: string[] = [];
const registerCalls: Array<{ url: string; body: unknown }> = [];

function meResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req_test' },
  });
}

function resolveNextMe() {
  const resolve = meResolvers.shift();
  if (!resolve) throw new Error('no pending /me request');
  // A queued script payload wins (role-null first, then PENDING); the
  // default is the approved admin used by the older tests.
  const payload = shared.nextMe.length > 0 ? shared.nextMe.shift() : PROFILE;
  resolve(meResponse({ success: true, data: payload, request_id: 'req_test' }));
}

function Probe() {
  const { user, refreshProfile, signIn } = useAuth();
  return (
    <div>
      <span data-testid="email">{user?.email ?? 'signed-out'}</span>
      <button onClick={() => void refreshProfile()}>refresh</button>
      <button onClick={() => void signIn('admin@example.test', 'secret')}>sign-in</button>
    </div>
  );
}

beforeEach(() => {
  meResolvers.length = 0;
  meUrls.length = 0;
  registerCalls.length = 0;
  shared.authCallback = null;
  shared.session = { access_token: 'tok' };
  shared.signInSession = { access_token: 'tok-login' };
  shared.nextMe.length = 0;
  shared.router.push.mockClear();
  shared.router.replace.mockClear();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: { method?: string; body?: string }) => {
      const url = String(input);
      if (url.includes('/api/v1/auth/register-profile')) {
        registerCalls.push({ url, body: init?.body ? JSON.parse(init.body as string) : null });
        return meResponse({ success: true, data: PENDING_PROFILE, request_id: 'req_test' });
      }
      if (url.includes('/api/v1/me')) {
        meUrls.push(url);
        return new Promise<Response>(resolve => meResolvers.push(resolve));
      }
      return meResponse({ success: true, data: [], request_id: 'req_test' });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('AuthContext — GET /api/v1/me dedupe', () => {
  it('shares one in-flight request across concurrent triggers', async () => {
    render(
      <StrictMode>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </StrictMode>,
    );

    // Bootstrap (twice, Strict Mode) has issued exactly one request.
    await waitFor(() => expect(meResolvers.length).toBe(1));

    // A SIGNED_IN event lands while that request is still in flight —
    // e.g. the race between signIn()'s explicit loadProfile and Supabase's
    // own event. It must share the pending promise, not start request #2.
    act(() => {
      shared.authCallback?.('SIGNED_IN', { access_token: 'tok' });
    });
    await act(async () => {});

    expect(meResolvers.length).toBe(1);

    resolveNextMe();
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('admin@example.test'),
    );
    expect(meUrls.length).toBe(1);
  });

  it('still refetches on an explicit later refresh (no client-side caching)', async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(meResolvers.length).toBe(1));
    resolveNextMe();
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('admin@example.test'),
    );

    // Sequential call after settlement must hit the server again — the
    // server response remains the only source of role/status.
    fireEvent.click(screen.getByText('refresh'));
    await waitFor(() => expect(meUrls.length).toBe(2));
    await waitFor(() => expect(meResolvers.length).toBe(1)); // request #2 parked
    resolveNextMe();
    expect(meUrls.length).toBe(2);
  });

  it('issues exactly one /me for a login: SIGNED_IN with the same session is a no-op', async () => {
    // Login page: no session yet, so bootstrap issues no request.
    shared.session = null;
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('signed-out'),
    );
    expect(meUrls.length).toBe(0);

    fireEvent.click(screen.getByText('sign-in'));
    await waitFor(() => expect(meResolvers.length).toBe(1));
    resolveNextMe();
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('admin@example.test'),
    );

    // Supabase now emits SIGNED_IN for the session signIn() just loaded —
    // same token, so no second request (the pre-§14 race refetched here).
    act(() => {
      shared.authCallback?.('SIGNED_IN', { access_token: 'tok-login' });
    });
    await act(async () => {});
    expect(meUrls.length).toBe(1);
  });

  it('refetches when TOKEN_REFRESHED brings a new token during a pending load', async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(meResolvers.length).toBe(1));
    // A new token is a new session: it must not join the stale fetch.
    act(() => {
      shared.authCallback?.('TOKEN_REFRESHED', { access_token: 'tok-2' });
    });
    await waitFor(() => expect(meResolvers.length).toBe(2));
    expect(meUrls.length).toBe(2);

    resolveNextMe();
    resolveNextMe();
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('admin@example.test'),
    );
  });

  it('self-registers a session with no server profile, then lands on /pending', async () => {
    // Email-confirmation signup: no session at mount (login page), and the
    // first /me answers role null — the missing profiles row.
    shared.session = null;
    shared.signInSession = {
      access_token: 'tok-newbie',
      user: { user_metadata: { name: 'Newbie', requested_role: 'TECHNICIAN' } },
    };
    shared.nextMe.push(
      { id: '22222222-3333-4444-5555-666666666666', email: 'newbie@example.test', role: null },
      PENDING_PROFILE,
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('signed-out'),
    );
    expect(meUrls.length).toBe(0);

    fireEvent.click(screen.getByText('sign-in'));
    await waitFor(() => expect(meResolvers.length).toBe(1));
    resolveNextMe(); // role null...
    // ...so exactly one self-registration fires, then /me is re-read.
    await waitFor(() => expect(registerCalls.length).toBe(1));
    expect(registerCalls[0].body).toMatchObject({ name: 'Newbie', requested_role: 'TECHNICIAN' });
    await waitFor(() => expect(meResolvers.length).toBe(1)); // the re-read parked
    resolveNextMe(); // ...PENDING
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('newbie@example.test'),
    );
    expect(shared.router.push).toHaveBeenCalledWith('/pending');

    // The session's own SIGNED_IN event afterwards changes nothing.
    act(() => {
      shared.authCallback?.('SIGNED_IN', { access_token: 'tok-newbie' });
    });
    await act(async () => {});
    expect(meUrls.length).toBe(2);
    expect(registerCalls.length).toBe(1);
  });

  it('never self-registers when the server profile already exists', async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(meResolvers.length).toBe(1));
    resolveNextMe();
    await waitFor(() =>
      expect(screen.getByTestId('email').textContent).toBe('admin@example.test'),
    );
    expect(registerCalls.length).toBe(0);

    fireEvent.click(screen.getByText('refresh'));
    await waitFor(() => expect(meUrls.length).toBe(2));
    expect(registerCalls.length).toBe(0);
  });
});
