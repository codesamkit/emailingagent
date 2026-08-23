import React from 'react';
import { EmailItem } from '../../types/email';
import { formatRelativeTime } from '../../utils/date';
import { getImportanceConfig, extractSenderName } from '../../utils/formatters';
import {
  Calendar,
  ShieldAlert,
  Clock,
  Sparkles,
  Circle,
} from 'lucide-react';

interface EmailListItemProps {
  email: EmailItem;
  isSelected: boolean;
  onSelect: (email: EmailItem) => void;
  onToggleRead: (emailId: string, e: React.MouseEvent) => void;
}

export const EmailListItem: React.FC<EmailListItemProps> = ({
  email,
  isSelected,
  onSelect,
  onToggleRead,
}) => {
  const { name, email: emailAddr } = extractSenderName(email.sender);
  const importanceConfig = getImportanceConfig(email.importanceLevel, email.importanceScore);
  const isUnread = email.readStatus === 'unread';

  return (
    <div
      onClick={() => onSelect(email)}
      className={`p-3.5 border-b border-slate-200 cursor-pointer transition-all relative select-none ${
        isSelected
          ? 'bg-blue-50/70 border-l-4 border-l-blue-600 shadow-xs'
          : 'hover:bg-slate-50 border-l-4 border-l-transparent bg-white'
      } ${isUnread ? 'bg-slate-50/50' : ''}`}
    >
      {/* Top row: Sender & Timestamp & Unread Indicator */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          {/* Read / Unread toggle dot */}
          <button
            onClick={(e) => onToggleRead(email.emailId, e)}
            className="text-slate-400 hover:text-blue-600 p-0.5 rounded transition-colors"
            title={isUnread ? 'Mark as Read' : 'Mark as Unread'}
          >
            {isUnread ? (
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-600 shadow-xs"></span>
              </span>
            ) : (
              <Circle className="w-2.5 h-2.5 text-slate-300 hover:text-slate-500" />
            )}
          </button>

          {/* Sender Name */}
          <span
            className={`text-sm truncate ${
              isUnread ? 'font-bold text-slate-900' : 'font-semibold text-slate-700'
            }`}
          >
            {name}
          </span>
          <span className="text-[11px] text-slate-400 truncate hidden sm:inline">
            &lt;{emailAddr}&gt;
          </span>
        </div>

        {/* Timestamp */}
        <span className="text-[11px] font-mono text-slate-400 shrink-0 font-medium">
          {formatRelativeTime(email.receivedAt)}
        </span>
      </div>

      {/* Subject Line */}
      <div className="mb-1.5 flex items-center gap-2">
        <h4
          className={`text-sm line-clamp-1 ${
            isUnread ? 'font-bold text-slate-900' : 'font-medium text-slate-700'
          }`}
        >
          {email.subject || '(No subject)'}
        </h4>
      </div>

      {/* AI Summary Snippet */}
      {email.summary && (
        <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed mb-2.5 font-normal">
          {email.summary}
        </p>
      )}

      {/* Bottom Row: Badges and Indicators */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {/* Importance Score Pill */}
        <div
          className={`px-2 py-0.5 rounded-md font-mono text-[11px] font-bold border flex items-center gap-1 ${importanceConfig.badgeClass}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${importanceConfig.dotClass}`} />
          <span>{importanceConfig.label}</span>
          {email.importanceScore !== null && (
            <span className="opacity-90">({Math.round(email.importanceScore)})</span>
          )}
        </div>

        {/* No-Reply Badge */}
        {email.isNoReply && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 border border-slate-200 font-medium">
            <ShieldAlert className="w-3 h-3 text-slate-500" />
            No-Reply
          </span>
        )}

        {/* Scheduling Badge */}
        {email.isSchedulingRelated && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-sky-50 text-sky-700 border border-sky-200 font-medium">
            <Calendar className="w-3 h-3 text-sky-600" />
            Scheduling
          </span>
        )}

        {/* Outline Status */}
        {email.replyOutline && email.replyOutline.length > 0 && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 border border-indigo-200 font-medium">
            <Sparkles className="w-3 h-3 text-indigo-600" />
            Outline Ready ({email.replyOutline.length})
          </span>
        )}

        {/* Stale Thread Warning */}
        {email.isStale && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-50 text-amber-800 border border-amber-200 font-medium">
            <Clock className="w-3 h-3 text-amber-600" />
            Stale Outline
          </span>
        )}
      </div>
    </div>
  );
};
