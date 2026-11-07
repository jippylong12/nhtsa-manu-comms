import { useAuth } from '../auth/AuthContext';
import styles from './pages.module.css';

export function AccountPage() {
  const { user, signOut } = useAuth();
  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Account</h1>
      {user?.email && <p className={styles.lead}>{user.email}</p>}
      <button className="btn btn-secondary" onClick={() => void signOut()}>
        Sign out
      </button>
    </div>
  );
}
