/* The inbox search box: submits on Enter and 300 ms after the last keystroke. */

import { forwardRef, useEffect, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';

import styles from './SearchBox.module.css';

const DEBOUNCE_MS = 300;

interface Props {
    /** The committed search (from the URL). */
    value: string;
    onSubmit: (value: string) => void;
}

export const SearchBox = forwardRef<HTMLInputElement, Props>(function SearchBox({ value, onSubmit }, ref) {
    const [text, setText] = useState(value);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

    // The URL changed from outside (Clear search, back button): follow it.
    useEffect(() => {
        setText(value);
    }, [value]);

    useEffect(
        () => () => {
            if (timer.current) clearTimeout(timer.current);
        },
        []
    );

    const commit = (next: string) => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = null;
        if (next.trim() !== value) onSubmit(next.trim());
    };

    const change = (next: string) => {
        setText(next);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => commit(next), DEBOUNCE_MS);
    };

    return (
        <div className={styles.wrap}>
            <Search size={16} className={styles.icon} aria-hidden="true" />
            <input
                ref={ref}
                type="text"
                className={styles.input}
                placeholder="Search this car's bulletins (brake shudder, screen reboots, water leak…)"
                aria-label="Search bulletins"
                value={text}
                onChange={(e) => change(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') commit(text);
                }}
            />
            {text && (
                <button
                    type="button"
                    className={styles.clear}
                    aria-label="Clear search"
                    onClick={() => {
                        setText('');
                        commit('');
                    }}
                >
                    <X size={16} aria-hidden="true" />
                </button>
            )}
        </div>
    );
});
