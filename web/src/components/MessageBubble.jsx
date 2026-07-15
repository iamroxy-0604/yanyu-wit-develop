import { useState, useEffect } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './MessageBubble.css';

/**
 * Format file size for display.
 */
function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MessageBubble({ message, isStreaming = false }) {
  const { role, content, toolCalls, attachments } = message;

  // Tool calls display (Accordion Group)
  if (role === 'tool_calls') {
    return <ToolCallGroup toolCalls={toolCalls} />;
  }

  // Skip tool messages (they're embedded in tool_calls)
  if (role === 'tool') return null;

  const isUser = role === 'user';

  return (
    <div
      className={`message-row ${isUser ? 'user' : 'assistant'} animate-fade-in`}
    >
      {!isUser && (
        <div className="message-avatar assistant-avatar">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2L2 7l10 5 10-5-10-5z" />
            <path d="M2 17l10 5 10-5" />
            <path d="M2 12l10 5 10-5" />
          </svg>
        </div>
      )}
      <div className={`message-bubble ${isUser ? 'user-bubble' : 'assistant-bubble'}`}>
        {/* Attachment previews above user messages */}
        {isUser && attachments && attachments.length > 0 && (
          <div className="msg-attachments">
            {attachments.map((att, i) => (
              <MessageAttachment key={att.id || i} attachment={att} />
            ))}
          </div>
        )}
        {isUser ? (
          <p className="message-text">{content}</p>
        ) : (
          <div className="markdown-content">
            <Markdown remarkPlugins={[remarkGfm]}>{content || ''}</Markdown>
            {isStreaming && <span className="streaming-cursor">▌</span>}
          </div>
        )}
      </div>
    </div>
  );
}


/**
 * MessageAttachment — renders an attachment preview inside a chat message.
 * Images get a full preview; other files get an icon chip.
 */
function MessageAttachment({ attachment }) {
  const { original_name, mime_type, size_bytes, objectUrl } = attachment;
  const isImage = mime_type && mime_type.startsWith('image/');

  return (
    <div className="msg-attachment-item">
      {isImage && objectUrl ? (
        <div className="msg-attachment-image-wrap">
          <img
            src={objectUrl}
            alt={original_name}
            className="msg-attachment-image"
            loading="lazy"
          />
        </div>
      ) : (
        <div className="msg-attachment-file">
          <span className="msg-attachment-file-icon">
            {getFileIcon(mime_type)}
          </span>
          <div className="msg-attachment-file-info">
            <span className="msg-attachment-file-name">{original_name}</span>
            <span className="msg-attachment-file-size">{formatSize(size_bytes)}</span>
          </div>
        </div>
      )}
    </div>
  );
}


function getFileIcon(mimeType) {
  if (!mimeType) return '📎';
  if (mimeType.startsWith('image/')) return '🖼️';
  if (mimeType.includes('zip') || mimeType.includes('gzip') || mimeType.includes('compressed')) return '📦';
  if (mimeType.includes('pdf')) return '📄';
  if (mimeType.startsWith('text/')) return '📝';
  return '📎';
}

function ToolCallGroup({ toolCalls }) {
  const hasRunning = toolCalls?.some(tc => tc.status === 'running');
  // Always collapsed by default, only expand on manual click
  const [expanded, setExpanded] = useState(false);

  if (!toolCalls || toolCalls.length === 0) return null;

  const doneCount = toolCalls.filter(t => t.status === 'done').length;

  return (
    <div className="message-row tool-calls-row animate-fade-in">
      <div className="tool-call-group">
        <button 
          className={`tool-group-header ${hasRunning ? 'running' : ''}`}
          onClick={() => setExpanded(!expanded)}
        >
           <span className="tool-group-icon">
             {hasRunning ? (
               <svg className="spinning" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                 <path d="M21 12a9 9 0 11-6.219-8.56" />
               </svg>
             ) : (
               <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                 <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
               </svg>
             )}
           </span>
           <span className="tool-group-title">
             {hasRunning 
               ? `正在执行工具 (${doneCount + 1}/${toolCalls.length})...`
               : `调用了 ${toolCalls.length} 个工具`
             }
           </span>
           <svg
             className={`tool-group-chevron ${expanded ? 'expanded' : ''}`}
             width="14"
             height="14"
             viewBox="0 0 24 24"
             fill="none"
             stroke="currentColor"
             strokeWidth="2"
           >
             <polyline points="6 9 12 15 18 9" />
           </svg>
        </button>

        {expanded && (
          <div className="tool-calls-container">
            {toolCalls.map((tc, i) => (
              <ToolCallCard key={i} toolCall={tc} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolCallCard({ toolCall }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`tool-call-card ${toolCall.status === 'running' ? 'running' : ''}`}>
      <button
        className="tool-call-header"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="tool-call-icon">
          {toolCall.status === 'running' ? (
            <svg className="spinning" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 12a9 9 0 11-6.219-8.56" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          )}
        </span>
        <span className="tool-call-name">{toolCall.name}</span>
        <svg
          className={`tool-call-chevron ${expanded ? 'expanded' : ''}`}
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {expanded && (
        <div className="tool-call-details">
          {toolCall.input && (
            <div className="tool-call-section">
              <div className="tool-call-label">参数</div>
              <pre className="tool-call-code">
                {typeof toolCall.input === 'string'
                  ? toolCall.input
                  : JSON.stringify(toolCall.input, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.output && (
            <div className="tool-call-section">
              <div className="tool-call-label">结果</div>
              <pre className="tool-call-code">{toolCall.output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
