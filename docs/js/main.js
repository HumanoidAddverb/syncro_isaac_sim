/* ── main.js ── Sidebar toggle, ToC generation, copy-code, Mermaid ── */

document.addEventListener('DOMContentLoaded', () => {
  initSidebar();
  initTableOfContents();
  initCopyButtons();
  initMermaid();
});

/* ── Sidebar Toggle (mobile) ───────────────────────────────── */
function initSidebar() {
  const btn = document.querySelector('.navbar__hamburger');
  const sidebar = document.querySelector('.sidebar');
  if (!btn || !sidebar) return;

  btn.addEventListener('click', () => {
    sidebar.classList.toggle('open');
  });

  // Close sidebar when clicking a link (mobile)
  sidebar.querySelectorAll('.sidebar__link').forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 768) sidebar.classList.remove('open');
    });
  });

  // Close sidebar when clicking outside
  document.addEventListener('click', (e) => {
    if (window.innerWidth <= 768 && !sidebar.contains(e.target) && !btn.contains(e.target)) {
      sidebar.classList.remove('open');
    }
  });
}

/* ── Table of Contents (auto-generate from h2/h3) ──────────── */
function initTableOfContents() {
  const toc = document.querySelector('.toc');
  const content = document.querySelector('.main__inner');
  if (!toc || !content) return;

  const headings = content.querySelectorAll('h2, h3');
  if (headings.length === 0) { toc.style.display = 'none'; return; }

  const tocTitle = document.createElement('div');
  tocTitle.className = 'toc__title';
  tocTitle.textContent = 'On this page';
  toc.appendChild(tocTitle);

  headings.forEach((h, i) => {
    if (!h.id) h.id = 'section-' + i;
    const link = document.createElement('a');
    link.className = 'toc__link' + (h.tagName === 'H3' ? ' toc__link--h3' : '');
    link.href = '#' + h.id;
    link.textContent = h.textContent;
    toc.appendChild(link);
  });

  // Scroll spy
  const tocLinks = toc.querySelectorAll('.toc__link');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        tocLinks.forEach(l => l.classList.remove('active'));
        const active = toc.querySelector(`a[href="#${entry.target.id}"]`);
        if (active) active.classList.add('active');
      }
    });
  }, { rootMargin: '-80px 0px -70% 0px' });

  headings.forEach(h => observer.observe(h));
}

/* ── Copy Code Buttons ─────────────────────────────────────── */
function initCopyButtons() {
  document.querySelectorAll('pre').forEach(pre => {
    const wrapper = document.createElement('div');
    wrapper.className = 'code-block';
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    wrapper.appendChild(btn);

    btn.addEventListener('click', () => {
      const code = pre.querySelector('code') || pre;
      navigator.clipboard.writeText(code.textContent).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
      });
    });
  });
}

/* ── Mermaid Init (light theme) ────────────────────────────── */
function initMermaid() {
  if (typeof mermaid !== 'undefined') {
    mermaid.initialize({
      startOnLoad: true,
      theme: 'base',
      themeVariables: {
        primaryColor: '#fce8e9',
        primaryTextColor: '#1a1a28',
        primaryBorderColor: '#E3000F',
        lineColor: '#8a8a9e',
        secondaryColor: '#f0fae0',
        tertiaryColor: '#f0f0f5',
        background: '#ffffff',
        mainBkg: '#ffffff',
        nodeBorder: '#d8d8e2',
        clusterBkg: '#f7f7fa',
        clusterBorder: '#d8d8e2',
        titleColor: '#1a1a28',
        edgeLabelBackground: '#ffffff',
        textColor: '#2a2a3a',
        noteTextColor: '#2a2a3a',
        noteBkgColor: '#f0fae0',
        noteBorderColor: '#76B900',
      },
      flowchart: { curve: 'basis', padding: 16 },
      fontFamily: 'Inter, sans-serif',
    });
  }
}
