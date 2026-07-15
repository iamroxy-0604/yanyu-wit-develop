import { useEffect } from 'react';
import useStore from '../store/useStore';
import './AcpsOverview.css';

/**
 * AcpsOverview — displays ACPs (Agent Collaboration Protocols) trending/recommended agents
 * in a stunning glassmorphism card layout when ACPs capability is first activated in a new session.
 */
export default function AcpsOverview() {
  const {
    acpsOverviewData,
    acpsOverviewLoading,
    fetchAcpsOverview,
  } = useStore();

  useEffect(() => {
    fetchAcpsOverview();
  }, [fetchAcpsOverview]);

  if (acpsOverviewLoading) {
    return (
      <div className="acps-overview" id="acps-overview">
        <div className="acps-overview-header">
          <div className="acps-overview-icon">🌐</div>
          <h2 className="acps-overview-title">智能体互联</h2>
          <p className="acps-overview-subtitle">正在通过发现协议（ADP）检索推荐智能体...</p>
        </div>
        <div className="acps-skeleton-grid">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="acps-skeleton-card" style={{ animationDelay: `${i * 0.08}s` }}>
              <div className="acps-skeleton-line acps-skeleton-title" />
              <div className="acps-skeleton-line acps-skeleton-desc" />
              <div className="acps-skeleton-line acps-skeleton-desc" />
              <div className="acps-skeleton-line acps-skeleton-desc-short" />
              <div className="acps-skeleton-tags">
                <div className="acps-skeleton-line acps-skeleton-tag" />
                <div className="acps-skeleton-line acps-skeleton-tag" />
              </div>
              <div className="acps-skeleton-line acps-skeleton-footer" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const agents = acpsOverviewData?.agents || [];

  const handleLearnMore = () => {
    window.open('https://www.ioa.pub', '_blank');
  };

  return (
    <div className="acps-overview" id="acps-overview">
      {/* Header */}
      <div className="acps-overview-header">
        <div className="acps-overview-icon">🌐</div>
        <h2 className="acps-overview-title">智能体互联发现</h2>
        <p className="acps-overview-subtitle">
          智能体发现过程 (ADP) — 发现、连接并协同全球异构智能体共同完成任务
        </p>
      </div>

      {/* Agents Section */}
      {agents.length > 0 ? (
        <div className="acps-section">
          <div className="acps-section-header">
            <div className="acps-section-icon">✨</div>
            <h3 className="acps-section-title">推荐协同智能体</h3>
            <span className="acps-section-count">{agents.length} 个活跃</span>
          </div>
          <div className="acps-card-grid">
            {agents.map((agent, index) => (
              <AgentCard key={agent.aic || index} agent={agent} index={index} />
            ))}
          </div>
          
          <button className="acps-view-more" onClick={handleLearnMore}>
            <span>了解更多 (ioa.pub)</span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        </div>
      ) : (
        <div className="acps-empty">
          <p>暂时没有可展示的推荐智能体</p>
          <p className="acps-empty-hint">发现服务器可能暂时无法访问，请稍后再试</p>
        </div>
      )}
    </div>
  );
}

/**
 * AgentCard — displays core information of an agent.
 */
function AgentCard({ agent, index }) {
  const name = agent.name || '未命名智能体';
  const description = agent.description || '暂无描述';
  const active = agent.active !== false;
  const provider = agent.provider || '未知提供商';
  const skills = agent.skills || [];
  const aic = agent.aic || '';

  // Get first 6 digits of AIC for display if long
  const displayAic = aic.length > 12 ? aic.slice(0, 8) + '...' + aic.slice(-4) : aic;

  return (
    <div
      className="acps-card"
      style={{ animationDelay: `${index * 0.05}s` }}
    >
      <div className="acps-card-top">
        <span className="acps-agent-name">{name}</span>
        <span className={`acps-agent-status ${active ? 'status-active' : 'status-inactive'}`}>
          {active ? '活跃' : '离线'}
        </span>
      </div>
      <p className="acps-card-desc">{description}</p>
      
      {skills.length > 0 && (
        <div className="acps-skills-list">
          {skills.map((skill, i) => (
            <span key={i} className="acps-skill-tag">🏷️ {skill}</span>
          ))}
        </div>
      )}

      <div className="acps-card-footer">
        <span className="acps-agent-provider" title={provider}>🏢 {provider}</span>
        {aic && <span className="acps-agent-aic" title={aic}>AIC: {displayAic}</span>}
      </div>
    </div>
  );
}
