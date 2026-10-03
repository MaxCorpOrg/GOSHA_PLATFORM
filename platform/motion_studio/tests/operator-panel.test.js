import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../../panel_index.html', import.meta.url), 'utf8');
const start = html.indexOf('    let robotMotionGeneration = 0;');
const end = html.indexOf('    async function serviceAction(', start);
assert.ok(start > 0 && end > start);
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const movements = [
  { motion_id: 'builtin/wave', name: 'Приветствие', source: 'builtin' },
  { motion_id: 'stored/small-greeting', name: 'Малое приветствие', source: 'stored' },
];

function panel({ apiGet, apiPost } = {}) {
  let nodes = {};
  const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const context = vm.createContext({
    document: { getElementById: id => nodes[id] || null },
    robotWorkspaceId: 'gosha-main', escapeHtml,
    operatorUrl: path => path, apiGet, apiPost,
    notify() {}, setTimeout, Date,
  });
  vm.runInContext(html.slice(start, end), context);
  function selectElement(fragment) {
    const node = { _html: '', value: '' };
    Object.defineProperty(node, 'innerHTML', {
      get() { return this._html; },
      set(value) {
        this._html = value;
        const options = Array.from(value.matchAll(/<option value="([^"]*)"([^>]*)>/g));
        this.value = (options.find(option => option[2].includes('selected')) || options[0])?.[1] || '';
      },
    });
    node.innerHTML = fragment;
    return node;
  }
  function render() {
    const fragment = context.renderRobotMotionPanel('gosha-main');
    nodes = {
      robotMotionStatus: { textContent: fragment.match(/id="robotMotionStatus">([^<]*)</)[1] },
      robotMotionSelect: selectElement(fragment.match(/<select[^>]*>(.*?)<\/select>/s)[1]),
      robotMotionPlay: { disabled: /id="robotMotionPlay"[^>]*\bdisabled\b/.test(fragment) },
    };
    return nodes;
  }
  render();
  return { context, render, get nodes() { return nodes; } };
}

test('ordinary catalog survives runtime redraw during the pending response and afterward', async () => {
  const response = deferred();
  const ui = panel({ apiGet: () => response.promise });
  const request = ui.context.loadRobotMovements('gosha-main');
  const staleNode = ui.nodes.robotMotionStatus;
  ui.render();
  assert.match(ui.nodes.robotMotionStatus.textContent, /Читаю каталог/);
  response.resolve({ ok: true, data: { movements } });
  await request;
  assert.notEqual(ui.nodes.robotMotionStatus, staleNode);
  assert.match(ui.nodes.robotMotionStatus.textContent, /Доступно движений: 2/);
  ui.context.rememberRobotMovement('gosha-main', 'stored/small-greeting');
  ui.render();
  assert.equal(ui.nodes.robotMotionSelect.value, 'stored/small-greeting');
  assert.match(ui.nodes.robotMotionSelect.innerHTML, /Малое приветствие/);
});

test('voice disconnection error survives a runtime redraw and no actuator call is sent', async () => {
  let writes = 0;
  const ui = panel({ apiGet: async () => ({ ok: false, error: 'robot_voice_not_connected' }),
    apiPost: () => { writes++; } });
  await ui.context.loadRobotMovements('gosha-main');
  ui.render();
  assert.match(ui.nodes.robotMotionStatus.textContent, /Включите голос/);
  assert.equal(writes, 0);
});

test('redraw cannot reopen pending play; STOP cancels its later response', async () => {
  const pending = deferred();
  const calls = [];
  const ui = panel({ apiGet: async () => ({ ok: true, data: { movements } }),
    apiPost: async (_url, payload) => {
      calls.push(payload.action);
      return payload.action === 'play' ? pending.promise : { ok: true, data: { status: 'stopped' } };
    } });
  await ui.context.loadRobotMovements('gosha-main');
  const play = ui.context.playRobotMovement('gosha-main', ui.nodes.robotMotionPlay);
  ui.render();
  assert.equal(ui.nodes.robotMotionPlay.disabled, true);
  await ui.context.playRobotMovement('gosha-main', ui.nodes.robotMotionPlay);
  await ui.context.stopRobotMovement('gosha-main');
  ui.render();
  assert.equal(ui.nodes.robotMotionPlay.disabled, false);
  assert.match(ui.nodes.robotMotionStatus.textContent, /остановлено/);
  pending.resolve({ ok: true, data: { status: 'in_progress' } });
  await play;
  assert.deepEqual(calls, ['play', 'stop']);
  assert.match(ui.nodes.robotMotionStatus.textContent, /остановлено/);
});

test('unknown play result stays blocked after redraw and never retries play', async () => {
  let writes = 0;
  const ui = panel({ apiGet: async () => ({ ok: true, data: { movements } }),
    apiPost: async () => { writes++; throw new Error('connection lost'); } });
  await ui.context.loadRobotMovements('gosha-main');
  await ui.context.playRobotMovement('gosha-main', ui.nodes.robotMotionPlay);
  ui.render();
  assert.equal(ui.nodes.robotMotionPlay.disabled, true);
  assert.match(ui.nodes.robotMotionStatus.textContent, /не подтверждено/);
  await ui.context.playRobotMovement('gosha-main', ui.nodes.robotMotionPlay);
  assert.equal(writes, 1);
});
