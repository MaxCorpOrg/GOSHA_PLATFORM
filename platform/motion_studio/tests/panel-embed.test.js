import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { mountPanelEmbed } from '../src/panel-embed.js';

function editor(embedded = true) {
  const messages = [], requests = [];
  let handler, state = 'disconnected';
  const parent = { postMessage: (data, origin) => messages.push({ data, origin }) };
  const target = { parent, addEventListener: (_type, fn) => { handler = fn; } };
  const integration = mountPanelEmbed({ target, embedded, readState: () => state,
    disconnect() { requests.push('disconnect'); state = 'disconnected'; } });
  return { target, parent, messages, requests, integration,
    setState(value) { state = value; integration.publish(); },
    request(action, origin = 'https://gosha.website', source = parent) {
      handler?.({ origin, source, data: { type: 'gosha-motion-studio:request', action } });
    } };
}

test('embedding publishes only connection state and never opens or arms the robot', () => {
  const ui = editor();
  assert.deepEqual(ui.messages, [{ data: { type: 'gosha-motion-studio:status', state: 'disconnected' }, origin: 'https://gosha.website' }]);
  ui.setState('armed');
  ui.integration.publish();
  assert.equal(ui.messages.length, 2, 'telemetry updates must not repeatedly redraw the host panel');
  ui.request('arm');
  ui.request('pose');
  assert.deepEqual(ui.requests, []);
});

test('only the known parent can request orderly STOP and disconnect', () => {
  const ui = editor();
  ui.setState('armed');
  ui.request('disconnect', 'https://foreign.test');
  ui.request('disconnect', 'https://gosha.website', {});
  assert.deepEqual(ui.requests, []);
  ui.request('disconnect');
  assert.deepEqual(ui.requests, ['disconnect']);
  assert.equal(ui.messages.at(-1).data.state, 'disconnected');
  const standalone = editor(false);
  assert.deepEqual(standalone.messages, []);
  standalone.request('disconnect');
  assert.deepEqual(standalone.requests, []);
});

const html = readFileSync(new URL('../../panel_index.html', import.meta.url), 'utf8');
const start = html.indexOf('    // Motion Studio lives outside');
const end = html.indexOf('    function renderRobotWorkspace()', start);
assert.ok(start > 0 && end > start);

function panel(robotId = 'gosha-main') {
  const posts = [], navigations = [], timers = [], paints = [];
  let message;
  const frame = { hidden: false, contentWindow: { postMessage: (data, origin) => posts.push({ data, origin }) } };
  Object.defineProperty(frame, 'src', { set: value => navigations.push(value) });
  const nodes = { robotMotionStudioFrame: frame, robotMotionStudioHost: { hidden: true },
    robotMotionStudioState: {}, robotMotionStudioHelp: {}, robotMotionStudioRetry: {} };
  const context = vm.createContext({ document: { getElementById: id => nodes[id] },
    window: { location: { origin: 'https://gosha.website' }, addEventListener: (_type, handler) => { message = handler; } },
    clearTimeout() {}, setTimeout(fn) { timers.push(fn); return timers.length; },
    robotWorkspaceId: robotId, paintRobotMotionView(id) { paints.push(id); } });
  vm.runInContext(html.slice(start, end), context);
  return { context, nodes, frame, posts, navigations, timers, paints,
    get studio() { return vm.runInContext('robotStudio', context); },
    status(state, origin = 'https://gosha.website', source = frame.contentWindow) {
      message({ origin, source, data: { type: 'gosha-motion-studio:status', state } });
    } };
}

