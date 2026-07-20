/* Symptom Search - describe a problem, pick a vehicle, get a grounded answer
   citing the manufacturer communications that match. */

import { useState } from 'react';
import { Stethoscope, Search, Sparkles, X, AlertCircle, Lightbulb } from 'lucide-react';

import { CorpusCommunicationList } from '../../communications/components/CorpusCommunicationList';
import { useSymptomSearch } from '../hooks/useSymptomSearch';

import type { Vehicle, SymptomMatch, CorpusCommunicationSummary } from '@/client';

import styles from './SymptomSearchView.module.css';

interface Props {
  vehicles: Vehicle[];
  initialVehicleId: number | null;
}

const EXAMPLES = [
  'my truck shudders and jerks when braking hard',
  'the infotainment screen goes black and reboots while driving',
  'service high voltage warning and no heat in the cabin',
  'water is getting into the passenger footwell',
];

// nhtsa_ids are 6+ digit numbers; the composer cites them as [nhtsa_id].
const CITATION_RE = /\[(\d{6,})\]/g;

// The match list reuses the corpus card, which reads a CorpusCommunicationSummary.
// Every match is a processed communication with one document in this corpus.
function toSummary(m: SymptomMatch): CorpusCommunicationSummary {
  return {
    nhtsaId: m.nhtsaId,
    communicationNumber: m.communicationNumber,
    communicationType: m.communicationType,
    communicationDate: m.communicationDate,
    summary: '',
    status: 'processed',
    documentCount: 1,
    llmSummary: m.llmSummary,
    symptoms: m.symptoms,
    systems: m.systems,
    vehicles: [],
  };
}

function scrollToMatch(nhtsaId: string) {
  document
    .getElementById(`match-${nhtsaId}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/** Render the composed answer, turning [nhtsa_id] citations into buttons that
 *  scroll to the corresponding match card. */
function GroundedAnswer({ answer }: { answer: string }) {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  const re = new RegExp(CITATION_RE);
  let m: RegExpExecArray | null;
  while ((m = re.exec(answer)) !== null) {
    if (m.index > last) nodes.push(<span key={key++}>{answer.slice(last, m.index)}</span>);
    const id = m[1];
    nodes.push(
      <button
        key={key++}
        type="button"
        className={styles.citation}
        onClick={() => scrollToMatch(id)}
        title="Jump to this communication"
      >
        {id}
      </button>
    );
    last = m.index + m[0].length;
  }
  if (last < answer.length) nodes.push(<span key={key++}>{answer.slice(last)}</span>);
  return <p className={styles.answerText}>{nodes}</p>;
}

export function SymptomSearchView({ vehicles, initialVehicleId }: Props) {
  const [vehicleId, setVehicleId] = useState<number | null>(
    initialVehicleId ?? vehicles[0]?.vehicleId ?? null
  );
  const [description, setDescription] = useState('');

  const { mutate, data, isPending, isError, error, reset } = useSymptomSearch();

  const canSearch = !!vehicleId && description.trim().length > 0 && !isPending;

  const runSearch = () => {
    if (!canSearch || vehicleId == null) return;
    mutate({ vehicleId, description: description.trim() });
  };

  const clear = () => {
    setDescription('');
    reset();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      runSearch();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      clear();
    }
  };

  const cost = data?.cost;

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <header className={styles.header}>
          <div className={styles.titleRow}>
            <Stethoscope size={22} className={styles.titleIcon} />
            <h2 className={styles.title}>Symptom Search</h2>
          </div>
          <p className={styles.subtitle}>
            Describe a problem in plain language and get the matching recalls, service
            bulletins, and manufacturer communications for your vehicle, with a grounded
            answer that cites each one.
          </p>
        </header>

        {/* Search form */}
        <div className={`${styles.searchCard} glass-card`}>
          <div className={styles.controls}>
            <label className={styles.vehicleSelectWrap}>
              <span className={styles.controlLabel}>Vehicle</span>
              <select
                className={styles.vehicleSelect}
                value={vehicleId ?? ''}
                onChange={(e) => setVehicleId(e.target.value ? Number(e.target.value) : null)}
                aria-label="Vehicle to search"
              >
                {vehicles.length === 0 && <option value="">No vehicles tracked</option>}
                {vehicles.map((v) => (
                  <option key={v.vehicleId} value={v.vehicleId}>
                    {v.year} {v.model}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className={styles.inputWrap}>
            <textarea
              className={styles.textarea}
              placeholder="e.g. the truck shudders and jerks when I brake hard on the highway"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={3}
              aria-label="Describe the symptom"
            />
            {description && (
              <button
                type="button"
                className={styles.clearBtn}
                onClick={clear}
                aria-label="Clear (Esc)"
              >
                <X size={16} />
              </button>
            )}
          </div>

          <div className={styles.actionRow}>
            <span className={styles.hint}>Enter to search · Shift+Enter for a new line · Esc to clear</span>
            <button className="btn btn-primary" onClick={runSearch} disabled={!canSearch}>
              <Search size={16} />
              Search
            </button>
          </div>

          {!data && !isPending && (
            <div className={styles.examples}>
              <span className={styles.examplesLabel}>
                <Lightbulb size={13} /> Try
              </span>
              {EXAMPLES.map((ex) => (
                <button key={ex} className={styles.exampleChip} onClick={() => setDescription(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Results */}
        {isPending && (
          <div className={styles.loading}>
            <div className={styles.loadingHead}>
              <Sparkles size={16} className={styles.spin} />
              Searching this vehicle's communications and composing an answer...
            </div>
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton" style={{ height: 88 }} />
            ))}
          </div>
        )}

        {isError && (
          <div className={styles.errorBox}>
            <AlertCircle size={18} />
            <div>
              <strong>Search failed.</strong>{' '}
              {error?.message || 'Something went wrong. Please try again.'}
            </div>
          </div>
        )}

        {data && !isPending && (
          <div className={styles.results}>
            {data.refused ? (
              <div className={styles.noMatch}>
                <Search size={40} className={styles.noMatchIcon} />
                <h3 className={styles.noMatchTitle}>No matching communications found</h3>
                <p className={styles.noMatchBody}>{data.answer}</p>
                <ul className={styles.suggestions}>
                  <li>Describe what you observe (a warning message, a noise, a behavior).</li>
                  <li>Add when it happens (while charging, braking, at highway speed).</li>
                  <li>Try a different vehicle if the problem is on another one.</li>
                </ul>
              </div>
            ) : (
              <>
                <div className={`${styles.answerCard} glass-card`}>
                  <div className={styles.answerHead}>
                    <Sparkles size={16} className={styles.answerIcon} />
                    <span>Grounded answer</span>
                    {!data.answerGenerated && (
                      <span className={styles.degraded}>summary unavailable</span>
                    )}
                  </div>
                  <GroundedAnswer answer={data.answer} />
                  {cost && (
                    <div className={styles.metaRow}>
                      <span className={styles.metaChip} title="Retrieval + answer cost for this query">
                        cost ${cost.totalUsd.toFixed(6)}
                      </span>
                      {data.model && <span className={styles.metaChip}>{data.model}</span>}
                      <span className={styles.metaChip}>
                        {data.matches.length} match{data.matches.length === 1 ? '' : 'es'}
                      </span>
                    </div>
                  )}
                </div>

                <CorpusCommunicationList
                  communications={data.matches.map(toSummary)}
                  anchorPrefix="match"
                  highlightIds={data.citedIds}
                />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
