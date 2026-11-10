/* /terms and /privacy: one readable column, public, no sign-in. */

import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

import { LAST_UPDATED, PRIVACY, PRODUCT, TERMS } from './content';
import type { LegalDoc } from './content';
import styles from './LegalPage.module.css';

function LegalPage({ doc, other }: { doc: LegalDoc; other: { to: string; label: string } }) {
  useEffect(() => {
    const previous = document.title;
    document.title = `${doc.title} · ${PRODUCT}`;
    window.scrollTo(0, 0);
    return () => {
      document.title = previous;
    };
  }, [doc.title]);

  return (
    <article className={styles.page}>
      <Link to="/" className={styles.back}>
        <ArrowLeft size={16} aria-hidden="true" /> Home
      </Link>
      <header className={styles.head}>
        <h1 className={styles.title}>{doc.title}</h1>
        <p className={styles.updated}>Last updated {LAST_UPDATED}</p>
        <p className={styles.intro}>{doc.intro}</p>
      </header>
      {doc.sections.map((section, i) => (
        <section key={section.heading} className={styles.section}>
          <h2 className={styles.h2}>
            <span className={styles.num}>{i + 1}</span>
            {section.heading}
          </h2>
          {section.paragraphs?.map((p) => (
            <p key={p} className={styles.p}>
              {p}
            </p>
          ))}
          {section.bullets && (
            <ul className={styles.list}>
              {section.bullets.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}
        </section>
      ))}
      <footer className={styles.foot}>
        See also our <Link to={other.to}>{other.label}</Link>.
      </footer>
    </article>
  );
}

export function TermsPage() {
  return <LegalPage doc={TERMS} other={{ to: '/privacy', label: 'Privacy Policy' }} />;
}

export function PrivacyPage() {
  return <LegalPage doc={PRIVACY} other={{ to: '/terms', label: 'Terms of Service' }} />;
}
