'use strict';

// ---------- утилиты ----------
function showError(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.add('visible');
}
function clearError(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = '';
  el.classList.remove('visible');
}
function setBusy(btn, busy) { if (btn) btn.disabled = busy; }
function el(tag, attrs, ...children) {
  const n = document.createElement(tag);
  for (const k in attrs || {}) {
    if (k === 'class') n.className = attrs[k];
    else if (k === 'text') n.textContent = attrs[k];
    else n.setAttribute(k, attrs[k]);
  }
  for (const c of children) if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return n;
}
function opt(value, label) {
  const o = document.createElement('option');
  o.value = value; o.textContent = label; return o;
}
function fillSelect(id, items, getVal, getLabel, placeholder) {
  const sel = document.getElementById(id);
  if (!sel) return;
  sel.innerHTML = '';
  if (placeholder) {
    const p = opt('', placeholder); p.disabled = true; p.selected = true; sel.appendChild(p);
  }
  for (const it of items) sel.appendChild(opt(getVal(it), getLabel(it)));
}

// ---------- локализация ----------
const STATUS_RU = {
  created: 'Создано', sent: 'Отправлено', received: 'Получено',
  active: 'Активен', shipped: 'Отправлен клиенту',
  cancelled: 'Отменён', accepted: 'Принят МП',
};
const OP_RU = {
  receipt: 'Приход',
  transfer_send: 'Отправка перемещения',
  transfer_receive: 'Приёмка перемещения',
  fbs_ship: 'Отправка FBS',
  fbo_send: 'Отправка FBO',
  fbo_accept: 'Приёмка FBO',
};
function statusRu(s) { return STATUS_RU[s] || s; }
function opRu(t) { return OP_RU[t] || t; }
function fmtDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = n => String(n).padStart(2, '0');
    return `${p(d.getDate())}.${p(d.getMonth()+1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch { return iso; }
}

// ---------- API ----------
async function api(path, opts = {}) {
  const headers = { ...(opts.body ? { 'Content-Type': 'application/json' } : {}) };
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Ошибка сервера' }));
    const d = err.detail;
    const msg = (d && typeof d === 'object' && d.message) ? d.message
              : (typeof d === 'string' ? d : JSON.stringify(d));
    throw new Error(msg || ('HTTP ' + res.status));
  }
  return res.status === 204 ? null : res.json();
}

// ---------- селекты (SKU, склады) ----------
let allProducts = [];
let allWarehouses = [];

function fillProductSelects() {
  const skus = allProducts.map(p => p.sku);
  for (const id of ['receiptSku', 'transferSku', 'fbsSku', 'fboSku']) {
    fillSelect(id, skus, x => x, x => x, '— товар —');
  }
}

function fillWarehouseSelects() {
  const active = allWarehouses.filter(w => !w.archived);
  for (const id of ['receiptWh', 'fbsWh', 'fboWh']) {
    fillSelect(id, active, w => w.warehouse_id,
      w => `${w.name} (#${w.warehouse_id})`, '— склад —');
  }
  fillSelect('transferFrom', allWarehouses, w => w.warehouse_id,
    w => `${w.name} (#${w.warehouse_id})` + (w.archived ? ' [архив]' : ''), '— откуда —');
  fillSelect('transferTo', active, w => w.warehouse_id,
    w => `${w.name} (#${w.warehouse_id})`, '— куда —');
  fillSelect('whPatchId', allWarehouses, w => w.warehouse_id,
    w => `${w.name} (#${w.warehouse_id})`, '— склад —');
}

