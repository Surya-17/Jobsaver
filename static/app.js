let currentOffset = 0;
const PAGE_SIZE = 50;
let currentFilters = {};
let currentSort = 'posted';
let currentView = 'active';
let debounceTimer = null;
let pollTimer = null;
let pendingSkipId = null;
let pendingSkipCard = null;
let pendingSkipTimer = null;

// ── Rendering ────────────────────────────────────────────────────────────────

function fmtDatetime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function atsClass(type) {
  const known = ['greenhouse', 'workday', 'generic', 'amazon', 'apple', 'microsoft'];
  return known.includes(type) ? `ats-${type}` : 'ats-generic';
}

function renderCard(job) {
  const location = job.location ? `<span class="location">📍 ${escHtml(job.location)}</span>` : '';
  const posted = job.date_posted ? `<span class="posted">Posted ${escHtml(job.date_posted)}</span>` : '';
  const seen = `<span class="seen">Seen ${escHtml(fmtDatetime(job.first_seen_at || job.scraped_at))}</span>`;

  const actions = currentView === 'skipped'
    ? `<button class="restore-btn" onclick="restoreJob(${job.id})">Restore</button>`
    : `<select class="status-select" data-id="${job.id}" onchange="handleStatusChange(this)">
        <option value="" ${!job.status ? 'selected' : ''}>Actions ▾</option>
        <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>Applied</option>
        <option value="resume_modify" ${job.status === 'resume_modify' ? 'selected' : ''}>Modify Resume</option>
        <option value="skip">Skip</option>
       </select>`;

  return `
    <div class="job-card" data-id="${job.id}">
      <div class="job-card-top">
        <span class="company-badge">${escHtml(job.company_name)}</span>
        <span class="ats-badge ${atsClass(job.ats_type)}">${escHtml(job.ats_type || '')}</span>
        <div class="job-actions">${actions}</div>
      </div>
      <h3 class="job-title">
        <a href="${escHtml(job.job_url)}" target="_blank" rel="noopener">${escHtml(job.job_title)}</a>
      </h3>
      <div class="job-meta">
        ${location}
        ${posted}
        ${seen}
      </div>
    </div>`;
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Fetching ─────────────────────────────────────────────────────────────────

async function fetchJobs(append = false) {
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: currentOffset, sort: currentSort, view: currentView });
  if (currentFilters.company) params.set('company', currentFilters.company);
  if (currentFilters.title)   params.set('title',   currentFilters.title);
  if (currentFilters.since)   params.set('since',   currentFilters.since);

  const res = await fetch(`/api/jobs?${params}`);
  const data = await res.json();

  const list = document.getElementById('job-list');
  const cards = data.jobs.map(renderCard).join('');

  if (append) {
    list.insertAdjacentHTML('beforeend', cards);
  } else {
    list.innerHTML = cards || '<p class="empty">No jobs matched your filters.</p>';
  }

  document.getElementById('showing-count').textContent =
    list.querySelectorAll('.job-card').length;
  document.getElementById('total-count').textContent = data.total;

  const wrap = document.getElementById('load-more-wrap');
  const loaded = (data.offset + data.jobs.length);
  wrap.innerHTML = loaded < data.total
    ? '<button id="load-more-btn" class="btn-secondary" onclick="loadMore()">Load more</button>'
    : '';
}

// ── Sort & View ───────────────────────────────────────────────────────────────

function setSort(sort) {
  currentSort = sort;
  currentOffset = 0;
  document.getElementById('sort-posted').classList.toggle('active', sort === 'posted');
  document.getElementById('sort-found').classList.toggle('active', sort === 'found');
  fetchJobs();
}

function setView(view) {
  currentView = view;
  currentOffset = 0;
  const skippedBtn = document.getElementById('skipped-btn');
  if (view === 'skipped') {
    skippedBtn.textContent = '← Active Jobs';
    skippedBtn.onclick = () => setView('active');
  } else {
    fetchStats(); // refresh skipped count in button
    skippedBtn.onclick = () => setView('skipped');
  }
  fetchJobs();
}

// ── Status / Skip ─────────────────────────────────────────────────────────────

async function handleStatusChange(select) {
  const jobId = parseInt(select.dataset.id);
  const status = select.value;
  if (!status) return;

  if (status === 'skip') {
    select.value = '';
    const card = select.closest('.job-card');
    card.classList.add('skipping');
    showUndoToast(jobId, card);
  } else {
    await fetch(`/api/jobs/${jobId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
  }
}

function showUndoToast(jobId, cardEl) {
  if (pendingSkipTimer) commitSkip();

  pendingSkipId = jobId;
  pendingSkipCard = cardEl;

  const toast = document.getElementById('undo-toast');
  const bar = document.getElementById('undo-bar');
  toast.classList.add('visible');
  bar.style.transition = 'none';
  bar.style.width = '100%';
  requestAnimationFrame(() => requestAnimationFrame(() => {
    bar.style.transition = 'width 5s linear';
    bar.style.width = '0%';
  }));

  pendingSkipTimer = setTimeout(commitSkip, 5000);
}

async function commitSkip() {
  if (!pendingSkipId) return;
  clearTimeout(pendingSkipTimer);
  const id = pendingSkipId;
  const card = pendingSkipCard;
  pendingSkipId = null;
  pendingSkipCard = null;
  pendingSkipTimer = null;
  document.getElementById('undo-toast').classList.remove('visible');

  await fetch(`/api/jobs/${id}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'skipped' }),
  });
  card?.remove();
  fetchStats();
}

