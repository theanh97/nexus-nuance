(function () {
  const STORAGE_KEY = 'autodev-theme';
  const THEMES = ['light', 'dark'];

  function getPreferredTheme() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && THEMES.includes(saved)) return saved;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    const resolved = THEMES.includes(theme) ? theme : 'light';
    document.documentElement.dataset.theme = resolved;
    if (document.body) {
      document.body.dataset.theme = resolved;
    }
    localStorage.setItem(STORAGE_KEY, resolved);
    document.dispatchEvent(new CustomEvent('autodev-theme-changed', { detail: { theme: resolved } }));
    return resolved;
  }

  function toggleTheme() {
    const current = document.documentElement.dataset.theme || getPreferredTheme();
    return applyTheme(current === 'dark' ? 'light' : 'dark');
  }

  function initToggle(buttonId) {
    const btn = document.getElementById(buttonId || 'theme-toggle');
    if (!btn) return;
    const syncLabel = () => {
      const active = document.documentElement.dataset.theme || getPreferredTheme();
      btn.setAttribute('aria-label', active === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
      btn.textContent = active === 'dark' ? 'Light' : 'Dark';
    };
    btn.addEventListener('click', function () {
      toggleTheme();
      syncLabel();
    });
    document.addEventListener('autodev-theme-changed', syncLabel);
    syncLabel();
  }

  window.AutoDevTheme = {
    applyTheme,
    toggleTheme,
    initToggle,
    getPreferredTheme,
  };

  applyTheme(getPreferredTheme());
  document.addEventListener('DOMContentLoaded', function () {
    applyTheme(getPreferredTheme());
    initToggle('theme-toggle');
  });
})();
