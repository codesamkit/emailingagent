import { EmailItem, FilterOptions, InboxStats } from '../types/email';
import { MOCK_EMAILS } from './mockData';

const BASE_URL = '/api';

// In-memory store for mutations when in mock mode or fallback
let localEmails = [...MOCK_EMAILS];

export const api = {
  async isBackendAvailable(): Promise<boolean> {
    try {
      const res = await fetch(`${BASE_URL}/health`, { method: 'GET', signal: AbortSignal.timeout(1500) });
      return res.ok;
    } catch {
      return false;
    }
  },

  async getStats(): Promise<InboxStats> {
    try {
      const res = await fetch(`${BASE_URL}/stats`, { signal: AbortSignal.timeout(2000) });
      if (!res.ok) throw new Error('API request failed');
      return await res.json();
    } catch {
      // Recompute stats from local state for fidelity
      return {
        total: localEmails.length,
        unread: localEmails.filter(e => e.readStatus === 'unread').length,
        noReply: localEmails.filter(e => e.isNoReply).length,
        scheduling: localEmails.filter(e => e.isSchedulingRelated).length,
        withOutline: localEmails.filter(e => e.replyOutline && e.replyOutline.length > 0).length,
        unprocessed: localEmails.filter(e => e.processedAt === null).length,
        byLevel: {
          urgent: localEmails.filter(e => e.importanceLevel === 'urgent').length,
          high: localEmails.filter(e => e.importanceLevel === 'high').length,
          medium: localEmails.filter(e => e.importanceLevel === 'medium').length,
          low: localEmails.filter(e => e.importanceLevel === 'low').length,
        }
      };
    }
  },

  async getEmails(filters?: Partial<FilterOptions>): Promise<{ total: number; emails: EmailItem[] }> {
    try {
      const params = new URLSearchParams();
      if (filters?.readStatus && filters.readStatus !== 'all') params.append('readStatus', filters.readStatus);
      if (filters?.importance && filters.importance !== 'all') params.append('importance', filters.importance);
      if (filters?.noReply !== undefined && filters.noReply !== null) params.append('noReply', String(filters.noReply));
      if (filters?.scheduling !== undefined && filters.scheduling !== null) params.append('scheduling', String(filters.scheduling));
      if (filters?.hasOutline !== undefined && filters.hasOutline !== null) params.append('hasOutline', String(filters.hasOutline));
      if (filters?.search) params.append('search', filters.search);
      if (filters?.sortBy) params.append('sortBy', filters.sortBy);
      if (filters?.descending !== undefined) params.append('descending', String(filters.descending));

      const queryStr = params.toString() ? `?${params.toString()}` : '';
      const res = await fetch(`${BASE_URL}/emails${queryStr}`, { signal: AbortSignal.timeout(2000) });
      if (!res.ok) throw new Error('API request failed');
      const data = await res.json();
      return {
        total: data.total,
        emails: data.emails,
      };
    } catch {
      // Local fallback with filtering & sorting
      let filtered = [...localEmails];

      if (filters?.search) {
        const q = filters.search.toLowerCase();
        filtered = filtered.filter(e =>
          e.subject.toLowerCase().includes(q) ||
          e.sender.toLowerCase().includes(q) ||
          (e.summary && e.summary.toLowerCase().includes(q))
        );
      }

      if (filters?.readStatus && filters.readStatus !== 'all') {
        filtered = filtered.filter(e => e.readStatus === filters.readStatus);
      }

      if (filters?.importance && filters.importance !== 'all') {
        filtered = filtered.filter(e => e.importanceLevel === filters.importance);
      }

      if (filters?.noReply !== undefined && filters.noReply !== null) {
        filtered = filtered.filter(e => Boolean(e.isNoReply) === filters.noReply);
      }

      if (filters?.scheduling !== undefined && filters.scheduling !== null) {
        filtered = filtered.filter(e => Boolean(e.isSchedulingRelated) === filters.scheduling);
      }

      if (filters?.hasOutline !== undefined && filters.hasOutline !== null) {
        filtered = filtered.filter(e => Boolean(e.replyOutline && e.replyOutline.length > 0) === filters.hasOutline);
      }

      if (filters?.isStale !== undefined && filters.isStale !== null) {
        filtered = filtered.filter(e => Boolean(e.isStale) === filters.isStale);
      }

      // Sort
      const sortBy = filters?.sortBy || 'importance';
      const descending = filters?.descending !== false;

      filtered.sort((a, b) => {
        let valA: any = a.importanceScore ?? -1;
        let valB: any = b.importanceScore ?? -1;

        if (sortBy === 'received_at') {
          valA = new Date(a.receivedAt).getTime();
          valB = new Date(b.receivedAt).getTime();
        } else if (sortBy === 'sender') {
          valA = a.sender.toLowerCase();
          valB = b.sender.toLowerCase();
        } else if (sortBy === 'subject') {
          valA = a.subject.toLowerCase();
          valB = b.subject.toLowerCase();
        }

        if (valA < valB) return descending ? 1 : -1;
        if (valA > valB) return descending ? -1 : 1;
        return 0;
      });

      return {
        total: filtered.length,
        emails: filtered,
      };
    }
  },

  async getEmail(emailId: string): Promise<EmailItem | null> {
    try {
      const res = await fetch(`${BASE_URL}/emails/${emailId}`, { signal: AbortSignal.timeout(2000) });
      if (!res.ok) throw new Error('API request failed');
      return await res.json();
    } catch {
      const found = localEmails.find(e => e.emailId === emailId);
      return found ? { ...found } : null;
    }
  },

  async updateOutline(emailId: string, outline: string[]): Promise<EmailItem> {
    try {
      const res = await fetch(`${BASE_URL}/emails/${emailId}/outline`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outline }),
        signal: AbortSignal.timeout(2000),
      });
      if (!res.ok) throw new Error('Failed to update outline via API');
      return await res.json();
    } catch {
      // Local state update
      const index = localEmails.findIndex(e => e.emailId === emailId);
      if (index === -1) throw new Error(`Email ${emailId} not found`);

      const updated: EmailItem = {
        ...localEmails[index],
        replyOutline: outline,
        replyOutlineStatus: 'edited',
      };
      localEmails[index] = updated;
      return updated;
    }
  },

  async toggleReadStatus(emailId: string): Promise<EmailItem> {
    const index = localEmails.findIndex(e => e.emailId === emailId);
    if (index === -1) throw new Error(`Email ${emailId} not found`);

    const current = localEmails[index];
    const newStatus = current.readStatus === 'read' ? 'unread' : 'read';

    let updatedOutline = current.replyOutline;
    let updatedOutlineStatus = current.replyOutlineStatus;
    let outlineEligible = false;

    if (newStatus === 'read' && !current.isNoReply) {
      outlineEligible = true;
      if (!updatedOutline || updatedOutline.length === 0) {
        updatedOutline = [
          'Acknowledge receipt and thank the sender',
          'Address the main question or request clearly',
          'Provide availability or confirm next steps',
        ];
        updatedOutlineStatus = 'suggested';
      }
    } else if (newStatus === 'unread') {
      outlineEligible = false;
      updatedOutline = null;
      updatedOutlineStatus = 'none';
    }

    const updated: EmailItem = {
      ...current,
      readStatus: newStatus,
      outlineEligible,
      replyOutline: updatedOutline,
      replyOutlineStatus: updatedOutlineStatus,
    };
    localEmails[index] = updated;
    return updated;
  },

  async expandDraft(emailId: string, tone: 'professional' | 'concise' | 'direct' | 'warm' = 'professional'): Promise<string> {
    const email = localEmails.find(e => e.emailId === emailId);
    if (!email || !email.replyOutline || email.replyOutline.length === 0) {
      throw new Error('This email has no outline to expand.');
    }

    // Try backend if available
    try {
      const res = await fetch(`${BASE_URL}/emails/${emailId}/expand`, {
        method: 'POST',
        signal: AbortSignal.timeout(2000),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.draft) return data.draft;
      }
    } catch {
      // Continue to intelligent client-side generation
    }

    // High quality intelligent draft expansion based on outline bullets & tone
    const senderName = email.sender.split('@')[0].replace('.', ' ');
    const formattedSender = senderName.charAt(0).toUpperCase() + senderName.slice(1);

    const greeting = tone === 'direct' ? `Hi ${formattedSender},` : tone === 'warm' ? `Hi ${formattedSender},\n\nHope you're having a wonderful week!` : `Dear ${formattedSender},`;
    
    const bodyParagraphs = email.replyOutline.map((bullet) => {
      if (bullet.toLowerCase().includes('acknowledge') || bullet.toLowerCase().includes('thank')) {
        return tone === 'concise'
          ? `Thanks for reaching out and sharing this update.`
          : `Thank you for reaching out and sharing the latest updates. We really appreciate your collaboration on this.`;
      }
      if (bullet.toLowerCase().includes('calendar') || bullet.toLowerCase().includes('pm') || bullet.toLowerCase().includes('am') || bullet.toLowerCase().includes('availab')) {
        return `Regarding scheduling, I've checked my calendar and can confirm availability for the suggested slots (${bullet.replace(/.*confirm you can make /i, '').replace(/\(per calendar.*\)/i, '').trim()}). Please let me know which time suits you best.`;
      }
      if (bullet.toLowerCase().includes('review') || bullet.toLowerCase().includes('feedback')) {
        return `I will thoroughly review the materials and provide comprehensive feedback ahead of the upcoming deadline.`;
      }
      if (bullet.toLowerCase().includes('failover') || bullet.toLowerCase().includes('incident') || bullet.toLowerCase().includes('approv')) {
        return `I approve the emergency routing and failover procedure immediately. Let's keep the war room channel actively updated.`;
      }
      return `${bullet.charAt(0).toUpperCase() + bullet.slice(1)}.`;
    }).join('\n\n');

    const signoff = tone === 'concise' ? 'Best,\nValence User' : tone === 'warm' ? 'Warm regards,\nValence User' : tone === 'direct' ? 'Thanks,\nValence User' : 'Sincerely,\nValence User';

    return `${greeting}\n\n${bodyParagraphs}\n\n${signoff}`;
  },

  resetDemoData() {
    localEmails = [...MOCK_EMAILS];
  }
};
