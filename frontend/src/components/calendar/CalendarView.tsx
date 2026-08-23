import React, { useState } from 'react';
import { Card } from '../ui/Card';
import { Calendar, ChevronLeft, ChevronRight, CalendarCheck } from 'lucide-react';

export const CalendarView: React.FC = () => {
  const [currentWeekOffset, setCurrentWeekOffset] = useState(0);

  const getWeekDays = (offset: number) => {
    const now = new Date();
    const startOfWeek = new Date(now);
    startOfWeek.setDate(now.getDate() - now.getDay() + 1 + offset * 7);
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(startOfWeek);
      d.setDate(startOfWeek.getDate() + i);
      return d;
    });
  };

  const weekDays = getWeekDays(currentWeekOffset);
  const today = new Date();

  const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  const mockEvents: { day: number; title: string; time: string; color: string }[] = [
    { day: 1, title: 'Team Standup', time: '9:00 AM', color: 'bg-blue-100 border-blue-400 text-blue-800' },
    { day: 1, title: 'Product Roadmap Review', time: '11:00 AM', color: 'bg-indigo-100 border-indigo-400 text-indigo-800' },
    { day: 2, title: 'Q3 Partnership Sync', time: '2:00 PM', color: 'bg-blue-100 border-blue-400 text-blue-800' },
    { day: 3, title: 'Investor Call — Marcus Vance', time: '10:30 AM', color: 'bg-purple-100 border-purple-400 text-purple-800' },
    { day: 4, title: 'Design System Review', time: '3:00 PM', color: 'bg-sky-100 border-sky-400 text-sky-800' },
  ];

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 overflow-y-auto h-full bg-slate-50">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <Calendar className="w-5 h-5 text-blue-600" />
            Smart Calendar — Week View
          </h2>
          <p className="text-xs text-slate-500 mt-1">Auto-synced with Google Calendar. Open slots suggested for scheduling emails.</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setCurrentWeekOffset(currentWeekOffset - 1)} className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button onClick={() => setCurrentWeekOffset(0)} className="px-3 py-1.5 text-xs font-medium bg-white border border-slate-200 text-slate-600 hover:text-slate-900 rounded-lg">
            Today
          </button>
          <button onClick={() => setCurrentWeekOffset(currentWeekOffset + 1)} className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900">
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Week Grid */}
      <Card padding="none" className="overflow-hidden">
        <div className="grid grid-cols-7 border-b border-slate-200">
          {weekDays.map((day, i) => {
            const isToday = day.toDateString() === today.toDateString();
            return (
              <div key={i} className={`text-center py-3 border-r border-slate-200 last:border-r-0 ${isToday ? 'bg-blue-50' : 'bg-white'}`}>
                <p className={`text-[11px] font-semibold uppercase tracking-wider ${isToday ? 'text-blue-600' : 'text-slate-500'}`}>
                  {dayNames[i]}
                </p>
                <p className={`text-xl font-bold mt-0.5 ${isToday ? 'text-blue-700 bg-blue-600 text-white w-8 h-8 flex items-center justify-center rounded-full mx-auto' : 'text-slate-800'}`}>
                  {day.getDate()}
                </p>
              </div>
            );
          })}
        </div>

        <div className="grid grid-cols-7 min-h-[320px]">
          {weekDays.map((day, i) => {
            const isToday = day.toDateString() === today.toDateString();
            const dayEvents = mockEvents.filter(e => e.day === i + 1);
            return (
              <div key={i} className={`border-r border-slate-200 last:border-r-0 p-2 space-y-1.5 ${isToday ? 'bg-blue-50/40' : 'bg-white'}`}>
                {dayEvents.map((event, j) => (
                  <div key={j} className={`p-2 rounded-lg border-l-2 text-xs font-medium ${event.color}`}>
                    <p className="font-semibold truncate">{event.title}</p>
                    <p className="text-[10px] opacity-80 font-mono">{event.time}</p>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </Card>

      {/* Scheduling Suggestions */}
      <Card padding="md" className="space-y-3">
        <div className="flex items-center gap-2 border-b border-slate-200 pb-2">
          <CalendarCheck className="w-4 h-4 text-sky-600" />
          <span className="text-sm font-bold text-slate-800">AI-Suggested Open Meeting Slots</span>
          <span className="text-[11px] text-slate-500">— sourced from scheduling ask emails</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            { label: 'Tomorrow', time: '2:00 PM – 2:45 PM', email: 'Alex Rivers (Q3 Sync)' },
            { label: 'In 2 Days', time: '10:30 AM – 11:15 AM', email: 'Alex Rivers (Q3 Sync)' },
            { label: 'In 2 Days', time: '4:00 PM – 4:45 PM', email: 'Alex Rivers (Q3 Sync)' },
          ].map((slot, i) => (
            <div key={i} className="p-3 rounded-lg bg-sky-50 border border-sky-200 space-y-1 hover:border-sky-400 hover:shadow-xs transition-all cursor-pointer">
              <p className="text-[11px] font-bold uppercase tracking-wider text-sky-700">{slot.label}</p>
              <p className="text-sm font-bold text-slate-900 font-mono">{slot.time}</p>
              <p className="text-[11px] text-sky-700 font-medium truncate">For: {slot.email}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
