import styles from './SidebarVehicleItem.module.css';
import type { Vehicle } from '../../client';

interface SidebarVehicleItemProps {
  vehicle: Vehicle;
  isSelected: boolean;
  onSelect: () => void;
}

export function SidebarVehicleItem({ vehicle, isSelected, onSelect }: SidebarVehicleItemProps) {
  return (
    <button
      className={styles.item}
      data-selected={isSelected}
      onClick={onSelect}
    >
      <div className={styles.info}>
        <span className={styles.name}>
          {vehicle.year} {vehicle.model}
        </span>
      </div>
      {vehicle.commCount !== undefined && vehicle.commCount > 0 && (
        <span className={styles.badge}>
          {vehicle.commCount}
        </span>
      )}
    </button>
  );
}
