/* Route guards. RequireAuth asks signed-out visitors to sign in; RequireAdmin
 * additionally refuses signed-in non-admins without mounting anything inside. */

import { useState, type ReactNode } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';

import { useAuth } from './AuthContext';
import { SignInDialog } from './SignInDialog';
import styles from './guards.module.css';

function Gate({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.gate}>
      <h1 className={styles.title}>{title}</h1>
      {children}
    </div>
  );
}

// A signed-out visitor to a private page gets the sign-in dialog over an empty page.
// Closing it goes home; there is nothing to look at here without an account.
function SignInGate() {
  const navigate = useNavigate();
  const [signedIn, setSignedIn] = useState(false);
  if (signedIn) return <Gate title="Loading..." />;
  return (
    <div className={styles.gate}>
      <SignInDialog
        onClose={() => navigate('/', { replace: true })}
        onSignedIn={() => setSignedIn(true)}
      />
    </div>
  );
}

// Signing out while on a private page goes home instead of asking to sign back in.
function useSignedOutHere(user: unknown): boolean {
  const [hadUser, setHadUser] = useState(false);
  if (user && !hadUser) setHadUser(true);
  return hadUser && !user;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const signedOutHere = useSignedOutHere(user);
  if (loading) return <Gate title="Loading..." />;
  if (signedOutHere) return <Navigate to="/" replace />;
  if (!user) return <SignInGate />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, me, loading, meLoading } = useAuth();
  const signedOutHere = useSignedOutHere(user);
  if (loading || meLoading) return <Gate title="Loading..." />;
  if (signedOutHere) return <Navigate to="/" replace />;
  if (!user) return <SignInGate />;
  if (!me?.isAdmin) return <Gate title="This area is for the site admin" />;
  return <>{children}</>;
}
