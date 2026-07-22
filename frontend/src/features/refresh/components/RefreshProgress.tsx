/* Stage-aware progress for a running refresh.
 *
 * A single bar would sit apparently frozen through the analysis stage, which is
 * queued with the model provider and is by far the longest part. Naming the
 * stages is what lets someone tell working from stuck. */

import { AlertTriangle, Check, CheckCircle2, Loader2, X } from 'lucide-react';

import { REFRESH_STAGES, isRefreshActive } from '@/client';
import type { RefreshRun, RefreshStage } from '@/client';
import styles from './RefreshProgress.module.css';

interface Props {
  run: RefreshRun | null | undefined;
  onDismiss?: () => void;
  onRetry?: () => void;
}

const stageIndex = (stage: RefreshStage | null): number =>
  stage ? REFRESH_STAGES.findIndex((s) => s.key === stage) : -1;

function formatCost(usd: number | null): string | null {
  if (usd === null) return null;
  if (usd === 0) return 'nothing';
  if (usd < 0.01) return 'under a cent';
  return `$${usd.toFixed(2)}`;
}

export function RefreshProgress({ run, onDismiss, onRetry }: Props) {
  if (!run) return null;

  const active = isRefreshActive(run);
  const failed = run.status === 'failed';
  // A run can succeed and still not have finished everything, for instance when
  // a bulletin ran out of retries. Saying so is better than a green tick over
  // an incomplete corpus.
  const partial = run.status === 'succeeded' && !!run.error;
  const current = stageIndex(run.stage);

  const counts = run.counts as Record<string, number | undefined>;
  const cost = formatCost(run.actualCostUsd);

  return (
    <div
      className={`${styles.panel} ${failed ? styles.failed : ''} ${partial ? styles.partial : ''}`}
      role="status"
      aria-live="polite"
    >
      <div className={styles.header}>
        {active ? (
          <Loader2 size={18} className={`${styles.icon} ${styles.spinning}`} />
        ) : failed ? (
          <X size={18} className={`${styles.icon} ${styles.iconFailed}`} />
        ) : partial ? (
          <AlertTriangle size={18} className={`${styles.icon} ${styles.iconPartial}`} />
        ) : (
          <CheckCircle2 size={18} className={`${styles.icon} ${styles.iconDone}`} />
        )}

        <div className={styles.headline}>
          <strong>
            {/* Describes the run, not the vehicle. "Up to date" belongs to the
                preview, and having both say it read as a stutter. */}
            {active
              ? (run.stageNote ?? 'Working')
              : failed
                ? 'Refresh failed'
                : partial
                  ? 'Finished, with some gaps'
                  : 'Refresh complete'}
          </strong>
          {!active && (
            <span className={styles.summary}>
              {typeof counts.analysed === 'number' && `${counts.analysed} analysed`}
              {cost && ` · cost ${cost}`}
            </span>
          )}
        </div>

        {!active && onDismiss && (
          <button className="btn btn-ghost btn-sm" onClick={onDismiss}>
            Dismiss
          </button>
        )}
      </div>

      {active && (
        <ol className={styles.stages}>
          {REFRESH_STAGES.map((stage, i) => {
            const state = i < current ? 'done' : i === current ? 'now' : 'todo';
            return (
              <li key={stage.key} className={`${styles.stage} ${styles[state]}`}>
                <span className={styles.marker} aria-hidden="true">
                  {state === 'done' ? <Check size={11} /> : <span className={styles.dot} />}
                </span>
                <span className={styles.stageLabel}>{stage.label}</span>
              </li>
            );
          })}
        </ol>
      )}

      {active && run.total > 0 && (
        <p className={styles.scale}>
          {run.total} document{run.total === 1 ? '' : 's'} in this run
        </p>
      )}

      {(failed || partial) && run.error && (
        <p className={styles.reason}>
          {run.error}
          {failed && onRetry && (
            <button className={styles.retry} onClick={onRetry}>
              Try again
            </button>
          )}
        </p>
      )}
    </div>
  );
}
