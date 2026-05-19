let currentOffset = 0;
const PAGE_SIZE = 50;
let currentFilters = {};
let debounceTimer = null;
let pollTimer = null;

// ── Rendering ────────────────────────────────────────────────────────────────

function atsClass(type) {
  const known = ['greenhouse', 'workday', 'generic', 'amazon', 'apple', 'microsoft'];
  return known.includes(type) ? `ats-${type}` : 'ats-generic';
}

function renderCard(job) {
  const location = job.location ? `<span class="location">📍 ${escHtml(job.location)}</span>` : '';
  const posted = job.date_posted ? `<span class="posted">Posted ${escHtml(job.date_posted)}</span>` : '';
  const seen = `<span class="seen">Seen ${escHtml((job.first_seen_at || job.scraped_at || '').slice(0, 10))}</span>`;
  return `
    <div class="job-card">
      <div class="job-card-top">
        <span class="company-badge">${escHtml(job.company_name)}</span>
        <span class="ats-badge ${atsClass(job.ats_type)}">${escHtml(job.ats_type || '')}</span>
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
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: currentOffset });
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
          msg.textContent = `Done: ${r.total_new} new, ${r.total_updated} updated, ${r.companies_failed} failed.`;
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
      dateEl.textContent = s.last_scraped.slice(0, 10);
      dateEl.dataset.iso = s.last_scraped;
    }
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
