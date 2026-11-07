/* Public landing page shell. M2 fills the vehicle picker. */

import { useState } from 'react';

import { useAuth } from '../auth/AuthContext';
import { SignInDialog } from '../auth/SignInDialog';
import styles from './pages.module.css';

export function LandingPage() {
  const { user } = useAuth();
  const [signInOpen, setSignInOpen] = useState(false);

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>NHTSA Comms</h1>
      <p className={styles.lead}>Manufacturer communications for your vehicle, in one place.</p>
      {!user && (
        <p>
          <button className="btn btn-primary" onClick={() => setSignInOpen(true)}>
            Sign in
          </button>
        </p>
      )}
      <div className={styles.placeholder}>Vehicle picker coming soon</div>
      {signInOpen && <SignInDialog onClose={() => setSignInOpen(false)} />}
    </div>
  );
}
