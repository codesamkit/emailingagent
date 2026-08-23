import React, { useState, useEffect, useCallback } from 'react';
import { EmailItem, FilterOptions, InboxStats } from './types/email';
import { api } from './services/api';
import { Navbar } from './components/layout/Navbar';
import { Sidebar } from './components/layout/Sidebar';
import { StatsBar } from './components/layout/StatsBar';
import { FilterBar } from './components/inbox/FilterBar';
import { EmailList } from './components/inbox/EmailList';
import { EmailDetail } from './components/detail/EmailDetail';
import { CalendarView } from './components/calendar/CalendarView';
import { AnalyticsView } from './components/analytics/AnalyticsView';
import { SettingsView } from './components/settings/SettingsView';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'inbox' | 'calendar' | 'analytics' | 'settings'>('inbox');
  const [emails, setEmails] = useState<EmailItem[]>([]);
  const [selectedEmail, setSelectedEmail] = useState<EmailItem | null>(null);
  const [stats, setStats] = useState<InboxStats>({
    total: 0,
    unread: 0,
    noReply: 0,
    scheduling: 0,
    withOutline: 0,
    unprocessed: 0,
    byLevel: { urgent: 0, high: 0, medium: 0, low: 0 },
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isBackendConnected, setIsBackendConnected] = useState(false);
  const [activeQuickFilter, setActiveQuickFilter] = useState('all');

  const [filters, setFilters] = useState<FilterOptions>({
    search: '',
    readStatus: 'all',
    importance: 'all',
    noReply: null,
    scheduling: null,
    hasOutline: null,
    isStale: null,
    sortBy: 'importance',
    descending: true,
  });

  // Load emails & stats
  const loadData = useCallback(async (currentFilters = filters) => {
    try {
      const isLive = await api.isBackendAvailable();
      setIsBackendConnected(isLive);

      const [emailData, statsData] = await Promise.all([
        api.getEmails(currentFilters),
        api.getStats(),
      ]);

      setEmails(emailData.emails);
      setStats(statsData);

      // Auto-select first email if none selected or if selected email is no longer in list
      if (emailData.emails.length > 0) {
        setSelectedEmail((prev) => {
          if (!prev) return emailData.emails[0];
          const exists = emailData.emails.find((e) => e.emailId === prev.emailId);
          return exists || emailData.emails[0];
        });
      } else {
        setSelectedEmail(null);
      }
    } catch (err) {
      console.error('Failed to load data:', err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [filters]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Sync / Refresh Handler
  const handleRefresh = async () => {
    setIsRefreshing(true);
    await loadData();
  };

  // Filter change handler
  const handleFilterChange = (newFilters: FilterOptions) => {
    setFilters(newFilters);
    loadData(newFilters);
  };

  // Quick Filter Sidebar click handler
  const handleQuickFilter = (type: string) => {
    setActiveQuickFilter(type);
    setActiveTab('inbox');

    let updated: Partial<FilterOptions> = {
      search: '',
      readStatus: 'all',
      importance: 'all',
      noReply: null,
      scheduling: null,
      hasOutline: null,
      isStale: null,
    };

    switch (type) {
      case 'urgent_unread':
        updated = { ...updated, importance: 'urgent' };
        break;
      case 'scheduling':
        updated = { ...updated, scheduling: true };
        break;
      case 'outlines':
        updated = { ...updated, hasOutline: true };
        break;
      case 'noreply':
        updated = { ...updated, noReply: true };
        break;
      case 'stale':
        updated = { ...updated, isStale: true };
        break;
      default:
        break;
    }

    const merged = { ...filters, ...updated };
    setFilters(merged);
    loadData(merged);
  };

  // Outline Save Handler
  const handleSaveOutline = async (emailId: string, bullets: string[]) => {
    const updated = await api.updateOutline(emailId, bullets);
    setEmails((prev) => prev.map((e) => (e.emailId === emailId ? updated : e)));
    setSelectedEmail(updated);
    const updatedStats = await api.getStats();
    setStats(updatedStats);
  };

  // Toggle Read Status Handler
  const handleToggleRead = async (emailId: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    const updated = await api.toggleReadStatus(emailId);
    setEmails((prev) => prev.map((item) => (item.emailId === emailId ? updated : item)));
    if (selectedEmail?.emailId === emailId) {
      setSelectedEmail(updated);
    }
    const updatedStats = await api.getStats();
    setStats(updatedStats);
  };

  // Reset demo dataset
  const handleResetData = () => {
    api.resetDemoData();
    handleQuickFilter('all');
  };

  // Keyboard navigation shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger shortcuts if inside textarea or input
      if (['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement).tagName)) return;

      if (e.key === 'j' || e.key === 'ArrowDown') {
        // Next email
        if (emails.length > 0 && selectedEmail) {
          const currentIndex = emails.findIndex((item) => item.emailId === selectedEmail.emailId);
          if (currentIndex < emails.length - 1) {
            setSelectedEmail(emails[currentIndex + 1]);
          }
        }
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        // Previous email
        if (emails.length > 0 && selectedEmail) {
          const currentIndex = emails.findIndex((item) => item.emailId === selectedEmail.emailId);
          if (currentIndex > 0) {
            setSelectedEmail(emails[currentIndex - 1]);
          }
        }
      } else if (e.key === 'r' && selectedEmail) {
        // Toggle read
        handleToggleRead(selectedEmail.emailId);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [emails, selectedEmail]);

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-valens-black font-sans">
      {/* Top Navbar */}
      <Navbar
        searchQuery={filters.search}
        onSearchChange={(q) => handleFilterChange({ ...filters, search: q })}
        onRefresh={handleRefresh}
        isRefreshing={isRefreshing}
        isBackendConnected={isBackendConnected}
        activeTab={activeTab}
      />

      {/* Main Workspace Layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar */}
        <Sidebar
          activeTab={activeTab}
          onTabChange={setActiveTab}
          stats={stats}
          onQuickFilter={handleQuickFilter}
          activeQuickFilter={activeQuickFilter}
        />

        {/* Center / Right Content Views */}
        <main className="flex-1 flex flex-col overflow-hidden">
          {activeTab === 'inbox' && (
            <>
              {/* KPI Ribbon */}
              <StatsBar stats={stats} onFilterClick={handleQuickFilter} />

              {/* Master-Detail Split Screen for Inbox */}
              <div className="flex-1 flex flex-col md:flex-row overflow-hidden">
                {/* Left Feed Pane (Email List & Filters) */}
                <div className="w-full md:w-[420px] lg:w-[460px] flex flex-col border-r border-slate-800 shrink-0 h-full overflow-hidden bg-valens-surface/40">
                  <FilterBar
                    filters={filters}
                    onChange={handleFilterChange}
                    totalCount={emails.length}
                  />
                  <EmailList
                    emails={emails}
                    selectedEmailId={selectedEmail?.emailId ?? null}
                    onSelectEmail={setSelectedEmail}
                    onToggleRead={handleToggleRead}
                    isLoading={isLoading}
                  />
                </div>

                {/* Right Inspector Pane (Full Deep Reading & AI Reasoning) */}
                <div className="flex-1 h-full overflow-hidden">
                  {selectedEmail && (
                    <EmailDetail
                      email={selectedEmail}
                      onSaveOutline={handleSaveOutline}
                      onToggleRead={handleToggleRead}
                    />
                  )}
                </div>
              </div>
            </>
          )}

          {activeTab === 'calendar' && (
            <CalendarView />
          )}

          {activeTab === 'analytics' && <AnalyticsView stats={stats} />}

          {activeTab === 'settings' && <SettingsView onResetData={handleResetData} />}
        </main>
      </div>
    </div>
  );
};
