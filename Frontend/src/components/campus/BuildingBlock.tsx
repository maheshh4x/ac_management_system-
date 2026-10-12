'use client';

import React, { useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';
import { Building } from '@/lib/types/campus';

interface BuildingBlockProps {
  building: Building;
  isSelected?: boolean;
  onClick?: () => void;
}

export default function BuildingBlock({ building, isSelected, onClick }: BuildingBlockProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  const { layout } = building;
  if (!layout) return null;

  const targetY = isSelected ? layout.h / 2 + 0.5 : layout.h / 2;

  useFrame((state, delta) => {
    if (meshRef.current) {
      // Smooth hover / selection elevation
      meshRef.current.position.y = THREE.MathUtils.lerp(
        meshRef.current.position.y,
        hovered || isSelected ? targetY + 0.2 : targetY,
        10 * delta
      );
    }
  });

  return (
    <mesh
      ref={meshRef}
      castShadow
      receiveShadow
      position={[layout.x, targetY, layout.y]}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
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
      <boxGeometry args={[layout.w, layout.h, 4]} />
      <meshStandardMaterial 
        color={isSelected ? '#3b82f6' : (hovered ? '#60a5fa' : (layout.color || '#94a3b8'))} 
        roughness={0.7}
        metalness={0.1}
      />
      
      {/* Tooltip on hover */}
      {(hovered || isSelected) && (
        <Html position={[0, layout.h / 2 + 0.5, 0]} center style={{ pointerEvents: 'none' }}>
          <div className="bg-slate-900/90 text-white px-3 py-1.5 rounded text-sm font-medium whitespace-nowrap shadow-xl backdrop-blur-sm border border-slate-700 pointer-events-none">
            {building.name}
            <div className="text-xs text-slate-300 mt-0.5">{building.type}</div>
          </div>
        </Html>
      )}
    </mesh>
  );
}
