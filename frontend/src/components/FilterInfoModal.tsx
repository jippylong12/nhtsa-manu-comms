/* Filter Information Modal - Explains what each filter means */

import { X, Info, Search, Filter, AlertTriangle, Wrench, FileText, RefreshCw, Shield, Star, Bookmark, HelpCircle } from 'lucide-react';
import { COMM_TYPE_COLORS, COMM_TYPE_LABELS } from '../client';
import type { CommType } from '../client';

interface FilterInfoModalProps {
  isOpen: boolean;
  onClose: () => void;
}

// Extended descriptions for each communication type
const COMM_TYPE_DESCRIPTIONS: Record<CommType, { icon: React.ReactNode; description: string; priority: 'high' | 'medium' | 'low' }> = {
  TSB: {
    icon: <FileText size={16} />,
    description: 'Official bulletins issued by manufacturers to address specific problems. These provide repair procedures and parts information for known issues.',
    priority: 'high',
  },
  PIT: {
    icon: <Wrench size={16} />,
    description: 'Early-stage technical information shared before a formal TSB is issued. Helps technicians diagnose and address emerging issues.',
    priority: 'medium',
  },
  PIC: {
    icon: <Info size={16} />,
    description: 'Information aimed at customer-facing concerns. Addresses common questions or issues that might be raised by vehicle owners.',
    priority: 'medium',
  },
  PIP: {
    icon: <Bookmark size={16} />,
    description: 'Preliminary information related to parts availability, updates, or replacements. Useful for tracking component changes.',
    priority: 'low',
  },
  SB: {
    icon: <FileText size={16} />,
    description: 'General service bulletins that provide maintenance and repair guidance. May overlap with TSBs but are often manufacturer-specific.',
    priority: 'medium',
  },
  TB: {
    icon: <Wrench size={16} />,
    description: 'Technical bulletins with detailed technical information for service professionals. Focus on diagnostic and repair procedures.',
    priority: 'medium',
  },
  IB: {
    icon: <Info size={16} />,
    description: 'Informational bulletins that provide general updates without specific repair procedures. May cover policy or procedural changes.',
    priority: 'low',
  },
  SU: {
    icon: <RefreshCw size={16} />,
    description: 'Service updates for previously issued information. May contain corrections, additional details, or revised procedures.',
    priority: 'medium',
  },
  WA: {
    icon: <Shield size={16} />,
    description: 'Warranty-related communications. Cover warranty extensions, special programs, or warranty policy clarifications.',
    priority: 'medium',
  },
  CSP: {
    icon: <Star size={16} />,
    description: 'Customer satisfaction programs offering goodwill repairs or extended coverage beyond standard warranty, often for known issues.',
    priority: 'high',
  },
  RC: {
    icon: <AlertTriangle size={16} />,
    description: 'Safety recalls and campaigns requiring immediate attention. These address safety defects that could harm occupants or others.',
    priority: 'high',
  },
  SC: {
    icon: <Shield size={16} />,
    description: 'Special coverage programs extending warranty for specific components. Often issued for parts with higher-than-expected failure rates.',
    priority: 'medium',
  },
  NA: {
    icon: <FileText size={16} />,
    description: 'North American bulletins identified by the XX-NA-XXX format. May contain region-specific service information.',
    priority: 'low',
  },
  OTHER: {
    icon: <HelpCircle size={16} />,
    description: 'Communications that don\'t fit into standard categories. Review individually to understand their relevance.',
    priority: 'low',
  },
};

