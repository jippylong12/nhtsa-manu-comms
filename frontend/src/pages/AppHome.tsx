/* /app: the signed-in user's cars as cards, with add and remove. */

import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Car, Plus, Lock } from 'lucide-react';

import type { VehicleCreate } from '@/client';
import { AddVehicleModal } from '../features/vehicles/components/AddVehicleModal';
import { MyVehicleCard } from '../features/my-vehicles/components/MyVehicleCard';
import {
  useAddVehicle,
  useMeQuery,
  useRemoveVehicle,
} from '../features/my-vehicles/hooks/useMyVehicles';
import { PastDueBanner } from '../features/billing/components/PastDueBanner';
import { isPastDue, planOf } from '../features/billing/plan';
import { useToast } from '../features/my-vehicles/hooks/useToast';
import { ApiError } from '@/client';
import { useCheckout, usePortal } from '../features/billing/hooks/useBilling';
import {
  ALL_ACCESS_CARS,
  ALL_ACCESS_PRICE_LABEL,
  MONTHLY_CARS,
  MONTHLY_PRICE_LABEL,
} from '../features/billing/prices';
import styles from './AppHome.module.css';

export function AppHome() {
  const location = useLocation();
  const handedToast = (location.state as { toast?: string } | null)?.toast ?? null;
  const { toast, showToast } = useToast(handedToast);
  const [modalOpen, setModalOpen] = useState(false);
  const [sampleLimit, setSampleLimit] = useState<string | null>(null);
  const checkout = useCheckout();
  const portal = usePortal();
  const meQuery = useMeQuery();
  const plan = planOf(meQuery.data);
  const leaving = checkout.isPending || checkout.isSuccess || portal.isPending || portal.isSuccess;
  const addVehicle = useAddVehicle();
  const removeVehicle = useRemoveVehicle();
  const vehicles = meQuery.data?.vehicles ?? [];

  const handleAdd = (data: VehicleCreate) => {
    addVehicle.mutate(
      { year: Number(data.year), make: data.make, model: data.model },
      {
        onSuccess: () => setModalOpen(false),
        onError: (err) => {
          // 402: the free samples or the plan's cars are used up. An offer, not an error toast.
          if (err instanceof ApiError && err.status === 402) {
            setModalOpen(false);
            setSampleLimit(err.message);
            return;
          }
          showToast(err.message || 'We could not add that car.');
        },
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
      {isPastDue(meQuery.data) && <PastDueBanner />}

      <div className={styles.head}>
        <h1 className={styles.heading}>My vehicles</h1>
        <button type="button" className={`btn btn-primary ${styles.add}`} onClick={() => setModalOpen(true)}>
          <Plus size={18} /> Add vehicle
        </button>
      </div>

      {sampleLimit && (
        <div className={styles.upsell} role="status">
          <span className={styles.upsellIcon} aria-hidden="true">
            <Lock size={18} />
          </span>
          <div className={styles.upsellText}>
            <p className={styles.upsellTitle}>{sampleLimit}</p>
            {plan === null && (
              <p className={styles.upsellBody}>
                Monthly covers {MONTHLY_CARS} cars and emails you the morning a new bulletin lands
                for one of them. All access covers {ALL_ACCESS_CARS}.
              </p>
            )}
            {plan === 'monthly' && (
              <p className={styles.upsellBody}>
                All access is {ALL_ACCESS_PRICE_LABEL} for {ALL_ACCESS_CARS} cars. You change plan
                on the billing page.
              </p>
            )}
            {checkout.isError && <p className={styles.upsellError}>{checkout.error.message}</p>}
            {portal.isError && <p className={styles.upsellError}>{portal.error.message}</p>}
          </div>
          <div className={styles.upsellActions}>
            {plan === null && (
              <>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={leaving}
                  onClick={() => checkout.mutate({ kind: 'subscription', plan: 'monthly' })}
                >
                  {leaving && checkout.variables?.plan === 'monthly'
                    ? 'Opening checkout...'
                    : `Monthly ${MONTHLY_PRICE_LABEL}`}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={leaving}
                  onClick={() => checkout.mutate({ kind: 'subscription', plan: 'all_access' })}
                >
                  {leaving && checkout.variables?.plan === 'all_access'
                    ? 'Opening checkout...'
                    : `All access ${ALL_ACCESS_PRICE_LABEL}`}
                </button>
              </>
            )}
            {plan === 'monthly' && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={leaving}
                onClick={() => portal.mutate()}
              >
                {leaving ? 'Opening...' : 'Change plan'}
              </button>
            )}
            <button type="button" className="btn btn-ghost" onClick={() => setSampleLimit(null)}>
              Not now
            </button>
          </div>
        </div>
      )}

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
