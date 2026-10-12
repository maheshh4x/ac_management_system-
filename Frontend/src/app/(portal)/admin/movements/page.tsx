import { redirect } from 'next/navigation';

export default function MovementsRedirect() {
  redirect('/admin/maintenance?tab=movement');
}