test('ordinary redraw preserves the same editor frame and switching sections requests STOP', () => {
  const ui = panel();
  ui.context.syncRobotMotionStudio('gosha-main', 'motion');
  ui.status('disconnected');
  for (let i = 0; i < 20; i++) ui.context.syncRobotMotionStudio('gosha-main', 'motion');
  assert.equal(ui.navigations.length, 1);
  assert.deepEqual(ui.posts, []);
  ui.status('armed');
  ui.context.syncRobotMotionStudio('gosha-main', 'personality');
  assert.equal(ui.posts.at(-1).data.action, 'disconnect');
  assert.equal(ui.nodes.robotMotionStudioHost.hidden, true);
  const stopRequests = ui.posts.length;
  ui.status('stopping');
  ui.status('stopping');
  assert.equal(ui.posts.length, stopRequests, 'STOP acknowledgement updates must not create a message loop or resend STOP');
  ui.status('disconnected');
  ui.context.syncRobotMotionStudio('gosha-main', 'motion');
  assert.equal(ui.navigations.length, 1, 'returning to the editor must preserve drafts and history');
  ui.context.syncRobotMotionStudio('other-robot', 'motion');
  assert.equal(ui.nodes.robotMotionStudioHost.hidden, true);
  assert.equal(ui.navigations.length, 1);
  assert.ok(html.indexOf('id="robotMotionStudioFrame"') < html.indexOf('id="robotWorkspaceBody"'), 'frame must be outside the replaced workspace body');
});

test('spoofed status cannot unlock ordinary launch and connection failure permits explicit retry', () => {
  const ui = panel();
  ui.context.syncRobotMotionStudio('gosha-main', 'motion');
  ui.timers[0]();
  assert.equal(ui.nodes.robotMotionStudioRetry.hidden, false);
  ui.context.retryRobotMotionStudio();
  assert.equal(ui.navigations.length, 2);
  ui.status('armed');
  assert.equal(ui.context.isRobotStudioConnected('gosha-main'), true);
  assert.equal(ui.context.isRobotStudioConnected('gosha-test'), true);
  assert.equal(ui.context.isRobotStudioConnected('unknown'), true);
  ui.status('disconnected', 'https://foreign.test');
  ui.status('disconnected', 'https://gosha.website', {});
  ui.status('__proto__');
  assert.equal(ui.context.isRobotStudioConnected('gosha-main'), true);
  ui.context.retryRobotMotionStudio();
  assert.equal(ui.navigations.length, 2, 'reload must not destroy an active USB session');
  ui.status('disconnected');
  assert.equal(ui.context.isRobotStudioConnected('gosha-main'), false);
});

test('only the main and test gosha-v1 cards show and load the same editor', () => {
  for (const id of ['gosha-main', 'gosha-test']) {
    const ui = panel(id);
    assert.equal(ui.context.supportsRobotMotionStudio(id), true);
    assert.match(ui.context.robotMotionStudioHint(id), /Создайте движение/);
    ui.context.syncRobotMotionStudio(id, 'overview');
    assert.equal(ui.navigations.length, 0);
    ui.context.syncRobotMotionStudio(id, 'motion');
    assert.equal(ui.nodes.robotMotionStudioHost.hidden, false);
    assert.equal(ui.navigations.length, 1);
    ui.status('disconnected');
    for (let i = 0; i < 20; i++) ui.context.syncRobotMotionStudio(id, 'motion');
    assert.equal(ui.navigations.length, 1);
    assert.equal(ui.studio.robotId, id);
    assert.equal(ui.paints.at(-1), id);
  }
  for (const id of ['unknown', 'gosha-test-2', 'GOSHA-TEST', '', null]) {
    const ui = panel(id);
    assert.equal(ui.context.supportsRobotMotionStudio(id), false);
    assert.match(ui.context.robotMotionStudioHint(id), /пока не подключён/);
    ui.context.syncRobotMotionStudio(id, 'motion');
    ui.context.retryRobotMotionStudio();
    assert.equal(ui.nodes.robotMotionStudioHost.hidden, true);
    assert.equal(ui.navigations.length, 0);
    assert.equal(ui.context.isRobotStudioConnected(id), false);
  }
});

