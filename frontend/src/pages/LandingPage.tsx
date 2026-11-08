/* Public landing page: pick a car, see its severity counts, sign up for the rest. */

import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { SignInDialog } from '../auth/SignInDialog';
import { CountsCard } from '../features/lookup/CountsCard';
import { useLookup } from '../features/lookup/useLookup';
import { VehiclePicker, type PickerValue } from '../features/lookup/VehiclePicker';
import styles from './LandingPage.module.css';

const STEPS = [
  { title: 'Look up your car', body: 'Pick the year, make and model. No account needed.' },
  { title: 'Sign up free', body: 'Get a free sample of the bulletins we have read for it.' },
  { title: 'Unlock or subscribe', body: 'Unlock every update, or subscribe to hear about new ones.' },
];

export function LandingPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [signInOpen, setSignInOpen] = useState(false);

  const yearParam = Number(params.get('year'));
  const value: PickerValue = {
    year: Number.isInteger(yearParam) && yearParam > 0 ? yearParam : null,
    make: params.get('make') ?? '',
    model: params.get('model') ?? '',
  };

  const lookup = useLookup(value.year, value.make, value.model);
  const selected = !!value.year && !!value.make && !!value.model;

  function handleChange(next: PickerValue) {
    const p = new URLSearchParams();
    if (next.year) p.set('year', String(next.year));
    if (next.make) p.set('make', next.make);
    if (next.model) p.set('model', next.model);
    setParams(p, { replace: true });
  }

  function handleSeeAll() {
    if (!user) {
      setSignInOpen(true);
      return;
    }
    navigate('/app');
  }

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <h1 className={styles.heading}>NHTSA Comms</h1>
        <p className={styles.value}>
          Every recall, service bulletin and manufacturer notice for your car, read and sorted for you.
        </p>
      </header>

      <VehiclePicker value={value} onChange={handleChange} />

      <div className={styles.result}>
        {selected && lookup.isLoading && (
          <p className={styles.status} role="status">Looking up your car...</p>
        )}
        {selected && lookup.isError && (
          <div className={styles.error} role="alert">
            <span>We couldn't look that up just now.</span>
            <button type="button" className={`btn btn-secondary ${styles.retry}`} onClick={() => lookup.refetch()}>
              Try again
            </button>
          </div>
        )}
        {selected && lookup.data && <CountsCard data={lookup.data} onSeeAll={handleSeeAll} />}
      </div>

      <section className={styles.how} aria-labelledby="how-title">
        <h2 id="how-title" className={styles.howTitle}>How it works</h2>
        <ol className={styles.steps}>
          {STEPS.map((s, i) => (
            <li key={s.title} className={styles.step}>
              <span className={styles.stepNum} aria-hidden="true">{i + 1}</span>
              <span className={styles.stepTitle}>{s.title}</span>
              <span className={styles.stepBody}>{s.body}</span>
            </li>
          ))}
        </ol>
      </section>

      <footer className={styles.footer}>
        Communications are sourced from NHTSA and summarized automatically; verify against the source
        document before acting.
      </footer>

      {signInOpen && <SignInDialog onClose={() => setSignInOpen(false)} />}
    </div>
  );
}
