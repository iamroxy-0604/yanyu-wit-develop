import { useEffect, useRef, useMemo } from 'react';
import useStore from '../store/useStore';
import MessageBubble from './MessageBubble';
import InputBar from './InputBar';
import FluxOverview from './FluxOverview';
import AcpsOverview from './AcpsOverview';
import GuidePage from './GuidePage';
import SandboxPanel from './SandboxPanel';
import './ChatArea.css';

export default function ChatArea() {
  const {
    messages,
    isStreaming,
    streamingContent,
    activeSessionId,
    isLoggedIn,
    activeCapability,
    fluxOverviewData,
    fluxOverviewLoading,
    acpsOverviewData,
    acpsOverviewLoading,
    showGuidePage,
    sandboxDiffs,
    sandboxPanelOpen,
    toggleSandboxPanel,
    fetchSandboxDiff,
  } = useStore();

  const messagesEndRef = useRef(null);
  const chatContainerRef = useRef(null);

  const prevStreamingRef = useRef(false);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, streamingContent]);

  // Auto-fetch sandbox diff when streaming finishes
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      fetchSandboxDiff();
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming]);

  // Fetch sandbox diff on mount / login
  useEffect(() => {
    if (isLoggedIn) {
      fetchSandboxDiff();
    }
  }, [isLoggedIn]);

  // Normalize messages unconditionally (React Hooks rule)
  const displayMessages = useMemo(() => {
    const result = [];
    
    let currentAssistantText = [];
    let currentToolCalls = [];
    let currentAttachments = undefined;
    
    const flushAssistant = () => {
       if (currentToolCalls.length > 0) {
          result.push({ role: 'tool_calls', toolCalls: currentToolCalls });
       }
       if (currentAssistantText.length > 0) {
          result.push({ 
              role: 'assistant', 
              content: currentAssistantText.join('\n\n'),
              attachments: currentAttachments 
          });
       }
       currentAssistantText = [];
       currentToolCalls = [];
       currentAttachments = undefined;
    };

    messages.forEach((msg) => {
      // Synthetic tool calls (from streaming state in store)
      if (msg._type === 'streaming_tools' || msg._type === 'final_tools') {
        flushAssistant();
        result.push({ ...msg, toolCalls: [...msg.toolCalls] });
        return;
      }

      // User messages
      if (msg.role === 'user') {
        flushAssistant();
        result.push(msg);
        return;
      }

      // Raw Assistant messages from DB
      if (msg.role === 'assistant') {
        if (msg.content) {
           currentAssistantText.push(msg.content);
        }
        if (msg.attachments) {
           currentAttachments = msg.attachments;
        }
        const calls = msg.tool_calls || msg.toolCalls;
        if (calls && calls.length > 0) {
           calls.forEach(c => {
              currentToolCalls.push({
                 id: c.id,
                 name: c.name,
                 input: c.args || c.input,
                 status: 'running'
              });
           });
        }
      }

      // Tool result from DB
      if (msg.role === 'tool') {
         const target = currentToolCalls.find(c => c.id === msg.tool_call_id)
                     || currentToolCalls.find(c => c.name === msg.name && c.status === 'running');
         if (target) {
            target.output = msg.content;
            target.status = 'done';
         } else {
            currentToolCalls.push({ name: msg.name, output: msg.content, status: 'done' });
         }
      }
    });

    flushAssistant();

    // Cleanup: If not streaming, ensure no tools are stuck in 'running' state
    if (!isStreaming) {
      result.forEach(r => {
         if (r.role === 'tool_calls') {
             r.toolCalls.forEach(tc => {
                 if (tc.status === 'running') {
                     tc.status = 'done';
                     if (!tc.output) tc.output = '(No output recorded / Execution interrupted)';
                 }
             });
         }
      });
    }

    return result;
  }, [messages, isStreaming]);

  // Welcome screen when no session is active
  if (!isLoggedIn) {
    return (
      <div className="chat-area" id="chat-area">
        <div className="chat-column">
          <div className="chat-welcome">
            <h2 className="welcome-title">
              <span className="welcome-greeting">你好 👋</span>
              <span className="welcome-subtitle">欢迎使用 YanYu-Wit</span>
            </h2>
            <p className="welcome-hint">请先登录以开始对话</p>
          </div>
        </div>
      </div>
    );
  }

  if (!activeSessionId && !showGuidePage) {
    return (
      <div className="chat-area" id="chat-area">
        <div className="chat-column">
          <div className="chat-welcome">
            <h2 className="welcome-title">
              <span className="welcome-greeting">
                {`你好，${useStore.getState().user?.display_name || ''}`}
              </span>
              <span className="welcome-subtitle">今天想聊点什么？</span>
            </h2>
          </div>
          <InputBar />
        </div>
      </div>
    );
  }


  const hasMessages = displayMessages.length > 0 || isStreaming;
  const showFluxOverview = activeCapability === 'flux' && !hasMessages && (fluxOverviewData || fluxOverviewLoading);
  const showAcpsOverview = activeCapability === 'acps' && !hasMessages && (acpsOverviewData || acpsOverviewLoading);
  const shouldShowGuide = showGuidePage && !hasMessages && !showFluxOverview && !showAcpsOverview;

  return (
    <div className={`chat-area ${sandboxPanelOpen ? 'sandbox-open' : ''}`} id="chat-area">
      {/* Chat Column */}
      <div className="chat-column">
        {/* Messages */}
        <div className="chat-messages" ref={chatContainerRef}>
          <div className="chat-messages-inner">
            {/* Guide Page — shown on fresh/new chat */}
            {shouldShowGuide && <GuidePage />}

            {/* Flux Overview — shown when flux is active on a fresh session */}
            {showFluxOverview && <FluxOverview />}

            {/* ACPs Overview — shown when acps is active on a fresh session */}
            {showAcpsOverview && <AcpsOverview />}

            {!hasMessages && !showFluxOverview && !showAcpsOverview && !shouldShowGuide && (
              <div className="chat-empty">
                <p>发送消息开始对话 ✨</p>
              </div>
            )}

            {displayMessages.map((msg, index) => (
              <MessageBubble key={index} message={msg} />
            ))}

            {/* Streaming assistant message */}
            {isStreaming && streamingContent && (
              <MessageBubble
                message={{ role: 'assistant', content: streamingContent }}
                isStreaming={true}
              />
            )}

            {/* Streaming indicator */}
            {isStreaming && !streamingContent && displayMessages[displayMessages.length - 1]?.role !== 'tool_calls' && (
              <div className="typing-indicator">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input */}
        <InputBar />
      </div>

      {/* Sandbox Column — slides in from right */}
      <div className={`sandbox-column ${sandboxPanelOpen ? 'open' : ''}`}>
        {sandboxPanelOpen && <SandboxPanel />}
      </div>

      {/* Sandbox toggle button (shown only when there are diffs) */}
      {sandboxDiffs.length > 0 && !sandboxPanelOpen && (
        <button
          className="sandbox-toggle-btn"
          onClick={toggleSandboxPanel}
          title="查看沙箱变更"
          id="sandbox-toggle"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 6h12M4 10h12M4 14h8" />
          </svg>
          <span className="sandbox-toggle-label">
            {sandboxDiffs.length} 个变更
          </span>
        </button>
      )}
    </div>
  );
}

