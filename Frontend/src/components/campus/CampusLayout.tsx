'use client';

import React, { useEffect, useState } from 'react';
import { getBuildings } from '@/lib/campus-services';
import { Building } from '@/lib/types/campus';
import BuildingBlock from './BuildingBlock';
import { useCampus } from '@/contexts/CampusContext';

export default function CampusLayout() {
  const [buildings, setBuildings] = useState<Building[]>([]);
  const { state, setState } = useCampus();

  useEffect(() => {
    let mounted = true;
    getBuildings().then(data => {
      if (mounted) setBuildings(data);
    });
    return () => { mounted = false; };
  }, []);

  const handleBuildingClick = (buildingId: string) => {
    if (state.level === 'Campus' || state.level === 'Building') {
      setState(prev => ({
        ...prev,
        level: 'Building',
        selectedBuildingId: buildingId,
      }));
    }
  };

  return (
    <group>
      {/* Grass/Campus Base */}
      <mesh receiveShadow position={[0, -0.1, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[30, 30]} />
        <meshStandardMaterial color="#86efac" />
      </mesh>
      
      {/* Central Lawn Mockup */}
      <mesh receiveShadow position={[-3, 0.01, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[6, 4]} />
        <meshStandardMaterial color="#22c55e" />
      </mesh>

      {/* Roads mock layout (simplified) */}
      <mesh receiveShadow position={[0, 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
         <planeGeometry args={[26, 1]} />
         <meshStandardMaterial color="#cbd5e1" />
      </mesh>
      <mesh receiveShadow position={[-5, 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
         <planeGeometry args={[1, 15]} />
         <meshStandardMaterial color="#cbd5e1" />
      </mesh>
      <mesh receiveShadow position={[3, 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
         <planeGeometry args={[1, 15]} />
         <meshStandardMaterial color="#cbd5e1" />
      </mesh>

      {/* Buildings */}
      {buildings.map(b => (
        <BuildingBlock 
          key={b.id} 
          building={b} 
          isSelected={state.selectedBuildingId === b.id}
          onClick={() => handleBuildingClick(b.id)}
        />
      ))}
    </group>
  );
}
