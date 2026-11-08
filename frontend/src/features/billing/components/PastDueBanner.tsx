/* Shown on /app and the vehicle page while the subscription is past due. */

import { AlertTriangle } from 'lucide-react';

import { usePortal } from '../hooks/useBilling';
import styles from './PastDueBanner.module.css';

export function PastDueBanner() {
    const portal = usePortal();
    return (
        <div className={styles.banner} role="alert">
            <AlertTriangle size={18} aria-hidden="true" className={styles.icon} />
            <span className={styles.text}>
                Update your payment method to keep alerts.
                {portal.isError && <span className={styles.error}> {portal.error.message}</span>}
            </span>
            <button
                type="button"
                className={`btn btn-secondary ${styles.btn}`}
                disabled={portal.isPending}
                onClick={() => portal.mutate()}
            >
                {portal.isPending ? 'Opening…' : 'Update payment method'}
            </button>
        </div>
    );
}
