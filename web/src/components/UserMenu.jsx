import { useState, useRef, useEffect } from 'react';
import useStore from '../store/useStore';
import { fetchAtrStatus, registerAtr } from '../api/client';
import HeartbeatModal from './HeartbeatModal';
import ModelManager from './ModelManager';
import './UserMenu.css';

export default function UserMenu() {
  const { user, isLoggedIn, login, logout, deployMode } = useStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [atr, setAtr] = useState(null);
  const [loadingAtr, setLoadingAtr] = useState(false);
  const [endpointUrl, setEndpointUrl] = useState('');
  const [showHeartbeat, setShowHeartbeat] = useState(false);
  const [showModelManager, setShowModelManager] = useState(false);

  const menuRef = useRef(null);

  const loadAtrStatus = async () => {
    try {
      const status = await fetchAtrStatus();
      setAtr(status);
    } catch (e) {
      console.error("Failed to load ATR status", e);
    }
  };

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Fetch ATR status when menu opens
  useEffect(() => {
    if (isLoggedIn && menuOpen) {
      loadAtrStatus();
    }
  }, [isLoggedIn, menuOpen]);

  // Poll status periodically if registration is in progress
  useEffect(() => {
    let timer;
    if (isLoggedIn && atr?.registering) {
      timer = setInterval(loadAtrStatus, 1500);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [isLoggedIn, atr?.registering]);

  const handleRegisterAtr = async () => {
    if (loadingAtr || atr?.registering) return;
    const trimmed = endpointUrl.trim();
    if (!trimmed) {
      alert('请先输入智能体回调 Endpoint');
      return;
    }
    setLoadingAtr(true);
    try {
      await registerAtr(trimmed);
      await loadAtrStatus();
    } catch (e) {
      alert("注册智能体失败: " + e.message);
    } finally {
      setLoadingAtr(false);
    }
  };

  if (!isLoggedIn) {
    return (
      <button className="login-btn" onClick={login} id="login-btn">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
        <span>登录</span>
      </button>
    );
  }

  const initials = (user?.display_name || 'U')
    .split(' ')
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  return (
    <div className="user-menu" ref={menuRef}>
      <button
        className="user-avatar-btn"
        onClick={() => setMenuOpen(!menuOpen)}
        id="user-avatar-btn"
        aria-label="User menu"
      >
        <div className="user-avatar">{initials}</div>
      </button>

      {menuOpen && (
        <div className="user-dropdown animate-fade-in">
          <div className="user-dropdown-header">
            <div className="user-dropdown-avatar">{initials}</div>
            <div className="user-dropdown-info">
              <div className="user-dropdown-name">{user?.display_name}</div>
              <div className="user-dropdown-email">{user?.email}</div>
            </div>
          </div>
          <div className="user-dropdown-divider" />
          
          {/* ATR Trusted Registration Status Panel */}
          <div className="user-dropdown-atr">
            <div className="atr-status-header">
              <span className="atr-status-title">智能体注册状态</span>
              {atr?.registering ? (
                <span className="atr-badge badge-registering">
                  <span className="atr-spinner" />
                  注册中
                </span>
              ) : atr?.registered ? (
                <span className="atr-badge badge-registered">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  已注册
                </span>
              ) : (
                <span className="atr-badge badge-unregistered">未注册</span>
              )}
            </div>

            {atr?.registered && (
              <div className="atr-aic-box animate-fade-in" title={atr.agent_aic}>
                <span className="atr-aic-label">智能体id:</span>
                <span className="atr-aic-value">{atr.agent_aic}</span>
              </div>
            )}

            {!atr?.registered && !atr?.registering && (
              <div className="atr-action-box">
                {atr?.error && (
                  <div className="atr-error-text" title={atr.error}>
                    ⚠️ {atr.error.length > 50 ? atr.error.slice(0, 50) + "..." : atr.error}
                  </div>
                )}
                <div className="atr-endpoint-group">
                  <label className="atr-endpoint-label">回调 Endpoint</label>
                  <input
                    className="atr-endpoint-input"
                    type="text"
                    placeholder="例如 http://10.0.0.1:7014"
                    value={endpointUrl}
                    onChange={(e) => setEndpointUrl(e.target.value)}
                    disabled={loadingAtr}
                  />
                </div>
                <button
                  className="atr-btn"
                  onClick={handleRegisterAtr}
                  disabled={loadingAtr || !endpointUrl.trim()}
                >
                  {loadingAtr ? "正在启动..." : "一键注册智能体"}
                </button>
              </div>
            )}
          </div>

          <div className="user-dropdown-divider" />

          {/* Model Management */}
          <button
            className="user-dropdown-item"
            onClick={() => {
              setShowModelManager(true);
              setMenuOpen(false);
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
            模型管理
          </button>

          {/* Task Management (PC mode only) */}
          {deployMode !== 'saas' && (
            <button
              className="user-dropdown-item"
              onClick={() => {
                setShowHeartbeat(true);
                setMenuOpen(false);
              }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              任务管理
            </button>
          )}
          <button
            className="user-dropdown-item"
            onClick={() => {
              logout();
              setMenuOpen(false);
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
            退出登录
          </button>
        </div>
      )}

      {/* Heartbeat Task Manager Modal (PC mode only) */}
      {deployMode !== 'saas' && showHeartbeat && (
        <HeartbeatModal onClose={() => setShowHeartbeat(false)} />
      )}

      {/* Model Manager Modal */}
      {showModelManager && (
        <ModelManager onClose={() => setShowModelManager(false)} />
      )}
    </div>
  );
}
