(() => {
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  try {
    const saved = localStorage.getItem('logged-theme');
    if (['light', 'dark', 'system'].includes(saved)) preference = saved;
  } catch { /* Keep system mode when storage is unavailable. */ }

  function applyTheme() {
    const theme = preference === 'system'
      ? (system.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById('theme');
    if (button) {
      button.title = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
      button.setAttribute('aria-label', button.title);
    }
  }
  applyTheme();
  system.addEventListener('change', applyTheme);
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme();
    document.getElementById('theme').addEventListener('click', () => {
      preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('logged-theme', preference); } catch { /* Apply for this visit. */ }
      applyTheme();
    });
  });
})();
