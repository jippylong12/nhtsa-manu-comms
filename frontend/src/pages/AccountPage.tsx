/* /app/account: who is signed in, what plan they have, alerts, and their cars' access. */

import { Link } from 'react-router-dom';
import { format } from 'date-fns';

import { useAuth } from '../auth/AuthContext';
import { PastDueBanner } from '../features/billing/components/PastDueBanner';
import { useCheckout, usePortal } from '../features/billing/hooks/useBilling';
import {
  hasBillingAccount,
  planState,
  subscriptionOf,
  unlockedCount,
} from '../features/billing/plan';
import { MONTHLY_PRICE_LABEL, UNLOCK_PRICE_LABEL } from '../features/billing/prices';
import { accessLabel, vehicleName } from '../features/my-vehicles/access';
import { useMeQuery } from '../features/my-vehicles/hooks/useMyVehicles';
import styles from './AccountPage.module.css';

function planLine(me: ReturnType<typeof useMeQuery>['data']): string {
  const state = planState(me);
  const end = subscriptionOf(me)?.currentPeriodEnd;
  if (state === 'subscribed') {
    return `Subscribed: ${MONTHLY_PRICE_LABEL}${end ? `, renews ${format(new Date(end), 'MMM d, yyyy')}` : ''}`;
  }
  if (state === 'past_due') return 'Payment problem: your subscription is past due';
  if (state === 'unlocked') {
    const n = unlockedCount(me);
    return `Unlocked: ${n} ${n === 1 ? 'car' : 'cars'}`;
  }
  return 'Free sample';
}

export function AccountPage() {
  const { user, signOut } = useAuth();
  const meQuery = useMeQuery();
  const checkout = useCheckout();
  const portal = usePortal();
  const me = meQuery.data;
  const subscribed = subscriptionOf(me) !== undefined;
  const checkingOut = checkout.isPending || checkout.isSuccess;

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <div>
          <h1 className={styles.heading}>Account</h1>
          {user?.email && <p className={styles.email}>{user.email}</p>}
        </div>
        <button type="button" className={`btn btn-secondary ${styles.btn}`} onClick={() => void signOut()}>
          Sign out
        </button>
      </header>

      {meQuery.isPending && (
        <div aria-busy="true" aria-label="Loading your plan" className={styles.stack}>
          <div className="skeleton" style={{ height: 110 }} />
          <div className="skeleton" style={{ height: 110 }} />
        </div>
      )}

      {meQuery.isError && (
        <div className={styles.error} role="alert">
          <span>We couldn't load your account.</span>
          <button type="button" className={`btn btn-secondary ${styles.btn}`} onClick={() => void meQuery.refetch()}>
            Try again
          </button>
        </div>
      )}

      {me && (
        <>
          {planState(me) === 'past_due' && <PastDueBanner />}

          <section className={styles.card} aria-labelledby="plan-title">
            <h2 id="plan-title" className={styles.cardTitle}>
              Plan
            </h2>
            <p className={styles.planLine}>{planLine(me)}</p>
            <div className={styles.actions}>
              {hasBillingAccount(me) && (
                <button
                  type="button"
                  className={`btn btn-secondary ${styles.btn}`}
                  disabled={portal.isPending}
                  onClick={() => portal.mutate()}
                >
                  {portal.isPending ? 'Opening…' : 'Manage billing'}
                </button>
              )}
              {!subscribed && (
                <button
                  type="button"
                  className={`btn btn-primary ${styles.btn}`}
                  disabled={checkingOut}
                  onClick={() => checkout.mutate({ kind: 'subscription' })}
                >
                  {checkingOut && checkout.variables.kind === 'subscription'
                    ? 'Opening checkout…'
                    : `Subscribe · ${MONTHLY_PRICE_LABEL}`}
                </button>
              )}
            </div>
            {portal.isError && (
              <p className={styles.inlineError} role="alert">
                {portal.error.message}
              </p>
            )}
            {checkout.isError && (
              <p className={styles.inlineError} role="alert">
                {checkout.error.message}
              </p>
            )}
          </section>

          <section className={styles.card} aria-labelledby="alerts-title">
            <h2 id="alerts-title" className={styles.cardTitle}>
              Alerts
            </h2>
            <label className={styles.toggleRow}>
              <input type="checkbox" disabled aria-describedby="alerts-note" />
              <span>Daily email when a new bulletin lands for one of your cars</span>
            </label>
            <p id="alerts-note" className={styles.note}>
              Coming next
            </p>
          </section>

          <section className={styles.card} aria-labelledby="vehicles-title">
            <h2 id="vehicles-title" className={styles.cardTitle}>
              Vehicles
            </h2>
            {me.vehicles.length === 0 ? (
              <p className={styles.note}>
                No cars yet. <Link to="/app">Add one</Link>
              </p>
            ) : (
              <ul className={styles.list}>
                {me.vehicles.map((v) => {
                  const access = accessLabel(v, me);
                  const opening = checkingOut && checkout.variables.vehicleId === v.vehicleId;
                  return (
                    <li key={v.vehicleId} className={styles.row}>
                      <Link to={`/app/vehicles/${v.vehicleId}`} className={styles.car}>
                        {vehicleName(v)}
                      </Link>
                      <span className={`${styles.badge} ${access === 'Sample' ? '' : styles.badgeFull}`}>{access}</span>
                      {v.access === 'sample' && (
                        <button
                          type="button"
                          className={`btn btn-secondary ${styles.btn}`}
                          disabled={checkingOut}
                          onClick={() => checkout.mutate({ kind: 'unlock', vehicleId: v.vehicleId })}
                        >
                          {opening ? 'Opening checkout…' : `Unlock · ${UNLOCK_PRICE_LABEL}`}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
