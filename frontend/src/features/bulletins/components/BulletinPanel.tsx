/* BulletinPanel - the detail column for the selected bulletin: is this my car's problem, and how do I check? */

import { format, parseISO } from 'date-fns';
import { ExternalLink, Lock, Stethoscope, X } from 'lucide-react';

import { COMM_TYPE_LABELS } from '@/client';
import type { CommType, MyBulletin } from '@/client';
import { MONTHLY_PRICE_LABEL, UNLOCK_PRICE_LABEL } from '../../billing/prices';
import { useCorpusDetailQuery } from '../../communications/hooks/useCorpus';
import { SeverityPill } from './SeverityPill';
import styles from './BulletinPanel.module.css';

interface Props {
    bulletin: MyBulletin;
    onClose: () => void;
    onUnlock: () => void;
    onSubscribe: () => void;
    variant?: 'product' | 'admin';
    /** Needed for the lazy "What it covers" text on the product side; without it the row summary shows. */
    vehicleId?: number;
}

const AUDIENCE: Record<string, string> = {
    dealer: 'Addressed to dealers and technicians',
    owner: 'Addressed to owners',
    both: 'Owners and dealers',
};

function formatDate(iso: string | null | undefined): string {
    if (!iso) return 'Date unknown';
    try {
        const d = parseISO(iso);
        return Number.isNaN(d.getTime()) ? 'Date unknown' : format(d, 'MMM d, yyyy');
    } catch {
        return 'Date unknown';
    }
}

function Chips({ items }: { items: (string | number)[] }) {
    return (
        <ul className={styles.chips}>
            {items.map((c) => (
                <li key={c} className={styles.chip}>
                    {c}
                </li>
            ))}
        </ul>
    );
}

export function BulletinPanel({
    bulletin: b,
    onClose,
    onUnlock,
    onSubscribe,
    variant = 'product',
    vehicleId,
}: Props) {
    const code = b.communicationType ?? null;
    const type = code ? (COMM_TYPE_LABELS[code as CommType] ?? code) : 'Bulletin';
    const headline = b.llmSummary || b.summary || 'No summary available';

    // The product panel only reads the sample-aware per-vehicle route; admin may read the corpus route.
    const canFetch = !b.locked && (variant === 'admin' || vehicleId !== undefined);
    const detail = useCorpusDetailQuery(canFetch ? b.nhtsaId : null, vehicleId);

    const header = (
        <header className={styles.header}>
            <div className={styles.top}>
                <div className={styles.meta}>
                    <span className={styles.date}>{formatDate(b.communicationDate)}</span>
                    <span className={styles.type}>
                        {type}
                        {code && <span className={styles.code}> {code}</span>}
                    </span>
                    <SeverityPill severity={b.severity} />
                </div>
                <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
                    <X size={14} aria-hidden="true" /> Close
                </button>
            </div>
            {!b.locked && <h2 className={styles.headline}>{headline}</h2>}
        </header>
    );

    if (b.locked) {
        return (
            <aside className={styles.panel} aria-label="Bulletin detail">
                {header}
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
            </aside>
        );
    }

    const symptoms = (b.symptoms ?? []).filter(Boolean);
    const conditions = b.applicabilityConditions?.trim() || '';
    const systems = (b.systems ?? []).filter(Boolean);
    const components = (b.components ?? []).filter(Boolean);
    const years = b.applicabilityYears ?? [];
    const models = b.applicabilityModels ?? [];
    const applicability = b.applicability?.trim() || '';
    const docs = b.documents ?? [];

    const covers = (detail.data?.detailsSummary || b.llmSummary || '').trim();
    const coversLoading = canFetch && detail.isLoading;
    const showCovers = coversLoading || (covers !== '' && covers !== headline.trim());

    const revisions = (b.supersededCount ?? 0) > 0 ? `latest of ${b.supersededCount} revisions` : 'first revision';
    const audience = b.audience ? AUDIENCE[b.audience] : undefined;

    return (
        <aside className={styles.panel} aria-label="Bulletin detail">
            {header}

            <section className={styles.section}>
                <h3 className={styles.h3}>
                    <Stethoscope size={14} aria-hidden="true" /> How to tell if your car has this
                </h3>
                {symptoms.length > 0 && (
                    <ul className={styles.list}>
                        {symptoms.map((s) => (
                            <li key={s}>{s}</li>
                        ))}
                    </ul>
                )}
                {conditions && <p className={styles.prose}>{conditions}</p>}
                {symptoms.length === 0 && !conditions && (
                    <p className={styles.prose}>The bulletin does not list symptoms. Open it to read the details.</p>
                )}
            </section>

            {(showCovers || systems.length > 0) && (
                <section className={styles.section}>
                    <h3 className={styles.h3}>What it covers</h3>
                    {coversLoading ? (
                        <div className={styles.skeletons} aria-hidden="true">
                            <div className={`skeleton ${styles.skeletonLine}`} />
                            <div className={`skeleton ${styles.skeletonLine}`} />
                        </div>
                    ) : (
                        showCovers && <p className={styles.prose}>{covers}</p>
                    )}
                    {systems.length > 0 && <Chips items={systems} />}
                </section>
            )}

            <section className={styles.section}>
                {docs.length > 0 ? (
                    <>
                        <a
                            className={`btn btn-primary ${styles.pdf}`}
                            href={docs[0].url}
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            Read the bulletin (PDF)
                            <ExternalLink size={14} aria-hidden="true" />
                        </a>
                        {docs.length > 1 && (
                            <ul className={styles.docLinks}>
                                {docs.slice(1).map((d, i) => (
                                    <li key={d.url}>
                                        <a className={styles.link} href={d.url} target="_blank" rel="noopener noreferrer">
                                            Document {i + 2}
                                        </a>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </>
                ) : (
                    <p className={styles.noDoc}>NHTSA published this bulletin without a document.</p>
                )}
            </section>

            <details className={styles.details} open={variant === 'admin' ? true : undefined}>
                <summary className={styles.summary}>Technical details</summary>
                <dl className={styles.rows}>
                    {b.remedy?.trim() && (
                        <div className={styles.row}>
                            <dt>Remedy</dt>
                            <dd>{b.remedy}</dd>
                        </div>
                    )}
                    {components.length > 0 && (
                        <div className={styles.row}>
                            <dt>Components</dt>
                            <dd>
                                <Chips items={components} />
                            </dd>
                        </div>
                    )}
                    {(years.length > 0 || models.length > 0 || applicability) && (
                        <div className={styles.row}>
                            <dt>Applies to</dt>
                            <dd>
                                {years.length > 0 || models.length > 0 ? (
                                    <>
                                        {years.length > 0 && <Chips items={years} />}
                                        {models.length > 0 && <Chips items={models} />}
                                    </>
                                ) : (
                                    applicability
                                )}
                            </dd>
                        </div>
                    )}
                    {audience && (
                        <div className={styles.row}>
                            <dt>Audience</dt>
                            <dd>{audience}</dd>
                        </div>
                    )}
                    {b.communicationNumber && (
                        <div className={styles.row}>
                            <dt>Communication number</dt>
                            <dd>{b.communicationNumber}</dd>
                        </div>
                    )}
                    <div className={styles.row}>
                        <dt>NHTSA id</dt>
                        <dd>{b.nhtsaId}</dd>
                    </div>
                    <div className={styles.row}>
                        <dt>Revisions</dt>
                        <dd>{revisions}</dd>
                    </div>
                </dl>
            </details>
        </aside>
    );
}
