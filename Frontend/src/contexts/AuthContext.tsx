'use client';

/**
 * Real authentication context (Phase 2, task 4).
 *
 * Flow: Supabase Auth session (cookies via @supabase/ssr, auto-refresh) →
 * GET /api/v1/me for the server-side profile. The backend profile is the
 * only source of role + approval status — nothing is trusted from client
 * storage. demo_auth / Admin@123 / client-side password hashes are gone.
 *
 * Account status: profiles with status != APPROVED are blocked server-side
 * (403 ACCOUNT_PENDING/REJECTED) for everything except GET /me, so this
 * context routes those users to /pending.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useRouter } from 'next/navigation';
import type { AuthChangeEvent, Session } from '@supabase/supabase-js';

import { api, ApiError } from '@/lib/api/client';
import {
  mapProfileToUser,
  toProfilePatch,
  type ApiProfile,
} from '@/lib/adapters/user';
import { setAuthSnapshot, type Role } from '@/lib/permissions';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';
import { getSafeNext } from '@/lib/redirect';

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  phone?: string;
  address?: string;
  department?: string;
  staffId?: string;
  designation?: string;
  assignedBlocks?: string[];
  yearsOfExperience?: number;
  certificationNumber?: string;
  reportingManagerId?: string;
  approvedAt?: string;
  status?: 'pending' | 'approved' | 'rejected';
}

export type AccountStatus = NonNullable<User['status']>;

interface AuthContextType {
  user: User | null;
  status: AccountStatus | null;
  isLoading: boolean;
  signIn: (email: string, password: string, next?: string | null) => Promise<void>;
  signUp: (input: SignUpInput) => Promise<{ needsEmailConfirmation: boolean }>;
  signOut: () => Promise<void>;
  /** Self-service profile edit (whitelisted fields only). */
  updateSession: (patch: Partial<User>) => Promise<void>;
  refreshProfile: () => Promise<void>;
  /**
   * Compatibility path for accounts created before automatic onboarding:
   * re-running registration completes a never-reviewed PENDING row in place
   * (server upgrades it), then reloads the profile. REJECTED rows and
   * reviewed rows are returned untouched.
   */
  finalizeOnboarding: () => Promise<void>;
}

