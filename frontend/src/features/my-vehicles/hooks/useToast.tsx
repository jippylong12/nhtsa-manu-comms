/* Smallest accessible toast: a polite live region that clears itself. */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import styles from '../components/Toast.module.css';

const TOAST_MS = 4500;

export function useToast(initial: string | null = null): {
    toast: ReactNode;
    showToast: (message: string) => void;
} {
    const [message, setMessage] = useState<string | null>(initial);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

    const arm = useCallback(() => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setMessage(null), TOAST_MS);
    }, []);

    const showToast = useCallback(
        (next: string) => {
            setMessage(next);
            arm();
        },
        [arm]
    );

    // Starts the dismiss timer for a toast handed in through `initial`.
    useEffect(() => {
        if (initial) arm();
        return () => {
            if (timer.current) clearTimeout(timer.current);
        };
    }, [initial, arm]);

    const toast = (
        <div className={styles.region} role="status" aria-live="polite">
            {message && <div className={styles.toast}>{message}</div>}
        </div>
    );
    return { toast, showToast };
}
