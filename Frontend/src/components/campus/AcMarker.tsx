'use client';

import React, { useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';
import { AC } from '@/lib/types/campus';

export default function AcMarker({ ac, position, isSelected, onClick }: { ac: AC, position: [number, number, number], isSelected: boolean, onClick: () => void }) {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'Working': return '#16a34a'; // Green
      case 'Fault': return '#dc2626'; // Red
      case 'Maintenance': return '#f59e0b'; // Amber
      default: return '#64748b'; // Gray
    }
  };

  useFrame((state, delta) => {
    if (meshRef.current) {
      if (isSelected || hovered) {
        meshRef.current.rotation.y += delta; // Spin slowly when selected/hovered
      } else {
         // reset rotation slightly
         meshRef.current.rotation.y = THREE.MathUtils.lerp(meshRef.current.rotation.y, 0, delta * 2);
      }
    }
  });

  return (
    <mesh 
      ref={meshRef}
      position={position}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHovered(true);
        document.body.style.cursor = 'pointer';
      }}
      onPointerOut={() => {
        setHovered(false);
        document.body.style.cursor = 'auto';
      }}
    >
      <boxGeometry args={[0.5, 0.5, 0.5]} />
      <meshStandardMaterial color={getStatusColor(ac.status)} emissive={getStatusColor(ac.status)} emissiveIntensity={isSelected ? 0.5 : 0.1} />
      
      {(hovered || isSelected) && (
        <Html position={[0, 0.8, 0]} center style={{ pointerEvents: 'none' }}>
           <div className="bg-slate-900/90 text-white px-2 py-1 rounded text-[10px] whitespace-nowrap shadow-md backdrop-blur-sm pointer-events-none border border-slate-700 flex flex-col items-center">
             <span className="font-semibold">{ac.id}</span>
             <div className="flex items-center gap-1 mt-0.5">
               <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: getStatusColor(ac.status) }}></span>
               <span className="capitalize">{ac.status}</span>
             </div>
           </div>
        </Html>
      )}
    </mesh>
  );
}
