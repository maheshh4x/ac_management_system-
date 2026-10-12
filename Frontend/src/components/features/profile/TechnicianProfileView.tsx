'use client';

import { useState } from 'react';
import { BriefcaseBusiness, CheckCircle2, Edit3, KeyRound, Lock, Mail, MapPin, Phone, Save, ShieldCheck, UserRound } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

export function TechnicianProfileView() {
  const { user, updateSession } = useAuth();
  const [editing, setEditing] = useState(false);
  const [phone, setPhone] = useState(user?.phone ?? '');
  const [address, setAddress] = useState(user?.address ?? '');
  const [message, setMessage] = useState('');
  const [requestMessage, setRequestMessage] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordMessage, setPasswordMessage] = useState('');
  const [passwordError, setPasswordError] = useState('');

  const assignedBlocks = user?.assignedBlocks ?? [];
  if (!user) return null;

  const saveContact = async () => {
    // PATCH /api/v1/me Ã¢â‚¬â€ whitelisted contact fields only (Phase 1).
    await updateSession({ phone: phone.trim(), address: address.trim() });
    setEditing(false); setMessage('Contact details updated.');
  };

  const requestProfileUpdate = () => {
    if (!requestMessage.trim()) return;
    // Approver notifications move to the backend in Phase 7 Ã¢â‚¬â€ no client-side
    // notification store exists anymore.
    setRequestMessage('');
    setMessage('Profile update requests are sent by contacting your manager; approval happens in the admin console.');
  };

  const changePassword = async () => {
    setPasswordError(''); setPasswordMessage('');
    if (!currentPassword) return setPasswordError('Please enter your current password.');
    if (newPassword.length < 8) return setPasswordError('New password must be at least 8 characters.');
    if (newPassword !== confirmPassword) return setPasswordError('New passwords do not match.');
    const supabase = getSupabaseBrowserClient();
    const { error: verifyError } = await supabase.auth.signInWithPassword({ email: user.email, password: currentPassword });
    if (verifyError) return setPasswordError('Current password is incorrect.');
    const { error } = await supabase.auth.updateUser({ password: newPassword });
    if (error) return setPasswordError(error.message);
    setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); setPasswordMessage('Password changed successfully.');
  };

  return <div className="mx-auto flex max-w-3xl flex-col gap-5 pb-10"><div><h1 className="text-2xl font-bold text-slate-900">My Profile</h1><p className="mt-1 text-sm text-slate-500">Your approved technician account details and security settings.</p></div><section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div className="flex flex-wrap items-center gap-4"><div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-amber-100 text-xl font-bold text-amber-700">{user.name.slice(0, 2).toUpperCase()}</div><div><h2 className="text-xl font-bold text-slate-900">{user.name}</h2><p className="mt-1 flex items-center gap-1.5 text-sm text-slate-600"><ShieldCheck size={15} className="text-emerald-600" />Approved technician</p></div><span className="ml-auto rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700">{user.status ?? 'approved'}</span></div></section><section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div className="mb-4 flex items-center justify-between"><h2 className="flex items-center gap-2 font-bold text-slate-900"><UserRound size={17} className="text-blue-600" />Account details</h2>{!editing && <button onClick={() => setEditing(true)} className="flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 px-3 text-sm font-semibold text-slate-700"><Edit3 size={15} />Edit contact</button>}</div><div className="grid grid-cols-1 gap-4 sm:grid-cols-2"><ReadOnly label="Full name" value={user.name} icon={<UserRound size={14} />} /><ReadOnly label="Email" value={user.email} icon={<Mail size={14} />} /><ReadOnly label="Staff ID" value={user.staffId ?? 'Not recorded'} icon={<KeyRound size={14} />} /><ReadOnly label="Designation" value={user.designation ?? 'Not recorded'} icon={<BriefcaseBusiness size={14} />} /><ReadOnly label="Assigned block(s)" value={assignedBlocks.length ? assignedBlocks.join(', ') : 'Not assigned'} icon={<MapPin size={14} />} /><ReadOnly label="Reporting manager" value={user.reportingManagerId ? 'Assigned by admin' : 'Assigned after approval'} icon={<UserRound size={14} />} /><ReadOnly label="Years of experience" value={user.yearsOfExperience?.toString() ?? 'Not recorded'} /><ReadOnly label="Certification / license" value={user.certificationNumber ?? 'Not recorded'} /><div><label className="text-xs font-bold uppercase text-slate-400">Phone</label>{editing ? <input type="tel" value={phone} onChange={event => setPhone(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-blue-300 px-3 text-sm" /> : <p className="mt-1 flex min-h-11 items-center gap-2 rounded-xl border border-slate-100 bg-slate-50 px-3 text-sm font-semibold"><Phone size={14} className="text-slate-400" />{phone || 'Not recorded'}</p>}</div><div><label className="text-xs font-bold uppercase text-slate-400">Department</label><p className="mt-1 flex min-h-11 items-center rounded-xl border border-slate-100 bg-slate-50 px-3 text-sm font-semibold">{user.department || 'Not recorded'}</p></div><div className="sm:col-span-2"><label className="text-xs font-bold uppercase text-slate-400">Address</label>{editing ? <input value={address} onChange={event => setAddress(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-blue-300 px-3 text-sm" /> : <p className="mt-1 min-h-11 rounded-xl border border-slate-100 bg-slate-50 px-3 py-3 text-sm font-semibold">{address || 'Not recorded'}</p>}</div></div>{editing && <div className="mt-4 flex gap-2"><button onClick={saveContact} className="flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white"><Save size={15} />Save changes</button><button onClick={() => setEditing(false)} className="min-h-11 rounded-xl border border-slate-200 px-4 text-sm font-semibold text-slate-700">Cancel</button></div>}{message && <p className="mt-3 flex items-center gap-2 text-sm font-semibold text-emerald-700"><CheckCircle2 size={15} />{message}</p>}</section><section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><h2 className="flex items-center gap-2 font-bold text-slate-900"><ShieldCheck size={17} className="text-blue-600" />Request restricted profile changes</h2><p className="mt-1 text-sm text-slate-500">Staff ID, designation, assigned blocks, and certification changes require approval.</p><textarea value={requestMessage} onChange={event => setRequestMessage(event.target.value)} rows={3} placeholder="Describe the change you need" className="mt-4 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm" /><button onClick={requestProfileUpdate} className="mt-3 min-h-11 rounded-xl bg-slate-900 px-4 text-sm font-semibold text-white">Request profile update</button></section><section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><h2 className="flex items-center gap-2 font-bold text-slate-900"><Lock size={17} className="text-blue-600" />Change password</h2><div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3"><input type="password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} placeholder="Current password" className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm" /><input type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} placeholder="New password" className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm" /><input type="password" value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} placeholder="Confirm password" className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm" /></div>{passwordError && <p className="mt-3 text-sm font-semibold text-red-600">{passwordError}</p>}{passwordMessage && <p className="mt-3 text-sm font-semibold text-emerald-700">{passwordMessage}</p>}<button onClick={changePassword} className="mt-4 min-h-11 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white">Change password</button></section></div>;
}

function ReadOnly({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) { return <div><label className="text-xs font-bold uppercase text-slate-400">{label}</label><p className="mt-1 flex min-h-11 items-center gap-2 rounded-xl border border-slate-100 bg-slate-50 px-3 text-sm font-semibold text-slate-800">{icon}{value}</p></div>; }
