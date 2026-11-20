// Payment reconciliation screens. Uses the helpers app.js defines ($, $$, api, post, esc, notify, guarded).
const recon = { kind: 'payouts', files: {}, closes: [], poll: null, current: null };

const RECON_FILES = {
  payouts: [
    ['payouts', 'Stripe payouts export', 'Required. Export from a week before the month starts, so early deposits can be matched.'],
    ['itemized', 'Stripe itemized payout reconciliation report', 'Optional. Adds a check that each payout’s charges, fees and refunds add up.'],
    ['books', 'QuickBooks transaction detail for the bank account', 'Required. The deposits recorded in your books.']
  ],
  orders: [
    ['orders', 'Store orders export', 'Required.'],
    ['transactions', 'Payments export', 'Required. Charges, refunds and disputes from the payment platform.']
  ]
};

function moneyMetric(value, currency) {
  const n = Number(value);
  return `${Number.isFinite(n) ? n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : esc(value)}${currency ? ` <small>${esc(currency)}</small>` : ''}`;
}

function money(value, currency) {
  const n = Number(value);
  const text = Number.isFinite(n) ? n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : esc(value);
  return currency ? `${text} ${esc(currency)}` : text;
}

function showRecon() {
  clearTimeout(state.poll);
  state.project = null;
  $('#welcome').hidden = true;
  $('#workspace').hidden = true;
  $('#recon').hidden = false;
  $('#breadcrumb').textContent = 'Reconcile payments';
  $$('[data-project]').forEach(b => b.classList.remove('active'));
  $('#open-recon').classList.add('active');
  renderReconForm();
  return refreshRecon();
}

async function refreshRecon() {
  const data = await api('/reconciliations');
  recon.closes = data.closes;
  $('#recon-history').innerHTML = data.items.length ? data.items.map(item => {
    const h = item.headline;
    const line = !h ? esc(item.message)
      : item.kind === 'payouts' ? `${h.payouts} payouts · ${money(h.total)} · unexplained ${money(h.unexplained)} · ${h.exceptions} to review`
      : `${h.orders} orders · ${h.matched} matched · ${h.review} in review · ${h.exceptions} exceptions`;
    const what = item.kind === 'payouts' ? `${esc(item.period)}${item.account ? ' · ' + esc(item.account) : ''}` : esc(item.platform);
    return `<button class="source-card recon-item${recon.current === item.id ? ' active' : ''}" data-recon="${esc(item.id)}"><div><b>${esc(item.title)} · ${what}</b><span class="muted">${esc(item.created.slice(0, 16).replace('T', ' '))} UTC · ${esc(item.state)}</span><p>${line}</p></div></button>`;
  }).join('') : '<div class="empty">No reconciliations yet. Your saved ones appear here.</div>';
  $$('[data-recon]').forEach(b => b.onclick = guarded(() => openRecon(b.dataset.recon)));
  if (recon.kind === 'payouts') renderCloses();
}

function renderReconForm() {
  $$('[data-kind]').forEach(b => b.classList.toggle('active', b.dataset.kind === recon.kind));
  const fields = recon.kind === 'payouts'
    ? `<div class="form-grid"><label>Month to reconcile<input id="recon-period" type="month" required></label><label>Bank account label · to save this month’s close<input id="recon-account" maxlength="80" placeholder="e.g. Checking 1234"></label><label>Carry forward an earlier close<select id="recon-prior"></select></label></div>
       <details class="advanced"><summary>Matching settings</summary><div class="form-grid"><label>Days a deposit may post after the payout<input id="recon-window" type="number" min="0" max="31" value="5"></label><label>How deposits name the processor<input id="recon-processor" value="stripe"></label></div></details>`
    : `<div class="form-grid"><label>Store and payment platform<select id="recon-platform"><option value="shopify">Shopify orders · Shopify Payments</option><option value="woocommerce-stripe">WooCommerce orders · Stripe charges</option></select></label><label class="check"><input id="recon-postcode" type="checkbox"> Also compare postcodes · only when both files use the same address format</label></div>`;
  $('#recon-form').innerHTML = fields + '<div class="recon-slots" id="recon-slots"></div>';
  renderSlots();
  if (recon.kind === 'payouts') {
    renderCloses();
    $('#recon-account').oninput = renderCloses;
  }
}

