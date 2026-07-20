/* Corpus Communication List - renders processed LLM output with graceful
   degradation for pending/failed communications. */

import { useState } from 'react';
import { format } from 'date-fns';
import {
  FileText,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Sparkles,
  Clock,
  AlertCircle,
  Wrench,
  Cpu,
  Stethoscope,
} from 'lucide-react';

import type { CorpusCommunicationSummary, CommType, ProcessingStatus } from '@/client';
import { COMM_TYPE_COLORS, COMM_TYPE_LABELS } from '@/client';
import { useCorpusDetailQuery } from '../hooks/useCorpus';

import styles from './CorpusCommunicationList.module.css';

interface Props {
  communications: CorpusCommunicationSummary[];
  isLoading?: boolean;
  onSelectType?: (t: CommType) => void;
  onSelectSystem?: (s: string) => void;
  activeSystems?: string[];
}

const STATUS_META: Record<ProcessingStatus, { label: string; icon: typeof Sparkles; cls: string }> = {
  processed: { label: 'Analyzed', icon: Sparkles, cls: 'statusProcessed' },
  pending: { label: 'Pending', icon: Clock, cls: 'statusPending' },
  failed: { label: 'No document', icon: AlertCircle, cls: 'statusFailed' },
};

function StatusBadge({ status }: { status: ProcessingStatus }) {
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  return (
    <span className={`${styles.statusBadge} ${styles[meta.cls]}`}>
      <Icon size={11} />
      {meta.label}
    </span>
  );
}

interface RowProps {
  comm: CorpusCommunicationSummary;
  onSelectType?: (t: CommType) => void;
  onSelectSystem?: (s: string) => void;
  activeSystems?: string[];
}

