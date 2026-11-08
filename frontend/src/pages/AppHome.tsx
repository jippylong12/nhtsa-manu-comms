/* /app: the signed-in user's cars as cards, with add and remove. */

import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Car, Plus } from 'lucide-react';

import type { VehicleCreate } from '@/client';
import { AddVehicleModal } from '../features/vehicles/components/AddVehicleModal';
import { MyVehicleCard } from '../features/my-vehicles/components/MyVehicleCard';
import {
  useAddVehicle,
  useMeQuery,
  useRemoveVehicle,
} from '../features/my-vehicles/hooks/useMyVehicles';
import { useToast } from '../features/my-vehicles/hooks/useToast';
import styles from './AppHome.module.css';

export function AppHome() {
  const location = useLocation();
  const handedToast = (location.state as { toast?: string } | null)?.toast ?? null;
  const { toast, showToast } = useToast(handedToast);
  const [modalOpen, setModalOpen] = useState(false);
  const meQuery = useMeQuery();
  const addVehicle = useAddVehicle();
  const removeVehicle = useRemoveVehicle();
  const vehicles = meQuery.data?.vehicles ?? [];

  const handleAdd = (data: VehicleCreate) => {
    addVehicle.mutate(
      { year: Number(data.year), make: data.make, model: data.model },
      {
        onSuccess: () => setModalOpen(false),
        onError: (err) => showToast(err.message || 'We could not add that car.'),
      }
    );
  };

  const handleRemove = (id: number) => {
    removeVehicle.mutate(id, {
      onError: (err) => showToast(err.message || 'We could not remove that car.'),
    });
  };

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.heading}>My vehicles</h1>
        <button type="button" className={`btn btn-primary ${styles.add}`} onClick={() => setModalOpen(true)}>
          <Plus size={18} /> Add vehicle
        </button>
      </div>

      {meQuery.isPending && (
        <div className={styles.grid} aria-busy="true" aria-label="Loading your vehicles">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 190 }} />
          ))}
        </div>
      )}

      {meQuery.isError && (
        <div className={styles.error} role="alert">
          <span>We couldn't load your vehicles.</span>
          <button type="button" className={`btn btn-secondary ${styles.retry}`} onClick={() => void meQuery.refetch()}>
            Try again
          </button>
        </div>
      )}

      {meQuery.isSuccess && vehicles.length === 0 && (
        <div className="empty-state">
          <Car className="empty-state-icon" />
          <h2 className="empty-state-title">Add your first car to see its bulletins</h2>
          <button type="button" className="btn btn-primary" onClick={() => setModalOpen(true)}>
            <Plus size={18} /> Add my car
          </button>
        </div>
      )}

      {vehicles.length > 0 && (
        <div className={styles.grid}>
          {vehicles.map((v) => (
            <MyVehicleCard
              key={v.vehicleId}
              vehicle={v}
              me={meQuery.data}
              onRemove={handleRemove}
              removing={removeVehicle.isPending && removeVehicle.variables === v.vehicleId}
            />
          ))}
        </div>
      )}

      <AddVehicleModal
        mode="product"
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleAdd}
        isLoading={addVehicle.isPending}
      />
      {toast}
    </div>
  );
}
