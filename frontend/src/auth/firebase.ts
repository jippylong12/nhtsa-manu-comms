/* Firebase bootstrap. Missing config disables sign-in instead of crashing. */

import { initializeApp } from 'firebase/app';
import { GoogleAuthProvider, getAuth, type Auth } from 'firebase/auth';

const env = import.meta.env;

/** Dev-only fake session for smoke tests; always undefined in production builds. */
export const fakeUid: string | undefined = env.DEV
  ? (env.VITE_AUTH_FAKE_UID as string | undefined) || undefined
  : undefined;

const config = {
  apiKey: env.VITE_FIREBASE_API_KEY as string | undefined,
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
  projectId: env.VITE_FIREBASE_PROJECT_ID as string | undefined,
  appId: env.VITE_FIREBASE_APP_ID as string | undefined,
};

const configured = Boolean(
  config.apiKey && config.authDomain && config.projectId && config.appId,
);

/** True when there is neither Firebase config nor a fake uid. */
export const authDisabled = !fakeUid && !configured;

/** The Firebase Auth instance; null when disabled or faked (no SDK call is made). */
export const firebaseAuth: Auth | null =
  !fakeUid && configured ? getAuth(initializeApp(config)) : null;

export const googleProvider = new GoogleAuthProvider();

export function fakeEmail(uid: string): string {
  return `${uid}@example.test`;
}
