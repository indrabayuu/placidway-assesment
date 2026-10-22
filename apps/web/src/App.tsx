import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import * as Collapsible from '@radix-ui/react-collapsible';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { createUuid } from '@/lib/uuid';
import {
  Activity as ActivityIcon,
  ArrowDown,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  CircleHelp,
  FileText,
  HeartPulse,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  MessageCircle,
  Plus,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  X,
} from 'lucide-react';

type Account = { id: 'owner' | 'client'; role: 'owner' | 'client'; name: string };
type Conversation = { id: string; title: string; response_language: string; created_at: string; updated_at: string };
type Citation = { id: string; url: string; title: string; section: string };
type Activity = { stage: string; message: string };
type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  citations: Citation[];
  activity: Activity[];
  thinking?: string;
  candidate_id?: string;
  provisional?: boolean;
  review_status?: string;
  outcome?: string;
  response_language?: string;
};
type Run = {
  id: string;
  conversation_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'interrupted';
  created_at: string;
  error: string | null;
  events_url: string;
  user_message_id?: string;
  input?: string;
  response_language?: string;
  retryable?: boolean;
};
type HistoryResponse = { items: Message[]; active_run: Run | null; latest_run?: Run | null };
type KnowledgeStatus = {
  ready: boolean;
  version: string | null;
  updated_at: string | null;
  source_count: number;
  chunk_count: number;
  price_count: number;
  refresh_running: boolean;
  last_error: string | null;
};
type ApiFailure = Error & { status: number };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Some successful or error responses, including logout, have no JSON body.
    }
    throw Object.assign(new Error(detail), { status: response.status }) as ApiFailure;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function isUnauthorized(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && (error as ApiFailure).status === 401;
}

