let currentOffset = 0;
const PAGE_SIZE = 50;
let currentFilters = { maxExp: null };
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

function fmtDate(dateStr) {
  if (!dateStr) return '';
  const [y, m, d] = dateStr.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  const opts = { month: 'short', day: 'numeric' };
  if (y !== new Date().getFullYear()) opts.year = 'numeric';
  return date.toLocaleDateString('en-US', opts);
}

function reformatDates() {
  document.querySelectorAll('.seen[data-iso]').forEach(el => {
    if (el.dataset.iso) el.textContent = 'Seen ' + fmtDatetime(el.dataset.iso);
  });
  document.querySelectorAll('.posted[data-date]').forEach(el => {
    if (el.dataset.date) el.textContent = 'Posted ' + fmtDate(el.dataset.date);
  });
}

function atsClass(type) {
  const known = ['greenhouse', 'workday', 'generic', 'amazon', 'apple', 'microsoft', 'ashby',
                 'oracle', 'jobspy', 'indeed', 'linkedin', 'glassdoor', 'google', 'zip_recruiter', 'manual'];
  return known.includes(type) ? `ats-${type}` : 'ats-generic';
}

function expBadge(years_exp) {
  if (years_exp == null || years_exp === 0) return '';
  if (years_exp >= 6) return `<span class="exp-badge exp-high">${years_exp}+ yrs req</span>`;
  if (years_exp >= 4) return `<span class="exp-badge exp-mid">${years_exp} yrs req</span>`;
  return `<span class="exp-badge exp-low">${years_exp} yr${years_exp > 1 ? 's' : ''} req</span>`;
}

