'use client';
import { use } from 'react';
import { ACDetailView } from '@/components/features/assets/ACDetailView';

export default function ViewerACDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ACDetailView acId={id} backHref="/viewer/search" />;
}
