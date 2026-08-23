export type ReadStatus = 'read' | 'unread';
export type ImportanceLevel = 'low' | 'medium' | 'high' | 'urgent';
export type ReplyOutlineStatus =
  | 'none'
  | 'not_applicable'
  | 'suggested'
  | 'edited'
  | 'expanded_to_draft'
  | 'sent';

export interface CalendarSlot {
  start: string;
  end: string;
}

export interface CalendarEvent {
  summary?: string;
  start: string;
  end: string;
  allDay?: boolean;
}

export interface CalendarContext {
  rangeStart: string;
  rangeEnd: string;
  busyBlocks: CalendarSlot[];
  suggestedSlots: CalendarSlot[];
  eventCount: number;
  events: CalendarEvent[];
  generatedAt: string;
}

export interface EmailItem {
  emailId: string;
  threadId: string;
  sender: string;
  subject: string;
  receivedAt: string;
  readStatus: ReadStatus;
  isNoReply: boolean | null;
  noReplyReason: string | null;
  importanceScore: number | null;
  importanceLevel: ImportanceLevel | null;
  importanceJustification: string | null;
  summary: string | null;
  isSchedulingRelated: boolean | null;
  hasCalendarContext: boolean;
  calendarContext?: CalendarContext | null;
  replyOutline: string[] | null;
  replyOutlineStatus: ReplyOutlineStatus;
  outlineEligible: boolean;
  processedAt: string | null;
  body?: string;
  isStale?: boolean;
}

export interface InboxStats {
  total: number;
  unread: number;
  noReply: number;
  scheduling: number;
  withOutline: number;
  unprocessed: number;
  byLevel: {
    urgent: number;
    high: number;
    medium: number;
    low: number;
  };
}

export interface FilterOptions {
  search: string;
  readStatus: 'all' | 'read' | 'unread';
  importance: 'all' | 'urgent' | 'high' | 'medium' | 'low';
  noReply: boolean | null;
  scheduling: boolean | null;
  hasOutline: boolean | null;
  isStale: boolean | null;
  sortBy: 'importance' | 'received_at' | 'sender' | 'subject';
  descending: boolean;
}
