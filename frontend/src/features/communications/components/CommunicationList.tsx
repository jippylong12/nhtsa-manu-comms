/* Communication List Component */

import { format } from 'date-fns';
import { FileText, ExternalLink, Tag, ChevronDown, ChevronUp } from 'lucide-react';
import { useState, useMemo } from 'react';
import type { Communication, CommType } from '@/client';
import { COMM_TYPE_COLORS, COMM_TYPE_LABELS } from '@/client';
import styles from './CommunicationList.module.css';

interface Props {
  communications: Communication[];
  isLoading?: boolean;
}

interface RowProps {
  comm: Communication;
  occurrence?: number;
}

function CommunicationRow({ comm, occurrence }: RowProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const commDate = comm.communicationDate
    ? format(new Date(comm.communicationDate), 'MMM d, yyyy')
    : 'Unknown';

  const commType = (comm.communicationType || 'OTHER') as CommType;
  const typeColor = COMM_TYPE_COLORS[commType] || COMM_TYPE_COLORS.OTHER;

  const displayNumber = comm.communicationNumber
    ? occurrence && occurrence > 1
      ? `${comm.communicationNumber} (${occurrence})`
      : comm.communicationNumber
    : null;

  return (
    <div className={styles.commRow} style={{ borderLeftColor: typeColor }}>
      <div className={styles.commHeader} onClick={() => setIsExpanded(!isExpanded)}>
        <div className={styles.commInfo}>
          <div className={styles.commTopRow}>
            <span
              className={styles.commTypeBadge}
              title={COMM_TYPE_LABELS[commType] || commType}
              style={{
                backgroundColor: `color-mix(in srgb, ${typeColor} 12%, transparent)`,
                color: typeColor,
                borderColor: `color-mix(in srgb, ${typeColor} 25%, transparent)`,
              }}
            >
              {commType}
            </span>
            <span className={styles.commDate}>{commDate}</span>
          </div>
          <h4 className={styles.commSummary}>{comm.summary || 'No summary available'}</h4>
          {displayNumber && (
            <span className={styles.commNumber}>
              {displayNumber}
              {occurrence && occurrence > 1 && (
                <span className={styles.duplicateBadge} title="Duplicate bulletin number">
                  dup
                </span>
              )}
            </span>
          )}
        </div>

        <div className={styles.commMeta}>
          {comm.matchedKeywords.length > 0 && (
            <div className={styles.commKeywords}>
              {comm.matchedKeywords.map((kw) => (
                <span key={kw} className="badge badge-success">
                  <Tag size={10} />
                  {kw}
                </span>
              ))}
            </div>
          )}
          <span className={styles.docCount}>
            <FileText size={14} />
            {comm.associatedDocuments.length}
          </span>
          <button className="btn btn-ghost btn-icon btn-sm">
            {isExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className={`${styles.commDetails} animate-fade-in`}>
          {comm.detailsSummary && (
            <p className={styles.detailsSummary}>{comm.detailsSummary}</p>
          )}

          {comm.associatedProducts.length > 0 && (
            <div className={styles.productsSection}>
              <h5>Associated Products</h5>
              <div className={styles.productsList}>
                {comm.associatedProducts.slice(0, 5).map((p, i) => (
                  <span key={i} className={styles.productTag}>
                    {p.productYear} {p.productModel}
                  </span>
                ))}
                {comm.associatedProducts.length > 5 && (
                  <span className={`${styles.productTag} ${styles.productTagMore}`}>
                    +{comm.associatedProducts.length - 5} more
                  </span>
                )}
              </div>
            </div>
          )}

          {comm.associatedDocuments.length > 0 && (
            <div className={styles.documentsSection}>
              <h5>Documents</h5>
              <ul className={styles.documentsList}>
                {comm.associatedDocuments.map((doc, i) => (
                  <li key={i}>
                    <a href={doc.url} target="_blank" rel="noopener noreferrer">
                      <FileText size={14} />
                      <span>{doc.summary}</span>
                      <ExternalLink size={12} />
                    </a>
                    {doc.loadDate && (
                      <span className={styles.docDate}>
                        {format(new Date(doc.loadDate), 'MMM d, yyyy')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function CommunicationList({ communications, isLoading }: Props) {
  const occurrenceMap = useMemo(() => {
    const counts = new Map<string, number>();
    const occurrences = new Map<string, number>();

    for (const comm of communications) {
      const num = comm.communicationNumber;
      if (num) {
        const currentCount = counts.get(num) || 0;
        counts.set(num, currentCount + 1);
        occurrences.set(String(comm.nhtsaId), currentCount + 1);
      }
    }

    return occurrences;
  }, [communications]);

  if (isLoading) {
    return (
      <div className={styles.commListLoading}>
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton" style={{ height: 100 }} />
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
          Try adjusting your filters or fetch the latest data.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.commList}>
      {communications.map((comm) => (
        <CommunicationRow
          key={comm.nhtsaId}
          comm={comm}
          occurrence={occurrenceMap.get(String(comm.nhtsaId))}
        />
      ))}
    </div>
  );
}
