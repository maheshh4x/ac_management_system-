import { redirect } from 'next/navigation';

export default function MovementsRedirect() {
  redirect('/manager/maintenance?tab=movement');
}
