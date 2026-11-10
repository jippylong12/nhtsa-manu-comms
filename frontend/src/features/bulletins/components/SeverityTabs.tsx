/* Severity tabs with counts and unread pills, the Unread only switch and Mark all read. */

import { useRef, useState } from 'react';
import { MailOpen } from 'lucide-react';

import { PRIORITY_COLORS } from '@/client';
import type { CommPriority, Facets, Severity } from '@/client';
import { ConfirmDialog } from './ConfirmDialog';
import styles from './SeverityTabs.module.css';

export type SeverityTab = 'all' | Severity;

const TABS: { key: SeverityTab; label: string; priority: CommPriority | null }[] = [
    { key: 'all', label: 'All', priority: null },
    { key: 'serious', label: 'Serious', priority: 'high' },
    { key: 'medium', label: 'Medium', priority: 'medium' },
    { key: 'small', label: 'Small', priority: 'low' },
];

const CONFIRM_ABOVE = 20;

interface Props {
    facets: Facets;
    selected: SeverityTab;
    onSelect: (tab: SeverityTab) => void;
    unreadOnly: boolean;
    onToggleUnread: (next: boolean) => void;
    onMarkAllRead: (severity: Severity | undefined) => void;
    marking?: boolean;
    /** The vehicle's unfiltered unread counts: what Mark all read really marks. */
    totalUnread: { serious: number; medium: number; small: number };
    /** A search or any filter is active, so the list shows less than Mark all read touches. */
    narrowed?: boolean;
}

function sum(c: { serious: number; medium: number; small: number }): number {
    return c.serious + c.medium + c.small;
}

export function SeverityTabs({
    facets,
    selected,
    onSelect,
    unreadOnly,
    onToggleUnread,
    onMarkAllRead,
    marking = false,
    totalUnread,
    narrowed = false,
}: Props) {
    const count = (k: SeverityTab) => (k === 'all' ? sum(facets.bySeverity) : facets.bySeverity[k]);
    const unread = (k: SeverityTab) =>
        k === 'all' ? sum(facets.unreadBySeverity) : facets.unreadBySeverity[k];

    const onKeyDown = (e: React.KeyboardEvent, index: number) => {
        const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        const next = TABS[(index + step + TABS.length) % TABS.length];
        onSelect(next.key);
        document.getElementById(`sev-tab-${next.key}`)?.focus();
    };

    const currentUnread = selected === 'all' ? sum(totalUnread) : totalUnread[selected];
    const [confirming, setConfirming] = useState(false);
    const markAllRef = useRef<HTMLButtonElement>(null);
    const where = selected === 'all' ? '' : ` ${selected}`;

    const doMark = () => onMarkAllRead(selected === 'all' ? undefined : selected);
    const markAll = () => {
        if (narrowed || currentUnread > CONFIRM_ABOVE) {
            setConfirming(true);
            return;
        }
        doMark();
    };
    const closeConfirm = () => {
        setConfirming(false);
        markAllRef.current?.focus();
    };

    return (
        <div className={styles.row}>
            <div className={styles.tabs} role="tablist" aria-label="Severity">
                {TABS.map((t, i) => {
                    const active = selected === t.key;
                    const color = t.priority ? PRIORITY_COLORS[t.priority] : 'var(--color-primary)';
                    const u = unread(t.key);
                    return (
                        <button
                            key={t.key}
                            type="button"
                            role="tab"
                            id={`sev-tab-${t.key}`}
                            aria-selected={active}
                            tabIndex={active ? 0 : -1}
                            className={`${styles.tab} ${active ? styles.tabActive : ''}`}
                            style={active ? { borderBottomColor: color } : undefined}
                            onClick={() => onSelect(t.key)}
                            onKeyDown={(e) => onKeyDown(e, i)}
                        >
                            {t.label}
                            <span className={styles.count}>{count(t.key)}</span>
                            {u > 0 && (
                                <span
                                    className={styles.pill}
                                    style={{ backgroundColor: color }}
                                    aria-label={`${u} unread`}
                                >
                                    {u}
                                </span>
                            )}
                        </button>
                    );
                })}
            </div>
            <div className={styles.actions}>
                <button
                    type="button"
                    role="switch"
                    aria-checked={unreadOnly}
                    className={`${styles.toggle} ${unreadOnly ? styles.toggleOn : ''}`}
                    onClick={() => onToggleUnread(!unreadOnly)}
                >
                    <MailOpen size={16} aria-hidden="true" /> Unread only
                </button>
                <button
                    ref={markAllRef}
                    type="button"
                    className={`btn btn-ghost btn-sm ${styles.markAll}`}
                    disabled={currentUnread === 0 || marking}
                    onClick={markAll}
                >
                    Mark all read
                </button>
            </div>
            {confirming && (
                <ConfirmDialog
                    title={`Mark all ${currentUnread}${where} unread bulletins as read?`}
                    message={
                        narrowed
                            ? "This includes bulletins that don't match your current search or filters."
                            : undefined
                    }
                    confirmLabel="Mark as read"
                    onCancel={closeConfirm}
                    onConfirm={() => {
                        doMark();
                        closeConfirm();
                    }}
                />
            )}
        </div>
    );
}
