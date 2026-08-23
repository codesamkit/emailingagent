import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Card } from '../ui/Card';
import {
  Calendar,
  ChevronLeft,
  ChevronRight,
  CalendarCheck,
  Loader2,
  RefreshCw,
  CloudOff,
} from 'lucide-react';
import { CalendarEvent, CalendarWindow } from '../../types/email';
import { api } from '../../services/api';

// One forward window is fetched and every visible week is sliced out of it, so
// navigating between weeks costs no extra Google calls. The API caps `days` at
// 30, which is therefore also how far ahead this view can look.
const WINDOW_DAYS = 30;

// Assigned by position so the grid stays readable. Deliberately not derived
// from the event's content — a color that looked like it meant something
// (priority, category) would be inventing information the API doesn't send.
const EVENT_COLORS = [
  'bg-blue-100 border-blue-400 text-blue-800',
  'bg-indigo-100 border-indigo-400 text-indigo-800',
  'bg-sky-100 border-sky-400 text-sky-800',
  'bg-purple-100 border-purple-400 text-purple-800',
];

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

/** All-day events arrive as UTC midnight; reading them locally would shift
 *  them a day west of UTC, so those are read off the UTC parts. */
const eventDay = (event: CalendarEvent): Date | null => {
  if (!event.start) return null;
  const parsed = new Date(event.start);
  if (Number.isNaN(parsed.getTime())) return null;
  return event.allDay
    ? new Date(parsed.getUTCFullYear(), parsed.getUTCMonth(), parsed.getUTCDate())
    : startOfDay(parsed);
};

const timeLabel = (event: CalendarEvent): string => {
  if (event.allDay) return 'All day';
  if (!event.start) return '';
  const opts: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' };
  const start = new Date(event.start).toLocaleTimeString(undefined, opts);
  if (!event.end) return start;
  return `${start} – ${new Date(event.end).toLocaleTimeString(undefined, opts)}`;
};

const slotLabel = (iso: string): string =>
  new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });

