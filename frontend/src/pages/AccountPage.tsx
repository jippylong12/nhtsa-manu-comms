/* /app/account: the plan and what it gives, alerts (all and per car), and the account's cars. */

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { format } from 'date-fns';
import { Bell, BellOff, Check, Crown, Lock, Plus, Sparkles, Trash2 } from 'lucide-react';

import type { Me, MyVehicle, SubscriptionPlan } from '@/client';
import { useAuth } from '../auth/AuthContext';
import { useSetAlerts, useSetVehicleAlerts } from '../features/alerts/hooks/useAlerts';
import { PastDueBanner } from '../features/billing/components/PastDueBanner';
import { useCheckout, usePortal } from '../features/billing/hooks/useBilling';
import { hasBillingAccount, planOf, planState, subscriptionOf, unlockedCount } from '../features/billing/plan';
import {
  ALL_ACCESS_CARS,
  ALL_ACCESS_PRICE_LABEL,
  ALL_ACCESS_REMOVALS,
  MONTHLY_CARS,
  MONTHLY_PRICE_LABEL,
  MONTHLY_REMOVALS,
  UNLOCK_PRICE_LABEL,
} from '../features/billing/prices';
import { accessLabel, isUnlocked, vehicleName } from '../features/my-vehicles/access';
import { RemoveVehicleDialog } from '../features/my-vehicles/components/RemoveVehicleDialog';
import { useMeQuery, useRemoveVehicle } from '../features/my-vehicles/hooks/useMyVehicles';
import styles from './AccountPage.module.css';

interface PlanInfo {
  name: string;
  price: string;
  cars: number;
  benefits: string[];
}

const PLAN_INFO: Record<SubscriptionPlan, PlanInfo> = {
  monthly: {
    name: 'Monthly',
    price: MONTHLY_PRICE_LABEL,
    cars: MONTHLY_CARS,
    benefits: [
      'An email the morning a new bulletin lands for one of your cars',
      `Every bulletin, in full, for ${MONTHLY_CARS} cars at a time`,
      'Symptom search: describe the problem, get the matching bulletins',
      `Swap ${MONTHLY_REMOVALS} car a month`,
    ],
  },
  all_access: {
    name: 'All access',
    price: ALL_ACCESS_PRICE_LABEL,
    cars: ALL_ACCESS_CARS,
    benefits: [
      'An email the morning a new bulletin lands for any of your cars',
      `Every bulletin, in full, for ${ALL_ACCESS_CARS} cars at a time`,
      'Symptom search: describe the problem, get the matching bulletins',
      `Swap up to ${ALL_ACCESS_REMOVALS} cars a month`,
    ],
  },
};

function Benefits({ items }: { items: string[] }) {
  return (
    <ul className={styles.benefits}>
      {items.map((b) => (
        <li key={b}>
          <Check size={16} aria-hidden="true" />
          <span>{b}</span>
        </li>
      ))}
    </ul>
  );
}

/** Cars a subscription is covering: everything on the account that was not bought outright. */
function planCars(me: Me): MyVehicle[] {
  return me.vehicles.filter((v) => !isUnlocked(v, me));
}

