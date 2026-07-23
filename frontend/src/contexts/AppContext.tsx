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
  /** The off-canvas nav drawer, which only the compact layout renders. */
  isNavOpen: boolean;
}

type AppAction =
  | { type: 'SELECT_VEHICLE'; payload: number | null }
  | { type: 'OPEN_ADD_MODAL' }
  | { type: 'CLOSE_ADD_MODAL' }
  | { type: 'SET_VIEW'; payload: AppView }
  | { type: 'TOGGLE_NAV' }
  | { type: 'CLOSE_NAV' };

// Every action that navigates also closes the drawer. Keeping that here rather
// than in the sidebar's click handlers means a new nav control cannot forget to
// do it, and the drawer can never be left covering the view it just opened.
function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'SELECT_VEHICLE':
      // Picking a vehicle always returns to the browse view.
      return {
        ...state,
        selectedVehicleId: action.payload,
        view: 'browse',
        isNavOpen: false,
      };
    case 'OPEN_ADD_MODAL':
      return { ...state, showAddModal: true, isNavOpen: false };
    case 'CLOSE_ADD_MODAL':
      return { ...state, showAddModal: false };
    case 'SET_VIEW':
      return { ...state, view: action.payload, isNavOpen: false };
    case 'TOGGLE_NAV':
      return { ...state, isNavOpen: !state.isNavOpen };
    case 'CLOSE_NAV':
      return { ...state, isNavOpen: false };
    default:
      return state;
  }
}

const initialState: AppState = {
  selectedVehicleId: null,
  showAddModal: false,
  view: 'browse',
  isNavOpen: false,
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
