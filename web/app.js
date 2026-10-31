'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const pretty = v => JSON.stringify(v, null, 2);
const fmt = v => Number(v || 0).toLocaleString();
const state = {
  token: '',
  project: null,
  recipe: null,
  run: null,
  tab: 'sources',
  dirty: false,
  offset: 0,
  rows: null,
  modalSave: null,
  poll: null
};
let notificationTimer;

function notify(message, error = false) {
  const n = $('#notification');
  n.textContent = message;
  n.className = error ? 'error' : '';
  n.hidden = false;
  clearTimeout(notificationTimer);
  notificationTimer = setTimeout(() => n.hidden = true, error ? 11000 : 4500);
}
async function api(path, options = {}) {
  const headers = {
    'x-workbench-token': state.token,
    ...options.headers
  };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const r = await fetch('/api' + path, {
    ...options,
    headers
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || data.detail || 'Request failed');
  return data;
}

function guarded(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      notify(e.message, true);
    }
  };
}
const post = (path, body) => api(path, {
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body)
});
const put = (path, body) => api(path, {
  method: 'PUT',
  body: JSON.stringify(body)
});
const projectPath = () => '/projects/' + state.project.id;

function selectOptions(values, selected, blank = false) {
  return (blank ? '<option value="">Not mapped</option>' : '') + values.map(v => `<option value="${esc(typeof v==='object'?v.value:v)}" ${(typeof v==='object'?v.value:v)===selected?'selected':''}>${esc(typeof v==='object'?v.label:v)}</option>`).join('');
}

