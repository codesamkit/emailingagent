import React from 'react';
import { ImportanceLevel } from '../../types/email';
import { getImportanceConfig } from '../../utils/formatters';

interface ImportanceBadgeProps {
  level: ImportanceLevel | null;
  score: number | null;
  justification: string | null;
}

export const ImportanceBadge: React.FC<ImportanceBadgeProps> = ({ level, score, justification }) => {
  const config = getImportanceConfig(level, score);
  const pct = score !== null ? Math.round(score) : 0;
  const radius = 20;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (pct / 100) * circumference;

  return (
    <div className="flex items-start gap-4 p-4 rounded-xl bg-slate-50 border border-slate-200">
      {/* Radial Score Ring */}
      <div className="relative shrink-0 w-14 h-14">
        <svg className="w-14 h-14 -rotate-90" viewBox="0 0 52 52">
          {/* Track */}
          <circle cx="26" cy="26" r={radius} fill="none" stroke="#E2E8F0" strokeWidth="5" />
          {/* Fill */}
          <circle
            cx="26"
            cy="26"
            r={radius}
            fill="none"
            stroke={config.ringColor}
            strokeWidth="5"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={`text-xs font-bold font-mono ${config.textClass}`}>
            {score !== null ? pct : '—'}
          </span>
        </div>
      </div>

      {/* Text Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${config.badgeClass}`}>
            {config.label} Priority
          </span>
          {score !== null && (
            <span className="text-[11px] text-slate-500 font-mono font-medium">
              Score: {score.toFixed(1)}/100
            </span>
          )}
        </div>
        {justification && (
          <p className="text-xs text-slate-600 leading-relaxed line-clamp-3">{justification}</p>
        )}
      </div>
    </div>
  );
};
