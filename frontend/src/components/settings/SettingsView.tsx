import React, { useState } from 'react';
import { Card } from '../ui/Card';
import { Button } from '../ui/Button';
import { Sliders, Users, Key, RefreshCw, Plus, Trash2, CheckCircle2, Sparkles, Mail, Calendar } from 'lucide-react';

interface SettingsViewProps {
  onResetData: () => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({ onResetData }) => {
  const [vipList, setVipList] = useState<string[]>([
    'alex.rivers@acmecorp.com',
    'marcus.vance@investor-partners.vc',
    'elena.rostova@cloudscale.io',
  ]);
  const [newVipEmail, setNewVipEmail] = useState('');
  const [isSaved, setIsSaved] = useState(false);

  const handleAddVip = () => {
    if (!newVipEmail.trim() || !newVipEmail.includes('@')) return;
    setVipList([...vipList, newVipEmail.trim().toLowerCase()]);
    setNewVipEmail('');
  };

  const handleSaveSettings = () => {
    setIsSaved(true);
    setTimeout(() => setIsSaved(false), 2500);
  };

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6 overflow-y-auto h-full bg-slate-50">
      <div>
        <h2 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
          <Sliders className="w-5 h-5 text-blue-600" />
          Valence Agent Rules & Integrations
        </h2>
        <p className="text-xs text-slate-500 mt-1">Customize VIP rules, AI scoring weights, and inspect OAuth API access.</p>
      </div>

      {/* Connected Accounts */}
      <Card padding="lg" className="space-y-4">
        <div className="flex items-center justify-between border-b border-slate-200 pb-3">
          <span className="text-sm font-bold text-slate-800 flex items-center gap-2">
            <Key className="w-4 h-4 text-blue-600" />
            Connected Accounts & OAuth Scopes
          </span>
          <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-50 text-blue-700 border border-blue-200">
            Auth: Full Tier
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[
            { icon: <Mail className="w-4 h-4 text-blue-600" />, label: 'Gmail API', account: 'iamsamkitshah@gmail.com', scopes: 'gmail.readonly, gmail.send (human-in-the-loop)' },
            { icon: <Calendar className="w-4 h-4 text-sky-600" />, label: 'Google Calendar API', account: 'Primary Calendar', scopes: 'calendar.freebusy, calendar.events' },
          ].map(({ icon, label, account, scopes }) => (
            <div key={label} className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1.5">
              <div className="flex items-center justify-between text-xs font-semibold text-slate-800">
                <span className="flex items-center gap-1.5">{icon}{label}</span>
                <span className="text-emerald-600 flex items-center gap-1 text-[11px] font-medium">
                  <CheckCircle2 className="w-3 h-3" />Connected
                </span>
              </div>
              <p className="text-xs text-slate-600 font-mono">{account}</p>
              <div className="text-[10px] text-slate-500 font-mono">{scopes}</div>
            </div>
          ))}
        </div>
      </Card>

      {/* VIP Contacts */}
      <Card padding="lg" className="space-y-4">
        <div className="flex items-center justify-between border-b border-slate-200 pb-3">
          <div>
            <span className="text-sm font-bold text-slate-800 flex items-center gap-2">
              <Users className="w-4 h-4 text-blue-600" />
              VIP Senders & Domains
            </span>
            <p className="text-xs text-slate-500 mt-0.5">Emails from these senders automatically receive a +25 pt importance boost.</p>
          </div>
          <span className="text-xs text-slate-500 font-mono">{vipList.length} VIPs</span>
        </div>

        <div className="space-y-2">
          {vipList.map((email) => (
            <div key={email} className="flex items-center justify-between p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-xs text-slate-700 font-mono">
              <span>{email}</span>
              <button onClick={() => setVipList(vipList.filter(e => e !== email))} className="text-slate-400 hover:text-red-500 p-1 rounded">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex gap-2">
          <input
            type="email"
            placeholder="Add new VIP email (e.g. partner@firm.com)..."
            value={newVipEmail}
            onChange={(e) => setNewVipEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAddVip(); }}
            className="flex-1 px-3 py-2 text-xs bg-white text-slate-800 placeholder-slate-400 rounded-lg border border-slate-200 focus:outline-none focus:border-blue-500"
          />
          <Button variant="secondary" size="sm" onClick={handleAddVip} icon={<Plus className="w-3.5 h-3.5" />}>Add VIP</Button>
        </div>
      </Card>

      {/* Model Config */}
      <Card padding="lg" className="space-y-4">
        <div className="flex items-center justify-between border-b border-slate-200 pb-3">
          <span className="text-sm font-bold text-slate-800 flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-indigo-600" />
            AI Intelligence Model Configuration
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="text-slate-500 block mb-1 font-medium">Inference Engine</label>
            <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-slate-800 font-mono">Claude 3.5 Sonnet (anthropic)</div>
          </div>
          <div>
            <label className="text-slate-500 block mb-1 font-medium">Outline Gating Policy</label>
            <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-slate-700 font-mono">Strict: Read + Non-NoReply</div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between pt-4 border-t border-slate-200 gap-3">
          <Button variant="outline" size="sm" onClick={onResetData} icon={<RefreshCw className="w-3.5 h-3.5" />}>Reset Demo Dataset</Button>
          <Button variant="primary" size="sm" onClick={handleSaveSettings} icon={isSaved ? <CheckCircle2 className="w-3.5 h-3.5" /> : undefined}>
            {isSaved ? 'Settings Saved!' : 'Save Rule Changes'}
          </Button>
        </div>
      </Card>
    </div>
  );
};
