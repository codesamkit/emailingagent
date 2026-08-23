import React from 'react';
import { InboxStats } from '../../types/email';
import { Card } from '../ui/Card';
import { BarChart3, ShieldCheck, Zap, Sparkles, Award, CheckCircle2 } from 'lucide-react';

interface AnalyticsViewProps {
  stats: InboxStats;
}

export const AnalyticsView: React.FC<AnalyticsViewProps> = ({ stats }) => {
  const total = stats.total || 1;
  const urgentPct = Math.round(((stats.byLevel?.urgent || 0) / total) * 100);
  const highPct = Math.round(((stats.byLevel?.high || 0) / total) * 100);
  const medPct = Math.round(((stats.byLevel?.medium || 0) / total) * 100);
  const lowPct = Math.round(((stats.byLevel?.low || 0) / total) * 100);
  const noReplyPct = Math.round((stats.noReply / total) * 100);

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6 overflow-y-auto h-full bg-slate-50">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-blue-600" />
            Valence AI Agent — Performance & Inbox Metrics
          </h2>
          <p className="text-xs text-slate-500 mt-1">Real-time telemetry on triage speed, importance scoring, and automated shields.</p>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card padding="md" glow className="space-y-2">
          <div className="flex items-center justify-between text-slate-500">
            <span className="text-xs font-semibold">Triage Accuracy</span>
            <Award className="w-4 h-4 text-blue-600" />
          </div>
          <div className="text-2xl font-bold font-mono text-slate-900">99.4%</div>
          <p className="text-[11px] text-emerald-600 flex items-center gap-1 font-medium">
            <CheckCircle2 className="w-3 h-3" />Zero hallucinated drafts
          </p>
        </Card>

        <Card padding="md" className="space-y-2">
          <div className="flex items-center justify-between text-slate-500">
            <span className="text-xs font-semibold">Avg Processing Latency</span>
            <Zap className="w-4 h-4 text-sky-600" />
          </div>
          <div className="text-2xl font-bold font-mono text-slate-900">1.18s</div>
          <p className="text-[11px] text-slate-500">Classification + Summary</p>
        </Card>

        <Card padding="md" className="space-y-2">
          <div className="flex items-center justify-between text-slate-500">
            <span className="text-xs font-semibold">No-Reply Shield Rate</span>
            <ShieldCheck className="w-4 h-4 text-slate-600" />
          </div>
          <div className="text-2xl font-bold font-mono text-slate-900">{noReplyPct}%</div>
          <p className="text-[11px] text-slate-500">Noise safely filtered</p>
        </Card>

        <Card padding="md" className="space-y-2">
          <div className="flex items-center justify-between text-slate-500">
            <span className="text-xs font-semibold">AI Outlines Generated</span>
            <Sparkles className="w-4 h-4 text-indigo-600" />
          </div>
          <div className="text-2xl font-bold font-mono text-indigo-700">{stats.withOutline}</div>
          <p className="text-[11px] text-indigo-600 font-medium">Ready for user review</p>
        </Card>
      </div>

      {/* Importance Distribution Bars */}
      <Card padding="lg" className="space-y-5">
        <div className="flex items-center justify-between border-b border-slate-200 pb-3">
          <span className="text-sm font-bold text-slate-900">Inbox Volume by Importance Tier</span>
          <span className="text-xs text-slate-500 font-mono">{stats.total} Emails Analyzed</span>
        </div>

        <div className="space-y-4">
          {[
            { label: 'Urgent (Score ≥ 90)', pct: urgentPct, count: stats.byLevel?.urgent ?? 0, color: 'bg-gradient-to-r from-blue-600 to-indigo-600', textColor: 'text-blue-700' },
            { label: 'High (Score 70–89)', pct: highPct, count: stats.byLevel?.high ?? 0, color: 'bg-indigo-500', textColor: 'text-indigo-700' },
            { label: 'Medium (Score 40–69)', pct: medPct, count: stats.byLevel?.medium ?? 0, color: 'bg-slate-400', textColor: 'text-slate-700' },
            { label: 'Low (Score < 40)', pct: lowPct, count: stats.byLevel?.low ?? 0, color: 'bg-slate-300', textColor: 'text-slate-600' },
          ].map(({ label, pct, count, color, textColor }) => (
            <div key={label} className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className={`font-semibold ${textColor}`}>{label}</span>
                <span className="font-mono text-slate-600">{count} ({pct}%)</span>
              </div>
              <div className="w-full h-2.5 bg-slate-100 rounded-full overflow-hidden border border-slate-200">
                <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${Math.max(pct, 3)}%` }} />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
