/* /app/billing/success: the return from Stripe. Waits for the webhook, then shows the way on. */

import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Loader2 } from 'lucide-react';

import type { CheckoutKind, Me } from '@/client';
import { recallCheckout } from '../features/billing/pending';
import { useMeQuery } from '../features/my-vehicles/hooks/useMyVehicles';
import { vehicleName } from '../features/my-vehicles/access';
import { meKeys } from '../features/queryKeys';
import { planOf } from '../features/billing/plan';
import { trackPurchase } from '../analytics/analytics';
import styles from './BillingSuccessPage.module.css';

const POLL_MS = 2_000;
const GIVE_UP_MS = 20_000;

interface Landed {
  kind: CheckoutKind;
  vehicleId: number | null;
}

/** The entitlement this purchase should have created, read only from /api/me. */
function findLanded(me: Me | undefined, pending: ReturnType<typeof recallCheckout>): Landed | null {
  const active = me?.entitlements.filter((e) => e.status === 'active') ?? [];
  if (pending) {
    const hit = active.find(
      (e) =>
        e.kind === pending.kind && (pending.kind === 'subscription' || e.vehicleId === pending.vehicleId)
    );
    return hit ? { kind: hit.kind, vehicleId: hit.vehicleId } : null;
  }
  const hit = active.find((e) => e.kind === 'subscription') ?? active[0];
  return hit ? { kind: hit.kind, vehicleId: hit.vehicleId } : null;
}

export function BillingSuccessPage() {
  const [params] = useSearchParams();
  const sessionId = params.get('session_id'); // display only; access comes from /api/me
  const queryClient = useQueryClient();
  const meQuery = useMeQuery();
  const [pending] = useState(recallCheckout);
  const [timedOut, setTimedOut] = useState(false);
  const landed = findLanded(meQuery.data, pending);
  const done = landed !== null;
  const { refetch } = meQuery;

  useEffect(() => {
    if (done) {
      void queryClient.invalidateQueries({ queryKey: meKeys.all });
      return;
    }
    void refetch();
    const poll = setInterval(() => void refetch(), POLL_MS);
    const giveUp = setTimeout(() => setTimedOut(true), GIVE_UP_MS);
    return () => {
      clearInterval(poll);
      clearTimeout(giveUp);
    };
  }, [done, refetch, queryClient]);

  // Counted once the entitlement exists, so an abandoned or failed payment is never a sale.
  const boughtPlan = landed?.kind === 'subscription' ? (pending?.plan ?? planOf(meQuery.data)) : null;
  useEffect(() => {
    if (landed) trackPurchase(landed.kind, boughtPlan, sessionId);
  }, [landed, boughtPlan, sessionId]);

  const kind = landed?.kind ?? pending?.kind;
  const vehicle = vehicleFor(meQuery.data, landed?.vehicleId ?? pending?.vehicleId);

  if (!done && timedOut) {
    return (
      <div className={styles.page}>
        <div className={styles.card} role="status">
          <h1 className={styles.title}>Thanks, you're set</h1>
          <p className={styles.line}>
            Your payment went through; access is being activated. Refresh in a minute or email support.
          </p>
          <Link to="/app" className={`btn btn-secondary ${styles.cta}`}>
            Back to my vehicles
          </Link>
        </div>
      </div>
    );
  }

  if (!done) {
    return (
      <div className={styles.page}>
        <div className={styles.card} role="status" aria-busy="true">
          <Loader2 size={28} className={styles.spin} aria-hidden="true" />
          <h1 className={styles.title}>Thanks, you're set</h1>
          <p className={styles.line}>Activating your access…</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.card} role="status">
        <CheckCircle2 size={28} className={styles.ok} aria-hidden="true" />
        <h1 className={styles.title}>Thanks, you're set</h1>
        {kind === 'unlock' ? (
          <p className={styles.line}>
            {vehicle ? `${vehicleName(vehicle)} is` : 'Your car is'} unlocked: every bulletin is open.
          </p>
        ) : (
          <p className={styles.line}>You're subscribed: your cars are open, and new bulletins come to you by email.</p>
        )}
        {kind === 'unlock' && landed?.vehicleId != null ? (
          <Link to={`/app/vehicles/${landed.vehicleId}`} className={`btn btn-primary ${styles.cta}`}>
            Open {vehicle ? vehicleName(vehicle) : 'your car'}
          </Link>
        ) : (
          <Link to="/app" className={`btn btn-primary ${styles.cta}`}>
            Go to my vehicles
          </Link>
        )}
        {sessionId && <p className={styles.ref}>Reference {sessionId.slice(-8)}</p>}
      </div>
    </div>
  );
}

function vehicleFor(me: Me | undefined, vehicleId: number | null | undefined) {
  return vehicleId == null ? undefined : me?.vehicles.find((v) => v.vehicleId === vehicleId);
}
