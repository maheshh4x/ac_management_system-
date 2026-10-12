'use client';

import React, { useEffect, useState } from 'react';
import { useCampus } from '@/contexts/CampusContext';
import { getRooms } from '@/lib/campus-services';
import { Room } from '@/lib/types/campus';
import { Html } from '@react-three/drei';
import * as THREE from 'three';
import AcMarker from './AcMarker';

export default function FloorView() {
  const { state, setState } = useCampus();
  const [rooms, setRooms] = useState<Room[]>([]);

  useEffect(() => {
    if (state.selectedFloorId) {
      getRooms(state.selectedFloorId).then(setRooms);
    }
  }, [state.selectedFloorId]);

  // If we don't have a floor, do nothing (shouldn't happen strictly)
  if (!state.selectedFloorId) return null;

  // Simple layout of rooms for the floor
  return (
    <group position={[0, 0, 0]}>
      {/* Floor Base */}
      <mesh receiveShadow position={[0, -0.1, 0]} rotation={[-Math.PI / 2, 0, 0]}>
         <planeGeometry args={[20, 15]} />
         <meshStandardMaterial color="#e2e8f0" />
      </mesh>
      
      {/* Rooms mock grid layout */}
      {rooms.map((room, index) => {
        // Simple mock layout: tile rooms 
        const x = -5 + (index % 3) * 5;
        const z = -2 + Math.floor(index / 3) * 5;
        const isHovered = false; // We can add local state if needed
        const isSelected = state.selectedRoomId === room.id;

        return (
          <group key={room.id} position={[x, 0.5, z]}>
             {/* Room bounds layout */}
             <mesh 
               castShadow 
               receiveShadow 
               position={[0, 0.5, 0]}
               onClick={(e) => {
                 e.stopPropagation();
                 setState(prev => ({ ...prev, level: 'Room', selectedRoomId: room.id }));
               }}
               onPointerOver={(e) => {
                 e.stopPropagation();
                 document.body.style.cursor = 'pointer';
               }}
               onPointerOut={() => {
                 document.body.style.cursor = 'auto';
               }}
             >
               <boxGeometry args={[4, 1, 4]} />
               <meshStandardMaterial color={isSelected ? "#93c5fd" : "#cbd5e1"} transparent opacity={0.6} />
             </mesh>

             <Html position={[0, 2, 0]} center style={{ pointerEvents: 'none' }}>
                <div className="bg-slate-900/80 text-white px-2 py-1 rounded text-xs whitespace-nowrap shadow-md backdrop-blur-sm pointer-events-none">
                  {room.name} ({room.type})
                </div>
             </Html>

             {/* If Room is selected, render its AC markers inside it */}
             {isSelected && (
               <AcMarkersList roomId={room.id} />
             )}
          </group>
        );
      })}
    </group>
  );
}

function AcMarkersList({ roomId }: { roomId: string }) {
  const { state, setState } = useCampus();
  const [acs, setAcs] = React.useState<any[]>([]);
  
  React.useEffect(() => {
    import('@/lib/campus-services').then(mod => {
       mod.getACs(roomId).then(setAcs);
    });
  }, [roomId]);

  return (
    <group position={[0, 1.5, 0]}>
      {acs.map((ac, i) => {
        const ax = -1 + (i % 2) * 2;
        const az = -1 + Math.floor(i / 2) * 2;
        return (
          <AcMarker 
             key={ac.id} 
             ac={ac} 
             position={[ax, 0, az]} 
             isSelected={state.selectedAcId === ac.id}
             onClick={() => {
                setState(prev => ({ ...prev, level: 'AC', selectedAcId: ac.id }));
             }}
          />
        );
      })}
    </group>
  )
}
