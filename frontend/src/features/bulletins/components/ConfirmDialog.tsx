/* In-app confirm dialog: Esc, Cancel and a backdrop click close it; focus starts on Cancel. */

import { useEffect, useId, useRef } from 'react';

import styles from './ConfirmDialog.module.css';

interface Props {
    title: string;
    message?: string;
    confirmLabel: string;
    onConfirm: () => void;
    onCancel: () => void;
}

export function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }: Props) {
    const titleId = useId();
    const cancelRef = useRef<HTMLButtonElement>(null);
    const confirmRef = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        cancelRef.current?.focus();
    }, []);

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Escape') {
            e.stopPropagation();
            onCancel();
            return;
        }
        if (e.key !== 'Tab') return;
        // Keep focus inside the two buttons while the dialog is open.
        const first = cancelRef.current;
        const last = confirmRef.current;
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
        }
    };

    return (
        <div
            className={styles.backdrop}
            onMouseDown={(e) => {
                if (e.target === e.currentTarget) onCancel();
            }}
        >
            <div
                className={styles.dialog}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                onKeyDown={onKeyDown}
            >
                <h2 id={titleId} className={styles.title}>
                    {title}
                </h2>
                {message && <p className={styles.message}>{message}</p>}
                <div className={styles.actions}>
                    <button
                        ref={cancelRef}
                        type="button"
                        className={`btn btn-ghost ${styles.button}`}
                        onClick={onCancel}
                    >
                        Cancel
                    </button>
                    <button
                        ref={confirmRef}
                        type="button"
                        className={`btn btn-primary ${styles.button}`}
                        onClick={onConfirm}
                    >
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}
