/* eslint-disable react-refresh/only-export-components */
// Context + provider + hook live together, same convention as AppContext.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as firebaseSignOut,
} from 'firebase/auth';

import { meApi, setAuthTokenProvider } from '@/client';
import type { Me } from '@/client';
import { fakeEmail, fakeUid, firebaseAuth, googleProvider } from './firebase';

export interface AuthUser {
  uid: string;
  email: string | null;
  displayName: string | null;
}

interface AuthContextValue {
  user: AuthUser | null;
  me: Me | null;
  /** True until the first auth state is known. */
  loading: boolean;
  /** True while a signed-in user's `me` is still loading. */
  meLoading: boolean;
  /** True when sign-in is not configured (no Firebase keys, no fake uid). */
  authDisabled: boolean;
  signInWithGoogle: () => Promise<void>;
  signInWithEmail: (email: string, password: string) => Promise<void>;
  signUpWithEmail: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const disabledError = () => new Error('Sign-in is not configured in this environment');

function fakeUser(uid: string): AuthUser {
  return { uid, email: fakeEmail(uid), displayName: uid };
}

// The signed-in user the token provider reads; module-level so it can be set during render.
let currentUser: AuthUser | null = null;

function registerTokenProvider() {
  setAuthTokenProvider(async () => {
    const current = currentUser;
    if (!current) return null;
    if (fakeUid) return `fake:${current.uid}:${current.email}`;
    return (await firebaseAuth?.currentUser?.getIdToken()) ?? null;
  });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(() => (fakeUid ? fakeUser(fakeUid) : null));
  const [loading, setLoading] = useState<boolean>(() => Boolean(firebaseAuth));
  // Registered during the first render (idempotent, reads module state lazily) so
  // child queries, whose effects run before this component's effects, have it.
  useState(() => {
    currentUser = user;
    registerTokenProvider();
    return null;
  });
  useEffect(() => {
    currentUser = user;
  }, [user]);
  // Cleared only on unmount. Clearing it whenever `user` changed left a gap: a page
  // mounting in the commit that signs the user in (a refresh on a private page) fired
  // its first request with no token and showed an error.
  useEffect(() => {
    registerTokenProvider(); // re-register after a StrictMode cleanup
    return () => setAuthTokenProvider(null);
  }, []);

  useEffect(() => {
    if (!firebaseAuth) return;
    return onAuthStateChanged(firebaseAuth, (fbUser) => {
      const next = fbUser
        ? { uid: fbUser.uid, email: fbUser.email, displayName: fbUser.displayName }
        : null;
      currentUser = next;
      setUser(next);
      setLoading(false);
    });
  }, []);

  const uid = user?.uid ?? null;
  useEffect(() => {
    // Identity changed: whatever `me` we hold belongs to somebody else.
    if (uid) void queryClient.invalidateQueries({ queryKey: ['me'] });
    else queryClient.removeQueries({ queryKey: ['me'] });
  }, [uid, queryClient]);

  const meQuery = useQuery<Me>({
    queryKey: ['me'],
    queryFn: meApi.get,
    enabled: Boolean(uid),
    retry: false,
  });

  const signInWithGoogle = useCallback(async () => {
    if (!firebaseAuth) throw disabledError();
    await signInWithPopup(firebaseAuth, googleProvider);
  }, []);
  const signInWithEmail = useCallback(async (email: string, password: string) => {
    if (!firebaseAuth) throw disabledError();
    await signInWithEmailAndPassword(firebaseAuth, email, password);
  }, []);
  const signUpWithEmail = useCallback(async (email: string, password: string) => {
    if (!firebaseAuth) throw disabledError();
    await createUserWithEmailAndPassword(firebaseAuth, email, password);
  }, []);
  const signOut = useCallback(async () => {
    if (fakeUid) {
      currentUser = null;
      setUser(null);
      return;
    }
    if (firebaseAuth) await firebaseSignOut(firebaseAuth);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      me: user ? (meQuery.data ?? null) : null,
      loading,
      meLoading: Boolean(user) && meQuery.isPending,
      authDisabled: !fakeUid && !firebaseAuth,
      signInWithGoogle,
      signInWithEmail,
      signUpWithEmail,
      signOut,
    }),
    [user, meQuery.data, meQuery.isPending, loading, signInWithGoogle, signInWithEmail, signUpWithEmail, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
