import { Plus } from 'lucide-react';
import { SidebarVehicleItem } from './SidebarVehicleItem';
import styles from './Sidebar.module.css';
import type { Vehicle } from '../../client';

interface SidebarProps {
  vehicles: Vehicle[];
  selectedVehicleId: number | null;
  onSelectVehicle: (id: number | null) => void;
  onAddVehicle: () => void;
  isLoading?: boolean;
}

export function Sidebar({
  vehicles,
  selectedVehicleId,
  onSelectVehicle,
  onAddVehicle,
  isLoading,
}: SidebarProps) {
  return (
    <div className={styles.sidebar}>
      <div className={styles.header}>
        <button
          className={styles.overviewButton}
          onClick={() => onSelectVehicle(null)}
          data-active={selectedVehicleId === null}
        >
          All Vehicles
        </button>
        <button
          className={styles.addButton}
          onClick={onAddVehicle}
          title="Add Vehicle"
        >
          <Plus size={16} />
        </button>
      </div>
      <div className={styles.vehicleList}>
        {isLoading ? (
          Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className={styles.skeleton} />
          ))
        ) : (
          vehicles.map((vehicle) => (
            <SidebarVehicleItem
              key={vehicle.vehicleId}
              vehicle={vehicle}
              isSelected={vehicle.vehicleId === selectedVehicleId}
              onSelect={() => onSelectVehicle(vehicle.vehicleId)}
            />
          ))
        )}
      </div>
    </div>
  );
}
