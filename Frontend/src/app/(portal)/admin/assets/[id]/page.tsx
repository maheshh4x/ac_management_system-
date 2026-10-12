'use client';
import { use } from 'react';
import { ACDetailView } from '@/components/features/assets/ACDetailView';

export default function AdminACDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ACDetailView acId={id} backHref="/admin/assets" />;
}
