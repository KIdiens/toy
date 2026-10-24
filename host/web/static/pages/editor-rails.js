/* 窄屏仍可操作文件与属性；抽屉保持非模态，支持关闭按钮与 Escape。 */
export function bindEditorRails(q) {
  const entries = [
    ['#editor-files-panel', '#files-toggle', '#files-close'],
    ['#editor-inspector-panel', '#inspector-toggle', '#inspector-close'],
  ];
  function show(index, open, restore = false) {
    const [panel, toggle] = entries[index].map(q);
    panel?.setAttribute('data-open', String(open));
    toggle?.setAttribute('aria-expanded', String(open));
    if (open) panel?.querySelector('button, select, input')?.focus();
    else if (restore) toggle?.focus();
  }
  entries.forEach(([panel, toggle, close], index) => {
    q(toggle)?.addEventListener('click', () => {
      const open = q(panel)?.getAttribute('data-open') !== 'true';
      show(1 - index, false); show(index, open, !open);
    });
    q(close)?.addEventListener('click', () => show(index, false, true));
  });
  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    entries.forEach(([panel], index) => {
      if (q(panel)?.getAttribute('data-open') === 'true') show(index, false, true);
    });
  });
}
