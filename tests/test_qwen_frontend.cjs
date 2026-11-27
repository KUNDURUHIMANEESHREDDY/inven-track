'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const studio = fs.readFileSync(path.join(__dirname, '../frontend/ai_models.js'), 'utf8');
const runtime = fs.readFileSync(path.join(__dirname, '../frontend/qwen_local.js'), 'utf8');
const page = fs.readFileSync(path.join(__dirname, '../frontend/digital_inventory_and_sales_management_system.html'), 'utf8');

test('Qwen is visible as an opt-in assistant model and cloud provider UI remains', () => {
    assert.match(page, /id="ai-qwen-download"/);
    assert.match(page, /id="ai-qwen-status"/);
    assert.match(page, /src="\/qwen_local.js"/);
    assert.match(page, /window\.invenQwen\.generate\(context\.instruction, query\)/);
    assert.match(page, /fetch\('\/api\/ai\/chat'/);
    assert.match(studio, /Qwen3\.5-0\.8B · On this device/);
});

test('selecting local Qwen preserves assigned cloud model and is scoped per owner and bot', async () => {
    const store = new Map();
    const elements = new Map();
    const element = (id) => {
        if (!elements.has(id)) elements.set(id, {
            value: '', textContent: '', innerHTML: '', classList: { add() {}, remove() {}, toggle() {} }
        });
        return elements.get(id);
    };
    const calls = [];
    const config = { ownerId: 17, models: [{
        id: 42, name: 'Cloud AI', provider: 'openai', protocol: 'openai',
        endpoint: 'https://api.openai.com/v1', modelId: 'gpt-test', hasKey: true
    }], bots: ['staff', 'sales', 'inventory'].map(slug => ({
        slug, name: slug, modelId: slug === 'staff' ? 42 : null, extraInstruction: ''
    })), providers: { openai: { label: 'OpenAI', protocol: 'openai', endpoint: 'https://api.openai.com/v1', model: 'gpt-test' } } };
    const window = { localStorage: {
        getItem: k => store.get(k) || null,
        setItem: (k, v) => store.set(k, v),
        removeItem: k => store.delete(k)
    } };
    const sandbox = { window, URL, document: {
        getElementById: element, addEventListener() {}
    }, fetch: async (url, opts) => {
        if (url === '/api/ai/config') return { ok: true, json: async () => config };
        calls.push({ url, payload: JSON.parse(opts.body) });
        return { ok: true, json: async () => ({}) };
    } };
    vm.runInNewContext(studio, sandbox);
    await window.aiLoadSettings();
    element('ai-bot-model-staff').value = 'browser-qwen';
    element('ai-bot-instructions-staff').value = 'Read-only help';
    await window.aiSaveBot('staff');
    assert.equal(calls[0].url, '/api/ai/bots/staff');
    assert.equal(calls[0].payload.modelId, 42);
    assert.equal(calls[0].payload.extraInstruction, 'Read-only help');
    assert.equal(store.get('inven:qwen:v1:17:staff'), '1');
    assert.equal(window.aiUsesLocalBot('staff'), true);
    assert.equal(window.aiUsesLocalBot('sales'), false);
    assert.match(element('ai-bot-list').innerHTML, /value="browser-qwen" selected/);
    element('ai-bot-model-staff').value = '42';
    await window.aiSaveBot('staff');
    assert.equal(window.aiUsesLocalBot('staff'), false);
    assert.equal(calls[1].payload.modelId, 42);
});

test('Qwen local runtime loads lazily and refuses unsupported browsers', async () => {
    let created = 0;
    class FakeWorker {
        constructor(path, options) {
            created += 1;
            assert.equal(path, '/qwen_worker.bundle.js');
            assert.equal(options.type, 'module');
        }
        postMessage(request) {
            if (request.type === 'load') this.onmessage({ data: { type: 'result', id: request.id, text: 'ready' } });
            else this.onmessage({ data: { type: 'result', id: request.id, text: 'local reply' } });
        }
        terminate() {}
    }
    const window = { isSecureContext: true };
    vm.runInNewContext(runtime, { window, navigator: { gpu: {} }, Worker: FakeWorker, document: { querySelector: () => null }, Map, Promise });
    assert.equal(created, 0);
    assert.equal(await window.invenQwen.load(), 'ready');
    assert.equal(created, 1);
    assert.equal(await window.invenQwen.generate('store facts', 'hi'), 'local reply');
    assert.equal(created, 1);
    const unsupported = { isSecureContext: true };
    vm.runInNewContext(runtime, { window: unsupported, navigator: {}, Worker: FakeWorker, document: { querySelector: () => null }, Map, Promise });
    await assert.rejects(unsupported.invenQwen.load(), /WebGPU/);
});
