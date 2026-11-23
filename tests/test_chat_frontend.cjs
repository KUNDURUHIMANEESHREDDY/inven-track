'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'digital_inventory_and_sales_management_system.html'), 'utf8');
const start = html.indexOf('let aiChatInFlight = false;');
const end = html.indexOf('function generateLocalSmartReply(', start);
assert.ok(start > 0 && end > start, 'Chat backend implementation must exist');
const code = html.slice(start, end) + '\nthis.submitChat = handleChatSubmit;\n';

function setup(response) {
    const input = { value: 'Help with my inventory' };
    const sendButton = { disabled: false };
    const messages = [];
    const calls = [];
    let typing = 0;
    let fired;
    let cleared = false;
    class FakeController {
        constructor() {
            this.signal = {
                aborted: false,
                onAbort: null,
            };
        }
        abort() {
            this.signal.aborted = true;
            if (this.signal.onAbort) {
                const err = new Error('Request aborted');
                err.name = 'AbortError';
                this.signal.onAbort(err);
            }
        }
    }
    const sandbox = {
        AbortController: FakeController,
        document: { getElementById(id) {
            if (id === 'ai-chat-input') return input;
            if (id === 'ai-send-btn') return sendButton;
            return null;
        } },
        appendChatMessage(sender, value) { messages.push({ sender, value }); },
        appendTypingIndicator() { typing++; },
        removeTypingIndicator() { typing--; },
        setTimeout(callback, duration) {
            assert.equal(duration, 25000);
            fired = callback;
            return 1;
        },
        clearTimeout() { cleared = true; },
        fetch(url, options) {
            calls.push({ url, options });
            return typeof response === 'function' ? response(url, options) : Promise.resolve(response);
        }
    };
    vm.runInNewContext(code, sandbox, { filename: 'chat-frontend-inline.js' });
    return {
        sandbox, input, sendButton, messages, calls,
        get typing() { return typing; },
        get cleared() { return cleared; },
        triggerTimeout() { fired(); }
    };
}

test('uses authenticated backend, not Gemini browser API, and clears typing on success', async () => {
    const h = setup({ ok: true, status: 200, json: async () => ({ reply: 'Stock is healthy.' }) });
    await h.sandbox.submitChat({ preventDefault() {} });
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].url, '/api/ai/chat');
    assert.equal(h.calls[0].options.method, 'POST');
    assert.equal(h.calls[0].options.credentials, 'same-origin');
    assert.deepEqual(JSON.parse(h.calls[0].options.body), { bot: 'staff', message: 'Help with my inventory' });
    assert.equal(h.messages[1].value, 'Stock is healthy.');
    assert.equal(h.typing, 0);
    assert.equal(h.sendButton.disabled, false);
    assert.equal(h.cleared, true);
    assert.ok(!html.includes('callGeminiWithBackoff'));
});

test('displays upstream 403 explicitly instead of silent fallback or infinite thinking', async () => {
    const h = setup({ ok: false, status: 502, json: async () => ({
        detail: 'AI provider HTTP 403: request forbidden. Check API key permissions.'
    }) });
    await h.sandbox.submitChat({ preventDefault() {} });
    assert.match(h.messages[1].value, /AI provider HTTP 403/);
    assert.equal(h.typing, 0);
    assert.equal(h.sendButton.disabled, false);
});

test('stops pending request after 25 seconds and restores send button', async () => {
    const h = setup((_url, opts) => new Promise((_resolve, reject) => {
        opts.signal.onAbort = reject;
    }));
    const pending = h.sandbox.submitChat({ preventDefault() {} });
    assert.equal(h.sendButton.disabled, true);
    h.triggerTimeout();
    await pending;
    assert.match(h.messages[1].value, /timed out after 25 seconds/);
    assert.equal(h.typing, 0);
    assert.equal(h.sendButton.disabled, false);
});

test('prevents duplicate submission while first AI request is in flight', async () => {
    let complete;
    const h = setup(() => new Promise(resolve => { complete = resolve; }));
    const pending = h.sandbox.submitChat({ preventDefault() {} });
    h.input.value = 'second message';
    await h.sandbox.submitChat({ preventDefault() {} });
    assert.equal(h.calls.length, 1);
    complete({ ok: true, status: 200, json: async () => ({ reply: 'Done' }) });
    await pending;
    assert.equal(h.typing, 0);
    assert.equal(h.sendButton.disabled, false);
});

test('unassigned model error explains the next action', async () => {
    const h = setup({ ok: false, status: 503, json: async () => ({
        detail: 'Assign a model to this bot in AI Model Settings'
    }) });
    await h.sandbox.submitChat({ preventDefault() {} });
    assert.match(h.messages[1].value, /assign one/);
    assert.equal(h.typing, 0);
});
