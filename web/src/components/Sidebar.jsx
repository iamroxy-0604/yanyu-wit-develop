import { useState } from 'react';
import useStore from '../store/useStore';
import './Sidebar.css';

export default function Sidebar() {
  const {
    sessions,
    activeSessionId,
    startNewChat,
    selectSession,
    deleteSession,
    isLoggedIn,
  } = useStore();

  const [hoveredId, setHoveredId] = useState(null);

  const handleNewChat = () => {
    if (!isLoggedIn) return;
    startNewChat();
  };

  const handleDelete = (e, sessionId) => {
    e.stopPropagation();
    deleteSession(sessionId);
  };

  return (
    <div className="sidebar" id="sidebar">
      {/* New Chat Button */}
      <div className="sidebar-top">
        <button
          className="new-chat-btn"
          onClick={handleNewChat}
          disabled={!isLoggedIn}
          id="new-chat-btn"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>新建对话</span>
        </button>
      </div>

      {/* Session List */}
      <div className="sidebar-sessions">
        {!isLoggedIn ? (
          <div className="sidebar-empty">
            <p>请先登录以查看对话</p>
          </div>
        ) : sessions.length === 0 ? (
          <div className="sidebar-empty">
            <p>暂无对话</p>
            <p className="sidebar-empty-hint">点击上方按钮开始新对话</p>
          </div>
        ) : (
          <>
            <div className="sidebar-section-label">对话</div>
            <ul className="session-list">
              {sessions.map((session) => (
                <li
                  key={session.id}
                  className={`session-item ${
                    session.id === activeSessionId ? 'active' : ''
                  }`}
                  onClick={() => selectSession(session.id)}
                  onMouseEnter={() => setHoveredId(session.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  id={`session-${session.id}`}
                >
                  <svg
                    className="session-icon"
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  >
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                  <span className="session-title">
                    {session.title || '新对话'}
                  </span>
                  {(hoveredId === session.id ||
                    session.id === activeSessionId) && (
                    <button
                      className="session-delete-btn"
                      onClick={(e) => handleDelete(e, session.id)}
                      aria-label="删除对话"
                      title="删除对话"
                    >
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
