import { useEffect, useState } from 'react';
import useStore from './store/useStore';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import UserMenu from './components/UserMenu';
import Callback from './components/Callback';
import './App.css';

function App() {
  const { sidebarOpen, toggleSidebar, isLoggedIn, restoreSession, loginSuccess } = useStore();
  const [initializing, setInitializing] = useState(true);

  // Check if this is the OIDC callback route
  const isCallback = window.location.pathname === '/callback';

  // Restore session from localStorage on mount
  useEffect(() => {
    if (!isCallback) {
      restoreSession().finally(() => setInitializing(false));
    } else {
      setInitializing(false);
    }
  }, []);

  // Show callback page for /callback route
  if (isCallback) {
    return <Callback onLoginSuccess={(user) => loginSuccess(user)} />;
  }

  // Show loading while checking stored token
  if (initializing) {
    return (
      <div className="app-loading">
        <div className="app-loading-spinner" />
      </div>
    );
  }

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className={`app-sidebar ${sidebarOpen ? 'open' : 'collapsed'}`}>
        <Sidebar />
      </aside>

      {/* Main Area */}
      <main className="app-main">
        {/* Header */}
        <header className="app-header">
          <button
            className="header-menu-btn"
            onClick={toggleSidebar}
            aria-label="Toggle sidebar"
            id="sidebar-toggle"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <h1 className="header-title">YanYu-Wit</h1>
          <div className="header-spacer" />
          <UserMenu />
        </header>

        {/* Chat Area */}
        <ChatArea />
      </main>

      {/* Sidebar overlay for mobile */}
      {sidebarOpen && (
        <div className="sidebar-overlay" onClick={toggleSidebar} />
      )}
    </div>
  );
}

export default App;
