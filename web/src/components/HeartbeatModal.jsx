import { useState, useEffect, useRef } from 'react';
import {
  fetchHeartbeatJobs,
  createHeartbeatJob,
  deleteHeartbeatJob,
  updateHeartbeatJob,
  fetchHeartbeatRuns,
  revealHeartbeatJobFolder,
} from '../api/client';
import './HeartbeatModal.css';


// ═══════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════

function formatTimeHMS(ms) {
  if (!ms) return '-';
  try {
    const d = new Date(ms);
    return d.toLocaleString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return '-';
  }
}

function formatDate(ms) {
  if (!ms) return '-';
  try {
    const d = new Date(ms);
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '-';
  }
}

function scheduleLabel(schedule) {
  if (!schedule) return '-';
  const f = schedule.frequency;
  const t = schedule.time || '09:00';
  const dayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

  if (f === 'daily') return `每天 ${t}`;
  if (f === 'weekly') {
    if (schedule.weekdays?.length > 0) {
      const days = schedule.weekdays
        .filter(d => d >= 0 && d <= 6)
        .sort()
        .map(d => dayNames[d])
        .join('、');
      return `每周 ${days} ${t}`;
    }
    return `每周 ${t}`;
  }
  if (f === 'monthly') {
    if (schedule.monthdays?.length > 0) {
      const days = schedule.monthdays.sort((a, b) => a - b).join('、');
      return `每月 ${days}日 ${t}`;
    }
    return `每月 ${t}`;
  }
  if (f === 'once') {
    if (schedule.once_at) {
      try {
        const dt = new Date(schedule.once_at);
        return `一次性: ${dt.toLocaleString('zh-CN')}`;
      } catch { /* */ }
    }
    return '一次性';
  }
  return '-';
}

function statusIcon(job) {
  if (!job.enabled) return { icon: '⏸️', text: '已暂停', cls: 'paused' };
  if (job.state?.running) return { icon: '🔄', text: '执行中', cls: 'running' };
  if (job.state?.last_status === 'ok' && !job.state?.next_run_at_ms)
    return { icon: '✅', text: '已完成', cls: 'done' };
  if (job.state?.last_status === 'error')
    return { icon: '❌', text: '出错', cls: 'error' };
  return { icon: '⏳', text: '等待中', cls: 'waiting' };
}

const FREQUENCIES = [
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' },
];

const TASK_TYPES = [
  { value: 'agent', label: '🤖 智能体任务' },
  { value: 'script', label: '📜 脚本任务' },
];

const WEEKDAY_OPTIONS = [
  { value: 0, label: '周一' },
  { value: 1, label: '周二' },
  { value: 2, label: '周三' },
  { value: 3, label: '周四' },
  { value: 4, label: '周五' },
  { value: 5, label: '周六' },
  { value: 6, label: '周日' },
];

const MONTHDAY_OPTIONS = Array.from({ length: 31 }, (_, i) => ({
  value: i + 1,
  label: `${i + 1}日`,
}));

// 生成时间选择列表（每15分钟一个间隔）
function generateTimeOptions() {
  const options = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 15) {
      options.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  return options;
}
const TIME_OPTIONS = generateTimeOptions();


// ═══════════════════════════════════════════════════════════════════════════
// Multi-select dropdown component (for weekdays / monthdays)
// ═══════════════════════════════════════════════════════════════════════════

