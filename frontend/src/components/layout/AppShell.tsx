import { type ReactNode } from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';
import styles from './AppShell.module.css';

interface AppShellProps {
  sidebar: ReactNode;
  children: ReactNode;
}

export function AppShell({ sidebar, children }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <Group orientation="horizontal" id="app-layout">
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
        <Separator className={styles.resizeHandle} />
        <Panel minSize={50} className={styles.mainPanel}>
          <main className={styles.main}>
            {children}
          </main>
        </Panel>
      </Group>
    </div>
  );
}
