'use client';

import React, { createContext, useContext, useState, ReactNode } from 'react';

export type NavigationLevel = 'Campus' | 'Building' | 'Floor' | 'Room' | 'AC';

interface CampusState {
  level: NavigationLevel;
  selectedBuildingId: string | null;
  selectedFloorId: string | null;
  selectedRoomId: string | null;
  selectedAcId: string | null;
}

interface CampusContextType {
  state: CampusState;
  setState: React.Dispatch<React.SetStateAction<CampusState>>;
  navigateUp: () => void;
  resetNavigation: () => void;
}

const CampusContext = createContext<CampusContextType | undefined>(undefined);

export function CampusProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<CampusState>({
    level: 'Campus',
    selectedBuildingId: null,
    selectedFloorId: null,
    selectedRoomId: null,
    selectedAcId: null,
  });

  const navigateUp = () => {
    setState((prev) => {
      if (prev.level === 'AC') return { ...prev, level: 'Room', selectedAcId: null };
      if (prev.level === 'Room') return { ...prev, level: 'Floor', selectedRoomId: null };
      if (prev.level === 'Floor') return { ...prev, level: 'Building', selectedFloorId: null };
      if (prev.level === 'Building') return { ...prev, level: 'Campus', selectedBuildingId: null };
      return prev;
    });
  };

  const resetNavigation = () => {
    setState({
      level: 'Campus',
      selectedBuildingId: null,
      selectedFloorId: null,
      selectedRoomId: null,
      selectedAcId: null,
    });
  };

  return (
    <CampusContext.Provider value={{ state, setState, navigateUp, resetNavigation }}>
      {children}
    </CampusContext.Provider>
  );
}

export function useCampus() {
  const context = useContext(CampusContext);
  if (context === undefined) {
    throw new Error('useCampus must be used within a CampusProvider');
  }
  return context;
}
