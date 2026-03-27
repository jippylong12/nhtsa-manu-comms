/* StatsBar Component - Horizontal stat chips for communication type counts */

import styles from './StatsBar.module.css';

interface StatsBarProps {
  stats: Record<string, number>;
  colorMap?: Record<string, string>;
  onStatClick?: (type: string) => void;
  activeTypes?: string[];
}

export function StatsBar({ stats, colorMap, onStatClick, activeTypes = [] }: StatsBarProps) {
  const entries = Object.entries(stats).filter(([, count]) => count > 0);

  if (entries.length === 0) return null;

  return (
    <div className={styles.statsBar}>
      {entries.map(([type, count]) => {
        const color = colorMap?.[type];
        const isActive = activeTypes.includes(type);

        return (
          <button
            key={type}
            className={`${styles.statChip} ${isActive ? styles.active : ''}`}
            style={{ '--type-color': color } as React.CSSProperties}
            onClick={() => onStatClick?.(type)}
            type="button"
          >
            {color && <span className={styles.dot} style={{ backgroundColor: color }} />}
            <span className={styles.count}>{count}</span>
            <span className={styles.label}>{type}</span>
          </button>
        );
      })}
    </div>
  );
}
