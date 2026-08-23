import React from 'react';
import { EmailItem } from '../../types/email';
import { EmailListItem } from './EmailListItem';
import { Inbox } from 'lucide-react';

interface EmailListProps {
  emails: EmailItem[];
  selectedEmailId: string | null;
  onSelectEmail: (email: EmailItem) => void;
  onToggleRead: (emailId: string, e: React.MouseEvent) => void;
  isLoading: boolean;
}

export const EmailList: React.FC<EmailListProps> = ({
  emails,
  selectedEmailId,
  onSelectEmail,
  onToggleRead,
  isLoading,
}) => {
  if (isLoading) {
    return (
      <div className="flex-1 overflow-y-auto divide-y divide-slate-800/40 p-4 space-y-4">
        {[1, 2, 3, 4, 5].map((n) => (
          <div key={n} className="p-4 rounded-xl bg-slate-900/40 border border-slate-800 animate-pulse space-y-3">
            <div className="flex justify-between items-center">
              <div className="h-4 bg-slate-800 rounded w-1/3"></div>
              <div className="h-3 bg-slate-800 rounded w-16"></div>
            </div>
            <div className="h-4 bg-slate-800 rounded w-3/4"></div>
            <div className="h-3 bg-slate-800/60 rounded w-full"></div>
            <div className="flex gap-2">
              <div className="h-5 bg-slate-800 rounded w-20"></div>
              <div className="h-5 bg-slate-800 rounded w-24"></div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (emails.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center bg-valens-black/50">
        <div className="w-16 h-16 rounded-2xl bg-slate-900 border border-slate-800 flex items-center justify-center mb-4 text-slate-500 shadow-[0_0_20px_rgba(0,0,0,0.5)]">
          <Inbox className="w-8 h-8" />
        </div>
        <h3 className="text-base font-semibold text-white mb-1">Inbox Zero or No Matches</h3>
        <p className="text-xs text-slate-400 max-w-xs leading-relaxed">
          No emails match your current search and filter criteria. Try clearing active filters or refreshing the feed.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto divide-y divide-slate-800/60 bg-valens-black">
      {emails.map((email) => (
        <EmailListItem
          key={email.emailId}
          email={email}
          isSelected={email.emailId === selectedEmailId}
          onSelect={onSelectEmail}
          onToggleRead={onToggleRead}
        />
      ))}
    </div>
  );
};
