import { useState, useRef, useEffect, useCallback } from 'react';
import useStore from '../store/useStore';
import './InputBar.css';

const SUGGESTIONS = [
  // { text: '你能做什么', icon: '🟦', capability: null },
  // { text: '帮我看看有哪些技能包', icon: '⚡', capability: 'flux' },
];

/**
 * Format file size for display.
 */
function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Get an icon for a MIME type.
 */
function getFileIcon(file) {
  if (file.type.startsWith('image/')) return '🖼️';
  if (file.type.includes('zip') || file.type.includes('gzip') || file.type.includes('compressed')) return '📦';
  if (file.type.includes('pdf')) return '📄';
  if (file.type.startsWith('text/')) return '📝';
  return '📎';
}

export default function InputBar() {
  const [input, setInput] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const menuRef = useRef(null);
  const modelMenuRef = useRef(null);
  const {
    sendMessage,
    isStreaming,
    activeSessionId,
    isLoggedIn,
    pendingFiles,
    addPendingFile,
    removePendingFile,
    messages,
    activeCapability,
    setCapability,
    removeCapability,
    fetchFluxOverview,
    fetchAcpsOverview,
    providers,
    activeProviderIndex,
    activateProvider,
    deployMode,
  } = useStore();

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    }
  }, [input]);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    if (menuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [menuOpen]);

  // Close model menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target)) {
        setModelMenuOpen(false);
      }
    };
    if (modelMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [modelMenuOpen]);

  // Add files from various sources
  const handleFilesAdd = useCallback((files) => {
    if (!files || files.length === 0) return;
    for (const file of files) {
      addPendingFile(file);
    }
  }, [addPendingFile]);

  // File input change handler
  const handleFileSelect = (e) => {
    handleFilesAdd(e.target.files);
    // Reset the input so the same file can be selected again
    e.target.value = '';
    setMenuOpen(false);
  };

  // Paste handler for clipboard images
  const handlePaste = useCallback((e) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const files = [];
    for (const item of items) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }

    if (files.length > 0) {
      e.preventDefault(); // Prevent pasting image data as text
      handleFilesAdd(files);
    }
  }, [handleFilesAdd]);

  // Drag and drop handlers
  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    handleFilesAdd(e.dataTransfer.files);
  }, [handleFilesAdd]);

  const handleSubmit = async () => {
    if (!input.trim() || isStreaming) return;
    const content = input.trim();
    setInput('');
    sendMessage(content);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const isDisabled = !isLoggedIn || isStreaming;
  const showSuggestions = isLoggedIn && messages.length === 0 && !isStreaming && !activeSessionId;

  const handleSuggestionClick = async (suggestion) => {
    if (isDisabled) return;

    if (suggestion.capability) {
      setCapability(suggestion.capability);
    }

    setInput('');
    sendMessage(suggestion.text);
  };

  // Handle capability selection from menu
  const handleSelectCapability = (cap) => {
    setCapability(cap);
    setMenuOpen(false);

    // If selecting flux for the first time in a session with no messages, fetch overview
    if (cap === 'flux' && messages.length === 0) {
      fetchFluxOverview();
    } else if (cap === 'acps' && messages.length === 0) {
      fetchAcpsOverview();
    }
  };

  return (
    <div
      className={`input-bar-wrapper ${isDragOver ? 'drag-over' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="drag-overlay">
          <div className="drag-overlay-content">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span>拖放文件到此处</span>
          </div>
        </div>
      )}

      {/* Candidate suggestions */}
      {showSuggestions && (
        <div className="suggestion-chips" id="suggestion-chips">
          {SUGGESTIONS.map((s) => (
            <button
              key={s.text}
              className="suggestion-chip"
              onClick={() => handleSuggestionClick(s)}
            >
              <span className="suggestion-chip-icon">{s.icon}</span>
              <span className="suggestion-chip-text">{s.text}</span>
            </button>
          ))}
        </div>
      )}

      {/* File previews — displayed above the input bar */}
      {pendingFiles.length > 0 && (
        <div className="input-files-tray" id="input-files-tray">
          {pendingFiles.map((file, index) => (
            <AttachmentChip
              key={`${file.name}-${index}`}
              file={file}
              onRemove={() => removePendingFile(index)}
            />
          ))}
        </div>
      )}

      <div className={`input-bar ${pendingFiles.length > 0 ? 'has-files' : ''}`} id="input-bar">
        {/* Plus / Menu button */}
        <div className="plus-menu-container" ref={menuRef}>
          <button
            className={`plus-btn ${menuOpen ? 'open' : ''}`}
            onClick={() => setMenuOpen(!menuOpen)}
            disabled={isDisabled}
            aria-label="打开功能菜单"
            title="功能菜单"
            id="plus-btn"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>

          {/* Popup menu */}
          {menuOpen && (
            <div className="plus-menu" id="plus-menu">
              {/* Upload file */}
              <button
                className="plus-menu-item"
                onClick={() => fileInputRef.current?.click()}
              >
                <span className="plus-menu-item-icon">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                </span>
                <span className="plus-menu-item-label">上传文件</span>
              </button>

              <div className="plus-menu-divider" />

              {/* Flux capability */}
              <button
                className={`plus-menu-item ${activeCapability === 'flux' ? 'active' : ''}`}
                onClick={() => handleSelectCapability('flux')}
              >
                <span className="plus-menu-item-icon flux-icon">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                  </svg>
                </span>
                <span className="plus-menu-item-label">信息看板</span>
                {activeCapability === 'flux' && (
                  <span className="plus-menu-item-check">✓</span>
                )}
              </button>

              {/* ACPs capability */}
              <button
                className={`plus-menu-item ${activeCapability === 'acps' ? 'active' : ''}`}
                onClick={() => handleSelectCapability('acps')}
              >
                <span className="plus-menu-item-icon acps-icon">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                  </svg>
                </span>
                <span className="plus-menu-item-label">智能体互联</span>
                {activeCapability === 'acps' && (
                  <span className="plus-menu-item-check">✓</span>
                )}
              </button>

            </div>
          )}
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={handleFileSelect}
          accept="image/*,.zip,.gz,.pdf,.txt,.md,.py,.js,.json,.csv,.xml,.yaml,.yml"
          id="file-input-hidden"
        />

        <textarea
          ref={textareaRef}
          className="input-textarea"
          placeholder={
            !isLoggedIn
              ? '请先登录...'
              : isStreaming
                ? '等待回复中...'
                : pendingFiles.length > 0
                  ? '输入消息描述你想用这些文件做什么...'
                  : '输入消息... (可粘贴/拖拽文件)'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          disabled={isDisabled}
          rows={1}
          id="message-input"
        />

        {/* Model selector */}
        {isLoggedIn && providers.length > 0 && (
          <div className="model-selector-container" ref={modelMenuRef}>
            <button
              className="model-selector-btn"
              onClick={() => setModelMenuOpen(!modelMenuOpen)}
              title="切换模型"
              id="model-selector-btn"
            >
              <span className="model-selector-name">
                {providers[activeProviderIndex]?.name || '模型'}
              </span>
              <svg width="10" height="6" viewBox="0 0 10 6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M1 1L5 5L9 1" />
              </svg>
            </button>

            {modelMenuOpen && (
              <div className="model-selector-dropdown" id="model-selector-dropdown">
                {providers.map((p, i) => (
                  <button
                    key={i}
                    className={`model-selector-item ${i === activeProviderIndex ? 'active' : ''}`}
                    onClick={async () => {
                      if (i !== activeProviderIndex) {
                        try {
                          await activateProvider(i);
                        } catch (e) {
                          console.error('Failed to switch provider:', e);
                        }
                      }
                      setModelMenuOpen(false);
                    }}
                  >
                    <span className="model-selector-item-name">{p.name}</span>
                    <span className="model-selector-item-type">{p.type}</span>
                    {i === activeProviderIndex && (
                      <svg className="model-selector-item-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <button
          className={`send-btn ${input.trim() && !isDisabled ? 'active' : ''}`}
          onClick={handleSubmit}
          disabled={!input.trim() || isDisabled}
          aria-label="发送消息"
          id="send-btn"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        </button>
      </div>

      {/* Capability chip — displayed below the input bar */}
      {activeCapability && (
        <div className="capability-bar">
          <div className="capability-chip" data-capability={activeCapability}>
            <span className="capability-chip-icon">
              {activeCapability === 'flux' ? '⚡' : '🛡️'}
            </span>
            <span className="capability-chip-label">
              {activeCapability === 'flux' ? '信息看板' : '智能体互联'}
            </span>
            <button
              className="capability-chip-remove"
              onClick={removeCapability}
              aria-label="移除能力"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <p className="input-disclaimer">
        YanYu-Wit 可能会出错，请仔细甄别回复内容
      </p>
    </div>
  );
}


/**
 * AttachmentChip — renders a single pending file with preview.
 */
function AttachmentChip({ file, onRemove }) {
  const [thumbnail, setThumbnail] = useState(null);

  useEffect(() => {
    // Generate thumbnail for image files
    if (file.type.startsWith('image/')) {
      const url = URL.createObjectURL(file);
      setThumbnail(url);
      return () => URL.revokeObjectURL(url);
    }
  }, [file]);

  return (
    <div className="attachment-chip" title={`${file.name} (${formatSize(file.size)})`}>
      {thumbnail ? (
        <img src={thumbnail} alt={file.name} className="attachment-thumbnail" />
      ) : (
        <span className="attachment-icon">{getFileIcon(file)}</span>
      )}
      <div className="attachment-info">
        <span className="attachment-name">{file.name}</span>
        <span className="attachment-size">{formatSize(file.size)}</span>
      </div>
      <button className="attachment-remove" onClick={onRemove} aria-label="删除附件">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}
