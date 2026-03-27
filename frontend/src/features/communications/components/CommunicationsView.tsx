/* Communications View - Extracted from App.tsx Dashboard */

import { useState, useMemo } from 'react';
import { ArrowLeft, Search, Filter, Calendar, TrendingUp, HelpCircle } from 'lucide-react';

import { CommunicationList } from './CommunicationList';
import { FetchProgressBar } from './FetchProgress';
import { FilterInfoModal } from '../../../components/FilterInfoModal';

import {
  useCommunicationsQuery,
  useFetchCommunications,
  useVehicleStatsQuery,
} from '../hooks/useCommunications';

import type { Vehicle, CommType, CommPriority, CommunicationFilters } from '@/client';
import { COMM_TYPE_COLORS, COMM_PRIORITY_TYPES, PRIORITY_COLORS } from '@/client';

import styles from './CommunicationsView.module.css';

interface CommunicationsViewProps {
  vehicleId: number;
  vehicle: Vehicle;
  onBack: () => void;
}

export function CommunicationsView({ vehicleId, vehicle, onBack }: CommunicationsViewProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedTypes, setSelectedTypes] = useState<CommType[]>([]);
  const [showFilterInfo, setShowFilterInfo] = useState(false);

  const { data: statsData } = useVehicleStatsQuery(vehicleId);
  const { fetch, progress, isFetching, reset } = useFetchCommunications();

  const filters: CommunicationFilters = useMemo(() => {
    const f: CommunicationFilters = { vehicleId, perPage: 100 };
    if (searchTerm.trim()) f.search = searchTerm.trim();
    if (selectedTypes.length > 0) f.commTypes = selectedTypes;
    return f;
  }, [vehicleId, searchTerm, selectedTypes]);

  const { data: commsData, isLoading: commsLoading } = useCommunicationsQuery(filters);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
  };

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.pageHeader}>
          <button
            className="btn btn-ghost"
            onClick={() => {
              onBack();
            }}
          >
            <ArrowLeft size={18} />
            Back to Vehicles
          </button>
        </div>

        <div className={`${styles.vehicleBanner} glass-card`}>
          <div className={styles.vehicleBannerInfo}>
            <span className={styles.vehicleYear}>{vehicle.year}</span>
            <h2 className={styles.vehicleModel}>{vehicle.model}</h2>
            <p className={styles.vehicleBannerText}>
              {commsData?.total || 0} communications
              {selectedTypes.length > 0 && ` (filtered by ${selectedTypes.length} type${selectedTypes.length > 1 ? 's' : ''})`}
            </p>
          </div>
          <div className={styles.vehicleBannerActions}>
            <button
              className="btn btn-primary"
              onClick={() => fetch(vehicleId, true)}
              disabled={isFetching}
            >
              Refresh Data
            </button>
          </div>
        </div>

        {/* Stats Summary */}
        {statsData && (
          <div className={styles.statsGrid}>
            <div className={styles.statCard}>
              <div className={styles.statIcon}>
                <TrendingUp size={20} />
              </div>
              <div className={styles.statContent}>
                <span className={styles.statValue}>{statsData.totalCount}</span>
                <span className={styles.statLabel}>Total Communications</span>
              </div>
            </div>
            <div className={styles.statCard}>
              <div className={styles.statIcon}>
                <Calendar size={20} />
              </div>
              <div className={styles.statContent}>
                <span className={styles.statValue}>{statsData.last30DaysCount}</span>
                <span className={styles.statLabel}>Last 30 Days</span>
              </div>
            </div>
            {statsData.categories.map((cat) => (
              <div
                key={cat.type}
                className={`${styles.statCard} ${styles.categoryStat} ${selectedTypes.includes(cat.type as CommType) ? styles.selected : ''}`}
                style={{ borderColor: COMM_TYPE_COLORS[cat.type as CommType] }}
                onClick={() => {
                  const type = cat.type as CommType;
                  setSelectedTypes(prev =>
                    prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
                  );
                }}
              >
                <div
                  className={styles.statDot}
                  style={{ backgroundColor: COMM_TYPE_COLORS[cat.type as CommType] }}
                />
                <div className={styles.statContent}>
                  <span className={styles.statValue}>{cat.count}</span>
                  <span className={styles.statLabel}>{cat.type}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Filters */}
        <div className={styles.filtersBar}>
          <form onSubmit={handleSearch} className={styles.searchForm}>
            <div className={`input-group ${styles.searchInput}`}>
              <Search size={18} className={styles.searchIcon} />
              <input
                type="text"
                className="input"
                placeholder="Search summary or comm number..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </form>

          <div className={styles.typeFilters}>
            <Filter size={16} />
            <button
              className={`${styles.typeFilterBtn} ${selectedTypes.length === 0 ? styles.active : ''}`}
              onClick={() => setSelectedTypes([])}
            >
              All
            </button>

            {/* Priority filter buttons */}
            <div className={styles.priorityDivider} />
            {(['high', 'medium', 'low'] as CommPriority[]).map((priority) => {
              const priorityTypes = COMM_PRIORITY_TYPES[priority];
              const availableTypes = statsData?.categories
                .filter(cat => priorityTypes.includes(cat.type as CommType))
                .map(cat => cat.type as CommType) || [];
              const allSelected = availableTypes.length > 0 &&
                availableTypes.every(t => selectedTypes.includes(t));
              const someSelected = availableTypes.some(t => selectedTypes.includes(t));
              const totalCount = statsData?.categories
                .filter(cat => priorityTypes.includes(cat.type as CommType))
                .reduce((sum, cat) => sum + cat.count, 0) || 0;

              if (totalCount === 0) return null;

              return (
                <button
                  key={priority}
                  className={`${styles.typeFilterBtn} ${styles.priorityBtn} ${allSelected ? styles.active : ''} ${someSelected && !allSelected ? styles.partial : ''}`}
                  style={{
                    '--type-color': PRIORITY_COLORS[priority],
                  } as React.CSSProperties}
                  onClick={() => {
                    if (allSelected) {
                      setSelectedTypes(prev => prev.filter(t => !priorityTypes.includes(t)));
                    } else {
                      setSelectedTypes(prev => {
                        const newTypes = availableTypes.filter(t => !prev.includes(t));
                        return [...prev, ...newTypes];
                      });
                    }
                  }}
                  title={`${priority.charAt(0).toUpperCase() + priority.slice(1)} priority: ${priorityTypes.join(', ')}`}
                >
                  {priority === 'high' ? '🔴' : priority === 'medium' ? '🟡' : '🟢'} {priority.charAt(0).toUpperCase() + priority.slice(1)} ({totalCount})
                </button>
              );
            })}
            <div className={styles.priorityDivider} />

            {/* Individual type buttons */}
            {statsData?.categories.map((cat) => (
              <button
                key={cat.type}
                className={`${styles.typeFilterBtn} ${selectedTypes.includes(cat.type as CommType) ? styles.active : ''}`}
                style={{
                  '--type-color': COMM_TYPE_COLORS[cat.type as CommType] || COMM_TYPE_COLORS.OTHER,
                } as React.CSSProperties}
                onClick={() => {
                  const type = cat.type as CommType;
                  setSelectedTypes(prev =>
                    prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
                  );
                }}
                title={cat.label}
              >
                {cat.type} ({cat.count})
              </button>
            ))}
          </div>

          <button
            className={`btn btn-ghost btn-icon ${styles.filterHelpBtn}`}
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
    </div>
  );
}