export function AccountPage() {
  const { user, signOut } = useAuth();
  const meQuery = useMeQuery();
  const checkout = useCheckout();
  const portal = usePortal();
  const setAlerts = useSetAlerts();
  const setVehicleAlerts = useSetVehicleAlerts();
  const removeVehicle = useRemoveVehicle();
  const [removing, setRemoving] = useState<MyVehicle | null>(null);
  const me = meQuery.data;
  const state = planState(me);
  const plan = planOf(me);
  const sub = subscriptionOf(me);
  const info = plan ? PLAN_INFO[plan] : null;
  const checkingOut = checkout.isPending || checkout.isSuccess;
  const leaving = portal.isPending || portal.isSuccess;
  const end = sub?.currentPeriodEnd ? format(new Date(sub.currentPeriodEnd), 'MMMM d, yyyy') : null;
  const ending = Boolean(sub?.cancelAtPeriodEnd);
  const alertsOn = Boolean(me?.alerts.enabled && me.alerts.eligible);

  const closeRemove = () => {
    setRemoving(null);
    removeVehicle.reset();
  };

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
          <div className="skeleton" style={{ height: 220 }} />
          <div className="skeleton" style={{ height: 140 }} />
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
          {state === 'past_due' && <PastDueBanner />}

          {/* Plan */}
          {info ? (
            <section className={`${styles.card} ${styles.member}`} aria-labelledby="plan-title">
              <div className={styles.memberTop}>
                <span className={styles.crest} aria-hidden="true">
                  <Crown size={22} />
                </span>
                <div className={styles.memberName}>
                  <p className={styles.eyebrow}>Your plan</p>
                  <h2 id="plan-title" className={styles.planName}>
                    {info.name}
                  </h2>
                </div>
                <p className={styles.planPrice}>{info.price}</p>
              </div>
              <p className={styles.renewal} data-ending={ending || undefined}>
                {state === 'past_due'
                  ? 'Your last payment did not go through. Update your card to keep your plan.'
                  : ending
                    ? `Cancelled. Everything stays open until ${end ?? 'the end of this period'}, then your plan ends.`
                    : end
                      ? `Renews ${end}. Cancel any time.`
                      : 'Cancel any time.'}
              </p>
              <Benefits items={info.benefits} />
              <div className={styles.usage}>
                <div className={styles.usageRow}>
                  <span>Cars on your plan</span>
                  <strong>
                    {planCars(me).length} of {info.cars}
                  </strong>
                </div>
                <div
                  className={styles.meter}
                  role="progressbar"
                  aria-label="Cars on your plan"
                  aria-valuemin={0}
                  aria-valuemax={info.cars}
                  aria-valuenow={Math.min(planCars(me).length, info.cars)}
                >
                  <span style={{ width: `${Math.min(100, (planCars(me).length / info.cars) * 100)}%` }} />
                </div>
                {me.removals && (
                  <p className={styles.note}>
                    {Math.max(0, me.removals.limit - me.removals.used)} of {me.removals.limit}{' '}
                    {me.removals.limit === 1 ? 'removal' : 'removals'} left this month
                    {unlockedCount(me) > 0 ? '. Unlocked cars do not count.' : '.'}
                  </p>
                )}
              </div>
              {plan === 'monthly' && !ending && (
                <div className={styles.upgrade}>
                  <Sparkles size={18} aria-hidden="true" />
                  <p>
                    <strong>All access</strong> covers {ALL_ACCESS_CARS} cars for {ALL_ACCESS_PRICE_LABEL}.
                  </p>
                  <button
                    type="button"
                    className={`btn btn-secondary ${styles.btn}`}
                    disabled={leaving}
                    onClick={() => portal.mutate('manage')}
                  >
                    Change plan
                  </button>
                </div>
              )}
              <div className={styles.actions}>
                <button
                  type="button"
                  className={`btn btn-secondary ${styles.btn}`}
                  disabled={leaving}
                  onClick={() => portal.mutate('manage')}
                >
                  {leaving && portal.variables !== 'cancel' ? 'Opening…' : ending ? 'Keep my plan' : 'Manage billing'}
                </button>
                {!ending && (
                  <button
                    type="button"
                    className={`btn btn-ghost ${styles.btn} ${styles.cancel}`}
                    disabled={leaving}
                    onClick={() => portal.mutate('cancel')}
                  >
                    {leaving && portal.variables === 'cancel' ? 'Opening…' : 'Cancel subscription'}
                  </button>
                )}
              </div>
              {!ending && (
                <p className={styles.note}>
                  Cancelling takes one more click on the next page. You keep everything until the end of
                  the period you paid for.
                </p>
              )}
              {portal.isError && (
                <p className={styles.inlineError} role="alert">
                  {portal.error.message}
                </p>
              )}
            </section>
          ) : (
            <section className={styles.card} aria-labelledby="plan-title">
              <h2 id="plan-title" className={styles.cardTitle}>
                Plan
              </h2>
              <p className={styles.planLine}>
                {state === 'unlocked'
                  ? `${unlockedCount(me)} ${unlockedCount(me) === 1 ? 'car' : 'cars'} unlocked for good`
                  : 'Free sample'}
              </p>
              <p className={styles.note}>
                {state === 'unlocked'
                  ? 'Unlocked cars stay yours, new bulletins included. A plan adds more cars and the alert email.'
                  : 'You are reading the newest 1 serious, 2 medium and 3 small bulletins for each car.'}
              </p>
              <div className={styles.offers}>
                {(['monthly', 'all_access'] as const).map((key) => {
                  const offer = PLAN_INFO[key];
                  const opening = checkingOut && checkout.variables?.plan === key;
                  return (
                    <article key={key} className={styles.offer} data-featured={key === 'monthly' || undefined}>
                      {key === 'monthly' && <span className={styles.offerTag}>Most popular</span>}
                      <h3 className={styles.offerName}>{offer.name}</h3>
                      <p className={styles.offerPrice}>{offer.price}</p>
                      <Benefits items={offer.benefits} />
                      <button
                        type="button"
                        className={`btn ${key === 'monthly' ? 'btn-primary' : 'btn-secondary'} ${styles.btn} ${styles.offerCta}`}
                        disabled={checkingOut}
                        onClick={() => checkout.mutate({ kind: 'subscription', plan: key })}
                      >
                        {opening ? 'Opening checkout…' : `Get ${offer.name}`}
                      </button>
                    </article>
                  );
                })}
              </div>
              <p className={styles.note}>Cancel any time from this page. No contract.</p>
              {hasBillingAccount(me) && (
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={`btn btn-ghost ${styles.btn}`}
                    disabled={leaving}
                    onClick={() => portal.mutate('manage')}
                  >
                    {leaving ? 'Opening…' : 'Receipts and billing'}
                  </button>
                </div>
              )}
              {portal.isError && (
                <p className={styles.inlineError} role="alert">
                  {portal.error.message}
                </p>
              )}
              {checkout.isError && checkout.variables?.kind === 'subscription' && (
                <p className={styles.inlineError} role="alert">
                  {checkout.error.message}
                </p>
              )}
            </section>
          )}

          {/* Alerts */}
          <section className={styles.card} aria-labelledby="alerts-title">
            <h2 id="alerts-title" className={styles.cardTitle}>
              Alerts
            </h2>
            {me.alerts.eligible ? (
              <>
                <div className={styles.alertStatus} data-on={alertsOn || undefined}>
                  <span className={styles.alertIcon} aria-hidden="true">
                    {alertsOn ? <Bell size={20} /> : <BellOff size={20} />}
                  </span>
                  <div className={styles.alertText}>
                    <p className={styles.alertTitle}>{alertsOn ? 'Alerts are on' : 'Alerts are off'}</p>
                    <p className={styles.note}>
                      {alertsOn
                        ? `We email ${me.email} the morning a new bulletin lands for one of your cars.`
                        : 'You will not get an email when a new bulletin lands.'}
                    </p>
                  </div>
                  <label className={styles.switch}>
                    <input
                      type="checkbox"
                      role="switch"
                      checked={alertsOn}
                      disabled={setAlerts.isPending}
                      aria-label="Email me about new bulletins"
                      onChange={(e) => setAlerts.mutate(e.target.checked)}
                    />
                    <span aria-hidden="true" />
                  </label>
                </div>
                {alertsOn && me.vehicles.length > 0 && (
                  <ul className={`${styles.list} ${styles.alertCars}`} aria-label="Alerts per car">
                    {me.vehicles.map((v) => (
                      <li key={v.vehicleId} className={styles.row}>
                        <span className={styles.car}>{vehicleName(v)}</span>
                        <span className={styles.note}>{v.alertsEnabled ? 'In your alerts' : 'Left out'}</span>
                        <label className={styles.switch}>
                          <input
                            type="checkbox"
                            role="switch"
                            checked={v.alertsEnabled}
                            disabled={setVehicleAlerts.isPending}
                            aria-label={`Alerts for ${vehicleName(v)}`}
                            onChange={(e) =>
                              setVehicleAlerts.mutate({ vehicleId: v.vehicleId, enabled: e.target.checked })
                            }
                          />
                          <span aria-hidden="true" />
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
                <p className={styles.note}>
                  {me.alerts.lastSentAt
                    ? `Last email sent ${format(new Date(me.alerts.lastSentAt), 'MMM d, yyyy')}.`
                    : 'No email sent yet: we only write when there is something new.'}
                </p>
                {(setAlerts.isError || setVehicleAlerts.isError) && (
                  <p className={styles.inlineError} role="alert">
                    {(setAlerts.error ?? setVehicleAlerts.error)?.message}
                  </p>
                )}
              </>
            ) : (
              <div className={styles.alertStatus}>
                <span className={styles.alertIcon} aria-hidden="true">
                  <Lock size={20} />
                </span>
                <div className={styles.alertText}>
                  <p className={styles.alertTitle}>Alerts come with a plan</p>
                  <p className={styles.note}>
                    Carmakers publish new bulletins all year. With Monthly or All access we email you the
                    morning one lands for your car, so you hear about it before your next dealer visit.
                  </p>
                </div>
              </div>
            )}
          </section>

          {/* My cars */}
          <section className={styles.card} aria-labelledby="vehicles-title">
            <div className={styles.cardHead}>
              <h2 id="vehicles-title" className={styles.cardTitle}>
                My cars
              </h2>
              <Link to="/app" state={{ add: true }} className={`btn btn-primary ${styles.btn}`}>
                <Plus size={18} aria-hidden="true" /> Add a car
              </Link>
            </div>
            {me.vehicles.length === 0 ? (
              <p className={styles.note}>
                No cars yet. <Link to="/app" state={{ add: true }}>Add one</Link>
              </p>
            ) : (
              <ul className={styles.list}>
                {me.vehicles.map((v) => {
                  const access = accessLabel(v, me);
                  const opening = checkingOut && checkout.variables?.vehicleId === v.vehicleId;
                  return (
                    <li key={v.vehicleId} className={styles.row}>
                      <div className={styles.carBlock}>
                        <Link to={`/app/vehicles/${v.vehicleId}`} className={styles.car}>
                          {vehicleName(v)}
                        </Link>
                        <span className={styles.note}>
                          {access === 'Unlocked'
                            ? 'Yours for good, new bulletins included'
                            : access === 'On your plan'
                              ? `Open while your ${info?.name ?? ''} plan is active`
                              : 'Newest 1 serious, 2 medium, 3 small'}
                        </span>
                      </div>
                      <span className={styles.badge} data-access={access}>
                        {access === 'Sample' ? 'Free sample' : access}
                      </span>
                      {access !== 'Unlocked' && (
                        <button
                          type="button"
                          className={`btn btn-secondary ${styles.btn}`}
                          disabled={checkingOut}
                          onClick={() => checkout.mutate({ kind: 'unlock', vehicleId: v.vehicleId })}
                        >
                          {opening
                            ? 'Opening checkout…'
                            : access === 'Sample'
                              ? `Unlock · ${UNLOCK_PRICE_LABEL}`
                              : `Keep for good · ${UNLOCK_PRICE_LABEL}`}
                        </button>
                      )}
                      <button
                        type="button"
                        className={`btn btn-ghost btn-icon ${styles.btn}`}
                        aria-label={`Remove ${vehicleName(v)}`}
                        onClick={() => setRemoving(v)}
                      >
                        <Trash2 size={17} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            {checkout.isError && checkout.variables?.kind === 'unlock' && (
              <p className={styles.inlineError} role="alert">
                {checkout.error.message}
              </p>
            )}
          </section>

          <p className={styles.legal}>
            <Link to="/terms">Terms</Link>
            <span aria-hidden="true">·</span>
            <Link to="/privacy">Privacy</Link>
          </p>
        </>
      )}

      {removing && (
        <RemoveVehicleDialog
          vehicle={removing}
          me={me}
          pending={removeVehicle.isPending}
          error={removeVehicle.isError ? removeVehicle.error.message : null}
          onClose={closeRemove}
          onConfirm={() => removeVehicle.mutate(removing.vehicleId, { onSuccess: closeRemove })}
        />
      )}
    </div>
  );
}
