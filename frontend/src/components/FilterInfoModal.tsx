/* Filter Information Modal - Explains what each filter means */

import { X, Info, Search, Filter, AlertTriangle, Wrench, FileText, RefreshCw, Shield, Star, Bookmark, HelpCircle } from 'lucide-react';
import { COMM_TYPE_COLORS, COMM_TYPE_LABELS, PRIORITY_COLORS } from '../client';
import type { CommType, CommPriority } from '../client';
import styles from './FilterInfoModal.module.css';

interface FilterInfoModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const COMM_TYPE_DESCRIPTIONS: Record<CommType, { icon: React.ReactNode; description: string; priority: CommPriority }> = {
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
  BL: {
    icon: <FileText size={16} />,
    description: 'Generic bulletins classified from the attached document type. General service or informational content.',
    priority: 'low',
  },
  OL: {
    icon: <Info size={16} />,
    description: 'Owner letters mailed to vehicle owners about a program, recall, or important information for their vehicle.',
    priority: 'low',
  },
  DL: {
    icon: <FileText size={16} />,
    description: 'Dealer letters sent to dealerships with instructions or information about a service program.',
    priority: 'low',
  },
  MC: {
    icon: <HelpCircle size={16} />,
    description: 'Generic manufacturer communications that do not carry a more specific type classification.',
    priority: 'low',
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
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={`${styles.filterInfoModal} animate-slide-up`} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div className={styles.modalIcon}>
            <Info size={24} />
          </div>
          <h2>Understanding Filters</h2>
          <button className="btn btn-ghost btn-icon" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className={styles.modalBody}>
          {/* Search Section */}
          <section className={styles.infoSection}>
            <h3 className={styles.sectionTitle}>
              <Search size={18} />
              Search
            </h3>
            <p className={styles.sectionDescription}>
              Search by communication number or keywords in the summary. Results are filtered in real-time as you type.
            </p>
          </section>

          {/* Category Filters Section */}
          <section className={styles.infoSection}>
            <h3 className={styles.sectionTitle}>
              <Filter size={18} />
              Category Filters
            </h3>
            <p className={styles.sectionDescription}>
              Click on category buttons to filter communications by type. Click the same category again to clear the filter.
              Categories are also clickable in the stats cards above.
            </p>
          </section>

          {/* Priority Legend */}
          <div className={styles.priorityLegend}>
            <span
              className={styles.priorityBadge}
              style={{
                background: `color-mix(in srgb, ${PRIORITY_COLORS.high} 20%, transparent)`,
                color: PRIORITY_COLORS.high,
                border: `1px solid color-mix(in srgb, ${PRIORITY_COLORS.high} 40%, transparent)`
              }}
            >
              High Priority
            </span>
            <span
              className={styles.priorityBadge}
              style={{
                background: `color-mix(in srgb, ${PRIORITY_COLORS.medium} 20%, transparent)`,
                color: PRIORITY_COLORS.medium,
                border: `1px solid color-mix(in srgb, ${PRIORITY_COLORS.medium} 40%, transparent)`
              }}
            >
              Medium Priority
            </span>
            <span
              className={styles.priorityBadge}
              style={{
                background: `color-mix(in srgb, ${PRIORITY_COLORS.low} 20%, transparent)`,
                color: PRIORITY_COLORS.low,
                border: `1px solid color-mix(in srgb, ${PRIORITY_COLORS.low} 40%, transparent)`
              }}
            >
              Low Priority
            </span>
          </div>

          {/* High Priority Types */}
          <section className={styles.infoSection}>
            <h3 className={`${styles.sectionTitle} ${styles.prioritySectionTitle}`}>
              <AlertTriangle size={18} />
              High Priority Communications
            </h3>
            <div className={styles.typeList}>
              {highPriority.map((type) => (
                <TypeCard key={type} type={type} />
              ))}
            </div>
          </section>

          {/* Medium Priority Types */}
          <section className={styles.infoSection}>
            <h3 className={`${styles.sectionTitle} ${styles.prioritySectionTitle}`}>
              <Wrench size={18} />
              Service & Technical Communications
            </h3>
            <div className={styles.typeList}>
              {mediumPriority.map((type) => (
                <TypeCard key={type} type={type} />
              ))}
            </div>
          </section>

          {/* Low Priority Types */}
          <section className={styles.infoSection}>
            <h3 className={`${styles.sectionTitle} ${styles.prioritySectionTitle}`}>
              <Info size={18} />
              Informational Communications
            </h3>
            <div className={styles.typeList}>
              {lowPriority.map((type) => (
                <TypeCard key={type} type={type} />
              ))}
            </div>
          </section>
        </div>

        <div className={styles.modalFooter}>
          <button className="btn btn-primary" onClick={onClose}>
            Got it!
          </button>
        </div>
      </div>
    </div>
  );
}

function TypeCard({ type }: { type: CommType }) {
  const { icon, description } = COMM_TYPE_DESCRIPTIONS[type];
  const label = COMM_TYPE_LABELS[type];
  const color = COMM_TYPE_COLORS[type];

  return (
    <div
      className={styles.typeCard}
      style={{ '--type-color': color } as React.CSSProperties}
    >
      <div className={styles.typeBadge} style={{ background: color }}>
        {icon}
        {type}
      </div>
      <div className={styles.typeInfo}>
        <div className={styles.typeLabel}>{label}</div>
        <p className={styles.typeDescription}>{description}</p>
      </div>
    </div>
  );
}
