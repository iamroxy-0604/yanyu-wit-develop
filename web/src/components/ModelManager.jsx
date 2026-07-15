import { useState, useEffect, useRef } from 'react';
import useStore from '../store/useStore';
import './ModelManager.css';

const PROVIDER_ICONS = {
  openai: '🟢',
  anthropic: '🟠',
  google: '🔵',
  ollama: '⚪',
};

const TYPE_DEFAULTS = {
  openai: { model: 'gpt-4o', base_url: 'https://api.openai.com/v1' },
  anthropic: { model: 'claude-sonnet-4-20250514', base_url: '' },
  google: { model: 'gemini-2.5-flash', base_url: '' },
  ollama: { model: 'qwen3:32b', base_url: 'http://localhost:11434/v1' },
};

const VALID_TYPES = Object.keys(TYPE_DEFAULTS);

export default function ModelManager({ onClose }) {
  const {
    providers,
    activeProviderIndex,
    loadProviders,
    addProvider,
    updateProvider,
    removeProvider,
    activateProvider,
  } = useStore();

  const [showForm, setShowForm] = useState(false);
  const [editIndex, setEditIndex] = useState(null);
  const [formData, setFormData] = useState({
    type: 'openai',
    name: '',
    base_url: '',
    api_key: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const overlayRef = useRef(null);

  useEffect(() => {
    loadProviders();
  }, []);

  // Close on overlay click
  const handleOverlayClick = (e) => {
    if (e.target === overlayRef.current) {
      onClose();
    }
  };

  // Close on Escape
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const resetForm = () => {
    setFormData({ type: 'openai', name: '', base_url: '', api_key: '' });
    setShowForm(false);
    setEditIndex(null);
  };

  const handleTypeChange = (type) => {
    const defaults = TYPE_DEFAULTS[type] || {};
    setFormData((prev) => ({
      ...prev,
      type,
      name: defaults.model || '',
      base_url: defaults.base_url || '',
    }));
  };

  const handleShowAdd = () => {
    const defaults = TYPE_DEFAULTS['openai'];
    setFormData({
      type: 'openai',
      name: defaults.model,
      base_url: defaults.base_url,
      api_key: '',
    });
    setEditIndex(null);
    setShowForm(true);
  };

  const handleShowEdit = (index) => {
    const p = providers[index];
    setFormData({
      type: p.type || 'openai',
      name: p.name || '',
      base_url: p.base_url || '',
      api_key: '', // Don't prefill masked key
    });
    setEditIndex(index);
    setShowForm(true);
  };

  const handleSubmit = async () => {
    if (!formData.type || !formData.name) return;
    setSubmitting(true);
    try {
      if (editIndex !== null) {
        // Update - only send non-empty fields
        const patch = { type: formData.type, name: formData.name, base_url: formData.base_url };
        if (formData.api_key) patch.api_key = formData.api_key;
        await updateProvider(editIndex, patch);
      } else {
        await addProvider(formData);
      }
      resetForm();
    } catch (err) {
      alert('操作失败: ' + err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (index) => {
    const p = providers[index];
    if (!confirm(`确认删除 ${p.type} / ${p.name}？`)) return;
    try {
      await removeProvider(index);
    } catch (err) {
      alert('删除失败: ' + err.message);
    }
  };

  const handleActivate = async (index) => {
    if (index === activeProviderIndex) return;
    try {
      await activateProvider(index);
    } catch (err) {
      alert('切换失败: ' + err.message);
    }
  };

  return (
    <div className="model-manager-overlay" ref={overlayRef} onClick={handleOverlayClick}>
      <div className="model-manager-modal">
        {/* Header */}
        <div className="mm-header">
          <h2>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
            模型管理
          </h2>
          <button className="mm-close-btn" onClick={onClose} aria-label="关闭">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="mm-body">
          {/* Provider List */}
          {providers.length === 0 && !showForm ? (
            <div className="mm-empty">
              <div className="mm-empty-icon">🤖</div>
              <p>尚未配置任何 LLM Provider</p>
              <p style={{ fontSize: 12, marginTop: 8, opacity: 0.6 }}>点击下方按钮添加第一个 Provider</p>
            </div>
          ) : (
            <div className="mm-provider-list">
              {providers.map((p, i) => (
                <div
                  key={i}
                  className={`mm-provider-card ${i === activeProviderIndex ? 'active' : ''}`}
                  onClick={() => handleActivate(i)}
                  title={`点击激活 ${p.type} / ${p.name}`}
                >
                  <div className="mm-provider-icon" data-type={p.type}>
                    {PROVIDER_ICONS[p.type] || '🔧'}
                  </div>
                  <div className="mm-provider-info">
                    <div className="mm-provider-name">
                      {p.name}
                      {i === activeProviderIndex && (
                        <span className="mm-active-badge">当前</span>
                      )}
                    </div>
                    <div className="mm-provider-meta">
                      {p.type} · {p.base_url || '默认地址'} · Key: {p.api_key || '未设置'}
                    </div>
                  </div>
                  <div className="mm-provider-actions">
                    <button
                      className="mm-action-btn"
                      onClick={(e) => { e.stopPropagation(); handleShowEdit(i); }}
                      title="编辑"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                      </svg>
                    </button>
                    <button
                      className="mm-action-btn danger"
                      onClick={(e) => { e.stopPropagation(); handleDelete(i); }}
                      title="删除"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Add Button */}
          {!showForm && (
            <button className="mm-add-btn" onClick={handleShowAdd}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              添加 Provider
            </button>
          )}

          {/* Add/Edit Form */}
          {showForm && (
            <div className="mm-form">
              <div className="mm-form-title">
                {editIndex !== null ? '编辑 Provider' : '添加新 Provider'}
              </div>

              <div className="mm-form-row">
                <label className="mm-form-label">Provider 类型</label>
                <select
                  className="mm-form-select"
                  value={formData.type}
                  onChange={(e) => handleTypeChange(e.target.value)}
                >
                  {VALID_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {PROVIDER_ICONS[t]} {t}
                    </option>
                  ))}
                </select>
              </div>

              <div className="mm-form-row">
                <label className="mm-form-label">Model Name</label>
                <input
                  className="mm-form-input"
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData((d) => ({ ...d, name: e.target.value }))}
                  placeholder="例如 gpt-4o"
                />
              </div>

              <div className="mm-form-row">
                <label className="mm-form-label">API Base URL</label>
                <input
                  className="mm-form-input"
                  type="text"
                  value={formData.base_url}
                  onChange={(e) => setFormData((d) => ({ ...d, base_url: e.target.value }))}
                  placeholder="留空使用默认地址"
                />
              </div>

              <div className="mm-form-row">
                <label className="mm-form-label">
                  API Key{editIndex !== null ? '（留空则不修改）' : ''}
                </label>
                <input
                  className="mm-form-input"
                  type="password"
                  value={formData.api_key}
                  onChange={(e) => setFormData((d) => ({ ...d, api_key: e.target.value }))}
                  placeholder={editIndex !== null ? '不修改请留空' : '输入 API Key'}
                />
              </div>

              <div className="mm-form-actions">
                <button className="mm-btn mm-btn-cancel" onClick={resetForm}>
                  取消
                </button>
                <button
                  className="mm-btn mm-btn-primary"
                  onClick={handleSubmit}
                  disabled={submitting || !formData.name}
                >
                  {submitting ? '保存中...' : editIndex !== null ? '保存修改' : '添加'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
