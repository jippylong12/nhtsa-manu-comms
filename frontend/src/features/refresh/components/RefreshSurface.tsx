/* Everything the refresh flow puts on screen: the confirm dialog, the live
 * progress, and the "nothing to do" answer.
 *
 * Kept in one component because the vehicle grid and the vehicle detail page
 * both offer a refresh and must behave identically. */

import { CheckCircle2, XCircle } from 'lucide-react';

import { RefreshDialog } from './RefreshDialog';
import { RefreshProgress } from './RefreshProgress';
import type { useRefreshFlow } from '../hooks/useRefresh';
import styles from './RefreshSurface.module.css';

interface Props {
  flow: ReturnType<typeof useRefreshFlow>;
  /** Hidden while the vehicle is not the one being refreshed. */
  show?: boolean;
}

export function RefreshSurface({ flow, show = true }: Props) {
  if (!show) return null;

  return (
    <>
      {flow.upToDate && (
        <div className={styles.notice} role="status">
          <CheckCircle2 size={16} className={styles.ok} />
          <span>Already up to date. Nothing new to load.</span>
          <button className="btn btn-ghost btn-sm" onClick={flow.dismiss}>
            Dismiss
          </button>
        </div>
      )}

      {flow.error && (
        <div className={`${styles.notice} ${styles.bad}`} role="alert">
          <XCircle size={16} />
          <span>{flow.error}</span>
          <button className="btn btn-ghost btn-sm" onClick={flow.dismiss}>
            Dismiss
          </button>
        </div>
      )}

      <RefreshProgress
        run={flow.run}
        onRetry={() => flow.run && flow.ask(flow.run.vehicleId)}
      />

      <RefreshDialog
        preview={flow.preview}
        isStarting={flow.isStarting}
        onConfirm={flow.confirm}
        onCancel={flow.cancel}
      />
    </>
  );
}
