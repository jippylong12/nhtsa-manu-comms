/* /unsubscribe/:token: the link in every alert email. Works signed out. */

import { Link, useParams } from 'react-router-dom';

import { useUnsubscribe } from '../features/alerts/hooks/useAlerts';
import styles from './pages.module.css';

export function UnsubscribePage() {
  const { token = '' } = useParams();
  const result = useUnsubscribe(token);

  return (
    <div className={`${styles.page} ${styles.unsubscribe}`}>
      <div className={styles.unsubscribeCard} aria-live="polite" aria-busy={result.isPending}>
        {result.isPending && (
          <>
            <h1 className={styles.heading}>Unsubscribing…</h1>
            <div className="skeleton" style={{ height: 16, width: '60%' }} />
          </>
        )}

        {result.isSuccess && (
          <>
            <h1 className={styles.heading}>
              {result.data.status === 'unsubscribed'
                ? "You're unsubscribed from daily alerts"
                : 'You were already unsubscribed'}
            </h1>
            <p className={styles.lead}>We won't send you any more alert emails.</p>
            <Link to="/app/account" className="btn btn-secondary">
              Turn alerts back on
            </Link>
          </>
        )}

        {result.isError && (
          <>
            <h1 className={styles.heading}>
              {'status' in result.error && result.error.status === 404
                ? "This link isn't valid"
                : "We couldn't unsubscribe you"}
            </h1>
            <p className={styles.lead}>
              {'status' in result.error && result.error.status === 404
                ? 'Open the unsubscribe link from your latest alert email, or manage alerts from your account.'
                : 'Something went wrong on our side. Try the link again in a moment.'}
            </p>
            <Link to="/app/account" className="btn btn-secondary">
              Go to your account
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