function renderSlots() {
  const slots = RECON_FILES[recon.kind].map(([role, label, hint]) => {
    const file = recon.files[role];
    return `<label class="source-card recon-slot"><input type="file" accept=".csv,.xlsx" data-role="${role}" hidden><div><b>${esc(label)}</b><span class="muted">${esc(hint)}</span>${file ? `<p>✓ ${esc(file.name)} · ${(file.bytes / 1024).toFixed(1)} KB · sha256 ${esc(file.sha256.slice(0, 12))}…</p>` : ''}</div><span class="secondary small">${file ? 'Replace' : 'Choose file'}</span></label>`;
  }).join('');
  $('#recon-slots').innerHTML = slots;
  $$('[data-role]').forEach(input => input.onchange = guarded(async () => {
    const chosen = input.files[0];
    if (!chosen) return;
    const form = new FormData();
    form.append('file', chosen);
    notify('Adding ' + chosen.name + '…');
    recon.files[input.dataset.role] = await api('/reconciliations/uploads', { method: 'POST', body: form });
    notify('Added ' + chosen.name + '.');
    renderSlots();
  }));
}

function renderCloses() {
  const select = $('#recon-prior');
  if (!select) return;
  const account = $('#recon-account').value.trim();
  const usable = recon.closes.filter(c => c.account === account);
  select.innerHTML = `<option value="">None · first month for this account</option>` +
    usable.map(c => `<option value="${esc(c.id)}">${esc(c.period)} close · ${esc(c.account)} · ${esc(c.currency || '')}</option>`).join('');
  select.disabled = !usable.length;
}

$$('[data-kind]').forEach(b => b.onclick = () => {
  recon.kind = b.dataset.kind;
  recon.files = {};
  renderReconForm();
});

$('#recon-start').onclick = guarded(async () => {
  const files = Object.fromEntries(Object.entries(recon.files).map(([role, f]) => [role, f.id]));
  const body = recon.kind === 'payouts'
    ? { kind: 'payouts', period: $('#recon-period').value, close_account: $('#recon-account').value, prior_close: $('#recon-prior').value,
        window_after: Number($('#recon-window').value), processor: $('#recon-processor').value, files }
    : { kind: 'orders', platform: $('#recon-platform').value, match_postcode: $('#recon-postcode').checked, files };
  const started = await post('/reconciliations', body);
  recon.files = {};
  renderReconForm();
  await openRecon(started.id);
});

