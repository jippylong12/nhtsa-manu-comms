/* /app/vehicles/:id: one car's bulletins, the free sample, locked rows and the plan buttons. */

import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, FileText } from 'lucide-react';

import { ApiError } from '@/client';
import type { Facets, MyBulletinsParams, Severity, Vehicle } from '@/client';
import { SymptomSearchView } from '../features/symptom-search/components/SymptomSearchView';
import { PlanBar } from '../features/my-vehicles/components/PlanBar';
import { BulletinListSkeleton } from '../features/my-vehicles/components/ProductBulletinList';
import { BulletinRow } from '../features/bulletins/components/BulletinRow';
import { BulletinSheet } from '../features/bulletins/components/BulletinSheet';
import { useInboxKeyboard } from '../features/bulletins/hooks/useInboxKeyboard';
import { useMediaQuery } from '../hooks/useMediaQuery';
import {
  BulletinPanel,
  FiltersPopover,
  SearchBox,
  SeverityTabs,
} from '../features/bulletins';
import type { SeverityTab } from '../features/bulletins';
import { accessLabel, availabilityLabel, vehicleName } from '../features/my-vehicles/access';
import {
  useMarkAllRead,
  useMeQuery,
  useMyBulletinsQuery,
} from '../features/my-vehicles/hooks/useMyVehicles';
import { useMarkRead } from '../features/my-vehicles/hooks/useMarkRead';
import { useToast } from '../features/my-vehicles/hooks/useToast';
import { PastDueBanner } from '../features/billing/components/PastDueBanner';
import { useCheckout } from '../features/billing/hooks/useBilling';
import { isPastDue } from '../features/billing/plan';
import styles from './VehiclePage.module.css';