function renderCard(job) {
  const location = job.location ? `<span class="location">📍 ${escHtml(job.location)}</span>` : '';
  const posted = job.date_posted ? `<span class="posted">Posted ${escHtml(fmtDate(job.date_posted))}</span>` : '';
  const seen = `<span class="seen">Seen ${escHtml(fmtDatetime(job.first_seen_at || job.scraped_at))}</span>`;

  const actions = currentView === 'skipped'
    ? `<button class="restore-btn" onclick="restoreJob(${job.id})">Restore</button>`
    : `<select class="status-select" data-id="${job.id}" onchange="handleStatusChange(this)">
        <option value="" ${!job.status ? 'selected' : ''}>Actions ▾</option>
        <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>Applied</option>
        <option value="resume_modify" ${job.status === 'resume_modify' ? 'selected' : ''}>Modify Resume</option>
        <option value="skip">Skip</option>
       </select>`;

  const pill = job.status === 'applied'
    ? `<span class="status-pill applied">Applied</span>`
    : job.status === 'resume_modify'
    ? `<span class="status-pill resume_modify">Modify Resume</span>`
    : '';

  const queueBtn = currentView === 'skipped' ? '' : (job.queued
    ? `<button class="queue-btn" disabled>✓ Queued</button>`
    : `<button class="queue-btn" data-id="${job.id}" onclick="addToQueue(${job.id}, this)" title="Add to tailor queue">＋ Queue</button>`);

  return `
    <div class="job-card" data-id="${job.id}" data-status="${escHtml(job.status || '')}">
      <div class="job-card-top">
        <span class="company-badge">${escHtml(job.company_name)}</span>
        <span class="ats-badge ${atsClass(job.source)}">${escHtml(job.source || '')}</span>
        ${expBadge(job.years_exp)}
        ${pill}
        <div class="job-actions">${queueBtn}${actions}</div>
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
  if (currentFilters.companies?.length) params.set('company', currentFilters.companies.join(','));
  if (currentFilters.sources?.length)   params.set('source',  currentFilters.sources.join(','));
  if (currentFilters.title)        params.set('title', currentFilters.title);
  if (currentFilters.since)        params.set('since', currentFilters.since);
  if (currentFilters.postedSince)  params.set('posted_since', currentFilters.postedSince);
  if (currentFilters.maxExp != null) params.set('max_exp', currentFilters.maxExp);

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
    // Update card appearance immediately
    const card = select.closest('.job-card');
    card.dataset.status = status;
    card.querySelector('.status-pill')?.remove();
    const pill = status === 'applied'
      ? `<span class="status-pill applied">Applied</span>`
      : status === 'resume_modify'
      ? `<span class="status-pill resume_modify">Modify Resume</span>`
      : '';
    if (pill) card.querySelector('.ats-badge').insertAdjacentHTML('afterend', pill);
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

function msValues(name) {
  return [...document.querySelectorAll(`.multiselect[data-name="${name}"] .ms-item input:checked`)]
    .map(i => i.value);
}

function filterMsOptions(input) {
  const q = input.value.toLowerCase();
  input.closest('.multiselect').querySelectorAll('.ms-item').forEach(item => {
    item.style.display = item.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

function applyFilters() {
  const maxExpVal = document.getElementById('f-max-exp').value;
  currentFilters = {
    companies:   msValues('company'),
    sources:     msValues('source'),
    title:       document.getElementById('f-title').value,
    since:       document.getElementById('f-found-since').value,
    postedSince: document.getElementById('f-posted-since').value,
    maxExp:      maxExpVal ? parseInt(maxExpVal) : null,
  };
  currentOffset = 0;
  fetchJobs();
}

function clearFilters() {
  document.querySelectorAll('.multiselect input[type="checkbox"]').forEach(c => { c.checked = false; });
  document.querySelectorAll('.multiselect .ms-search').forEach(s => { s.value = ''; filterMsOptions(s); });
  document.getElementById('f-title').value = '';
  document.getElementById('f-found-since').value = '';
  document.getElementById('f-posted-since').value = '';
  document.getElementById('f-max-exp').value = '';
  currentFilters = { maxExp: null };
  currentOffset = 0;
  fetchJobs();
}

function loadMore() {
  currentOffset += PAGE_SIZE;
  fetchJobs(true);
}

// ── Tailor queue ───────────────────────────────────────────────────────────────

async function fetchQueue() {
  const res = await fetch('/api/queue');
  const data = await res.json();
  document.getElementById('queue-count').textContent = data.jobs.length;
  const list = document.getElementById('queue-list');
  list.innerHTML = data.jobs.length
    ? data.jobs.map(renderQueueItem).join('')
    : '<p class="queue-empty">Add jobs with ＋ Queue to tailor resumes here.</p>';
}

function renderQueueItem(job) {
  const jd = job.full_description
    ? `<span class="queue-flag ok">JD ✓</span>`
    : `<button class="queue-mini" onclick="fetchJd(${job.id}, this)">Fetch JD</button>`;
  const resume = job.resume_path
    ? `<a class="queue-mini" href="/api/jobs/${job.id}/resume" target="_blank">⬇ Resume</a>`
    : `<button class="queue-mini" onclick="tailorResume(${job.id}, this)">Tailor Resume</button>`;
  const applied = job.status === 'applied'
    ? `<span class="status-pill applied">Applied</span>`
    : `<button class="queue-mini" onclick="markApplied(${job.id})">Mark Applied</button>`;
  return `
    <div class="queue-item" data-id="${job.id}">
      <div class="queue-item-top">
        <span class="ats-badge ${atsClass(job.source)}">${escHtml(job.source || '')}</span>
        <button class="queue-remove" onclick="removeFromQueue(${job.id})" title="Remove">✕</button>
      </div>
      <a class="queue-title" href="${escHtml(job.job_url)}" target="_blank" rel="noopener">${escHtml(job.job_title)}</a>
      <div class="queue-company">${escHtml(job.company_name)}</div>
      <div class="queue-actions">${jd} ${resume} ${applied}</div>
      <div class="queue-msg"></div>
    </div>`;
}

async function addToQueue(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '✓ Queued'; }
  await fetch(`/api/jobs/${id}/queue`, { method: 'POST' });
  fetchQueue();
}

async function removeFromQueue(id) {
  await fetch(`/api/jobs/${id}/queue`, { method: 'DELETE' });
  fetchQueue();
  // Re-enable the list card's queue button if visible.
  const cardBtn = document.querySelector(`.job-card[data-id="${id}"] .queue-btn`);
  if (cardBtn) { cardBtn.disabled = false; cardBtn.textContent = '＋ Queue'; }
}

function queueMsg(id, text, isErr = false, log = null) {
  const el = document.querySelector(`.queue-item[data-id="${id}"] .queue-msg`);
  if (!el) return;
  el.className = 'queue-msg' + (isErr ? ' err' : '');
  el.textContent = text;
  if (log) {
    const pre = document.createElement('details');
    pre.innerHTML = `<summary>compile log</summary><pre>${escHtml(log)}</pre>`;
    el.appendChild(pre);
  }
}

async function fetchJd(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  const r = await fetch(`/api/jobs/${id}/fetch-jd`, { method: 'POST' }).then(r => r.json());
  if (r.ok) fetchQueue();
  else { queueMsg(id, r.error || 'JD fetch failed', true); if (btn) { btn.disabled = false; btn.textContent = 'Fetch JD'; } }
}

async function tailorResume(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Tailoring…'; }
  const r = await fetch(`/api/jobs/${id}/tailor-resume`, { method: 'POST' }).then(r => r.json());
  if (r.ok) fetchQueue();
  else {
    queueMsg(id, r.error || 'Tailoring failed', true, r.compile_log);
    if (btn) { btn.disabled = false; btn.textContent = 'Tailor Resume'; }
  }
}

async function markApplied(id) {
  await fetch(`/api/jobs/${id}/status`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'applied' }),
  });
  fetchQueue();
  fetchStats();
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
  // Multi-selects: debounce so toggling several boxes batches into one fetch.
  document.querySelectorAll('.multiselect .ms-list').forEach(list => {
    list.addEventListener('change', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(applyFilters, 400);
    });
  });
  document.getElementById('f-found-since').addEventListener('change', applyFilters);
  document.getElementById('f-posted-since').addEventListener('change', applyFilters);
  document.getElementById('f-max-exp').addEventListener('change', applyFilters);

  fetchStats();
  fetchQueue();
  reformatDates();

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
