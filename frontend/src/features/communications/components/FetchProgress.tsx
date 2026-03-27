/* Fetch Progress Component */

import { Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';
import type { FetchProgress } from '@/client';
import styles from './FetchProgress.module.css';

interface Props {
    progress: FetchProgress | null;
    onDismiss?: () => void;
}

export function FetchProgressBar({ progress, onDismiss }: Props) {
    if (!progress) return null;

    const getIcon = () => {
        switch (progress.status) {
            case 'complete':
                return <CheckCircle2 size={20} className={`${styles.statusIcon} ${styles.success}`} />;
            case 'error':
                return <XCircle size={20} className={`${styles.statusIcon} ${styles.errorIcon}`} />;
            default:
                return <Loader2 size={20} className={`${styles.statusIcon} ${styles.spinning}`} />;
        }
    };

    const isFinished = progress.status === 'complete' || progress.status === 'error';

    const statusClass = progress.status === 'complete'
        ? styles.complete
        : progress.status === 'error'
        ? styles.error
        : '';

    return (
        <div className={`${styles.fetchProgress} ${statusClass}`}>
            <div className={styles.progressHeader}>
                {getIcon()}
                <span className={styles.progressMessage}>{progress.message}</span>
                {isFinished && onDismiss && (
                    <button className="btn btn-ghost btn-sm" onClick={onDismiss}>
                        Dismiss
                    </button>
                )}
            </div>

            {progress.status !== 'complete' && progress.status !== 'error' && (
                <div className={styles.progressBarContainer}>
                    <div className="progress">
                        <div
                            className="progress-bar"
                            style={{ width: `${progress.progress}%` }}
                        />
                    </div>
                    <span className={styles.progressPercent}>{progress.progress}%</span>
                </div>
            )}

            {progress.totalIds > 0 && (
                <div className={styles.progressStats}>
                    <span>
                        <strong>{progress.fetchedIds}</strong> / {progress.totalIds} fetched
                    </span>
                    {progress.newCount > 0 && (
                        <span className={styles.newCount}>
                            <AlertCircle size={14} />
                            {progress.newCount} new
                        </span>
                    )}
                </div>
            )}
        </div>
    );
}
