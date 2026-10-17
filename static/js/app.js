/* Yurtora CRM — app.js: мобильное меню, count-up цифр, ripple на кнопках */
(() => {
  const body = document.body;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // --- Мобильное меню ---
  const setNav = (open) => body.classList.toggle('mv-nav-open', open);
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-mv-nav-toggle]')) setNav(!body.classList.contains('mv-nav-open'));
    else if (e.target.closest('[data-mv-nav-close]')) setNav(false);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setNav(false); });

  // --- Count-up: <span data-countup="1500000">…</span> ---
  const fmt = (n) => Math.round(n).toLocaleString('ru-RU');
  document.querySelectorAll('[data-countup]').forEach((el) => {
    const target = Number(el.dataset.countup);
    if (!Number.isFinite(target)) return;
    if (reduce) { el.textContent = fmt(target); return; }
    const duration = 900;
    const start = performance.now();
    const step = (now) => {
      const p = Math.min((now - start) / duration, 1);
      el.textContent = fmt(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });

  // --- Excel eksport: joriy filtrlar (URL) bilan ---
  document.addEventListener('click', (e) => {
    const link = e.target.closest('[data-export-xlsx]');
    if (!link) return;
    e.preventDefault();
    const params = new URLSearchParams(location.search);
    params.delete('page');
    params.set('export', 'xlsx');
    location.href = `${location.pathname}?${params.toString()}`;
  });

  // --- Ochiq menyu tashqariga bosilganda yopiladi ---
  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.mv-menu[open]').forEach((d) => { if (!d.contains(e.target)) d.removeAttribute('open'); });
  });

  // --- Amallar menyusi oynaga nisbatan joylashadi: jadvalning overflow konteyneri uni kesmaydi
  // va ichki aylantirish paydo bo'lmaydi. Pastda joy yetmasa, menyu yuqoriga ochiladi. ---
  const placeMenu = (d) => {
    const list = d.querySelector('.mv-menu-list');
    if (!list) return;
    list.classList.add('is-floating');
    const r = d.querySelector('summary').getBoundingClientRect();
    const h = list.offsetHeight, w = list.offsetWidth, gap = 4;
    const top = (r.bottom + gap + h <= window.innerHeight - 8 || r.top - gap - h < 8) ? r.bottom + gap : r.top - gap - h;
    list.style.top = `${Math.round(top)}px`;
    list.style.left = `${Math.round(Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)))}px`;
  };
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (d instanceof HTMLDetailsElement && d.classList.contains('mv-menu') && d.open) placeMenu(d);
  }, true);
  const closeMenus = () => document.querySelectorAll('details.mv-menu[open]').forEach((d) => d.removeAttribute('open'));
  window.addEventListener('resize', closeMenus);
  document.addEventListener('scroll', (e) => { if (!(e.target instanceof Element && e.target.closest('.mv-menu-list'))) closeMenus(); }, true);

  // --- Ripple tugmalarda (harakatni kamaytirish rejimida o'chiq; qolgan funksiyalar ishlayveradi) ---
  if (!reduce) document.addEventListener('pointerdown', (e) => {
    const el = e.target.closest('button, a.bg-indigo-600, .mv-ripple');
    if (!el || el.disabled || el.hasAttribute('disabled')) return;
    if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
    el.style.overflow = 'hidden';

    const rect = el.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 2;
    const dot = document.createElement('span');
    dot.className = 'mv-ripple-dot';
    dot.style.width = dot.style.height = size + 'px';
    dot.style.left = (e.clientX - rect.left - size / 2) + 'px';
    dot.style.top = (e.clientY - rect.top - size / 2) + 'px';
    el.appendChild(dot);
    dot.addEventListener('animationend', () => dot.remove());
  });
  // --- Oyna «To'lov»: qidiruv va forma. Oddiy fetch ishlatiladi (HTMX/CDN'ga bog'liq emas) ---
  const payModal = document.getElementById('pay-modal');
  if (payModal) {
    const search = document.getElementById('pay-search');
    const results = document.getElementById('pay-results');
    const formBox = document.getElementById('pay-form');
    const MIN_LETTERS = 3;   // 3 ta harfdan oldin hech narsa ko'rsatilmaydi
    const errorHtml = '<p class="mv-pay-empty">Xatolik yuz berdi. Qayta urinib ko‘ring.</p>';
    let searchSeq = 0;       // eskirgan javoblarni e'tiborsiz qoldirish uchun
    let timer = null;

    const getHtml = async (url) => {
      const res = await fetch(url, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'HX-Current-URL': location.href },
      });
      if (!res.ok || res.redirected) throw new Error('bad response');
      return res.text();
    };
    const runSearch = async () => {
      const query = search.value.trim();
      const seq = ++searchSeq;
      if (query.replace(/\s+/g, '').length < MIN_LETTERS) { results.innerHTML = ''; return; }
      try {
        const html = await getHtml(`${search.dataset.url}?q=${encodeURIComponent(query)}`);
        if (seq === searchSeq) results.innerHTML = html;
      } catch (err) {
        if (seq === searchSeq) results.innerHTML = errorHtml;
      }
    };
    const openPay = () => {
      payModal.hidden = false;
      search.value = '';
      results.innerHTML = '';
      formBox.innerHTML = '';
      search.focus();
    };
    const closePay = () => { payModal.hidden = true; };

    search.addEventListener('input', () => {
      formBox.innerHTML = '';   // eski o'quvchi formasi yangi qidiruvda qolib ketmasin
      clearTimeout(timer);
      timer = setTimeout(runSearch, 250);
    });
    document.addEventListener('click', async (e) => {
      if (e.target.closest('[data-pay-open]')) { openPay(); return; }
      // Ro'yxatdagi "To'lov qabul qilish": oyna shu o'quvchi formasi bilan ochiladi
      const payStudent = e.target.closest('[data-pay-student]');
      if (payStudent) {
        openPay();
        payStudent.closest('details')?.removeAttribute('open');
        try { formBox.innerHTML = await getHtml(payStudent.dataset.payStudent); } catch (err) { formBox.innerHTML = errorHtml; }
        return;
      }
      if (e.target.closest('[data-pay-close]')) { closePay(); return; }
      const pick = e.target.closest('[data-pay-pick]');
      if (pick) {
        try {
          formBox.innerHTML = await getHtml(pick.dataset.payPick);
          results.innerHTML = '';
        } catch (err) {
          formBox.innerHTML = errorHtml;
        }
      }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !payModal.hidden) closePay(); });
  }

  // --- Kunlik statistika oynasi: yuklash faqat ochilganda yoki davr o'zgarganda ---
  const dsModal = document.getElementById('ds-modal');
  if (dsModal) {
    const dsBody = document.getElementById('ds-body');
    const dsSelect = document.getElementById('ds-period');
    const dsDate = document.getElementById('ds-date');
    const dsUrl = dsModal.dataset.url;
    let loadedKey = null;   // oxirgi muvaffaqiyatli so'rov: bir xil so'rov qayta yuborilmaydi
    let requestId = 0;      // eskirgan javoblarni e'tiborsiz qoldirish uchun
    let lastFocus = null;

    const queryKey = () => {
      const params = new URLSearchParams({ period: dsSelect.value });
      if (dsSelect.value === 'date' && dsDate.value) params.set('date', dsDate.value);
      return params.toString();
    };
    const loadStats = async () => {
      const key = queryKey();
      if (key === loadedKey) return;
      const myId = ++requestId;
      dsBody.innerHTML = '<p class="mv-ds-state">Yuklanmoqda…</p>';
      try {
        const res = await fetch(`${dsUrl}?${key}`, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        if (!res.ok || res.redirected) throw new Error('bad response');
        const html = await res.text();
        if (myId !== requestId) return;
        dsBody.innerHTML = html;
        loadedKey = key;
      } catch (err) {
        if (myId !== requestId) return;
        loadedKey = null;
        dsBody.innerHTML = '<div class="mv-ds-state mv-ds-state--error"><p>Xatolik yuz berdi. Qayta urinib ko‘ring.</p>'
          + '<button type="button" class="mv-ds-retry" data-ds-retry>Qayta yuklash</button></div>';
      }
    };
    const openDs = () => {
      lastFocus = document.activeElement;
      dsModal.hidden = false;
      document.body.classList.add('mv-ds-lock');
      dsModal.querySelector('.mv-ds-x').focus();
      loadStats();
    };
    const closeDs = () => {
      dsModal.hidden = true;
      document.body.classList.remove('mv-ds-lock');
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    };
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-ds-open]')) openDs();
      else if (e.target.closest('[data-ds-close]')) closeDs();
      else if (e.target.closest('[data-ds-retry]')) loadStats();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !dsModal.hidden) closeDs(); });
    dsSelect.addEventListener('change', () => {
      dsDate.hidden = dsSelect.value !== 'date';
      loadStats();
    });
    dsDate.addEventListener('change', () => { if (dsDate.value) loadStats(); });
    if (dsModal.dataset.autoOpen === '1') openDs();
  }
})();

