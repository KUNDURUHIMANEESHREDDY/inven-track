/* Browser-only Qwen runtime. Lazy-loading avoids impacting normal InvenTrack startup. */
(function () {
    'use strict';
    let worker = null;
    let serial = 0;
    const pending = new Map();
    let stateListener = function() {};
    function available() {
        return Boolean(window.isSecureContext && navigator.gpu && typeof Worker !== 'undefined');
    }
    function notify(state, details) {
        stateListener(state, details || {});
        const typing = document.querySelector('#ai-typing-indicator .ai-typing-text');
        if (typing) {
            typing.textContent = state === 'downloading' ? 'Downloading on-device Qwen' +
                (Number.isFinite(details && details.percent) ? ' · ' + details.percent + '%' : '') + '…'
                : state === 'generating' ? 'Qwen is generating locally…'
                : state === 'loading' ? 'Loading on-device Qwen…'
                : state === 'failed' ? 'Local Qwen failed' : 'InvenTrack AI is thinking…';
        }
    }
    function getWorker() {
        if (!available()) {
            throw new Error('This browser does not support WebGPU. Select an assigned cloud model instead.');
        }
        if (!worker) {
            worker = new Worker('/qwen_worker.bundle.js', { type: 'module' });
            worker.onmessage = function(event) {
                const data = event.data || {};
                if (data.type === 'status') {
                    notify(data.state, data);
                } else if (data.type === 'result' || data.type === 'error') {
                    const task = pending.get(data.id);
                    if (!task) return;
                    pending.delete(data.id);
                    if (data.type === 'error') task.reject(new Error(data.message || 'Qwen failed'));
                    else task.resolve(data.text);
                }
            };
            worker.onerror = function() {
                for (const task of pending.values()) task.reject(new Error('Qwen worker crashed. Reload to try again.'));
                pending.clear();
                worker.terminate();
                worker = null;
                notify('failed', {});
            };
        }
        return worker;
    }
    function run(type, instruction, message) {
        return new Promise(function(resolve, reject) {
            try {
                const id = ++serial;
                pending.set(id, { resolve, reject });
                getWorker().postMessage({ id, type, instruction, message });
            } catch (error) {
                pending.delete(serial);
                reject(error);
            }
        });
    }
    window.invenQwen = {
        available,
        load: function() { return run('load', '', ''); },
        generate: function(instruction, message) { return run('generate', instruction, message); },
        set onStatus(listener) { stateListener = typeof listener === 'function' ? listener : function() {}; },
    };
})();
