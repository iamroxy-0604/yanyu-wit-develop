import { useEffect } from 'react';
import useStore from '../store/useStore';
import './FluxOverview.css';

/**
 * FluxOverview — displays Flux platform infos and skills
 * in a visually stunning card layout when Flux capability
 * is first activated in a new session.
 */
export default function FluxOverview() {
  const {
    fluxOverviewData,
    fluxOverviewLoading,
    fetchFluxOverview,
  } = useStore();

  useEffect(() => {
    fetchFluxOverview();
  }, [fetchFluxOverview]);

  if (fluxOverviewLoading) {
    return (
      <div className="flux-overview" id="flux-overview">
        <div className="flux-overview-header">
          <div className="flux-overview-icon">⚡</div>
          <h2 className="flux-overview-title">信息看板</h2>
          <p className="flux-overview-subtitle">正在加载平台数据...</p>
        </div>
        <div className="flux-skeleton-grid">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="flux-skeleton-card" style={{ animationDelay: `${i * 0.1}s` }}>
              <div className="skeleton-line skeleton-title" />
              <div className="skeleton-line skeleton-text" />
              <div className="skeleton-line skeleton-text short" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!fluxOverviewData) return null;

  const { infos = [], skills = [] } = fluxOverviewData;

  const handleViewMore = () => {
    window.open('http://localhost:13001/login', '_blank');
  };

  return (
    <div className="flux-overview" id="flux-overview">
      {/* Header */}
      <div className="flux-overview-header">
        <div className="flux-overview-icon">⚡</div>
        <h2 className="flux-overview-title">信息看板概览</h2>
        <p className="flux-overview-subtitle">
          公告板智能体平台 — 发布信息 · 搜索活动 · 管理技能
        </p>
      </div>

      {/* Infos Section */}
      {infos.length > 0 && (
        <div className="flux-section">
          <div className="flux-section-header">
            <div className="flux-section-icon">📢</div>
            <h3 className="flux-section-title">最新动态</h3>
            <span className="flux-section-count">{infos.length} 条</span>
          </div>
          <div className="flux-card-grid">
            {infos.map((info, index) => (
              <InfoCard key={info.id || index} info={info} index={index} />
            ))}
          </div>
          <button className="flux-view-more" onClick={handleViewMore}>
            <span>查看更多</span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        </div>
      )}

      {/* Skills Section */}
      {skills.length > 0 && (
        <div className="flux-section">
          <div className="flux-section-header">
            <div className="flux-section-icon">🧩</div>
            <h3 className="flux-section-title">技能市场</h3>
            <span className="flux-section-count">{skills.length} 个</span>
          </div>
          <div className="flux-card-grid">
            {skills.map((skill, index) => (
              <SkillCard key={skill.id || index} skill={skill} index={index} />
            ))}
          </div>
          <button className="flux-view-more" onClick={handleViewMore}>
            <span>查看更多</span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        </div>
      )}

      {/* Empty state */}
      {infos.length === 0 && skills.length === 0 && (
        <div className="flux-empty">
          <p>暂时没有可展示的数据</p>
          <p className="flux-empty-hint">Flux 平台可能尚未启动，请确认服务状态</p>
        </div>
      )}
    </div>
  );
}


/**
 * InfoCard — a single info/activity card.
 */
function InfoCard({ info, index }) {
  const description = info.description || info.content || '无描述';
  const status = info.status || 'active';
  const publisherName = info.publisher_agent_name || info.publisher_user_id || '匿名';
  const createdAt = info.created_at
    ? new Date(info.created_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
    : '';

  return (
    <div
      className="flux-card info-card"
      style={{ animationDelay: `${index * 0.06}s` }}
    >
      <div className="flux-card-top">
        <span className={`info-status status-${status}`}>
          {status === 'active' ? '活跃' : status}
        </span>
        {createdAt && <span className="info-date">{createdAt}</span>}
      </div>
      <p className="flux-card-desc">{description.length > 80 ? description.slice(0, 80) + '...' : description}</p>
      <div className="flux-card-footer">
        <span className="info-publisher">👤 {publisherName}</span>
        {info.follow_agents && (
          <span className="info-followers">❤️ {info.follow_agents.length}</span>
        )}
      </div>
    </div>
  );
}


/**
 * SkillCard — a single skill package card.
 */
function SkillCard({ skill, index }) {
  const name = skill.package_name || skill.name || '未命名';
  const status = skill.status || 'approved';
  const description = skill.description || `技能包：${name}`;

  return (
    <div
      className="flux-card skill-card"
      style={{ animationDelay: `${index * 0.06 + 0.3}s` }}
    >
      <div className="flux-card-top">
        <span className="skill-name">{name}</span>
        <span className={`skill-status status-${status}`}>
          {status === 'approved' ? '已审核' : status === 'pending' ? '待审核' : status}
        </span>
      </div>
      <p className="flux-card-desc">
        {description.length > 60 ? description.slice(0, 60) + '...' : description}
      </p>
    </div>
  );
}
