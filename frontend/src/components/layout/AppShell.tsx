import { type ReactNode } from 'react';
import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels';
import styles from './AppShell.module.css';

interface AppShellProps {
  sidebar: ReactNode;
  children: ReactNode;
}

export function AppShell({ sidebar, children }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <PanelGroup direction="horizontal" autoSaveId="app-layout">
        <Panel
          defaultSize={20}
          minSize={15}
          maxSize={30}
          collapsible
          collapsedSize={0}
          className={styles.sidebarPanel}
        >
          <aside className={styles.sidebar}>
            {sidebar}
          </aside>
        </Panel>
        <PanelResizeHandle className={styles.resizeHandle} />
        <Panel minSize={50} className={styles.mainPanel}>
          <main className={styles.main}>
            {children}
          </main>
        </Panel>
      </PanelGroup>
    </div>
  );
}
