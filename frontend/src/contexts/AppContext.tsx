/* eslint-disable react-refresh/only-export-components */
// Context + provider + hooks live together by convention. The react-refresh
// rule's "only export components" is a dev-HMR nicety that misfires on the
// context hooks; disabling it here is the standard pattern for context files.
import { createContext, useContext, useReducer, type ReactNode, type Dispatch } from 'react';

export type AppView = 'browse' | 'symptom-search';

interface AppState {
  selectedVehicleId: number | null;
  showAddModal: boolean;
  view: AppView;
}

type AppAction =
  | { type: 'SELECT_VEHICLE'; payload: number | null }
  | { type: 'OPEN_ADD_MODAL' }
  | { type: 'CLOSE_ADD_MODAL' }
  | { type: 'SET_VIEW'; payload: AppView };

function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'SELECT_VEHICLE':
      // Picking a vehicle always returns to the browse view.
      return { ...state, selectedVehicleId: action.payload, view: 'browse' };
    case 'OPEN_ADD_MODAL':
      return { ...state, showAddModal: true };
    case 'CLOSE_ADD_MODAL':
      return { ...state, showAddModal: false };
    case 'SET_VIEW':
      return { ...state, view: action.payload };
    default:
      return state;
  }
}

const initialState: AppState = {
  selectedVehicleId: null,
  showAddModal: false,
  view: 'browse',
};

const AppStateContext = createContext<AppState>(initialState);
const AppDispatchContext = createContext<Dispatch<AppAction>>(() => {});

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  return (
    <AppStateContext.Provider value={state}>
      <AppDispatchContext.Provider value={dispatch}>
        {children}
      </AppDispatchContext.Provider>
    </AppStateContext.Provider>
  );
}

export function useAppState() {
  return useContext(AppStateContext);
}

export function useAppDispatch() {
  return useContext(AppDispatchContext);
}
