'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Search, MapPin, Airplay, Building2, Loader2 } from 'lucide-react';
import { searchAssets } from '@/lib/campus-services';
import { useRouter } from 'next/navigation';
import { useCampus } from '@/contexts/CampusContext';
import { useAuth } from '@/contexts/AuthContext';

export function GlobalSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<{ acs: any[], rooms: any[], buildings: any[] }>({ acs: [], rooms: [], buildings: [] });
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const { setState } = useCampus();
  const { user } = useAuth();

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSearch = async (val: string) => {
    setQuery(val);
    if (val.length < 2) {
      setResults({ acs: [], rooms: [], buildings: [] });
      setIsOpen(false);
      return;
    }
    
    setIsLoading(true);
    setIsOpen(true);
    const res = await searchAssets(val);
    setResults(res);
    setIsLoading(false);
  };

  const handleSelect = (type: 'ac' | 'room' | 'building', item: any) => {
    setIsOpen(false);
    setQuery('');
    
    // Jump to 3D Map View
    router.push('/campus');
    
    // Dispatch selected context so 3D map handles it
    setTimeout(() => {
      if (type === 'ac') {
        setState({
          level: 'AC',
          selectedBuildingId: item.location.buildingId,
          selectedFloorId: item.location.floorId,
          selectedRoomId: item.location.roomId,
          selectedAcId: item.id
        });
      } else if (type === 'room') {
        setState({
           level: 'Room',
           selectedBuildingId: item.id.split('-').slice(0,2).join('-'),
           selectedFloorId: item.floorId,
           selectedRoomId: item.id,
           selectedAcId: null
        });
      } else if (type === 'building') {
        setState({
           level: 'Building',
           selectedBuildingId: item.id,
           selectedFloorId: null,
           selectedRoomId: null,
           selectedAcId: null
        });
      }
    }, 500); // small delay to let page mount if coming from another path
  };

  return (
    <div ref={wrapperRef} className="relative w-full shadow-sm rounded-lg border border-slate-200 bg-slate-50 focus-within:ring-2 focus-within:ring-blue-500 focus-within:bg-white transition-all flex items-center px-4">
      <Search className="w-5 h-5 text-slate-400 mr-2 flex-shrink-0" />
      <input
        type="text"
        className="w-full bg-transparent border-none py-2.5 outline-none text-sm text-slate-700 placeholder:text-slate-500"
        placeholder={user?.role === 'TECHNICIAN' ? "Search AC ID, QR Code Data..." : "Search ACs, Rooms, Departments..."}
        value={query}
        onChange={(e) => handleSearch(e.target.value)}
        onFocus={() => query.length >= 2 && setIsOpen(true)}
      />
      {isLoading && <Loader2 className="w-4 h-4 text-slate-400 animate-spin absolute right-4" />}

      {isOpen && (results.acs.length > 0 || results.rooms.length > 0 || results.buildings.length > 0) && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-white rounded-xl shadow-2xl border border-slate-200 overflow-hidden z-50">
          <div className="max-h-96 overflow-y-auto p-2">
            
            {results.acs.length > 0 && (
              <div className="mb-2 last:mb-0">
                <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider px-3 py-2">AC Units</div>
                {results.acs.map(ac => (
                  <button key={ac.id} onClick={() => handleSelect('ac', ac)} className="w-full text-left flex items-start p-3 hover:bg-slate-50 rounded-lg transition-colors gap-3 group">
                    <div className="mt-0.5 bg-blue-100 text-blue-600 p-1.5 rounded-md group-hover:bg-blue-600 group-hover:text-white transition-colors">
                      <Airplay size={16} />
                    </div>
                    <div>
                      <div className="font-semibold text-sm text-slate-800">{ac.make} {ac.model} <span className="text-slate-400 font-normal">({ac.id})</span></div>
                      <div className="text-xs text-slate-500 mt-0.5">Loc: {ac.location.roomId} • Status: {ac.status}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}

            {results.rooms.length > 0 && (
              <div className="mb-2 last:mb-0">
                <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider px-3 py-2">Rooms & Labs</div>
                {results.rooms.map(room => (
                  <button key={room.id} onClick={() => handleSelect('room', room)} className="w-full text-left flex items-start p-3 hover:bg-slate-50 rounded-lg transition-colors gap-3 group">
                    <div className="mt-0.5 bg-emerald-100 text-emerald-600 p-1.5 rounded-md group-hover:bg-emerald-600 group-hover:text-white transition-colors">
                      <MapPin size={16} />
                    </div>
                    <div>
                      <div className="font-semibold text-sm text-slate-800">{room.name} <span className="text-slate-400 font-normal">({room.id})</span></div>
                      <div className="text-xs text-slate-500 mt-0.5">Type: {room.type}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}
            
            {results.buildings.length > 0 && (
              <div className="mb-2 last:mb-0">
                <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider px-3 py-2">Buildings</div>
                {results.buildings.map(b => (
                  <button key={b.id} onClick={() => handleSelect('building', b)} className="w-full text-left flex items-start p-3 hover:bg-slate-50 rounded-lg transition-colors gap-3 group">
                    <div className="mt-0.5 bg-purple-100 text-purple-600 p-1.5 rounded-md group-hover:bg-purple-600 group-hover:text-white transition-colors">
                      <Building2 size={16} />
                    </div>
                    <div>
                      <div className="font-semibold text-sm text-slate-800">{b.name}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{b.type}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}

          </div>
        </div>
      )}
    </div>
  );
}
