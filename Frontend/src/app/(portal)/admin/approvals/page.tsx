import ApprovalQueue from '@/components/features/ApprovalQueue';
import { MovementApprovals } from '@/components/features/MovementApprovals';

export default function AdminApprovalsPage() {
  return (
    <>
      <ApprovalQueue />
      <MovementApprovals />
    </>
  );
}
