import { createContext, useContext, useReducer, type ReactNode, type Dispatch } from 'react';

interface AppState {
  selectedVehicleId: number | null;
  showAddModal: boolean;
}

type AppAction =
  | { type: 'SELECT_VEHICLE'; payload: number | null }
  | { type: 'OPEN_ADD_MODAL' }
  | { type: 'CLOSE_ADD_MODAL' };

function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'SELECT_VEHICLE':
      return { ...state, selectedVehicleId: action.payload };
    case 'OPEN_ADD_MODAL':
      return { ...state, showAddModal: true };
    case 'CLOSE_ADD_MODAL':
      return { ...state, showAddModal: false };
    default:
      return state;
  }
}

const initialState: AppState = {
  selectedVehicleId: null,
  showAddModal: false,
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
