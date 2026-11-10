/* Severity counts card: ready, processing and unknown (example) states */

import { differenceInCalendarDays, format, formatDistanceToNowStrict, parseISO } from 'date-fns';
import { Clock, SearchX } from 'lucide-react';

import { PRIORITY_COLORS } from '@/client';
import type { CommPriority, LookupCounts, LookupResponse } from '@/client';
import { displayVehicle } from '@/features/vehicles/displayName';
import styles from './CountsCard.module.css';

const TILES: { key: keyof LookupCounts; label: string; priority: CommPriority }[] = [
    { key: 'serious', label: 'Serious', priority: 'high' },
    { key: 'medium', label: 'Medium', priority: 'medium' },
    { key: 'small', label: 'Small', priority: 'low' },
];

const vehicleName = displayVehicle;

type Freshness = 'week' | 'month' | 'older';

interface Latest {
    date: string;
    ago: string;
    freshness: Freshness;
    lead: string;
}

/** The newest bulletin's date and how fresh it is: this week, this month, or older. */
function latestOf(iso: string | null): Latest | null {
    if (!iso) return null;
    try {
        const d = parseISO(iso);
        if (Number.isNaN(d.getTime())) return null;
        const days = differenceInCalendarDays(new Date(), d);
        const freshness: Freshness = days <= 7 ? 'week' : days <= 31 ? 'month' : 'older';
        const lead =
            freshness === 'week' ? 'New this week' : freshness === 'month' ? 'New this month' : 'Latest bulletin';
        return {
            date: format(d, 'MMM d, yyyy'),
            ago: days <= 0 ? 'today' : `${formatDistanceToNowStrict(d)} ago`,
            freshness,
            lead,
        };
    } catch {
        return null;
    }
}

interface CountsCardProps {
    data: LookupResponse;
    onSeeAll: () => void;
}

export function CountsCard({ data, onSeeAll }: CountsCardProps) {
    const processing = data.availability === 'processing';
    const unknown = data.availability === 'unknown';
    const shown = unknown && data.example ? data.example : data;
    const latest = processing ? null : latestOf(shown.latestDate);

    return (
        <section className={styles.card} aria-label="Update counts">
            <div aria-live="polite">
                {unknown && (
                    <div className={styles.unknown} role="status">
                        <span className={styles.unknownIcon} aria-hidden="true">
                            <SearchX size={18} />
                        </span>
                        <div>
                            <p className={styles.unknownTitle}>We haven't read the {vehicleName(data)} yet</p>
                            <p className={styles.unknownText}>
                                Add it free and we'll read its bulletins, usually within a day, then email you
                                when they're ready.
                            </p>
                        </div>
                    </div>
                )}
                {(!unknown || data.example) && (
                    <>
                        {unknown && <p className={styles.exampleLabel}>Here's what you'll get, from another car</p>}
                        <h2 className={styles.title}>{vehicleName(shown)}</h2>
                        <div className={styles.tiles}>
                            {TILES.map((t) => (
                                <div
                                    key={t.key}
                                    className={`${styles.tile} ${processing ? styles.skeleton : ''}`}
                                    style={{ ['--tile-color' as string]: PRIORITY_COLORS[t.priority] }}
                                    data-testid={`tile-${t.key}`}
                                >
                                    <span className={styles.count}>
                                        {processing ? (
                                            <span className={styles.srOnly}>Loading</span>
                                        ) : (
                                            shown.counts[t.key]
                                        )}
                                    </span>
                                    <span className={styles.label}>{t.label}</span>
                                </div>
                            ))}
                        </div>
                        {processing ? (
                            <p className={styles.note}>
                                We're reading this car's bulletins now. Sign up and we'll email you when it's ready.
                            </p>
                        ) : (
                            latest && (
                                <p className={styles.latest} data-freshness={latest.freshness}>
                                    <Clock size={16} aria-hidden="true" />
                                    <span className={styles.latestLead}>{latest.lead}</span>
                                    <span className={styles.latestDate}>{latest.date}</span>
                                    <span className={styles.latestAgo}>{latest.ago}</span>
                                </p>
                            )
                        )}
                    </>
                )}
            </div>
            <button type="button" className={`btn btn-primary ${styles.cta}`} onClick={onSeeAll}>
                {unknown ? 'Add my car free' : 'See them all'}
            </button>
        </section>
    );
}