function grid(columns, rows, action) {
  return `<table class="data-grid"><thead><tr>${action?'<th>Review</th>':''}${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map((r,i)=>`<tr>${action?`<td><button class="quiet small review-record" data-row="${i}">Inspect / correct ↗</button></td>`:''}${r.map((v,j)=>`<td class="${columns[j]==='__issues'?'issue':''}" title="${esc(v)}">${v===null?'<span class="muted">∅</span>':esc(v)}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${columns.length+1}" class="empty">No records in this selection.</td></tr>`}</tbody></table>`;
}

function markDirty() {
  state.dirty = true;
  $('#recipe-status').textContent = 'Unsaved recipe changes. Save before running.';
  $('#recipe-json').value = pretty(state.recipe);
  $('#approve-joins').hidden = true;
  if (state.run?.manifest) {
    state.run.stale = true;
    renderRun();
  }
}

function openModal(title, body, save, label = 'Save') {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = body;
  state.modalSave = save;
  $('#confirm-modal').textContent = label;
  $('#confirm-modal').hidden = !save;
  $('#modal').showModal();
}

function closeModal() {
  $('#modal').close();
  state.modalSave = null;
}
$('#close-modal').onclick = closeModal;
$('#cancel-modal').onclick = closeModal;
$('#confirm-modal').onclick = guarded(async () => {
  if (state.modalSave) await state.modalSave();
  closeModal();
});
async function refreshProjects() {
  const projects = await api('/projects');
  $('#projects').innerHTML = projects.map(p => `<button data-project="${p.id}" class="${state.project?.id===p.id?'active':''}">◈ &nbsp;${esc(p.name)}</button>`).join('');
  $$('[data-project]').forEach(b => b.onclick = guarded(() => loadProject(b.dataset.project)));
}
async function loadProject(id) {
  clearTimeout(state.poll);
  state.project = await api('/projects/' + id);
  state.recipe = structuredClone(state.project.recipe);
  state.dirty = false;
  state.run = null;
  state.offset = 0;
  $('#welcome').hidden = true;
  $('#workspace').hidden = false;
  $('#project-name').textContent = state.project.name;
  $('#breadcrumb').textContent = state.project.name;
  $('#project-subtitle').textContent = `${state.project.sources.length} source files · Saved recipes · Original files preserved`;
  renderSources();
  renderRecipe();
  if (state.project.latest_run) {
    state.run = await api(projectPath() + '/runs/' + state.project.latest_run);
    renderRun();
    if (['queued', 'running'].includes(state.run.state)) pollRun();
  } else renderRun();
  await refreshProjects();
  switchTab(state.tab);
}

function newProject() {
  openModal('Create a project', '<label class="form-label">Project name<input id="new-name" placeholder="e.g. Monthly customer operations" maxlength="120" autofocus></label>', async () => {
    const p = await post('/projects', {
      name: $('#new-name').value
    });
    state.tab = 'sources';
    await loadProject(p.id);
  }, 'Create project');
}
$('#new-project').onclick = newProject;
$('#welcome-new').onclick = newProject;
$('#demo').onclick = guarded(async () => {
  const button = $('#demo');
  button.disabled = true;
  try {
    const p = await post('/demo');
    state.tab = 'sources';
    await loadProject(p.id);
    notify('Demo ready. Inspect the sources, then run the project.');
  } finally {
    button.disabled = false;
  }
});
$('#storage-info').onclick = guarded(async () => {
  const s = await api('/session');
  openModal('Where your work is saved', `<p>Project files, recipes, and completed packages:</p><p><code>${esc(s.project_root)}</code></p><p>Active processing databases and dependencies stay outside this folder. Processing runs locally. OneDrive synchronization follows your existing OneDrive settings.</p>`, null);
});

function switchTab(tab) {
  state.tab = tab;
  $$('[data-tab]').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
    b.setAttribute('aria-selected', b.dataset.tab === tab);
  });
  $$('.tab-panel').forEach(p => p.hidden = p.id !== tab + '-panel');
  if (tab === 'review') {
    renderProfiles();
    if (state.run?.manifest) guarded(loadRows)();
  }
  if (tab === 'export') renderExport();
}
$$('[data-tab]').forEach(b => b.onclick = () => {
  if (b.dataset.tab === state.tab) return;
  state.tab = b.dataset.tab;
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const transition = document.startViewTransition(() => switchTab(state.tab));
    // Rapid navigation may skip a visual transition; the DOM update still runs.
    transition.ready.catch(() => {});
  } else switchTab(b.dataset.tab);
});
$('#open-recipe').onclick = guarded(async () => {
  if (!state.recipe) {
    state.recipe = await post(projectPath() + '/suggest-recipe');
    state.project.recipe = state.recipe;
    renderRecipe();
  }
  switchTab('recipe');
});
async function uploadFiles(files, replaceId) {
  if (!state.project) return;
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    if (replaceId) form.append('replace_id', replaceId);
    notify('Adding ' + file.name + '…');
    await api(projectPath() + '/sources', {
      method: 'POST',
      body: form
    });
  }
  const id = state.project.id;
  await loadProject(id);
  notify(replaceId ? 'Source replaced. Review mappings and rerun.' : 'Files added. Set up or update the recipe before running.');
}
$('#files').onchange = guarded(async e => {
  await uploadFiles([...e.target.files]);
  e.target.value = '';
});
const dz = $('#dropzone');
dz.onkeydown = e => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    $('#files').click();
  }
};
['dragover', 'dragenter'].forEach(type => dz.addEventListener(type, e => {
  e.preventDefault();
  dz.classList.add('over');
}));
['dragleave', 'drop'].forEach(type => dz.addEventListener(type, e => {
  e.preventDefault();
  dz.classList.remove('over');
}));
dz.addEventListener('drop', guarded(e => uploadFiles([...e.dataTransfer.files])));

function renderSources() {
  $('#source-count').textContent = state.project.sources.length;
  $('#source-list').innerHTML = state.project.sources.map(s => `<article class="source-card"><span class="file-icon">${s.filename.toLowerCase().endsWith('.xlsx')?'XLSX':'CSV'}</span><div><div class="source-name">${esc(s.filename)}</div><div class="source-meta">${fmt(s.bytes/1024)} KB · ${s.headers.length} columns · Header row ${s.settings.header_row}${s.settings.sheet?' · '+esc(s.settings.sheet):' · '+esc(s.settings.encoding)}</div></div><div class="actions"><button class="quiet" data-configure="${s.id}">Inspect & configure</button><button class="quiet" data-replace="${s.id}">Replace file</button></div></article>`).join('');
  $$('[data-configure]').forEach(b => b.onclick = () => configureSource(b.dataset.configure));
  $$('[data-replace]').forEach(b => b.onclick = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,.xlsx';
    input.onchange = guarded(() => uploadFiles([...input.files], b.dataset.replace));
    input.click();
  });
}

function configureSource(id) {
  const s = state.project.sources.find(s => s.id === id);
  openModal(s.filename, `<div class="form-grid">${s.sheets.length?`<label>Worksheet<select id="source-sheet">${selectOptions(s.sheets,s.settings.sheet)}</select></label>`:`<label>Encoding<select id="source-encoding">${selectOptions(['utf-8-sig','cp1252','utf-16'],s.settings.encoding)}</select></label><label>Delimiter<select id="source-delimiter">${selectOptions([{value:',',label:'Comma'},{value:';',label:'Semicolon'},{value:'\t',label:'Tab'},{value:'|',label:'Pipe'}],s.settings.delimiter)}</select></label>`}<label>Header row / record<input id="source-header" type="number" min="1" max="200" value="${s.settings.header_row}"></label></div><div class="note">The current selection is a suggestion. Confirm the right sheet and header before running. A workbook with multiple tables should be split into separate sheets or files.</div><h3>First source rows</h3><div class="grid-wrap">${grid(['Row',...Array.from({length:Math.min(12,Math.max(...s.header_candidates.map(r=>r.length)))},(_,i)=>'Column '+(i+1))],s.header_candidates.slice(0,10).map((r,i)=>[i+1,...r.slice(0,12)]))}</div><h3>Field suggestions</h3>${s.notes.map(n=>`<p><b>${esc(n.field)}</b> — ${esc(n.reason)}</p>`).join('')}`, async () => {
    const settings = {
      ...s.settings,
      header_row: Number($('#source-header').value),
      end_row: $('#source-end').value ? Number($('#source-end').value) : null
    };
    if ($('#source-sheet')) settings.sheet = $('#source-sheet').value;
    if ($('#source-encoding')) {
      settings.encoding = $('#source-encoding').value;
      settings.delimiter = $('#source-delimiter').value;
    }
    await put(projectPath() + '/sources/' + id, settings);
    await loadProject(state.project.id);
    notify('Source selection saved. Check the recipe mappings before rerunning.');
  });
  $('#modal-body .form-grid').insertAdjacentHTML('beforeend', `<label>Last data row / record (optional)<input id="source-end" type="number" min="2" value="${esc(s.settings.end_row ?? '')}" placeholder="Read to end"></label>`);
}

function renderRecipe() {
  const r = state.recipe;
  $('#table-editors').innerHTML = r ? r.tables.map((t, ti) => `<article class="table-editor"><div class="card-heading"><h3>▤ &nbsp;${esc(t.name)}</h3><span class="muted">${t.sources.length} source${t.sources.length===1?'':'s'}</span><div class="actions"><label class="compact">Duplicates <select data-dup="${ti}">${selectOptions([{value:'flag',label:'Flag repeats'},{value:'keep_all',label:'Keep all'},{value:'drop_exact',label:'Exclude exact repeats'}],t.duplicate_policy)}</select></label><button class="quiet small" data-add-field="${ti}">＋ Field</button></div></div><div class="grid-wrap"><table class="field-table"><thead><tr><th>OUTPUT FIELD</th><th>TYPE</th><th>REQUIRED</th><th>KEY</th><th>RULES</th></tr></thead><tbody>${t.fields.map((f,fi)=>`<tr><td><b>${esc(f.name)}</b></td><td><select data-type="${ti},${fi}">${selectOptions([{value:'text',label:'Text'},{value:'integer',label:'Whole number'},{value:'decimal',label:'Decimal'},{value:'date',label:'Date'}],f.type)}</select></td><td><input type="checkbox" aria-label="${esc(f.name)} required" data-required="${ti},${fi}" ${f.required?'checked':''}></td><td><input type="checkbox" aria-label="${esc(f.name)} key" data-key="${ti},${fi}" ${t.key.includes(f.name)?'checked':''}></td><td><button class="quiet small" data-rules="${ti},${fi}">Configure ↗</button></td></tr>`).join('')}</tbody></table></div><div class="source-mappings">${t.sources.map((s,si)=>{const source=state.project.sources.find(x=>x.id===s.id);return `<div class="mapping-row"><b>${esc(source?.filename||'Missing source')}</b><span>→ ${esc(t.name)}</span><button class="quiet small" data-map="${ti},${si}">Map columns</button><label>Move to <select data-move="${ti},${si}">${selectOptions(r.tables.map(t=>t.name),t.name)}</select></label></div>`;}).join('')}</div></article>`).join('') : '<div class="empty">Add source files, then generate a starting recipe.</div>';
  $('#recipe-status').textContent = state.dirty ? 'Unsaved recipe changes. Save before running.' : r ? 'Recipe saved. First-run suggestions should be checked against your project context.' : '';
  $('#recipe-json').value = r ? pretty(r) : '';
  $$('[data-type]').forEach(b => b.onchange = () => {
    const [ti, fi] = b.dataset.type.split(',').map(Number);
    r.tables[ti].fields[fi].type = b.value;
    r.tables[ti].fields[fi].inferred = false;
    markDirty();
  });
  $$('[data-required]').forEach(b => b.onchange = () => {
    const [ti, fi] = b.dataset.required.split(',').map(Number);
    r.tables[ti].fields[fi].required = b.checked;
    markDirty();
  });
  $$('[data-key]').forEach(b => b.onchange = () => {
    const [ti, fi] = b.dataset.key.split(',').map(Number), t = r.tables[ti], name = t.fields[fi].name;
    t.key = b.checked ? [...new Set([...t.key, name])] : t.key.filter(n => n !== name);
    markDirty();
  });
  $$('[data-dup]').forEach(b => b.onchange = () => {
    r.tables[Number(b.dataset.dup)].duplicate_policy = b.value;
    markDirty();
  });
  $$('[data-rules]').forEach(b => b.onclick = () => fieldRules(...b.dataset.rules.split(',').map(Number)));
  $$('[data-map]').forEach(b => b.onclick = () => mapSource(...b.dataset.map.split(',').map(Number)));
  $$('[data-move]').forEach(b => b.onchange = () => {
    const [ti, si] = b.dataset.move.split(',').map(Number), old = r.tables[ti], source = old.sources.splice(si, 1)[0], target = r.tables.find(t => t.name === b.value);
    for (const [name] of Object.entries(source.mapping)) {
      if (!target.fields.some(f => f.name === name)) target.fields.push(structuredClone(old.fields.find(f => f.name === name)));
    }
    target.sources.push(source);
    r.tables = r.tables.filter(t => t.sources.length);
    markDirty();
    renderRecipe();
  });
  $$('[data-add-field]').forEach(b => b.onclick = () => openModal('Add output field', '<label class="form-label">Field name<input id="field-name" placeholder="e.g. department"></label>', () => {
    const name = $('#field-name').value;
    if (!/^[a-z][a-z0-9_]{0,59}$/.test(name)) throw new Error('Use a lowercase field name with underscores.');
    r.tables[Number(b.dataset.addField)].fields.push({
      name,
      type: 'text',
      required: false,
      trim: true,
      missing: [],
      mapping: {},
      allowed: []
    });
    markDirty();
    renderRecipe();
  }));
  renderViews();
  renderSuggestions();
  $$('.table-editor').forEach((card, ti) => {
    $$('.source-mappings > .mapping-row', card).forEach((row, si) => {
      const button = document.createElement('button');
      button.className = 'quiet small';
      button.textContent = 'Source format: ' + (r.tables[ti].sources[si].format || 'Field defaults');
      button.onclick = () => sourceFormat(ti, si);
      row.append(button);
    });
    const controls = document.createElement('div');
    controls.className = 'source-mappings';
    controls.innerHTML = `<button class="secondary small">Cross-field checks (${(r.tables[ti].checks || []).length})</button><span class="muted"> Compare fields before duplicate and join checks.</span>`;
    $('button', controls).onclick = () => tableChecks(ti);
    card.append(controls);
  });
}

function fieldRules(ti, fi) {
  const f = state.recipe.tables[ti].fields[fi];
  openModal('Rules · ' + f.name, `<div class="form-grid"><label>Trim outer whitespace<input type="checkbox" id="rule-trim" ${f.trim!==false?'checked':''}></label><label>Missing markers · one per line<textarea id="rule-missing" rows="3">${esc((f.missing||[]).join('\n'))}</textarea></label><label>Allowed values · one per line<textarea id="rule-allowed" rows="3">${esc((f.allowed||[]).join('\n'))}</textarea></label><label>Category mapping · JSON object<textarea id="rule-mapping" rows="3">${esc(pretty(f.mapping||{}))}</textarea></label><label>Date format<select id="rule-date">${selectOptions([{value:'%Y-%m-%d',label:'YYYY-MM-DD'},{value:'%m/%d/%Y',label:'MM/DD/YYYY'},{value:'%d/%m/%Y',label:'DD/MM/YYYY'},{value:'%m-%d-%Y',label:'MM-DD-YYYY'},{value:'%d-%m-%Y',label:'DD-MM-YYYY'},{value:'%Y/%m/%d',label:'YYYY/MM/DD'}],f.date_format||'%Y-%m-%d')}</select></label><label>Decimal separator<select id="rule-decimal">${selectOptions(['.',','],f.decimal_separator||'.')}</select></label><label>Thousands separator<select id="rule-thousands">${selectOptions([{value:'',label:'None'},{value:',',label:'Comma'},{value:'.',label:'Dot'},{value:' ',label:'Space'}],f.thousands_separator||'')}</select></label><label>Explicit symbols to strip<input id="rule-symbols" value="${esc(f.strip_symbols||'')}" placeholder="e.g. $"></label><label>Minimum (optional)<input id="rule-min" value="${esc(f.min??'')}"></label><label>Maximum (optional)<input id="rule-max" value="${esc(f.max??'')}"></label></div><p>Dates and numeric formats are explicit. Amounts are never rounded or converted between currencies. Empty fields stay missing; no values are imputed.</p>`, () => {
    Object.assign(f, {
      trim: $('#rule-trim').checked,
      missing: $('#rule-missing').value.split('\n').filter(Boolean),
      allowed: $('#rule-allowed').value.split('\n').filter(Boolean),
      mapping: JSON.parse($('#rule-mapping').value),
      date_format: $('#rule-date').value,
      decimal_separator: $('#rule-decimal').value,
      thousands_separator: $('#rule-thousands').value,
      strip_symbols: $('#rule-symbols').value,
      min: $('#rule-min').value || null,
      max: $('#rule-max').value || null,
      decimal_places: Number($('#rule-places').value),
      precision_mode: $('#rule-precision').value,
      case_insensitive: $('#rule-case').checked,
      date_exact: $('#rule-exact').checked,
      unsigned: $('#rule-unsigned').checked,
      inferred: false
    });
    markDirty();
    renderRecipe();
  });
  $('#modal-body .form-grid').insertAdjacentHTML('beforeend', `<label>Maximum decimal places<input id="rule-places" type="number" min="0" max="12" value="${f.decimal_places ?? 12}"></label><label>Precision policy<select id="rule-precision">${selectOptions([{value:'nonzero',label:'Ignore redundant trailing zeros'},{value:'strict',label:'Reject any extra fractional digit'}], f.precision_mode || 'nonzero')}</select></label><label>Canonical categories ignore case<input id="rule-case" type="checkbox" ${f.case_insensitive?'checked':''}></label><label>Require exact date padding<input id="rule-exact" type="checkbox" ${f.date_exact!==false?'checked':''}></label><label>Reject number sign characters<input id="rule-unsigned" type="checkbox" ${f.unsigned?'checked':''}></label>`);
}

function sourceFormat(ti, si) {
  const recipe = state.recipe,
    table = recipe.tables[ti],
    assignment = table.sources[si];
  recipe.formats ||= {};
  let chosen = assignment.format || '';
  const fields = table.fields.filter(f => ['date', 'decimal', 'integer'].includes(f.type));

  function draw() {
    const settings = recipe.formats[chosen]?.fields || {};
    const rows = fields.map((f, i) => {
      const s = {...f, ...settings[f.name]};
      let controls;
      if (f.type === 'date') {
        controls = `<label>Date format<select data-format-date="${i}">${selectOptions(['%Y-%m-%d','%m/%d/%Y','%d/%m/%Y','%m-%d-%Y','%d-%m-%Y','%Y/%m/%d'],s.date_format||'%Y-%m-%d')}</select></label><label>Exact date padding<input data-format-exact="${i}" type="checkbox" ${s.date_exact!==false?'checked':''}></label>`;
      } else {
        controls = `<label>Decimal separator<select data-format-decimal="${i}">${selectOptions(['.', ','],s.decimal_separator||'.')}</select></label><label>Thousands separator<select data-format-thousands="${i}">${selectOptions([{value:'',label:'None'},{value:',',label:'Comma'},{value:'.',label:'Dot'},{value:' ',label:'Space'}],s.thousands_separator||'')}</select></label><label>Symbols to strip<input data-format-symbols="${i}" value="${esc(s.strip_symbols||'')}"></label>`;
      }
      return `<div class="note"><label><input data-format-enable="${i}" type="checkbox" ${settings[f.name]?'checked':''}> Override ${esc(f.name)} parsing</label><div class="form-grid">${controls}</div></div>`;
    }).join('');
    $('#modal-body').innerHTML = `<p>A named format can be reused across files and refreshes. Editing it changes every source that uses it. Business validation stays on the logical table.</p><div class="form-grid"><label>Reuse format<select id="format-existing">${selectOptions([{value:'',label:'Field defaults'}, ...Object.keys(recipe.formats)],chosen)}</select></label><label>Save format as<input id="format-name" maxlength="100" value="${esc(chosen)}" placeholder="e.g. US monthly export"></label></div>${rows}`;
    $('#format-existing').onchange = e => {
      chosen = e.target.value;
      draw();
    };
  }
  openModal('Reusable source format', '', () => {
    const name = $('#format-name').value.trim();
    if (!name) {
      delete assignment.format;
      markDirty();
      renderRecipe();
      return;
    }
    const overrides = {};
    fields.forEach((f, i) => {
      if (!$(`[data-format-enable="${i}"]`).checked) return;
      overrides[f.name] = f.type === 'date' ? {
        date_format: $(`[data-format-date="${i}"]`).value,
        date_exact: $(`[data-format-exact="${i}"]`).checked
      } : {
        decimal_separator: $(`[data-format-decimal="${i}"]`).value,
        thousands_separator: $(`[data-format-thousands="${i}"]`).value,
        strip_symbols: $(`[data-format-symbols="${i}"]`).value
      };
    });
    recipe.formats[name] = {
      fields: overrides
    };
    assignment.format = name;
    markDirty();
    renderRecipe();
  });
  draw();
}

function tableChecks(ti) {
  const table = state.recipe.tables[ti],
    checks = structuredClone(table.checks || []),
    fields = table.fields.map(f => f.name);

  function draw() {
    $('#modal-body').innerHTML = `<p>These comparisons run after parsing and before duplicate checks. Missing operands are handled by the field's Required rule.</p>${checks.map((c,i)=>`<div class="mapping-row"><select data-check-left="${i}">${selectOptions(fields,c.left)}</select><select data-check-op="${i}">${selectOptions(['>=','<=','>','<','==','!='],c.op)}</select><select data-check-right="${i}">${selectOptions(fields,c.right)}</select><button class="quiet danger" data-remove-check="${i}">Remove</button></div>`).join('')}<button id="add-check" class="secondary small">＋ Add comparison</button>`;
    for (const key of ['left', 'op', 'right']) $$(`[data-check-${key}]`).forEach(b => b.onchange = () => {
      checks[Number(b.dataset['check' + key[0].toUpperCase() + key.slice(1)])][key] = b.value;
    });
    $$('[data-remove-check]').forEach(b => b.onclick = () => {
      checks.splice(Number(b.dataset.removeCheck), 1);
      draw();
    });
    $('#add-check').onclick = () => {
      const dates = table.fields.filter(f => f.type === 'date').map(f => f.name);
      const candidates = dates.length >= 2 ? dates : fields;
      checks.push({
        left: candidates[1] || candidates[0],
        op: '>=',
        right: candidates[0]
      });
      draw();
    };
  }
  openModal('Cross-field checks · ' + table.name, '', () => {
    table.checks = checks;
    markDirty();
    renderRecipe();
  });
  draw();
}

