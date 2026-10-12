'use client';

import Link from 'next/link';
import { FormEvent, useState } from 'react';
import React from 'react';
import {
  ArrowLeft, CheckCircle2, Eye, EyeOff, KeyRound, Mail,
  Phone, ShieldCheck, UserRound, Users, Wrench, BriefcaseBusiness,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { Role } from '@/lib/permissions';

const roles: { value: Role; label: string }[] = [
  { value: 'FACILITY_MANAGER', label: 'Manager' },
  { value: 'TECHNICIAN', label: 'Technician' },
  { value: 'VIEWER', label: 'Viewer' },
];

const inputClass = 'mt-1 block w-full rounded-xl border-0 bg-slate-50 py-2.5 pl-11 text-slate-900 ring-1 ring-inset ring-slate-200 placeholder:text-slate-400 shadow-sm transition-all focus:bg-white focus:ring-2 focus:ring-blue-600 sm:text-sm';

type SignupForm = {
  name: string; email: string; password: string; confirmPassword: string;
  phone: string; role: Role | ''; staffId: string; designation: string;
};

const initialForm: SignupForm = {
  name: '', email: '', password: '', confirmPassword: '', phone: '', role: '', staffId: '', designation: '',
};

function Field({ label, icon: Icon, children, className = '' }: { label: string; icon: typeof Mail; children: React.ReactNode; className?: string }) {
  return <label className={`block text-sm font-medium text-slate-900 ${className}`}><span>{label}</span><div className="relative"><Icon className="pointer-events-none absolute left-4 top-3 h-5 w-5 text-slate-400" />{children}</div></label>;
}

function PasswordField({ label, value, onChange, visible, onToggle }: { label: string; value: string; onChange: (value: string) => void; visible: boolean; onToggle: () => void }) {
  return <Field label={label} icon={KeyRound}><input required type={visible ? 'text' : 'password'} value={value} onChange={event => onChange(event.target.value)} placeholder="At least 8 characters" autoComplete="new-password" className={`${inputClass} pr-11`} /><button type="button" onClick={onToggle} className="absolute right-3 top-2.5 rounded-lg p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700" aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button></Field>;
}

export default function SignupPage() {
  const { signUp } = useAuth();
  const [form, setForm] = useState<SignupForm>(initialForm);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<'approved-flow' | 'confirm-email' | null>(null);
  const needsStaffDetails = form.role === 'TECHNICIAN' || form.role === 'FACILITY_MANAGER';
  const isTechnician = form.role === 'TECHNICIAN';

  const update = <K extends keyof SignupForm>(key: K, value: SignupForm[K]) => setForm(current => ({ ...current, [key]: value }));
  const changeRole = (role: Role) => setForm(current => ({ ...current, role, staffId: '', designation: '' }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (!form.role) return setError('Please choose the role you are requesting.');
    if (form.password.length < 8) return setError('Password must be at least 8 characters.');
    if (form.password !== form.confirmPassword) return setError('Passwords do not match.');
    if (needsStaffDetails && !form.staffId.trim()) return setError('Employee / Staff ID is required for this role.');

    setSubmitting(true);
    try {
      const result = await signUp({
        name: form.name,
        email: form.email,
        password: form.password,
        requestedRole: form.role,
        staffId: form.staffId.trim() || undefined,
        phone: form.phone.trim() || undefined,
        designation: form.designation.trim() || undefined,
      });
      setSubmitted(result.needsEmailConfirmation ? 'confirm-email' : 'approved-flow');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the account. Please try again.');
      setSubmitting(false);
    }
  };

  if (submitted) return (
    <main className="min-h-screen flex items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-xl">
        <CheckCircle2 className="mx-auto text-emerald-600" size={48} />
        <h1 className="mt-4 text-2xl font-bold text-slate-900">
          {submitted === 'confirm-email' ? 'Confirm your email' : 'Request submitted'}
        </h1>
        <p className="mt-2 text-slate-600">
          {submitted === 'confirm-email'
            ? 'We sent a confirmation link to your email address. Confirm it, then sign in — your account becomes active once an approver accepts it.'
            : 'Your request has been sent for approval. You can sign in after an approver accepts your account.'}
        </p>
        <Link href="/login" className="mt-6 inline-flex items-center gap-2 font-semibold text-blue-600"><ArrowLeft size={16} /> Return to sign in</Link>
      </div>
    </main>
  );

  return <main className="min-h-screen bg-slate-50 px-4 py-8 sm:py-10"><div className="mx-auto max-w-3xl"><Link href="/login" className="inline-flex items-center gap-2 text-sm text-slate-600 hover:text-blue-600"><ArrowLeft size={16} /> Back to sign in</Link><div className="mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl"><div className="bg-slate-900 p-6 text-white"><div className="flex items-center gap-3"><ShieldCheck className="text-blue-400" /><div><h1 className="text-2xl font-bold">Create an account</h1><p className="mt-1 text-sm text-slate-300">All new accounts require approval before sign in.</p></div></div></div><form onSubmit={submit} className="grid grid-cols-1 gap-5 p-6 sm:grid-cols-2">
    {error && <div className="sm:col-span-2 rounded-xl border border-red-200 bg-red-50 p-4 text-center text-sm font-medium text-red-600">{error}</div>}
    <Field label="Full Name" icon={UserRound} className="sm:col-span-2"><input required value={form.name} onChange={event => update('name', event.target.value)} className={inputClass} placeholder="Full name" autoComplete="name" /></Field>
    <Field label="Email address" icon={Mail}><input required type="email" value={form.email} onChange={event => update('email', event.target.value)} className={inputClass} placeholder="you@college.com" autoComplete="email" /></Field>
    <Field label="Phone number" icon={Phone}><input required type="tel" value={form.phone} onChange={event => update('phone', event.target.value)} className={inputClass} placeholder="Phone number" autoComplete="tel" /></Field>
    <PasswordField label="Password" value={form.password} onChange={value => update('password', value)} visible={showPassword} onToggle={() => setShowPassword(value => !value)} />
    <PasswordField label="Confirm Password" value={form.confirmPassword} onChange={value => update('confirmPassword', value)} visible={showConfirmPassword} onToggle={() => setShowConfirmPassword(value => !value)} />
    <p className="-mt-3 text-xs text-slate-500 sm:col-span-2">Use at least 8 characters for your password.</p>
    <Field label="Role" icon={Users} className="sm:col-span-2"><select required value={form.role} onChange={event => changeRole(event.target.value as Role)} className={inputClass}><option value="">Select role</option>{roles.map(role => <option key={role.value} value={role.value}>{role.label}</option>)}</select></Field>

    {needsStaffDetails && <div className="sm:col-span-2 border-t border-slate-100 pt-5"><div className="mb-4 flex items-center gap-2"><Wrench className="text-blue-600" size={18} /><h2 className="font-semibold text-slate-900">{isTechnician ? 'Technician Details' : 'Manager Details'}</h2></div><div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
      <Field label="Employee / Staff ID" icon={KeyRound}><input required value={form.staffId} onChange={event => update('staffId', event.target.value)} className={inputClass} placeholder="Staff ID" /></Field>
      <Field label="Designation" icon={BriefcaseBusiness}><input value={form.designation} onChange={event => update('designation', event.target.value)} className={inputClass} placeholder={isTechnician ? 'AC Technician' : 'Facility Manager'} /></Field>
      <p className="text-xs text-slate-500 sm:col-span-2">Department, assigned blocks and reporting manager are set by the approver when your account is accepted.</p>
    </div></div>}
    <button type="submit" disabled={submitting} className="sm:col-span-2 mt-1 flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-3 py-3 text-sm font-bold text-white shadow-md transition-all hover:bg-blue-700 hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 active:scale-[0.98] disabled:opacity-70 disabled:cursor-wait"><ShieldCheck size={18} /> {submitting ? 'Creating account…' : 'Create Account'}</button>
  </form></div></div></main>;
}
