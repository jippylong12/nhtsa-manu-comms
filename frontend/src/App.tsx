import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { VehicleCreate } from '@/client';
import { AppProvider, useAppState, useAppDispatch } from './contexts/AppContext';
import { AppShell } from './components/layout/AppShell';
import { Sidebar } from './components/layout/Sidebar';
import { CorpusView } from './features/communications/components/CorpusView';
import { SymptomSearchView } from './features/symptom-search/components/SymptomSearchView';
import { VehicleGrid } from './features/vehicles/components/VehicleGrid';
import { AddVehicleModal } from './features/vehicles/components/AddVehicleModal';
import { useVehiclesQuery, useCreateVehicle, useDeleteVehicle } from './features/vehicles/hooks/useVehicles';
import { useRefreshFlow } from './features/refresh/hooks/useRefresh';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 1000 * 60 * 5, retry: 1 } },
});

function Dashboard() {
  const { selectedVehicleId, showAddModal, view } = useAppState();
  const dispatch = useAppDispatch();

  const { data: vehiclesData, isLoading: vehiclesLoading } = useVehiclesQuery();
  const { mutate: createVehicle, isPending: isCreating } = useCreateVehicle();
  const { mutate: deleteVehicle } = useDeleteVehicle();

  // Which card's refresh button was pressed. The flow follows one vehicle at a
  // time, matching the server, which permits a single run at a time.
  const [refreshTarget, setRefreshTarget] = useState<number | null>(null);
  const gridRefresh = useRefreshFlow(refreshTarget);

  const vehicles = vehiclesData?.items ?? [];
  const selectedVehicle = vehicles.find(v => v.vehicleId === selectedVehicleId) ?? null;

  const handleAddVehicle = (data: VehicleCreate) => {
    createVehicle(data, { onSuccess: () => dispatch({ type: 'CLOSE_ADD_MODAL' }) });
  };

  const handleDeleteVehicle = (vehicleId: number) => {
    if (confirm('Are you sure you want to remove this vehicle?')) {
      deleteVehicle(vehicleId);
      if (selectedVehicleId === vehicleId) {
        dispatch({ type: 'SELECT_VEHICLE', payload: null });
      }
    }
  };

  const handleRefresh = (vehicleId: number) => {
    setRefreshTarget(vehicleId);
    gridRefresh.ask(vehicleId);
  };

  return (
    <AppShell
      sidebar={
        <Sidebar
          vehicles={vehicles}
          selectedVehicleId={selectedVehicleId}
          view={view}
          onSelectVehicle={(id) => dispatch({ type: 'SELECT_VEHICLE', payload: id })}
          onOpenSymptomSearch={() => dispatch({ type: 'SET_VIEW', payload: 'symptom-search' })}
          onAddVehicle={() => dispatch({ type: 'OPEN_ADD_MODAL' })}
          isLoading={vehiclesLoading}
        />
      }
    >
      {view === 'symptom-search' ? (
        <SymptomSearchView vehicles={vehicles} initialVehicleId={selectedVehicleId} />
      ) : selectedVehicle ? (
        <CorpusView
          vehicleId={selectedVehicle.vehicleId}
          vehicle={selectedVehicle}
          onBack={() => dispatch({ type: 'SELECT_VEHICLE', payload: null })}
        />
      ) : (
        <VehicleGrid
          vehicles={vehicles}
          isLoading={vehiclesLoading}
          onSelectVehicle={(id) => dispatch({ type: 'SELECT_VEHICLE', payload: id })}
          onDeleteVehicle={handleDeleteVehicle}
          onRefreshVehicle={handleRefresh}
          onAddVehicle={() => dispatch({ type: 'OPEN_ADD_MODAL' })}
          refresh={gridRefresh}
          refreshTargetId={refreshTarget}
        />
      )}

      <AddVehicleModal
        isOpen={showAddModal}
        onClose={() => dispatch({ type: 'CLOSE_ADD_MODAL' })}
        onSubmit={handleAddVehicle}
        isLoading={isCreating}
      />
    </AppShell>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <Dashboard />
      </AppProvider>
    </QueryClientProvider>
  );
}
