/* Severity counts card: ready, processing and unknown (example) states */

import { format, parseISO } from 'date-fns';

import { PRIORITY_COLORS } from '@/client';
import type { CommPriority, LookupCounts, LookupResponse } from '@/client';
import styles from './CountsCard.module.css';

const TILES: { key: keyof LookupCounts; label: string; priority: CommPriority }[] = [
    { key: 'serious', label: 'Serious', priority: 'high' },
    { key: 'medium', label: 'Medium', priority: 'medium' },
    { key: 'small', label: 'Small', priority: 'low' },
];

function vehicleName(v: { year: number; make: string; model: string }) {
    return `${v.year} ${v.make} ${v.model}`;
}

function formatDate(iso: string | null) {
    if (!iso) return null;
    try {
        return format(parseISO(iso), 'MMM d, yyyy');
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
    const date = processing ? null : formatDate(shown.latestDate);

    return (
        <section className={styles.card} aria-label="Update counts">
            <div aria-live="polite">
                {unknown && (
                    <p className={styles.banner} role="status">
                        {data.example
                            ? `Nobody has asked about a ${vehicleName(data)} yet. Here's what a ${vehicleName(data.example)} looks like. Sign up and we'll read yours, usually within a day.`
                            : `Nobody has asked about a ${vehicleName(data)} yet. Sign up and we'll read yours, usually within a day.`}
                    </p>
                )}
                {(!unknown || data.example) && (
                    <>
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
                            date && <p className={styles.note}>Latest bulletin {date}</p>
                        )}
                    </>
                )}
            </div>
            <button type="button" className={`btn btn-primary ${styles.cta}`} onClick={onSeeAll}>
                See them all
            </button>
        </section>
    );
}
