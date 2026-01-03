/* Communication List Component */

import { format } from 'date-fns';
import { FileText, ExternalLink, Tag, ChevronDown, ChevronUp } from 'lucide-react';
import { useState, useMemo } from 'react';
import type { Communication, CommType } from '@/client';
import { COMM_TYPE_COLORS, COMM_TYPE_LABELS } from '@/client';

interface Props {
  communications: Communication[];
  isLoading?: boolean;
}

interface RowProps {
  comm: Communication;
  occurrence?: number; // Which occurrence this is (2 means second time seeing this number)
}

function CommunicationRow({ comm, occurrence }: RowProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const commDate = comm.communicationDate
    ? format(new Date(comm.communicationDate), 'MMM d, yyyy')
    : 'Unknown';

  const commType = (comm.communicationType || 'OTHER') as CommType;
  const typeColor = COMM_TYPE_COLORS[commType] || COMM_TYPE_COLORS.OTHER;

  // Display communication number with occurrence indicator
  const displayNumber = comm.communicationNumber
    ? occurrence && occurrence > 1
      ? `${comm.communicationNumber} (${occurrence})`
      : comm.communicationNumber
    : null;

  return (
    <div className="comm-row" style={{ borderLeftColor: typeColor }}>
      <div className="comm-header" onClick={() => setIsExpanded(!isExpanded)}>
        <div className="comm-info">
          <div className="comm-top-row">
            <span
              className="comm-type-badge"
              title={COMM_TYPE_LABELS[commType] || commType}
              style={{
                backgroundColor: `${typeColor}20`,
                color: typeColor,
                borderColor: `${typeColor}40`,
              }}
            >
              {commType}
            </span>
            <span className="comm-date">{commDate}</span>
          </div>
          <h4 className="comm-summary">{comm.summary || 'No summary available'}</h4>
          {displayNumber && (
            <span className="comm-number">
              {displayNumber}
              {occurrence && occurrence > 1 && (
                <span className="duplicate-badge" title="Duplicate bulletin number">
                  dup
                </span>
              )}
            </span>
          )}
        </div>

        <div className="comm-meta">
          {comm.matchedKeywords.length > 0 && (
            <div className="comm-keywords">
              {comm.matchedKeywords.map((kw) => (
                <span key={kw} className="badge badge-success">
                  <Tag size={10} />
                  {kw}
                </span>
              ))}
            </div>
          )}
          <span className="doc-count">
            <FileText size={14} />
            {comm.associatedDocuments.length}
          </span>
          <button className="btn btn-ghost btn-icon btn-sm">
            {isExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="comm-details animate-fade-in">
          {comm.detailsSummary && (
            <p className="details-summary">{comm.detailsSummary}</p>
          )}

          {comm.associatedProducts.length > 0 && (
            <div className="products-section">
              <h5>Associated Products</h5>
              <div className="products-list">
                {comm.associatedProducts.slice(0, 5).map((p, i) => (
                  <span key={i} className="product-tag">
                    {p.productYear} {p.productModel}
                  </span>
                ))}
                {comm.associatedProducts.length > 5 && (
                  <span className="product-tag more">
                    +{comm.associatedProducts.length - 5} more
                  </span>
                )}
              </div>
            </div>
          )}

          {comm.associatedDocuments.length > 0 && (
            <div className="documents-section">
              <h5>Documents</h5>
              <ul className="documents-list">
                {comm.associatedDocuments.map((doc, i) => (
                  <li key={i}>
                    <a href={doc.url} target="_blank" rel="noopener noreferrer">
                      <FileText size={14} />
                      <span>{doc.summary}</span>
                      <ExternalLink size={12} />
                    </a>
                    {doc.loadDate && (
                      <span className="doc-date">
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

      <style>{`
        .comm-row {
          background: var(--bg-surface);
          border: 1px solid var(--border-subtle);
          border-left: 4px solid;
          border-radius: var(--radius-lg);
          overflow: hidden;
          transition: all var(--transition-default);
        }

        .comm-row:hover {
          border-color: var(--border-default);
          border-left-width: 4px;
        }

        .comm-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          padding: var(--space-md) var(--space-lg);
          cursor: pointer;
          gap: var(--space-md);
        }

        .comm-info {
          flex: 1;
          min-width: 0;
        }

        .comm-top-row {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          margin-bottom: var(--space-xs);
        }

        .comm-type-badge {
          font-size: 0.625rem;
          font-weight: 700;
          padding: 2px 6px;
          border-radius: var(--radius-sm);
          border: 1px solid;
          text-transform: uppercase;
          letter-spacing: 0.03em;
        }

        .comm-date {
          font-size: 0.75rem;
          font-weight: 500;
          color: var(--text-muted);
          letter-spacing: 0.02em;
        }

        .comm-summary {
          font-size: 0.9375rem;
          font-weight: 500;
          margin: var(--space-xs) 0;
          color: var(--text-primary);
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .comm-number {
          font-size: 0.75rem;
          color: var(--text-muted);
          font-family: var(--font-mono);
          display: inline-flex;
          align-items: center;
          gap: var(--space-xs);
        }

        .duplicate-badge {
          font-size: 0.625rem;
          font-weight: 600;
          padding: 1px 4px;
          background: hsl(38, 92%, 50%);
          color: white;
          border-radius: var(--radius-sm);
          text-transform: uppercase;
        }

        .comm-meta {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          flex-shrink: 0;
        }

        .comm-keywords {
          display: flex;
          gap: 4px;
        }

        .comm-keywords .badge {
          font-size: 0.625rem;
        }

        .doc-count {
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 0.75rem;
          color: var(--text-muted);
          padding: 4px 8px;
          background: var(--bg-elevated);
          border-radius: var(--radius-sm);
        }

        .comm-details {
          padding: 0 var(--space-lg) var(--space-lg);
          border-top: 1px solid var(--border-subtle);
          background: var(--bg-elevated);
        }

        .details-summary {
          font-size: 0.875rem;
          color: var(--text-secondary);
          margin: var(--space-md) 0;
          line-height: 1.6;
        }

        .products-section,
        .documents-section {
          margin-top: var(--space-md);
        }

        .products-section h5,
        .documents-section h5 {
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--text-muted);
          text-transform: uppercase;
          letter-spacing: 0.05em;
          margin-bottom: var(--space-sm);
        }

        .products-list {
          display: flex;
          flex-wrap: wrap;
          gap: var(--space-xs);
        }

        .product-tag {
          font-size: 0.75rem;
          padding: 2px 8px;
          background: var(--bg-hover);
          border-radius: var(--radius-sm);
          color: var(--text-secondary);
        }

        .product-tag.more {
          color: var(--text-muted);
          font-style: italic;
        }

        .documents-list {
          list-style: none;
          display: flex;
          flex-direction: column;
          gap: var(--space-xs);
        }

        .documents-list li {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }

        .documents-list a {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          font-size: 0.875rem;
          color: var(--text-secondary);
          padding: var(--space-sm);
          border-radius: var(--radius-sm);
          transition: all var(--transition-fast);
          flex: 1;
        }

        .documents-list a:hover {
          background: var(--bg-hover);
          color: var(--color-primary);
        }

        .documents-list a span {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .doc-date {
          font-size: 0.75rem;
          color: var(--text-muted);
        }

        @media (max-width: 640px) {
          .comm-header {
            flex-direction: column;
          }

          .comm-meta {
            align-self: flex-start;
            margin-top: var(--space-sm);
          }
        }
      `}</style>
    </div>
  );
}

export function CommunicationList({ communications, isLoading }: Props) {
  // Track duplicate communication numbers
  const occurrenceMap = useMemo(() => {
    const counts = new Map<string, number>();
    const occurrences = new Map<string, number>(); // nhtsaId -> occurrence number

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
      <div className="comm-list-loading">
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
    <div className="comm-list">
      {communications.map((comm) => (
        <CommunicationRow
          key={comm.nhtsaId}
          comm={comm}
          occurrence={occurrenceMap.get(String(comm.nhtsaId))}
        />
      ))}

      <style>{`
        .comm-list {
          display: flex;
          flex-direction: column;
          gap: var(--space-md);
        }

        .comm-list-loading {
          display: flex;
          flex-direction: column;
          gap: var(--space-md);
        }
      `}</style>
    </div>
  );
}
