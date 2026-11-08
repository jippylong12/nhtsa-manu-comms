/* Bulletin rows for the signed-in product: full rows expand inline, locked rows
   carry only fixed placeholder text, never real content. */

import { useState } from 'react';
import { format } from 'date-fns';
import { ChevronDown, ExternalLink, FileText, Lock, Stethoscope, Wrench } from 'lucide-react';

import { COMM_TYPE_LABELS, PRIORITY_COLORS } from '@/client';
import type { CommPriority, CommType, MyBulletin, Severity } from '@/client';
import styles from './ProductBulletinList.module.css';

const SEVERITY_PRIORITY: Record<Severity, CommPriority> = {
    serious: 'high',
    medium: 'medium',
    small: 'low',
};
const SEVERITY_LABEL: Record<Severity, string> = { serious: 'Serious', medium: 'Medium', small: 'Small' };

// Fixed grey filler. It is blurred and hidden from assistive tech; it is never bulletin text.
const PLACEHOLDER_TEXT = 'Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod';

function typeLabel(code: string | null | undefined): string {
    if (!code) return 'Bulletin';
    return COMM_TYPE_LABELS[code as CommType] ?? code;
}

function formatDate(iso: string | null | undefined): string {
    if (!iso) return 'Unknown date';
    try {
        return format(new Date(iso), 'MMM d, yyyy');
    } catch {
        return 'Unknown date';
    }
}

function LineOne({ b }: { b: MyBulletin }) {
    const color = PRIORITY_COLORS[SEVERITY_PRIORITY[b.severity]];
    return (
        <div className={styles.lineOne}>
            <span
                className={styles.dot}
                style={{ backgroundColor: color }}
                role="img"
                aria-label={`${SEVERITY_LABEL[b.severity]} severity`}
            />
            <span className={styles.date}>{formatDate(b.communicationDate)}</span>
            <span className={styles.type}>{typeLabel(b.communicationType)}</span>
        </div>
    );
}

function LockedRow({ b }: { b: MyBulletin }) {
    return (
        <li
            className={`${styles.row} ${styles.locked}`}
            style={{ borderLeftColor: PRIORITY_COLORS[SEVERITY_PRIORITY[b.severity]] }}
            data-testid="locked-row"
        >
            <div className={styles.rowInner}>
                <LineOne b={b} />
                <div className={styles.lockedBody}>
                    <span className={styles.blur} aria-hidden="true">
                        {PLACEHOLDER_TEXT}
                    </span>
                    <span className={styles.unlock}>
                        <Lock size={14} aria-hidden="true" /> Unlock to read
                    </span>
                </div>
            </div>
        </li>
    );
}

function FullRow({ b }: { b: MyBulletin }) {
    const [expanded, setExpanded] = useState(false);
    const headline = b.llmSummary || b.summary || 'No summary available';
    const symptoms = b.symptoms ?? [];
    const chips = [
        ...symptoms.map((s) => ({ key: `sym-${s}`, text: s, symptom: true })),
        ...(b.systems ?? []).map((s) => ({ key: `sys-${s}`, text: s, symptom: false })),
    ].slice(0, 3);
    const years = b.applicabilityYears ?? [];
    const models = b.applicabilityModels ?? [];
    const hasStructured = years.length > 0 || models.length > 0;
    const hasApplicability = hasStructured || !!b.applicability || !!b.applicabilityConditions;
    const docs = b.documents ?? [];
    const detailId = `bulletin-${b.nhtsaId}`;

    return (
        <li
            className={styles.row}
            style={{ borderLeftColor: PRIORITY_COLORS[SEVERITY_PRIORITY[b.severity]] }}
        >
            <button
                type="button"
                className={styles.rowButton}
                aria-expanded={expanded}
                aria-controls={detailId}
                onClick={() => setExpanded((e) => !e)}
            >
                <div className={styles.rowInner}>
                    <LineOne b={b} />
                    <h3 className={styles.headline}>{headline}</h3>
                    {chips.length > 0 && (
                        <div className={styles.chipRow}>
                            {chips.map((c) => (
                                <span
                                    key={c.key}
                                    className={`${styles.chip} ${c.symptom ? styles.chipSymptom : styles.chipSystem}`}
                                >
                                    {c.text}
                                </span>
                            ))}
                        </div>
                    )}
                </div>
                <ChevronDown
                    size={18}
                    className={`${styles.toggle} ${expanded ? styles.toggleOpen : ''}`}
                    aria-hidden="true"
                />
            </button>

            {expanded && (
                <div id={detailId} className={`${styles.details} animate-fade-in`}>
                    {symptoms.length > 0 && (
                        <div className={styles.field}>
                            <span className={styles.fieldLabel}>
                                <Stethoscope size={13} /> Symptoms
                            </span>
                            <div className={styles.chipRow}>
                                {symptoms.map((s) => (
                                    <span key={s} className={`${styles.chip} ${styles.chipSymptom}`}>
                                        {s}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                    {hasApplicability && (
                        <div className={styles.field}>
                            <span className={styles.fieldLabel}>Applies to</span>
                            {hasStructured ? (
                                <div className={styles.chipRow}>
                                    {years.map((y) => (
                                        <span key={`y-${y}`} className={`${styles.chip} ${styles.chipSystem}`}>
                                            {y}
                                        </span>
                                    ))}
                                    {models.map((m) => (
                                        <span key={`m-${m}`} className={`${styles.chip} ${styles.chipSystem}`}>
                                            {m}
                                        </span>
                                    ))}
                                </div>
                            ) : (
                                b.applicability && <p className={styles.fieldValue}>{b.applicability}</p>
                            )}
                            {b.applicabilityConditions && (
                                <p className={styles.fieldValue}>{b.applicabilityConditions}</p>
                            )}
                        </div>
                    )}
                    {docs.map((d, i) => (
                        <a
                            key={d.url}
                            className={styles.pdfLink}
                            href={d.url}
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            <FileText size={14} />
                            {docs.length > 1 ? `Source document ${i + 1}` : 'Source document'}
                            <ExternalLink size={12} />
                        </a>
                    ))}
                    {b.remedy && (
                        <div className={styles.field}>
                            <span className={styles.fieldLabel}>
                                <Wrench size={13} /> Remedy
                            </span>
                            <p className={styles.fieldValue}>{b.remedy}</p>
                        </div>
                    )}
                </div>
            )}
        </li>
    );
}

export function ProductBulletinList({ items }: { items: MyBulletin[] }) {
    return (
        <ul className={styles.list}>
            {items.map((b) => (b.locked ? <LockedRow key={b.nhtsaId} b={b} /> : <FullRow key={b.nhtsaId} b={b} />))}
        </ul>
    );
}

export function BulletinListSkeleton() {
    return (
        <div className={styles.list} aria-hidden="true">
            {[1, 2, 3, 4].map((i) => (
                <div key={i} className="skeleton" style={{ height: 96 }} />
            ))}
        </div>
    );
}
