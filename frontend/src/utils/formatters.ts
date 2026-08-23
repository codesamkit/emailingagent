import { ImportanceLevel, ReplyOutlineStatus } from '../types/email';

export function getImportanceConfig(level: ImportanceLevel | null, _score: number | null) {
  if (!level) {
    return {
      label: 'Unscored',
      badgeClass: 'bg-slate-100 text-slate-600 border-slate-200',
      textClass: 'text-slate-600',
      dotClass: 'bg-slate-400',
      ringColor: '#94A3B8',
      accentBorder: 'border-slate-200',
    };
  }

  switch (level) {
    case 'urgent':
      return {
        label: 'Urgent',
        badgeClass: 'bg-blue-50 text-blue-700 border-blue-300 shadow-xs font-semibold',
        textClass: 'text-blue-700 font-bold',
        dotClass: 'bg-blue-600 animate-pulse',
        ringColor: '#2563EB',
        accentBorder: 'border-blue-300',
      };
    case 'high':
      return {
        label: 'High',
        badgeClass: 'bg-indigo-50 text-indigo-700 border-indigo-200 font-semibold',
        textClass: 'text-indigo-700',
        dotClass: 'bg-indigo-600',
        ringColor: '#4F46E5',
        accentBorder: 'border-indigo-200',
      };
    case 'medium':
      return {
        label: 'Medium',
        badgeClass: 'bg-slate-100 text-slate-700 border-slate-200 font-medium',
        textClass: 'text-slate-700',
        dotClass: 'bg-slate-500',
        ringColor: '#64748B',
        accentBorder: 'border-slate-200',
      };
    case 'low':
    default:
      return {
        label: 'Low',
        badgeClass: 'bg-slate-50 text-slate-500 border-slate-200 font-medium',
        textClass: 'text-slate-500',
        dotClass: 'bg-slate-400',
        ringColor: '#94A3B8',
        accentBorder: 'border-slate-200',
      };
  }
}

export function getOutlineStatusBadge(status: ReplyOutlineStatus) {
  switch (status) {
    case 'suggested':
      return {
        label: 'AI Outline Ready',
        className: 'bg-blue-50 text-blue-700 border-blue-200 font-semibold',
      };
    case 'edited':
      return {
        label: 'User Edited',
        className: 'bg-emerald-50 text-emerald-700 border-emerald-200 font-semibold',
      };
    case 'expanded_to_draft':
      return {
        label: 'Draft Expanded',
        className: 'bg-indigo-50 text-indigo-700 border-indigo-200 font-semibold',
      };
    case 'not_applicable':
      return {
        label: 'No-Reply (N/A)',
        className: 'bg-slate-100 text-slate-600 border-slate-200',
      };
    case 'none':
    default:
      return {
        label: 'No Outline',
        className: 'bg-slate-50 text-slate-400 border-slate-200',
      };
  }
}

export function extractSenderName(sender: string): { name: string; email: string } {
  const match = sender.match(/^(.*?)(?:<(.+?)>)?$/);
  if (match && match[2]) {
    return {
      name: match[1].replace(/["']/g, '').trim() || match[2],
      email: match[2].trim(),
    };
  }
  return {
    name: sender.split('@')[0],
    email: sender,
  };
}
