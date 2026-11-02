// 两个工作区各自保留文档、图表与任务，只在首次访问时加载另一页。
(() => {
  const tab = path => path === '/research' || path === '/research/' ? 'research'
    : path === '/' || path === '/index.html' ? 'signals' : null;
  const originalTab = tab(location.pathname);
  if (!originalTab) return;
  const workspace = document.querySelector('.workspace');
  let frame = null, activeTab = originalTab;
  const urls = {[originalTab]: location.pathname + location.search + location.hash};
  const originalTitle = document.title;

  function show(target, push = true) {
    const nextTab = tab(new URL(target, location.origin).pathname);
    if (!nextTab || nextTab === activeTab) return;
    if (!urls[nextTab]) urls[nextTab] = target;
    if (nextTab === originalTab) {
      frame.hidden = true;
      workspace.inert = false;
      workspace.removeAttribute('aria-hidden');
      document.documentElement.style.overflow = '';
      document.title = originalTitle;
    } else {
      if (!frame) {
        frame = document.createElement('iframe');
        frame.id = 'workspace-tab-frame';
        frame.title = nextTab === 'research' ? '行情研究' : '盘中信号';
        frame.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;border:0;background:#f6f8fa;z-index:1000';
        frame.src = urls[nextTab];
        document.body.appendChild(frame);
      }
      frame.hidden = false;
      workspace.inert = true;
      workspace.setAttribute('aria-hidden', 'true');
      document.documentElement.style.overflow = 'hidden';
      document.title = nextTab === 'research' ? '观市 · 股票研究工作台' : '观市 · 盘中信号';
    }
    activeTab = nextTab;
    if (push) history.pushState({marketTab: nextTab}, '', urls[nextTab]);
  }

  // 子工作区将切换交给父页面，避免嵌套创建和销毁页面。
  const embedded = window.parent !== window && window.parent.location.origin === location.origin
    && typeof window.parent.marketWorkspaceNavigate === 'function';
  if (!embedded) {
    window.marketWorkspaceNavigate = show;
    history.replaceState({marketTab: originalTab}, '', location.href);
    window.addEventListener('popstate', () => show(location.pathname + location.search + location.hash, false));
  }
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target || link.hasAttribute('download')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !tab(url.pathname)) return;
    event.preventDefault();
    const target = url.pathname + url.search + url.hash;
    if (embedded) window.parent.marketWorkspaceNavigate(target);
    else show(target);
  });
})();