export function FilterInfoModal({ isOpen, onClose }: FilterInfoModalProps) {
  if (!isOpen) return null;

  const highPriority = (Object.keys(COMM_TYPE_DESCRIPTIONS) as CommType[]).filter(
    (type) => COMM_TYPE_DESCRIPTIONS[type].priority === 'high'
  );
  const mediumPriority = (Object.keys(COMM_TYPE_DESCRIPTIONS) as CommType[]).filter(
    (type) => COMM_TYPE_DESCRIPTIONS[type].priority === 'medium'
  );
  const lowPriority = (Object.keys(COMM_TYPE_DESCRIPTIONS) as CommType[]).filter(
    (type) => COMM_TYPE_DESCRIPTIONS[type].priority === 'low'
  );

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal filter-info-modal animate-slide-up" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-icon">
            <Info size={24} />
          </div>
          <h2>Understanding Filters</h2>
          <button className="btn btn-ghost btn-icon" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="modal-body">
          {/* Search Section */}
          <section className="info-section">
            <h3 className="section-title">
              <Search size={18} />
              Search
            </h3>
            <p className="section-description">
              Search by communication number or keywords in the summary. Results are filtered in real-time as you type.
            </p>
          </section>

          {/* Category Filters Section */}
          <section className="info-section">
            <h3 className="section-title">
              <Filter size={18} />
              Category Filters
            </h3>
            <p className="section-description">
              Click on category buttons to filter communications by type. Click the same category again to clear the filter.
              Categories are also clickable in the stats cards above.
            </p>
          </section>

          {/* Priority Legend */}
          <div className="priority-legend">
            <span className="priority-badge priority-high">High Priority</span>
            <span className="priority-badge priority-medium">Medium Priority</span>
            <span className="priority-badge priority-low">Low Priority</span>
          </div>

          {/* High Priority Types */}
          <section className="info-section">
            <h3 className="section-title priority-section-title">
              <AlertTriangle size={18} />
              High Priority Communications
            </h3>
            <div className="type-list">
              {highPriority.map((type) => (
                <TypeCard key={type} type={type} />
              ))}
            </div>
          </section>

          {/* Medium Priority Types */}
          <section className="info-section">
            <h3 className="section-title priority-section-title">
              <Wrench size={18} />
              Service & Technical Communications
            </h3>
            <div className="type-list">
              {mediumPriority.map((type) => (
                <TypeCard key={type} type={type} />
              ))}
            </div>
          </section>

          {/* Low Priority Types */}
          <section className="info-section">
            <h3 className="section-title priority-section-title">
              <Info size={18} />
              Informational Communications
            </h3>
            <div className="type-list">
              {lowPriority.map((type) => (
                <TypeCard key={type} type={type} />
              ))}
            </div>
          </section>
        </div>

        <div className="modal-footer">
          <button className="btn btn-primary" onClick={onClose}>
            Got it!
          </button>
        </div>

        <style>{`
                    .modal-overlay {
                        position: fixed;
                        inset: 0;
                        background: hsla(0, 0%, 0%, 0.7);
                        backdrop-filter: blur(4px);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        z-index: 1000;
                        padding: var(--space-lg);
                    }

                    .filter-info-modal {
                        background: var(--bg-surface);
                        border: 1px solid var(--border-subtle);
                        border-radius: var(--radius-xl);
                        width: 100%;
                        max-width: 700px;
                        max-height: 85vh;
                        overflow: hidden;
                        display: flex;
                        flex-direction: column;
                    }

                    .filter-info-modal .modal-header {
                        display: flex;
                        align-items: center;
                        gap: var(--space-md);
                        padding: var(--space-lg);
                        border-bottom: 1px solid var(--border-subtle);
                    }

                    .filter-info-modal .modal-icon {
                        width: 40px;
                        height: 40px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
                        border-radius: var(--radius-md);
                        color: white;
                    }

                    .filter-info-modal .modal-header h2 {
                        flex: 1;
                        font-size: 1.25rem;
                        margin: 0;
                    }

                    .filter-info-modal .modal-body {
                        padding: var(--space-lg);
                        overflow-y: auto;
                        flex: 1;
                    }

                    .info-section {
                        margin-bottom: var(--space-lg);
                    }

                    .section-title {
                        display: flex;
                        align-items: center;
                        gap: var(--space-sm);
                        font-size: 1rem;
                        font-weight: 600;
                        color: var(--text-primary);
                        margin-bottom: var(--space-sm);
                    }

                    .priority-section-title {
                        margin-top: var(--space-md);
                    }

                    .section-description {
                        color: var(--text-secondary);
                        line-height: 1.6;
                        margin: 0;
                    }

                    .priority-legend {
                        display: flex;
                        gap: var(--space-md);
                        flex-wrap: wrap;
                        margin-bottom: var(--space-lg);
                        padding: var(--space-md);
                        background: var(--bg-elevated);
                        border-radius: var(--radius-md);
                        border: 1px solid var(--border-subtle);
                    }

                    .priority-badge {
                        font-size: 0.75rem;
                        font-weight: 600;
                        padding: var(--space-xs) var(--space-md);
                        border-radius: var(--radius-full);
                    }

                    .priority-high {
                        background: hsla(0, 85%, 55%, 0.15);
                        color: hsl(0, 85%, 55%);
                        border: 1px solid hsla(0, 85%, 55%, 0.3);
                    }

                    .priority-medium {
                        background: hsla(38, 92%, 50%, 0.15);
                        color: hsl(38, 92%, 50%);
                        border: 1px solid hsla(38, 92%, 50%, 0.3);
                    }

                    .priority-low {
                        background: hsla(215, 15%, 50%, 0.15);
                        color: var(--text-secondary);
                        border: 1px solid var(--border-subtle);
                    }

                    .type-list {
                        display: flex;
                        flex-direction: column;
                        gap: var(--space-sm);
                    }

                    .type-card {
                        display: flex;
                        align-items: flex-start;
                        gap: var(--space-md);
                        padding: var(--space-md);
                        background: var(--bg-elevated);
                        border: 1px solid var(--border-subtle);
                        border-radius: var(--radius-md);
                        border-left: 3px solid var(--type-color);
                        transition: all var(--transition-fast);
                    }

                    .type-card:hover {
                        background: var(--bg-hover);
                        border-color: var(--border-default);
                    }

                    .type-badge {
                        display: flex;
                        align-items: center;
                        gap: var(--space-xs);
                        padding: var(--space-xs) var(--space-sm);
                        background: var(--type-color);
                        color: white;
                        font-size: 0.75rem;
                        font-weight: 700;
                        border-radius: var(--radius-sm);
                        min-width: 60px;
                        justify-content: center;
                        flex-shrink: 0;
                    }

                    .type-info {
                        flex: 1;
                    }

                    .type-label {
                        font-weight: 600;
                        color: var(--text-primary);
                        margin-bottom: 2px;
                    }

                    .type-description {
                        font-size: 0.875rem;
                        color: var(--text-secondary);
                        line-height: 1.5;
                        margin: 0;
                    }

                    .filter-info-modal .modal-footer {
                        display: flex;
                        justify-content: flex-end;
                        gap: var(--space-md);
                        padding: var(--space-lg);
                        border-top: 1px solid var(--border-subtle);
                        background: var(--bg-elevated);
                    }

                    @media (max-width: 600px) {
                        .filter-info-modal {
                            max-height: 90vh;
                        }

                        .priority-legend {
                            flex-direction: column;
                            align-items: flex-start;
                        }

                        .type-card {
                            flex-direction: column;
                        }

                        .type-badge {
                            min-width: auto;
                        }
                    }
                `}</style>
      </div>
    </div>
  );
}

// Individual Type Card Component
function TypeCard({ type }: { type: CommType }) {
  const { icon, description } = COMM_TYPE_DESCRIPTIONS[type];
  const label = COMM_TYPE_LABELS[type];
  const color = COMM_TYPE_COLORS[type];

  return (
    <div
      className="type-card"
      style={{ '--type-color': color } as React.CSSProperties}
    >
      <div className="type-badge" style={{ background: color }}>
        {icon}
        {type}
      </div>
      <div className="type-info">
        <div className="type-label">{label}</div>
        <p className="type-description">{description}</p>
      </div>
    </div>
  );
}
