/* Corpus View - processed pipeline output: LLM summaries, tags, FTS search */

import { useMemo, useState } from 'react';
import { ArrowLeft, Search, Filter, Sparkles, X } from 'lucide-react';

import { CorpusCommunicationList } from './CorpusCommunicationList';
import {
  useCorpusCommunicationsQuery,
  useCorpusTagsQuery,
} from '../hooks/useCorpus';

import type { Vehicle, CommType, CorpusFilters, ProcessingStatus } from '@/client';
import { COMM_TYPE_COLORS } from '@/client';

import styles from './CorpusView.module.css';

interface CorpusViewProps {
  vehicleId: number;
  vehicle: Vehicle;
  onBack: () => void;
}

const STATUS_TABS: { key: ProcessingStatus | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'processed', label: 'Processed' },
  { key: 'pending', label: 'Pending' },
  { key: 'failed', label: 'Unavailable' },
];

export function CorpusView({ vehicleId, vehicle, onBack }: CorpusViewProps) {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<ProcessingStatus | 'all'>('all');
  const [selectedSystems, setSelectedSystems] = useState<string[]>([]);
  const [selectedType, setSelectedType] = useState<CommType | null>(null);

  const { data: tagsData } = useCorpusTagsQuery();

  const filters: CorpusFilters = useMemo(() => {
    const f: CorpusFilters = { vehicleId, perPage: 200 };
    if (search.trim()) f.search = search.trim();
    if (status !== 'all') f.status = status;
    if (selectedSystems.length > 0) f.systems = selectedSystems;
    if (selectedType) f.commType = selectedType;
    return f;
  }, [vehicleId, search, status, selectedSystems, selectedType]);

  const { data, isLoading } = useCorpusCommunicationsQuery(filters);

  const items = data?.items ?? [];
  const processedCount = items.filter((c) => c.status === 'processed').length;

  const topSystems = (tagsData?.systems ?? []).slice(0, 12);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
  };

  const toggleSystem = (tag: string) => {
    setSelectedSystems((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const hasActiveFilters =
    search.trim() || status !== 'all' || selectedSystems.length > 0 || selectedType;

  const clearAll = () => {
    setSearchInput('');
    setSearch('');
    setStatus('all');
    setSelectedSystems([]);
    setSelectedType(null);
  };

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.pageHeader}>
          <button className="btn btn-ghost" onClick={onBack}>
            <ArrowLeft size={18} />
            Back to Vehicles
          </button>
        </div>

        <div className={`${styles.banner} glass-card`}>
          <div className={styles.bannerInfo}>
            <span className={styles.vehicleYear}>{vehicle.year}</span>
            <h2 className={styles.vehicleModel}>{vehicle.model}</h2>
            <p className={styles.bannerMeta}>
              <Sparkles size={14} className={styles.sparkle} />
              {data?.total ?? 0} communications
              {processedCount > 0 && ` · ${processedCount} analyzed on this page`}
            </p>
          </div>
        </div>

        {/* Search */}
        <form onSubmit={handleSubmit} className={styles.searchForm}>
          <div className={`input-group ${styles.searchInput}`}>
            <Search size={18} className={styles.searchIcon} />
            <input
              type="text"
              className="input"
              placeholder="Search summaries, symptoms, remedies, and full document text..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
            {searchInput && (
              <button
                type="button"
                className={styles.clearSearch}
                onClick={() => {
                  setSearchInput('');
                  setSearch('');
                }}
                aria-label="Clear search"
              >
                <X size={16} />
              </button>
            )}
          </div>
          <button type="submit" className="btn btn-primary">
            Search
          </button>
        </form>

        {/* Status tabs */}
        <div className={styles.statusTabs}>
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.key}
              className={`${styles.statusTab} ${status === tab.key ? styles.statusTabActive : ''}`}
              onClick={() => setStatus(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* System tag chips */}
        {topSystems.length > 0 && (
          <div className={styles.tagRow}>
            <span className={styles.tagRowLabel}>
              <Filter size={14} /> Systems
            </span>
            {topSystems.map((t) => (
              <button
                key={t.tag}
                className={`${styles.tagChip} ${selectedSystems.includes(t.tag) ? styles.tagChipActive : ''}`}
                onClick={() => toggleSystem(t.tag)}
                title={`${t.count} document${t.count === 1 ? '' : 's'}`}
              >
                {t.tag}
                <span className={styles.tagCount}>{t.count}</span>
              </button>
            ))}
          </div>
        )}

        {hasActiveFilters && (
          <div className={styles.activeFilters}>
            <button className={styles.clearAll} onClick={clearAll}>
              <X size={14} /> Clear filters
            </button>
            {selectedType && (
              <span
                className={styles.activeChip}
                style={{ '--chip-color': COMM_TYPE_COLORS[selectedType] } as React.CSSProperties}
              >
                {selectedType}
                <X size={12} onClick={() => setSelectedType(null)} />
              </span>
            )}
          </div>
        )}

        <CorpusCommunicationList
          communications={items}
          isLoading={isLoading}
          onSelectType={(t) => setSelectedType((prev) => (prev === t ? null : t))}
          onSelectSystem={toggleSystem}
          activeSystems={selectedSystems}
        />
      </div>
    </div>
  );
}