function mapSource(ti, si) {
  const t = state.recipe.tables[ti],
    a = t.sources[si],
    source = state.project.sources.find(s => s.id === a.id);
  openModal('Map · ' + source.filename, `<div class="mapping-editor">${t.fields.map((f,i)=>`<label>${esc(f.name)}<select data-map-field="${i}">${selectOptions(source.headers,a.mapping[f.name]||'',true)}</select></label>`).join('')}</div><label class="form-label"><span><input type="checkbox" id="ignore-unmapped" ${a.ignored_columns?.length?'checked':''}> Explicitly omit source columns left unmapped</span></label><p>Unmapped output fields stay missing. Source columns must be mapped or deliberately omitted.</p>`, () => {
    const mapping = {};
    $$('[data-map-field]').forEach(b => {
      if (b.value) mapping[t.fields[Number(b.dataset.mapField)].name] = b.value;
    });
    const unused = source.headers.filter(h => !Object.values(mapping).includes(h));
    if (unused.length && !$('#ignore-unmapped').checked) throw new Error('Map these columns or explicitly omit them: ' + unused.join(', '));
    a.mapping = mapping;
    a.ignored_columns = unused;
    markDirty();
    renderRecipe();
  });
}
async function saveRecipe() {
  if (!state.recipe) throw new Error('Generate a recipe first.');
  state.recipe = await put(projectPath() + '/recipe', state.recipe);
  state.project.recipe = structuredClone(state.recipe);
  state.dirty = false;
  $('#recipe-status').textContent = 'Recipe saved.';
  $('#recipe-json').value = pretty(state.recipe);
  if (state.run) {
    state.run = await api(projectPath() + '/runs/' + state.run.id);
    renderRun();
  }
}
$('#save-recipe').onclick = guarded(async () => {
  await saveRecipe();
  notify('Recipe saved.');
});
$('#suggest-recipe').onclick = guarded(async () => {
  if (state.recipe) {
    openModal('Regenerate the starting recipe?', '<p>This replaces the current mappings, rules, and combined views with new suggestions from your sources. Existing run snapshots stay available on disk.</p>', async () => {
      state.recipe = await post(projectPath() + '/suggest-recipe');
      state.dirty = false;
      renderRecipe();
    }, 'Regenerate');
  } else {
    state.recipe = await post(projectPath() + '/suggest-recipe');
    renderRecipe();
  }
});
$('#apply-json').onclick = guarded(async () => {
  const recipe = JSON.parse($('#recipe-json').value);
  state.recipe = await put(projectPath() + '/recipe', recipe);
  state.dirty = false;
  renderRecipe();
  notify('Recipe applied and saved.');
});

