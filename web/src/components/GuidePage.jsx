import useStore from '../store/useStore';
import './GuidePage.css';

/**
 * GuidePage — introductory landing page shown when no active session exists.
 * Displays Flux platform capabilities with interactive action buttons.
 */

const GUIDE_ACTIONS = [
  {
    key: 'publish',
    icon: (
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
        <line x1="16" y1="2" x2="16" y2="6" />
        <line x1="8" y1="2" x2="8" y2="6" />
        <line x1="3" y1="10" x2="21" y2="10" />
        <line x1="12" y1="14" x2="12" y2="18" />
        <line x1="10" y1="16" x2="14" y2="16" />
      </svg>
    ),
    title: '发布活动：',
    desc: '轻松发布各类交友、运动或社区活动',
    action: '去发布 →',
  },
  {
    key: 'search',
    icon: (
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
        <circle cx="11" cy="11" r="3" />
      </svg>
    ),
    title: '搜索活动：',
    desc: '发现附近的各类精彩活动和团队',
    action: '去搜索 →',
  },
  {
    key: 'find',
    icon: (
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
      </svg>
    ),
    title: '搜索技能：',
    desc: '寻找特定领域的达人或服务',
    action: '去寻找 →',
  },
];

export default function GuidePage() {
  const { sendGuideAction, isStreaming } = useStore();

  const handleAction = (actionKey) => {
    if (isStreaming) return;
    sendGuideAction(actionKey);
  };

  return (
    <div className="guide-page" id="guide-page">
      <div className="guide-card">
        {/* Header */}
        <div className="guide-header">
          <div className="guide-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="url(#bolt-grad)" stroke="none" />
              <defs>
                <linearGradient id="bolt-grad" x1="3" y1="2" x2="21" y2="22">
                  <stop offset="0%" stopColor="#f59e0b" />
                  <stop offset="100%" stopColor="#ef4444" />
                </linearGradient>
              </defs>
            </svg>
          </div>
          <h2 className="guide-title">公告板智能体概览</h2>
          <p className="guide-subtitle">
            发布活动 · 搜索活动 · 搜索技能
          </p>
        </div>

        {/* Action Items */}
        <div className="guide-actions">
          {GUIDE_ACTIONS.map((item, index) => (
            <div key={item.key} className="guide-action-item" style={{ animationDelay: `${index * 0.08}s` }}>
              <div className="guide-action-icon">{item.icon}</div>
              <div className="guide-action-content">
                <span className="guide-action-title">{item.title}</span>
                <span className="guide-action-desc">{item.desc}</span>
              </div>
              <button
                className="guide-action-btn"
                onClick={() => handleAction(item.key)}
                disabled={isStreaming}
              >
                {item.action}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
