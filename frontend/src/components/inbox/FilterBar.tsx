import React from 'react';
import { FilterOptions } from '../../types/email';
import {
  ArrowUpDown,
  Calendar,
  FileText,
  ShieldAlert,
  Clock,
} from 'lucide-react';

interface FilterBarProps {
  filters: FilterOptions;
  onChange: (newFilters: FilterOptions) => void;
  totalCount: number;
}

export const FilterBar: React.FC<FilterBarProps> = ({ filters, onChange, totalCount }) => {
  const update = (partial: Partial<FilterOptions>) => {
    onChange({ ...filters, ...partial });
  };

  return (
    <div className="p-3 bg-white border-b border-slate-200 space-y-2.5">
      {/* Top Row: Read Status, Importance Level, and Sort */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {/* Read Status Segments */}
        <div className="flex items-center bg-slate-100 rounded-lg p-0.5 border border-slate-200 text-xs">
          <button
            onClick={() => update({ readStatus: 'all' })}
            className={`px-2.5 py-1 rounded-md font-medium transition-all ${
              filters.readStatus === 'all'
                ? 'bg-white text-blue-700 shadow-xs font-semibold'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            All ({totalCount})
          </button>
          <button
            onClick={() => update({ readStatus: 'unread' })}
            className={`px-2.5 py-1 rounded-md font-medium transition-all ${
              filters.readStatus === 'unread'
                ? 'bg-white text-blue-700 shadow-xs font-semibold'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Unread
          </button>
          <button
            onClick={() => update({ readStatus: 'read' })}
            className={`px-2.5 py-1 rounded-md font-medium transition-all ${
              filters.readStatus === 'read'
                ? 'bg-white text-blue-700 shadow-xs font-semibold'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Read
          </button>
        </div>

        {/* Importance Level Selector */}
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-slate-500 font-medium hidden sm:inline">Priority:</span>
          <select
            value={filters.importance}
            onChange={(e) => update({ importance: e.target.value as any })}
            className="px-2.5 py-1 text-xs bg-white text-slate-700 rounded-lg border border-slate-200 focus:outline-none focus:border-blue-500 font-medium"
          >
            <option value="all">All Priorities</option>
            <option value="urgent">Urgent Priority</option>
            <option value="high">High Priority</option>
            <option value="medium">Medium Priority</option>
            <option value="low">Low Priority</option>
          </select>
        </div>

        {/* Sorting Dropdown */}
        <div className="flex items-center gap-1.5 ml-auto">
          <ArrowUpDown className="w-3.5 h-3.5 text-slate-400" />
          <select
            value={filters.sortBy}
            onChange={(e) => update({ sortBy: e.target.value as any })}
            className="px-2.5 py-1 text-xs bg-white text-slate-700 rounded-lg border border-slate-200 focus:outline-none focus:border-blue-500 font-medium"
          >
            <option value="importance">Sort: Importance Score</option>
            <option value="received_at">Sort: Date Received</option>
            <option value="sender">Sort: Sender Name</option>
            <option value="subject">Sort: Subject</option>
          </select>
          <button
            onClick={() => update({ descending: !filters.descending })}
            className="p-1 text-xs rounded-lg bg-white border border-slate-200 text-slate-600 hover:text-slate-900 shadow-xs"
            title={filters.descending ? 'Descending (High to Low)' : 'Ascending (Low to High)'}
          >
            {filters.descending ? '↓' : '↑'}
          </button>
        </div>
      </div>

      {/* Bottom Row: Feature Toggle Pills */}
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        {/* Scheduling Toggle */}
        <button
          onClick={() => update({ scheduling: filters.scheduling === true ? null : true })}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
            filters.scheduling === true
              ? 'bg-blue-50 text-blue-700 border-blue-400 font-semibold shadow-xs'
              : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:text-slate-900'
          }`}
        >
          <Calendar className="w-3 h-3 text-sky-600" />
          <span>Scheduling Asks</span>
        </button>

        {/* Has AI Outline Toggle */}
        <button
          onClick={() => update({ hasOutline: filters.hasOutline === true ? null : true })}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
            filters.hasOutline === true
              ? 'bg-indigo-50 text-indigo-700 border-indigo-400 font-semibold shadow-xs'
              : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:text-slate-900'
          }`}
        >
          <FileText className="w-3 h-3 text-indigo-600" />
          <span>Has AI Outline</span>
        </button>

        {/* No-Reply Toggle */}
        <button
          onClick={() => update({ noReply: filters.noReply === true ? null : true })}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
            filters.noReply === true
              ? 'bg-slate-200 text-slate-800 border-slate-400 font-semibold shadow-xs'
              : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:text-slate-900'
          }`}
        >
          <ShieldAlert className="w-3 h-3 text-slate-500" />
          <span>No-Reply Flagged</span>
        </button>

        {/* Stale Thread Toggle */}
        <button
          onClick={() => update({ isStale: filters.isStale === true ? null : true })}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
            filters.isStale === true
              ? 'bg-amber-50 text-amber-800 border-amber-400 font-semibold shadow-xs'
              : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:text-slate-900'
          }`}
        >
          <Clock className="w-3 h-3 text-amber-600" />
          <span>Stale Outlines</span>
        </button>

        {/* Clear all filters shortcut if any active */}
        {(filters.readStatus !== 'all' ||
          filters.importance !== 'all' ||
          filters.scheduling !== null ||
          filters.hasOutline !== null ||
          filters.noReply !== null ||
          filters.isStale !== null ||
          filters.search !== '') && (
          <button
            onClick={() =>
              onChange({
                search: '',
                readStatus: 'all',
                importance: 'all',
                noReply: null,
                scheduling: null,
                hasOutline: null,
                isStale: null,
                sortBy: 'importance',
                descending: true,
              })
            }
            className="text-[11px] text-blue-600 hover:text-blue-800 underline font-medium ml-auto px-2 py-0.5"
          >
            Reset All Filters
          </button>
        )}
      </div>
    </div>
  );
};
