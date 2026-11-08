/* /app/vehicles/:id: one car's bulletins, the free sample, locked rows and the plan buttons. */

import { useRef, useState } from 'react';
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Search } from 'lucide-react';

import { ApiError } from '@/client';
import type { Severity, Vehicle } from '@/client';
import { SymptomSearchView } from '../features/symptom-search/components/SymptomSearchView';
import { PlanBar } from '../features/my-vehicles/components/PlanBar';
import {
  BulletinListSkeleton,
  ProductBulletinList,
} from '../features/my-vehicles/components/ProductBulletinList';
import { accessLabel, availabilityLabel, vehicleName } from '../features/my-vehicles/access';
import { useMeQuery, useMyBulletinsQuery } from '../features/my-vehicles/hooks/useMyVehicles';
import { PastDueBanner } from '../features/billing/components/PastDueBanner';
import { useCheckout } from '../features/billing/hooks/useBilling';
import { isPastDue } from '../features/billing/plan';
import styles from './VehiclePage.module.css';

const PER_PAGE = 50;
const SEARCH_DEBOUNCE_MS = 300;
const NOT_YOURS = "That car isn't on your account.";

type SeverityTab = 'all' | Severity;
const SEVERITY_TABS: { key: SeverityTab; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'serious', label: 'Serious' },
  { key: 'medium', label: 'Medium' },
  { key: 'small', label: 'Small' },
];

function parseSeverity(v: string | null): Severity | undefined {
  return v === 'serious' || v === 'medium' || v === 'small' ? v : undefined;
}

