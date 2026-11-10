/* BulletinPanel - the minimal detail column for the selected bulletin (the sheet and full content come later). */

import { format, parseISO } from 'date-fns';
import { ExternalLink, FileText, Lock, X } from 'lucide-react';

import { COMM_TYPE_LABELS } from '@/client';
import type { CommType, MyBulletin } from '@/client';
import { MONTHLY_PRICE_LABEL, UNLOCK_PRICE_LABEL } from '../../billing/prices';
import styles from './BulletinPanel.module.css';

interface Props {
    bulletin: MyBulletin;
    onClose: () => void;
    onUnlock: () => void;
    onSubscribe: () => void;
}

function formatDate(iso: string | null | undefined): string {
    if (!iso) return 'Date unknown';
    try {
        const d = parseISO(iso);
        return Number.isNaN(d.getTime()) ? 'Date unknown' : format(d, 'MMM d, yyyy');
    } catch {
        return 'Date unknown';
    }
}

export function BulletinPanel({ bulletin: b, onClose, onUnlock, onSubscribe }: Props) {
    const type = b.communicationType
        ? (COMM_TYPE_LABELS[b.communicationType as CommType] ?? b.communicationType)
        : 'Bulletin';
    const docs = b.documents ?? [];

    return (
        <aside className={styles.panel} aria-label="Bulletin detail">
            <div className={styles.top}>
                <div className={styles.meta}>
                    <span>{formatDate(b.communicationDate)}</span>
                    <span className={styles.type}>{type}</span>
                </div>
                <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
                    <X size={14} aria-hidden="true" /> Close
                </button>
            </div>

            {b.locked ? (
                <div className={styles.plan}>
                    <p className={styles.planTitle}>
                        <Lock size={14} aria-hidden="true" /> This bulletin is locked
                    </p>
                    <p className={styles.planText}>Unlock this car to read it, or subscribe for every car.</p>
                    <div className={styles.planButtons}>
                        <button type="button" className="btn btn-primary" onClick={onUnlock}>
                            Unlock this car {UNLOCK_PRICE_LABEL}
                        </button>
                        <button type="button" className="btn btn-secondary" onClick={onSubscribe}>
                            All cars {MONTHLY_PRICE_LABEL}
                        </button>
                    </div>
                </div>
            ) : (
                <>
                    <h2 className={styles.headline}>{b.llmSummary || b.summary || 'No summary available'}</h2>
                    {docs.length > 0 ? (
                        docs.map((d, i) => (
                            <a
                                key={d.url}
                                className={`btn btn-secondary ${styles.pdf}`}
                                href={d.url}
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                <FileText size={14} aria-hidden="true" />
                                {docs.length > 1 ? `Open document ${i + 1}` : 'Open the document'}
                                <ExternalLink size={12} aria-hidden="true" />
                            </a>
                        ))
                    ) : (
                        <p className={styles.noDoc}>This bulletin is without a document.</p>
                    )}
                </>
            )}
        </aside>
    );
}
