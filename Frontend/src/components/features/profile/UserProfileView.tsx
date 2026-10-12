// src/components/features/profile/UserProfileView.tsx
'use client';

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  User, Mail, Shield, Edit3, Save, X, Lock, CheckCircle2,
  Eye, EyeOff, ShieldCheck, LogOut
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { canEditProfile } from '@/lib/permissions';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

const ROLE_LABELS: Record<string, string> = {
  SUPER_ADMIN:      'System Administrator',
  FACILITY_MANAGER: 'Faculty Manager',
  TECHNICIAN:       'Maintenance Technician',
  VIEWER:           'Viewer (Read-Only)',
};

const ROLE_COLORS: Record<string, string> = {
  SUPER_ADMIN:      'bg-blue-600',
  FACILITY_MANAGER: 'bg-emerald-600',
  TECHNICIAN:       'bg-amber-500',
  VIEWER:           'bg-slate-500',
};

const ROLE_PERMISSIONS: Record<string, string[]> = {
  SUPER_ADMIN:      ['View all data', 'Import & manage AC assets', 'Full maintenance management', 'User & role management', 'View reports & analytics', 'Audit log access'],
  FACILITY_MANAGER: ['View all data', 'Approve/reject movement requests', 'Manage maintenance jobs', 'View reports & analytics', 'Manage users (limited)'],
  TECHNICIAN:       ['View assigned AC units', 'Upload job photos & reports', 'Update maintenance job status', 'Submit movement requests'],
  VIEWER:           ['View dashboard & all asset data', 'View reports (read-only)', 'No edit or upload access'],
};

