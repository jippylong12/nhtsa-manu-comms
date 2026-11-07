import { Outlet } from 'react-router-dom';

import { TopBar } from '../components/layout/TopBar';

export function SiteLayout() {
  return (
    <>
      <TopBar />
      <main>
        <Outlet />
      </main>
    </>
  );
}
