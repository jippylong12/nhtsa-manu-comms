/* BulletinSheet - below 900 px the panel is a full-screen modal sheet over the list. */

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft } from 'lucide-react';

import styles from './BulletinSheet.module.css';

interface Props {
    title: string;
    onClose: () => void;
    children: ReactNode;
}

const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

export function BulletinSheet({ title, onClose, children }: Props) {
    const dialogRef = useRef<HTMLDivElement>(null);
    const backRef = useRef<HTMLButtonElement>(null);
    const titleId = useId();

    // Lock page scroll while the sheet is up, and put focus on its first control.
    useEffect(() => {
        const prev = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        backRef.current?.focus();
        return () => {
            document.body.style.overflow = prev;
        };
    }, []);

    // Focus never leaves the sheet: Tab cycles, and a stray focus is pulled back in.
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key !== 'Tab') return;
            const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
                (el) => el.offsetParent !== null || el === document.activeElement
            );
            if (items.length === 0) {
                e.preventDefault();
                return;
            }
            const first = items[0];
            const last = items[items.length - 1];
            const active = document.activeElement;
            if (!dialog.contains(active)) {
                e.preventDefault();
                first.focus();
            } else if (e.shiftKey && active === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && active === last) {
                e.preventDefault();
                first.focus();
            }
        };
        const onFocusIn = (e: FocusEvent) => {
            if (e.target instanceof Node && !dialog.contains(e.target)) backRef.current?.focus();
        };
        document.addEventListener('keydown', onKeyDown);
        document.addEventListener('focusin', onFocusIn);
        return () => {
            document.removeEventListener('keydown', onKeyDown);
            document.removeEventListener('focusin', onFocusIn);
        };
    }, []);

    // Esc is handled once for the whole page by useInboxKeyboard.
    return createPortal(
        <div ref={dialogRef} className={styles.sheet} role="dialog" aria-modal="true" aria-labelledby={titleId}>
            <div className={styles.bar}>
                <button
                    ref={backRef}
                    type="button"
                    className={styles.back}
                    aria-label="Back to bulletins"
                    onClick={onClose}
                >
                    <ChevronLeft size={20} aria-hidden="true" />
                </button>
                <span id={titleId} className={styles.title}>
                    {title}
                </span>
            </div>
            <div className={styles.body}>{children}</div>
        </div>,
        document.body
    );
}
