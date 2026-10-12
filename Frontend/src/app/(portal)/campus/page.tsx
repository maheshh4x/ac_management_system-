'use client';

import React, { useEffect, useRef, useState } from 'react';
import { CampusProvider } from '@/contexts/CampusContext';
import CampusOverlay from '@/components/campus/ui/CampusOverlay';
import LayoutEditorPanel from '@/components/campus/ui/LayoutEditorPanel';
import { useAssetFacets } from '@/lib/api/assets';

export default function CampusPage() {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [mapReady, setMapReady] = useState(false);
  const { data: facets } = useAssetFacets();

  // The static 3D map cannot authenticate itself, so the host page (which
  // holds the API session) forwards per-block stats over postMessage.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if ((event.data as { type?: string } | null)?.type === 'acms:map-ready') setMapReady(true);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  useEffect(() => {
    if (!mapReady || !facets) return;
    frameRef.current?.contentWindow?.postMessage(
      {
        type: 'acms:stats',
        blocks: facets.block.map(entry => ({ name: entry.value, acs: entry.count })),
      },
      window.location.origin,
    );
  }, [mapReady, facets]);

  return (
    <CampusProvider>
      <div className="relative w-full h-[calc(100vh-140px)] rounded-xl overflow-hidden shadow-lg border border-border bg-slate-900 group">
        <iframe
          ref={frameRef}
          src="/campus_map.html"
          title="3D Campus Map"
          style={{ width: "100%", height: "100%", border: 0 }}
        />
        <CampusOverlay />
        {/* MANAGE_CAMPUS: edits block positions and saves them through
            PATCH /api/v1/campus/blocks/<id>/layout (Phase 3 task 5). */}
        <div className="absolute bottom-4 right-4 z-20">
          <LayoutEditorPanel />
        </div>
      </div>
    </CampusProvider>
  );
}