export function VehiclePage() {
  const { id } = useParams();
  const vehicleId = Number(id);
  const [params, setParams] = useSearchParams();
  const checkout = useCheckout();
  const meQuery = useMeQuery();
  const vehicle = meQuery.data?.vehicles.find((v) => v.vehicleId === vehicleId);
  const ready = vehicle?.availability === 'ready';

  const severity = parseSeverity(params.get('severity'));
  const search = params.get('search') ?? '';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const view = params.get('view') === 'symptoms' ? 'symptoms' : 'bulletins';

  const [searchText, setSearchText] = useState(search);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const bulletins = useMyBulletinsQuery(
    vehicleId,
    { severity, search: search || undefined, page, perPage: PER_PAGE },
    ready
  );

  // A car that is not on this account (or does not exist) goes back to the list.
  const notFound =
    (meQuery.isSuccess && !vehicle) ||
    (bulletins.error instanceof ApiError && bulletins.error.status === 404);
  if (!Number.isInteger(vehicleId) || notFound) {
    return <Navigate to="/app" replace state={{ toast: NOT_YOURS }} />;
  }

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };

  const onSearchChange = (value: string) => {
    setSearchText(value);
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(
      () => update({ search: value.trim() || null, page: null }),
      SEARCH_DEBOUNCE_MS
    );
  };

  const setSeverity = (tab: SeverityTab) =>
    update({ severity: tab === 'all' ? null : tab, page: null });

  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = SEVERITY_TABS[(index + step + SEVERITY_TABS.length) % SEVERITY_TABS.length];
    setSeverity(next.key);
    document.getElementById(`sev-tab-${next.key}`)?.focus();
  };

  if (meQuery.isError) {
    return (
      <div className={styles.page}>
        <div className={styles.error} role="alert">
          <span>We couldn't load this car.</span>
          <button type="button" className={`btn btn-secondary ${styles.retry}`} onClick={() => void meQuery.refetch()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (!vehicle) {
    return (
      <div className={styles.page} aria-busy="true">
        <div className="skeleton" style={{ height: 40, width: '50%' }} />
        <BulletinListSkeleton />
      </div>
    );
  }

  const full = vehicle.access === 'full';
  const counts = vehicle.counts;
  const tabCount: Record<SeverityTab, number> = {
    all: counts.serious + counts.medium + counts.small,
    serious: counts.serious,
    medium: counts.medium,
    small: counts.small,
  };
  const data = bulletins.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.perPage)) : 1;
  const access = accessLabel(vehicle, meQuery.data);

  // SymptomSearchView reads vehicleId, year and model; the rest of Vehicle is tracker bookkeeping.
  const symptomVehicle: Vehicle = {
    id: String(vehicle.vehicleId),
    vehicleId: vehicle.vehicleId,
    year: String(vehicle.year),
    make: vehicle.make,
    model: vehicle.model,
    keywords: [],
    lastFetched: null,
    commCount: tabCount.all,
    createdAt: vehicle.addedAt,
    updatedAt: vehicle.addedAt,
  };

  const list = (
    <>
      {!ready && <BulletinListSkeleton />}
      {ready && bulletins.isPending && <BulletinListSkeleton />}
      {ready && bulletins.isError && (
        <div className={styles.error} role="alert">
          <span>We couldn't load the bulletins.</span>
          <button type="button" className={`btn btn-secondary ${styles.retry}`} onClick={() => void bulletins.refetch()}>
            Try again
          </button>
        </div>
      )}
      {data && data.items.length === 0 && (
        <div className="empty-state">
          <h3 className="empty-state-title">No bulletins match</h3>
          <p className="empty-state-description">Try another search or severity.</p>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              setSearchText('');
              update({ search: null, severity: null, page: null });
            }}
          >
            Clear filters
          </button>
        </div>
      )}
      {data && data.items.length > 0 && <ProductBulletinList items={data.items} />}
      {data && totalPages > 1 && (
        <nav className={styles.pager} aria-label="Pages">
          <button
            type="button"
            className={`btn btn-secondary ${styles.pagerBtn}`}
            disabled={page <= 1}
            onClick={() => update({ page: page - 1 > 1 ? String(page - 1) : null })}
          >
            Previous
          </button>
          <span>
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className={`btn btn-secondary ${styles.pagerBtn}`}
            disabled={page >= totalPages}
            onClick={() => update({ page: String(page + 1) })}
          >
            Next
          </button>
        </nav>
      )}
    </>
  );

  return (
    <div className={styles.page}>
      <Link to="/app" className={styles.back}>
        <ArrowLeft size={16} /> My vehicles
      </Link>

      {isPastDue(meQuery.data) && <PastDueBanner />}

      <header className={styles.header}>
        <h1 className={styles.title}>{vehicleName(vehicle)}</h1>
        <div className={styles.badges}>
          <span className={styles.badge}>{availabilityLabel(vehicle)}</span>
          <span className={`${styles.badge} ${full ? styles.badgeAccess : ''}`}>{access}</span>
        </div>
        {!ready && (
          <p className={styles.processing} role="status">
            We're reading this car's bulletins. You'll get an email when it's ready.
          </p>
        )}
        {full && ready && <p className={styles.unlockedLine}>{access}</p>}
      </header>

      {full && (
        <div className={styles.tabs} role="tablist" aria-label="View">
          {(['bulletins', 'symptoms'] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              id={`view-tab-${v}`}
              aria-selected={view === v}
              className={`${styles.tab} ${view === v ? styles.tabActive : ''}`}
              onClick={() => update({ view: v === 'symptoms' ? 'symptoms' : null })}
            >
              {v === 'bulletins' ? 'Bulletins' : 'Symptom search'}
            </button>
          ))}
        </div>
      )}

      {full && view === 'symptoms' ? (
        <SymptomSearchView
          vehicles={[symptomVehicle]}
          initialVehicleId={vehicle.vehicleId}
          productPage
        />
      ) : (
        <div className={styles.layout}>
          <div className={styles.main}>
            <div className={styles.searchWrap}>
              <Search size={16} className={styles.searchIcon} aria-hidden="true" />
              <input
                type="search"
                className={`input ${styles.search}`}
                placeholder="Search these bulletins"
                aria-label="Search bulletins"
                value={searchText}
                onChange={(e) => onSearchChange(e.target.value)}
              />
            </div>

            <div className={styles.tabs} role="tablist" aria-label="Severity">
              {SEVERITY_TABS.map((t, i) => {
                const active = (severity ?? 'all') === t.key;
                return (
                  <button
                    key={t.key}
                    type="button"
                    role="tab"
                    id={`sev-tab-${t.key}`}
                    aria-selected={active}
                    tabIndex={active ? 0 : -1}
                    className={`${styles.tab} ${active ? styles.tabActive : ''}`}
                    onClick={() => setSeverity(t.key)}
                    onKeyDown={(e) => onTabKeyDown(e, i)}
                  >
                    {t.label} <span className={styles.tabCount}>{tabCount[t.key]}</span>
                  </button>
                );
              })}
            </div>

            {list}
          </div>

          {!full && ready && data && (
            <div className={styles.side}>
              <PlanBar
                counts={counts}
                sampleCounts={data.sampleCounts}
                onUnlock={() => checkout.mutate({ kind: 'unlock', vehicleId })}
                onSubscribe={() => checkout.mutate({ kind: 'subscription' })}
                pending={checkout.isPending || checkout.isSuccess ? checkout.variables.kind : null}
                error={checkout.isError ? checkout.error.message : null}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
