/* Main Application Component */

import { useState, useMemo } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Plus, Car, ArrowLeft, Search, Filter, Calendar, TrendingUp, HelpCircle } from 'lucide-react';

import { Header } from './components/Header';
import { VehicleCard } from './features/vehicles/components/VehicleCard';
import { AddVehicleModal } from './features/vehicles/components/AddVehicleModal';
import { FilterInfoModal } from './components/FilterInfoModal';
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
  useVehicleStatsQuery,
} from './features/communications/hooks/useCommunications';

import type { Vehicle, CommType, CommunicationFilters } from './client';
import { COMM_TYPE_COLORS } from './client';

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
  const [showFilterInfo, setShowFilterInfo] = useState(false);
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedType, setSelectedType] = useState<CommType | ''>('');

  const { data: vehiclesData, isLoading: vehiclesLoading } = useVehiclesQuery();
  const { mutate: createVehicle, isPending: isCreating } = useCreateVehicle();
  const { mutate: deleteVehicle } = useDeleteVehicle();

  const { fetch, progress, isFetching, reset } = useFetchCommunications();

  // Stats for selected vehicle
  const { data: statsData } = useVehicleStatsQuery(selectedVehicleId || 0);

  // Build filters for communications query
  const filters: CommunicationFilters = useMemo(() => {
    if (!selectedVehicleId) return {};
    const f: CommunicationFilters = { vehicleId: selectedVehicleId, perPage: 100 };
    if (searchTerm.trim()) f.search = searchTerm.trim();
    if (selectedType) f.commType = selectedType;
    return f;
  }, [selectedVehicleId, searchTerm, selectedType]);

  const { data: commsData, isLoading: commsLoading } = useCommunicationsQuery(
    selectedVehicleId ? filters : {}
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

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    // Search is already reactive via state
  };

  // Communications View
  if (selectedVehicleId && selectedVehicle) {
    return (
      <div className="page">
        <div className="container">
          <div className="page-header">
            <button
              className="btn btn-ghost"
              onClick={() => {
                setSelectedVehicleId(null);
                setSearchTerm('');
                setSelectedType('');
              }}
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
                {commsData?.total || 0} communications
                {selectedType && ` (filtered by ${selectedType})`}
              </p>
            </div>
            <div className="vehicle-banner-actions">
              <button
                className="btn btn-primary"
                onClick={() => fetch(selectedVehicleId, true)}
                disabled={isFetching}
              >
                Refresh Data
              </button>
            </div>
          </div>

          {/* Stats Summary */}
          {statsData && (
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-icon">
                  <TrendingUp size={20} />
                </div>
                <div className="stat-content">
                  <span className="stat-value">{statsData.totalCount}</span>
                  <span className="stat-label">Total Communications</span>
                </div>
              </div>
              <div className="stat-card">
                <div className="stat-icon">
                  <Calendar size={20} />
                </div>
                <div className="stat-content">
                  <span className="stat-value">{statsData.last30DaysCount}</span>
                  <span className="stat-label">Last 30 Days</span>
                </div>
              </div>
              {statsData.categories.map((cat) => (
                <div
                  key={cat.type}
                  className="stat-card category-stat"
                  style={{ borderColor: COMM_TYPE_COLORS[cat.type as CommType] }}
                  onClick={() => setSelectedType(selectedType === cat.type ? '' : cat.type as CommType)}
                >
                  <div
                    className="stat-dot"
                    style={{ backgroundColor: COMM_TYPE_COLORS[cat.type as CommType] }}
                  />
                  <div className="stat-content">
                    <span className="stat-value">{cat.count}</span>
                    <span className="stat-label">{cat.type}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="filters-bar">
            <form onSubmit={handleSearch} className="search-form">
              <div className="input-group search-input">
                <Search size={18} className="search-icon" />
                <input
                  type="text"
                  className="input"
                  placeholder="Search summary or comm number..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>
            </form>

            <div className="type-filters">
              <Filter size={16} />
              <button
                className={`type-filter-btn ${selectedType === '' ? 'active' : ''}`}
                onClick={() => setSelectedType('')}
              >
                All
              </button>
              {statsData?.categories.map((cat) => (
                <button
                  key={cat.type}
                  className={`type-filter-btn ${selectedType === cat.type ? 'active' : ''}`}
                  style={{
                    '--type-color': COMM_TYPE_COLORS[cat.type as CommType] || COMM_TYPE_COLORS.OTHER,
                  } as React.CSSProperties}
                  onClick={() => setSelectedType(selectedType === cat.type ? '' : cat.type as CommType)}
                  title={cat.label}
                >
                  {cat.type} ({cat.count})
                </button>
              ))}
            </div>

            <button
              className="btn btn-ghost btn-icon filter-help-btn"
              onClick={() => setShowFilterInfo(true)}
              title="Learn about filters"
            >
              <HelpCircle size={18} />
            </button>
          </div>

          <FilterInfoModal
            isOpen={showFilterInfo}
            onClose={() => setShowFilterInfo(false)}
          />

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
            margin-bottom: var(--space-lg);
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

          /* Stats Grid */
          .stats-grid {
            display: flex;
            gap: var(--space-md);
            margin-bottom: var(--space-lg);
            flex-wrap: wrap;
          }

          .stat-card {
            display: flex;
            align-items: center;
            gap: var(--space-md);
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            padding: var(--space-md) var(--space-lg);
            min-width: 140px;
          }

          .stat-card.category-stat {
            border-left: 3px solid;
            cursor: pointer;
            transition: all var(--transition-fast);
          }

          .stat-card.category-stat:hover {
            background: var(--bg-hover);
          }

          .stat-icon {
            width: 40px;
            height: 40px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--bg-elevated);
            border-radius: var(--radius-md);
            color: var(--color-primary);
          }

          .stat-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
          }

          .stat-content {
            display: flex;
            flex-direction: column;
          }

          .stat-value {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1;
          }

          .stat-label {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 2px;
          }

          /* Filters Bar */
          .filters-bar {
            display: flex;
            gap: var(--space-lg);
            align-items: center;
            margin-bottom: var(--space-lg);
            flex-wrap: wrap;
          }

          .search-form {
            flex: 1;
            min-width: 250px;
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
            pointer-events: none;
          }

          .search-input .input {
            padding-left: calc(var(--space-md) + 28px);
            width: 100%;
          }

          .type-filters {
            display: flex;
            align-items: center;
            gap: var(--space-sm);
            color: var(--text-muted);
          }

          .type-filter-btn {
            padding: var(--space-xs) var(--space-md);
            font-size: 0.75rem;
            font-weight: 600;
            border: 1px solid var(--border-default);
            border-radius: var(--radius-full);
            background: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all var(--transition-fast);
          }

          .type-filter-btn:hover {
            background: var(--bg-hover);
            color: var(--text-primary);
          }

          .type-filter-btn.active {
            background: var(--type-color, var(--color-primary));
            border-color: var(--type-color, var(--color-primary));
            color: white;
          }

          .filter-help-btn {
            margin-left: var(--space-sm);
            color: var(--text-muted);
            transition: all var(--transition-fast);
          }

          .filter-help-btn:hover {
            color: var(--color-primary);
            background: var(--bg-elevated);
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

            .stats-grid {
              justify-content: center;
            }

            .filters-bar {
              flex-direction: column;
              align-items: stretch;
            }

            .type-filters {
              flex-wrap: wrap;
              justify-content: center;
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
