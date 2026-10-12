// src/components/features/3d/AC3DViewer.tsx
'use client';

import React, { Suspense, useRef, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, useGLTF, Environment, ContactShadows } from '@react-three/drei';
import { RotateCcw, Maximize2 } from 'lucide-react';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';

function ACModel({ url }: { url: string }) {
  // useDraco=false, useMeshopt=false: our GLB uses neither extension, and both
  // defaults are incompatible with the app's CSP — Draco wires a gstatic.com
  // decoder URL and Meshopt eagerly runs WebAssembly.instantiate() (blocked by
  // script-src without 'wasm-unsafe-eval'). See docs/security-review.md.
  const { scene } = useGLTF(url, false, false);
  const cloned = React.useMemo(() => {
    const c = scene.clone(true);
    // Auto-center and scale
    const box = new THREE.Box3().setFromObject(c);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const scale = maxDim > 0 ? 2.5 / maxDim : 1;
    c.scale.setScalar(scale);
    c.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
    return c;
  }, [scene]);

  return <primitive object={cloned} />;
}

function LoadingFallback() {
  return (
    <mesh>
      <boxGeometry args={[1, 1.5, 0.3]} />
      <meshStandardMaterial color="#3B82F6" wireframe />
    </mesh>
  );
}

interface AC3DViewerProps {
  modelUrl?: string;
  className?: string;
  height?: number;
}

export const AC3DViewer = React.memo(function AC3DViewer({
  modelUrl = '/model/ac_model3.glb',
  className = '',
  height = 380,
}: AC3DViewerProps) {
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const [autoRotate, setAutoRotate] = useState(true);

  const resetCamera = () => {
    if (controlsRef.current) {
      controlsRef.current.reset();
    }
  };

  return (
    <div className={`relative rounded-2xl overflow-hidden bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 border border-slate-700 ${className}`} style={{ height }}>
      <Canvas
        camera={{ position: [0, 1, 4], fov: 45 }}
        gl={{ antialias: true, alpha: true }}
        shadows
      >
        <ambientLight intensity={0.6} />
        <directionalLight position={[5, 8, 5]} intensity={1.2} castShadow />
        <directionalLight position={[-5, 2, -5]} intensity={0.4} color="#bfdbfe" />
        <pointLight position={[0, 5, 0]} intensity={0.3} color="#dbeafe" />

        <Suspense fallback={<LoadingFallback />}>
          <ACModel url={modelUrl} />
          <ContactShadows position={[0, -1.4, 0]} opacity={0.4} scale={6} blur={2} />
          {/* Self-hosted HDR: drei's preset="city" fetched from raw.githack.com,
              which CSP connect-src blocks (fatal inside Suspense). */}
          <Environment files="/hdri/potsdamer_platz_512.hdr" />
        </Suspense>

        <OrbitControls
          ref={controlsRef}
          autoRotate={autoRotate}
          autoRotateSpeed={1.2}
          enableDamping
          dampingFactor={0.08}
          minDistance={1.5}
          maxDistance={8}
          minPolarAngle={0.2}
          maxPolarAngle={Math.PI * 0.82}
        />
      </Canvas>

      {/* Overlay controls */}
      <div className="absolute top-3 right-3 flex flex-col gap-1.5">
        <button
          onClick={() => setAutoRotate(v => !v)}
          title={autoRotate ? 'Pause rotation' : 'Resume rotation'}
          className={`p-2 rounded-lg text-xs font-medium shadow backdrop-blur-sm transition-colors ${
            autoRotate
              ? 'bg-blue-600/80 text-white hover:bg-blue-600'
              : 'bg-slate-700/80 text-slate-300 hover:bg-slate-600/80'
          }`}
        >
          <RotateCcw size={14} />
        </button>
        <button
          onClick={resetCamera}
          title="Reset camera"
          className="p-2 rounded-lg bg-slate-700/80 text-slate-300 hover:bg-slate-600/80 shadow backdrop-blur-sm transition-colors"
        >
          <Maximize2 size={14} />
        </button>
      </div>

      {/* Hint label */}
      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-full bg-slate-900/70 backdrop-blur-sm text-xs text-slate-400 border border-slate-700 pointer-events-none select-none">
        🖱️ Drag to rotate · Scroll to zoom
      </div>
    </div>
  );
});
