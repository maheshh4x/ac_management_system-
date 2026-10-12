import ApprovalQueue from '@/components/features/ApprovalQueue';
import { MovementApprovals } from '@/components/features/MovementApprovals';

export default function ManagerApprovalsPage() {
  return (
    <>
      <ApprovalQueue />
      <MovementApprovals />
    </>
  );
}
