import React from 'react';
import { CalendarContext as CalendarContextType } from '../../types/email';
import { formatTimeSlot } from '../../utils/date';
import { Calendar, CheckCircle2 } from 'lucide-react';

interface CalendarContextProps {
  context: CalendarContextType;
}

export const CalendarContext: React.FC<CalendarContextProps> = ({ context }) => {
  return (
    <div className="p-4 rounded-xl bg-sky-50 border border-sky-200 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1 rounded-md bg-sky-600 text-white">
            <Calendar className="w-4 h-4" />
          </div>
          <span className="text-xs font-bold uppercase tracking-wider text-sky-800">
            Google Calendar Context
          </span>
        </div>
        <span className="text-[11px] text-sky-600 font-medium">
          {context.eventCount} events in window
        </span>
      </div>

      {/* Suggested Slots */}
      {context.suggestedSlots && context.suggestedSlots.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-sky-700">
            AI-Suggested Open Slots:
          </p>
          {context.suggestedSlots.slice(0, 3).map((slot, i) => (
            <div
              key={i}
              className="flex items-center gap-2.5 p-2.5 rounded-lg bg-white border border-sky-200 hover:border-sky-400 transition-all"
            >
              <CheckCircle2 className="w-4 h-4 text-sky-600 shrink-0" />
              <span className="text-xs font-medium text-slate-800 font-mono">
                {formatTimeSlot(slot.start, slot.end)}
              </span>
              <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 font-medium border border-sky-200">
                Free Slot
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Busy Blocks Summary */}
      {context.busyBlocks && context.busyBlocks.length > 0 && (
        <p className="text-[11px] text-sky-600 font-medium">
          {context.busyBlocks.length} conflicting busy block{context.busyBlocks.length !== 1 ? 's' : ''} detected and excluded from suggestions.
        </p>
      )}
    </div>
  );
};
