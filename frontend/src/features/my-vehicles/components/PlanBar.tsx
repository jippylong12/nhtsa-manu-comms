/* The two ways to open a car: side card on desktop, sticky bar on mobile. */

import { Lock } from 'lucide-react';

import type { SeverityCounts } from '@/client';
import styles from './PlanBar.module.css';

interface Props {
    counts: SeverityCounts;
    sampleCounts: SeverityCounts;
    onUnlock: () => void;
    onSubscribe: () => void;
}

export function PlanBar({ counts, sampleCounts, onUnlock, onSubscribe }: Props) {
    return (
        <aside className={styles.plan} aria-label="Unlock this car">
            <p className={styles.sample}>
                <Lock size={14} aria-hidden="true" /> Showing your free sample: {sampleCounts.serious} of{' '}
                {counts.serious} serious, {sampleCounts.medium} of {counts.medium} medium,{' '}
                {sampleCounts.small} of {counts.small} small
            </p>
            <div className={styles.buttons}>
                <button type="button" className={`btn btn-primary ${styles.btn}`} onClick={onUnlock}>
                    Unlock this car · $3
                </button>
                <button type="button" className={`btn btn-secondary ${styles.btn}`} onClick={onSubscribe}>
                    Subscribe · $5/month, every car + daily alerts
                </button>
            </div>
        </aside>
    );
}