function CorpusRow({ comm, onSelectType, onSelectSystem, activeSystems = [] }: RowProps) {
  const [expanded, setExpanded] = useState(false);
  // Detail (full document list, remedy, applicability) is fetched lazily on
  // first expand, so the list view stays a single request.
  const { data: detail, isLoading: detailLoading } = useCorpusDetailQuery(
    expanded ? comm.nhtsaId : null
  );

  const commType = (comm.communicationType || 'OTHER') as CommType;
  const typeColor = COMM_TYPE_COLORS[commType] || COMM_TYPE_COLORS.OTHER;
  const commDate = comm.communicationDate
    ? format(new Date(comm.communicationDate), 'MMM d, yyyy')
    : 'Unknown date';

  // The LLM summary is the headline when present; otherwise fall back to the
  // NHTSA one-liner so pending/unavailable rows still read sensibly.
  const headline = comm.llmSummary || comm.summary || 'No summary available';
  const isProcessed = comm.status === 'processed';

  return (
    <div className={styles.row} style={{ borderLeftColor: typeColor }}>
      <div className={styles.header} onClick={() => setExpanded(!expanded)}>
        <div className={styles.info}>
          <div className={styles.topRow}>
            <button
              className={styles.typeBadge}
              title={COMM_TYPE_LABELS[commType] || commType}
              style={{
                backgroundColor: `color-mix(in srgb, ${typeColor} 12%, transparent)`,
                color: typeColor,
                borderColor: `color-mix(in srgb, ${typeColor} 25%, transparent)`,
              }}
              onClick={(e) => {
                e.stopPropagation();
                onSelectType?.(commType);
              }}
            >
              {commType}
            </button>
            <span className={styles.date}>{commDate}</span>
            <StatusBadge status={comm.status} />
            {comm.communicationNumber && (
              <span className={styles.commNumber}>{comm.communicationNumber}</span>
            )}
          </div>

          <h4 className={`${styles.summary} ${isProcessed ? styles.summaryAi : ''}`}>
            {isProcessed && <Sparkles size={14} className={styles.aiIcon} />}
            {headline}
          </h4>

          {(comm.symptoms.length > 0 || comm.systems.length > 0) && (
            <div className={styles.chipRow}>
              {comm.systems.slice(0, 4).map((s) => (
                <button
                  key={`sys-${s}`}
                  className={`${styles.chip} ${styles.chipSystem} ${activeSystems.includes(s) ? styles.chipActive : ''}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectSystem?.(s);
                  }}
                >
                  <Cpu size={10} />
                  {s}
                </button>
              ))}
              {comm.symptoms.slice(0, 3).map((s) => (
                <span key={`sym-${s}`} className={`${styles.chip} ${styles.chipSymptom}`}>
                  <Stethoscope size={10} />
                  {s}
                </span>
              ))}
              {comm.symptoms.length > 3 && (
                <span className={styles.chipMore}>+{comm.symptoms.length - 3}</span>
              )}
            </div>
          )}
        </div>

        <div className={styles.meta}>
          {comm.vehicles.length > 1 && (
            <span className={styles.sharedBadge} title="Applies to multiple tracked vehicles">
              {comm.vehicles.length} vehicles
            </span>
          )}
          <span className={styles.docCount}>
            <FileText size={14} />
            {comm.documentCount}
          </span>
          <button className="btn btn-ghost btn-icon btn-sm">
            {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>
      </div>

      {expanded && (
        <div className={`${styles.details} animate-fade-in`}>
          {detailLoading && <div className="skeleton" style={{ height: 80 }} />}

          {detail && (
            <>
              {detail.status === 'failed' && detail.statusReason && (
                <div className={styles.reasonBox}>
                  <AlertCircle size={14} />
                  {detail.statusReason}
                </div>
              )}

              {detail.documents.map((doc) => (
                <div key={doc.id} className={styles.docBlock}>
                  {doc.remedy && (
                    <div className={styles.field}>
                      <span className={styles.fieldLabel}>
                        <Wrench size={13} /> Remedy
                      </span>
                      <p className={styles.fieldValue}>{doc.remedy}</p>
                    </div>
                  )}
                  {doc.applicability && (
                    <div className={styles.field}>
                      <span className={styles.fieldLabel}>Applies to</span>
                      <p className={styles.fieldValue}>{doc.applicability}</p>
                    </div>
                  )}
                  {doc.symptoms.length > 0 && (
                    <div className={styles.field}>
                      <span className={styles.fieldLabel}>
                        <Stethoscope size={13} /> Symptoms
                      </span>
                      <div className={styles.chipRow}>
                        {doc.symptoms.map((s) => (
                          <span key={s} className={`${styles.chip} ${styles.chipSymptom}`}>
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {doc.components.length > 0 && (
                    <div className={styles.field}>
                      <span className={styles.fieldLabel}>Components</span>
                      <div className={styles.chipRow}>
                        {doc.components.map((c) => (
                          <span key={c} className={`${styles.chip} ${styles.chipComponent}`}>
                            {c}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  <a
                    className={styles.pdfLink}
                    href={doc.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <FileText size={14} />
                    {doc.docSummary || 'Source document'}
                    <ExternalLink size={12} />
                  </a>
                </div>
              ))}

              {detail.documents.length === 0 && detail.status !== 'failed' && (
                <p className={styles.emptyDetail}>
                  This communication has no processed documents yet.
                </p>
              )}

              {detail.vehicles.length > 0 && (
                <div className={styles.vehiclesRow}>
                  {detail.vehicles.map((v) => (
                    <span key={v.nhtsaVehicleId} className={styles.vehicleTag}>
                      {v.year} {v.make} {v.model}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function CorpusCommunicationList({
  communications,
  isLoading,
  onSelectType,
  onSelectSystem,
  activeSystems,
}: Props) {
  if (isLoading) {
    return (
      <div className={styles.loading}>
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton" style={{ height: 96 }} />
        ))}
      </div>
    );
  }

  if (communications.length === 0) {
    return (
      <div className="empty-state">
        <FileText className="empty-state-icon" />
        <h3 className="empty-state-title">No communications found</h3>
        <p className="empty-state-description">
          Try adjusting your search or filters.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.list}>
      {communications.map((comm) => (
        <CorpusRow
          key={comm.nhtsaId}
          comm={comm}
          onSelectType={onSelectType}
          onSelectSystem={onSelectSystem}
          activeSystems={activeSystems}
        />
      ))}
    </div>
  );
}