export const CalendarView: React.FC = () => {
  const [weekOffset, setWeekOffset] = useState(0);
  const [data, setData] = useState<CalendarWindow | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async () => {
    setIsLoading(true);
    setData(await api.getCalendar(WINDOW_DAYS));
    setIsLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const today = startOfDay(new Date());

  // Monday-based week containing today, shifted by the offset.
  const weekDays = useMemo(() => {
    const monday = new Date(today);
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) + weekOffset * 7);
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(monday);
      d.setDate(monday.getDate() + i);
      return d;
    });
  }, [today.getTime(), weekOffset]);

  // Events bucketed by day so each column is a lookup rather than a scan.
  const byDay = useMemo(() => {
    const map = new Map<number, CalendarEvent[]>();
    for (const event of data?.events || []) {
      const day = eventDay(event);
      if (!day) continue;
      const key = day.getTime();
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(event);
    }
    return map;
  }, [data]);

  // The window only runs forward from today, so weeks outside it hold no data
  // and navigating to them would imply an empty calendar rather than an
  // unfetched one. The buttons stop at the edges instead.
  const maxWeekOffset = Math.floor((WINDOW_DAYS - 1) / 7);
  const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const suggestedSlots = data?.suggestedSlots || [];

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 overflow-y-auto h-full bg-slate-50">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <Calendar className="w-5 h-5 text-blue-600" />
            Calendar — Week View
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            {data?.connected
              ? `Your Google Calendar, next ${WINDOW_DAYS} days — ${data.eventCount} event${
                  data.eventCount === 1 ? '' : 's'
                }.`
              : 'Reading your Google Calendar…'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={isLoading}
            title="Refresh from Google Calendar"
            className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900 disabled:opacity-50"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
          </button>
          <button
            onClick={() => setWeekOffset(weekOffset - 1)}
            disabled={weekOffset <= 0}
            className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900 disabled:opacity-40"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button
            onClick={() => setWeekOffset(0)}
            className="px-3 py-1.5 text-xs font-medium bg-white border border-slate-200 text-slate-600 hover:text-slate-900 rounded-lg"
          >
            Today
          </button>
          <button
            onClick={() => setWeekOffset(weekOffset + 1)}
            disabled={weekOffset >= maxWeekOffset}
            className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900 disabled:opacity-40"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {data && !data.connected && (
        <Card padding="md">
          <div className="flex items-start gap-3">
            <CloudOff className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-slate-800">Calendar not connected</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {data.error} Start the backend, and run{' '}
                <code className="px-1 py-0.5 rounded bg-slate-100 font-mono text-[11px]">
                  python -m calendaring.cli auth
                </code>{' '}
                if this is the first run.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Week Grid */}
      <Card padding="none" className="overflow-hidden">
        <div className="grid grid-cols-7 border-b border-slate-200">
          {weekDays.map((day, i) => {
            const isToday = day.getTime() === today.getTime();
            return (
              <div
                key={i}
                className={`text-center py-3 border-r border-slate-200 last:border-r-0 ${
                  isToday ? 'bg-blue-50' : 'bg-white'
                }`}
              >
                <p
                  className={`text-[11px] font-semibold uppercase tracking-wider ${
                    isToday ? 'text-blue-600' : 'text-slate-500'
                  }`}
                >
                  {dayNames[i]}
                </p>
                <p
                  className={`text-xl font-bold mt-0.5 ${
                    isToday
                      ? 'text-blue-700 bg-blue-600 text-white w-8 h-8 flex items-center justify-center rounded-full mx-auto'
                      : 'text-slate-800'
                  }`}
                >
                  {day.getDate()}
                </p>
              </div>
            );
          })}
        </div>

        <div className="grid grid-cols-7 min-h-[320px]">
          {weekDays.map((day, i) => {
            const isToday = day.getTime() === today.getTime();
            const dayEvents = byDay.get(day.getTime()) || [];
            return (
              <div
                key={i}
                className={`border-r border-slate-200 last:border-r-0 p-2 space-y-1.5 ${
                  isToday ? 'bg-blue-50/40' : 'bg-white'
                }`}
              >
                {dayEvents.map((event, j) => (
                  <div
                    key={j}
                    title={event.summary || undefined}
                    className={`p-2 rounded-lg border-l-2 text-xs font-medium ${
                      EVENT_COLORS[(i + j) % EVENT_COLORS.length]
                    }`}
                  >
                    <p className="font-semibold truncate">{event.summary || '(busy)'}</p>
                    <p className="text-[10px] opacity-80 font-mono">{timeLabel(event)}</p>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </Card>

      {isLoading && !data && (
        <p className="text-xs text-slate-500 flex items-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading your calendar…
        </p>
      )}

      {data?.connected && data.eventCount === 0 && (
        <p className="text-xs text-slate-500">
          Nothing scheduled in the next {WINDOW_DAYS} days.
        </p>
      )}

      {/* Suggested slots. Only ever shows what the API actually returned --
          get_calendar_context leaves suggested_slots empty (filling it is
          suggest_available_slots' job), so this is normally an empty state. */}
      <Card padding="md" className="space-y-3">
        <div className="flex items-center gap-2 border-b border-slate-200 pb-2">
          <CalendarCheck className="w-4 h-4 text-sky-600" />
          <span className="text-sm font-bold text-slate-800">Suggested Open Meeting Slots</span>
          <span className="text-[11px] text-slate-500">— from scheduling emails</span>
        </div>
        {suggestedSlots.length === 0 ? (
          <p className="text-xs text-slate-500">
            No suggested slots. These are computed per email — open a scheduling email to see times
            proposed against your calendar.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {suggestedSlots.map((slot, i) => (
              <div
                key={i}
                className="p-3 rounded-lg bg-sky-50 border border-sky-200 space-y-1 hover:border-sky-400 hover:shadow-xs transition-all"
              >
                <p className="text-sm font-bold text-slate-900 font-mono">{slotLabel(slot.start)}</p>
                <p className="text-[11px] text-sky-700 font-medium">
                  until{' '}
                  {new Date(slot.end).toLocaleTimeString(undefined, {
                    hour: 'numeric',
                    minute: '2-digit',
                  })}
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};
