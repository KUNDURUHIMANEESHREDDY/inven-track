/* Bundled by npm run build:qwen. No API keys or store data are persisted here. */
import { AutoProcessor, Qwen3_5ForConditionalGeneration, env } from '@huggingface/transformers';

const MODEL_ID = 'onnx-community/Qwen3.5-0.8B-ONNX';
env.useBrowserCache = true;
env.useWasmCache = true;
env.allowLocalModels = false;
let loading = null;
let processor = null;
let model = null;

function report(state, extra = {}) {
    self.postMessage({ type: 'status', state, ...extra });
}
function friendlyError(error) {
    const value = error && error.message ? String(error.message) : 'Unknown model error';
    // Never include application prompts or personally identifiable store data in errors.
    if (/out of memory|allocation|GPUDevice|device lost/i.test(value)) {
        return 'WebGPU ran out of resources or the GPU device was lost. Disable local Qwen and use a cloud model.';
    }
    if (/fetch|network|download|HTTP|404/i.test(value)) {
        return 'Model download failed. Check your connection and try again.';
    }
    return 'Local Qwen could not initialize or generate. Check browser WebGPU support and try again.';
}
async function ensureLoaded() {
    if (!self.navigator || !self.navigator.gpu) {
        throw new Error('WebGPU unavailable');
    }
    if (!loading) {
        loading = (async () => {
            const progress_callback = (event) => {
                if (event && (event.status === 'progress' || event.status === 'progress_total')) {
                    report('downloading', { file: String(event.file || ''), percent:
                        Number.isFinite(event.progress) ? Math.max(0, Math.min(100, Math.round(event.progress))) : null });
                }
            };
            report('loading');
            processor = await AutoProcessor.from_pretrained(MODEL_ID, { progress_callback });
            model = await Qwen3_5ForConditionalGeneration.from_pretrained(MODEL_ID, {
                device: 'webgpu',
                dtype: {
                    embed_tokens: 'q4',
                    vision_encoder: 'q4',
                    decoder_model_merged: 'q4',
                },
                progress_callback,
            });
            report('ready');
        })().catch((error) => {
            loading = null;
            processor = null;
            model = null;
            throw error;
        });
    }
    return loading;
}
self.addEventListener('message', async (event) => {
    const { id, type, instruction, message } = event.data || {};
    if (!Number.isInteger(id) || !['load', 'generate'].includes(type)) return;
    try {
        await ensureLoaded();
        if (type === 'load') {
            self.postMessage({ type: 'result', id, text: 'Model ready' });
            return;
        }
        if (typeof message !== 'string' || message.length > 2000 ||
            typeof instruction !== 'string' || instruction.length > 10000) {
            throw new Error('Invalid prompt');
        }
        report('generating');
        const conversation = [
            { role: 'system', content: [{ type: 'text', text: instruction }] },
            { role: 'user', content: [{ type: 'text', text: message }] },
        ];
        const prompt = processor.apply_chat_template(conversation, { add_generation_prompt: true });
        const inputs = await processor(prompt);
        const output = await model.generate({
            ...inputs,
            max_new_tokens: 180,
            do_sample: false,
        });
        const decoded = processor.batch_decode(
            output.slice(null, [inputs.input_ids.dims.at(-1), null]),
            { skip_special_tokens: true },
        )[0]?.trim();
        if (!decoded) throw new Error('Model returned an empty answer');
        self.postMessage({ type: 'result', id, text: decoded });
        report('ready');
    } catch (error) {
        self.postMessage({ type: 'error', id, message: friendlyError(error) });
        report('failed');
    }
});
