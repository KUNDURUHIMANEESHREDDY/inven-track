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
            value, checked: false, textContent: '', disabled: false, children: [],
            replaceChildren() { this.children = []; this.textContent = ''; },
            appendChild(node) { this.children.push(node); },
            setAttribute() {},
            addEventListener(_name, fn) { this.onClick = fn; },
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
    element('ai-endpoint-hint');
    element('ai-reset-endpoint');
    element('ai-key-note');
    element('ai-discover-button');
    element('ai-discover-note');
    element('ai-discover-results');
    const sandbox = {
        URL,
        window: {},
        document: { getElementById: id => elements[id] || null, addEventListener() {}, createElement: tag => ({
            tagName: tag, textContent: '', className: '', type: '', onClick: null,
            addEventListener(_name, fn) { this.onClick = fn; }
        }) },
        fetch: async (url, options) => {
            if (url === '/api/ai/config') {
                return { ok: true, status: 200, json: async () => ({
                    models: [], bots: [], providers: {
                        custom: { label: 'Custom endpoint', endpoint: '', protocol: 'openai', model: '' },
                        openai: { label: 'OpenAI', endpoint: 'https://api.openai.com/v1', protocol: 'openai', model: 'gpt-4.1-mini' },
                        '9router': { label: '9Router', endpoint: 'https://9router.com/v1', protocol: 'openai', model: 'if/qwen3-coder-plus' }
                    }
                }) };
            }
            sandbox.lastRequest = url;
            sandbox.submitted = JSON.parse(options.body);
            return { ok: status >= 200 && status < 300, status,
                json: async () => status >= 200 && status < 300 ? detail : ({ detail }) };
        }
    };
    vm.runInNewContext(script, sandbox, { filename: 'ai_models.js' });
    return { sandbox, elements, ready: sandbox.window.aiLoadSettings() };
}

test('model save displays a field-specific validation error inside the modal', async () => {
    const { sandbox, elements, ready } = mockForm(422, [
        { loc: ['body', 'endpoint'], msg: 'Value error, Endpoint must be a valid HTTPS base URL', input: 'private-secret' }
    ]);
    await ready;
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    const banner = elements['ai-save-error'];
    assert.match(banner.textContent, /Base endpoint: Endpoint must be a valid HTTPS/);
    assert.equal(banner.textContent.includes('private-secret'), false);
    assert.equal(banner.classList.contains('hidden'), false);
    assert.equal(elements['ai-save-model'].disabled, false);
    assert.equal(sandbox.submitted.apiKey, 'secret-cannot-appear-in-errors');
});

test('server failures remain visible without exposing API keys', async () => {
    const { sandbox, elements, ready } = mockForm(500, []);
    await ready;
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.match(elements['ai-save-error'].textContent, /HTTP 500/);
    assert.doesNotMatch(elements['ai-save-error'].textContent, /secret-cannot/);
});

test('modal and toast error containers appear above the content', () => {
    assert.match(html, /id="ai-save-error" role="alert"/);
    assert.ok(html.includes('id="toast-container" class="fixed top-20 right-5 z-[90]'));
});

test('pasted OpenAI-compatible chat route is converted to base URL', async () => {
    const { sandbox, elements, ready } = mockForm(500, []);
    elements['ai-endpoint'].value = 'https://9router.com/v1/chat/completions/';
    await ready;
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted.endpoint, 'https://9router.com/v1');
    assert.equal(elements['ai-endpoint'].value, 'https://9router.com/v1');
});

test('query-string credentials are rejected without sending the URL to the backend', async () => {
    const { sandbox, elements, ready } = mockForm(500, []);
    elements['ai-endpoint'].value = 'https://api.example.com/v1?key=SECRETQUERY';
    await ready;
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted, undefined);
    assert.match(elements['ai-save-error'].textContent, /remove the \?query/);
    assert.doesNotMatch(elements['ai-save-error'].textContent, /SECRETQUERY/);
});

