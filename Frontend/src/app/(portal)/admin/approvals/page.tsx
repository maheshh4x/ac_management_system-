import ApprovalQueue from '@/components/features/ApprovalQueue';
import { MovementApprovals } from '@/components/features/MovementApprovals';
import { IssueApprovals } from '@/components/features/issues/IssueApprovals';

export default function AdminApprovalsPage() {
  return (
    <>
      <ApprovalQueue />
      <MovementApprovals />
      <IssueApprovals />
    </>
  );
}
