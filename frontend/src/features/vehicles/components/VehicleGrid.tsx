/* Vehicle Grid Component - Overview/Landing View */

import { Plus, Car } from 'lucide-react';
import { VehicleCard } from './VehicleCard';
import { RefreshSurface } from '../../refresh/components/RefreshSurface';
import type { useRefreshFlow } from '../../refresh/hooks/useRefresh';
import type { Vehicle } from '@/client';

import styles from './VehicleGrid.module.css';

interface VehicleGridProps {
  vehicles: Vehicle[];
  isLoading: boolean;
  onSelectVehicle: (vehicleId: number) => void;
  onDeleteVehicle: (vehicleId: number) => void;
  onRefreshVehicle: (vehicleId: number) => void;
  onAddVehicle: () => void;
  refresh: ReturnType<typeof useRefreshFlow>;
  /** The vehicle whose refresh button was pressed, if any. */
  refreshTargetId: number | null;
}

export function VehicleGrid({
  vehicles,
  isLoading,
  onSelectVehicle,
  onDeleteVehicle,
  onRefreshVehicle,
  onAddVehicle,
  refresh,
  refreshTargetId,
}: VehicleGridProps) {
  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.heroSection}>
          <div className={styles.heroContent}>
            <h1 className={styles.heroTitle}>
              Track Vehicle
              <span className={styles.gradientText}>Communications</span>
            </h1>
            <p className={styles.heroDescription}>
              Monitor NHTSA manufacturer communications for software updates,
              safety recalls, and service bulletins for your vehicles.
            </p>
            <button
              className="btn btn-primary btn-lg"
              onClick={onAddVehicle}
            >
              <Plus size={20} />
              Add Vehicle
            </button>
          </div>
        </div>

        <RefreshSurface flow={refresh} />

        {isLoading ? (
          <div className={styles.vehiclesGrid}>
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton" style={{ height: 200 }} />
            ))}
          </div>
        ) : vehicles.length === 0 ? (
          <div className={`empty-state glass-card ${styles.emptyState}`}>
            <Car className="empty-state-icon" size={64} />
            <h3 className="empty-state-title">No vehicles added yet</h3>
            <p className="empty-state-description">
              Add a vehicle to start tracking manufacturer communications.
            </p>
            <button
              className="btn btn-primary"
              onClick={onAddVehicle}
            >
              <Plus size={18} />
              Add Your First Vehicle
            </button>
          </div>
        ) : (
          <div className={styles.vehiclesGrid}>
            {vehicles.map((vehicle: Vehicle) => (
              <VehicleCard
                key={vehicle.vehicleId}
                vehicle={vehicle}
                onRefresh={onRefreshVehicle}
                onDelete={onDeleteVehicle}
                onSelect={onSelectVehicle}
                // Only the card actually being refreshed spins. Before, every
                // card in the grid did.
                isRefreshing={
                  refreshTargetId === vehicle.vehicleId &&
                  (refresh.isAsking || refresh.isStarting || refresh.isActive)
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
