import { useState, useEffect, useCallback } from 'react';
import useStore from '../store/useStore';
import { html as diff2html } from 'diff2html';
import 'diff2html/bundles/css/diff2html.min.css';
import './SandboxPanel.css';

/**
 * SandboxPanel — Inline panel showing sandbox diff with apply/discard.
 * Rendered inside the sandbox-column of ChatArea's split-view layout.
 */
export default function SandboxPanel() {
  const {
    sandboxDiffs,
    sandboxLoading,
    toggleSandboxPanel,
    fetchSandboxDiff,
    applySandboxChanges,
    discardSandboxChanges,
    activeSessionId,
  } = useStore();

  const [expandedFiles, setExpandedFiles] = useState({});
  const [actionLoading, setActionLoading] = useState(false);

  // Fetch data on mount
  useEffect(() => {
    fetchSandboxDiff();
  }, [activeSessionId]);

  const toggleFile = useCallback((path) => {
    setExpandedFiles((prev) => ({ ...prev, [path]: !prev[path] }));
  }, []);

  const handleApply = async () => {
    if (!confirm('确定要将沙箱的所有变更应用到工作区吗？')) return;
    setActionLoading(true);
    try {
      await applySandboxChanges();
    } catch (e) {
      alert(`应用失败: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDiscard = async () => {
    if (!confirm('确定要丢弃沙箱的所有变更吗？此操作不可恢复。')) return;
    setActionLoading(true);
    try {
      await discardSandboxChanges();
    } catch (e) {
      alert(`丢弃失败: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="sandbox-panel" id="sandbox-panel">
      {/* Header */}
      <div className="sandbox-header">
        <div className="sandbox-header-left">
          <div className="sandbox-header-icon">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="2" width="12" height="12" rx="2" />
              <path d="M2 6h12M6 6v8" />
            </svg>
          </div>
          <h3>沙箱变更预览</h3>
        </div>
        <button className="sandbox-close-btn" onClick={toggleSandboxPanel} aria-label="关闭">
          <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="4" y1="4" x2="14" y2="14" />
            <line x1="14" y1="4" x2="4" y2="14" />
          </svg>
        </button>
      </div>

      {/* Content */}
      <div className="sandbox-content">
        {sandboxLoading ? (
          <div className="sandbox-loading">
            <div className="sandbox-loading-spinner" />
          </div>
        ) : sandboxDiffs.length === 0 ? (
          <div className="sandbox-empty">
            <div className="sandbox-empty-icon">✨</div>
            <p>沙箱与工作区没有差异<br />Agent 操作完成后变更会自动显示</p>
          </div>
        ) : (
          <div>
            {sandboxDiffs.map((diff) => (
              <DiffFile
                key={diff.path}
                diff={diff}
                expanded={!!expandedFiles[diff.path]}
                onToggle={() => toggleFile(diff.path)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      {sandboxDiffs.length > 0 && (
        <div className="sandbox-actions">
          <button
            className="sandbox-btn sandbox-btn-discard"
            onClick={handleDiscard}
            disabled={actionLoading}
          >
            🗑️ 丢弃变更
          </button>
          <button
            className="sandbox-btn sandbox-btn-apply"
            onClick={handleApply}
            disabled={actionLoading}
          >
            ✅ 应用到工作区
          </button>
        </div>
      )}
    </div>
  );
}


// --- Single Diff File ---
function DiffFile({ diff, expanded, onToggle }) {
  const badgeLabel = {
    added: '新增',
    modified: '修改',
    deleted: '删除',
  };

  // Generate diff HTML using diff2html
  const diffHtml = expanded && diff.is_text && diff.diff
    ? diff2html(diff.diff, {
        drawFileList: false,
        outputFormat: 'line-by-line',
        matching: 'lines',
        diffStyle: 'word',
      })
    : '';

  return (
    <div className="sandbox-diff-file">
      <div className="sandbox-diff-file-header" onClick={onToggle}>
        <span className={`sandbox-diff-badge ${diff.type}`}>
          {badgeLabel[diff.type] || diff.type}
        </span>
        <span className="sandbox-diff-path" title={diff.path}>{diff.path}</span>
        <span className={`sandbox-diff-toggle ${expanded ? 'open' : ''}`}>▶</span>
      </div>
      {expanded && (
        <div className="sandbox-diff-body">
          {diff.is_text && diffHtml ? (
            <div dangerouslySetInnerHTML={{ __html: diffHtml }} />
          ) : (
            <div style={{ padding: '12px 16px', color: 'var(--color-text-tertiary)', fontSize: '13px' }}>
              {diff.diff || '(二进制文件)'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