// ---------- ОСТАТКИ ----------
async function loadStock() {
  clearError('stockError');
  const head = document.getElementById('stockHead');
  const body = document.getElementById('stockBody');
  if (!body || !head) return;
  head.innerHTML = '';
  body.innerHTML = '<tr><td class="loading">Загрузка…</td></tr>';
  try {
    const [data, warehouses] = await Promise.all([api('/stock'), api('/warehouses')]);
    allProducts = data;
    allWarehouses = warehouses;
    fillProductSelects();
    fillWarehouseSelects();

    const ff = warehouses;

    head.innerHTML = '';
    const hr = el('tr');
    hr.appendChild(el('th', { text: 'Артикул' }));
    hr.appendChild(el('th', { text: 'Товар' }));
    for (const w of ff) {
      hr.appendChild(el('th', {}, w.name,
        w.archived ? el('span', { class: 'badge bg-secondary ms-1', text: 'архив' }) : null));
    }
    hr.appendChild(el('th', { text: 'В пути' }));
    hr.appendChild(el('th', { text: 'У МП' }));
    head.appendChild(hr);

    if (!data.length) {
      body.innerHTML = '<tr><td colspan="' + (ff.length + 4) + '" class="loading">Нет товаров</td></tr>';
      return;
    }

    body.innerHTML = '';
    for (const p of data) {
      const tr = el('tr');
      tr.appendChild(el('td', {}, el('strong', { text: p.sku })));
      tr.appendChild(el('td', { text: p.name || '' }));

      for (const w of ff) {
        const wh = (p.warehouses || []).find(x => x.warehouse_id === w.warehouse_id);
        if (!wh) {
          tr.appendChild(el('td', { class: 'text-secondary', text: '—' }));
          continue;
        }
        const onHand = wh.on_hand ?? 0;
        const reserved = wh.reserved ?? 0;
        const available = wh.available ?? 0;
        const td = el('td', wh.archived ? { class: 'archived-row' } : {});
        td.appendChild(el('div', { text: String(onHand) }));
        if (reserved > 0) {
          td.appendChild(el('small', { class: 'text-secondary', text: 'рез. ' + reserved }));
          td.appendChild(el('div', {},
            el('small', {
              class: available > 0 ? 'text-success' : 'text-secondary',
              text: 'дост. ' + available,
            })));
        }
        tr.appendChild(td);
      }

      const t = p.in_transit || {};
      const parts = [];
      if (t.between_ff) parts.push('ФФ: ' + t.between_ff);
      if (t.to_marketplace_fbo) parts.push('МП: ' + t.to_marketplace_fbo);
      if (t.to_client_fbs) parts.push('Клиент: ' + t.to_client_fbs);
      tr.appendChild(el('td', { class: 'small' }, parts.length ? parts.join(' · ') : '—'));

      const at = p.at_marketplace ?? 0;
      tr.appendChild(el('td', { text: at ? String(at) : '—' }));

      body.appendChild(tr);
    }
  } catch (e) {
    body.innerHTML = '';
    showError('stockError', e.message);
  }
}

// ---------- СКЛАДЫ ----------
async function loadWarehouses() {
  clearError('warehousesError');
  const body = document.getElementById('warehousesBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="5" class="loading">Загрузка…</td></tr>';
  try {
    const data = await api('/warehouses');
    allWarehouses = data;
    fillWarehouseSelects();
    body.innerHTML = '';
    if (!data.length) {
      body.innerHTML = '<tr><td colspan="5" class="loading">Нет складов</td></tr>';
      return;
    }
    for (const w of data) {
      const tr = el('tr', w.archived ? { class: 'archived-row' } : {});
      tr.appendChild(el('td', { text: String(w.warehouse_id ?? w.id ?? '') }));
      tr.appendChild(el('td', { text: w.name }));
      tr.appendChild(el('td', { text: String(w.on_hand ?? w.total_on_hand ?? 0) }));
      tr.appendChild(el('td', {},
        w.archived
          ? el('span', { class: 'badge bg-secondary', text: 'архив' })
          : el('span', { class: 'badge bg-success', text: 'активен' })));
      const actions = el('td', { class: 'ops-actions' });
      if (!w.archived) {
        const btn = el('button', { class: 'btn btn-outline-danger btn-sm', text: 'Архивировать' });
        btn.addEventListener('click', () => archiveWarehouse(w.warehouse_id ?? w.id));
        actions.appendChild(btn);
      } else {
        const btn = el('button', { class: 'btn btn-outline-success btn-sm', text: 'Разархивировать' });
        btn.addEventListener('click', () => unarchiveWarehouse(w.warehouse_id ?? w.id));
        actions.appendChild(btn);
      }
      tr.appendChild(actions);
      body.appendChild(tr);
    }
  } catch (e) {
    body.innerHTML = '';
    showError('warehousesError', e.message);
  }
}

async function archiveWarehouse(id) {
  if (!confirm('Архивировать склад #' + id + '? Остатки и история сохранятся.')) return;
  try {
    await api('/warehouses/' + id, { method: 'DELETE' });
    await loadWarehouses();
    await loadStock();
  } catch (e) { showError('warehousesError', e.message); }
}

async function unarchiveWarehouse(id) {
  if (!confirm('Вернуть склад #' + id + ' из архива?')) return;
  try {
    await api('/warehouses/' + id + '/unarchive', { method: 'POST' });
    await loadWarehouses();
    await loadStock();
  } catch (e) { showError('warehousesError', e.message); }
}

// ---------- ФОРМЫ ----------
async function postJSON(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body) });
}

function bindForm(formId, errorId, handler) {
  const form = document.getElementById(formId);
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearError(errorId);
    const btn = form.querySelector('button');
    setBusy(btn, true);
    try { await handler(); e.target.reset(); }
    catch (err) { showError(errorId, err.message); }
    finally { setBusy(btn, false); }
  });
}

