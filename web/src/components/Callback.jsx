/**
 * Callback Component
 * ====================
 * Handles the OIDC redirect from Keycloak.
 * Extracts `code` and `state` from the URL, sends them to the backend
 * to exchange for an app token, then redirects to the main app.
 */

import { useEffect, useState, useRef } from 'react';
import { exchangeToken, setToken } from '../api/client';
import './Callback.css';

export default function Callback({ onLoginSuccess }) {
  const [status, setStatus] = useState('processing');
  const [error, setError] = useState(null);
  const effectRun = useRef(false);

  useEffect(() => {
    if (effectRun.current) return;
    effectRun.current = true;

    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');
    const errorParam = params.get('error');

    if (errorParam) {
      setStatus('error');
      setError(`Keycloak 登录失败: ${errorParam} - ${params.get('error_description') || ''}`);
      return;
    }

    if (!code || !state) {
      setStatus('error');
      setError('回调缺少 code 或 state 参数');
      return;
    }

    // Exchange code for token
    exchangeToken(code, state)
      .then((resp) => {
        // Store the app token
        setToken(resp.access_token);
        setStatus('success');

        // Notify parent and redirect after brief success display
        setTimeout(() => {
          // Clean callback params from URL
          window.history.replaceState({}, '', '/');
          onLoginSuccess(resp.user);
        }, 800);
      })
      .catch((err) => {
        setStatus('error');
        setError(err.message || '登录失败');
      });
  }, []);

  return (
    <div className="callback-container">
      <div className="callback-card">
        {status === 'processing' && (
          <>
            <div className="callback-spinner" />
            <h2 className="callback-title">正在登录...</h2>
            <p className="callback-subtitle">正在与身份认证服务通信</p>
          </>
        )}
        {status === 'success' && (
          <>
            <div className="callback-icon callback-success-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <h2 className="callback-title">登录成功</h2>
            <p className="callback-subtitle">正在跳转...</p>
          </>
        )}
        {status === 'error' && (
          <>
            <div className="callback-icon callback-error-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
            </div>
            <h2 className="callback-title">登录失败</h2>
            <p className="callback-error">{error}</p>
            <button
              className="callback-retry-btn"
              onClick={() => { window.location.href = '/'; }}
            >
              返回重试
            </button>
          </>
        )}
      </div>
    </div>
  );
}
