'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '..', 'ai_models.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'digital_inventory_and_sales_management_system.html'), 'utf8');

function mockForm(status, detail) {
    const elements = {};
    function element(id, value = '') {
        const classes = new Set(id === 'ai-save-error' ? ['hidden'] : []);
        return elements[id] = {
            value, checked: false, textContent: '', disabled: false,
            classList: {
                add(key) { classes.add(key); },
                remove(key) { classes.delete(key); },
                toggle(key, enabled) { if (enabled) classes.add(key); else classes.delete(key); },
                contains(key) { return classes.has(key); }
            }
        };
    }
    element('ai-provider', 'custom');
    element('ai-protocol', 'openai');
    element('ai-model-name', 'Test connection');
    element('ai-endpoint', 'https://example.com/v1');
    element('ai-model-id', 'test-model');
    element('ai-api-key', '  secret-cannot-appear-in-errors  ');
    element('ai-clear-key');
    element('ai-save-model');
    element('ai-save-error');
    const sandbox = {
        URL,
        window: {},
        document: { getElementById: id => elements[id] || null, addEventListener() {} },
        fetch: async (_url, options) => {
            sandbox.submitted = JSON.parse(options.body);
            return { ok: false, status, json: async () => ({ detail }) };
        }
    };
    vm.runInNewContext(script, sandbox, { filename: 'ai_models.js' });
    return { sandbox, elements };
}

test('model save displays a field-specific validation error inside the modal', async () => {
    const { sandbox, elements } = mockForm(422, [
        { loc: ['body', 'endpoint'], msg: 'Value error, Endpoint must be a valid HTTPS base URL', input: 'private-secret' }
    ]);
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    const banner = elements['ai-save-error'];
    assert.match(banner.textContent, /Base endpoint: Endpoint must be a valid HTTPS/);
    assert.equal(banner.textContent.includes('private-secret'), false);
    assert.equal(banner.classList.contains('hidden'), false);
    assert.equal(elements['ai-save-model'].disabled, false);
    assert.equal(sandbox.submitted.apiKey, 'secret-cannot-appear-in-errors');
});

test('server failures remain visible without exposing API keys', async () => {
    const { sandbox, elements } = mockForm(500, []);
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.match(elements['ai-save-error'].textContent, /HTTP 500/);
    assert.doesNotMatch(elements['ai-save-error'].textContent, /secret-cannot/);
});

test('modal and toast error containers appear above the content', () => {
    assert.match(html, /id="ai-save-error" role="alert"/);
    assert.ok(html.includes('id="toast-container" class="fixed top-20 right-5 z-[90]'));
});

test('pasted OpenAI-compatible chat route is converted to base URL', async () => {
    const { sandbox, elements } = mockForm(500, []);
    elements['ai-endpoint'].value = 'https://9router.com/v1/chat/completions/';
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted.endpoint, 'https://9router.com/v1');
    assert.equal(elements['ai-endpoint'].value, 'https://9router.com/v1');
});

test('query-string credentials are rejected without sending the URL to the backend', async () => {
    const { sandbox, elements } = mockForm(500, []);
    elements['ai-endpoint'].value = 'https://api.example.com/v1?key=SECRETQUERY';
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted, undefined);
    assert.match(elements['ai-save-error'].textContent, /remove the \?query/);
    assert.doesNotMatch(elements['ai-save-error'].textContent, /SECRETQUERY/);
});

test('non-HTTPS endpoint is blocked before saving', async () => {
    const { sandbox, elements } = mockForm(500, []);
    elements['ai-endpoint'].value = 'http://example.com/v1';
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted, undefined);
    assert.match(elements['ai-save-error'].textContent, /use https:\/\//);
});
