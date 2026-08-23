import React from 'react';
import { EmailItem } from '../../types/email';
import { getOutlineStatusBadge } from '../../utils/formatters';
import { Button } from '../ui/Button';
import {
  Sparkles,
  Plus,
  Trash2,
  Save,
  FileEdit,
  MailCheck,
  ShieldAlert,
} from 'lucide-react';

interface OutlineEditorProps {
  email: EmailItem;
  onSaveOutline: (emailId: string, bullets: string[]) => Promise<void>;
  onOpenDraftModal: () => void;
  onToggleRead: (emailId: string) => void;
}

export const OutlineEditor: React.FC<OutlineEditorProps> = ({
  email,
  onSaveOutline,
  onOpenDraftModal,
  onToggleRead,
}) => {
  const [bullets, setBullets] = React.useState<string[]>(email.replyOutline || []);
  const [newBulletText, setNewBulletText] = React.useState('');
  const [isSaving, setIsSaving] = React.useState(false);
  const [hasChanges, setHasChanges] = React.useState(false);

  React.useEffect(() => {
    setBullets(email.replyOutline || []);
    setHasChanges(false);
  }, [email.emailId, email.replyOutline]);

  const handleAddBullet = () => {
    if (!newBulletText.trim()) return;
    setBullets([...bullets, newBulletText.trim()]);
    setNewBulletText('');
    setHasChanges(true);
  };

  const handleEditBullet = (index: number, value: string) => {
    const updated = [...bullets];
    updated[index] = value;
    setBullets(updated);
    setHasChanges(true);
  };

  const handleDeleteBullet = (index: number) => {
    setBullets(bullets.filter((_, i) => i !== index));
    setHasChanges(true);
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSaveOutline(email.emailId, bullets);
      setHasChanges(false);
    } finally {
      setIsSaving(false);
    }
  };

  const statusBadge = getOutlineStatusBadge(email.replyOutlineStatus);

  if (email.isNoReply) {
    return (
      <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-slate-500" />
          <span className="text-xs font-bold uppercase tracking-wider text-slate-600">
            Reply Outline: Not Applicable
          </span>
        </div>
        <p className="text-xs text-slate-500 leading-relaxed">
          Transactional & automated senders do not require a response. Reply outline generation is gated off for safety.
        </p>
      </div>
    );
  }

  if (email.readStatus === 'unread') {
    return (
      <div className="p-5 rounded-xl bg-blue-50/60 border border-blue-200 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-blue-600" />
            <span className="text-xs font-bold uppercase tracking-wider text-blue-700">
              AI Reply Outline Gated
            </span>
          </div>
          <span className="text-[11px] px-2 py-0.5 rounded bg-blue-100 text-blue-700 border border-blue-200 font-medium">
            Unread Status
          </span>
        </div>
        <p className="text-xs text-slate-700 leading-relaxed">
          Reply outlines are generated after marking email as <strong>Read</strong> — keeping AI actions intentional.
        </p>
        <Button
          variant="primary"
          size="sm"
          onClick={() => onToggleRead(email.emailId)}
          icon={<MailCheck className="w-4 h-4" />}
        >
          Mark as Read & Generate Outline
        </Button>
      </div>
    );
  }

  return (
    <div className="p-5 rounded-xl bg-white border border-blue-200 shadow-xs space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1 rounded-md bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-xs">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-800">
              AI Planned Reply Outline
            </h4>
            <p className="text-[11px] text-slate-500">
              Editable bullet skeleton ready to expand into a full draft.
            </p>
          </div>
        </div>
        <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${statusBadge.className}`}>
          {statusBadge.label}
        </span>
      </div>

      {/* Bullet List */}
      <div className="space-y-2">
        {bullets.map((bullet, idx) => (
          <div
            key={idx}
            className="flex items-start gap-2 p-2.5 rounded-lg bg-slate-50 border border-slate-200 hover:border-blue-300 group transition-all"
          >
            <div className="pt-2">
              <span className="w-1.5 h-1.5 rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 block" />
            </div>
            <textarea
              rows={1}
              value={bullet}
              onChange={(e) => handleEditBullet(idx, e.target.value)}
              className="flex-1 bg-transparent text-xs text-slate-800 placeholder-slate-400 resize-none focus:outline-none focus:ring-1 focus:ring-blue-400/50 rounded p-1 leading-relaxed"
            />
            <button
              onClick={() => handleDeleteBullet(idx)}
              className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 p-1 rounded transition-all"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
        {bullets.length === 0 && (
          <div className="p-4 rounded-lg bg-slate-50 border border-dashed border-slate-300 text-center text-xs text-slate-500">
            No outline bullets yet. Add one below.
          </div>
        )}
      </div>

      {/* Add Bullet Input */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Add a new reply bullet point..."
          value={newBulletText}
          onChange={(e) => setNewBulletText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleAddBullet(); }}
          className="flex-1 px-3 py-1.5 text-xs bg-white text-slate-800 placeholder-slate-400 rounded-lg border border-slate-200 focus:outline-none focus:border-blue-500"
        />
        <Button variant="secondary" size="sm" onClick={handleAddBullet} icon={<Plus className="w-3.5 h-3.5" />}>
          Add
        </Button>
      </div>

      {/* Action Footer */}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-200">
        <div className="flex items-center gap-2">
          {hasChanges && (
            <Button variant="primary" size="sm" onClick={handleSave} loading={isSaving} icon={<Save className="w-3.5 h-3.5" />}>
              Save Changes
            </Button>
          )}
          <span className="text-[11px] text-slate-500">{bullets.length} bullet{bullets.length !== 1 ? 's' : ''}</span>
        </div>
        <Button variant="primary" size="sm" onClick={onOpenDraftModal} icon={<FileEdit className="w-3.5 h-3.5" />}>
          Expand to Full Draft
        </Button>
      </div>
    </div>
  );
};