// --- Yuqori menyu: tashqariga bosilganda yoki Esc bosilganda ochiq ro'yxatlar yopiladi ---
(() => {
  const closeAll = (except) => document.querySelectorAll('.yt-dd[open]').forEach((d) => { if (d !== except) d.removeAttribute('open'); });
  document.addEventListener('click', (e) => closeAll(e.target.closest('.yt-dd')));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(null); });
})();

// --- Summalar: xonalar bo'shliq bilan ajratiladi (1 500 000). Server bo'shliqli qiymatni ham qabul qiladi ---
(() => {
  const group = (digits) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const format = (input) => {
    const raw = input.value;
    const caret = input.selectionStart ?? raw.length;
    const digitsBefore = raw.slice(0, caret).replace(/\D/g, '').length;
    const out = group(raw.replace(/\D/g, '').replace(/^0+(?=\d)/, ''));
    if (out === raw) return;
    input.value = out;
    if (document.activeElement !== input) return;
    // Kursor o'sha raqamdan keyin qoladi
    let pos = 0;
    for (let seen = 0; pos < out.length && seen < digitsBefore; pos++) if (/\d/.test(out[pos])) seen++;
    input.setSelectionRange(pos, pos);
  };
  const formatAll = (root) => root.querySelectorAll('input[data-money]').forEach(format);
  document.addEventListener('input', (e) => { if (e.target.matches?.('input[data-money]')) format(e.target); });
  formatAll(document);
  // To'lov oynasi va HTMX qismlari sahifaga keyin qo'shiladi
  new MutationObserver((records) => records.forEach((r) => r.addedNodes.forEach((n) => {
    if (n.nodeType !== 1) return;
    if (n.matches('input[data-money]')) format(n); else formatAll(n);
  }))).observe(document.body, { childList: true, subtree: true });
})();

// --- Umumiy qidiruv: Ctrl/⌘+K bilan ochiladi, strelkalar bilan yuriladi, Esc yopadi ---
(() => {
  const input = document.getElementById('yt-search-input');
  const pop = document.getElementById('yt-search-results');
  if (!input || !pop) return;
  const kbd = document.querySelector('.yt-kbd');
  if (kbd && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) kbd.textContent = '⌘K';
  const links = () => [...pop.querySelectorAll('a')];
  const close = () => { pop.innerHTML = ''; };
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); input.focus(); input.select(); }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && links().length) { e.preventDefault(); links()[0].focus(); }
    else if (e.key === 'Escape') { close(); input.blur(); }
  });
  pop.addEventListener('keydown', (e) => {
    const list = links();
    const i = list.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); (list[i + 1] || list[0]).focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (i <= 0) input.focus(); else list[i - 1].focus(); }
    else if (e.key === 'Escape') { close(); input.focus(); }
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.yt-search')) close(); });
})();
