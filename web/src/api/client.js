/**
 * API Client
 * ===========
 * Centralized HTTP client with auth token management.
 * Supports OIDC login flow and authenticated API calls.
 */

const API_BASE = '';  // Uses Vite proxy in dev

const TOKEN_KEY = 'yanyu_wit_token';

/**
 * Get the stored auth token (from localStorage).
 */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * Set the auth token (persists to localStorage).
 */
export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

/**
 * Clear the auth token (logout).
 */
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * Make an authenticated API request.
 */
async function request(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const error = new Error(errorData.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

// --- Auth (OIDC flow) ---

/**
 * Step 1: Get the Keycloak login URL from the backend.
 * The backend generates state/nonce/PKCE and stores them in the session.
 */
export async function fetchLoginUrl() {
  const resp = await fetch(`${API_BASE}/auth/login-url`, {
    credentials: 'include',  // Include session cookie
  });
  if (!resp.ok) {
    throw new Error('Failed to get login URL');
  }
  return resp.json();
}

/**
 * Step 7: Exchange the auth code for an app token.
 * Sends the code and state to the backend, which verifies with Keycloak
 * and returns our app JWT.
 */
export async function exchangeToken(code, state) {
  const resp = await fetch(`${API_BASE}/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',  // Include session cookie
    body: JSON.stringify({ code, state }),
  });
  if (!resp.ok) {
    const errorData = await resp.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Token exchange failed');
  }
  return resp.json();
}

// --- User ---

export async function fetchMe() {
  return request('/api/me');
}

// --- ATR (Trusted Registration) ---

export async function fetchAtrStatus() {
  return request('/api/atr/status');
}

export async function registerAtr(endpoint) {
  return request('/api/atr/register', {
    method: 'POST',
    body: JSON.stringify({ endpoint }),
  });
}


// --- Sessions ---

export async function createSession(title = null) {
  return request('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export async function listSessions(activeOnly = true, limit = 50) {
  return request(`/api/sessions?active_only=${activeOnly}&limit=${limit}`);
}

export async function getSession(sessionId) {
  return request(`/api/sessions/${sessionId}`);
}

export async function updateSession(sessionId, title) {
  return request(`/api/sessions/${sessionId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

export async function deleteSession(sessionId) {
  return request(`/api/sessions/${sessionId}`, {
    method: 'DELETE',
  });
}

// --- Messages ---

export async function fetchMessages(sessionId) {
  return request(`/api/sessions/${sessionId}/messages`);
}

// --- Attachments ---

/**
 * Upload a file attachment to a session.
 * @param {string} sessionId
 * @param {File} file
 * @returns {Promise<{id, original_name, mime_type, size_bytes, uploaded_at}>}
 */
export async function uploadAttachment(sessionId, file) {
  const formData = new FormData();
  formData.append('file', file);

  const headers = {};
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  // Don't set Content-Type manually — let the browser add the boundary

  const response = await fetch(
    `${API_BASE}/api/sessions/${sessionId}/attachments`,
    { method: 'POST', headers, body: formData }
  );

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Upload failed: ${response.status}`);
  }
  return response.json();
}

/**
 * Delete an attachment from a session.
 */
export async function deleteAttachment(sessionId, attachmentId) {
  return request(`/api/sessions/${sessionId}/attachments/${attachmentId}`, {
    method: 'DELETE',
  });
}

// --- Chat (SSE Streaming) ---

/**
 * Send a message and stream the response via SSE.
 *
 * @param {string} sessionId
 * @param {string} content
 * @param {string[]} attachmentIds - IDs of uploaded attachments to reference
 * @param {function} onEvent - Callback called with each parsed SSE event object
 * @returns {Promise<void>}
 */
export async function streamChat(sessionId, content, attachmentIds, capability, onEvent) {
  const headers = {
    'Content-Type': 'application/json',
  };
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}/api/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      content,
      attachment_ids: attachmentIds || [],
      capability: capability || null,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE lines
    const lines = buffer.split('\n');
    buffer = lines.pop(); // Keep incomplete line in buffer

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          onEvent(data);
        } catch (e) {
          // Skip malformed lines
        }
      }
    }
  }

  // Process any remaining buffer
  if (buffer.startsWith('data: ')) {
    try {
      const data = JSON.parse(buffer.slice(6));
      onEvent(data);
    } catch (e) {
      // Skip
    }
  }
}

// --- Flux Overview ---

/**
 * Fetch Flux platform overview (infos + skills) for the welcome display.
 * @returns {Promise<{infos: Array, skills: Array}>}
 */
export async function fetchFluxOverview() {
  return request('/api/flux/overview');
}

// --- ACPs Overview ---

/**
 * Fetch ACPs platform overview (trending agents) for the welcome display.
 * @returns {Promise<{agents: Array}>}
 */
export async function fetchAcpsOverview() {
  return request('/api/acps/overview');
}

// --- Heartbeat (Scheduled Tasks) ---

export async function fetchHeartbeatJobs(includeDisabled = true) {
  return request(`/api/heartbeat/jobs?include_disabled=${includeDisabled}`);
}

export async function createHeartbeatJob(data) {
  return request('/api/heartbeat/jobs', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateHeartbeatJob(jobId, data) {
  return request(`/api/heartbeat/jobs/${jobId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteHeartbeatJob(jobId) {
  return request(`/api/heartbeat/jobs/${jobId}`, {
    method: 'DELETE',
  });
}

export async function triggerHeartbeatJob(jobId) {
  return request(`/api/heartbeat/jobs/${jobId}/run`, {
    method: 'POST',
  });
}

export async function fetchHeartbeatRuns(jobId, limit = 20) {
  return request(`/api/heartbeat/jobs/${jobId}/runs?limit=${limit}`);
}

export async function fetchHeartbeatStatus() {
  return request('/api/heartbeat/status');
}

export async function revealHeartbeatJobFolder(jobId) {
  return request(`/api/heartbeat/jobs/${jobId}/reveal`, {
    method: 'POST',
  });
}


// --- Provider Management ---

/**
 * Fetch all configured providers (API keys are masked).
 * @returns {Promise<{providers: Array, active_index: number}>}
 */
export async function fetchProviders() {
  return request('/api/providers');
}

/**
 * Create a new provider.
 * @param {{type: string, name: string, base_url?: string, api_key?: string}} data
 * @returns {Promise<{index: number, type: string, name: string}>}
 */
export async function createProvider(data) {
  return request('/api/providers', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update a provider at the given index.
 * @param {number} index
 * @param {{type?: string, name?: string, base_url?: string, api_key?: string}} data
 */
export async function updateProvider(index, data) {
  return request(`/api/providers/${index}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a provider at the given index.
 * @param {number} index
 */
export async function deleteProvider(index) {
  return request(`/api/providers/${index}`, {
    method: 'DELETE',
  });
}

/**
 * Activate a provider at the given index.
 * @param {number} index
 * @returns {Promise<{active_index: number, type: string, name: string}>}
 */
export async function activateProvider(index) {
  return request(`/api/providers/${index}/activate`, {
    method: 'POST',
  });
}

// --- Sandbox Management ---

/**
 * Fetch the diff between sandbox and physical workspace.
 * @param {string} sessionId
 * @returns {Promise<{diffs: Array}>}
 */
export async function fetchSandboxDiff(sessionId) {
  return request(`/api/sandbox/diff?session_id=${sessionId || ''}`);
}

/**
 * Apply sandbox changes back to the physical workspace.
 * @param {string} sessionId
 */
export async function applySandbox(sessionId) {
  return request(`/api/sandbox/apply?session_id=${sessionId || ''}`, {
    method: 'POST',
  });
}

/**
 * Discard sandbox changes.
 * @param {string} sessionId
 */
export async function discardSandbox(sessionId) {
  return request(`/api/sandbox/discard?session_id=${sessionId || ''}`, {
     method: 'POST',
  });
}

/**
 * Fetch sandbox git version history.
 * @param {string} sessionId
 * @returns {Promise<{versions: Array}>}
 */
export async function fetchSandboxVersions(sessionId) {
  return request(`/api/sandbox/versions?session_id=${sessionId || ''}`);
}

/**
 * Revert sandbox to a specific git commit.
 * @param {string} sessionId
 * @param {string} commitHash
 */
export async function revertSandbox(sessionId, commitHash) {
  return request(`/api/sandbox/revert?session_id=${sessionId || ''}&commit_hash=${commitHash}`, {
    method: 'POST',
  });
}