bindForm('receiptForm', 'receiptError', async () => {
  await postJSON('/receipts', {
    sku: document.getElementById('receiptSku').value,
    warehouse_id: parseInt(document.getElementById('receiptWh').value, 10),
    qty: parseInt(document.getElementById('receiptQty').value, 10),
  });
  await loadStock();
});

bindForm('transferForm', 'transferError', async () => {
  await postJSON('/transfers', {
    sku: document.getElementById('transferSku').value,
    from_warehouse_id: parseInt(document.getElementById('transferFrom').value, 10),
    to_warehouse_id: parseInt(document.getElementById('transferTo').value, 10),
    qty: parseInt(document.getElementById('transferQty').value, 10),
  });
  await loadStock();
  await loadTransfers();
});

bindForm('fbsForm', 'fbsError', async () => {
  await postJSON('/fbs/orders', {
    sku: document.getElementById('fbsSku').value,
    warehouse_id: parseInt(document.getElementById('fbsWh').value, 10),
    qty: parseInt(document.getElementById('fbsQty').value, 10),
  });
  await loadStock();
  await loadFbs();
});

bindForm('fboForm', 'fboError', async () => {
  await postJSON('/fbo', {
    sku: document.getElementById('fboSku').value,
    warehouse_id: parseInt(document.getElementById('fboWh').value, 10),
    qty: parseInt(document.getElementById('fboQty').value, 10),
  });
  await loadStock();
  await loadFbo();
});

bindForm('whForm', 'whError', async () => {
  await postJSON('/warehouses', { name: document.getElementById('whName').value });
  await loadWarehouses();
  await loadStock();
});

bindForm('whPatchForm', 'whPatchError', async () => {
  const id = document.getElementById('whPatchId').value;
  await api('/warehouses/' + id, {
    method: 'PATCH',
    body: JSON.stringify({ name: document.getElementById('whPatchName').value }),
  });
  await loadWarehouses();
  await loadStock();
});

// ---------- ОПЕРАЦИИ ----------
function mkOpBtn(label, handler, cls) {
  const b = el('button', { class: 'btn btn-sm ' + (cls || 'btn-outline-primary'), text: label });
  b.addEventListener('click', async () => {
    setBusy(b, true);
    clearError('opsError');
    try { await handler(); }
    catch (e) { showError('opsError', e.message); }
    finally { setBusy(b, false); }
  });
  return b;
}

function opsTable(headers, rows) {
  const table = el('table', { class: 'table table-vcenter' });
  table.appendChild(el('thead', {}, el('tr', {}, ...headers.map(h => el('th', { text: h })))));
  const tbody = el('tbody');
  if (!rows.length) {
    tbody.appendChild(el('tr', {}, el('td', { colspan: String(headers.length), class: 'loading', text: 'Пока нет данных' })));
  } else {
    for (const r of rows) tbody.appendChild(r);
  }
  table.appendChild(tbody);
  return table;
}

async function loadTransfers() {
  const div = document.getElementById('opsList');
  if (!div) return;
  div.innerHTML = '<h3>Перемещения</h3><div class="loading">Загрузка…</div>';
  clearError('opsError');
  try {
    const data = await api('/transfers');
    const rows = data.map(t => {
      const tr = el('tr');
      tr.appendChild(el('td', { text: String(t.id) }));
      tr.appendChild(el('td', { text: t.sku }));
      tr.appendChild(el('td', { text: (t.from_warehouse ?? t.from_warehouse_id) + ' → ' + (t.to_warehouse ?? t.to_warehouse_id) }));
      tr.appendChild(el('td', { text: String(t.qty) }));
      tr.appendChild(el('td', { text: statusRu(t.status) }));
      const actions = el('td', { class: 'ops-actions' });
      if (t.status === 'created') actions.appendChild(mkOpBtn('Отправить', () => sendTransfer(t.id)));
      if (t.status === 'sent')    actions.appendChild(mkOpBtn('Принять',   () => receiveTransfer(t.id)));
      if (!actions.childNodes.length) actions.appendChild(document.createTextNode('—'));
      tr.appendChild(actions);
      return tr;
    });
    div.innerHTML = '<h3>Перемещения</h3>';
    div.appendChild(opsTable(['ID','SKU','Откуда → Куда','Кол-во','Статус','Действия'], rows));
  } catch (e) {
    div.innerHTML = '<h3>Перемещения</h3>';
    showError('opsError', e.message);
  }
}
async function sendTransfer(id) {
  await api('/transfers/' + id + '/send', { method: 'POST' });
  await loadStock(); await loadTransfers();
}
async function receiveTransfer(id) {
  await api('/transfers/' + id + '/receive', { method: 'POST' });
  await loadStock(); await loadTransfers();
}

