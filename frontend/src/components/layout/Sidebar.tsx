import { Plus, Stethoscope } from 'lucide-react';
import { SidebarVehicleItem } from './SidebarVehicleItem';
import styles from './Sidebar.module.css';
import type { Vehicle } from '../../client';
import type { AppView } from '../../contexts/AppContext';

interface SidebarProps {
  vehicles: Vehicle[];
  selectedVehicleId: number | null;
  view: AppView;
  onSelectVehicle: (id: number | null) => void;
  onOpenSymptomSearch: () => void;
  onAddVehicle: () => void;
  isLoading?: boolean;
}

export function Sidebar({
  vehicles,
  selectedVehicleId,
  view,
  onSelectVehicle,
  onOpenSymptomSearch,
  onAddVehicle,
  isLoading,
}: SidebarProps) {
  return (
    <div className={styles.sidebar}>
      <nav className={styles.nav}>
        <button
          className={styles.navItem}
          data-active={view === 'symptom-search'}
          onClick={onOpenSymptomSearch}
        >
          <Stethoscope size={16} />
          Symptom Search
        </button>
      </nav>

      <div className={styles.header}>
        <button
          className={styles.overviewButton}
          onClick={() => onSelectVehicle(null)}
          data-active={view === 'browse' && selectedVehicleId === null}
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
              isSelected={view === 'browse' && vehicle.vehicleId === selectedVehicleId}
              onSelect={() => onSelectVehicle(vehicle.vehicleId)}
            />
          ))
        )}
      </div>
    </div>
  );
}
