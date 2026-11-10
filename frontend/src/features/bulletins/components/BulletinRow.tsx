/* BulletinRow - the lean row: severity bar, date and type, unread dot, two-line headline. */

import { forwardRef } from 'react';
import { format, parseISO } from 'date-fns';
import { Lock } from 'lucide-react';

import { COMM_TYPE_LABELS, PRIORITY_COLORS } from '@/client';
import type { CommPriority, CommType, MyBulletin, Severity } from '@/client';
import styles from './BulletinRow.module.css';

export interface BulletinRowProps {
    bulletin: MyBulletin;
    selected: boolean;
    onOpen: (nhtsaId: string) => void;
    variant: 'product' | 'admin';
    tabIndex?: number;
}

const SEVERITY_PRIORITY: Record<Severity, CommPriority> = {
    serious: 'high',
    medium: 'medium',
    small: 'low',
};

// Fixed grey filler. It is blurred and hidden from assistive tech; it is never bulletin text.
const PLACEHOLDER_TEXT = 'Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod';

function formatDate(iso: string | null | undefined): string {
    if (!iso) return 'Date unknown';
    try {
        const d = parseISO(iso);
        return Number.isNaN(d.getTime()) ? 'Date unknown' : format(d, 'MMM d, yyyy');
    } catch {
        return 'Date unknown';
    }
}

export const BulletinRow = forwardRef<HTMLButtonElement, BulletinRowProps>(function BulletinRow(
    { bulletin, selected, onOpen, variant, tabIndex },
    ref,
) {
    const code = bulletin.communicationType ?? null;
    const label = code ? (COMM_TYPE_LABELS[code as CommType] ?? code) : 'Bulletin';
    const date = formatDate(bulletin.communicationDate);
    const read = bulletin.read === true;
    const headline = bulletin.llmSummary ?? bulletin.summary ?? 'No summary available';
    const color = PRIORITY_COLORS[SEVERITY_PRIORITY[bulletin.severity]];
    const showDot = !read && variant === 'product';

    // A locked row never exposes the headline, not even to assistive tech.
    const ariaParts = [date, label, bulletin.locked ? 'Locked, unlock to read' : headline];
    if (!read) ariaParts.push('unread');

    return (
        <button
            ref={ref}
            type="button"
            className={`${styles.row} ${selected ? styles.selected : ''}`}
            aria-selected={selected}
            aria-label={ariaParts.join(', ')}
            tabIndex={tabIndex}
            onClick={() => onOpen(bulletin.nhtsaId)}
        >
            <span className={styles.bar} style={{ backgroundColor: color }} aria-hidden="true" />
            <span className={styles.body}>
                <span className={styles.lineOne}>
                    <span className={styles.date}>{date}</span>
                    <span className={styles.sep} aria-hidden="true">
                        ·
                    </span>
                    <span className={styles.type}>
                        {label}
                        {code && <span className={styles.code}> {code}</span>}
                    </span>
                    {showDot && <span className={styles.dot} aria-hidden="true" />}
                </span>
                {bulletin.locked ? (
                    <span className={styles.lockedBody}>
                        <span className={styles.blur} aria-hidden="true">
                            {PLACEHOLDER_TEXT}
                        </span>
                        <span className={styles.unlock} aria-hidden="true">
                            <Lock size={14} /> Unlock to read
                        </span>
                    </span>
                ) : (
                    <span className={`${styles.headline} ${read ? styles.headlineRead : ''}`}>{headline}</span>
                )}
            </span>
        </button>
    );
});