async function loadFbs() {
  const div = document.getElementById('opsList');
  if (!div) return;
  div.innerHTML = '<h3>FBS-заказы</h3><div class="loading">Загрузка…</div>';
  clearError('opsError');
  try {
    const data = await api('/fbs/orders');
    const rows = data.map(o => {
      const tr = el('tr');
      tr.appendChild(el('td', { text: String(o.id) }));
      tr.appendChild(el('td', { text: o.sku }));
      tr.appendChild(el('td', { text: String(o.warehouse ?? o.warehouse_id) }));
      tr.appendChild(el('td', { text: String(o.qty) }));
      tr.appendChild(el('td', { text: statusRu(o.status) }));
      const actions = el('td', { class: 'ops-actions' });
      if (o.status === 'active') {
        actions.appendChild(mkOpBtn('Отправить', () => shipFbs(o.id)));
        actions.appendChild(mkOpBtn('Отменить', () => cancelFbs(o.id), 'btn-outline-danger'));
      }
      if (!actions.childNodes.length) actions.appendChild(document.createTextNode('—'));
      tr.appendChild(actions);
      return tr;
    });
    div.innerHTML = '<h3>FBS-заказы</h3>';
    div.appendChild(opsTable(['ID','SKU','Склад','Кол-во','Статус','Действия'], rows));
  } catch (e) {
    div.innerHTML = '<h3>FBS-заказы</h3>';
    showError('opsError', e.message);
  }
}
async function shipFbs(id) {
  await api('/fbs/orders/' + id + '/ship', { method: 'POST' });
  await loadStock(); await loadFbs();
}
async function cancelFbs(id) {
  await api('/fbs/orders/' + id + '/cancel', { method: 'POST' });
  await loadStock(); await loadFbs();
}

async function loadFbo() {
  const div = document.getElementById('opsList');
  if (!div) return;
  div.innerHTML = '<h3>FBO-отправки</h3><div class="loading">Загрузка…</div>';
  clearError('opsError');
  try {
    const data = await api('/fbo');
    const rows = data.map(o => {
      const tr = el('tr');
      tr.appendChild(el('td', { text: String(o.id) }));
      tr.appendChild(el('td', { text: o.sku }));
      tr.appendChild(el('td', { text: String(o.warehouse ?? o.warehouse_id) }));
      tr.appendChild(el('td', { text: String(o.qty) }));
      tr.appendChild(el('td', { text: statusRu(o.status) }));
      const actions = el('td', { class: 'ops-actions' });
      if (o.status === 'created') actions.appendChild(mkOpBtn('Отправить', () => sendFbo(o.id)));
      if (o.status === 'sent')    actions.appendChild(mkOpBtn('Принять',   () => acceptFbo(o.id)));
      if (!actions.childNodes.length) actions.appendChild(document.createTextNode('—'));
      tr.appendChild(actions);
      return tr;
    });
    div.innerHTML = '<h3>FBO-отправки</h3>';
    div.appendChild(opsTable(['ID','SKU','Склад','Кол-во','Статус','Действия'], rows));
  } catch (e) {
    div.innerHTML = '<h3>FBO-отправки</h3>';
    showError('opsError', e.message);
  }
}
async function sendFbo(id) {
  await api('/fbo/' + id + '/send', { method: 'POST' });
  await loadStock(); await loadFbo();
}
async function acceptFbo(id) {
  await api('/fbo/' + id + '/accept', { method: 'POST' });
  await loadStock(); await loadFbo();
}

// ---------- ИСТОРИЯ ----------
async function loadHistory() {
  clearError('historyError');
  const body = document.getElementById('historyBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="6" class="loading">Загрузка…</td></tr>';
  try {
    const data = await api('/history?limit=200');
    body.innerHTML = '';
    if (!data.length) {
      body.innerHTML = '<tr><td colspan="6" class="loading">Пока нет движений</td></tr>';
      return;
    }
    for (const h of data) {
      const tr = el('tr');
      tr.appendChild(el('td', { text: fmtDate(h.created_at) }));
      tr.appendChild(el('td', { text: h.sku || '' }));
      tr.appendChild(el('td', { text: String(h.qty ?? '') }));
      tr.appendChild(el('td', { text: h.from || '—' }));
      tr.appendChild(el('td', { text: h.to || '—' }));
      tr.appendChild(el('td', { text: opRu(h.op_type) }));
      body.appendChild(tr);
    }
  } catch (e) {
    body.innerHTML = '';
    showError('historyError', e.message);
  }
}

// ---------- СТАРТ ----------
(function init() {
  const active = document.body.dataset.activeTab || 'stock';
  if (active === 'warehouses') {
    loadWarehouses();
    return;
  }
  if (active === 'history') {
    loadHistory();
    return;
  }
  loadStock();
  loadWarehouses();
  loadTransfers();
})();
