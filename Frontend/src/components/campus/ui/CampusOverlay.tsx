'use client';

import React, { useEffect, useState } from 'react';
import { useCampus } from '@/contexts/CampusContext';
import { Building, Floor, AC } from '@/lib/types/campus';
import { getBuilding, getFloors } from '@/lib/campus-services';
import { useAssetFacets } from '@/lib/api/assets';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Map, Building2, Layers, Grid, Airplay, ChevronRight, Search } from 'lucide-react';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';

export default function CampusOverlay() {
  const { state, setState, resetNavigation } = useCampus();
  const [activeBuilding, setActiveBuilding] = useState<Building | null>(null);
  const [floors, setFloors] = useState<Floor[]>([]);
  const { data: facets } = useAssetFacets();

  useEffect(() => {
    if (state.selectedBuildingId) {
      getBuilding(state.selectedBuildingId).then(b => setActiveBuilding(b || null));
      getFloors(state.selectedBuildingId)
        .then(f => setFloors(f))
        .catch(() => setFloors([]));
    } else {
      setActiveBuilding(null);
      setFloors([]);
    }
  }, [state.selectedBuildingId]);

  return (
    <div className="absolute inset-0 pointer-events-none flex flex-col justify-between p-4 z-10 overflow-hidden">
      
      {/* Top Bar - Breadcrumbs & Search */}
      <div className="flex justify-between items-start gap-4">
        {/* Breadcrumbs */}
        <div className="pointer-events-auto bg-white/90 backdrop-blur-md rounded-lg shadow-sm border border-slate-200 px-4 py-3 flex items-center space-x-2 text-sm text-slate-600">
          <button onClick={resetNavigation} className="hover:text-blue-600 font-medium flex items-center gap-1 transition-colors">
            <Map className="w-4 h-4" /> Campus
          </button>
          
          {state.selectedBuildingId && activeBuilding && (
            <>
              <ChevronRight className="w-4 h-4 text-slate-400" />
              <button 
                onClick={() => setState(prev => ({ ...prev, level: 'Building', selectedFloorId: null, selectedRoomId: null, selectedAcId: null }))}
                className={`flex items-center gap-1 transition-colors ${state.level === 'Building' ? 'text-blue-600 font-semibold' : 'hover:text-blue-600'}`}
              >
                <Building2 className="w-4 h-4" /> {activeBuilding.name}
              </button>
            </>
          )}

          {state.selectedFloorId && (
            <>
              <ChevronRight className="w-4 h-4 text-slate-400" />
              <button 
                onClick={() => setState(prev => ({ ...prev, level: 'Floor', selectedRoomId: null, selectedAcId: null }))}
                className={`flex items-center gap-1 transition-colors ${state.level === 'Floor' ? 'text-blue-600 font-semibold' : 'hover:text-blue-600'}`}
              >
                <Layers className="w-4 h-4" /> {/* Map mock Floor ID to simple text for now */}
                {state.selectedFloorId.split('-').slice(-2).join(' ')}
              </button>
            </>
          )}

          {state.selectedRoomId && (
            <>
              <ChevronRight className="w-4 h-4 text-slate-400" />
              <button 
                onClick={() => setState(prev => ({ ...prev, level: 'Room', selectedAcId: null }))}
                className={`flex items-center gap-1 transition-colors ${state.level === 'Room' ? 'text-blue-600 font-semibold' : 'hover:text-blue-600'}`}
              >
                <Grid className="w-4 h-4" /> {state.selectedRoomId}
              </button>
            </>
          )}

          {state.selectedAcId && (
            <>
              <ChevronRight className="w-4 h-4 text-slate-400" />
              <span className="text-blue-600 font-semibold flex items-center gap-1">
                <Airplay className="w-4 h-4" /> {state.selectedAcId}
              </span>
            </>
          )}
        </div>

        {/* Global Search Bar mockup */}
        <div className="pointer-events-auto bg-white/90 backdrop-blur-md rounded-lg shadow-sm border border-slate-200 px-4 py-2 flex items-center w-64 md:w-80 transition-all focus-within:ring-2 focus-within:ring-blue-500">
          <Search className="w-4 h-4 text-slate-400 mr-2" />
          <input 
            type="text" 
            placeholder="Search AC-127, Admin Block..."
            className="bg-transparent border-none outline-none w-full text-sm text-slate-700" 
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const query = e.currentTarget.value;
                import('@/lib/campus-services').then(mod => {
                  mod.searchAssets(query).then(res => {
                    if (res.acs.length > 0) {
                      const ac = res.acs[0];
                      setState({
                        level: 'AC',
                        selectedBuildingId: ac.location.buildingId,
                        selectedFloorId: ac.location.floorId,
                        selectedRoomId: ac.location.roomId,
                        selectedAcId: ac.id
                      });
                    } else if (res.buildings.length > 0) {
                      setState({
                        level: 'Building',
                        selectedBuildingId: res.buildings[0].id,
                        selectedFloorId: null,
                        selectedRoomId: null,
                        selectedAcId: null
                      });
                    } else if (res.rooms.length > 0) {
                      const room = res.rooms[0];
                      setState({
                         level: 'Room',
                         selectedBuildingId: room.id.split('-').slice(0, 2).join('-'), // naive building id extraction
                         selectedFloorId: room.floorId,
                         selectedRoomId: room.id,
                         selectedAcId: null
                      })
                    }
                  });
                });
              }
            }}
          />
        </div>
      </div>

      {/* Slide-out Sidebar Panel */}
      <AnimatePresence>
        {state.level !== 'Campus' && (
          <motion.div
            initial={{ x: '100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '100%', opacity: 0 }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="absolute top-0 right-0 w-80 h-full bg-white shadow-2xl border-l border-slate-200 pointer-events-auto overflow-y-auto"
          >
            {/* Header */}
            <div className="sticky top-0 bg-white/80 backdrop-blur-md border-b border-slate-100 p-4 flex items-center justify-between z-10">
              <h2 className="font-semibold text-lg text-slate-900">
                {state.level === 'Building' && activeBuilding?.name}
                {state.level === 'Floor' && `Floor Details`}
                {state.level === 'Room' && `Room ${state.selectedRoomId}`}
                {state.level === 'AC' && `Asset ${state.selectedAcId}`}
              </h2>
              <button 
                onClick={resetNavigation} 
                className="p-1 rounded-md hover:bg-slate-100 text-slate-500 transition-colors"
                title="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content Body */}
            <div className="p-4 space-y-6">
              {state.level === 'Building' && activeBuilding && (
                <>
                  <div className="space-y-1">
                    <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Building Info</p>
                    <div className="flex justify-between items-end border-b border-slate-100 pb-2">
                       <span className="text-sm text-slate-600">ID</span>
                       <span className="font-mono font-medium text-slate-900">{activeBuilding.id}</span>
                    </div>
                    <div className="flex justify-between items-end border-b border-slate-100 pb-2 pt-2">
                       <span className="text-sm text-slate-600">Floors</span>
                       <span className="font-medium text-slate-900">{floors.length}</span>
                    </div>
                    <div className="flex justify-between items-end pb-2 pt-2">
                       <span className="text-sm text-slate-600">Total ACs</span>
                       <span className="font-medium text-slate-900">
                         {facets
                           ? (facets.block.find(entry => entry.value === activeBuilding.name)?.count ?? 0)
                           : '—'}
                       </span>
                    </div>
                  </div>
                  
                  <button 
                    onClick={() => setState(prev => ({ ...prev, level: 'Floor', selectedFloorId: floors[0]?.id ?? null }))}
                    className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg shadow-sm transition-colors flex items-center justify-center gap-2"
                  >
                    Enter Building <ChevronRight className="w-4 h-4" />
                  </button>
                </>
              )}

              {state.level === 'Floor' && (
                <>
                  <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 text-center">
                    <p className="text-sm text-blue-800">Interior Floor View</p>
                    <p className="text-xs text-blue-600 mt-1">Select a room from the 3D map to view its layout and AC assets.</p>
                  </div>
                </>
              )}

              {state.level === 'AC' && (
                <ACDetailsPanel acId={state.selectedAcId} />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ACDetailsPanel({ acId }: { acId: string | null }) {
  const [ac, setAc] = React.useState<AC | null>(null);

  React.useEffect(() => {
    if (acId) {
      import('@/lib/campus-services').then(mod => {
        mod.getAC(acId).then(data => setAc(data || null));
      });
    }
  }, [acId]);

  if (!ac) return <div className="text-sm text-slate-500 text-center py-4">Loading AC Details...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-xl font-bold text-slate-900">{ac.make} {ac.model}</h3>
        <p className="text-sm text-slate-500">{ac.capacity} - {ac.type}</p>
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Status</p>
        <div className="flex items-center gap-2">
          <span className={`w-3 h-3 rounded-full ${ac.status === 'Working' ? 'bg-green-500' : ac.status === 'Fault' ? 'bg-red-500' : ac.status === 'Maintenance' ? 'bg-amber-500' : 'bg-slate-400'}`}></span>
          <span className="font-medium text-slate-900">{ac.status}</span>
        </div>
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Details</p>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between border-b border-slate-100 pb-1"><span className="text-slate-500">Serial No.</span><span className="font-medium text-slate-900">{ac.serialNumber}</span></div>
          <div className="flex justify-between border-b border-slate-100 pb-1"><span className="text-slate-500">Mfg. Year</span><span className="font-medium text-slate-900">{ac.manufacturingYear}</span></div>
          <div className="flex justify-between border-b border-slate-100 pb-1"><span className="text-slate-500">Inst. Year</span><span className="font-medium text-slate-900">{ac.installationYear}</span></div>
          <div className="flex justify-between border-b border-slate-100 pb-1"><span className="text-slate-500">Star Rating</span><span className="font-medium text-slate-900">{ac.starRating}⭐</span></div>
          <div className="flex justify-between pb-1"><span className="text-slate-500">Power Rating</span><span className="font-medium text-slate-900">{ac.powerRating}</span></div>
        </div>
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Maintenance</p>
        <div className="bg-slate-50 p-3 rounded-lg border border-slate-100 text-sm space-y-2">
          <div className="flex justify-between"><span className="text-slate-500">Last Type</span><span className="font-medium">{ac.lastMaintenanceType}</span></div>
          <div className="flex justify-between"><span className="text-slate-500">Last Date</span><span className="font-medium">{ac.lastMaintenanceDate}</span></div>
        </div>
      </div>

      <div className="space-y-2 pt-4">
        <button className="w-full bg-slate-900 hover:bg-slate-800 text-white font-medium py-2 rounded-lg transition-colors text-sm">
          View Full Details
        </button>
        <ProtectedAction permission="EDIT_AC">
          <button className="w-full bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 font-medium py-2 rounded-lg transition-colors text-sm">
            Update Info
          </button>
        </ProtectedAction>
      </div>
    </div>
  );
}
