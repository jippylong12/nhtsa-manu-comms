/* Top bar for the public and signed-in surfaces: product name, who is signed
 * in, and Sign in / Sign out. On narrow screens the text collapses to icons. */

import { useState } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { Car, CircleUser, LogIn, LogOut } from 'lucide-react';

import { useAuth } from '../../auth/AuthContext';
import { SignInDialog } from '../../auth/SignInDialog';
import { Logo } from '../brand/Logo';
import styles from './TopBar.module.css';

export function TopBar() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const [signInOpen, setSignInOpen] = useState(false);

  return (
    <header className={styles.bar}>
      <Link to={user ? '/app' : '/'} className={styles.brand} aria-label="Not Just My Car, home">
        <Logo size={28} />
      </Link>
      <div className={styles.right}>
        {user ? (
          <>
            <NavLink
              to="/app"
              end
              className={({ isActive }) => `${styles.nav} ${isActive ? styles.navActive : ''}`}
            >
              <Car size={16} aria-hidden="true" />
              <span className={styles.label}>My vehicles</span>
            </NavLink>
            <NavLink
              to="/app/account"
              className={({ isActive }) => `${styles.nav} ${isActive ? styles.navActive : ''}`}
              title={user.email ?? undefined}
            >
              <CircleUser size={16} aria-hidden="true" />
              <span className={styles.label}>Account</span>
            </NavLink>
            <button
              className={`btn btn-ghost btn-sm ${styles.action}`}
              onClick={() => void signOut().then(() => navigate('/'))}
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
      {signInOpen && (
        <SignInDialog
          onClose={() => setSignInOpen(false)}
          // Signing in from the top bar goes straight to the person's cars.
          onSignedIn={() => {
            setSignInOpen(false);
            navigate('/app');
          }}
        />
      )}
    </header>
  );
}
