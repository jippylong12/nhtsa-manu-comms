/* Main Application Component */

import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Plus, Car, ArrowLeft, Search } from 'lucide-react';

import { Header } from './components/Header';
import { VehicleCard } from './features/vehicles/components/VehicleCard';
import { AddVehicleModal } from './features/vehicles/components/AddVehicleModal';
import { CommunicationList } from './features/communications/components/CommunicationList';
import { FetchProgressBar } from './features/communications/components/FetchProgress';

import {
  useVehiclesQuery,
  useCreateVehicle,
  useDeleteVehicle,
} from './features/vehicles/hooks/useVehicles';
import {
  useCommunicationsQuery,
  useFetchCommunications,
} from './features/communications/hooks/useCommunications';

import type { Vehicle } from './client';

// Query Client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
});

// Dashboard View
function Dashboard() {
  const [showAddModal, setShowAddModal] = useState(false);
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null);

  const { data: vehiclesData, isLoading: vehiclesLoading } = useVehiclesQuery();
  const { mutate: createVehicle, isPending: isCreating } = useCreateVehicle();
  const { mutate: deleteVehicle } = useDeleteVehicle();

  const { fetch, progress, isFetching, reset } = useFetchCommunications();

  const { data: commsData, isLoading: commsLoading } = useCommunicationsQuery(
    selectedVehicleId ? { vehicleId: selectedVehicleId, perPage: 100 } : {}
  );

  const selectedVehicle = vehiclesData?.items.find(
    (v) => v.vehicleId === selectedVehicleId
  );

  const handleAddVehicle = (data: {
    vehicleId: number;
    year: string;
    model: string;
    keywords: string[];
  }) => {
    createVehicle(data, {
      onSuccess: () => {
        setShowAddModal(false);
      },
    });
  };

  const handleDeleteVehicle = (vehicleId: number) => {
    if (confirm('Are you sure you want to remove this vehicle?')) {
      deleteVehicle(vehicleId);
      if (selectedVehicleId === vehicleId) {
        setSelectedVehicleId(null);
      }
    }
  };

  const handleFetchVehicle = (vehicleId: number) => {
    fetch(vehicleId, false);
  };

  // Communications View
  if (selectedVehicleId && selectedVehicle) {
    return (
      <div className="page">
        <div className="container">
          <div className="page-header">
            <button
              className="btn btn-ghost"
              onClick={() => setSelectedVehicleId(null)}
            >
              <ArrowLeft size={18} />
              Back to Vehicles
            </button>
          </div>

          <div className="vehicle-banner glass-card">
            <div className="vehicle-banner-info">
              <span className="vehicle-year">{selectedVehicle.year}</span>
              <h2>{selectedVehicle.model}</h2>
              <p>
                {commsData?.total || 0} manufacturer communications
              </p>
            </div>
            <div className="vehicle-banner-actions">
              <div className="input-group search-input">
                <Search size={18} className="search-icon" />
                <input
                  type="text"
                  className="input"
                  placeholder="Search communications..."
                />
              </div>
              <button
                className="btn btn-primary"
                onClick={() => fetch(selectedVehicleId, true)}
                disabled={isFetching}
              >
                Refresh Data
              </button>
            </div>
          </div>

          <FetchProgressBar progress={progress} onDismiss={reset} />

          <CommunicationList
            communications={commsData?.items || []}
            isLoading={commsLoading}
          />
        </div>

        <style>{`
          .page-header {
            margin-bottom: var(--space-lg);
          }

          .vehicle-banner {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: var(--space-xl);
            padding: var(--space-xl);
            gap: var(--space-lg);
          }

          .vehicle-banner-info .vehicle-year {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--color-primary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
          }

          .vehicle-banner-info h2 {
            font-size: 2rem;
            margin: var(--space-xs) 0;
            background: linear-gradient(135deg, var(--text-primary) 0%, var(--color-primary-light) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
          }

          .vehicle-banner-info p {
            margin: 0;
          }

          .vehicle-banner-actions {
            display: flex;
            gap: var(--space-md);
            align-items: center;
          }

          .search-input {
            position: relative;
          }

          .search-input .search-icon {
            position: absolute;
            left: var(--space-md);
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
          }

          .search-input .input {
            padding-left: calc(var(--space-md) + 24px);
            width: 280px;
          }

          @media (max-width: 768px) {
            .vehicle-banner {
              flex-direction: column;
              text-align: center;
            }

            .vehicle-banner-actions {
              flex-direction: column;
              width: 100%;
            }

            .search-input .input {
              width: 100%;
            }
          }
        `}</style>
      </div>
    );
  }

  // Vehicles Grid View
  return (
    <div className="page">
      <div className="container">
        <div className="hero-section">
          <div className="hero-content">
            <h1 className="hero-title">
              Track Vehicle
              <span className="gradient-text">Communications</span>
            </h1>
            <p className="hero-description">
              Monitor NHTSA manufacturer communications for software updates,
              safety recalls, and service bulletins for your vehicles.
            </p>
            <button
              className="btn btn-primary btn-lg"
              onClick={() => setShowAddModal(true)}
            >
              <Plus size={20} />
              Add Vehicle
            </button>
          </div>
        </div>

        <FetchProgressBar progress={progress} onDismiss={reset} />

        {vehiclesLoading ? (
          <div className="vehicles-grid">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton" style={{ height: 200 }} />
            ))}
          </div>
        ) : vehiclesData?.items.length === 0 ? (
          <div className="empty-state glass-card">
            <Car className="empty-state-icon" size={64} />
            <h3 className="empty-state-title">No vehicles added yet</h3>
            <p className="empty-state-description">
              Add a vehicle to start tracking manufacturer communications.
            </p>
            <button
              className="btn btn-primary"
              onClick={() => setShowAddModal(true)}
            >
              <Plus size={18} />
              Add Your First Vehicle
            </button>
          </div>
        ) : (
          <div className="vehicles-grid">
            {vehiclesData?.items.map((vehicle: Vehicle) => (
              <VehicleCard
                key={vehicle.vehicleId}
                vehicle={vehicle}
                onFetch={handleFetchVehicle}
                onDelete={handleDeleteVehicle}
                onSelect={setSelectedVehicleId}
                isFetching={isFetching}
              />
            ))}
          </div>
        )}

        <AddVehicleModal
          isOpen={showAddModal}
          onClose={() => setShowAddModal(false)}
          onSubmit={handleAddVehicle}
          isLoading={isCreating}
        />
      </div>

      <style>{`
        .hero-section {
          text-align: center;
          padding: var(--space-2xl) 0;
          margin-bottom: var(--space-xl);
        }

        .hero-title {
          font-size: 3rem;
          font-weight: 700;
          line-height: 1.1;
          margin-bottom: var(--space-md);
        }

        .gradient-text {
          display: block;
          background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-accent) 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }

        .hero-description {
          font-size: 1.125rem;
          color: var(--text-secondary);
          max-width: 600px;
          margin: 0 auto var(--space-xl);
        }

        .vehicles-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
          gap: var(--space-lg);
        }

        .empty-state.glass-card {
          padding: var(--space-2xl);
          text-align: center;
        }

        @media (max-width: 768px) {
          .hero-title {
            font-size: 2rem;
          }

          .vehicles-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </div>
  );
}

// App with Providers
function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Header />
      <Dashboard />
    </QueryClientProvider>
  );
}

export default App;
