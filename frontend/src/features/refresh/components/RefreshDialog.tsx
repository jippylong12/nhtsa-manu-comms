/* The confirm step: what this will do, what it will cost, how long it will take.
 *
 * It appears on every refresh, so it has to be light. Three facts and two
 * buttons, no explanation of what a pipeline is. */

import { Coins, Clock, FileText, X } from 'lucide-react';

import type { RefreshPreview } from '@/client';
import styles from './RefreshDialog.module.css';

interface Props {
  preview: RefreshPreview | null;
  isStarting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RefreshDialog({ preview, isStarting, onConfirm, onCancel }: Props) {
  if (!preview) return null;

  const { newBulletins, unfinishedDocuments } = preview;

  // Two kinds of work reach here. Usually it is new bulletins; after an
  // interrupted run it can be documents already fetched but never analysed,
  // and saying "0 new bulletins" while charging for work would be a lie.
  const headline = preview.scanPending
    ? 'First refresh for this vehicle'
    : newBulletins > 0
      ? `${newBulletins} new bulletin${newBulletins === 1 ? '' : 's'}`
      : `${unfinishedDocuments} document${unfinishedDocuments === 1 ? '' : 's'} left to analyse`;

  return (
    <div className={styles.backdrop} role="presentation" onClick={onCancel}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="refresh-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button className={styles.close} onClick={onCancel} aria-label="Cancel">
          <X size={16} />
        </button>

        <h3 className={styles.title} id="refresh-dialog-title">
          {headline}
        </h3>

        {preview.scanPending ? (
          <p className={styles.note}>
            This is the first refresh for this vehicle. It starts by scanning NHTSA's bulletin
            files, which takes a minute or two; the bulletins found are then read and analysed.
            The cost shows on the progress view once the scan knows how many there are.
          </p>
        ) : (
          <>
        <dl className={styles.facts}>
          <div className={styles.fact}>
            <dt>
              <FileText size={15} /> To process
            </dt>
            <dd>
              {preview.documentsToProcess} document
              {preview.documentsToProcess === 1 ? '' : 's'}
            </dd>
          </div>
          <div className={styles.fact}>
            <dt>
              <Coins size={15} /> Cost
            </dt>
            <dd>{preview.estimatedCostText}</dd>
          </div>
          <div className={styles.fact}>
            <dt>
              <Clock size={15} /> Time
            </dt>
            <dd>{preview.estimatedDurationText}</dd>
          </div>
        </dl>

        {newBulletins > 0 && unfinishedDocuments > 0 && (
          <p className={styles.note}>
            Includes {unfinishedDocuments} document
            {unfinishedDocuments === 1 ? '' : 's'} from an earlier run that did not finish.
          </p>
        )}

        <p className={styles.estimateNote}>Cost and time are estimates.</p>
          </>
        )}

        <div className={styles.actions}>
          <button className="btn btn-ghost" onClick={onCancel} disabled={isStarting}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={onConfirm} disabled={isStarting}>
            {isStarting ? 'Starting...' : 'Load them'}
          </button>
        </div>
      </div>
    </div>
  );
}
