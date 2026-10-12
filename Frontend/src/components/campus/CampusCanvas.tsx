'use client';

import React, { Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Environment, ContactShadows, Sky } from '@react-three/drei';
import CampusLayout from './CampusLayout';
import { useCampus } from '@/contexts/CampusContext';
import FloorView from './FloorView';

export default function CampusCanvas() {
  const { state } = useCampus();
  
  return (
    <Canvas 
      shadows 
      camera={{ position: [0, 15, 20], fov: 45 }}
      className="bg-[#f8fafc]"
    >
      <ambientLight intensity={0.5} />
      <directionalLight 
        castShadow 
        position={[10, 20, 10]} 
        intensity={1.5} 
        shadow-mapSize={[1024, 1024]}
      >
        <orthographicCamera attach="shadow-camera" args={[-20, 20, 20, -20]} />
      </directionalLight>

      <Sky sunPosition={[10, 20, 10]} turbidity={0.1} rayleigh={0.5} inclination={0.6} distance={1000} />

      <Suspense fallback={null}>
        {state.level === 'Campus' || state.level === 'Building' ? (
          <CampusLayout />
        ) : (
          <FloorView />
        )}

        <ContactShadows 
          resolution={1024} 
          scale={100} 
          blur={1.5} 
          opacity={0.5} 
          far={20} 
          color="#0f172a" 
        />
      </Suspense>

      <OrbitControls 
        makeDefault 
        minPolarAngle={0} 
        maxPolarAngle={Math.PI / 2.1} 
        maxDistance={50}
        minDistance={5}
      />
    </Canvas>
  );
}
