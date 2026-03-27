import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppProvider, useAppState, useAppDispatch } from './contexts/AppContext';
import { AppShell } from './components/layout/AppShell';
import { Sidebar } from './components/layout/Sidebar';
import { CommunicationsView } from './features/communications/components/CommunicationsView';
import { VehicleGrid } from './features/vehicles/components/VehicleGrid';
import { AddVehicleModal } from './features/vehicles/components/AddVehicleModal';
import { useVehiclesQuery, useCreateVehicle, useDeleteVehicle } from './features/vehicles/hooks/useVehicles';
import { useFetchCommunications } from './features/communications/hooks/useCommunications';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 1000 * 60 * 5, retry: 1 } },
});

function Dashboard() {
  const { selectedVehicleId, showAddModal } = useAppState();
  const dispatch = useAppDispatch();

  const { data: vehiclesData, isLoading: vehiclesLoading } = useVehiclesQuery();
  const { mutate: createVehicle, isPending: isCreating } = useCreateVehicle();
  const { mutate: deleteVehicle } = useDeleteVehicle();
  const { fetch: fetchComms, progress, isFetching, reset } = useFetchCommunications();

  const vehicles = vehiclesData?.items ?? [];
  const selectedVehicle = vehicles.find(v => v.vehicleId === selectedVehicleId) ?? null;

  const handleAddVehicle = (data: { vehicleId: number; year: string; model: string; keywords: string[] }) => {
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

  return (
    <AppShell
      sidebar={
        <Sidebar
          vehicles={vehicles}
          selectedVehicleId={selectedVehicleId}
          onSelectVehicle={(id) => dispatch({ type: 'SELECT_VEHICLE', payload: id })}
          onAddVehicle={() => dispatch({ type: 'OPEN_ADD_MODAL' })}
          isLoading={vehiclesLoading}
        />
      }
    >
      {selectedVehicle ? (
        <CommunicationsView
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
          onFetchComms={(vehicleId) => fetchComms(vehicleId, false)}
          onAddVehicle={() => dispatch({ type: 'OPEN_ADD_MODAL' })}
          isFetching={isFetching}
          progress={progress}
          onDismissProgress={reset}
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
