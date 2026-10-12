// src/lib/types/campus.ts

export type AcStatus = 'Working' | 'Fault' | 'Maintenance' | 'Unknown' | 'Offline';

export type BuildingStatus = 'Active' | 'Inactive' | 'Under Maintenance';

export interface BuildingPhoto {
  id: string;
  name: string;
  dataUrl: string;
  isCover: boolean;
}

export interface LocationData {
  buildingId: string;
  floorId: string;
  departmentId?: string;
  roomId: string;
}

export interface AC {
  id: string;
  make: string;
  model: string;
  capacity: string;
  serialNumber: string;
  type: string;
  manufacturingYear?: string;
  installationYear?: string;
  starRating?: string;
  powerRating?: string;
  powerFactor?: string;
  status: AcStatus;
  lastMaintenanceType?: string;
  lastMaintenanceDate?: string;
  location: LocationData;
}

export interface Room {
  id: string;
  name: string;
  type: string; // e.g. "Lab", "Classroom", "Office"
  floorId: string; // Back reference for easier tracing
  order?: number;
}

export interface Department {
  id: string;
  name: string;
  floorId: string;
}

export interface Floor {
  id: string;
  name: string;
  buildingId: string;
  order?: number;
}

export interface Building {
  id: string;
  name: string;
  type: string;
  description?: string;
  status?: BuildingStatus;
  photos?: BuildingPhoto[];
  layout?: { x: number; y: number; w: number; h: number; color?: string }; // Layout for 3D map mapping relative positions
}
