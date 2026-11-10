/* Filters: type, systems and date range behind one button. Popover at >= 900 px, bottom sheet below. */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { SlidersHorizontal } from 'lucide-react';

import { COMM_PRIORITY_TYPES, COMM_TYPE_LABELS } from '@/client';
import type { CommPriority } from '@/client';
import { useCorpusTagsQuery } from '../../communications/hooks/useCorpus';
import styles from './FiltersPopover.module.css';

export interface FilterValues {
    type?: string;
    systems: string[];
    from?: string;
    to?: string;
}

/** Number of active filter groups: type, systems and dates each count once. */
function activeFilterCount(v: FilterValues): number {
    return (v.type ? 1 : 0) + (v.systems.length ? 1 : 0) + (v.from || v.to ? 1 : 0);
}

interface Props {
    vehicleId: number;
    value: FilterValues;
    /** URL param changes: a null value removes the param. */
    onChange: (changes: Record<string, string | null>) => void;
    onClear: () => void;
}

const GROUPS: { priority: CommPriority; label: string }[] = [
    { priority: 'high', label: 'Serious' },
    { priority: 'medium', label: 'Medium' },
    { priority: 'low', label: 'Small' },
];
const TOP_SYSTEMS = 12;
const SHEET_QUERY = '(max-width: 899px)';
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled])';

function useIsSheet(): boolean {
    const [sheet, setSheet] = useState(() => window.matchMedia(SHEET_QUERY).matches);
    useEffect(() => {
        const mq = window.matchMedia(SHEET_QUERY);
        const on = () => setSheet(mq.matches);
        mq.addEventListener('change', on);
        return () => mq.removeEventListener('change', on);
    }, []);
    return sheet;
}