async function openRecon(id) {
  clearTimeout(recon.poll);
  recon.current = id;
  const detail = await api('/reconciliations/' + id);
  const busy = ['queued', 'running'].includes(detail.state);
  $('#recon-progress').hidden = !busy;
  renderReconResult(detail);
  await refreshRecon();
  if (busy) recon.poll = setTimeout(() => openRecon(id).catch(e => notify(e.message, true)), 700);
  else $('#recon-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// One card per item: what it is and how much, the explanation, the next step, and the exact records.
function exceptionCards(rows, records, currency) {
  return `<div class="recon-exceptions">${rows.map(row => {
    const refs = records.flatMap(([key, label]) => (row[key] || []).length ? [`<span><b>${esc(label)}</b> ${row[key].map(esc).join(', ')}</span>`] : []).join('');
    return `<article class="source-card recon-exception"><header><span class="tag">${esc(row.code)}</span>${row.amount !== undefined ? `<b>${money(row.amount, currency)}</b>` : ''}</header><p>${esc(row.detail)}</p>${row.action ? `<p class="recon-action">${esc(row.action)}</p>` : ''}<div class="recon-refs">${refs}</div></article>`;
  }).join('')}</div>`;
}

function renderReconResult(d) {
  const req = d.request;
  const inputs = Object.values(req.inputs).map(i => `${esc(i.name)} (sha256 ${esc(i.sha256.slice(0, 12))}…)`).join(' · ');
  const scope = req.kind === 'payouts'
    ? `${esc(req.period)}${req.close_account ? ' · account ' + esc(req.close_account) : ''}${d.currency ? ' · ' + esc(d.currency) : ''}${req.prior_close ? ' · carries forward the ' + esc(req.prior_close.slice(0, 8)) + ' close' : ''}`
    : esc(d.platform) + (req.match_postcode ? ' · postcodes compared' : '');
  let body = '';
  if (d.state === 'failed' || d.state === 'interrupted') {
    body = `<div class="banner error">${esc(d.message)}</div>`;
  } else if (d.state !== 'completed') {
    body = `<div class="inline-note">${esc(d.message)}…</div>`;
  } else if (req.kind === 'payouts') {
    const r = d.result, t = r.tieout, cur = d.currency;
    const lines = Object.entries(t.lines).sort((a, b) => b[1] - a[1]);
    const checks = [['Every payout is accounted for exactly once', t.every_payout_placed_once],
      ['Matched payouts equal matched deposits to the cent', t.matched_payouts_equal_matched_deposits],
      ['Nothing unexplained', Number(t.unexplained) === 0]];
    body = `<div class="metrics"><div class="metric"><span>Payouts in the month</span><strong>${r.counts.payouts}</strong></div><div class="metric"><span>Payout total</span><strong>${moneyMetric(t.payouts_total, cur)}</strong></div><div class="metric${Number(t.unexplained) ? ' warn' : ''}"><span>Unexplained</span><strong>${moneyMetric(t.unexplained, cur)}</strong></div><div class="metric${r.exceptions.length ? ' warn' : ''}"><span>Needs attention</span><strong>${r.exceptions.length}</strong></div></div>
      <div class="recon-tieout"><table class="summary-table"><thead><tr><th>Where the payouts went</th><th class="num">Amount</th></tr></thead><tbody>${lines.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${money(v, cur)}</td></tr>`).join('')}<tr><td><b>Unexplained</b></td><td class="num"><b>${money(t.unexplained, cur)}</b></td></tr></tbody></table>
      <ul class="recon-checks">${checks.map(([label, ok]) => `<li class="${ok ? 'ok' : 'bad'}">${ok ? '✓' : '✕'} ${esc(label)}</li>`).join('')}</ul></div>
      ${r.exceptions.length ? '<h3>Needs attention</h3>' + exceptionCards(r.exceptions, [['payouts', 'Payouts'], ['deposits', 'Book rows']], cur) : '<div class="banner">Nothing needs attention.</div>'}`;
  } else {
    const r = d.result, h = d.headline;
    body = `<div class="metrics"><div class="metric"><span>Orders</span><strong>${h.orders}</strong></div><div class="metric"><span>Matched</span><strong>${h.matched}</strong></div><div class="metric${h.review ? ' warn' : ''}"><span>In review</span><strong>${h.review}</strong></div><div class="metric${h.exceptions ? ' warn' : ''}"><span>Exceptions</span><strong>${h.exceptions}</strong></div></div>
      ${r.exceptions.length ? '<h3>Exceptions</h3>' + exceptionCards(r.exceptions, [['orders', 'Orders'], ['transactions', 'Transactions']]) : '<div class="banner">No exceptions.</div>'}`;
  }
  const files = (d.files || []).map(f => `<a class="export-file" href="/api/reconciliations/${encodeURIComponent(d.id)}/files/${encodeURIComponent(f)}"><span class="file-icon">${esc(f.split('.').pop().toUpperCase())}</span><b>${esc(f)}</b><span>↓</span></a>`).join('');
  $('#recon-result').innerHTML = `<div class="section-heading"><div><h2>${esc(d.title)}</h2><p>${scope}</p></div></div>${body}${files ? `<div class="export-files">${files}</div>` : ''}<p class="muted">Inputs, kept unchanged: ${inputs}. Saved as ${esc(d.id)}.</p>`;
}

$('#open-recon').onclick = guarded(showRecon);
