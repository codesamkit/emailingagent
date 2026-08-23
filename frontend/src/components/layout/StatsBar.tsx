import React from 'react';
import { InboxStats } from '../../types/email';
import { Mail, MailOpen, Flame, CalendarCheck, ShieldAlert, Sparkles } from 'lucide-react';

interface StatsBarProps {
  stats: InboxStats;
  onFilterClick?: (filterKey: string) => void;
}

export const StatsBar: React.FC<StatsBarProps> = ({ stats, onFilterClick }) => {
  const cards = [
    {
      key: 'all',
      label: 'Total Processed',
      value: stats.total,
      icon: <Mail className="w-4 h-4 text-blue-600" />,
      color: 'text-slate-900',
      badge: 'All Items',
    },
    {
      key: 'unread',
      label: 'Unread Intake',
      value: stats.unread,
      icon: <MailOpen className="w-4 h-4 text-sky-600" />,
      color: 'text-sky-700',
      badge: 'Needs Review',
    },
    {
      key: 'urgent',
      label: 'Urgent Priority',
      value: stats.byLevel?.urgent ?? 0,
      icon: <Flame className="w-4 h-4 text-blue-600" />,
      color: 'text-blue-600 font-bold',
      badge: 'Action Required',
    },
    {
      key: 'scheduling',
      label: 'Scheduling Asks',
      value: stats.scheduling,
      icon: <CalendarCheck className="w-4 h-4 text-indigo-600" />,
      color: 'text-indigo-700',
      badge: 'GCal Synced',
    },
    {
      key: 'outlines',
      label: 'Outlines Ready',
      value: stats.withOutline,
      icon: <Sparkles className="w-4 h-4 text-purple-600" />,
      color: 'text-purple-700',
      badge: 'AI Drafted',
    },
    {
      key: 'noreply',
      label: 'No-Reply Filtered',
      value: stats.noReply,
      icon: <ShieldAlert className="w-4 h-4 text-slate-500" />,
      color: 'text-slate-700',
      badge: 'Auto Shield',
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 p-4 bg-slate-100/60 border-b border-slate-200">
      {cards.map((card) => (
        <button
          key={card.key}
          onClick={() => onFilterClick && onFilterClick(card.key)}
          className="flex flex-col text-left p-3 rounded-xl bg-white hover:bg-slate-50 border border-slate-200 hover:border-blue-400 transition-all shadow-xs group cursor-pointer"
        >
          <div className="flex items-center justify-between w-full mb-1">
            <span className="text-[11px] font-medium text-slate-500 group-hover:text-slate-800 truncate">
              {card.label}
            </span>
            {card.icon}
          </div>
          <div className="flex items-baseline justify-between w-full">
            <span className={`text-xl font-bold font-mono ${card.color}`}>
              {card.value}
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200 font-medium">
              {card.badge}
            </span>
          </div>
        </button>
      ))}
    </div>
  );
};