export function FiltersPopover({ vehicleId, value, onChange, onClear }: Props) {
    const [open, setOpen] = useState(false);
    const [showAll, setShowAll] = useState(false);
    const isSheet = useIsSheet();
    const panelId = useId();
    const rootRef = useRef<HTMLDivElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const tags = useCorpusTagsQuery(vehicleId, 100);
    const count = activeFilterCount(value);

    const systems = useMemo(
        () => [...(tags.data?.systems ?? [])].sort((a, b) => b.count - a.count),
        [tags.data]
    );
    const visibleSystems = showAll
        ? systems
        : systems.filter((s, i) => i < TOP_SYSTEMS || value.systems.includes(s.tag));

    // Popover: never taller than the room between the trigger and the viewport bottom.
    const [maxHeight, setMaxHeight] = useState<number | undefined>();
    useEffect(() => {
        if (!open || isSheet) return;
        const measure = () => {
            const r = triggerRef.current?.getBoundingClientRect();
            if (r) setMaxHeight(Math.max(240, window.innerHeight - r.bottom - 8 - 16));
        };
        measure();
        window.addEventListener('resize', measure);
        window.addEventListener('scroll', measure, true);
        return () => {
            window.removeEventListener('resize', measure);
            window.removeEventListener('scroll', measure, true);
        };
    }, [open, isSheet]);

    const close = () => {
        setOpen(false);
        triggerRef.current?.focus();
    };

    // Esc closes; outside click closes the popover (the sheet has its own backdrop).
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') close();
        };
        const onDown = (e: MouseEvent) => {
            if (isSheet) return;
            if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('keydown', onKey);
        document.addEventListener('mousedown', onDown);
        return () => {
            document.removeEventListener('keydown', onKey);
            document.removeEventListener('mousedown', onDown);
        };
    }, [open, isSheet]);

    // Sheet: lock body scroll. Both: move focus in on open.
    useEffect(() => {
        if (!open) return;
        panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
        if (!isSheet) return;
        const prev = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            document.body.style.overflow = prev;
        };
    }, [open, isSheet]);

    const trap = (e: ReactKeyboardEvent) => {
        if (!isSheet || e.key !== 'Tab' || !panelRef.current) return;
        const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    };

    const toggleSystem = (tag: string) => {
        const next = value.systems.includes(tag)
            ? value.systems.filter((t) => t !== tag)
            : [...value.systems, tag];
        onChange({ systems: next.length ? next.join(',') : null, page: null });
    };

    const panel = (
        <div
            ref={panelRef}
            id={panelId}
            className={isSheet ? styles.sheet : styles.popover}
            role={isSheet ? 'dialog' : 'group'}
            aria-modal={isSheet ? true : undefined}
            aria-label="Filters"
            style={!isSheet && maxHeight ? { maxHeight } : undefined}
            onKeyDown={trap}
        >
            {isSheet && <div className={styles.handle} aria-hidden="true" />}
            <div className={styles.body}>
                <div className={styles.field}>
                    <label className={styles.label} htmlFor={`${panelId}-type`}>
                        Type
                    </label>
                    <select
                        id={`${panelId}-type`}
                        className={styles.control}
                        value={value.type ?? ''}
                        onChange={(e) => onChange({ type: e.target.value || null, page: null })}
                    >
                        <option value="">All types</option>
                        {GROUPS.map((g) => (
                            <optgroup key={g.priority} label={g.label}>
                                {COMM_PRIORITY_TYPES[g.priority].map((code) => (
                                    <option key={code} value={code}>
                                        {COMM_TYPE_LABELS[code]} ({code})
                                    </option>
                                ))}
                            </optgroup>
                        ))}
                    </select>
                </div>

                <fieldset className={styles.field}>
                    <legend className={styles.label}>Systems</legend>
                    {tags.isPending && <p className={styles.muted}>Loading systems…</p>}
                    {tags.isError && <p className={styles.muted}>Couldn't load systems.</p>}
                    <div className={styles.systems}>
                        {visibleSystems.map((s) => (
                            <label key={s.tag} className={styles.check}>
                                <input
                                    type="checkbox"
                                    aria-labelledby={`${panelId}-sys-${s.tag}`}
                                    checked={value.systems.includes(s.tag)}
                                    onChange={() => toggleSystem(s.tag)}
                                />
                                <span id={`${panelId}-sys-${s.tag}`} className={styles.checkName}>
                                    {s.tag} ({s.count})
                                </span>
                            </label>
                        ))}
                    </div>
                    {systems.length > TOP_SYSTEMS && (
                        <button
                            type="button"
                            className={styles.link}
                            onClick={() => setShowAll((v) => !v)}
                        >
                            {showAll ? 'Show fewer' : 'Show all'}
                        </button>
                    )}
                </fieldset>

                <fieldset className={styles.field}>
                    <legend className={styles.label}>Dates</legend>
                    <div className={styles.dates}>
                        <label className={styles.dateLabel}>
                            From
                            <input
                                type="date"
                                className={styles.control}
                                value={value.from ?? ''}
                                max={value.to || undefined}
                                onChange={(e) => onChange({ from: e.target.value || null, page: null })}
                            />
                        </label>
                        <label className={styles.dateLabel}>
                            To
                            <input
                                type="date"
                                className={styles.control}
                                value={value.to ?? ''}
                                min={value.from || undefined}
                                onChange={(e) => onChange({ to: e.target.value || null, page: null })}
                            />
                        </label>
                    </div>
                </fieldset>
            </div>
            <div className={styles.footer}>
                <button type="button" className={`btn btn-ghost ${styles.action}`} disabled={count === 0} onClick={onClear}>
                    Clear
                </button>
                <button type="button" className={`btn btn-primary ${styles.action}`} onClick={close}>
                    Done
                </button>
            </div>
        </div>
    );

    return (
        <div ref={rootRef} className={styles.root}>
            <button
                ref={triggerRef}
                type="button"
                className={`btn btn-ghost ${styles.trigger}`}
                aria-expanded={open}
                aria-controls={open ? panelId : undefined}
                onClick={() => setOpen((v) => !v)}
            >
                <SlidersHorizontal size={16} aria-hidden="true" />
                Filters
                {count > 0 && (
                    <span className={styles.pill} aria-label={`${count} active`}>
                        {count}
                    </span>
                )}
            </button>
            {open && !isSheet && panel}
            {open &&
                isSheet &&
                createPortal(
                    <div className={styles.backdrop} onMouseDown={close}>
                        <div onMouseDown={(e) => e.stopPropagation()} className={styles.sheetWrap}>
                            {panel}
                        </div>
                    </div>,
                    document.body
                )}
        </div>
    );
}