function downloadJSON(value, name) {
  const url = URL.createObjectURL(new Blob([pretty(value)], {
      type: 'application/json'
    })),
    a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('#download-recipe').onclick = () => downloadJSON(state.recipe, 'recipe.json');
$('#import-recipe').onchange = guarded(async e => {
  if (!e.target.files.length) return;
  const r = JSON.parse(await e.target.files[0].text());
  state.recipe = r;
  markDirty();
  renderRecipe();
  notify('Recipe loaded. Adjust source IDs and mappings if this is a different project, then save.');
});

function renderSuggestions() {
  const suggestions = state.run?.manifest?.relationship_suggestions || [];
  $('#suggestions').innerHTML = suggestions.map((s, i) => `<div class="suggestion"><div><b>${esc(s.base)} → ${esc(s.lookup)}</b> on ${esc(s.field)}<span>${fmt(s.matched)} / ${fmt(s.base_rows)} accepted base records match · ${s.lookup_unique?'Lookup key is unique':'Lookup repeats: aggregate before joining'}</span></div><button class="quiet" data-suggest-join="${i}">Add suggestion ＋</button></div>`).join('');
  $$('[data-suggest-join]').forEach(b => b.onclick = () => {
    const s = suggestions[Number(b.dataset.suggestJoin)];
    const v = state.recipe.views.find(v => v.base === s.base) || {
      name: s.base + '_combined',
      base: s.base,
      joins: []
    };
    if (!state.recipe.views.includes(v)) state.recipe.views.push(v);
    if (v.joins.some(j => j.table === s.lookup)) {
      notify('This lookup is already in the combined view.');
      return;
    }
    v.joins.push({
      table: s.lookup,
      left_on: [s.field],
      right_on: [s.field],
      how: 'left',
      aggregations: [],
      approved: false
    });
    markDirty();
    renderViews();
  });
}
$('#add-view').onclick = () => {
  if (!state.recipe) return notify('Generate a recipe first.', true);
  let name = 'combined_view';
  while (state.recipe.views.some(v => v.name === name)) name += '_2';
  state.recipe.views.push({
    name,
    base: state.recipe.tables[0].name,
    joins: []
  });
  markDirty();
  renderViews();
};

function renderViews() {
  const r = state.recipe;
  if (!r) {
    $('#view-editors').innerHTML = '';
    return;
  }
  $('#view-editors').innerHTML = r.views.map((v, vi) => `<article class="view-editor"><div class="view-fields"><label>View name<input data-view-name="${vi}" value="${esc(v.name)}"></label><label>Base table<select data-view-base="${vi}">${selectOptions(r.tables.map(t=>t.name),v.base)}</select></label></div>${v.joins.map((j,ji)=>`<div class="join-card"><h4>Lookup ${ji+1} ${j.approved?'<span class="approved">Approved</span>':'<span class="muted">Preview required</span>'}</h4><div class="form-grid"><label>Lookup table<select data-join-table="${vi},${ji}">${selectOptions(r.tables.filter(t=>t.name!==v.base).map(t=>t.name),j.table)}</select></label><label>Base keys · comma separated<input data-join-left="${vi},${ji}" value="${esc(j.left_on.join(', '))}" list="base-${vi}"></label><label>Lookup keys · comma separated<input data-join-right="${vi},${ji}" value="${esc(j.right_on.join(', '))}"></label><label>Join type<select data-join-how="${vi},${ji}">${selectOptions([{value:'left',label:'Left · keep all base records'},{value:'inner',label:'Inner · matched base records only'}],j.how||'left')}</select></label></div><p class="muted">Base fields: ${esc(r.tables.find(t=>t.name===v.base)?.fields.map(f=>f.name).join(', '))}<br>Lookup fields: ${esc(r.tables.find(t=>t.name===j.table)?.fields.map(f=>f.name).join(', '))}</p><div class="aggregations">${(j.aggregations||[]).map((a,ai)=>`<div class="mapping-row"><select data-agg-op="${vi},${ji},${ai}">${selectOptions(['count','sum','min','max'],a.op)}</select><select data-agg-field="${vi},${ji},${ai}">${selectOptions(r.tables.find(t=>t.name===j.table)?.fields.map(f=>f.name)||[],a.field)}</select><span>as</span><input aria-label="Aggregation output name" data-agg-name="${vi},${ji},${ai}" value="${esc(a.name)}"><button class="quiet danger" data-remove-agg="${vi},${ji},${ai}">Remove</button></div>`).join('')}</div><div class="actions"><button class="quiet" data-add-agg="${vi},${ji}">＋ Aggregate child records</button><button class="quiet danger" data-remove-join="${vi},${ji}">Remove lookup</button></div></div>`).join('')}<div class="actions"><button class="secondary small" data-add-join="${vi}">＋ Lookup table</button><button class="quiet danger" data-remove-view="${vi}">Remove view</button></div></article>`).join('');
  $$('[data-view-name]').forEach(b => b.onchange = () => {
    r.views[Number(b.dataset.viewName)].name = b.value;
    markDirty();
  });
  $$('[data-view-base]').forEach(b => b.onchange = () => {
    const v = r.views[Number(b.dataset.viewBase)];
    v.base = b.value;
    v.joins = [];
    markDirty();
    renderViews();
  });
  for (const [attr, key] of [
      ['joinTable', 'table'],
      ['joinLeft', 'left_on'],
      ['joinRight', 'right_on'],
      ['joinHow', 'how']
    ]) {
    const selector = '[data-' + attr.replace(/[A-Z]/g, m => '-' + m.toLowerCase()) + ']';
    $$(selector).forEach(b => b.onchange = () => {
      const [vi, ji] = b.dataset[attr].split(',').map(Number), j = r.views[vi].joins[ji];
      j[key] = key.endsWith('_on') ? b.value.split(',').map(x => x.trim()).filter(Boolean) : b.value;
      j.approved = false;
      if (key === 'table') {
        j.right_on = [];
        j.aggregations = [];
      }
      markDirty();
      renderViews();
    });
  }
  for (const [attr, key] of [
      ['aggOp', 'op'],
      ['aggField', 'field'],
      ['aggName', 'name']
    ]) {
    $$('[data-' + attr.replace(/[A-Z]/g, m => '-' + m.toLowerCase()) + ']').forEach(b => b.onchange = () => {
      const [vi, ji, ai] = b.dataset[attr].split(',').map(Number), j = r.views[vi].joins[ji];
      j.aggregations[ai][key] = b.value;
      j.approved = false;
      markDirty();
    });
  }
  $$('[data-add-join]').forEach(b => b.onclick = () => {
    const v = r.views[Number(b.dataset.addJoin)],
      t = r.tables.find(t => t.name !== v.base && !v.joins.some(j => j.table === t.name));
    if (!t) return notify('Add a different logical table before adding another lookup.', true);
    v.joins.push({
      table: t.name,
      left_on: [],
      right_on: [],
      how: 'left',
      aggregations: [],
      approved: false
    });
    markDirty();
    renderViews();
  });
  $$('[data-remove-view]').forEach(b => b.onclick = () => {
    r.views.splice(Number(b.dataset.removeView), 1);
    markDirty();
    renderViews();
  });
  $$('[data-remove-join]').forEach(b => b.onclick = () => {
    const [vi, ji] = b.dataset.removeJoin.split(',').map(Number);
    r.views[vi].joins.splice(ji, 1);
    markDirty();
    renderViews();
  });
  $$('[data-add-agg]').forEach(b => b.onclick = () => {
    const [vi, ji] = b.dataset.addAgg.split(',').map(Number), j = r.views[vi].joins[ji], fields = r.tables.find(t => t.name === j.table).fields, f = fields.find(f => f.type === 'decimal' || f.type === 'integer') || fields[0];
    j.aggregations.push({
      op: ['integer', 'decimal'].includes(f.type) ? 'sum' : 'count',
      field: f.name,
      name: f.name + '_total'
    });
    j.approved = false;
    markDirty();
    renderViews();
  });
  $$('[data-remove-agg]').forEach(b => b.onclick = () => {
    const [vi, ji, ai] = b.dataset.removeAgg.split(',').map(Number), j = r.views[vi].joins[ji];
    j.aggregations.splice(ai, 1);
    j.approved = false;
    markDirty();
    renderViews();
  });
}
$('#preview-joins').onclick = guarded(async () => {
  await saveRecipe();
  const reports = await post(projectPath() + '/join-preview');
  $('#join-report').innerHTML = reports.map(v => `<div class="join-report-card"><b>${esc(v.name)} · ${fmt(v.before)} → ${fmt(v.after)} rows</b>${v.joins.map(j=>`<p class="muted">${esc(j.lookup)}: ${fmt(j.unmatched_base_rows)} unmatched base records · ${fmt(j.unmatched_lookup_rows)} unmatched lookup records · ${esc(j.how)} join${j.aggregated?' · child records aggregated':''}</p>`).join('')}</div>`).join('') || '<p class="note">Add a lookup relationship to preview.</p>';
  $('#approve-joins').hidden = !reports.some(v => v.joins.length);
});
$('#approve-joins').onclick = guarded(async () => {
  for (const v of state.recipe.views)
    for (const j of v.joins) j.approved = true;
  await saveRecipe();
  renderViews();
  $('#approve-joins').hidden = true;
  notify('Relationships approved. Run the project to publish the combined views.');
});
$('#run').onclick = guarded(async () => {
  if (state.dirty) await saveRecipe();
  if (!state.recipe) throw new Error('Add sources and save a recipe first.');
  state.run = await post(projectPath() + '/runs');
  state.project.latest_run = state.run.id;
  renderRun();
  switchTab('review');
  pollRun();
});
async function pollRun() {
  clearTimeout(state.poll);
  const pid = state.project.id,
    rid = state.run.id;
  try {
    const result = await api('/projects/' + pid + '/runs/' + rid);
    if (state.project.id !== pid || state.run.id !== rid) return;
    state.run = result;
    renderRun();
    if (['running', 'queued'].includes(state.run.state)) {
      state.poll = setTimeout(pollRun, 900);
    } else if (state.run.manifest) {
      renderSuggestions();
      renderProfiles();
      await loadRows();
      notify(state.run.manifest.status === 'ready' ? 'Project ready. Your data package is saved.' : 'Run saved. Check the review queue before using the results.');
    }
  } catch (e) {
    notify(e.message, true);
  }
}

function renderProfiles() {
  let host = $('#field-profiles');
  if (!host) {
    host = document.createElement('div');
    host.id = 'field-profiles';
    $('#table-summary').after(host);
  }
  const profiles = state.run?.manifest?.profiles || [];
  host.innerHTML = profiles.length ? `<details class="advanced"><summary>Field quality · missing values, distinct values & outliers</summary><p>Counts cover all parsed records. Invalid conversions can appear as missing. Outliers are diagnostics on accepted values; records are retained.</p><div class="grid-wrap">${grid(['Table','Field','Type','Missing / invalid null','Distinct','Minimum','Maximum','IQR outliers'],profiles.map(f=>[f.table,f.field,f.type,f.missing,f.distinct,f.min??'—',f.max??'—',f.outliers??'—']))}</div><p>Formula results come from stored Excel caches; cache freshness is unverified. The audit retains formula provenance.</p></details>` : '';
}

function renderRun() {
  const run = state.run,
    m = run?.manifest,
    banner = $('#run-banner');
  $('#progress-panel').hidden = !run || !['running', 'queued'].includes(run.state);
  $('#run').disabled = !!run && ['running', 'queued'].includes(run.state);
  if (run) {
    $('#progress-label').textContent = run.message;
    $('#progress-number').textContent = run.progress + '%';
    $('#progress').value = run.progress;
  }
  banner.hidden = !run;
  banner.className = 'banner';
  if (run?.state === 'failed' || run?.state === 'interrupted') {
    banner.classList.add('error');
    banner.textContent = run.message;
  } else if (m) {
    banner.classList.toggle('warn', m.status !== 'ready' || run.stale);
    banner.textContent = (run.stale ? 'Saved result is stale: inputs, recipe, or decisions changed. Rerun. ' : m.status === 'incomplete' ? 'Incomplete project: some files could not be processed. ' : m.status === 'needs_review' ? 'Run saved with records requiring review. ' : 'Project reconciled. ') + `${fmt(m.input_rows)} parsed rows · ${fmt(m.review_rows)} need review.`;
  } else if (run) banner.textContent = run.message;
  $('#review-count').textContent = m ? fmt(m.review_rows) : '—';
  const populations = m ? [
    ['accepted', 'Accepted', m.tables.reduce((a, t) => a + t.accepted, 0)],
    ['review', 'Needs review', m.review_rows],
    ['excluded', 'Excluded', m.tables.reduce((a, t) => a + t.excluded, 0)]
  ] : [];
  let segmentStart = 0;
  const segments = populations.map(([kind,,n]) => {
    const width = m.input_rows ? n / m.input_rows * 1000 : 0;
    const rectangle = n > 0 ? `<rect class="population-segment ${kind}" x="${segmentStart}" y="0" width="${width}" height="12"></rect>` : '';
    segmentStart += width;
    return rectangle;
  }).join('');
  let arcStart = 0;
  const circumference = 2 * Math.PI * 65;
  const arcs = populations.map(([kind,,n]) => {
    const length = m.input_rows ? n / m.input_rows * circumference : 0;
    const circle = n > 0 ? `<circle class="population-arc ${kind}" cx="90" cy="90" r="65" stroke-dasharray="${length} ${circumference}" stroke-dashoffset="${-arcStart}"/>` : '';
    arcStart += length;
    return circle;
  }).join('');
  const acceptedShare = m?.input_rows ? Math.round(populations[0][2] / m.input_rows * 100) + '%' : '—';
  $('#reconciliation').innerHTML = m ? `<figure class="reconciliation-figure"><figcaption><span>RECORD RECONCILIATION</span><span class="recon-count">${fmt(m.input_rows)} parsed records${m.file_issues.length?' · unreadable-file row counts remain unknown':''}</span></figcaption><div class="recon-content"><div class="recon-ring"><svg viewBox="0 0 180 180" aria-hidden="true"><circle class="ring-track" cx="90" cy="90" r="65"/><g class="ring-arcs">${arcs}</g><text class="ring-value" x="90" y="87" text-anchor="middle">${acceptedShare}</text><text class="ring-caption" x="90" y="108" text-anchor="middle">${m.input_rows?'ACCEPTED SHARE':'NO PARSED ROWS'}</text></svg></div><div class="population-legend">${populations.map(([kind,name,n])=>`<span><i class="${kind}" aria-hidden="true"></i><span>${name}</span><b>${fmt(n)}</b></span>`).join('')}</div></div><svg class="population-bar" viewBox="0 0 1000 12" preserveAspectRatio="none" role="img" aria-label="${populations.map(([,name,n])=>`${name}: ${fmt(n)}`).join('; ')}">${segments}</svg></figure>` : '';
  $('#metrics').innerHTML = m ? [
    ['Parsed input', m.input_rows],
    ['Accepted', m.tables.reduce((a, t) => a + t.accepted, 0)],
    ['Needs review', m.review_rows],
    ['Excluded', m.tables.reduce((a, t) => a + t.excluded, 0)]
  ].map(([name, value]) => `<div class="metric ${name==='Needs review'&&value?'warn':''}"><span>${name}</span><strong>${fmt(value)}</strong></div>`).join('') : '<div class="empty">Run the project to inspect the results.</div>';
  $('#file-issues').innerHTML = m?.file_issues.map(i => `<div class="banner error"><b>${esc(i.filename)}</b> · ${esc(i.reason)} · Parsed rows unknown.</div>`).join('') || '';
  $('#table-summary').innerHTML = m ? '<div class="grid-wrap summary-table">' + grid(['Table', 'Input', 'Accepted', 'Review', 'Excluded', 'Exact numeric totals'], m.tables.map(t => [t.name, t.input, t.accepted, t.review, t.excluded, Object.entries(t.totals).map(([f, v]) => f + ': ' + (v.sum ?? 'missing')).join(' · ')])) + '</div>' : '';
  const selection = $('#result-table').value;
  $('#result-table').innerHTML = m ? selectOptions([...m.tables, ...m.views].map(t => t.name), selection) : '';
  if (!m) $('#result-grid').innerHTML = '';
  renderExport();
}
let rowRequest = 0;
async function loadRows() {
  if (!state.run?.manifest) return;
  const table = $('#result-table').value;
  if (!table) return;
  const disposition = $('#disposition').value,
    request = ++rowRequest;
  $('#result-grid').innerHTML = '<div class="empty">Loading records…</div>';
  const data = await api(projectPath() + `/runs/${state.run.id}/rows?table=${encodeURIComponent(table)}&disposition=${disposition}&offset=${state.offset}&limit=50`);
  if (request !== rowRequest) return;
  state.rows = data;
  const business = data.columns.filter(c => !c.startsWith('__')),
    provenance = ['__source_name', '__sheet', '__row'];
  const visible = [...business, ...(['review', 'excluded', 'all'].includes(disposition) ? ['__issues'] : []), ...provenance, ...(disposition === 'all' ? ['__status', '__raw', '__formulas', '__record_id'] : [])];
  const indices = visible.filter(c => data.columns.includes(c)).map(c => [c, data.columns.indexOf(c)]);
  const action = data.columns.includes('__record_id') && !state.run.manifest.views.some(v => v.name === table);
  const labels = {
    __source_name: 'Source file',
    __sheet: 'Sheet',
    __row: 'Source row',
    __issues: 'Review reason',
    __status: 'Disposition',
    __raw: 'Original values',
    __formulas: 'Excel provenance',
    __record_id: 'Record reference'
  };
  $('#result-grid').innerHTML = grid(indices.map(([c]) => labels[c] || c.replaceAll('_', ' ')), data.rows.map(r => indices.map(([, i]) => r[i])), action);
  $('#page-label').textContent = data.total ? `${fmt(data.offset+1)}–${fmt(Math.min(data.offset+50,data.total))} of ${fmt(data.total)}` : '0 records';
  $('#previous-page').disabled = state.offset === 0;
  $('#next-page').disabled = state.offset + 50 >= data.total;
  $$('.review-record').forEach(b => b.onclick = () => reviewRecord(Number(b.dataset.row)));
}
$('#result-table').onchange = guarded(async () => {
  state.offset = 0;
  await loadRows();
});
$('#disposition').onchange = guarded(async () => {
  state.offset = 0;
  await loadRows();
});
$('#previous-page').onclick = guarded(async () => {
  state.offset = Math.max(0, state.offset - 50);
  await loadRows();
});
$('#next-page').onclick = guarded(async () => {
  state.offset += 50;
  await loadRows();
});

function reviewRecord(index) {
  const row = Object.fromEntries(state.rows.columns.map((c, i) => [c, state.rows.rows[index][i]]));
  const raw = JSON.parse(row.__raw),
    t = state.recipe.tables.find(t => t.name === $('#result-table').value),
    decision = state.project.decisions[row.__record_id] || {
      corrections: {},
      exclude: false
    };
  openModal('Review source record', `<p><b>${esc(row.__source_name)}</b> · ${esc(row.__sheet)} · record ${esc(row.__row)}</p><div class="note">${esc(row.__issues||'Accepted record. Corrections still pass through the saved validation rules.')}</div><h3>Original source values</h3><div class="grid-wrap">${grid(['Source heading','Original value'],Object.entries(raw))}</div><h3>Corrections</h3><p>Check the fields you want to override. An empty checked value is an explicit blank correction.</p><div class="mapping-editor">${t.fields.map((f,i)=>`<label><span><input type="checkbox" data-correct-enable="${i}" ${Object.hasOwn(decision.corrections,f.name)?'checked':''}> ${esc(f.name)}</span><input data-correct="${i}" value="${esc(Object.hasOwn(decision.corrections,f.name)?decision.corrections[f.name]:row[f.name]??'')}"></label>`).join('')}</div><p><label><input id="exclude-record" type="checkbox" ${decision.exclude?'checked':''}> Explicitly exclude this source record from accepted output</label></p><p>Decision ID: <code>${esc(row.__record_id)}</code>. Decisions apply to this exact source version and row.</p>`, async () => {
    const corrections = {};
    $$('[data-correct-enable]').forEach(b => {
      if (b.checked) {
        const i = Number(b.dataset.correctEnable);
        corrections[t.fields[i].name] = $(`[data-correct="${i}"]`).value;
      }
    });
    const decision = {
      corrections,
      exclude: $('#exclude-record').checked
    };
    await put(projectPath() + '/decisions/' + row.__record_id, decision);
    state.project.decisions[row.__record_id] = decision;
    state.run.stale = true;
    renderRun();
    notify('Decision saved. Rerun to validate it and update the clean tables.');
  }, 'Save decision');
}

function renderExport() {
  const m = state.run?.manifest;
  if (!m) {
    $('#export-content').innerHTML = '<div class="empty">Complete a run to create an export package.</div>';
    return;
  }
  const prefix = '/api' + projectPath() + '/runs/' + state.run.id;
  $('#export-content').innerHTML = `${state.run.stale?'<div class="banner warn">This package belongs to an earlier configuration. Rerun to include your current changes.</div>':''}<div class="export-hero"><div><h2>${m.status==='ready'?'Your project package is ready.':m.status==='incomplete'?'Partial results · incomplete inputs':'Clean tables, with a review queue.'}</h2><p>Includes complete CSV tables, review and exclusion records, the source audit, exact totals, a data dictionary, your recipe, and a closed database snapshot.</p><span class="muted">Saved run ${esc(state.run.id)} · ${esc(m.finished)}</span></div><a class="primary" href="${prefix}/package">Download package ↓</a></div><div class="export-files">${['Review.xlsx',...Object.keys(m.output_hashes).filter(f=>f!=='Review.xlsx'),'manifest.json'].map(f=>`<a class="export-file" href="${prefix}/files/${encodeURIComponent(f)}"><span class="file-icon">${esc(f.split('.').pop().toUpperCase())}</span><b>${esc(f)}</b><span>↓</span></a>`).join('')}</div><p class="muted">The Excel workbook contains labeled previews of up to 1,000 records per table. CSV exports contain all records. Decimal values remain exact; currency and units follow your source definitions.</p>`;
}
guarded(async () => {
  const s = await api('/session');
  state.token = s.token;
  $('#version').textContent = s.version;
  await refreshProjects();
})();