function isSessionExpiredMessage(message: string): boolean {
  return /session\s+(?:has\s+)?expired|sign\s+in\s+again/i.test(message);
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay ? formatTime(value) : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

function safeEventUrl(eventsUrl: string, after?: string): string {
  const url = new URL(eventsUrl, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) {
    throw new Error('The response stream URL is outside the chat API.');
  }
  url.searchParams.delete('after');
  if (after) url.searchParams.set('after', after);
  return url.toString();
}

function safeCitationUrl(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function parseEvent<T>(event: Event): T | null {
  try {
    return JSON.parse((event as MessageEvent<string>).data) as T;
  } catch {
    return null;
  }
}

function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [authError, setAuthError] = useState('');
  const [sessionExpired, setSessionExpired] = useState(false);
  const [secret, setSecret] = useState('');
  const [loginBusy, setLoginBusy] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [streamVersion, setStreamVersion] = useState(0);
  const [streamCursor, setStreamCursor] = useState('');
  const [connectionState, setConnectionState] = useState<'connected' | 'reconnecting'>('connected');
  const [runError, setRunError] = useState<{ message: string; retryable: boolean } | null>(null);
  const [failedPrompt, setFailedPrompt] = useState('');
  const [activityOpen, setActivityOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [knowledge, setKnowledge] = useState<KnowledgeStatus | null>(null);
  const [knowledgeError, setKnowledgeError] = useState('');
  const [refreshNotice, setRefreshNotice] = useState('');
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const [notice, setNotice] = useState('');
  const streamRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const latestEventIdRef = useRef('');
  const pendingPromptRef = useRef('');
  const clientMessageIdRef = useRef('');
  const selectedIdRef = useRef<string | null>(null);
  const skipMessageLoadRef = useRef(false);
  const shouldAutoScrollRef = useRef(true);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const drawerCloseRef = useRef<HTMLButtonElement | null>(null);
  const wasSidebarOpenRef = useRef(false);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === selectedId) ?? null,
    [conversations, selectedId],
  );
  const filteredConversations = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return conversations;
    return conversations.filter((conversation) => conversation.title.toLocaleLowerCase().includes(needle));
  }, [conversations, search]);
  const visibleActivity = useMemo(
    () => messages.flatMap((message) => message.role === 'assistant' ? message.activity : []),
    [messages],
  );

  const handleSessionExpired = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    setAccount(null);
    setConversations([]);
    setSelectedId(null);
    selectedIdRef.current = null;
    setMessages([]);
    setActiveRun(null);
    pendingPromptRef.current = '';
    setKnowledge(null);
    setSessionExpired(true);
  }, []);

  const recoverUnauthorized = useCallback((error: unknown) => {
    if (isUnauthorized(error)) handleSessionExpired();
  }, [handleSessionExpired]);

  const loadConversations = useCallback(async () => {
    try {
      const result = await request<{ items: Conversation[] }>('/api/conversations');
      setConversations([...result.items].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
    } catch (error) {
      recoverUnauthorized(error);
      if (!isUnauthorized(error)) setNotice(error instanceof Error ? error.message : 'Could not load conversations.');
    }
  }, [recoverUnauthorized]);

  const loadMessages = useCallback(async (conversationId: string) => {
    setLoadingMessages(true);
    try {
      const result = await request<HistoryResponse>(`/api/conversations/${conversationId}/messages`);
      if (selectedIdRef.current !== conversationId) return;
      setMessages(result.items);
      const lastUserMessage = [...result.items].reverse().find((message) => message.role === 'user');
      const runUserMessage = result.latest_run?.user_message_id
        ? result.items.find((message) => message.id === result.latest_run?.user_message_id)
        : undefined;
      const fallbackPrompt = result.latest_run?.input || runUserMessage?.content || lastUserMessage?.content || '';
      pendingPromptRef.current = result.active_run ? (result.active_run.input || fallbackPrompt) : '';
      const terminalRun = result.latest_run && ['failed', 'interrupted'].includes(result.latest_run.status)
        ? result.latest_run
        : null;
      const retryable = terminalRun ? (terminalRun.retryable ?? true) : false;
      setRunError(terminalRun ? {
        message: terminalRun.status === 'interrupted'
          ? 'The previous response was interrupted.'
          : 'The response could not be completed.',
        retryable,
      } : null);
      setFailedPrompt(terminalRun && retryable ? fallbackPrompt : '');
      setStreamCursor('');
      latestEventIdRef.current = '';
      setActiveRun(result.active_run);
      setActivityOpen(false);
      shouldAutoScrollRef.current = true;
      setConnectionState('connected');
    } catch (error) {
      recoverUnauthorized(error);
      if (!isUnauthorized(error)) setNotice(error instanceof Error ? error.message : 'Could not load this conversation.');
    } finally {
      if (selectedIdRef.current === conversationId) setLoadingMessages(false);
    }
  }, [recoverUnauthorized]);

  useEffect(() => {
    let current = true;
    request<Account>('/api/auth/me')
      .then((value) => {
        if (!current) return;
        setAccount(value);
        setSessionExpired(false);
      })
      .catch((error: unknown) => {
        if (!current) return;
        if (!isUnauthorized(error)) setAuthError(error instanceof Error ? error.message : 'Unable to reach the service.');
      })
      .finally(() => current && setAuthChecking(false));
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (!account) return;
    void loadConversations();
    if (account.role === 'owner') {
      const loadKnowledge = async () => {
        try {
          const value = await request<KnowledgeStatus>('/api/knowledge/status');
          setKnowledge(value);
          setKnowledgeError('');
        } catch (error) {
          recoverUnauthorized(error);
          if (!isUnauthorized(error)) setKnowledgeError(error instanceof Error ? error.message : 'Knowledge status unavailable.');
        }
      };
      void loadKnowledge();
      const timer = window.setInterval(loadKnowledge, 20_000);
      return () => window.clearInterval(timer);
    }
  }, [account, loadConversations, recoverUnauthorized]);

  useEffect(() => {
    if (!selectedId) {
      setMessages([]);
      setActiveRun(null);
      setRunError(null);
      setLoadingMessages(false);
      return;
    }
    if (skipMessageLoadRef.current) {
      skipMessageLoadRef.current = false;
      setLoadingMessages(false);
      return;
    }
    void loadMessages(selectedId);
  }, [selectedId]); // Selection is the only trigger; the loaded conversation may update independently.

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    if (!activeRun || !account) {
      streamRef.current?.close();
      streamRef.current = null;
      return;
    }
    let disposed = false;
    let eventUrl: string;
    try {
      eventUrl = safeEventUrl(activeRun.events_url, streamCursor || undefined);
    } catch (error) {
      setRunError({ message: error instanceof Error ? error.message : 'The response stream could not be opened.', retryable: false });
      setActiveRun(null);
      return;
    }
    const source = new EventSource(eventUrl, { withCredentials: true });
    streamRef.current = source;
    setConnectionState('connected');
    const remember = (event: Event) => {
      const id = (event as MessageEvent).lastEventId;
      if (id) {
        latestEventIdRef.current = id;
        setStreamCursor(id);
      }
    };

    source.addEventListener('run.started', remember);
    source.addEventListener('stage', (event) => {
      remember(event);
      if (selectedIdRef.current !== activeRun.conversation_id) return;
      const data = parseEvent<Activity>(event);
      if (!data) return;
      setMessages((current) => {
        const assistant = [...current].reverse().find((message) => message.role === 'assistant' && message.id === `draft-assistant-${activeRun.id}`);
        const nextActivity = [...(assistant?.activity ?? []), data];
        if (assistant) return current.map((message) => message.id === assistant.id ? { ...message, activity: nextActivity } : message);
        return [...current, {
          id: `draft-assistant-${activeRun.id}`,
          role: 'assistant',
          content: '',
          created_at: new Date().toISOString(),
          citations: [],
          activity: nextActivity,
        }];
      });
    });
    source.addEventListener('answer.start', (event) => {
      remember(event);
      if (selectedIdRef.current !== activeRun.conversation_id) return;
      const data = parseEvent<{ message_id: string; candidate_id?: string; response_language: string }>(event);
      if (!data) return;
      setMessages((current) => {
        const id = `draft-assistant-${activeRun.id}`;
        const existing = current.find((message) => message.id === id);
        const assistant: Message = {
          id, role: 'assistant', content: '', created_at: existing?.created_at ?? new Date().toISOString(),
          citations: [], activity: existing?.activity ?? [], thinking: existing?.thinking ?? '',
          candidate_id: data.candidate_id, provisional: true, review_status: 'Provisional',
          response_language: data.response_language,
        };
        return [...current.filter((message) => message.id !== id), assistant];
      });
    });
    const appendDelta = (event: Event, channel: 'content' | 'thinking') => {
      remember(event);
      if (selectedIdRef.current !== activeRun.conversation_id) return;
      const data = parseEvent<{ text: string; candidate_id?: string }>(event);
      if (!data || typeof data.text !== 'string') return;
      setMessages((current) => {
        const id = `draft-assistant-${activeRun.id}`;
        const existing = current.find((message) => message.id === id);
        if (existing?.candidate_id && data.candidate_id && existing.candidate_id !== data.candidate_id) return current;
        if (!existing) return [...current, {
          id, role: 'assistant', content: channel === 'content' ? data.text : '',
          thinking: channel === 'thinking' ? data.text : '', created_at: new Date().toISOString(),
          citations: [], activity: [], provisional: true, candidate_id: data.candidate_id,
        }];
        return current.map((message) => message.id === id ? { ...message, [channel]: (message[channel] ?? '') + data.text } : message);
      });
    };
    source.addEventListener('answer.delta', (event) => appendDelta(event, 'content'));
    source.addEventListener('thinking.delta', (event) => appendDelta(event, 'thinking'));
    source.addEventListener('answer.rejected', (event) => {
      remember(event);
      const data = parseEvent<{ candidate_id: string; reason: string }>(event);
      if (!data || selectedIdRef.current !== activeRun.conversation_id) return;
      setMessages((current) => current.map((message) => message.candidate_id === data.candidate_id
        ? { ...message, review_status: data.reason } : message));
    });
    source.addEventListener('review.complete', (event) => {
      remember(event);
      const data = parseEvent<{ candidate_id: string }>(event);
      if (!data || selectedIdRef.current !== activeRun.conversation_id) return;
      setMessages((current) => current.map((message) => message.candidate_id === data.candidate_id
        ? { ...message, review_status: 'Source checks passed' } : message));
    });
    source.addEventListener('answer.complete', (event) => {
      remember(event);
      if (selectedIdRef.current !== activeRun.conversation_id) return;
      const data = parseEvent<{ message: Message }>(event);
      if (!data?.message) return;
      source.close();
      setMessages((current) => {
        const provisional = current.find((message) => message.id === `draft-assistant-${activeRun.id}`);
        return [
          ...current.filter((message) => message.role !== 'assistant' || (!message.id.startsWith('draft-assistant-') && message.id !== data.message.id)),
          { ...data.message, thinking: provisional?.thinking, provisional: false },
        ];
      });
      setActiveRun(null);
      setRunError(null);
      setFailedPrompt('');
      pendingPromptRef.current = '';
      setConnectionState('connected');
      void loadConversations();
      window.setTimeout(() => inputRef.current?.focus(), 0);
    });
    source.addEventListener('run.error', (event) => {
      remember(event);
      if (selectedIdRef.current !== activeRun.conversation_id) return;
      const data = parseEvent<{ message: string; retryable: boolean }>(event);
      source.close();
      const message = data?.message || 'This response could not be completed.';
      if (isSessionExpiredMessage(message)) {
        handleSessionExpired();
        return;
      }
      const prompt = pendingPromptRef.current;
      setFailedPrompt(data?.retryable ? prompt : '');
      setRunError({ message, retryable: !!data?.retryable });
      setActiveRun(null);
      setMessages((current) => current.map((message) => message.provisional ? { ...message, review_status: 'Not completed' } : message));
      setConnectionState('connected');
    });
    source.onerror = () => {
      if (disposed) return;
      source.close();
      setConnectionState('reconnecting');
      void request<Run>(`/api/runs/${activeRun.id}`).then((status) => {
        if (disposed) return;
        if (status.status === 'completed') {
          return request<HistoryResponse>(`/api/conversations/${activeRun.conversation_id}/messages`).then((result) => {
            if (disposed || selectedIdRef.current !== activeRun.conversation_id) return;
            setMessages(result.items);
            setActiveRun(result.active_run);
            setRunError(null);
            setFailedPrompt('');
            pendingPromptRef.current = result.active_run?.input || '';
            setConnectionState('connected');
            void loadConversations();
          });
        }
        if (status.status === 'failed' || status.status === 'interrupted') {
          const retryable = status.retryable ?? true;
          setRunError({
            message: status.error || (status.status === 'interrupted' ? 'The response was interrupted.' : 'The response could not be completed.'),
            retryable,
          });
          setFailedPrompt(retryable ? pendingPromptRef.current : '');
          setActiveRun(null);
          setConnectionState('connected');
          return;
        }
        reconnectTimerRef.current = window.setTimeout(() => {
          if (disposed) return;
          setStreamCursor(latestEventIdRef.current);
          setStreamVersion((version) => version + 1);
        }, 1200);
      }).catch((error: unknown) => {
        if (disposed) return;
        if (isUnauthorized(error)) {
          handleSessionExpired();
          return;
        }
        reconnectTimerRef.current = window.setTimeout(() => {
          if (disposed) return;
          setStreamCursor(latestEventIdRef.current);
          setStreamVersion((version) => version + 1);
        }, 1800);
      });
    };
    return () => {
      disposed = true;
      source.close();
      if (streamRef.current === source) streamRef.current = null;
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
    };
  }, [activeRun, account, streamVersion]);

  useLayoutEffect(() => {
    const element = messageListRef.current;
    if (!element) return;
    if (shouldAutoScrollRef.current) {
      element.scrollTop = element.scrollHeight;
      setShowScrollButton(false);
    }
  }, [messages, activeRun]);

  const selectConversation = useCallback((id: string) => {
    if (id !== selectedId) {
      streamRef.current?.close();
      streamRef.current = null;
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
      setActiveRun(null);
      setMessages([]);
      setRunError(null);
      setFailedPrompt('');
      setShowScrollButton(false);
      shouldAutoScrollRef.current = true;
    }
    setSelectedId(id);
    selectedIdRef.current = id;
    setSidebarOpen(false);
    setNotice('');
    setActivityOpen(false);
  }, [selectedId]);

  const startNewConversation = useCallback(async () => {
    setSidebarOpen(false);
    setNotice('');
    try {
      const conversation = await request<Conversation>('/api/conversations', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setConversations((items) => [conversation, ...items.filter((item) => item.id !== conversation.id)]);
      selectConversation(conversation.id);
    } catch (error) {
      recoverUnauthorized(error);
      if (!isUnauthorized(error)) setNotice(error instanceof Error ? error.message : 'Could not start a conversation.');
    }
  }, [recoverUnauthorized, selectConversation]);

  const sendMessage = async (text: string) => {
    const content = text.trim();
    if (!content || sending || activeRun) return;
    setSending(true);
    setRunError(null);
    setNotice('');
    setFailedPrompt('');
    pendingPromptRef.current = content;
    let conversationId = selectedId;
    try {
      if (!conversationId) {
        const created = await request<Conversation>('/api/conversations', {
          method: 'POST',
          body: JSON.stringify({}),
        });
        conversationId = created.id;
        setConversations((items) => [created, ...items.filter((item) => item.id !== created.id)]);
      }
      const clientMessageId = createUuid();
      clientMessageIdRef.current = clientMessageId;
      shouldAutoScrollRef.current = true;
      setShowScrollButton(false);
      setMessages((items) => [...items, {
        id: `user-${clientMessageId}`,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
        citations: [],
        activity: [],
      }]);
      setDraft('');
      const run = await request<Run>(`/api/conversations/${conversationId}/runs`, {
        method: 'POST',
        body: JSON.stringify({ message: content, client_message_id: clientMessageId }),
      });
      if (selectedIdRef.current !== conversationId) {
        skipMessageLoadRef.current = true;
        setSelectedId(conversationId);
        selectedIdRef.current = conversationId;
        setMessages([{
          id: `user-${clientMessageId}`,
          role: 'user',
          content,
          created_at: new Date().toISOString(),
          citations: [],
          activity: [],
        }]);
      }
      setActiveRun(run);
      setActivityOpen(false);
      setStreamCursor('');
      latestEventIdRef.current = '';
      void loadConversations();
    } catch (error) {
      recoverUnauthorized(error);
      if (!isUnauthorized(error)) {
        // If run creation failed, remove its optimistic bubble so retrying cannot duplicate it visually.
        setMessages((items) => items.filter((item) => item.id !== `user-${clientMessageIdRef.current}`));
        const status = typeof error === 'object' && error !== null && 'status' in error ? (error as ApiFailure).status : 0;
        if (status === 409 && conversationId) {
          setNotice('A response is already in progress. We reconnected to it.');
          await loadMessages(conversationId);
        } else {
          setNotice(error instanceof Error ? error.message : 'Message could not be sent.');
          setDraft(content);
        }
      }
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void sendMessage(draft);
  };

  const handleComposerKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(draft);
    }
  };

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoginBusy(true);
    setAuthError('');
    try {
      const result = await request<Account>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ secret }),
      });
      setAccount(result);
      setSecret('');
      setSessionExpired(false);
      setAuthError('');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Unable to sign in.');
    } finally {
      setLoginBusy(false);
    }
  };

  const handleLogout = async () => {
    try { await request<void>('/api/auth/logout', { method: 'POST' }); } catch { /* The local session is cleared either way. */ }
    handleSessionExpired();
    setSessionExpired(false);
    setAuthError('');
  };

  const refreshKnowledge = async () => {
    setRefreshBusy(true);
    setRefreshNotice('');
    try {
      const result = await request<{ status: 'started' | 'already_running' }>('/api/admin/knowledge/refresh', { method: 'POST' });
      setRefreshNotice(result.status === 'started' ? 'Knowledge refresh started.' : 'A refresh is already running.');
      const status = await request<KnowledgeStatus>('/api/knowledge/status');
      setKnowledge(status);
    } catch (error) {
      recoverUnauthorized(error);
      setRefreshNotice(error instanceof Error ? error.message : 'Could not refresh knowledge.');
    } finally {
      setRefreshBusy(false);
      window.setTimeout(() => setRefreshNotice(''), 5000);
    }
  };

  const chooseStarter = (prompt: string) => {
    setDraft(prompt);
    inputRef.current?.focus();
  };

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (sidebarOpen && event.key === 'Escape') {
        event.preventDefault();
        setSidebarOpen(false);
        return;
      }
      if (sidebarOpen && event.key === 'Tab') {
        const sidebar = document.getElementById('conversation-sidebar');
        const focusable = sidebar?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])');
        if (focusable?.length) {
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        void startNewConversation();
      }
      if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey) {
        const target = event.target as HTMLElement | null;
        if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return;
        const searchInput = document.querySelector<HTMLInputElement>('.history-search input');
        if (searchInput) {
          event.preventDefault();
          searchInput.focus();
        }
      }
    };
    window.addEventListener('keydown', onShortcut);
    return () => window.removeEventListener('keydown', onShortcut);
  }, [sidebarOpen, startNewConversation]);

  useEffect(() => {
    if (sidebarOpen) drawerCloseRef.current?.focus();
    else if (wasSidebarOpenRef.current) menuButtonRef.current?.focus();
    wasSidebarOpenRef.current = sidebarOpen;
  }, [sidebarOpen]);

  const onMessageScroll = () => {
    const element = messageListRef.current;
    if (!element) return;
    const distanceToBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    shouldAutoScrollRef.current = distanceToBottom < 160;
    setShowScrollButton(distanceToBottom > 260);
  };

  const scrollToLatest = () => {
    shouldAutoScrollRef.current = true;
    setShowScrollButton(false);
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: 'smooth' });
  };

  if (authChecking) {
    return <main className="auth-screen"><div className="auth-loading"><span className="brand-mark"><HeartPulse size={22} /></span><LoaderCircle className="spin" size={19} aria-label="Loading" /></div></main>;
  }

  if (!account) {
    return (
      <main className="auth-screen">
        <section className="login-card" aria-labelledby="login-title">
          <div className="login-brand"><span className="brand-mark"><HeartPulse size={22} strokeWidth={2.4} /></span><span>PlacidWay</span></div>
          <div className="login-art" aria-hidden="true">
            <div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" />
            <div className="art-center"><HeartPulse size={38} strokeWidth={1.6} /></div>
            <span className="art-dot dot-one" /><span className="art-dot dot-two" /><span className="art-dot dot-three" />
          </div>
          <p className="eyebrow">YOUR CARE, WITH CLARITY</p>
          <h1 id="login-title">A calmer way to explore care.</h1>
          <p className="login-intro">Your private guide to medical travel, treatment options, and the questions that matter.</p>
          <form className="login-form" onSubmit={handleLogin}>
            <label htmlFor="secret">Access secret</label>
          <div className="secret-wrap"><LockKeyhole size={17} aria-hidden="true" /><Input id="secret" type="password" autoComplete="current-password" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder="Enter your access secret" required autoFocus /></div>
            {sessionExpired && <p className="form-note" role="status">Your session has expired. Sign in again to continue.</p>}
            {authError && <p className="form-error" role="alert">{authError}</p>}
            <Button className="primary-button login-button" type="submit" disabled={loginBusy || !secret}>
              {loginBusy ? <LoaderCircle size={17} className="spin" /> : <span>Continue securely</span>}
              {!loginBusy && <ArrowRight size={17} />}
            </Button>
          </form>
          <div className="privacy-note"><LockKeyhole size={14} /><span>Your conversations are private and protected.</span></div>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
      {sidebarOpen && <Button variant="ghost" className="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
      <aside id="conversation-sidebar" className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`} role={sidebarOpen ? 'dialog' : undefined} aria-modal={sidebarOpen || undefined} aria-label="Conversation history">
        <div className="sidebar-top">
          <div className="wordmark" aria-label="PlacidWay">
            <span className="brand-mark"><HeartPulse size={19} strokeWidth={2.5} /></span><span>PlacidWay</span>
          </div>
          <Button ref={drawerCloseRef} variant="ghost" size="icon" className="icon-button mobile-close" onClick={() => setSidebarOpen(false)} aria-label="Close navigation"><X size={19} /></Button>
        </div>
        <div className="sidebar-intro"><p>YOUR CARE COMPANION</p><h2>Good to have you here,<br /><strong>{account.name}</strong></h2></div>
        <Button variant="outline" className="new-chat-button" onClick={() => void startNewConversation()}><Plus size={17} /> <span>New conversation</span><span className="shortcut">⌘ K</span></Button>
        <div className="history-heading"><span>RECENT CONVERSATIONS</span><span className="history-count">{conversations.length}</span></div>
        {conversations.length > 4 && <label className="history-search"><Search size={15} /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a conversation" aria-label="Find a conversation" /><kbd>/</kbd></label>}
        <nav className="history-list" aria-label="Recent conversations">
          {filteredConversations.length === 0 ? <p className="history-empty">{search ? 'No matching conversations.' : 'Your conversations will appear here.'}</p> : filteredConversations.map((conversation) => (
            <Button key={conversation.id} variant="ghost" className={`history-item ${selectedId === conversation.id ? 'history-item-active' : ''}`} onClick={() => selectConversation(conversation.id)} aria-current={selectedId === conversation.id ? 'page' : undefined}>
              <span className="history-icon"><MessageCircle size={16} /></span>
              <span className="history-copy"><span className="history-title">{conversation.title || 'New conversation'}</span><span className="history-meta">{formatDate(conversation.updated_at)}</span></span>
              {selectedId === conversation.id && <span className="selected-indicator" aria-hidden="true" />}
            </Button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-help"><span className="help-icon"><CircleHelp size={17} /></span><span><strong>Here to help</strong><small>Thoughtful answers for your next step.</small></span><ArrowUpRight /></div>
          <div className="account-row"><span className="avatar" aria-hidden="true">{account.name.trim().slice(0, 1).toLocaleUpperCase() || 'P'}</span><span className="account-copy"><strong>{account.name}</strong><small>{account.role === 'owner' ? 'Workspace owner' : 'Private account'}</small></span><Button variant="ghost" size="icon" className="icon-button logout-button" onClick={() => void handleLogout()} title="Sign out" aria-label="Sign out"><LogOut size={17} /></Button></div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="topbar-left"><Button ref={menuButtonRef} variant="ghost" size="icon" className="icon-button menu-button" aria-label="Open navigation" aria-controls="conversation-sidebar" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Menu size={20} /></Button><span className="breadcrumb-muted">Your guide</span><span className="breadcrumb-separator">/</span><span className="breadcrumb-current">{activeConversation?.title || 'New conversation'}</span></div>
          <div className="topbar-right">
            {account.role === 'owner' && <div className="knowledge-control">
              <span className={`knowledge-dot ${knowledge?.ready ? 'knowledge-ready' : ''}`} />
              <span className="knowledge-label">{knowledgeError ? 'Knowledge unavailable' : knowledge?.refresh_running ? 'Updating sources' : knowledge?.ready ? 'Sources up to date' : 'Sources pending'}</span>
              {knowledge?.last_error && <span className="knowledge-error" role="img" aria-label="Last knowledge refresh failed" title={knowledge.last_error}>!</span>}
              <Button variant="ghost" size="icon" className="icon-button refresh-button" onClick={() => void refreshKnowledge()} disabled={refreshBusy || knowledge?.refresh_running} aria-label="Refresh knowledge sources" title="Refresh knowledge sources"><RefreshCw size={16} className={refreshBusy || knowledge?.refresh_running ? 'spin' : ''} /></Button>
            </div>}
          </div>
        </header>

        {(notice || refreshNotice) && <div className={`notice-bar ${notice ? 'notice-error' : ''}`} role="status"><span>{notice || refreshNotice}</span><Button variant="ghost" size="icon" className="icon-button" aria-label="Dismiss message" onClick={() => { setNotice(''); setRefreshNotice(''); }}><X size={15} /></Button></div>}

        <section className={`chat-view ${messages.length === 0 && !loadingMessages ? 'chat-empty' : ''}`} aria-label="Conversation">
          {messages.length === 0 && !loadingMessages ? (
            <div className="welcome-content">
              <div className="welcome-ornament" aria-hidden="true"><span className="welcome-glow" /><span className="welcome-core"><HeartPulse size={29} strokeWidth={1.65} /></span><span className="welcome-orbit orbit-a" /><span className="welcome-orbit orbit-b" /><span className="welcome-spark spark-a">✦</span><span className="welcome-spark spark-b">✦</span></div>
              <p className="eyebrow welcome-eyebrow"><Sparkles size={13} /> A LITTLE MORE CLARITY</p>
              <h1>Care decisions are personal.<br /><span>Finding answers can feel simpler.</span></h1>
              <p className="welcome-subtitle">Ask about treatments, destinations, or how to take your next step. I’ll help you explore with trusted information.</p>
              <div className="starter-grid" aria-label="Suggested questions">
                <Button variant="outline" className="starter-card" onClick={() => chooseStarter('How much does outpatient adenocarcinoma cancer treatment at ITC in Mexico cost?')}><span className="starter-icon mint"><HeartPulse size={17} /></span><span><strong>Explore treatment pricing</strong><small>Three-week programs at ITC</small></span><ArrowUpRight /></Button>
                <Button variant="outline" className="starter-card" onClick={() => chooseStarter('What is included in the outpatient and inpatient cancer treatment programs at ITC?')}><span className="starter-icon lavender"><BookOpen size={17} /></span><span><strong>Compare care programs</strong><small>Services and program details</small></span><ArrowUpRight /></Button>
                <Button variant="outline" className="starter-card" onClick={() => chooseStarter('What should I ask my doctor before considering alternative cancer treatment?')}><span className="starter-icon peach"><CircleHelp size={17} /></span><span><strong>Plan thoughtful questions</strong><small>Discuss options with your care team</small></span><ArrowUpRight /></Button>
              </div>
              <p className="welcome-footnote"><LockKeyhole size={13} /> A private space to explore, at your own pace.</p>
            </div>
          ) : (
            <div className="conversation-column" ref={messageListRef} onScroll={onMessageScroll} aria-live="polite" aria-busy={loadingMessages || !!activeRun}>
              {loadingMessages && <div className="loading-history"><LoaderCircle className="spin" size={19} /> Loading conversation…</div>}
              {!loadingMessages && messages.map((message) => <MessageBubble key={message.id} message={message} />)}
              {activeRun && <div className="run-status" role="status">
                {connectionState === 'reconnecting' ? <><RefreshCw className="spin" size={14} /> Reconnecting to your response…</> : <><span className="pulse-dot" /> {activeRun.status === 'queued' ? 'Preparing a thoughtful response…' : 'Finding a helpful answer…'}</>}
              </div>}
              {runError && <div className="run-error" role="alert"><span className="error-symbol" aria-hidden="true">!</span><span><strong>We hit a pause</strong><small>{runError.message}</small>{runError.retryable && failedPrompt && <Button variant="link" className="retry-button" onClick={() => void sendMessage(failedPrompt)}><RefreshCw size={14} /> Try again</Button>}</span></div>}
              {showScrollButton && <Button variant="outline" size="icon" className="scroll-bottom" onClick={scrollToLatest} aria-label="Scroll to latest message"><ArrowDown size={17} /></Button>}
            </div>
          )}
        </section>

        <div className="composer-area">
          {!!visibleActivity.length && <Collapsible.Root open={activityOpen} onOpenChange={setActivityOpen} className="activity-wrap">
            <Collapsible.Trigger asChild>
              <Button variant="ghost" className="activity-toggle"><ActivityIcon size={15} aria-hidden="true" /><span>{activeRun ? 'Working through your question' : 'How this answer came together'}</span><span className="activity-count">{visibleActivity.length}</span><ChevronDown size={15} className={activityOpen ? 'chevron-open' : ''} aria-hidden="true" /></Button>
            </Collapsible.Trigger>
            <Collapsible.Content className="activity-panel">{visibleActivity.map((item, index) => <div className="activity-step" key={`${item.stage}-${index}`}><span className="activity-check"><Check size={12} /></span><span><strong>{formatStage(item.stage)}</strong><small>{item.message}</small></span></div>)}</Collapsible.Content>
          </Collapsible.Root>}
          <form className="composer" onSubmit={handleSubmit}>
            <label htmlFor="chat-input" className="sr-only">Ask a question</label>
            <Textarea id="chat-input" ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder="Ask anything about your care journey…" rows={1} disabled={!!activeRun || sending} aria-describedby="composer-hint" />
            <Button className="send-button" type="submit" disabled={!draft.trim() || !!activeRun || sending} aria-label="Send message">{sending ? <LoaderCircle size={18} className="spin" /> : <Send size={17} />}</Button>
            <div className="composer-bottom"><span id="composer-hint"><LockKeyhole size={12} /> Private conversation</span><span>Enter to send <i>·</i> Shift + Enter for a new line</span></div>
          </form>
          <p className="disclaimer">PlacidWay shares general information, not medical advice. Please consult a qualified healthcare professional.</p>
        </div>
      </main>
    </div>
  );
}

function ArrowUpRight() {
  return <ArrowRight size={15} className="arrow-up-right" aria-hidden="true" />;
}

function formatStage(stage: string): string {
  return stage.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isRtlLanguage(language?: string): boolean {
  if (!language || language === 'auto') return false;
  try {
    const script = new Intl.Locale(language).maximize().script;
    return ['Arab', 'Hebr', 'Thaa', 'Nkoo', 'Adlm', 'Rohg', 'Syrc'].includes(script ?? '');
  } catch {
    return false;
  }
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  const rtl = !isUser && isRtlLanguage(message.response_language);
  const safeCitations = message.citations
    .map((citation) => ({ citation, url: safeCitationUrl(citation.url) }))
    .filter((item): item is { citation: Citation; url: string } => item.url !== null);
  return (
    <article className={`message-row ${isUser ? 'message-user' : 'message-assistant'}`}>
      {!isUser && <span className="assistant-avatar" aria-hidden="true"><HeartPulse size={16} /></span>}
      <div className={`message-content-wrap ${isUser ? 'user-content-wrap' : ''}`}>
        <div className={`message-label ${isUser ? 'user-label' : ''}`}>{isUser ? 'You' : 'PlacidWay guide'}<span>·</span><time dateTime={message.created_at}>{formatTime(message.created_at)}</time></div>
        {!isUser && message.thinking && <details className="thinking-panel">
          <summary>Thinking <span>· provisional</span></summary>
          <div className="thinking-content" dir="auto">{message.thinking}</div>
        </details>}
        {!isUser && message.provisional && <p className="response-status">AI Response · {message.review_status || 'Provisional'}</p>}
        <div className={`message-bubble ${isUser ? 'user-bubble' : 'assistant-bubble'}`} dir={rtl ? 'rtl' : 'auto'} lang={message.response_language || undefined}>
          {message.content ? <div className="markdown-content"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div> : <span className="assistant-thinking"><i /><i /><i /><span className="sr-only">Preparing response</span></span>}
        </div>
        {!isUser && safeCitations.length > 0 && <div className="source-list" aria-label="Sources"><p className="source-heading"><FileText size={13} /> Sources that may help</p><div className="source-cards">{safeCitations.map(({ citation, url }) => <a key={citation.id} className="source-card" href={url} target="_blank" rel="noopener noreferrer"><span className="source-icon"><FileText size={15} /></span><span><strong>{citation.title}</strong><small>{citation.section || 'PlacidWay resource'}</small></span><ArrowUpRight /></a>)}</div></div>}
      </div>
    </article>
  );
}

export default App;
