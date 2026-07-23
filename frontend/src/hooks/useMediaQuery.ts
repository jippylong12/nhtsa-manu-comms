/* Media-query subscription for layout decisions that CSS alone cannot make. */

import { useCallback, useSyncExternalStore } from 'react';

/**
 * Track whether a CSS media query currently matches.
 *
 * Prefer plain CSS wherever it can do the job. This exists for the cases where
 * the two layouts are different component trees rather than different styling
 * of one tree — the resizable panel group is one, since it measures and sizes
 * its panels in JavaScript and cannot be told to step aside by a media query.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener('change', onStoreChange);
      return () => list.removeEventListener('change', onStoreChange);
    },
    [query]
  );

  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false
  );
}
