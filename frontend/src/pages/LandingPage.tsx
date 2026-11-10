/* Public landing page: pick a car, see its severity counts, sign up for the rest. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowRight, ArrowUp, BellRing, Check, FileText, ListFilter, Stethoscope } from 'lucide-react';

import { useAuth } from '../auth/AuthContext';
import { SignInDialog } from '../auth/SignInDialog';
import { MONTHLY_PRICE_LABEL, UNLOCK_PRICE_LABEL } from '../features/billing/prices';
import { CountsCard } from '../features/lookup/CountsCard';
import { useAddVehicle } from '../features/my-vehicles/hooks/useMyVehicles';
import { useLookup } from '../features/lookup/useLookup';
import { VehiclePicker, type PickerValue } from '../features/lookup/VehiclePicker';
import styles from './LandingPage.module.css';

// One click fills the lookup, so a visitor sees a real result before typing anything.
const TRY_CARS: { label: string; value: PickerValue }[] = [
  { label: '2024 Silverado EV', value: { year: 2024, make: 'CHEVROLET', model: 'SILVERADO EV' } },
  { label: '2023 RAV4', value: { year: 2023, make: 'TOYOTA', model: 'RAV4' } },
  { label: '2021 Civic', value: { year: 2021, make: 'HONDA', model: 'CIVIC' } },
];

const POINTS = [
  {
    icon: ListFilter,
    title: 'Sorted by how much it matters',
    body: 'Every bulletin is filed as serious, medium or small, so a recall never hides behind a note about floor mats.',
  },
  {
    icon: Stethoscope,
    title: 'Written for owners',
    body: 'Each one opens with what it covers and how to tell if your car has the problem. The dealer jargon stays in the source PDF, one click away.',
  },
  {
    icon: BellRing,
    title: 'New ones come to you',
    body: 'Manufacturers publish new bulletins all year. Subscribers get an email the morning a new one lands for their car.',
  },
];

const PLANS = [
  {
    key: 'free',
    name: 'Free',
    price: '$0',
    cadence: '',
    blurb: 'See what is out there for your car.',
    features: [
      'Look up any car and see its counts',
      'A free account to read your sample',
      'A free sample: the newest 1 serious, 2 medium and 3 small, in full',
    ],
    cta: 'Start free',
    featured: false,
  },
  {
    key: 'unlock',
    name: 'Unlock a car',
    price: UNLOCK_PRICE_LABEL,
    cadence: 'once',
    blurb: 'Everything for one car, yours to keep.',
    features: [
      'Every bulletin for that car, in full',
      'Search and filters across all of them',
      'Symptom search: describe the problem, get the matching bulletins',
    ],
    cta: 'Start free, unlock inside',
    featured: false,
  },
  {
    key: 'subscribe',
    name: 'Subscribe',
    price: MONTHLY_PRICE_LABEL.replace('/month', ''),
    cadence: 'a month',
    blurb: 'Every car you own, and the news as it happens.',
    features: [
      'Everything in Unlock, for every car on your account (up to 10)',
      'A daily email when a new bulletin lands for one of your cars',
      'Cancel any time from your account page',
    ],
    cta: 'Start free, subscribe inside',
    featured: true,
  },
];

const FAQ = [
  {
    q: 'What is a manufacturer communication?',
    a: 'When a carmaker learns about a problem, it writes to its dealers: service bulletins, repair procedures, warranty extensions, recall notices. US law makes carmakers file a copy with NHTSA, the federal safety agency. Most owners never see them.',
  },
  {
    q: 'Is this the same as a recall lookup?',
    a: 'No. Recalls are a small part of it. Most of what carmakers file are service bulletins for known problems that never become a recall, which is exactly why they are worth knowing about before a dealer visit.',
  },
  {
    q: 'Where does the information come from?',
    a: "From NHTSA's public files. We read each document and summarize it, and every bulletin links to its original PDF so you can check our summary against the source.",
  },
  {
    q: 'How do I pay?',
    a: 'You do not pick a plan up front. Sign up free, add your car, and look at the sample. The Unlock and Subscribe buttons are on your car\'s page, and payment runs through Stripe.',
  },
  {
    q: 'What if my car is not there yet?',
    a: "Add it anyway. We read a new car's bulletins within about a day and email you when they are ready.",
  },
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

  const addVehicle = useAddVehicle();
  const pendingAdd = useRef(false);
  const [addError, setAddError] = useState<string | null>(null);

  const lookup = useLookup(value.year, value.make, value.model);
  const selected = !!value.year && !!value.make && !!value.model;

  function handleChange(next: PickerValue) {
    const p = new URLSearchParams();
    if (next.year) p.set('year', String(next.year));
    if (next.make) p.set('make', next.make);
    if (next.model) p.set('model', next.model);
    setParams(p, { replace: true });
  }

  const { mutateAsync: addVehicleAsync } = addVehicle;
  const { year, make, model } = value;
  const addAndOpen = useCallback(async () => {
    if (!year || !make || !model) {
      navigate('/app');
      return;
    }
    try {
      const added = await addVehicleAsync({ year, make, model });
      navigate(`/app/vehicles/${added.vehicleId}`);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : 'We could not add that car.');
    }
  }, [year, make, model, addVehicleAsync, navigate]);

  // The sign-in dialog resolves before or after `user` changes; either way the
  // add runs once, as soon as there is a user and the visitor asked for it.
  useEffect(() => {
    if (user && pendingAdd.current) {
      pendingAdd.current = false;
      queueMicrotask(() => void addAndOpen());
    }
  }, [user, addAndOpen]);

  const yearRef = useRef<HTMLInputElement>(null);
  const checkRef = useRef<HTMLDivElement>(null);

  // Send the visitor back to the lookup: it is the first step for everyone.
  function goToCheck() {
    checkRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => yearRef.current?.focus({ preventScroll: true }), 350);
  }

  // "Start free": with no car picked yet, the lookup comes first; otherwise sign in and add it.
  function handleStart() {
    setAddError(null);
    if (!selected) {
      goToCheck();
      return;
    }
    if (!user) {
      pendingAdd.current = true;
      setSignInOpen(true);
      return;
    }
    void addAndOpen();
  }

  function handleSeeAll() {
    setAddError(null);
    if (!user) {
      pendingAdd.current = true;
      setSignInOpen(true);
      return;
    }
    void addAndOpen();
  }

  return (
    <div className={styles.page}>
      <section className={styles.hero} aria-labelledby="hero-title">
        <p className={styles.eyebrow}>From NHTSA's manufacturer files</p>
        <h1 id="hero-title" className={styles.heading}>
          Your dealer got the memo. <span className={styles.accent}>You didn't.</span>
        </h1>
        <p className={styles.value}>
          Carmakers send dealers thousands of bulletins about known problems, repairs and recalls.
          See how many are on file for your car, in about ten seconds.
        </p>

        <div className={styles.check} ref={checkRef}>
          <div className={styles.checkHead}>
            <h2 className={styles.checkLabel}>Check your car</h2>
            <span className={styles.checkFree}>Free to check. No card needed.</span>
          </div>
          <VehiclePicker value={value} onChange={handleChange} yearRef={yearRef} />
          {!selected && (
            <div className={styles.tryRow}>
              <span className={styles.tryLabel}>Or try one:</span>
              {TRY_CARS.map((car) => (
                <button
                  key={car.label}
                  type="button"
                  className={styles.tryChip}
                  onClick={() => handleChange(car.value)}
                >
                  {car.label}
                </button>
              ))}
            </div>
          )}
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
            {addError && (
              <p className={styles.error} role="alert">{addError}</p>
            )}
          </div>
        </div>
      </section>

      <section className={styles.sample} aria-labelledby="sample-title">
        <div className={styles.sampleCopy}>
          <h2 id="sample-title" className={styles.h2}>What you actually get</h2>
          <p className={styles.lede}>
            Not a pile of PDFs. Each bulletin becomes a short answer to two questions: what is this,
            and does my car have it?
          </p>
          <ul className={styles.points}>
            {POINTS.map(({ icon: Icon, title, body }) => (
              <li key={title} className={styles.point}>
                <span className={styles.pointIcon} aria-hidden="true"><Icon size={18} /></span>
                <span>
                  <span className={styles.pointTitle}>{title}</span>
                  <span className={styles.pointBody}>{body}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <figure className={styles.mock} aria-label="Example bulletin">
          <figcaption className={styles.mockCaption}>Example · 2024 Chevrolet Silverado EV</figcaption>
          <div className={styles.mockHead}>
            <span className={styles.mockDate}>Jun 17, 2026</span>
            <span className={styles.mockType}>Preliminary Info Technical</span>
            <span className={styles.mockPill}>Medium</span>
          </div>
          <p className={styles.mockSummary}>
            GM restricts replacement of the front view camera on 2024-2026 models. Technicians must
            finish a set of diagnostic checks before any camera is replaced.
          </p>
          <p className={styles.mockLabel}><Stethoscope size={13} aria-hidden="true" /> How to tell if your car has this</p>
          <ul className={styles.mockList}>
            <li>"Front Camera Blocked" message</li>
            <li>Forward Collision or Automatic Braking warning lit</li>
            <li>Lane Keep Assist warning lit</li>
          </ul>
          <span className={styles.mockButton}><FileText size={14} aria-hidden="true" /> Read the bulletin (PDF)</span>
        </figure>
      </section>

      <section className={styles.pricing} aria-labelledby="pricing-title">
        <h2 id="pricing-title" className={styles.h2}>Simple pricing</h2>
        <p className={styles.lede}>Look first, pay only if it is worth it to you.</p>
        <div className={styles.plans}>
          {PLANS.map((plan) => (
            <article key={plan.key} className={`${styles.plan} ${plan.featured ? styles.planFeatured : ''}`}>
              {plan.featured && <span className={styles.planTag}>Most complete</span>}
              <h3 className={styles.planName}>{plan.name}</h3>
              <p className={styles.planPrice}>
                <span className={styles.planAmount}>{plan.price}</span>
                {plan.cadence && <span className={styles.planCadence}>{plan.cadence}</span>}
              </p>
              <p className={styles.planBlurb}>{plan.blurb}</p>
              <ul className={styles.planFeatures}>
                {plan.features.map((f) => (
                  <li key={f}>
                    <Check size={15} aria-hidden="true" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className={`btn ${plan.featured ? 'btn-primary' : 'btn-secondary'} ${styles.planCta}`}
                onClick={handleStart}
              >
                {selected ? plan.cta : 'Check your car first'}
              </button>
            </article>
          ))}
        </div>
        <p className={styles.pricingNote}>
          Every account starts free: check your car above, then sign up to read the sample. You choose Unlock or Subscribe on your car's page.
        </p>
      </section>

      <section className={styles.faq} aria-labelledby="faq-title">
        <h2 id="faq-title" className={styles.h2}>Questions</h2>
        <div className={styles.faqList}>
          {FAQ.map((item) => (
            <details key={item.q} className={styles.faqItem}>
              <summary className={styles.faqQ}>{item.q}</summary>
              <p className={styles.faqA}>{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className={styles.final} aria-labelledby="final-title">
        <h2 id="final-title" className={styles.finalTitle}>See what your carmaker has been telling dealers.</h2>
        <button type="button" className={`btn btn-primary btn-lg ${styles.finalCta}`} onClick={handleStart}>
          {selected ? (
            <>Start free <ArrowRight size={18} aria-hidden="true" /></>
          ) : (
            <>Check your car <ArrowUp size={18} aria-hidden="true" /></>
          )}
        </button>
      </section>

      <footer className={styles.footer}>
        Bulletins are sourced from NHTSA's public files and summarized automatically; check the source
        document before acting on one. NHTSA Comms is an independent service and is not affiliated
        with NHTSA or any carmaker.
      </footer>

      {signInOpen && <SignInDialog onClose={() => setSignInOpen(false)} />}
    </div>
  );
}
