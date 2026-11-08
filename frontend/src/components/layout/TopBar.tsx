/* Top bar for the public and signed-in surfaces: product name, who is signed
 * in, and Sign in / Sign out. On narrow screens the text collapses to icons. */

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { LogIn, LogOut } from 'lucide-react';

import { useAuth } from '../../auth/AuthContext';
import { SignInDialog } from '../../auth/SignInDialog';
import styles from './TopBar.module.css';

export function TopBar() {
  const { user, signOut } = useAuth();
  const [signInOpen, setSignInOpen] = useState(false);

  return (
    <header className={styles.bar}>
      <Link to="/" className={styles.brand}>
        NHTSA Comms
      </Link>
      <div className={styles.right}>
        {user ? (
          <>
            <span className={styles.email}>{user.email}</span>
            <button
              className={`btn btn-ghost btn-sm ${styles.action}`}
              onClick={() => void signOut()}
              aria-label="Sign out"
            >
              <LogOut size={16} />
              <span className={styles.label}>Sign out</span>
            </button>
          </>
        ) : (
          <button
            className={`btn btn-primary btn-sm ${styles.action}`}
            onClick={() => setSignInOpen(true)}
            aria-label="Sign in"
          >
            <LogIn size={16} />
            <span className={styles.label}>Sign in</span>
          </button>
        )}
      </div>
      {signInOpen && <SignInDialog onClose={() => setSignInOpen(false)} />}
    </header>
  );
}
