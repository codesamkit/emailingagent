import React from 'react';
import { ShieldAlert } from 'lucide-react';

interface NoReplyBadgeProps {
  reason: string | null;
}

export const NoReplyBadge: React.FC<NoReplyBadgeProps> = ({ reason }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 flex items-start gap-3">
      <div className="p-1.5 rounded-lg bg-slate-200 shrink-0">
        <ShieldAlert className="w-4 h-4 text-slate-600" />
      </div>
      <div className="space-y-1">
        <p className="text-xs font-bold text-slate-700 uppercase tracking-wider">
          No-Reply Sender — Auto Shield Active
        </p>
        <p className="text-xs text-slate-500 leading-relaxed">
          {reason || 'This sender is identified as an automated system. Reply drafting is disabled for safety.'}
        </p>
      </div>
    </div>
  );
};
