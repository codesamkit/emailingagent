import React, { useState } from 'react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { EmailItem } from '../../types/email';
import { api } from '../../services/api';
import { Sparkles, Copy, Check, Sliders, ExternalLink, Edit3 } from 'lucide-react';

interface DraftModalProps {
  isOpen: boolean;
  onClose: () => void;
  email: EmailItem;
}

export const DraftModal: React.FC<DraftModalProps> = ({ isOpen, onClose, email }) => {
  const [tone, setTone] = useState<'professional' | 'concise' | 'direct' | 'warm'>('professional');
  const [draftContent, setDraftContent] = useState<string>('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [copied, setCopied] = useState(false);

  React.useEffect(() => {
    if (!isOpen || !email) return;
    // The pipeline already auto-generates a professional-tone draft for
    // every eligible email — show it instantly instead of re-running the
    // LLM call just to open the modal. Switching tone still regenerates.
    if (tone === 'professional' && email.replyDraft) {
      setDraftContent(email.replyDraft);
      setIsGenerating(false);
      return;
    }
    generateDraft(tone);
  }, [isOpen, email?.emailId, tone]);

  const generateDraft = async (selectedTone: 'professional' | 'concise' | 'direct' | 'warm') => {
    setIsGenerating(true);
    try {
      const generated = await api.expandDraft(email.emailId, selectedTone);
      setDraftContent(generated);
    } catch (err: any) {
      setDraftContent(`Error expanding draft: ${err.message || 'Unknown error'}`);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(draftContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Valence AI Draft Expander" subtitle={`Reply for: "${email.subject}"`} maxWidth="2xl">
      <div className="space-y-4">
        {/* Tone Selector */}
        <div className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-xl bg-slate-50 border border-slate-200">
          <div className="flex items-center gap-1.5 text-xs text-slate-600 font-medium">
            <Sliders className="w-3.5 h-3.5 text-blue-600" />
            <span>Tone Style:</span>
          </div>
          <div className="flex items-center gap-1 bg-white rounded-lg p-0.5 border border-slate-200 text-xs">
            {(['professional', 'concise', 'direct', 'warm'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTone(t)}
                className={`px-3 py-1 rounded-md capitalize font-medium transition-all ${
                  tone === t ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-xs' : 'text-slate-500 hover:text-slate-900'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Source Bullets */}
        {email.replyOutline && (
          <div className="p-3 rounded-lg bg-blue-50 border border-blue-200 space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-blue-700">Source Outline Bullets:</span>
            <ul className="text-xs text-slate-700 list-disc list-inside space-y-0.5 font-mono">
              {email.replyOutline.map((b, i) => <li key={i} className="truncate">{b}</li>)}
            </ul>
          </div>
        )}

        {/* Draft Textarea */}
        <div className="relative">
          <div className="flex items-center justify-between pb-1.5 text-xs text-slate-500">
            <span className="flex items-center gap-1"><Edit3 className="w-3 h-3 text-blue-600" />Editable Draft Prose:</span>
            <span className="text-[11px] font-mono">{draftContent.length} characters</span>
          </div>
          <textarea
            rows={10}
            value={draftContent}
            onChange={(e) => setDraftContent(e.target.value)}
            disabled={isGenerating}
            className="w-full p-4 text-xs sm:text-sm bg-white text-slate-800 placeholder-slate-400 rounded-xl border border-slate-200 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 leading-relaxed resize-y focus:outline-none"
          />
          {isGenerating && (
            <div className="absolute inset-0 bg-white/80 backdrop-blur-xs rounded-xl flex items-center justify-center gap-2 text-xs text-blue-700 font-semibold">
              <Sparkles className="w-4 h-4 text-blue-600 animate-spin" />
              Expanding with Valence AI...
            </div>
          )}
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-200">
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleCopy} icon={copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}>
              {copied ? 'Copied!' : 'Copy Draft'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => generateDraft(tone)} icon={<Sparkles className="w-3.5 h-3.5 text-blue-600" />}>
              Regenerate
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={onClose}>Close</Button>
            <Button
              variant="primary"
              size="sm"
              icon={<ExternalLink className="w-3.5 h-3.5" />}
              onClick={() => {
                handleCopy();
                window.open(`mailto:${encodeURIComponent(email.sender)}?subject=${encodeURIComponent('Re: ' + email.subject)}&body=${encodeURIComponent(draftContent)}`, '_blank');
              }}
            >
              Copy & Open in Email
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
};
