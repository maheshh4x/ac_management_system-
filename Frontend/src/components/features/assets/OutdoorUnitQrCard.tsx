'use client';

/**
 * Outdoor Unit QR card (Feature A): the second, visually distinct QR code on
 * the asset detail page. It encodes the linked OUTDOOR row's own stable
 * identity (`/a/<outdoor-uuid>`) — never a copy of the indoor code.
 *
 * States: linked (QR + downloads + related-system/location lines),
 * not-linked (explicit state, no fabricated QR; editors get a path to the
 * edit form), and outdoor-row context (when this page itself IS the outdoor
 * unit, the card points back at the indoor parent instead).
 */
import React from 'react';
import { Boxes, Link2, QrCode, Unlink } from 'lucide-react';

import { AcQrActions } from '@/components/features/qr/AcQrActions';
import { effectiveLocationText } from '@/lib/adapters/asset';
import type { AssetDetail } from '@/lib/api/assets';
import type { AcStatus } from '@/lib/types/campus';
import type { ExtendedAC } from '@/lib/types/asset';

function toOutdoorStatus(operational: string | null | undefined): AcStatus {
  switch ((operational ?? '').trim().toUpperCase()) {
    case 'WORKING':
      return 'Working';
    case 'FAULT':
      return 'Fault';
    case 'MAINTENANCE':
      return 'Maintenance';
    case 'OFFLINE':
      return 'Offline';
    default:
      return 'Unknown';
  }
}

export function OutdoorUnitQrCard({
  ac,
  canEdit,
  onLink,
}: {
  ac: AssetDetail;
  canEdit: boolean;
  onLink: () => void;
}) {
  const isOutdoorRow = (ac.unitRole ?? 'INDOOR') === 'OUTDOOR';

  // This page IS the outdoor unit: show the indoor parent instead of a
  // self-link, with the parent's QR reachable from the parent's own page.
  if (isOutdoorRow) {
    const parent = ac.parentUnit;
    return (
      <div className="bg-white rounded-2xl border border-sky-200 shadow-sm p-5">
        <h3 className="text-xs font-bold uppercase text-sky-600 mb-3 flex items-center gap-1.5">
          <Boxes size={13} /> Outdoor Unit
        </h3>
        {parent ? (
          <div className="text-sm text-slate-600">
            <p>
              <span className="font-semibold text-slate-800">Linked indoor system:</span>{' '}
              {parent.asset_code ?? parent.ac_id}
              {parent.ac_make_company ? ` · ${parent.ac_make_company}` : ''}
              {parent.ac_model_number ? ` ${parent.ac_model_number}` : ''}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Location: {effectiveLocationText(ac, 'block')} / {effectiveLocationText(ac, 'floor')} /{' '}
              {effectiveLocationText(ac, 'room')}
            </p>
          </div>
        ) : (
          <p className="text-sm text-slate-500">This outdoor unit is not linked to an indoor system.</p>
        )}
      </div>
    );
  }

  const ou = ac.outdoorUnit;
  if (!ou?.asset_uuid) {
    return (
      <div className="bg-white rounded-2xl border border-dashed border-slate-300 shadow-sm p-5">
        <h3 className="text-xs font-bold uppercase text-slate-400 mb-3 flex items-center gap-1.5">
          <QrCode size={13} /> Outdoor Unit
        </h3>
        <p className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Unlink size={15} className="text-slate-400" /> Outdoor Unit Not Linked
        </p>
        <p className="mt-1 text-xs text-slate-500">
          No outdoor unit is linked to this system yet, so no QR is shown — a code here would falsely claim an
          identity.
        </p>
        {canEdit && (
          <button
            onClick={onLink}
            className="mt-3 inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
          >
            <Link2 size={13} /> Link via Edit Unit
          </button>
        )}
      </div>
    );
  }

  // Label-only asset view for the branded download: the outdoor row's own
  // make/model/serial (never the indoor's) at the shared site location.
  const outdoorAsset: ExtendedAC = {
    id: ou.asset_uuid,
    acId: ou.ac_id,
    uuid: ou.asset_uuid,
    make: ou.ac_make_company ?? '',
    model: ou.ac_model_number ?? '',
    capacity: '',
    serialNumber: ou.ac_serial_number ?? '',
    type: 'Outdoor Unit',
    status: toOutdoorStatus(ou.operational_status),
    operationalStatus: ou.operational_status ?? undefined,
    location: ac.location,
    buildingName: ac.buildingName,
    floorName: ac.floorName,
    roomName: ac.roomName,
    roomType: ac.roomType,
    departmentName: ac.departmentName,
  };

  return (
    <div className="bg-white rounded-2xl border-2 border-sky-200 shadow-sm p-5">
      <h3 className="text-xs font-bold uppercase text-sky-600 mb-1 flex items-center gap-1.5">
        <QrCode size={13} /> Outdoor Unit
      </h3>
      <p className="mb-1 text-xs text-slate-500">
        Related system: <span className="font-semibold text-slate-700">{ac.visualId ?? ac.acId ?? ac.id}</span>
        {' · '}Location: {effectiveLocationText(ac, 'block')} / {effectiveLocationText(ac, 'floor')} /{' '}
        {effectiveLocationText(ac, 'room')}
      </p>
      <AcQrActions acId={ou.asset_uuid} asset={outdoorAsset} />
    </div>
  );
}