test('non-HTTPS endpoint is blocked before saving', async () => {
    const { sandbox, elements, ready } = mockForm(500, []);
    elements['ai-endpoint'].value = 'http://example.com/v1';
    await ready;
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted, undefined);
    assert.match(elements['ai-save-error'].textContent, /use https:\/\//);
});

test('built-in 9Router ignores an invalid typed HTTP endpoint', async () => {
    const { sandbox, elements, ready } = mockForm(500, []);
    await ready;
    elements['ai-provider'].value = '9router';
    elements['ai-endpoint'].value = 'http://incorrect.invalid/v1';
    elements['ai-protocol'].value = 'anthropic';
    await sandbox.window.aiSaveModel({ preventDefault() {} });
    assert.equal(sandbox.submitted.provider, '9router');
    assert.equal(sandbox.submitted.endpoint, 'https://9router.com/v1');
    assert.equal(sandbox.submitted.protocol, 'openai');
});

test('changing providers locks preset endpoint and unlocks custom URL', async () => {
    const { sandbox, elements, ready } = mockForm(500, []);
    await ready;
    elements['ai-provider'].value = '9router';
    sandbox.window.aiProviderChanged();
    assert.equal(elements['ai-endpoint'].value, 'https://9router.com/v1');
    assert.equal(elements['ai-endpoint'].readOnly, true);
    assert.equal(elements['ai-protocol'].disabled, true);
    assert.match(elements['ai-endpoint-hint'].textContent, /Automatically configured/);
    elements['ai-provider'].value = 'custom';
    sandbox.window.aiProviderChanged();
    assert.equal(elements['ai-endpoint'].readOnly, false);
    assert.equal(elements['ai-protocol'].disabled, false);
});

test('fetch available models is an enabled, visibly styled button on new connections', () => {
    assert.match(html, /id="ai-discover-button" type="button" onclick="aiDiscoverModels\(\)"/);
    assert.match(html, /id="ai-discover-button"[^>]*class="[^"]*rounded-lg[^"]*bg-indigo-50/);
    assert.ok(!html.includes('Save the connection first to fetch models.'));
    assert.ok(!script.includes("byId('ai-discover-button').disabled = !model"));
});

test('discover models before saving sends key only to authenticated backend and fills model ID', async () => {
    const { sandbox, elements, ready } = mockForm(200, {
        models: ['oc/hy4-flash-free', 'oc/mimo-v2.6-flash-free']
    });
    await ready;
    elements['ai-endpoint'].value = 'https://rn59vaz.abc-tunnel.us/v1';
    await sandbox.window.aiDiscoverModels();
    assert.equal(sandbox.lastRequest, '/api/ai/models/discover-preview');
    assert.equal(sandbox.submitted.provider, 'custom');
    assert.equal(sandbox.submitted.protocol, 'openai');
    assert.equal(sandbox.submitted.endpoint, 'https://rn59vaz.abc-tunnel.us/v1');
    assert.equal(sandbox.submitted.apiKey, 'secret-cannot-appear-in-errors');
    assert.equal(elements['ai-discover-button'].disabled, false);
    assert.match(elements['ai-discover-results'].children[0].textContent, /Found 2 models/);
    elements['ai-discover-results'].children[1].onClick();
    assert.equal(elements['ai-model-id'].value, 'oc/hy4-flash-free');
});

test('model catalog 403 displays diagnostic without leaking API key', async () => {
    const { sandbox, elements, ready } = mockForm(502,
        'AI provider HTTP 403: request forbidden. Check API key permissions.');
    await ready;
    await sandbox.window.aiDiscoverModels();
    assert.equal(sandbox.lastRequest, '/api/ai/models/discover-preview');
    assert.match(elements['ai-discover-results'].textContent, /HTTP 403/);
    assert.doesNotMatch(elements['ai-discover-results'].textContent, /secret-cannot-appear/);
    assert.equal(elements['ai-discover-button'].disabled, false);
});
