import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';
import { AppProvider } from './contexts/AppContext';
import { AuthProvider } from './auth/AuthContext';
import { trackPageView } from './analytics/analytics';
import { RequireAdmin, RequireAuth } from './auth/guards';
import { SiteLayout } from './pages/SiteLayout';
import { LandingPage } from './pages/LandingPage';
import { AppHome } from './pages/AppHome';
import { VehiclePage } from './pages/VehiclePage';
import { AccountPage } from './pages/AccountPage';
import { BillingSuccessPage } from './pages/BillingSuccessPage';
import { UnsubscribePage } from './pages/UnsubscribePage';
import { PrivacyPage, TermsPage } from './pages/legal/LegalPage';
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
      {
        path: '/app/billing/success',
        element: (
          <RequireAuth>
            <BillingSuccessPage />
          </RequireAuth>
        ),
      },
      { path: '/unsubscribe/:token', element: <UnsubscribePage /> },
      { path: '/terms', element: <TermsPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
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

// Page views are sent by hand, with a scrubbed address, on every navigation.
router.subscribe(() => trackPageView());

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
