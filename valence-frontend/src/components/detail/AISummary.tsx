import React, { useState } from 'react';
import { Sparkles, Copy, Check } from 'lucide-react';

interface AISummaryProps {
  summary: string | null;
}

export const AISummary: React.FC<AISummaryProps> = ({ summary }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (!summary) return;
    navigator.clipboard.writeText(summary);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!summary) {
    return (
      <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 text-xs text-slate-400 italic flex items-center gap-2">
        <Sparkles className="w-4 h-4 text-slate-300" />
        No AI summary generated yet for this message.
      </div>
    );
  }

  return (
    <div className="relative p-4 rounded-xl bg-gradient-to-br from-blue-50 via-indigo-50/50 to-white border border-blue-200">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="p-1 rounded-md bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-xs">
            <Sparkles className="w-4 h-4" />
          </div>
          <span className="text-xs font-bold uppercase tracking-wider text-blue-800">
            Valence AI Executive Summary
          </span>
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-800 px-2 py-1 rounded bg-white hover:bg-slate-50 border border-slate-200 transition-colors"
        >
          {copied ? (
            <><Check className="w-3 h-3 text-emerald-500" /><span className="text-emerald-600">Copied</span></>
          ) : (
            <><Copy className="w-3 h-3" /><span>Copy</span></>
          )}
        </button>
      </div>
      <p className="text-sm text-slate-700 leading-relaxed">{summary}</p>
    </div>
  );
};