const PER_PAGE = 50;
const NOT_YOURS = "That car isn't on your account.";
const NOT_ON_CAR = "That bulletin isn't on this car.";
// Below this the panel is a full-screen sheet. Same number as the panel's own CSS breakpoint.
const COMPACT_QUERY = '(max-width: 899.98px)';

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
  const unreadOnly = params.get('unread') === '1';
  const commType = params.get('type') || undefined;
  const systems = params.get('systems')?.split(',').filter(Boolean);
  const dateFrom = params.get('from') || undefined;
  const dateTo = params.get('to') || undefined;
  const selectedId = params.get('b') || undefined;
  const page = Math.max(1, Number(params.get('page')) || 1);
  const view = params.get('view') === 'symptoms' ? 'symptoms' : 'bulletins';
  const searchRef = useRef<HTMLInputElement>(null);
  const markAllRead = useMarkAllRead(vehicleId);
  const navigate = useNavigate();
  const compact = useMediaQuery(COMPACT_QUERY);
  const { toast, showToast } = useToast();
  const markRead = useMarkRead(vehicleId, { onError: () => showToast("Couldn't mark as read") });
  const listRef = useRef<HTMLUListElement>(null);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const markedRef = useRef(new Set<string>());
  const pushedRef = useRef(false);
  const prevSelectedRef = useRef(selectedId);
  // Roving highlight: the id of the row the keyboard is on; `kbMode` shows it as selected.
  const [highlightId, setHighlightId] = useState<string | undefined>();
  const [kbMode, setKbMode] = useState(false);

  const listParams: MyBulletinsParams = {
    severity,
    search: search || undefined,
    unreadOnly: unreadOnly || undefined,
    commType,
    systems: systems?.length ? systems : undefined,
    dateFrom,
    dateTo,
    page,
    perPage: PER_PAGE,
  };
  const bulletins = useMyBulletinsQuery(vehicleId, listParams, ready);
  // A shared ?b= link can point at a bulletin outside the current page of the list.
  const inList = Boolean(selectedId && bulletins.data?.items.some((b) => b.nhtsaId === selectedId));
  const selectedFetch = useMyBulletinsQuery(
    vehicleId,
    { nhtsaId: selectedId, perPage: 1 },
    ready && Boolean(selectedId) && bulletins.isSuccess && !inList
  );

  const selected =
    bulletins.data?.items.find((b) => b.nhtsaId === selectedId) ??
    selectedFetch.data?.items.find((b) => b.nhtsaId === selectedId);

  // A ?b= id that is neither on this page nor on the car: drop it and say so.
  const unknownId = Boolean(
    selectedId &&
      ready &&
      bulletins.isSuccess &&
      !bulletins.isPlaceholderData &&
      !inList &&
      (selectedFetch.isError ||
        (selectedFetch.isSuccess &&
          !selectedFetch.isPlaceholderData &&
          !selectedFetch.data.items.some((b) => b.nhtsaId === selectedId)))
  );
  useEffect(() => {
    if (!unknownId) return;
    const next = new URLSearchParams(params);
    next.delete('b');
    setParams(next, { replace: true });
    showToast(NOT_ON_CAR);
  }, [unknownId, params, setParams, showToast]);

  // Opening an unread, unlocked bulletin reads it: once per bulletin, never for a locked one.
  const markReadMutate = markRead.mutate;
  useEffect(() => {
    if (!selected || selected.locked || selected.read === true) return;
    if (markedRef.current.has(selected.nhtsaId)) return;
    markedRef.current.add(selected.nhtsaId);
    markReadMutate({ nhtsaId: selected.nhtsaId, severity: selected.severity });
  }, [selected, markReadMutate]);

  // When the panel closes, focus goes back to the row that opened it.
  useEffect(() => {
    const prev = prevSelectedRef.current;
    prevSelectedRef.current = selectedId;
    if (prev && !selectedId) {
      pushedRef.current = false;
      rowRefs.current.get(prev)?.focus({ preventScroll: true });
    }
  }, [selectedId]);

  const items = bulletins.data?.items ?? [];
  const activeId =
    (selectedId && items.some((b) => b.nhtsaId === selectedId) ? selectedId : undefined) ??
    (highlightId && items.some((b) => b.nhtsaId === highlightId) ? highlightId : undefined) ??
    items[0]?.nhtsaId;
  const activeIndex = Math.max(0, items.findIndex((b) => b.nhtsaId === activeId));

  // Click and Enter push a history entry; arrowing with the panel open replaces it.
  const openRow = (nhtsaId: string, push: boolean) => {
    setHighlightId(nhtsaId);
    if (push && !selectedId) pushedRef.current = true;
    update({ b: nhtsaId }, push && !selectedId);
  };
  // Back out of the panel: undo our own push when there is one, so Back never reopens it.
  const closePanel = () => {
    if (pushedRef.current) navigate(-1);
    else update({ b: null });
  };

  useInboxKeyboard({
    enabled: ready && view === 'bulletins',
    count: items.length,
    highlighted: activeIndex,
    onHighlight: (i) => {
      const id = items[i].nhtsaId;
      setHighlightId(id);
      setKbMode(true);
      if (selectedId) update({ b: id });
      const el = rowRefs.current.get(id);
      el?.focus({ preventScroll: true });
      el?.scrollIntoView({ block: 'nearest' });
    },
    onOpen: (i) => openRow(items[i].nhtsaId, true),
    panelOpen: Boolean(selectedId),
    modal: compact,
    onClosePanel: closePanel,
    searchRef,
    listRef,
    onClearSearch: () => {
      if (search) update({ search: null, page: null });
    },
  });

  // A car that is not on this account (or does not exist) goes back to the list.
  const notFound =
    (meQuery.isSuccess && !vehicle) ||
    (bulletins.error instanceof ApiError && bulletins.error.status === 404);
  if (!Number.isInteger(vehicleId) || notFound) {
    return <Navigate to="/app" replace state={{ toast: NOT_YOURS }} />;
  }

  const update = (changes: Record<string, string | null>, push = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: !push });
  };

  const setSeverity = (tab: SeverityTab) =>
    update({ severity: tab === 'all' ? null : tab, page: null });

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
  const tabAll = counts.serious + counts.medium + counts.small;
  const facets: Facets = bulletins.data?.facets ?? {
    bySeverity: counts,
    unreadBySeverity: vehicle.unread,
  };
  const unreadTotal = vehicle.unread.serious + vehicle.unread.medium + vehicle.unread.small;
  const data = bulletins.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.perPage)) : 1;
  const access = accessLabel(vehicle, meQuery.data);
  const clearSearch = () => update({ search: null, page: null });
  const filterValues = { type: commType, systems: systems ?? [], from: dateFrom, to: dateTo };
  const filtersActive = Boolean(commType || systems?.length || dateFrom || dateTo);
  const clearFilters = () => update({ type: null, systems: null, from: null, to: null, page: null });

  // SymptomSearchView reads vehicleId, year and model; the rest of Vehicle is tracker bookkeeping.
  const symptomVehicle: Vehicle = {
    id: String(vehicle.vehicleId),
    vehicleId: vehicle.vehicleId,
    year: String(vehicle.year),
    make: vehicle.make,
    model: vehicle.model,
    keywords: [],
    lastFetched: null,
    commCount: tabAll,
    createdAt: vehicle.addedAt,
    updatedAt: vehicle.addedAt,
  };

  // Unread only with matches in this tab that are all read: caught up, not "no match".
  const tabKey = severity ?? 'all';
  const sumC = (c: { serious: number; medium: number; small: number }) =>
    c.serious + c.medium + c.small;
  const tabMatches = tabKey === 'all' ? sumC(facets.bySeverity) : facets.bySeverity[tabKey];
  const tabUnread = tabKey === 'all' ? sumC(facets.unreadBySeverity) : facets.unreadBySeverity[tabKey];
  const caughtUp = unreadOnly && tabMatches > 0 && tabUnread === 0;

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
          {caughtUp ? (
            <h3 className="empty-state-title">You're all caught up</h3>
          ) : search ? (
            <>
              <h3 className="empty-state-title">No bulletins match your search</h3>
              <button type="button" className="btn btn-secondary" onClick={clearSearch}>
                Clear search
              </button>
            </>
          ) : unreadOnly ? (
            <h3 className="empty-state-title">You're all caught up</h3>
          ) : (
            <h3 className="empty-state-title">No bulletins match</h3>
          )}
        </div>
      )}
      {data && data.items.length > 0 && (
        <ul ref={listRef} className={styles.list} aria-label="Bulletins">
          {data.items.map((b) => {
            const highlighted = kbMode && b.nhtsaId === activeId;
            return (
              <li key={b.nhtsaId} data-highlighted={highlighted}>
                <BulletinRow
                  ref={(el) => {
                    if (el) rowRefs.current.set(b.nhtsaId, el);
                    else rowRefs.current.delete(b.nhtsaId);
                  }}
                  variant="product"
                  bulletin={b}
                  selected={b.nhtsaId === selectedId || highlighted}
                  tabIndex={b.nhtsaId === activeId ? 0 : -1}
                  onOpen={(nhtsaId) => {
                    setKbMode(false);
                    openRow(nhtsaId, true);
                  }}
                />
              </li>
            );
          })}
        </ul>
      )}
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

  const panel = selected && (
    <BulletinPanel
      bulletin={selected}
      variant="product"
      vehicleId={vehicleId}
      onClose={closePanel}
      onUnlock={() => checkout.mutate({ kind: 'unlock', vehicleId })}
      onSubscribe={() => checkout.mutate({ kind: 'subscription' })}
    />
  );

  return (
    <div className={styles.page}>
      {toast}
      <Link to="/app" className={styles.back}>
        <ArrowLeft size={16} /> My vehicles
      </Link>

      {isPastDue(meQuery.data) && <PastDueBanner />}

      <header className={styles.header}>
        <h1 className={styles.title}>{vehicleName(vehicle)}</h1>
        <p className={styles.metaLine}>
          {ready && (
            <>
              <span>
                <strong>{unreadTotal}</strong> unread
              </span>
              <span className={styles.metaSep} aria-hidden="true">·</span>
              <span>
                <strong>{vehicle.newThisWeek}</strong> new this week
              </span>
              <span className={styles.metaSep} aria-hidden="true">·</span>
            </>
          )}
          <span className={`${styles.badge} ${full ? styles.badgeAccess : ''}`}>{access}</span>
          {!ready && <span className={styles.badge}>{availabilityLabel(vehicle)}</span>}
        </p>
        {!ready && (
          <p className={styles.processing} role="status">
            We're reading this car's bulletins. You'll get an email when it's ready.
          </p>
        )}
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
            <div className={styles.searchRow}>
              <SearchBox
                ref={searchRef}
                value={search}
                onSubmit={(v) => update({ search: v || null, page: null })}
              />
              <FiltersPopover
                vehicleId={vehicleId}
                value={filterValues}
                onChange={update}
                onClear={clearFilters}
              />
            </div>

            <SeverityTabs
              facets={facets}
              selected={severity ?? 'all'}
              onSelect={setSeverity}
              unreadOnly={unreadOnly}
              onToggleUnread={(on) => update({ unread: on ? '1' : null, page: null })}
              onMarkAllRead={(sev) => markAllRead.mutate(sev)}
              marking={markAllRead.isPending}
              totalUnread={vehicle.unread}
              narrowed={Boolean(search || filtersActive)}
            />

            {filtersActive && (
              <p className={styles.filteredLine}>
                Filtered ·{' '}
                <button type="button" className={styles.filteredClear} onClick={clearFilters}>
                  Clear
                </button>
              </p>
            )}

            {list}
          </div>

          {!compact && ready && (
            <div className={styles.panelCol}>
              {selected ? (
                panel
              ) : (
                <div className={styles.panelEmpty}>
                  <FileText size={28} aria-hidden="true" />
                  <p className={styles.panelEmptyTitle}>Select a bulletin</p>
                  <p className={styles.panelEmptyText}>
                    Pick one from the list to see what it covers and how to tell if your car has it.
                  </p>
                </div>
              )}
            </div>
          )}
          {selected && compact && (
            <BulletinSheet title={vehicleName(vehicle)} onClose={closePanel}>
              {panel}
            </BulletinSheet>
          )}

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
