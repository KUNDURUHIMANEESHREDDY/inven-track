/* AI Model Studio for InvenTrack. Secrets never enter client-side storage. */
(function () {
    'use strict';
    let registry = { models: [], bots: [], providers: {} };
    let editingId = null;
    let loading = false;
    const icons = { staff: 'messages-square', sales: 'chart-no-axes-combined', inventory: 'package-check' };
    const hints = {
        staff: 'Product help, billing and worker onboarding',
        sales: 'Sales trends, receipts and revenue explanations',
        inventory: 'Stock levels, restock guidance and alerts'
    };
    function byId(id) { return document.getElementById(id); }
    function safe(v) {
        return String(v ?? '').replace(/[&<>"']/g, function(c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function message(msg, type) {
        if (typeof window.showToast === 'function') window.showToast(msg, type || 'info');
    }
    function showSaveError(msg) {
        const banner = byId('ai-save-error');
        if (!banner) return;
        banner.textContent = msg || '';
        banner.classList.toggle('hidden', !msg);
    }
    function refreshIcons() { if (window.lucide) window.lucide.createIcons(); }
    function cleanEndpoint(input) {
        const value = String(input || '').trim();
        let url;
        try { url = new URL(value); }
        catch (_) { throw new Error('Base endpoint: enter an HTTPS URL, e.g. https://9router.com/v1'); }
        if (url.protocol !== 'https:') {
            throw new Error('Base endpoint: use https://, not http://');
        }
        if (!url.hostname || url.username || url.password) {
            throw new Error('Base endpoint: remove username/password from the URL; put the API key in its own field.');
        }
        if (value.includes('?') || value.includes('#')) {
            throw new Error('Base endpoint: remove the ?query or #fragment. Put the API key in the separate field.');
        }
        if (value.split('').some(c => c.trim() === '')) {
            throw new Error('Base endpoint: remove spaces and line breaks from the URL.');
        }
        const path = url.pathname.replace(new RegExp('/+$'), '');
        const basePath = path.endsWith('/chat/completions')
            ? path.slice(0, -'/chat/completions'.length) : path;
        return url.origin + basePath;
    }
    function endpointHint() {
        const provider = registry.providers[byId('ai-provider').value];
        const preset = provider && provider.endpoint;
        const managed = Boolean(preset);
        const input = byId('ai-endpoint');
        const hint = byId('ai-endpoint-hint');
        const reset = byId('ai-reset-endpoint');
        // A selected built-in provider always uses the audited server preset.
        // Custom providers alone allow arbitrary (HTTPS-only) API base URLs.
        if (input) {
            input.readOnly = managed;
            if (managed) input.value = preset;
            input.classList.toggle('cursor-not-allowed', managed);
        }
        const protocol = byId('ai-protocol');
        if (protocol) protocol.disabled = managed;
        if (hint) hint.textContent = managed
            ? 'Automatically configured for ' + provider.label + '. Select Custom endpoint to use your own URL.'
            : 'Enter your provider HTTPS API base URL. Do not include ?keys or /chat/completions.';
        if (reset) reset.classList.add('hidden');
    }
    window.aiResetEndpoint = function() {
        endpointHint();
        showSaveError('');
    };
    async function request(url, init) {
        const response = await fetch(url, Object.assign({
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }
        }, init || {}));
        const body = await response.json().catch(function () { return {}; });
        if (!response.ok) {
            const details = body.detail;
            const labels = { name: 'Connection name', provider: 'Provider', protocol: 'API format',
                endpoint: 'Base endpoint', modelId: 'Model ID', apiKey: 'API key' };
            if (Array.isArray(details) && details.length) {
                const errors = details.slice(0, 3).map(function(item) {
                    const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : '';
                    const title = labels[field] || 'Form';
                    const reason = String(item.msg || 'Invalid value').replace(/^Value error,?\s*/i, '');
                    return title + ': ' + reason;
                });
                throw new Error(errors.join('; ') || 'Invalid AI model settings');
            }
            throw new Error(typeof details === 'string' ? details :
                (response.status === 401 ? 'Log in to configure AI models' :
                'The server could not save this request (HTTP ' + response.status + ')'));
        }
        return body;
    }
    function providerLabel(key) {
        return (registry.providers[key] || {}).label || key;
    }
    function renderModels() {
        const container = byId('ai-model-list');
        if (!container) return;
        if (!registry.models.length) {
            container.innerHTML =
                '<div class="p-10 sm:p-14 text-center">' +
                '<span class="mx-auto w-14 h-14 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center mb-4"><i data-lucide="brain-circuit" class="w-7 h-7"></i></span>' +
                '<h4 class="font-bold text-slate-900">No models connected yet</h4>' +
                '<p class="text-sm text-slate-500 mt-1 mb-5">Add a model to power your AI assistants.</p>' +
                '<button type="button" onclick="aiOpenModelModal()" class="rounded-xl bg-indigo-600 text-white text-sm font-semibold py-2.5 px-5 hover:bg-indigo-700">Connect your first model</button></div>';
            refreshIcons();
            return;
        }
        container.innerHTML = registry.models.map(function(m) {
            return '<div class="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 hover:bg-slate-50/70 transition">' +
                '<div class="flex gap-3.5 min-w-0 items-start">' +
                '<span class="w-11 h-11 shrink-0 rounded-xl flex items-center justify-center bg-indigo-50 border border-indigo-100 text-indigo-600"><i data-lucide="cpu" class="w-5 h-5"></i></span>' +
                '<div class="min-w-0"><div class="flex flex-wrap items-center gap-2"><span class="font-bold text-sm text-slate-900">' + safe(m.name) + '</span>' +
                '<span class="bg-slate-100 border border-slate-200 text-[10px] rounded-md font-semibold text-slate-600 px-2 py-0.5">' + safe(providerLabel(m.provider)) + '</span></div>' +
                '<p class="text-xs text-slate-600 font-mono mt-1 break-all">' + safe(m.modelId) + '</p>' +
                '<div class="flex items-center gap-2 mt-2 text-[11px] text-slate-400"><i data-lucide="' + (m.hasKey ? 'lock-keyhole' : 'key-round') + '" class="w-3.5 h-3.5"></i>' +
                (m.hasKey ? 'Encrypted key saved' : 'No API key') + '<span>·</span><span class="truncate max-w-[190px]" title="' + safe(m.endpoint) + '">' + safe(m.endpoint) + '</span></div></div></div>' +
                '<div class="flex items-center gap-1.5 shrink-0 pl-14 sm:pl-0">' +
                '<button type="button" onclick="aiTestModel(' + m.id + ')" class="text-xs font-semibold text-emerald-700 hover:bg-emerald-50 border border-emerald-100 px-3 py-2 rounded-lg">Test</button>' +
                '<button type="button" onclick="aiOpenModelModal(' + m.id + ')" class="text-xs font-semibold text-indigo-700 hover:bg-indigo-50 border border-indigo-100 px-3 py-2 rounded-lg">Edit</button>' +
                '<button type="button" aria-label="Delete model" onclick="aiDeleteModel(' + m.id + ')" class="p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600 rounded-lg"><i data-lucide="trash-2" class="w-4 h-4"></i></button>' +
                '</div></div>';
        }).join('');
        refreshIcons();
    }
    function modelOptions(selected) {
        return '<option value="">No model selected</option>' + registry.models.map(function(m) {
            return '<option value="' + m.id + '"' + (selected === m.id ? ' selected' : '') + '>' +
                safe(m.name) + ' · ' + safe(m.modelId) + '</option>';
        }).join('');
    }
    function renderBots() {
        const container = byId('ai-bot-list');
        if (!container) return;
        container.innerHTML = registry.bots.map(function(bot) {
            const assigned = registry.models.some(function(m) { return m.id === bot.modelId; });
            return '<div class="rounded-2xl border border-slate-200 p-4 sm:p-5">' +
                '<div class="flex items-start gap-3 mb-4"><span class="flex items-center justify-center w-10 h-10 rounded-xl bg-slate-100 text-indigo-600 shrink-0"><i data-lucide="' + icons[bot.slug] + '" class="w-5 h-5"></i></span>' +
                '<div class="flex-1 min-w-0"><div class="flex flex-wrap justify-between gap-2"><h4 class="font-bold text-sm text-slate-900">' + safe(bot.name) + '</h4>' +
                '<span class="text-[10px] font-bold rounded-full px-2.5 py-1 ' + (assigned ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500') + '">' + (assigned ? '● Active' : 'Unassigned') + '</span></div>' +
                '<p class="text-xs text-slate-500 mt-1">' + hints[bot.slug] + '</p></div></div>' +
                '<label for="ai-bot-model-' + bot.slug + '" class="block text-[11px] text-slate-600 font-semibold mb-1.5">Assigned model</label>' +
                '<select id="ai-bot-model-' + bot.slug + '" class="w-full border border-slate-200 bg-slate-50 rounded-xl text-xs p-3 focus:ring-2 focus:ring-indigo-200 outline-none">' + modelOptions(bot.modelId) + '</select>' +
                '<label for="ai-bot-instructions-' + bot.slug + '" class="block text-[11px] text-slate-600 font-semibold mt-3 mb-1.5">Extra instructions <span class="font-normal text-slate-400">(optional)</span></label>' +
                '<textarea id="ai-bot-instructions-' + bot.slug + '" rows="2" maxlength="2000" placeholder="e.g. Answer briefly in English and Telugu" class="w-full resize-y border border-slate-200 bg-slate-50 rounded-xl text-xs p-3 focus:ring-2 focus:ring-indigo-200 outline-none">' + safe(bot.extraInstruction) + '</textarea>' +
                '<div class="mt-3 flex justify-end"><button type="button" onclick="aiSaveBot(' + "'" + bot.slug + "'" + ')" class="text-xs font-semibold px-4 py-2.5 bg-indigo-600 text-white hover:bg-indigo-700 rounded-lg">Save assignment</button></div></div>';
        }).join('');
        byId('ai-model-total').textContent = String(registry.models.length);
        byId('ai-active-total').innerHTML = registry.bots.filter(function(b) {
            return registry.models.some(function(m) { return m.id === b.modelId; });
        }).length + ' <span class="text-base text-slate-400 font-medium">/ 3</span>';
        refreshIcons();
    }
    function updateChatBotSelect() {
        const select = byId('ai-chat-bot');
        if (!select) return;
        const old = select.value || 'staff';
        select.innerHTML = registry.bots.map(function(bot) {
            return '<option value="' + safe(bot.slug) + '">' + safe(bot.name) + '</option>';
        }).join('');
        select.value = registry.bots.some(function(bot) { return bot.slug === old; }) ? old : 'staff';
    }
    window.aiLoadSettings = async function() {
        if (loading) return;
        loading = true;
        try {
            registry = await request('/api/ai/config');
            renderModels(); renderBots(); updateChatBotSelect();
        } catch (err) {
            const list = byId('ai-model-list');
            if (list) list.textContent = 'Could not load AI configuration: ' + err.message;
            message(err.message, 'warning');
        } finally { loading = false; }
    };
    function providerOptions() {
        return Object.entries(registry.providers).map(function(p) {
            return '<option value="' + safe(p[0]) + '">' + safe(p[1].label) + '</option>';
        }).join('');
    }
    window.aiOpenModelModal = function(id) {
        if (!registry.providers.openai) { message('Model settings are still loading. Try again shortly.', 'warning'); return; }
        editingId = id == null ? null : Number(id);
        const model = registry.models.find(function(m) { return m.id === editingId; });
        if (!byId('ai-model-modal')) return;
        byId('ai-model-form').reset();
        showSaveError('');
        byId('ai-provider').innerHTML = providerOptions();
        byId('ai-provider').value = model ? model.provider : 'openai';
        byId('ai-model-name').value = model ? model.name : '';
        byId('ai-protocol').value = model ? model.protocol : 'openai';
        byId('ai-endpoint').value = model ? model.endpoint : registry.providers.openai.endpoint;
        byId('ai-model-id').value = model ? model.modelId : registry.providers.openai.model;
        endpointHint();
        byId('ai-api-key').value = '';
        byId('ai-api-key').type = 'password';
        byId('ai-clear-key').checked = false;
        byId('ai-clear-key-row').classList.toggle('hidden', !(model && model.hasKey));
        byId('ai-key-note').textContent = model && model.hasKey ?
            'Key already stored securely. Leave blank to keep the existing key.' :
            (model && model.provider === '9router' ?
                'Get your 9Router gateway key under Dashboard → Settings → API Keys.' :
                'A key is optional for endpoints that do not require authentication.');
        byId('ai-discover-button').disabled = false;
        byId('ai-discover-note').textContent = model
            ? 'Uses the stored key; enter a new key here to preview changes.'
            : 'Enter an endpoint and API key, then fetch without saving.';
        byId('ai-discover-note').classList.remove('hidden');
        byId('ai-discover-results').classList.add('hidden');
        byId('ai-discover-results').replaceChildren();
        byId('ai-modal-title').textContent = model ? 'Edit AI connection' : 'Connect an AI model';
        byId('ai-model-modal').classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        refreshIcons();
        byId('ai-model-name').focus();
    };
    window.aiProviderChanged = function() {
        showSaveError('');
        const p = registry.providers[byId('ai-provider').value];
        if (!p) return;
        byId('ai-protocol').value = p.protocol;
        byId('ai-endpoint').value = p.endpoint || '';
        byId('ai-model-id').value = p.model || '';
        endpointHint();
        byId('ai-key-note').textContent = p.label === '9Router' ?
            'Get your 9Router gateway key in Dashboard → Settings → API Keys. Cloud endpoint availability may vary.' :
            'A key is optional for endpoints that do not require authentication.';
        if (!editingId) byId('ai-model-name').value = p.label === 'Custom endpoint' ? '' : p.label + ' model';
    };
    window.aiCloseModelModal = function() {
        byId('ai-api-key').value = '';
        byId('ai-model-modal').classList.add('hidden');
        document.body.style.overflow = '';
        editingId = null;
    };
    window.aiToggleKey = function() {
        const field = byId('ai-api-key');
        field.type = field.type === 'password' ? 'text' : 'password';
    };
    window.aiSaveModel = async function(event) {
        event.preventDefault();
        const btn = byId('ai-save-model');
        showSaveError('');
        const id = editingId;
        const payload = {
            provider: byId('ai-provider').value,
            protocol: byId('ai-protocol').value,
            name: byId('ai-model-name').value.trim(),
            endpoint: byId('ai-endpoint').value.trim(),
            modelId: byId('ai-model-id').value.trim(),
            apiKey: byId('ai-api-key').value.trim(),
            clearKey: byId('ai-clear-key').checked
        };
        btn.disabled = true; btn.classList.add('opacity-60');
        try {
            const selected = registry.providers[payload.provider];
            if (!selected) throw new Error('Select an AI provider before saving.');
            if (payload.provider === 'custom') {
                payload.endpoint = cleanEndpoint(payload.endpoint);
            } else {
                // Use the provider catalog, not a stale/edited URL in the form.
                payload.endpoint = selected.endpoint;
                payload.protocol = selected.protocol;
            }
            byId('ai-endpoint').value = payload.endpoint;
            await request(id ? '/api/ai/models/' + id : '/api/ai/models', {
                method: id ? 'PUT' : 'POST', body: JSON.stringify(payload)
            });
            aiCloseModelModal();
            await aiLoadSettings();
            message('AI model connection saved.', 'success');
        } catch (err) {
            showSaveError(err.message);
            message('Model connection could not be saved: ' + err.message, 'warning');
        }
        finally { btn.disabled = false; btn.classList.remove('opacity-60'); }
    };
    window.aiSaveBot = async function(slug) {
        const raw = byId('ai-bot-model-' + slug).value;
        const payload = {
            modelId: raw ? Number(raw) : null,
            extraInstruction: byId('ai-bot-instructions-' + slug).value
        };
        try {
            await request('/api/ai/bots/' + slug, { method: 'PUT', body: JSON.stringify(payload) });
            await aiLoadSettings();
            message('Bot model assignment saved.', 'success');
        } catch (err) { message(err.message, 'warning'); }
    };
    window.aiDeleteModel = async function(id) {
        const model = registry.models.find(function(x) { return x.id === id; });
        if (!model || !window.confirm('Delete "' + model.name + '"? Bots using it will be unassigned.')) return;
        try {
            await request('/api/ai/models/' + id, { method: 'DELETE' });
            await aiLoadSettings();
            message('Model connection removed.', 'info');
        } catch (err) { message(err.message, 'warning'); }
    };
    window.aiTestModel = async function(id) {
        message('Contacting provider. This may use API credits.', 'info');
        try {
            const result = await request('/api/ai/models/' + id + '/test', { method: 'POST', body: '{}' });
            message(result.connected ? 'Connection verified. Provider replied: ' + result.sample.slice(0, 75) : 'Provider did not verify.', 'success');
        } catch (err) { message('Connection failed: ' + err.message, 'warning'); }
    };
    window.aiDiscoverModels = async function() {
        const region = byId('ai-discover-results');
        const button = byId('ai-discover-button');
        if (button.disabled) return;
        region.classList.remove('hidden');
        region.setAttribute('role', 'status');
        region.textContent = 'Checking model catalog...';
        button.disabled = true;
        try {
            const provider = byId('ai-provider').value;
            const selected = registry.providers[provider];
            if (!selected) throw new Error('Choose an AI provider first.');
            const protocol = provider === 'custom' ? byId('ai-protocol').value : selected.protocol;
            const endpoint = provider === 'custom' ? cleanEndpoint(byId('ai-endpoint').value) : selected.endpoint;
            const apiKey = byId('ai-api-key').value.trim();
            const saved = registry.models.find(function(item) { return item.id === editingId; });
            const sameSavedSettings = saved && !apiKey && !byId('ai-clear-key').checked &&
                saved.provider === provider && saved.protocol === protocol && saved.endpoint === endpoint;
            let result;
            if (sameSavedSettings) {
                result = await request('/api/ai/models/' + editingId + '/discover');
            } else {
                if (saved && !apiKey && saved.hasKey) {
                    throw new Error('Enter your API key to preview changed settings, or save the changes first.');
                }
                result = await request('/api/ai/models/discover-preview', {
                    method: 'POST',
                    body: JSON.stringify({ provider: provider, protocol: protocol,
                        endpoint: endpoint, apiKey: apiKey })
                });
            }
            region.replaceChildren();
            if (!Array.isArray(result.models) || !result.models.length) {
                region.textContent = 'The provider returned no model IDs. Check the provider settings.';
                return;
            }
            const summary = document.createElement('div');
            summary.className = 'sticky top-0 bg-indigo-50 text-indigo-800 text-xs font-semibold px-3 py-2 border-b border-indigo-100';
            summary.textContent = 'Found ' + result.models.length + ' models. Select one to fill in Model ID.';
            region.appendChild(summary);
            result.models.forEach(function(name) {
                const item = document.createElement('button');
                item.type = 'button';
                item.className = 'block w-full text-left text-xs font-mono px-3 py-2.5 border-b border-slate-100 hover:bg-indigo-50 break-all';
                item.textContent = name;
                item.addEventListener('click', function() {
                    byId('ai-model-id').value = name;
                    region.classList.add('hidden');
                });
                region.appendChild(item);
            });
        } catch (err) {
            region.textContent = 'Could not fetch models: ' + err.message;
        } finally {
            button.disabled = false;
        }
    };
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && byId('ai-model-modal') && !byId('ai-model-modal').classList.contains('hidden')) {
            aiCloseModelModal();
        }
    });
})();
