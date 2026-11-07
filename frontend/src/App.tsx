import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';
import { AppProvider } from './contexts/AppContext';
import { AuthProvider } from './auth/AuthContext';
import { RequireAdmin, RequireAuth } from './auth/guards';
import { SiteLayout } from './pages/SiteLayout';
import { LandingPage } from './pages/LandingPage';
import { AppHome } from './pages/AppHome';
import { VehiclePage } from './pages/VehiclePage';
import { AccountPage } from './pages/AccountPage';
import { UnsubscribePage } from './pages/UnsubscribePage';
import { AdminDashboard } from './pages/AdminDashboard';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 1000 * 60 * 5, retry: 1 } },
});

const router = createBrowserRouter([
  {
    element: <SiteLayout />,
    children: [
      { path: '/', element: <LandingPage /> },
      {
        path: '/app',
        element: (
          <RequireAuth>
            <AppHome />
          </RequireAuth>
        ),
      },
      {
        path: '/app/vehicles/:id',
        element: (
          <RequireAuth>
            <VehiclePage />
          </RequireAuth>
        ),
      },
      {
        path: '/app/account',
        element: (
          <RequireAuth>
            <AccountPage />
          </RequireAuth>
        ),
      },
      { path: '/unsubscribe/:token', element: <UnsubscribePage /> },
    ],
  },
  {
    path: '/admin/*',
    element: (
      <RequireAdmin>
        <AdminDashboard />
      </RequireAdmin>
    ),
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AppProvider>
          <RouterProvider router={router} />
        </AppProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
