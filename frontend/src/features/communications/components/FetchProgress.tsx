/* Fetch Progress Component */

import { Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';
import type { FetchProgress } from '@/client';

interface Props {
    progress: FetchProgress | null;
    onDismiss?: () => void;
}

export function FetchProgressBar({ progress, onDismiss }: Props) {
    if (!progress) return null;

    const getIcon = () => {
        switch (progress.status) {
            case 'complete':
                return <CheckCircle2 size={20} className="status-icon success" />;
            case 'error':
                return <XCircle size={20} className="status-icon error" />;
            default:
                return <Loader2 size={20} className="status-icon spinning" />;
        }
    };

    const isFinished = progress.status === 'complete' || progress.status === 'error';

    return (
        <div className={`fetch-progress ${progress.status}`}>
            <div className="progress-header">
                {getIcon()}
                <span className="progress-message">{progress.message}</span>
                {isFinished && onDismiss && (
                    <button className="btn btn-ghost btn-sm" onClick={onDismiss}>
                        Dismiss
                    </button>
                )}
            </div>

            {progress.status !== 'complete' && progress.status !== 'error' && (
                <div className="progress-bar-container">
                    <div className="progress">
                        <div
                            className="progress-bar"
                            style={{ width: `${progress.progress}%` }}
                        />
                    </div>
                    <span className="progress-percent">{progress.progress}%</span>
                </div>
            )}

            {progress.totalIds > 0 && (
                <div className="progress-stats">
                    <span>
                        <strong>{progress.fetchedIds}</strong> / {progress.totalIds} fetched
                    </span>
                    {progress.newCount > 0 && (
                        <span className="new-count">
                            <AlertCircle size={14} />
                            {progress.newCount} new
                        </span>
                    )}
                </div>
            )}

            <style>{`
        .fetch-progress {
          background: var(--bg-elevated);
          border: 1px solid var(--border-subtle);
          border-radius: var(--radius-lg);
          padding: var(--space-md);
          margin-bottom: var(--space-lg);
          animation: slide-up var(--transition-default) ease-out;
        }

        .fetch-progress.complete {
          border-color: var(--color-success);
          background: hsla(142, 76%, 46%, 0.1);
        }

        .fetch-progress.error {
          border-color: var(--color-danger);
          background: hsla(0, 84%, 60%, 0.1);
        }

        .progress-header {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          margin-bottom: var(--space-sm);
        }

        .status-icon {
          flex-shrink: 0;
        }

        .status-icon.success {
          color: var(--color-success);
        }

        .status-icon.error {
          color: var(--color-danger);
        }

        .status-icon.spinning {
          color: var(--color-primary);
          animation: spin 1s linear infinite;
        }

        .progress-message {
          flex: 1;
          font-size: 0.875rem;
          color: var(--text-primary);
        }

        .progress-bar-container {
          display: flex;
          align-items: center;
          gap: var(--space-md);
          margin-bottom: var(--space-sm);
        }

        .progress-bar-container .progress {
          flex: 1;
        }

        .progress-percent {
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--color-primary);
          min-width: 40px;
          text-align: right;
        }

        .progress-stats {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 0.75rem;
          color: var(--text-muted);
        }

        .new-count {
          display: flex;
          align-items: center;
          gap: 4px;
          color: var(--color-accent);
        }

        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
        </div>
    );
}
