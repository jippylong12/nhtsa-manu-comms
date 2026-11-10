/* The two ways to open a car: side card on desktop, sticky bar on mobile. */

import { Loader2, Lock } from 'lucide-react';

import type { CheckoutKind, SeverityCounts } from '@/client';
import { MONTHLY_CARS, MONTHLY_PRICE_LABEL, UNLOCK_PRICE_LABEL } from '../../billing/prices';
import styles from './PlanBar.module.css';

interface Props {
    counts: SeverityCounts;
    sampleCounts: SeverityCounts;
    onUnlock: () => void;
    onSubscribe: () => void;
    /** Which checkout session is being created; both buttons wait while it is set. */
    pending?: CheckoutKind | null;
    /** The server's message when the last attempt failed, shown as written. */
    error?: string | null;
}

export function PlanBar({ counts, sampleCounts, onUnlock, onSubscribe, pending = null, error = null }: Props) {
    return (
        <aside className={styles.plan} aria-label="Unlock this car">
            <p className={styles.sample}>
                <Lock size={14} aria-hidden="true" /> Showing your free sample: {sampleCounts.serious} of{' '}
                {counts.serious} serious, {sampleCounts.medium} of {counts.medium} medium,{' '}
                {sampleCounts.small} of {counts.small} small
            </p>
            <div className={styles.buttons}>
                <button type="button" className={`btn btn-primary ${styles.btn}`} disabled={pending !== null} aria-busy={pending === 'unlock'} onClick={onUnlock}>
                    {pending === 'unlock' ? <Loader2 size={16} className={styles.spin} aria-hidden="true" /> : null}
                    {pending === 'unlock' ? 'Opening checkout…' : `Unlock this car · ${UNLOCK_PRICE_LABEL}`}
                </button>
                <button type="button" className={`btn btn-secondary ${styles.btn}`} disabled={pending !== null} aria-busy={pending === 'subscription'} onClick={onSubscribe}>
                    {pending === 'subscription' ? <Loader2 size={16} className={styles.spin} aria-hidden="true" /> : null}
                    {pending === 'subscription'
                        ? 'Opening checkout…'
                        : `Subscribe · ${MONTHLY_PRICE_LABEL}, ${MONTHLY_CARS} cars + daily alerts`}
                </button>
            </div>
            {error && (
                <p className={styles.error} role="alert">
                    {error}
                </p>
            )}
        </aside>
    );
}