function MultiSelectDropdown({ options, selected, onChange, placeholder }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggle = (val) => {
    if (selected.includes(val)) {
      onChange(selected.filter(v => v !== val));
    } else {
      onChange([...selected, val]);
    }
  };

  const displayText = selected.length > 0
    ? options.filter(o => selected.includes(o.value)).map(o => o.label).join('、')
    : placeholder;

  return (
    <div className="hb-multi-dropdown" ref={ref}>
      <button
        type="button"
        className="hb-select hb-day-select"
        onClick={() => setOpen(!open)}
        style={{ textAlign: 'left', position: 'relative' }}
      >
        <span style={{ opacity: selected.length > 0 ? 1 : 0.5 }}>{displayText}</span>
        <span style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 10, color: '#9aa0a6' }}>
          {open ? '▲' : '▼'}
        </span>
      </button>
      {open && (
        <div className="hb-multi-dropdown-menu">
          {options.map(opt => (
            <label key={opt.value} className="hb-multi-dropdown-item">
              <input
                type="checkbox"
                checked={selected.includes(opt.value)}
                onChange={() => toggle(opt.value)}
              />
              <span>{opt.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// HeartbeatModal Component
// ═══════════════════════════════════════════════════════════════════════════

export default function HeartbeatModal({ onClose }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [expandedJob, setExpandedJob] = useState(null);
  const [runLogs, setRunLogs] = useState({});

  // 防止拖拽关闭：追踪 mousedown 的起始目标
  const mouseDownTarget = useRef(null);

  // 创建表单状态
  const [form, setForm] = useState({
    name: '',
    description: '',
    type: 'agent',
    frequency: 'daily',
    time: '09:00',
    weekdays: [],
    monthdays: [],
    instruction: '',
    script_path: '',
  });
  const [submitting, setSubmitting] = useState(false);

  const loadJobs = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchHeartbeatJobs();
      setJobs(data.jobs || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadJobs();
  }, []);

  // --- Backdrop click: only close when mousedown AND mouseup both on backdrop ---
  const handleBackdropMouseDown = (e) => {
    mouseDownTarget.current = e.target;
  };

  const handleBackdropClick = (e) => {
    // Only close if both mousedown and mouseup (click) happened on the backdrop itself
    if (e.target === e.currentTarget && mouseDownTarget.current === e.currentTarget) {
      onClose();
    }
    mouseDownTarget.current = null;
  };

  // --- Form Handlers ---

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    if (form.type === 'agent' && !form.instruction.trim()) return;
    if (form.type === 'script' && !form.script_path.trim()) return;

    setSubmitting(true);
    try {
      await createHeartbeatJob(form);
      setForm({
        name: '', description: '', type: 'agent', frequency: 'daily',
        time: '09:00', weekdays: [], monthdays: [], instruction: '', script_path: '',
      });
      setShowForm(false);
      await loadJobs();
    } catch (e) {
      alert('创建失败: ' + e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (jobId) => {
    if (!confirm('确定要删除这个任务吗？')) return;
    try {
      await deleteHeartbeatJob(jobId);
      await loadJobs();
    } catch (e) {
      alert('删除失败: ' + e.message);
    }
  };

  const handleToggle = async (job) => {
    try {
      await updateHeartbeatJob(job.id, { enabled: !job.enabled });
      await loadJobs();
    } catch (e) {
      alert('更新失败: ' + e.message);
    }
  };

  const handleExpand = async (jobId) => {
    if (expandedJob === jobId) {
      setExpandedJob(null);
      return;
    }
    setExpandedJob(jobId);
    if (!runLogs[jobId]) {
      try {
        const data = await fetchHeartbeatRuns(jobId);
        setRunLogs(prev => ({ ...prev, [jobId]: data.entries || [] }));
      } catch {
        setRunLogs(prev => ({ ...prev, [jobId]: [] }));
      }
    }
  };

  const handleRevealFolder = async (jobId) => {
    try {
      await revealHeartbeatJobFolder(jobId);
    } catch (e) {
      alert('无法打开本地文件夹: ' + e.message);
    }
  };


  // ═══════════════════════════════════════════════════════════════════════
  // Render
  // ═══════════════════════════════════════════════════════════════════════

  return (
    <div
      className="hb-backdrop"
      onMouseDown={handleBackdropMouseDown}
      onClick={handleBackdropClick}
    >
      <div className="hb-modal animate-fade-in" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="hb-header">
          <h2 className="hb-title">
            <span>⏰</span> 定时任务管理
          </h2>
          <div className="hb-header-actions">
            <button
              className="hb-add-btn"
              onClick={() => setShowForm(!showForm)}
            >
              {showForm ? '取消' : '+ 新建任务'}
            </button>
            <button className="hb-close" onClick={onClose}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        {/* Create Form */}
        {showForm && (
          <form className="hb-form" onSubmit={handleCreate}>
            {/* 任务名称 */}
            <div className="hb-form-row">
              <label className="hb-form-label">任务名称</label>
              <input
                className="hb-input"
                placeholder="示例：新闻摘要"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
              />
            </div>

            {/* 任务类型 */}
            <div className="hb-form-row">
              <label className="hb-form-label">任务类型</label>
              <div className="hb-type-selector">
                {TASK_TYPES.map((t) => (
                  <button
                    key={t.value}
                    type="button"
                    className={`hb-type-btn ${form.type === t.value ? 'active' : ''}`}
                    onClick={() => setForm({ ...form, type: t.value })}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Agent 指令 / Script 路径 */}
            <div className="hb-form-row">
              <label className="hb-form-label">
                {form.type === 'agent' ? '执行指令' : '脚本路径'}
              </label>
              {form.type === 'agent' ? (
                <textarea
                  className="hb-textarea"
                  placeholder="示例：给我发送新闻摘要"
                  value={form.instruction}
                  onChange={(e) => setForm({ ...form, instruction: e.target.value })}
                  rows={2}
                  required
                />
              ) : (
                <input
                  className="hb-input"
                  placeholder="示例：scripts/cert_renewal.sh"
                  value={form.script_path}
                  onChange={(e) => setForm({ ...form, script_path: e.target.value })}
                  required
                />
              )}
            </div>

            {/* 调度配置 */}
            <div className="hb-form-row">
              <label className="hb-form-label">调度安排</label>
              <div className="hb-schedule-config">
                <div className="hb-schedule-row">
                  <select
                    className="hb-select"
                    value={form.frequency}
                    onChange={(e) => setForm({
                      ...form,
                      frequency: e.target.value,
                      weekdays: [],
                      monthdays: [],
                    })}
                  >
                    {FREQUENCIES.map((f) => (
                      <option key={f.value} value={f.value}>{f.label}</option>
                    ))}
                  </select>

                  {/* 每周：星期选择下拉框（动态出现在频率和时间之间） */}
                  {form.frequency === 'weekly' && (
                    <MultiSelectDropdown
                      options={WEEKDAY_OPTIONS}
                      selected={form.weekdays}
                      onChange={(val) => setForm({ ...form, weekdays: val })}
                      placeholder="选择星期"
                    />
                  )}

                  {/* 每月：日期选择下拉框 */}
                  {form.frequency === 'monthly' && (
                    <MultiSelectDropdown
                      options={MONTHDAY_OPTIONS}
                      selected={form.monthdays}
                      onChange={(val) => setForm({ ...form, monthdays: val })}
                      placeholder="选择日期"
                    />
                  )}

                  <select
                    className="hb-select hb-time-select"
                    value={form.time}
                    onChange={(e) => setForm({ ...form, time: e.target.value })}
                  >
                    {TIME_OPTIONS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* 描述 */}
            <div className="hb-form-row">
              <label className="hb-form-label">描述（可选）</label>
              <input
                className="hb-input"
                placeholder="任务描述"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>

            <div className="hb-form-hint">
              ⏱ 你的任务会在所选时间后的 1 小时内执行
            </div>

            <button className="hb-submit-btn" type="submit" disabled={submitting}>
              {submitting ? '创建中...' : '创建任务'}
            </button>
          </form>
        )}

        {/* Job List */}
        <div className="hb-body">
          {loading ? (
            <div className="hb-loading">
              <span className="hb-spinner" /> 加载中...
            </div>
          ) : error ? (
            <div className="hb-error">
              <span>⚠️ {error}</span>
              <button className="hb-retry-btn" onClick={loadJobs}>重试</button>
            </div>
          ) : jobs.length === 0 ? (
            <div className="hb-empty">
              <span className="hb-empty-icon">📋</span>
              <span>暂无定时任务</span>
              <span className="hb-empty-hint">点击"新建任务"添加，或在对话中告诉 Wit</span>
            </div>
          ) : (
            jobs.map((job) => {
              const st = statusIcon(job);
              const isExpanded = expandedJob === job.id;
              const typeLabel = job.type === 'agent' ? '🤖 智能体' : '📜 脚本';
              return (
                <div key={job.id} className={`hb-card ${st.cls}`}>
                  {/* Card Main Row */}
                  <div className="hb-card-main" onClick={() => handleExpand(job.id)}>
                    <div className="hb-card-status">
                      <span className="hb-card-icon">{st.icon}</span>
                    </div>
                    <div className="hb-card-info">
                      <div className="hb-card-name-row">
                        <span className="hb-card-name">{job.name}</span>
                        <span className="hb-card-type-badge">{typeLabel}</span>
                      </div>
                      <div className="hb-card-meta">
                        <span className="hb-card-schedule">{scheduleLabel(job.schedule)}</span>
                        {job.state?.next_run_at_ms && (
                          <span className="hb-card-next">
                            下次: {formatDate(job.state.next_run_at_ms)}
                          </span>
                        )}
                      </div>
                      {job.description && (
                        <div className="hb-card-desc">{job.description}</div>
                      )}
                    </div>
                    <div className="hb-card-actions">
                      <button
                        className={`hb-toggle-btn ${job.enabled ? 'on' : 'off'}`}
                        onClick={(e) => { e.stopPropagation(); handleToggle(job); }}
                        title={job.enabled ? '暂停' : '启用'}
                      >
                        {job.enabled ? '⏸' : '▶️'}
                      </button>
                      <button
                        className="hb-delete-btn"
                        onClick={(e) => { e.stopPropagation(); handleDelete(job.id); }}
                        title="删除"
                      >
                        🗑️
                      </button>
                    </div>
                  </div>

                  {/* Expanded Detail */}
                  {isExpanded && (
                    <div className="hb-card-expanded">
                      {/* 任务核心信息 */}
                      <div className="hb-detail-info">
                        <div className="hb-detail-info-row">
                          <span className="hb-detail-label">任务 ID</span>
                          <span className="hb-detail-value">{job.id}</span>
                        </div>
                        <div className="hb-detail-info-row">
                          <span className="hb-detail-label">类型</span>
                          <span className="hb-detail-value">{typeLabel}</span>
                        </div>
                        <div className="hb-detail-info-row">
                          <span className="hb-detail-label">
                            {job.type === 'agent' ? '指令' : '脚本路径'}
                          </span>
                          <span className="hb-detail-value hb-detail-instruction">
                            {job.type === 'agent' ? job.instruction : job.script_path}
                          </span>
                        </div>
                        <div className="hb-detail-info-row">
                          <span className="hb-detail-label">调度规则</span>
                          <span className="hb-detail-value">
                            {scheduleLabel(job.schedule)}
                          </span>
                        </div>
                        {job.state?.last_result && (
                          <div className="hb-detail-info-row">
                            <span className="hb-detail-label">最近结果</span>
                            <span className="hb-detail-value hb-detail-result">
                              {job.state.last_result.length > 200
                                ? job.state.last_result.slice(0, 200) + '...'
                                : job.state.last_result}
                            </span>
                          </div>
                        )}
                      </div>

                      {/* 打开文件夹按钮 */}
                      <div className="hb-reveal-action-row">
                        <button
                          className="hb-reveal-folder-btn"
                          onClick={() => handleRevealFolder(job.id)}
                        >
                          📂 查看文件夹
                        </button>
                      </div>

                      {/* 执行历史 */}
                      <div className="hb-runs-section">
                        <div className="hb-runs-title">执行历史</div>
                        {(runLogs[job.id] || []).length === 0 ? (
                          <div className="hb-runs-empty">暂无执行记录</div>
                        ) : (
                          <div className="hb-runs-table">
                            <div className="hb-runs-table-header">
                              <span className="hb-runs-col-status">状态</span>
                              <span className="hb-runs-col-time">时间</span>
                              <span className="hb-runs-col-duration">耗时</span>
                              <span className="hb-runs-col-result">执行结果</span>
                            </div>
                            {(runLogs[job.id] || []).slice(0, 10).map((entry, i) => (
                              <div key={i} className={`hb-runs-table-row ${entry.status}`}>
                                <span className="hb-runs-col-status">
                                  {entry.status === 'ok' ? '✅' : '❌'}
                                </span>
                                <span className="hb-runs-col-time">
                                  {formatTimeHMS(entry.ts)}
                                </span>
                                <span className="hb-runs-col-duration">
                                  {(entry.duration_ms / 1000).toFixed(1)}s
                                </span>
                                <span className="hb-runs-col-result" title={entry.result || entry.error || ''}>
                                  {entry.result
                                    ? (entry.result.length > 60
                                        ? entry.result.slice(0, 60) + '...'
                                        : entry.result)
                                    : (entry.error
                                        ? (entry.error.length > 60
                                            ? entry.error.slice(0, 60) + '...'
                                            : entry.error)
                                        : '-')}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
