import React, { useEffect, useState, useCallback } from 'react';
import { CheckSquare, Reply, ListChecks, Loader2 } from 'lucide-react';
import { TodoItem } from '../../types/email';
import { api } from '../../services/api';
import { Card } from '../ui/Card';

export const TodoView: React.FC = () => {
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  // Optimistically hidden as soon as the checkbox is clicked, rather than
  // waiting on the network round trip — "mark complete and it disappears"
  // should feel instant.
  const [completingIds, setCompletingIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    const { todos } = await api.getTodos();
    setTodos(todos);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleComplete = async (todoId: string) => {
    setCompletingIds((prev) => new Set(prev).add(todoId));
    setTodos((prev) => prev.filter((t) => t.todoId !== todoId));
    try {
      await api.completeTodo(todoId);
    } catch (err) {
      console.error('Failed to complete todo:', err);
    }
  };

  const actionItems = todos.filter((t) => t.kind === 'action_item');
  const needsReply = todos.filter((t) => t.kind === 'needs_reply');

  const renderItem = (item: TodoItem) => (
    <div
      key={item.todoId}
      className="flex items-start gap-3 p-3 rounded-lg border border-slate-200 bg-white hover:border-blue-300 transition-colors"
    >
      <button
        onClick={() => handleComplete(item.todoId)}
        disabled={completingIds.has(item.todoId)}
        aria-label="Mark complete"
        className="mt-0.5 shrink-0 w-5 h-5 rounded border-2 border-slate-300 hover:border-blue-500 hover:bg-blue-50 transition-colors flex items-center justify-center disabled:opacity-50"
      >
        {completingIds.has(item.todoId) && (
          <Loader2 className="w-3 h-3 text-blue-600 animate-spin" />
        )}
      </button>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-slate-800 font-medium truncate">{item.text}</p>
        <p className="text-xs text-slate-500 truncate">
          {item.sender} — {item.subject || '(no subject)'}
        </p>
      </div>
      {item.importanceLevel && (
        <span className="shrink-0 text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
          {item.importanceLevel}
        </span>
      )}
    </div>
  );

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6 overflow-y-auto h-full bg-slate-50">
      <div>
        <h2 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
          <ListChecks className="w-5 h-5 text-blue-600" />
          To-Do
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          Action items and emails that still need a reply, pulled from your inbox automatically.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : todos.length === 0 ? (
        <Card padding="lg" className="text-center text-slate-500 text-sm">
          Nothing outstanding — you're caught up.
        </Card>
      ) : (
        <>
          <Card padding="lg" className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <CheckSquare className="w-4 h-4 text-indigo-600" />
              Action items
              <span className="text-xs font-normal text-slate-400">({actionItems.length})</span>
            </div>
            {actionItems.length === 0 ? (
              <p className="text-xs text-slate-400 italic">No open action items.</p>
            ) : (
              <div className="space-y-2">{actionItems.map(renderItem)}</div>
            )}
          </Card>

          <Card padding="lg" className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <Reply className="w-4 h-4 text-blue-600" />
              Needs your reply
              <span className="text-xs font-normal text-slate-400">({needsReply.length})</span>
            </div>
            {needsReply.length === 0 ? (
              <p className="text-xs text-slate-400 italic">No emails waiting on a reply.</p>
            ) : (
              <div className="space-y-2">{needsReply.map(renderItem)}</div>
            )}
          </Card>
        </>
      )}
    </div>
  );
};
