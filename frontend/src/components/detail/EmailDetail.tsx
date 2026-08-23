import React from 'react';
import { EmailItem } from '../../types/email';
import { AISummary } from './AISummary';
import { ImportanceBadge } from './ImportanceBadge';
import { NoReplyBadge } from './NoReplyBadge';
import { CalendarContext } from './CalendarContext';
import { OutlineEditor } from './OutlineEditor';
import { DraftModal } from './DraftModal';
import { formatFullDateTime } from '../../utils/date';
import { extractSenderName } from '../../utils/formatters';
import { MailOpen, Mail, Tag, Clock } from 'lucide-react';

interface EmailDetailProps {
  email: EmailItem;
  onSaveOutline: (emailId: string, bullets: string[]) => Promise<void>;
  onToggleRead: (emailId: string) => void;
}

export const EmailDetail: React.FC<EmailDetailProps> = ({
  email,
  onSaveOutline,
  onToggleRead,
}) => {
  const [showDraftModal, setShowDraftModal] = React.useState(false);
  const { name, email: emailAddr } = extractSenderName(email.sender);

  return (
    <div className="flex flex-col h-full overflow-y-auto bg-white">
      {/* Email Header */}
      <div className="p-5 border-b border-slate-200 bg-white">
        <div className="flex items-start justify-between gap-4 mb-3">
          <h2 className="text-base font-bold text-slate-900 leading-snug flex-1">
            {email.subject || '(No subject)'}
          </h2>
          <button
            onClick={() => onToggleRead(email.emailId)}
            className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all bg-white text-slate-600 border-slate-200 hover:border-blue-400 hover:text-blue-700"
            title={email.readStatus === 'read' ? 'Mark as Unread' : 'Mark as Read'}
          >
            {email.readStatus === 'read' ? (
              <><Mail className="w-3.5 h-3.5" /> Mark Unread</>
            ) : (
              <><MailOpen className="w-3.5 h-3.5 text-blue-600" /> Mark Read</>
            )}
          </button>
        </div>

        {/* Meta Row */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500 mb-3">
          <div className="flex items-center gap-1.5">
            <div className="w-6 h-6 rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center text-white text-[10px] font-bold uppercase">
              {name.charAt(0)}
            </div>
            <div>
              <span className="font-semibold text-slate-800">{name}</span>
              <span className="text-slate-400 ml-1">&lt;{emailAddr}&gt;</span>
            </div>
          </div>
          <div className="flex items-center gap-1 text-slate-400">
            <Clock className="w-3 h-3" />
            <span>{formatFullDateTime(email.receivedAt)}</span>
          </div>
          {email.isSchedulingRelated && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-200 font-medium">
              <Tag className="w-3 h-3" />
              Scheduling
            </div>
          )}
        </div>

        {/* AI Importance Badge */}
        <ImportanceBadge
          level={email.importanceLevel}
          score={email.importanceScore}
          justification={email.importanceJustification}
        />
      </div>

      {/* AI Intelligence Panels */}
      <div className="p-5 space-y-4 flex-1">
        {/* AI Summary */}
        <AISummary summary={email.summary} />

        {/* No-Reply Shield */}
        {email.isNoReply && (
          <NoReplyBadge reason={email.noReplyReason} />
        )}

        {/* Calendar Context */}
        {email.hasCalendarContext && email.calendarContext && (
          <CalendarContext context={email.calendarContext} />
        )}

        {/* Reply Outline Editor */}
        <OutlineEditor
          email={email}
          onSaveOutline={onSaveOutline}
          onOpenDraftModal={() => setShowDraftModal(true)}
          onToggleRead={onToggleRead}
        />

        {/* Raw Email Body */}
        <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2">
          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
            Original Message
          </span>
          <pre className="text-xs text-slate-600 font-sans leading-relaxed whitespace-pre-wrap">
            {email.body || 'No body content available.'}
          </pre>
        </div>
      </div>

      {/* Draft Modal */}
      {showDraftModal && (
        <DraftModal
          isOpen={showDraftModal}
          onClose={() => setShowDraftModal(false)}
          email={email}
        />
      )}
    </div>
  );
};
