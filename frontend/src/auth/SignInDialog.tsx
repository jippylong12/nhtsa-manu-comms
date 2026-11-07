/* The sign-in modal: Google, or email and password with a sign-in / create toggle.
 *
 * Closes on Escape and backdrop click, traps focus while open, and says plainly
 * when sign-in is not configured instead of hiding the button. */

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { X } from 'lucide-react';

import styles from './SignInDialog.module.css';
import { useAuth } from './AuthContext';

interface Props {
  onClose: () => void;
}

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])';

function messageFor(err: unknown): string {
  const code = (err as { code?: string } | null)?.code;
  switch (code) {
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      return 'Email or password is incorrect.';
    case 'auth/email-already-in-use':
      return 'An account with that email already exists. Sign in instead.';
    case 'auth/weak-password':
      return 'Choose a password of at least 6 characters.';
    case 'auth/invalid-email':
      return 'Enter a valid email address.';
    case 'auth/popup-closed-by-user':
    case 'auth/cancelled-popup-request':
      return 'Sign-in was cancelled.';
    default:
      return err instanceof Error ? err.message : 'Sign-in failed. Try again.';
  }
}

export function SignInDialog({ onClose }: Props) {
  const { authDisabled, signInWithGoogle, signInWithEmail, signUpWithEmail } = useAuth();
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  // Focus in on open, back to the opener on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    (authDisabled ? dialogRef.current : emailRef.current)?.focus();
    return () => opener?.focus?.();
  }, [authDisabled]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== 'Tab' || !dialogRef.current) return;
    const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialogRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onClose();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    void run(() =>
      mode === 'signin' ? signInWithEmail(email, password) : signUpWithEmail(email, password),
    );
  };

  const disabled = authDisabled || busy;
  const creating = mode === 'signup';

  return (
    <div className={styles.backdrop} role="presentation" onClick={onClose} onKeyDown={handleKeyDown}>
      <div
        ref={dialogRef}
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="signin-dialog-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <button className={styles.close} onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>

        <h3 className={styles.title} id="signin-dialog-title">
          {creating ? 'Create account' : 'Sign in'}
        </h3>
        <p className={styles.lead}>
          {creating ? 'Create an account to track vehicles.' : 'Sign in to see your vehicles.'}
        </p>

        {authDisabled && (
          <p className={styles.notice} role="status">
            Sign-in is not configured in this environment
          </p>
        )}

        <button
          type="button"
          className={`btn btn-secondary ${styles.provider}`}
          disabled={disabled}
          onClick={() => void run(signInWithGoogle)}
        >
          Continue with Google
        </button>

        <div className={styles.divider}>or</div>

        <form className={styles.form} onSubmit={handleSubmit} noValidate={false}>
          <label className={styles.field}>
            Email
            <input
              ref={emailRef}
              className={styles.input}
              type="email"
              autoComplete="email"
              required
              value={email}
              disabled={disabled}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className={styles.field}>
            Password
            <input
              className={styles.input}
              type="password"
              autoComplete={creating ? 'new-password' : 'current-password'}
              required
              minLength={creating ? 6 : undefined}
              value={password}
              disabled={disabled}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}

          <button type="submit" className={`btn btn-primary ${styles.submit}`} disabled={disabled}>
            {busy ? 'Working...' : creating ? 'Create account' : 'Sign in'}
          </button>
        </form>

        <p className={styles.toggle}>
          {creating ? 'Already have an account?' : 'New here?'}{' '}
          <button
            type="button"
            className={styles.link}
            disabled={disabled}
            onClick={() => {
              setMode(creating ? 'signin' : 'signup');
              setError(null);
            }}
          >
            {creating ? 'Sign in' : 'Create account'}
          </button>
        </p>
      </div>
    </div>
  );
}
