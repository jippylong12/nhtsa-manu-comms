/* The sign-in modal: Google, or email and password with a sign-in / create toggle.
 *
 * Closes on Escape and backdrop click, traps focus while open, and says plainly
 * when sign-in is not configured instead of hiding the button. */

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Eye, EyeOff, Lock, Mail, X } from 'lucide-react';

import styles from './SignInDialog.module.css';
import { useAuth } from './AuthContext';

interface Props {
  onClose: () => void;
  /** Called after a successful sign-in instead of onClose, when the two differ. */
  onSignedIn?: () => void;
  /** Which form opens first. A visitor starting from the landing page is creating an account. */
  initialMode?: 'signin' | 'signup';
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

export function SignInDialog({ onClose, onSignedIn, initialMode = 'signin' }: Props) {
  const { authDisabled, signInWithGoogle, signInWithEmail, signUpWithEmail } = useAuth();
  const [mode, setMode] = useState<'signin' | 'signup'>(initialMode);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
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
      (onSignedIn ?? onClose)();
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
          {creating ? 'Create your free account' : 'Welcome back'}
        </h3>
        <p className={styles.lead}>
          {creating
            ? 'See the bulletins for your car. No card needed.'
            : 'Sign in to see the bulletins for your cars.'}
        </p>

        {authDisabled && (
          <p className={styles.notice} role="status">
            Sign-in is not configured in this environment
          </p>
        )}

        <button
          type="button"
          className={styles.provider}
          disabled={disabled}
          onClick={() => void run(signInWithGoogle)}
        >
          <svg className={styles.google} viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
            <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
            <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
            <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
          </svg>
          Continue with Google
        </button>

        <div className={styles.divider}>
          <span>or use your email</span>
        </div>

        <form className={styles.form} onSubmit={handleSubmit} noValidate={false}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="signin-email">
              Email
            </label>
            <div className={styles.control}>
              <Mail size={16} className={styles.fieldIcon} aria-hidden="true" />
              <input
                ref={emailRef}
                id="signin-email"
                className={styles.input}
                type="email"
                inputMode="email"
                autoComplete="email"
                placeholder="you@example.com"
                required
                value={email}
                disabled={disabled}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="signin-password">
              Password
            </label>
            <div className={styles.control}>
              <Lock size={16} className={styles.fieldIcon} aria-hidden="true" />
              <input
                id="signin-password"
                className={`${styles.input} ${styles.inputWithButton}`}
                type={showPassword ? 'text' : 'password'}
                autoComplete={creating ? 'new-password' : 'current-password'}
                placeholder={creating ? 'Create a password' : 'Your password'}
                aria-describedby={creating ? 'signin-password-hint' : undefined}
                required
                minLength={creating ? 6 : undefined}
                value={password}
                disabled={disabled}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className={styles.reveal}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                aria-pressed={showPassword}
                disabled={disabled}
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
              </button>
            </div>
            {creating && (
              <p className={styles.hint} id="signin-password-hint">
                Use 6 characters or more.
              </p>
            )}
          </div>

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}

          <button type="submit" className={`btn btn-primary ${styles.submit}`} disabled={disabled}>
            {busy ? 'Working...' : creating ? 'Create free account' : 'Sign in'}
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
            {creating ? 'Sign in' : 'Create a free account'}
          </button>
        </p>
      </div>
    </div>
  );
}
