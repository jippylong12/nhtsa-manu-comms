/* The brand: a square mark (three bulletins sorted serious, medium, small) and the wordmark.
   `variant="mark"` is the square alone; `variant="full"` is the mark with the name beside it. */

import styles from './Logo.module.css';

interface LogoProps {
  variant?: 'full' | 'mark';
  /** Height of the mark in pixels. The wordmark scales with it. */
  size?: number;
  className?: string;
}

export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      className={styles.mark}
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id="logo-tile" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1B2B52" />
          <stop offset="1" stopColor="#0D1528" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill="url(#logo-tile)" />
      <rect x="1" y="1" width="62" height="62" rx="14" fill="none" stroke="#3B82F6" strokeOpacity=".55" strokeWidth="2" />
      <circle cx="17" cy="20" r="5" fill="#EF4444" />
      <rect x="28" y="16" width="24" height="8" rx="4" fill="#F8FAFC" />
      <circle cx="17" cy="32" r="5" fill="#F5B400" />
      <rect x="28" y="28" width="17" height="8" rx="4" fill="#F8FAFC" fillOpacity=".82" />
      <circle cx="17" cy="44" r="5" fill="#34C777" />
      <rect x="28" y="40" width="21" height="8" rx="4" fill="#F8FAFC" fillOpacity=".64" />
    </svg>
  );
}

export function Logo({ variant = 'full', size = 28, className = '' }: LogoProps) {
  return (
    <span className={`${styles.logo} ${className}`} style={{ fontSize: size * 0.6 }}>
      <LogoMark size={size} />
      {variant === 'full' ? (
        <span className={styles.word}>
          <span className={styles.wordStrong}>NHTSA</span>
          <span className={styles.wordLight}>Comms</span>
        </span>
      ) : (
        <span className={styles.srOnly}>NHTSA Comms</span>
      )}
    </span>
  );
}