export interface SignUpInput {
  name: string;
  email: string;
  password: string;
  requestedRole: Role;
  staffId?: string;
  phone?: string;
  designation?: string;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

/** Roles a signup may request (the server validates again and always grants VIEWER). */
const VALID_REQUESTABLE_ROLES = ['TECHNICIAN', 'VIEWER'] as const;

export function roleHome(role: Role | null | undefined): string {
  switch (role) {
    case 'SUPER_ADMIN':
      return '/admin/dashboard';
    case 'FACILITY_MANAGER':
      return '/manager/dashboard';
    case 'TECHNICIAN':
      return '/technician/dashboard';
    default:
      return '/viewer/dashboard';
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  // Prevent an infinite register-profile → refetch loop.
  const registrationAttempted = useRef(false);

  // At most ONE profile fetch in flight. React Strict Mode's double mount,
  // the SIGNED_IN event racing signIn()'s explicit loadProfile(), and a
  // TOKEN_REFRESHED overlapping an in-progress fetch each fired their own
  // GET /api/v1/me — a full auth + DB round trip per caller (the endpoint
  // shows repeated calls climbing to 5234 ms while queued behind the
  // dashboard's concurrent queries). Concurrent callers now share one
  // promise; a later, sequential call still hits the server, and the
  // role/status always comes from that server response — nothing about
  // authorization is cached or trusted client-side.
  //
  // The inflight entry is keyed by session token, and the last-loaded token
  // is remembered: signIn()'s explicit load finishes just BEFORE its own
  // SIGNED_IN event arrives, and that event carries the SAME session — so a
  // second loadProfile for an already-loaded token is a no-op. Any NEW
  // session (different token: TOKEN_REFRESHED, account switch) or an
  // explicit refresh (force) still reaches the server. No authorization
  // data is cached beyond the current User state itself.
  const inflightProfile = useRef<{ token: string | null; promise: Promise<User | null> } | null>(
    null,
  );
  const loadedSessionToken = useRef<string | null>(null);
  // Access token that hard-failed /me authentication (backend 401). Further
  // /me attempts for the SAME token are suppressed: without this, every
  // TOKEN_REFRESHED event, query poll and remount re-requests /me, refreshes
  // at Supabase (which succeeds), retries, and 401s again — the repeated-401
  // storm. A DIFFERENT token (real refresh / account switch) or an explicit
  // force still reaches the server; success clears the marker.
  const failedToken = useRef<string | null>(null);
  const userRef = useRef<User | null>(null);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- mirror for event callbacks that must return the current user
  React.useEffect(() => {
    userRef.current = user;
  }, [user]);

  interface LoadProfileOptions {
    /** Supabase access token identifying the session (skip when already loaded). */
    token?: string | null;
    /** Explicit user action: always reach the server. */
    force?: boolean;
  }

  /**
   * Self-register the server profile when the session has none.
   *
   * Email-confirmation signups return no session, so signUp()'s explicit
   * register-profile call never runs for them — and the DB trigger is not
   * guaranteed to exist. The first loadProfile that sees such a session
   * (signIn, bootstrap, refresh) heals it here instead of stranding the
   * user on a roleless session: POST register-profile (idempotent; the
   * server forces role VIEWER / status PENDING no matter the input, so a
   * racing trigger or double call cannot double-create or escalate), then
   * re-read /me. Name/requested_role come from the signup metadata the
   * user themselves supplied — the same source the DB trigger reads — and
   * are omitted when absent (the server accepts a bare {}).
   *
   * Loop safety: exactly one attempt per loadProfile invocation; callers
   * are user/event-driven, never success-driven, so no retry storm is
   * possible. A failure throws and the caller signs the user back out to
   * the login screen.
   */
  const ensureServerProfile = useCallback(async (): Promise<ApiProfile> => {
    const {
      data: { session },
    } = await getSupabaseBrowserClient().auth.getSession();
    const meta = (session?.user?.user_metadata ?? {}) as Record<string, unknown>;
    const body: Record<string, unknown> = {};
    if (typeof meta.name === 'string' && meta.name.trim()) body.name = meta.name.trim();
    if (typeof meta.requested_role === 'string' && (VALID_REQUESTABLE_ROLES as readonly string[]).includes(meta.requested_role)) {
      body.requested_role = meta.requested_role;
    }
    await api<ApiProfile>('/api/v1/auth/register-profile', { method: 'POST', body });
    const reloaded = await api<ApiProfile>('/api/v1/me');
    if (reloaded.role == null) throw new Error('Could not create your profile.');
    return reloaded;
  }, []);

  const loadProfile = useCallback(async (opts?: LoadProfileOptions): Promise<User | null> => {
    const token = opts?.token ?? null;
    const force = opts?.force ?? false;
    if (!force && token !== null && loadedSessionToken.current === token) return userRef.current;
    // Same token already proven dead against /me: do not re-request it.
    // (force bypasses for explicit user action; success clears the marker.)
    if (!force && token !== null && failedToken.current === token) return userRef.current;
    // Join an identical in-flight fetch (force only bypasses the
    // same-session suppression above — a joined fetch still reaches the
    // server exactly once for all of its callers).
    const inFlight = inflightProfile.current;
    if (inFlight && inFlight.token === token) return inFlight.promise;
    const run = (async (): Promise<User | null> => {
      try {
        let profile = await api<ApiProfile>('/api/v1/me');
        // profiles.role is NOT NULL on every real row, so role == null
        // means the session has no server profile yet — heal it now
        // instead of routing a roleless user into the app.
        if (profile.role == null) profile = await ensureServerProfile();
        const mapped = mapProfileToUser(profile);
        setUser(mapped);
        setAuthSnapshot(mapped.role ?? null);
        loadedSessionToken.current = token;
        failedToken.current = null;
        return mapped;
      } catch (err) {
        if (
          err instanceof ApiError &&
          (err.code === 'USER_INACTIVE' || err.code === 'AUTH_NOT_CONFIGURED')
        ) {
          setUser(null);
          setAuthSnapshot(null);
          loadedSessionToken.current = null;
          await getSupabaseBrowserClient().auth.signOut();
          throw err;
        }
        if (
          err instanceof ApiError &&
          (err.status === 401 || err.code === 'INVALID_TOKEN' || err.code === 'TOKEN_EXPIRED')
        ) {
          // Hard authentication failure for THIS token (backend rejects it):
          // the session is dead server-side, so end it once instead of
          // letting every event/poll/remount retry /me with the same token.
          // 403s and network/5xx errors never land here — valid sessions
          // survive unrelated API failures.
          failedToken.current = token;
          setUser(null);
          setAuthSnapshot(null);
          loadedSessionToken.current = null;
          await getSupabaseBrowserClient().auth.signOut();
        }
        throw err;
      }
    })();
    const entry = { token, promise: run };
    const tracked = run.finally(() => {
      if (inflightProfile.current === entry) inflightProfile.current = null;
    });
    entry.promise = tracked;
    inflightProfile.current = entry;
    return tracked;
  }, [ensureServerProfile]);

  const bootstrap = useCallback(async () => {
    const supabase = getSupabaseBrowserClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) {
      setUser(null);
      setAuthSnapshot(null);
      return;
    }
    try {
      await loadProfile({ token: session.access_token });
    } catch (err) {
      if (
        err instanceof ApiError &&
        (err.code === 'INVALID_TOKEN' || err.code === 'TOKEN_EXPIRED')
      ) {
        // Stale/foreign token (e.g. backend secret rotated): force re-login.
        await supabase.auth.signOut();
        setUser(null);
        setAuthSnapshot(null);
        return;
      }
      throw err;
    }
  }, [loadProfile]);

  useEffect(() => {
    let cancelled = false;
    const supabase = getSupabaseBrowserClient();

    // eslint-disable-next-line react-hooks/set-state-in-effect -- async auth bootstrap: setState happens only in promise callbacks, never during render
    bootstrap()
      .catch(() => {
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event: AuthChangeEvent, session: Session | null) => {
      if (cancelled) return;
      if (event === 'SIGNED_OUT' || !session) {
        registrationAttempted.current = false;
        loadedSessionToken.current = null;
        // failedToken is intentionally NOT cleared here: the hard-failure
        // path signs out and marks the dead token in the same tick, and the
        // SIGNED_OUT event must not resurrect it. Fresh sign-ins clear the
        // marker explicitly; explicit signOut() clears it directly.
        setUser(null);
        setAuthSnapshot(null);
        return;
      }
      if (event === 'TOKEN_REFRESHED' || event === 'SIGNED_IN') {
        // Keep the server-side profile in sync without blocking the UI. A
        // SIGNED_IN that carries the session we just loaded (signIn's own
        // event) is a no-op inside loadProfile; a refreshed (new-token)
        // session always refetches.
        void loadProfile({ token: session.access_token }).catch(() => undefined);
      }
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, [bootstrap, loadProfile]);

  const signIn = useCallback(
    async (email: string, password: string, next?: string | null) => {
      // Fresh credential attempt: a previous dead token must not suppress it.
      failedToken.current = null;
      const supabase = getSupabaseBrowserClient();
      const { data, error } = await supabase.auth.signInWithPassword({
        email: email.trim().toLowerCase(),
        password,
      });
      if (error) {
        throw new Error(
          error.message.toLowerCase().includes('invalid login')
            ? 'Invalid email or password.'
            : error.message,
        );
      }
      // Coordinate with the SIGNED_IN event this sign-in is about to emit:
      // loading the returned session explicitly means the event (same
      // token) becomes a no-op instead of a second GET /api/v1/me.
      const profile = await loadProfile({ token: data.session?.access_token ?? null });
      if (!profile) throw new Error('Could not load your profile.');
      if (profile.status === 'pending' || profile.status === 'rejected') {
        router.push('/pending');
        return;
      }
      // QR-scan workflow: return to the originally requested page when it is
      // a safe same-origin app path; otherwise fall back to the role home.
      router.replace(getSafeNext(next) ?? roleHome(profile.role));
    },
    [loadProfile, router],
  );

  const signUp = useCallback(
    async (input: SignUpInput) => {
      const supabase = getSupabaseBrowserClient();
      const email = input.email.trim().toLowerCase();
      const { data, error } = await supabase.auth.signUp({
        email,
        password: input.password,
        options: {
          // role is recorded for the approver only — the grant always comes
          // from the server-side profile workflow.
          data: { name: input.name.trim(), requested_role: input.requestedRole },
        },
      });
      if (error) throw new Error(error.message);

      if (data.session) {
        // Email confirmation disabled: we have a session, so register the
        // profile row immediately (idempotent; trigger may have done it).
        registrationAttempted.current = true;
        await api('/api/v1/auth/register-profile', {
          method: 'POST',
          body: {
            name: input.name.trim(),
            requested_role: input.requestedRole,
            staff_id: input.staffId ?? null,
            phone: input.phone ?? null,
            designation: input.designation ?? null,
          },
        });
        await loadProfile({ token: data.session.access_token });
        return { needsEmailConfirmation: false };
      }
      // Confirmation email pending: no session, so no register-profile call
      // here. The row is created by the DB trigger when it exists — and
      // otherwise by loadProfile()'s self-registration the first time this
      // user signs in (see ensureServerProfile).
      return { needsEmailConfirmation: true };
    },
    [loadProfile],
  );

  const signOut = useCallback(async () => {
    await getSupabaseBrowserClient().auth.signOut();
    registrationAttempted.current = false;
    loadedSessionToken.current = null;
    failedToken.current = null;
    setUser(null);
    setAuthSnapshot(null);
    router.push('/login');
  }, [router]);

  const updateSession = useCallback(
    async (patch: Partial<User>) => {
      const updated = await api<ApiProfile>('/api/v1/me', {
        method: 'PATCH',
        body: toProfilePatch(patch),
      });
      const mapped = mapProfileToUser(updated);
      setUser(mapped);
      setAuthSnapshot(mapped.role ?? null);
    },
    [],
  );

  const refreshProfile = useCallback(async () => {
    // Explicit user action: always reach the server (force bypasses the
    // same-session suppression; joining an identical in-flight fetch still
    // counts as reaching it exactly once).
    const {
      data: { session },
    } = await getSupabaseBrowserClient().auth.getSession();
    await loadProfile({ token: session?.access_token ?? null, force: true });
  }, [loadProfile]);

  const finalizeOnboarding = useCallback(async () => {
    await api<ApiProfile>('/api/v1/auth/register-profile', { method: 'POST', body: {} });
    await refreshProfile();
  }, [refreshProfile]);

  return (
    <AuthContext.Provider
      value={{
        user,
        status: user?.status ?? null,
        isLoading,
        signIn,
        signUp,
        signOut,
        updateSession,
        refreshProfile,
        finalizeOnboarding,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
