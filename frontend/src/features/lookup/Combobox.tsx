/* Combobox - a type-to-search field over a fixed list of options (WAI-ARIA combobox with a listbox). */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, X } from 'lucide-react';

import styles from './Combobox.module.css';

interface ComboboxProps {
    label: string;
    placeholder: string;
    options: string[];
    /** The committed choice, or '' for none. */
    value: string;
    onChange: (next: string) => void;
    disabled?: boolean;
    loading?: boolean;
    /** Numeric keypad on phones (model years). */
    numeric?: boolean;
}

const MAX_SHOWN = 80;

function matches(option: string, query: string): boolean {
    const q = query.trim().toLowerCase();
    return !q || option.toLowerCase().includes(q);
}

export function Combobox({
    label,
    placeholder,
    options,
    value,
    onChange,
    disabled = false,
    loading = false,
    numeric = false,
}: ComboboxProps) {
    const id = useId();
    const listId = `${id}-list`;
    const inputRef = useRef<HTMLInputElement>(null);
    const wrapRef = useRef<HTMLDivElement>(null);
    const [open, setOpen] = useState(false);
    // What is typed. null means "show the committed value".
    const [draft, setDraft] = useState<string | null>(null);
    const [active, setActive] = useState(0);

    const text = draft ?? value;
    const shown = useMemo(
        () => options.filter((o) => matches(o, draft ?? '')).slice(0, MAX_SHOWN),
        [options, draft]
    );
    const activeIndex = Math.min(active, Math.max(0, shown.length - 1));

    // A click outside closes the list and drops an uncommitted draft.
    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
                setOpen(false);
                setDraft(null);
            }
        };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open]);

    // Keep the highlighted option in view while arrowing.
    useEffect(() => {
        if (!open) return;
        document.getElementById(`${id}-opt-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
    }, [open, activeIndex, id]);

    const commit = (option: string) => {
        onChange(option);
        setDraft(null);
        setOpen(false);
    };

    const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!open) {
                setOpen(true);
                return;
            }
            const step = e.key === 'ArrowDown' ? 1 : -1;
            setActive((activeIndex + step + shown.length) % Math.max(1, shown.length));
        } else if (e.key === 'Enter') {
            if (open && shown[activeIndex]) {
                e.preventDefault();
                commit(shown[activeIndex]);
            }
        } else if (e.key === 'Escape') {
            if (open) {
                e.preventDefault();
                setOpen(false);
                setDraft(null);
            }
        } else if (e.key === 'Tab') {
            // Leaving with exactly one match takes it: typing "toy" then Tab picks TOYOTA.
            if (open && draft && shown.length === 1) commit(shown[0]);
            else setDraft(null);
            setOpen(false);
        }
    };

    const hint = loading ? 'Loading...' : placeholder;

    return (
        <div className={styles.field} ref={wrapRef}>
            <label className={styles.label} htmlFor={id}>
                {label}
            </label>
            <div className={styles.control} data-disabled={disabled || undefined}>
                <input
                    ref={inputRef}
                    id={id}
                    className={styles.input}
                    type="text"
                    role="combobox"
                    inputMode={numeric ? 'numeric' : 'text'}
                    autoComplete="off"
                    autoCapitalize="characters"
                    spellCheck={false}
                    aria-expanded={open}
                    aria-controls={listId}
                    aria-autocomplete="list"
                    aria-activedescendant={open && shown.length ? `${id}-opt-${activeIndex}` : undefined}
                    placeholder={hint}
                    value={text}
                    disabled={disabled}
                    onFocus={(e) => {
                        setOpen(true);
                        e.target.select();
                    }}
                    onChange={(e) => {
                        setDraft(e.target.value);
                        setActive(0);
                        setOpen(true);
                        // Clearing the text clears the choice, so the fields after it reset.
                        if (e.target.value === '' && value) onChange('');
                    }}
                    onKeyDown={onKeyDown}
                />
                {value && !disabled ? (
                    <button
                        type="button"
                        className={styles.iconButton}
                        aria-label={`Clear ${label.toLowerCase()}`}
                        onClick={() => {
                            onChange('');
                            setDraft(null);
                            inputRef.current?.focus();
                        }}
                    >
                        <X size={16} aria-hidden="true" />
                    </button>
                ) : (
                    <ChevronDown size={16} className={styles.chevron} aria-hidden="true" />
                )}
            </div>
            {open && !disabled && (
                <ul id={listId} className={styles.list} role="listbox" aria-label={label}>
                    {shown.length === 0 ? (
                        <li className={styles.empty} role="presentation">
                            {loading ? 'Loading...' : 'No match'}
                        </li>
                    ) : (
                        shown.map((option, i) => (
                            <li
                                key={option}
                                id={`${id}-opt-${i}`}
                                role="option"
                                aria-selected={option === value}
                                className={`${styles.option} ${i === activeIndex ? styles.optionActive : ''}`}
                                // mousedown, not click: it fires before the input's blur closes the list.
                                onMouseDown={(e) => {
                                    e.preventDefault();
                                    commit(option);
                                }}
                                onMouseEnter={() => setActive(i)}
                            >
                                <span>{option}</span>
                                {option === value && <Check size={15} aria-hidden="true" />}
                            </li>
                        ))
                    )}
                </ul>
            )}
        </div>
    );
}