export function UserProfileView() {
  const { user, updateSession, signOut } = useAuth();

  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState(user?.name ?? '');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // Password section
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwError, setPwError] = useState<string | null>(null);

  if (!user) return null;

  const avatarBg = ROLE_COLORS[user.role] ?? 'bg-slate-500';
  const roleLabel = ROLE_LABELS[user.role] ?? user.role;
  const permissions = ROLE_PERMISSIONS[user.role] ?? [];
  const canEditCurrentProfile = canEditProfile(user.role);

  const handleSaveProfile = async () => {
    if (!canEditCurrentProfile) {
      setSaveMsg('Viewer accounts cannot edit profile details.');
      return;
    }

    setSaving(true);
    try {
      await updateSession({ name: editName.trim() });
      setIsEditing(false);
      setSaveMsg('Profile updated successfully.');
    } catch {
      setSaveMsg('Could not save your profile. Please try again.');
    } finally {
      setSaving(false);
    }
    setTimeout(() => setSaveMsg(null), 3000);
  };

  const handleChangePassword = async () => {
    setPwError(null);
    if (!currentPw) { setPwError('Please enter your current password.'); return; }
    if (newPw.length < 8) { setPwError('New password must be at least 8 characters.'); return; }
    if (newPw !== confirmPw) { setPwError('New passwords do not match.'); return; }
    if (!user.email) { setPwError('No email on this account.'); return; }
    // Verify the current password against Supabase, then rotate it.
    const supabase = getSupabaseBrowserClient();
    const { error: verifyError } = await supabase.auth.signInWithPassword({
      email: user.email,
      password: currentPw,
    });
    if (verifyError) { setPwError('Current password is incorrect.'); return; }
    const { error } = await supabase.auth.updateUser({ password: newPw });
    if (error) { setPwError('Could not change the password. Please try again.'); return; }
    setPwMsg('Password changed successfully.');
    setCurrentPw(''); setNewPw(''); setConfirmPw('');
    setShowPasswordForm(false);
    setTimeout(() => setPwMsg(null), 3000);
  };

  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-6 pb-10">
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-bold text-slate-900">My Profile</h1>
        <p className="text-slate-500 text-sm mt-0.5">Manage your account details and security settings.</p>
      </motion.div>

      {/* Hero card */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.05 }}
        className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden"
      >
        {/* Header band */}
        <div className={`h-20 ${avatarBg} bg-opacity-90`} style={{ background: `linear-gradient(135deg, var(--tw-gradient-stops))` }} />
        <div className="px-6 pb-6 -mt-10 flex items-end gap-5">
          <div className={`w-20 h-20 rounded-2xl ${avatarBg} text-white flex items-center justify-center font-bold text-2xl shadow-lg border-4 border-white`}>
            {user.name.slice(0, 2).toUpperCase()}
          </div>
          <div className="mb-2">
            <h2 className="text-xl font-bold text-slate-900">{user.name}</h2>
            <div className="flex items-center gap-2 mt-1">
              <ShieldCheck size={14} className="text-blue-500" />
              <span className="text-sm text-slate-600 font-medium">{roleLabel}</span>
            </div>
          </div>
          <div className="ml-auto mb-2">
            {!isEditing ? (
              <button
                onClick={() => canEditCurrentProfile && setIsEditing(true)}
                disabled={!canEditCurrentProfile}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Edit3 size={14} /> {canEditCurrentProfile ? 'Edit Profile' : 'Read Only'}
              </button>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={handleSaveProfile}
                  disabled={saving}
                  className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-sm font-semibold transition-colors disabled:opacity-60"
                >
                  <Save size={14} /> {saving ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={() => { setIsEditing(false); setEditName(user.name); }}
                  className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-xl text-sm font-semibold transition-colors"
                >
                  <X size={14} /> Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      </motion.div>

      {/* Save success */}
      <AnimatePresence>
        {saveMsg && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="flex items-center gap-2 text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3 text-sm font-semibold">
            <CheckCircle2 size={16} /> {saveMsg}
          </motion.div>
        )}
        {pwMsg && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="flex items-center gap-2 text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3 text-sm font-semibold">
            <CheckCircle2 size={16} /> {pwMsg}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Details & Security grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Account Details */}
        <motion.div initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 }}
          className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="text-sm font-bold text-slate-700 mb-4 flex items-center gap-2">
            <User size={15} className="text-blue-500" /> Account Details
          </h3>

          <div className="space-y-4">
            <div>
              <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">Full Name</label>
              {isEditing ? (
                <input
                  value={editName}
                  onChange={e => setEditName(e.target.value)}
                  className="w-full border border-blue-300 rounded-xl px-3 py-2 text-sm font-semibold text-slate-800 bg-white focus:ring-2 focus:ring-blue-400 outline-none"
                />
              ) : (
                <div className="text-sm font-semibold text-slate-800 py-2 px-3 bg-slate-50 rounded-xl border border-slate-100">{user.name}</div>
              )}
            </div>
            <div>
              <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">Email Address</label>
              {/* Email is the sign-in identity — changed by an admin or via
                  Supabase account recovery, never via profile edit. */}
              <div className="text-sm font-semibold text-slate-800 py-2 px-3 bg-slate-50 rounded-xl border border-slate-100 flex items-center gap-2">
                <Mail size={13} className="text-slate-400" /> {user.email}
              </div>
            </div>
            <div>
              <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">Role</label>
              <div className="text-sm font-semibold text-slate-800 py-2 px-3 bg-slate-50 rounded-xl border border-slate-100 flex items-center gap-2">
                <Shield size={13} className="text-blue-500" /> {roleLabel}
                <span className="ml-auto text-[10px] text-slate-400 font-medium">(Assigned by Admin)</span>
              </div>
            </div>
            <div>
              <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">User ID</label>
              <div className="text-xs font-mono text-slate-500 py-2 px-3 bg-slate-50 rounded-xl border border-slate-100">{user.id}</div>
            </div>
          </div>
        </motion.div>

        {/* Security */}
        <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 }}
          className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col">
          <h3 className="text-sm font-bold text-slate-700 mb-4 flex items-center gap-2">
            <Lock size={15} className="text-blue-500" /> Security
          </h3>

          {!showPasswordForm ? (
            <div className="flex-1 flex flex-col gap-4">
              <div className="flex-1 p-4 bg-slate-50 rounded-xl border border-slate-100">
                <div className="text-[11px] font-bold uppercase text-slate-400 mb-1">Password</div>
                <div className="text-sm text-slate-600">••••••••••••</div>
              </div>
              <button
                onClick={() => canEditCurrentProfile && setShowPasswordForm(true)}
                disabled={!canEditCurrentProfile}
                className="flex items-center gap-2 px-4 py-2.5 bg-slate-100 hover:bg-blue-50 hover:text-blue-700 text-slate-700 rounded-xl text-sm font-semibold transition-colors border border-slate-200 w-fit disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Lock size={14} /> {canEditCurrentProfile ? 'Change Password' : 'Read Only'}
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {/* Current password */}
              <div>
                <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">Current Password</label>
                <div className="relative">
                  <input
                    type={showCurrent ? 'text' : 'password'}
                    value={currentPw}
                    onChange={e => setCurrentPw(e.target.value)}
                    className="w-full border border-slate-200 rounded-xl px-3 py-2 pr-10 text-sm text-slate-800 bg-white focus:ring-2 focus:ring-blue-400 outline-none"
                    placeholder="Enter current password"
                  />
                  <button type="button" onClick={() => setShowCurrent(v => !v)} className="absolute right-3 top-2.5 text-slate-400 hover:text-slate-600">
                    {showCurrent ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
              {/* New password */}
              <div>
                <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">New Password</label>
                <div className="relative">
                  <input
                    type={showNew ? 'text' : 'password'}
                    value={newPw}
                    onChange={e => setNewPw(e.target.value)}
                    className="w-full border border-slate-200 rounded-xl px-3 py-2 pr-10 text-sm text-slate-800 bg-white focus:ring-2 focus:ring-blue-400 outline-none"
                    placeholder="Min. 8 characters"
                  />
                  <button type="button" onClick={() => setShowNew(v => !v)} className="absolute right-3 top-2.5 text-slate-400 hover:text-slate-600">
                    {showNew ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
              {/* Confirm password */}
              <div>
                <label className="text-[11px] font-bold uppercase text-slate-400 mb-1 block">Confirm New Password</label>
                <input
                  type="password"
                  value={confirmPw}
                  onChange={e => setConfirmPw(e.target.value)}
                  className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm text-slate-800 bg-white focus:ring-2 focus:ring-blue-400 outline-none"
                  placeholder="Re-enter new password"
                />
              </div>
              {pwError && <p className="text-xs text-red-600 font-semibold">{pwError}</p>}
              <div className="flex gap-2 mt-1">
                <button onClick={handleChangePassword} className="flex-1 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold transition-colors">Save Password</button>
                <button onClick={() => { setShowPasswordForm(false); setPwError(null); }} className="px-4 py-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-xl text-sm font-semibold transition-colors">Cancel</button>
              </div>
            </div>
          )}

          <div className="mt-4 pt-4 border-t border-slate-100">
            <button
              onClick={() => void signOut()}
              className="flex items-center gap-2 text-sm font-semibold text-red-600 hover:text-red-700 transition-colors"
            >
              <LogOut size={15} /> Sign Out
            </button>
          </div>
        </motion.div>
      </div>

      {/* Role Permissions */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
        className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
        <h3 className="text-sm font-bold text-slate-700 mb-4 flex items-center gap-2">
          <ShieldCheck size={15} className="text-blue-500" /> Your Role Permissions
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {permissions.map(perm => (
            <div key={perm} className="flex items-center gap-2.5 text-sm text-slate-700 bg-slate-50 rounded-xl px-3 py-2.5 border border-slate-100">
              <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
              {perm}
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400 mt-4">
          Your role is assigned by the system administrator. To request a role change, contact your campus IT department.
        </p>
      </motion.div>
    </div>
  );
}