test('card changes stop the shared USB session before either card can launch ordinary motion', () => {
  for (const [from, to] of [['gosha-main', 'gosha-test'], ['gosha-test', 'gosha-main']]) {
    const ui = panel(from);
    ui.context.syncRobotMotionStudio(from, 'motion');
    ui.status('armed');
    ui.context.robotWorkspaceId = to;
    ui.context.syncRobotMotionStudio(to, 'motion');
    assert.equal(ui.posts.length, 1);
    assert.equal(ui.posts[0].data.action, 'disconnect');
    assert.equal(ui.studio.robotId, to);
    assert.equal(ui.studio.disconnectPending, true);
    assert.equal(ui.frame.inert, true);
    assert.equal(ui.context.isRobotStudioConnected(from), true);
    assert.equal(ui.context.isRobotStudioConnected(to), true);
    ui.status('stopping'); ui.status('stopping');
    assert.equal(ui.posts.length, 1, 'no STOP loop while awaiting acknowledgement');
    // A delayed active status from the same iframe still cannot resume control.
    ui.status('ready');
    assert.equal(ui.posts.length, 2);
    assert.equal(ui.frame.inert, true);
    ui.status('disconnected');
    assert.equal(ui.frame.inert, false);
    assert.equal(ui.studio.disconnectPending, false);
    assert.equal(ui.context.isRobotStudioConnected(from), false);
    assert.equal(ui.context.isRobotStudioConnected(to), false);
    ui.context.syncRobotMotionStudio(to, 'motion');
    assert.equal(ui.navigations.length, 1, 'card switch preserves the existing editor mount');
    assert.ok(ui.posts.every(post => post.data.action === 'disconnect'));
  }
});

test('leaving the supported card hides Studio and cannot reload a connected session', () => {
  const ui = panel('gosha-test');
  ui.context.syncRobotMotionStudio('gosha-test', 'motion'); ui.status('armed');
  ui.context.robotWorkspaceId = 'unknown';
  ui.context.syncRobotMotionStudio('unknown', 'motion');
  assert.equal(ui.nodes.robotMotionStudioHost.hidden, true);
  assert.equal(ui.posts.length, 1);
  ui.context.retryRobotMotionStudio();
  assert.equal(ui.navigations.length, 1);
  ui.status('disconnected', 'https://foreign.test');
  ui.status('disconnected', 'https://gosha.website', {});
  assert.equal(ui.context.isRobotStudioConnected('gosha-main'), true);
  assert.equal(ui.context.isRobotStudioConnected('gosha-test'), true);
  ui.status('disconnected');
  assert.equal(ui.context.isRobotStudioConnected('gosha-test'), false);
  assert.equal(ui.context.isRobotStudioConnected('unknown'), false);
  ui.context.retryRobotMotionStudio();
  assert.equal(ui.navigations.length, 1, 'unknown card cannot explicitly load the editor either');
});

test('fault during a card switch keeps ordinary play blocked but allows explicit disconnect and retry', () => {
  const ui = panel('gosha-test');
  ui.context.syncRobotMotionStudio('gosha-test', 'motion'); ui.status('armed');
  ui.context.robotWorkspaceId = 'gosha-main';
  ui.context.syncRobotMotionStudio('gosha-main', 'motion'); ui.status('fault');
  assert.equal(ui.frame.inert, false, 'the editor error and disconnect controls remain accessible');
  for (const id of ['gosha-main', 'gosha-test', 'unknown'])
    assert.equal(ui.context.isRobotStudioConnected(id), true);
  ui.context.retryRobotMotionStudio();
  assert.equal(ui.navigations.length, 1);
  ui.context.requestRobotStudioDisconnect(); ui.status('disconnected');
  assert.equal(ui.frame.inert, false);
  assert.equal(ui.context.isRobotStudioConnected('gosha-main'), false);
  ui.context.retryRobotMotionStudio();
  assert.equal(ui.navigations.length, 2, 'only an explicit retry after confirmed disconnect reloads the editor');
});
