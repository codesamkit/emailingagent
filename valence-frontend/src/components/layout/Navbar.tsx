import React from 'react';
import { RefreshCw, Search, Sparkles, CheckCircle2, AlertCircle } from 'lucide-react';
import { Button } from '../ui/Button';
import { ValenceLogo } from '../ui/ValenceLogo';

interface NavbarProps {
  searchQuery: string;
  onSearchChange: (q: string) => void;
  onRefresh: () => void;
  isRefreshing: boolean;
  isBackendConnected: boolean;
  activeTab: string;
}

export const Navbar: React.FC<NavbarProps> = ({
  searchQuery,
  onSearchChange,
  onRefresh,
  isRefreshing,
  isBackendConnected,
}) => {
  return (
    <header className="h-16 border-b border-slate-200 bg-white/95 backdrop-blur-md px-4 sm:px-6 flex items-center justify-between sticky top-0 z-30 shadow-xs">
      {/* Left: Brand Identity with Official Valence Logo */}
      <div className="flex items-center gap-3">
        <ValenceLogo size="md" showWordmark={true} theme="light" />
        <div className="flex items-center gap-2 pl-2 border-l border-slate-200 hidden sm:flex">
          <span className="px-2 py-0.5 text-[10px] uppercase font-bold tracking-wider rounded-md bg-blue-50 text-blue-700 border border-blue-200">
            AI Agent
          </span>
          <span className="text-[11px] text-slate-500 font-medium hidden lg:inline">
            Intelligent Triage & Calendar-Aware Outlines
          </span>
        </div>
      </div>

      {/* Middle: Universal Search Bar */}
      <div className="flex-1 max-w-lg mx-4 hidden md:block">
        <div className="relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            placeholder="Search senders, subjects, summaries or AI tags... (Press /)"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full pl-9 pr-4 py-2 text-sm bg-slate-50 text-slate-900 placeholder-slate-400 rounded-xl border border-slate-200 focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:outline-none transition-all"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500 hover:text-slate-900 px-1.5 py-0.5 bg-slate-200 rounded"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Right: Agent Status & Quick Actions */}
      <div className="flex items-center gap-3">
        {/* Backend Connection Pill */}
        <div
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
            isBackendConnected
              ? 'bg-blue-50 text-blue-700 border-blue-200'
              : 'bg-slate-100 text-slate-600 border-slate-200'
          }`}
          title={
            isBackendConnected
              ? 'Connected to FastAPI backend on http://localhost:8000'
              : 'Running in Standalone Mock / Offline Demo mode'
          }
        >
          {isBackendConnected ? (
            <>
              <CheckCircle2 className="w-3.5 h-3.5 text-blue-600" />
              <span className="hidden sm:inline">Backend Live</span>
            </>
          ) : (
            <>
              <AlertCircle className="w-3.5 h-3.5 text-slate-500" />
              <span className="hidden sm:inline">Demo Mode</span>
            </>
          )}
        </div>

        {/* Sync / Refresh Button */}
        <Button
          variant="outline"
          size="sm"
          onClick={onRefresh}
          loading={isRefreshing}
          icon={<RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />}
        >
          <span className="hidden sm:inline">Sync Inbox</span>
        </Button>

        {/* AI Engine Status */}
        <div className="hidden lg:flex items-center gap-1.5 px-3 py-1 bg-gradient-to-r from-blue-50 via-indigo-50 to-slate-50 border border-blue-200 rounded-xl">
          <Sparkles className="w-3.5 h-3.5 text-indigo-600 animate-agent-pulse" />
          <span className="text-xs font-semibold text-slate-700">Claude 3.5 Sonnet</span>
        </div>
      </div>
    </header>
  );
};
