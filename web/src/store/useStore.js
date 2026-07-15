/**
 * Global State Store (Zustand)
 * =============================
 * Manages auth state, sessions, messages, attachments, and UI state.
 */

import { create } from 'zustand';
import * as api from '../api/client';

const useStore = create((set, get) => ({
  // --- Auth ---
  user: null,
  isLoggedIn: false,
  deployMode: 'pc',  // 'pc' | 'saas' — set from /api/me response

  /**
   * Initiate OIDC login: fetch login URL from backend, redirect to Keycloak.
   */
  login: async () => {
    try {
      const resp = await api.fetchLoginUrl();
      // Redirect the browser to Keycloak's login page
      window.location.href = resp.url;
    } catch (error) {
      console.error('Failed to get login URL:', error);
    }
  },

  /**
   * Called after successful OIDC callback to set user state.
   */
  loginSuccess: async (userData) => {
    try {
      // Fetch full user profile from our API
      const user = await api.fetchMe();
      set({ user, isLoggedIn: true, deployMode: user.deploy_mode || 'pc' });
      // Load sessions and providers after login
      get().loadSessions();
      get().loadProviders();
    } catch (error) {
      console.error('Failed to fetch user after login:', error);
      // Token might be invalid, clear it
      api.clearToken();
      set({ user: null, isLoggedIn: false });
    }
  },

  /**
   * Try to restore session from stored token (on page load).
   */
  restoreSession: async () => {
    const token = api.getToken();
    if (!token) return false;

    try {
      const user = await api.fetchMe();
      set({ user, isLoggedIn: true, deployMode: user.deploy_mode || 'pc' });
      get().loadSessions();
      get().loadProviders();
      return true;
    } catch (error) {
      // Token expired or invalid
      console.warn('Session restore failed:', error);
      api.clearToken();
      set({ user: null, isLoggedIn: false });
      return false;
    }
  },

  logout: () => {
    api.clearToken();
    set({
      user: null,
      isLoggedIn: false,
      deployMode: 'pc',
      sessions: [],
      activeSessionId: null,
      messages: [],
      pendingFiles: [],
      uploadedAttachments: [],
    });
  },

  // --- Sessions ---
  sessions: [],
  activeSessionId: null,
  showGuidePage: true,  // Show guide page by default

  loadSessions: async () => {
    try {
      const resp = await api.listSessions();
      set({ sessions: resp.sessions || [] });
    } catch (error) {
      console.error('Failed to load sessions:', error);
    }
  },

  /**
   * Start a new chat — just show guide page, don't create session yet.
   * Idempotent: multiple clicks have the same effect.
   */
  startNewChat: () => {
    set({
      activeSessionId: null,
      messages: [],
      pendingFiles: [],
      uploadedAttachments: [],
      activeCapability: null,
      fluxOverviewData: null,
      fluxOverviewFetched: false,
      acpsOverviewData: null,
      acpsOverviewFetched: false,
      showGuidePage: true,
    });
  },

  /**
   * Internal: actually create a session on the backend.
   */
  createSession: async () => {
    try {
      const session = await api.createSession();
      set((state) => ({
        sessions: [session, ...state.sessions],
        activeSessionId: session.id,
        messages: [],
        pendingFiles: [],
        uploadedAttachments: [],
        fluxOverviewData: null,
        fluxOverviewFetched: false,
        acpsOverviewData: null,
        acpsOverviewFetched: false,
        showGuidePage: false,
      }));
      return session;
    } catch (error) {
      console.error('Failed to create session:', error);
    }
  },

  /**
   * Handle guide page action button click.
   * Activates flux capability, creates session, and sends preset message.
   */
  sendGuideAction: async (actionType) => {
    const messages = {
      publish: '帮我发布一个活动',
      search: '帮我搜索附近的活动',
      find: '帮我寻找可用的技能包',
    };
    const content = messages[actionType];
    if (!content) return;

    // Activate flux capability
    set({ activeCapability: 'flux', showGuidePage: false });

    // Create session if needed
    let sessionId = get().activeSessionId;
    if (!sessionId) {
      const session = await get().createSession();
      if (session) sessionId = session.id;
    }
    if (!sessionId) return;

    // Send the message
    get().sendMessage(content);
  },

  selectSession: async (sessionId) => {
    set({ activeSessionId: sessionId, messages: [], pendingFiles: [], uploadedAttachments: [], activeCapability: null, fluxOverviewData: null, fluxOverviewFetched: false, acpsOverviewData: null, acpsOverviewFetched: false, showGuidePage: false });
    // Load messages for the selected session
    try {
      const resp = await api.fetchMessages(sessionId);
      set({ messages: resp.messages || [] });
    } catch (error) {
      console.error('Failed to load messages:', error);
    }
    // Fetch sandbox diff for this session (shows toggle if there are changes)
    get().fetchSandboxDiff();
  },

  deleteSession: async (sessionId) => {
    try {
      await api.deleteSession(sessionId);
      set((state) => {
        const sessions = state.sessions.filter((s) => s.id !== sessionId);
        const newActive =
          state.activeSessionId === sessionId
            ? sessions.length > 0
              ? sessions[0].id
              : null
            : state.activeSessionId;
        return {
          sessions,
          activeSessionId: newActive,
          messages: state.activeSessionId === sessionId ? [] : state.messages,
        };
      });
      // If we switched to a new session, load its messages
      const newActive = get().activeSessionId;
      if (newActive && newActive !== sessionId) {
        get().selectSession(newActive);
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
    }
  },

  renameSession: async (sessionId, title) => {
    try {
      const updated = await api.updateSession(sessionId, title);
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.id === sessionId ? { ...s, title: updated.title } : s
        ),
      }));
    } catch (error) {
      console.error('Failed to rename session:', error);
    }
  },

  // --- Attachments ---
  pendingFiles: [],        // File objects waiting to be uploaded with next message
  uploadedAttachments: [], // Already-uploaded attachment metadata for this session

  addPendingFile: (file) => {
    set((state) => ({
      pendingFiles: [...state.pendingFiles, file],
    }));
  },

  removePendingFile: (index) => {
    set((state) => ({
      pendingFiles: state.pendingFiles.filter((_, i) => i !== index),
    }));
  },

  clearPendingFiles: () => {
    set({ pendingFiles: [], uploadedAttachments: [] });
  },

  // --- Capability Selection ---
  activeCapability: null,      // null | 'flux' | 'acps'
  fluxOverviewData: null,      // { infos: [], skills: [] } | null
  fluxOverviewLoading: false,
  fluxOverviewFetched: false,  // tracks whether we've fetched for this session
  acpsOverviewData: null,      // { agents: [] } | null
  acpsOverviewLoading: false,
  acpsOverviewFetched: false,

  setCapability: (cap) => {
    const current = get().activeCapability;
    if (current === cap) return; // no change
    set({
      activeCapability: cap,
      // Reset flux overview when switching away from flux
      ...(cap !== 'flux' ? { fluxOverviewData: null, fluxOverviewFetched: false } : {}),
      // Reset acps overview when switching away from acps
      ...(cap !== 'acps' ? { acpsOverviewData: null, acpsOverviewFetched: false } : {}),
    });
  },

  removeCapability: () => {
    set({
      activeCapability: null,
      fluxOverviewData: null,
      fluxOverviewFetched: false,
      acpsOverviewData: null,
      acpsOverviewFetched: false,
    });
  },

  fetchFluxOverview: async () => {
    if (get().fluxOverviewFetched || get().fluxOverviewLoading) return;
    set({ fluxOverviewLoading: true });
    try {
      const data = await api.fetchFluxOverview();
      set({
        fluxOverviewData: data,
        fluxOverviewLoading: false,
        fluxOverviewFetched: true,
      });
    } catch (error) {
      console.error('Failed to fetch flux overview:', error);
      set({ fluxOverviewLoading: false, fluxOverviewFetched: true });
    }
  },

  fetchAcpsOverview: async () => {
    if (get().acpsOverviewFetched || get().acpsOverviewLoading) return;
    set({ acpsOverviewLoading: true });
    try {
      const data = await api.fetchAcpsOverview();
      set({
        acpsOverviewData: data,
        acpsOverviewLoading: false,
        acpsOverviewFetched: true,
      });
    } catch (error) {
      console.error('Failed to fetch acps overview:', error);
      set({ acpsOverviewLoading: false, acpsOverviewFetched: true });
    }
  },

  // --- Messages & Chat ---
  messages: [],
  isStreaming: false,
  streamingContent: '',

  sendMessage: async (content) => {
    let { activeSessionId, pendingFiles } = get();
    if (!content.trim()) return;

    // Hide guide page once a message is sent
    set({ showGuidePage: false });

    // Auto-create session if none active
    if (!activeSessionId) {
      const session = await get().createSession();
      if (session) {
        activeSessionId = session.id;
      }
    }
    if (!activeSessionId) return;

    // Build attachment info for display (with object URLs for images)
    const attachmentInfos = pendingFiles.map((file) => ({
      id: null, // Will be filled after upload
      original_name: file.name,
      mime_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      objectUrl: file.type?.startsWith('image/') ? URL.createObjectURL(file) : null,
    }));

    // Add user message to local state immediately (with attachments for display)
    const userMsg = {
      role: 'user',
      content,
      attachments: attachmentInfos.length > 0 ? attachmentInfos : undefined,
    };
    set((state) => ({
      messages: [...state.messages, userMsg],
      isStreaming: true,
      streamingContent: '',
    }));

    try {
      // 1. Upload all pending files first
      const attachmentIds = [];
      for (let i = 0; i < pendingFiles.length; i++) {
        try {
          const result = await api.uploadAttachment(activeSessionId, pendingFiles[i]);
          attachmentIds.push(result.id);
          // Update the attachment ID in the message
          if (attachmentInfos[i]) {
            attachmentInfos[i].id = result.id;
          }
        } catch (uploadErr) {
          console.error('Failed to upload attachment:', uploadErr);
          // Continue with other files
        }
      }

      // Clear pending files after upload
      set({ pendingFiles: [], uploadedAttachments: [] });

      // 2. Stream the chat with attachment references
      let assistantContent = '';
      const toolCalls = [];

      await api.streamChat(activeSessionId, content, attachmentIds, get().activeCapability, (event) => {
        switch (event.type) {
          case 'token':
            assistantContent += event.content;
            set({ streamingContent: assistantContent });
            break;
          case 'tool_start':
            if (assistantContent.trim() && !assistantContent.endsWith('\n\n')) {
              if (!assistantContent.endsWith('\n')) assistantContent += '\n';
              assistantContent += '\n';
              set({ streamingContent: assistantContent });
            }
            toolCalls.push({
              name: event.name,
              input: event.input,
              status: 'running',
            });
            set((state) => ({
              messages: [
                ...state.messages.filter(
                  (m) => m._type !== 'streaming_tools'
                ),
                {
                  _type: 'streaming_tools',
                  role: 'tool_calls',
                  toolCalls: [...toolCalls],
                },
              ],
            }));
            break;
          case 'tool_end':
            const lastCall = toolCalls[toolCalls.length - 1];
            if (lastCall) {
              lastCall.status = 'done';
              lastCall.output = event.output;
            }
            set((state) => ({
              messages: [
                ...state.messages.filter(
                  (m) => m._type !== 'streaming_tools'
                ),
                {
                  _type: 'streaming_tools',
                  role: 'tool_calls',
                  toolCalls: [...toolCalls],
                },
              ],
            }));
            break;
          case 'error':
            assistantContent += `\n\n⚠️ 错误: ${event.message}`;
            set({ streamingContent: assistantContent });
            break;
          case 'done':
            break;
        }
      });

      // Finalize: add assistant message, clear streaming state
      set((state) => ({
        messages: [
          ...state.messages.filter((m) => m._type !== 'streaming_tools'),
          ...(toolCalls.length > 0
            ? [{ role: 'tool_calls', toolCalls, _type: 'final_tools' }]
            : []),
          { role: 'assistant', content: assistantContent },
        ],
        isStreaming: false,
        streamingContent: '',
      }));

      // Reload session list to get updated title
      get().loadSessions();
    } catch (error) {
      console.error('Chat failed:', error);
      set((state) => ({
        messages: [
          ...state.messages,
          {
            role: 'assistant',
            content: `⚠️ 发送失败: ${error.message}`,
          },
        ],
        isStreaming: false,
        streamingContent: '',
        pendingFiles: [],
      }));
    }
  },

  // --- Provider Management ---
  providers: [],
  activeProviderIndex: 0,

  loadProviders: async () => {
    try {
      const resp = await api.fetchProviders();
      set({
        providers: resp.providers || [],
        activeProviderIndex: resp.active_index ?? 0,
      });
    } catch (error) {
      console.error('Failed to load providers:', error);
    }
  },

  addProvider: async (data) => {
    try {
      const result = await api.createProvider(data);
      await get().loadProviders();
      return result;
    } catch (error) {
      console.error('Failed to add provider:', error);
      throw error;
    }
  },

  updateProvider: async (index, data) => {
    try {
      const result = await api.updateProvider(index, data);
      await get().loadProviders();
      return result;
    } catch (error) {
      console.error('Failed to update provider:', error);
      throw error;
    }
  },

  removeProvider: async (index) => {
    try {
      await api.deleteProvider(index);
      await get().loadProviders();
    } catch (error) {
      console.error('Failed to remove provider:', error);
      throw error;
    }
  },

  activateProvider: async (index) => {
    try {
      const result = await api.activateProvider(index);
      set({ activeProviderIndex: result.active_index });
      await get().loadProviders();
      return result;
    } catch (error) {
      console.error('Failed to activate provider:', error);
      throw error;
    }
  },

  // --- UI State ---
  sidebarOpen: true,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),

  // --- Sandbox State ---
  sandboxPanelOpen: false,
  sandboxDiffs: [],
  sandboxLoading: false,

  toggleSandboxPanel: () => set((state) => ({ sandboxPanelOpen: !state.sandboxPanelOpen })),

  fetchSandboxDiff: async () => {
    const sessionId = get().activeSessionId;
    set({ sandboxLoading: true });
    try {
      const resp = await api.fetchSandboxDiff(sessionId || '');
      set({ sandboxDiffs: resp.diffs || [], sandboxLoading: false });
    } catch (error) {
      console.error('Failed to fetch sandbox diff:', error);
      set({ sandboxDiffs: [], sandboxLoading: false });
    }
  },

  applySandboxChanges: async () => {
    const sessionId = get().activeSessionId;
    try {
      await api.applySandbox(sessionId || '');
      set({ sandboxDiffs: [], sandboxPanelOpen: false });
    } catch (error) {
      console.error('Failed to apply sandbox:', error);
      throw error;
    }
  },

  discardSandboxChanges: async () => {
    const sessionId = get().activeSessionId;
    try {
      await api.discardSandbox(sessionId || '');
      set({ sandboxDiffs: [], sandboxPanelOpen: false });
    } catch (error) {
      console.error('Failed to discard sandbox:', error);
      throw error;
    }
  },
}));

export default useStore;

