import React from 'react';
import {
  Inbox,
  Calendar,
  BarChart3,
  Sliders,
  Flame,
  ShieldAlert,
  CalendarCheck,
  FileText,
  Clock,
  Mail,
  ListChecks,
} from 'lucide-react';
import { InboxStats } from '../../types/email';

interface SidebarProps {
  activeTab: 'inbox' | 'calendar' | 'todo' | 'analytics' | 'settings';
  onTabChange: (tab: 'inbox' | 'calendar' | 'todo' | 'analytics' | 'settings') => void;
  stats: InboxStats;
  onQuickFilter: (filterType: string) => void;
  activeQuickFilter: string;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onTabChange,
  stats,
  onQuickFilter,
  activeQuickFilter,
}) => {
  return (
    <aside className="w-64 border-r border-slate-200 bg-slate-50/90 flex flex-col justify-between h-[calc(100vh-4rem)] p-3 shrink-0 select-none">
      {/* Top Section: Navigation and Quick Filters */}
      <div className="space-y-6">
        {/* Main Navigation Views */}
        <div>
          <div className="px-3 mb-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Workspace
          </div>
          <nav className="space-y-1">
            <button
              onClick={() => onTabChange('inbox')}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'inbox'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md'
                  : 'text-slate-700 hover:bg-slate-200/70 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Inbox className="w-4 h-4" />
                <span>Inbox Triage</span>
              </div>
              {stats.unread > 0 && (
                <span
                  className={`text-xs px-2 py-0.5 rounded-full font-bold ${
                    activeTab === 'inbox'
                      ? 'bg-white/25 text-white'
                      : 'bg-blue-100 text-blue-700 font-semibold'
                  }`}
                >
                  {stats.unread}
                </span>
              )}
            </button>

            <button
              onClick={() => onTabChange('calendar')}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'calendar'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md'
                  : 'text-slate-700 hover:bg-slate-200/70 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Calendar className="w-4 h-4" />
                <span>Smart Calendar</span>
              </div>
              {stats.scheduling > 0 && (
                <span className="text-xs px-1.5 py-0.5 rounded-md bg-blue-100 text-blue-700 font-semibold">
                  {stats.scheduling}
                </span>
              )}
            </button>

            <button
              onClick={() => onTabChange('todo')}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'todo'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md'
                  : 'text-slate-700 hover:bg-slate-200/70 hover:text-slate-900'
              }`}
            >
              <ListChecks className="w-4 h-4" />
              <span>To-Do</span>
            </button>

            <button
              onClick={() => onTabChange('analytics')}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'analytics'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md'
                  : 'text-slate-700 hover:bg-slate-200/70 hover:text-slate-900'
              }`}
            >
              <BarChart3 className="w-4 h-4" />
              <span>Agent Insights</span>
            </button>

            <button
              onClick={() => onTabChange('settings')}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm font-medium transition-all ${
                activeTab === 'settings'
                  ? 'bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md'
                  : 'text-slate-700 hover:bg-slate-200/70 hover:text-slate-900'
              }`}
            >
              <Sliders className="w-4 h-4" />
              <span>Rules & VIPs</span>
            </button>
          </nav>
        </div>

        {/* Quick Triage Filters */}
        <div>
          <div className="px-3 mb-2 flex items-center justify-between">
            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
              AI Smart Filters
            </span>
            {activeQuickFilter !== 'all' && (
              <button
                onClick={() => onQuickFilter('all')}
                className="text-[10px] text-blue-600 hover:text-blue-700 underline font-medium"
              >
                Reset
              </button>
            )}
          </div>
          <div className="space-y-1">
            <button
              onClick={() => onQuickFilter('urgent_unread')}
              className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                activeQuickFilter === 'urgent_unread'
                  ? 'bg-blue-100/90 text-blue-800 border border-blue-300'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2">
                <Flame className="w-3.5 h-3.5 text-blue-600" />
                <span>Urgent Priority</span>
              </div>
              <span className="font-mono text-[11px] font-bold text-blue-600">
                {stats.byLevel?.urgent ?? 0}
              </span>
            </button>

            <button
              onClick={() => onQuickFilter('scheduling')}
              className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                activeQuickFilter === 'scheduling'
                  ? 'bg-blue-100/90 text-blue-800 border border-blue-300'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2">
                <CalendarCheck className="w-3.5 h-3.5 text-sky-600" />
                <span>Scheduling Asks</span>
              </div>
              <span className="font-mono text-[11px] font-bold text-sky-600">{stats.scheduling}</span>
            </button>

            <button
              onClick={() => onQuickFilter('outlines')}
              className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                activeQuickFilter === 'outlines'
                  ? 'bg-indigo-100/90 text-indigo-800 border border-indigo-300'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2">
                <FileText className="w-3.5 h-3.5 text-indigo-600" />
                <span>AI Outlines Ready</span>
              </div>
              <span className="font-mono text-[11px] font-bold text-indigo-600">{stats.withOutline}</span>
            </button>

            <button
              onClick={() => onQuickFilter('noreply')}
              className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                activeQuickFilter === 'noreply'
                  ? 'bg-slate-200 text-slate-800 border border-slate-300'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2">
                <ShieldAlert className="w-3.5 h-3.5 text-slate-500" />
                <span>No-Reply Shielded</span>
              </div>
              <span className="font-mono text-[11px] font-bold text-slate-600">{stats.noReply}</span>
            </button>

            <button
              onClick={() => onQuickFilter('stale')}
              className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                activeQuickFilter === 'stale'
                  ? 'bg-amber-100 text-amber-900 border border-amber-300'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-2">
                <Clock className="w-3.5 h-3.5 text-amber-600" />
                <span>Stale Outlines</span>
              </div>
              <span className="font-mono text-[11px] font-bold text-amber-700">1</span>
            </button>
          </div>
        </div>
      </div>

      {/* Bottom Section: Account Connections & AI Engine Status */}
      <div className="pt-4 border-t border-slate-200 space-y-3">
        <div className="p-3 rounded-xl bg-white border border-slate-200 shadow-xs space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-slate-800 flex items-center gap-1.5">
              <Mail className="w-3.5 h-3.5 text-blue-600" />
              Connected Inbox
            </span>
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
          </div>
          <p className="text-xs text-slate-600 font-mono truncate">iamsamkitshah@gmail.com</p>
          <div className="flex items-center gap-2 text-[10px] text-slate-500">
            <span>OAuth: Active</span>
            <span>•</span>
            <span>GCal: Synced</span>
          </div>
        </div>

        <div className="text-[10px] text-slate-500 text-center flex items-center justify-center gap-1.5 font-medium">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-600" />
          <span>Valence AI Autonomous Engine</span>
          <span className="text-indigo-600 font-mono font-bold">v0.1.0</span>
        </div>
      </div>
    </aside>
  );
};
