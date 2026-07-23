import { useEffect, useRef, type ReactNode } from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { Menu, X } from 'lucide-react';
import { useAppState, useAppDispatch } from '../../contexts/AppContext';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import styles from './AppShell.module.css';

interface AppShellProps {
  sidebar: ReactNode;
  children: ReactNode;
}

/** Width below which the sidebar stops being a column and becomes a drawer.
 *
 * The panel group is the wrong tool down here: it always keeps both panels on
 * screen, so the sidebar holds a share of a viewport that has none to spare and
 * both columns end up unreadable — vehicle names truncate to "2...", and the
 * hero heading runs past the right edge of what is left. 900px is where the
 * default 20% split stops being wide enough to spell out a vehicle name.
 *
 * Kept in sync by hand with the same value in AppShell.module.css; a media
 * query cannot read a custom property, so the number lives in both places.
 */
const COMPACT_QUERY = '(max-width: 899.98px)';

/** Re-measure the layout when the tab becomes visible.
 *
 * A page loaded into a background tab renders with `innerWidth === 0`, so the
 * panel group sizes itself against a zero-width container. Browsers normally
 * fire a resize once the tab is shown, but not reliably, which can leave the
 * layout collapsed until a manual refresh. Nudging a resize on visibility
 * change makes that self-correcting.
 */
function useRelayoutOnVisible() {
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        window.dispatchEvent(new Event('resize'));
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);
}

export function AppShell({ sidebar, children }: AppShellProps) {
  useRelayoutOnVisible();

  const isCompact = useMediaQuery(COMPACT_QUERY);
  const { isNavOpen } = useAppState();
  const dispatch = useAppDispatch();

  const toggleRef = useRef<HTMLButtonElement>(null);
  const wasNavOpen = useRef(false);

  // Closing the drawer hides whichever of its controls had focus, which drops
  // focus to <body> and sends the next Tab back to the top of the document.
  // Hand it back to the button that opened it. In the wide layout that button
  // does not exist and the ref is null, so this quietly does nothing.
  useEffect(() => {
    if (wasNavOpen.current && !isNavOpen) toggleRef.current?.focus();
    wasNavOpen.current = isNavOpen;
  }, [isNavOpen]);

  // The drawer sits over the content behind a scrim, so it owes the viewer the
  // same escape hatch a modal does.
  useEffect(() => {
    if (!isNavOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') dispatch({ type: 'CLOSE_NAV' });
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [isNavOpen, dispatch]);

  // Growing past the breakpoint (rotate, window resize) must not leave the flag
  // set: the wide layout renders no scrim and no toggle, so nothing would be
  // left to turn it off again before the next time the drawer appears.
  useEffect(() => {
    if (!isCompact && isNavOpen) dispatch({ type: 'CLOSE_NAV' });
  }, [isCompact, isNavOpen, dispatch]);

  if (isCompact) {
    return (
      <div className={styles.compactShell}>
        <header className={styles.topBar}>
          {/* The open drawer covers this button, so it never needs to render as
              a close affordance — the drawer carries its own. The name stays
              fixed for the same reason a disclosure button's does: aria-expanded
              carries the state, and "Open navigation, expanded" would contradict
              itself. */}
          <button
            ref={toggleRef}
            type="button"
            className={styles.navToggle}
            onClick={() => dispatch({ type: 'TOGGLE_NAV' })}
            aria-label="Navigation menu"
            aria-expanded={isNavOpen}
            aria-controls="app-nav-drawer"
          >
            <Menu size={20} />
          </button>
          <span className={styles.topBarTitle}>NHTSA Comms</span>
        </header>

        <div
          className={styles.scrim}
          data-open={isNavOpen}
          onClick={() => dispatch({ type: 'CLOSE_NAV' })}
          aria-hidden="true"
        />

        <aside
          id="app-nav-drawer"
          className={`${styles.sidebar} ${styles.drawer}`}
          data-open={isNavOpen}
          aria-label="Vehicles and views"
        >
          <div className={styles.drawerHead}>
            <button
              type="button"
              className={styles.navToggle}
              onClick={() => dispatch({ type: 'CLOSE_NAV' })}
              aria-label="Close navigation"
            >
              <X size={20} />
            </button>
          </div>
          {sidebar}
        </aside>

        {/* The scrim absorbs pointer input, but nothing was stopping Tab from
            walking into the content behind it — a keyboard or screen reader
            user would land on controls they cannot see. `inert` takes the whole
            subtree out of the tab order and the accessibility tree for as long
            as the drawer is over it. */}
        <main className={`${styles.main} ${styles.compactMain}`} inert={isNavOpen}>
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className={styles.shell}>
      <Group orientation="horizontal" id="app-layout">
        {/* react-resizable-panels v4 reads a bare number as pixels, so sizes
            must be percentage strings (v3 treated bare numbers as percentages).
            Without the "%", maxSize=30 capped the sidebar at 30px. */}
        <Panel
          defaultSize="20%"
          minSize="15%"
          maxSize="30%"
          collapsible
          collapsedSize="0%"
          className={styles.sidebarPanel}
        >
          <aside className={styles.sidebar}>
            {sidebar}
          </aside>
        </Panel>
        <Separator className={styles.resizeHandle} />
        <Panel minSize="50%" className={styles.mainPanel}>
          <main className={styles.main}>
            {children}
          </main>
        </Panel>
      </Group>
    </div>
  );
}
