/* Keyboard map for the bulletin inbox, attached at the page.
 *
 * Roving tabindex (the highlighted row is the only tab stop), not aria-activedescendant:
 * the rows are real buttons, so Enter and focus rings come for free.
 */

import { useEffect, useRef, type RefObject } from 'react';

interface Options {
    /** Rows in the current list. */
    count: number;
    /** Index of the highlighted row. */
    highlighted: number;
    onHighlight: (index: number) => void;
    /** Enter on the highlighted row (push-mode navigation). */
    onOpen: (index: number) => void;
    panelOpen: boolean;
    /** The panel is a modal sheet: only Esc stays live behind it. */
    modal: boolean;
    onClosePanel: () => void;
    searchRef: RefObject<HTMLInputElement | null>;
    listRef: RefObject<HTMLElement | null>;
    /** Clears the committed search (Esc with the box focused and the panel closed). */
    onClearSearch: () => void;
    enabled: boolean;
}

function isEditable(el: Element | null): boolean {
    if (!el) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (el as HTMLElement).isContentEditable;
}

export function useInboxKeyboard(options: Options) {
    const latest = useRef(options);
    useEffect(() => {
        latest.current = options;
    });

    useEffect(() => {
        const onKeyDown = (e: KeyboardEvent) => {
            const o = latest.current;
            if (!o.enabled || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
            const target = e.target instanceof Element ? e.target : null;
            const inSearch = target === o.searchRef.current;

            if (e.key === 'Escape') {
                if (o.panelOpen) {
                    e.preventDefault();
                    o.onClosePanel();
                } else if (inSearch) {
                    e.preventDefault();
                    o.onClearSearch();
                    o.searchRef.current?.blur();
                }
                return;
            }
            if (o.modal && o.panelOpen) return;

            if (e.key === '/') {
                if (isEditable(target) || isEditable(document.activeElement)) return;
                e.preventDefault();
                o.searchRef.current?.focus();
                return;
            }

            // The rest act on the list: only when focus is on the page itself or on a row.
            const onList = target === document.body || Boolean(target && o.listRef.current?.contains(target));
            if (!onList || o.count === 0) return;

            const last = o.count - 1;
            let next: number | null = null;
            if (e.key === 'ArrowDown') next = Math.min(last, o.highlighted + 1);
            else if (e.key === 'ArrowUp') next = Math.max(0, o.highlighted - 1);
            else if (e.key === 'Home') next = 0;
            else if (e.key === 'End') next = last;
            else if (e.key === 'Enter' && target === document.body) {
                e.preventDefault();
                o.onOpen(o.highlighted);
                return;
            }
            if (next !== null) {
                e.preventDefault();
                o.onHighlight(next);
            }
        };
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, []);
}
