/* Route guards. RequireAuth asks signed-out visitors to sign in; RequireAdmin
 * additionally refuses signed-in non-admins without mounting anything inside. */

import { useState, type ReactNode } from 'react';

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

function SignInGate() {
  const [open, setOpen] = useState(true);
  return (
    <Gate title="Sign in to continue">
      <button className="btn btn-primary" onClick={() => setOpen(true)}>
        Sign in
      </button>
      {open && <SignInDialog onClose={() => setOpen(false)} />}
    </Gate>
  );
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <Gate title="Loading..." />;
  if (!user) return <SignInGate />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, me, loading, meLoading } = useAuth();
  if (loading || meLoading) return <Gate title="Loading..." />;
  if (!user) return <SignInGate />;
  if (!me?.isAdmin) return <Gate title="This area is for the site admin" />;
  return <>{children}</>;
}