function undoSkip() {
  clearTimeout(pendingSkipTimer);
  const card = pendingSkipCard;
  pendingSkipId = null;
  pendingSkipCard = null;
  pendingSkipTimer = null;
  document.getElementById('undo-toast').classList.remove('visible');
  card?.classList.remove('skipping');
}

async function restoreJob(jobId) {
  await fetch(`/api/jobs/${jobId}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: null }),
  });
  document.querySelector(`.job-card[data-id="${jobId}"]`)?.remove();
  fetchStats();
}

// ── Filters ───────────────────────────────────────────────────────────────────

function applyFilters() {
  currentFilters = {
    company: document.getElementById('f-company').value,
    title:   document.getElementById('f-title').value,
    since:   document.getElementById('f-since').value,
  };
  currentOffset = 0;
  fetchJobs();
}

function clearFilters() {
  document.getElementById('f-company').value = '';
  document.getElementById('f-title').value = '';
  document.getElementById('f-since').value = '';
  currentFilters = {};
  currentOffset = 0;
  fetchJobs();
}

function loadMore() {
  currentOffset += PAGE_SIZE;
  fetchJobs(true);
}

// ── Scrape trigger ────────────────────────────────────────────────────────────

async function triggerScrape(testMode = false) {
  const btn = document.getElementById('scrape-btn');
  const testBtn = document.getElementById('test-btn');
  const msg = document.getElementById('scrape-msg');
  btn.disabled = true;
  testBtn.disabled = true;
  btn.innerHTML = testMode ? 'Scrape All' : '<span class="spinner"></span> Starting…';
  if (testMode) testBtn.innerHTML = '<span class="spinner"></span> Testing…';
  msg.textContent = '';

  const url = testMode ? '/scrape?test=true' : '/scrape';
  try {
    const res = await fetch(url, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'already_running') {
      msg.textContent = 'Already running.';
      btn.innerHTML = '<span class="spinner"></span> Scraping…';
    } else {
      msg.textContent = data.message || 'Started…';
      pollScrapeStatus();
    }
  } catch (e) {
    btn.disabled = false;
    testBtn.disabled = false;
    btn.textContent = 'Scrape All';
    testBtn.textContent = 'Test (10)';
    msg.textContent = 'Failed to start.';
  }
}

function pollScrapeStatus() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch('/api/scrape/status');
      const data = await res.json();
      const btn = document.getElementById('scrape-btn');
      const msg = document.getElementById('scrape-msg');

      if (!data.running) {
        clearInterval(pollTimer);
        btn.disabled = false;
        document.getElementById('test-btn').disabled = false;
        btn.textContent = 'Scrape All';
        document.getElementById('test-btn').textContent = 'Test (10)';

        if (data.last_run) {
          const r = data.last_run;
          const secs = r.started_at && r.finished_at
            ? Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000)
            : null;
          const duration = secs !== null ? ` in ${secs}s` : '';
          msg.textContent = `Done${duration}: ${r.total_new} new, ${r.total_updated} updated, ${r.companies_failed} failed.`;
        }
        // Refresh stats + job list
        fetchJobs();
        fetchStats();
      }
    } catch { /* ignore transient errors */ }
  }, 3000);
}

async function fetchStats() {
  try {
    const res = await fetch('/api/stats');
    const s = await res.json();
    document.getElementById('stat-total').textContent = s.total_jobs;
    document.getElementById('stat-companies').textContent = s.company_count;
    const dateEl = document.getElementById('stat-date');
    if (dateEl && s.last_scraped) {
      dateEl.textContent = fmtDatetime(s.last_scraped);
      dateEl.dataset.iso = s.last_scraped;
    }
    const skippedCountEl = document.getElementById('skipped-count');
    if (skippedCountEl) skippedCountEl.textContent = s.skipped_count ?? 0;
  } catch { /* ignore */ }
}

// ── Title input debounce ──────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('f-title').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilters, 300);
  });
  document.getElementById('f-company').addEventListener('change', applyFilters);
  document.getElementById('f-since').addEventListener('change', applyFilters);

  fetchStats();

  // Resume polling if scrape is already running on page load
  fetch('/api/scrape/status').then(r => r.json()).then(d => {
    if (d.running) {
      const btn = document.getElementById('scrape-btn');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Scraping…';
      pollScrapeStatus();
    }
  });
});
