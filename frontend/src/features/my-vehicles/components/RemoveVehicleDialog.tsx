/* Confirms removing a car and says, for this car and this account, what removing costs. */

import { useEffect, useRef } from 'react';
import { AlertTriangle, CheckCircle2, Trash2, X } from 'lucide-react';

import type { Me, MyVehicle } from '@/client';
import { isUnlocked, vehicleName } from '../access';
import { removalConsequence } from '../removal';
import styles from './RemoveVehicleDialog.module.css';

interface Props {
    vehicle: MyVehicle;
    me: Me | undefined;
    pending?: boolean;
    /** The server's message when the last attempt failed, shown as written. */
    error?: string | null;
    onConfirm: () => void;
    onClose: () => void;
}

export function RemoveVehicleDialog({ vehicle, me, pending = false, error = null, onConfirm, onClose }: Props) {
    const dialogRef = useRef<HTMLDivElement>(null);
    const name = vehicleName(vehicle);
    const consequence = removalConsequence({
        unlocked: isUnlocked(vehicle, me),
        sample: vehicle.access === 'sample',
        allowance: me?.removals ?? null,
    });
    const blocked = consequence.tone === 'blocked';

    useEffect(() => {
        dialogRef.current?.focus();
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    return (
        <div
            className={styles.backdrop}
            onMouseDown={(e) => {
                if (e.target === e.currentTarget) onClose();
            }}
        >
            <div
                ref={dialogRef}
                className={styles.dialog}
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="remove-title"
                aria-describedby="remove-consequence"
                tabIndex={-1}
            >
                <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
                    <X size={18} />
                </button>
                <h2 id="remove-title" className={styles.title}>
                    Remove {name}?
                </h2>
                <div id="remove-consequence" className={styles.notice} data-tone={consequence.tone}>
                    {consequence.tone === 'safe' ? (
                        <CheckCircle2 size={20} aria-hidden="true" />
                    ) : (
                        <AlertTriangle size={20} aria-hidden="true" />
                    )}
                    <div>
                        <p className={styles.noticeTitle}>{consequence.title}</p>
                        <p className={styles.noticeBody}>{consequence.body}</p>
                    </div>
                </div>
                {error && (
                    <p className={styles.error} role="alert">
                        {error}
                    </p>
                )}
                <div className={styles.actions}>
                    <button type="button" className={`btn btn-secondary ${styles.btn}`} onClick={onClose}>
                        {blocked ? 'Close' : 'Keep it'}
                    </button>
                    {!blocked && (
                        <button
                            type="button"
                            className={`btn btn-danger ${styles.btn}`}
                            disabled={pending}
                            onClick={onConfirm}
                        >
                            <Trash2 size={16} aria-hidden="true" />
                            {pending ? 'Removing...' : 'Remove car'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
