from pathlib import Path
p = Path(__file__).resolve().parent.parent / "digital_inventory_and_sales_management_system.html"
s = p.read_text(encoding="utf-8")
if 'id="new-prod-cost"' not in s:
    anchor = '                <div class="grid grid-cols-2 gap-3">\n                    <div>\n                        <label class="block text-xs font-semibold text-slate-700 mb-1">Opening Stock Qty'
    cost = '''                <div class="mb-3">
                    <label class="block text-xs font-semibold text-slate-700 mb-1">Purchase / Cost Price (INR, optional)</label>
                    <input type="number" id="new-prod-cost" step="0.01" min="0" placeholder="Actual unit cost" class="w-full px-3.5 py-2 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none focus:border-indigo-500">
                </div>
'''
    assert anchor in s
    s = s.replace(anchor, cost + anchor, 1)
if 'id="payment-razorpay-option"' not in s:
    anchor = '''                                        <label class="cursor-pointer">
                                            <input type="radio" name="paymentMode" value="Card"'''
    label = '''                                        <label id="payment-razorpay-option" class="cursor-pointer hidden">
                                            <input type="radio" name="paymentMode" value="Online (Razorpay)" class="peer sr-only">
                                            <div class="py-2 text-center text-xs font-semibold rounded-xl border border-slate-200 bg-slate-50 peer-checked:bg-indigo-50 peer-checked:border-indigo-600 peer-checked:text-indigo-700 transition">Pay Online</div>
                                        </label>
'''
    assert anchor in s
    s = s.replace(anchor, label + anchor, 1)
if 'Cost / Unit' not in s:
    anchor = '<th class="py-3 px-4">Unit Price</th>'
    assert anchor in s
    s = s.replace(anchor, anchor + '\n                                        <th class="py-3 px-4">Cost / Unit</th>', 1)
if 'Set Cost</button>' not in s:
    prefix = '$' + '{'
    anchor = '''                        <td class="py-3.5 px-4 font-semibold text-slate-900">''' + prefix + '''formatINR(item.price)}</td>'''
    replacement = anchor + '''
                        <td class="py-3.5 px-4 font-medium text-slate-600">''' + prefix + '''item.costPrice === null ? 'Not set' : formatINR(item.costPrice)}</td>'''
    assert anchor in s
    s = s.replace(anchor, replacement, 1)
    anchor = '''                            <button onclick="deleteInventoryItem(''' + prefix + '''item.id})"'''
    replacement = '''                            <button onclick="editProductCost(''' + prefix + '''item.id})" class="px-2.5 py-1 rounded-lg bg-amber-50 hover:bg-amber-100 text-amber-700 font-semibold text-[11px] transition">Set Cost</button>
''' + anchor
    assert anchor in s
    s = s.replace(anchor, replacement, 1)
s = s.replace('Profit (requires costs)', 'Gross Profit (before expenses)', 1)
s = s.replace('Add cost prices to calculate profit', 'Excludes GST and operating expenses', 1)
p.write_text(s, encoding="utf-8")
print("HTML updated")

p = p.parent / "api.js"
s = p.read_text(encoding="utf-8")
if 'window.payOnlineInvoice' not in s:
    s = s.replace('    let checkoutPending = false;', '    let checkoutPending = false;\n    let gatewayEnabled = false;', 1)
    s = s.replace('''        liveDashboard = data.dashboard;''','''        liveDashboard = data.dashboard;
        gatewayEnabled = Boolean(data.gatewayEnabled);
        const onlineOption = document.getElementById('payment-razorpay-option');
        if (onlineOption) onlineOption.classList.toggle('hidden', !gatewayEnabled);''', 1)
    s = s.replace('''        refreshAllViews();
    }

    function authError''','''        refreshAllViews();
        addPayButtons();
    }

    function authError''', 1)
    s = s.replace('''        const minStock = Number(document.getElementById('new-prod-min').value);''','''        const minStock = Number(document.getElementById('new-prod-min').value);
        const costField = document.getElementById('new-prod-cost');
        const costPrice = costField && costField.value.trim() !== '' ? Number(costField.value) : null;''', 1)
    s = s.replace('''body: JSON.stringify({ name, category, price, stock, minStock })''',
                  '''body: JSON.stringify({ name, category, price, stock, minStock, costPrice })''', 1)
    s = s.replace('''            openReceiptModal(invoice);
            showToast('Invoice ' + invoice.id + ' saved and stock deducted.', 'success');''',
'''            openReceiptModal(invoice);
            if (invoice.paymentStatus === 'pending') {
                showToast('Online invoice created. Complete payment to confirm.', 'info');
                await window.payOnlineInvoice(invoice.id);
            } else {
                showToast('Invoice ' + invoice.id + ' saved and stock deducted.', 'success');
            }''', 1)
    s = s.replace('''        document.getElementById('kpi-net-profit').textContent = 'N/A';''',
'''        document.getElementById('kpi-net-profit').textContent =
            m.profitAvailable && m.grossProfit !== undefined ? formatINR(m.grossProfit) : 'N/A';''', 1)
    anchor = '''    window.callGeminiWithBackoff = async function(_systemInstruction, userQuery) {'''
    integration = '''    window.editProductCost = async function(id) {
        const item = inventory.find(p => p.id === id);
        if (!item) return;
        const input = prompt('Actual purchase / unit cost (INR) for ' + item.name,
                             item.costPrice === null ? '' : String(item.costPrice));
        if (input === null) return;
        const amount = Number(input.trim());
        if (!input.trim() || !Number.isFinite(amount) || amount < 0) {
            showToast('Enter a valid non-negative purchase cost.', 'warning');
            return;
        }
        try {
            await api('/api/products/' + id, {
                method: 'PATCH', body: JSON.stringify({costPrice: amount})
            });
            await loadStore();
            showToast('Unit cost updated for future invoices.', 'success');
        } catch (error) { showToast(error.message, 'warning'); }
    };

    function addPayButtons() {
        const rows = document.querySelectorAll('#all-bills-tbody tr');
        rows.forEach((row, index) => {
            const bill = bills[index];
            if (!bill || bill.payment !== 'Online (Razorpay)' || bill.paymentStatus !== 'pending') return;
            const cell = row.lastElementChild;
            if (!cell || cell.querySelector('.retry-payment')) return;
            const btn = document.createElement('button');
            btn.textContent = 'Pay Online';
            btn.className = 'retry-payment ml-1 px-2.5 py-1 rounded-lg bg-indigo-600 text-white font-semibold text-xs';
            btn.onclick = () => window.payOnlineInvoice(bill.id);
            cell.appendChild(btn);
        });
    }

    async function ensureCheckoutScript() {
        if (window.Razorpay) return;
        await new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://checkout.razorpay.com/v1/checkout.js';
            script.onload = resolve;
            script.onerror = () => reject(new Error('Could not load secure checkout'));
            document.head.appendChild(script);
        });
    }

    window.payOnlineInvoice = async function(invoiceId) {
        if (!gatewayEnabled) {
            showToast('Online gateway is not enabled.', 'warning');
            return;
        }
        const number = Number(String(invoiceId).replace('INV-', '')) - 1000;
        if (!Number.isInteger(number) || number < 1) return;
        try {
            const order = await api('/api/payments/orders/' + number, {
                method: 'POST', body: '{}'
            });
            await ensureCheckoutScript();
            const checkout = new window.Razorpay({
                key: order.keyId, amount: order.amount, currency: order.currency,
                order_id: order.orderId, name: order.businessName,
                description: invoiceId,
                handler: async function(result) {
                    try {
                        const response = await api('/api/payments/verify', {
                            method: 'POST',
                            body: JSON.stringify({
                                invoiceNumber: number,
                                razorpay_order_id: result.razorpay_order_id,
                                razorpay_payment_id: result.razorpay_payment_id,
                                razorpay_signature: result.razorpay_signature
                            })
                        });
                        await loadStore();
                        openReceiptModal(response.invoice);
                        showToast('Online payment verified and captured.', 'success');
                    } catch (error) {
                        showToast('Payment not verified yet: ' + error.message, 'warning');
                    }
                },
                modal: { ondismiss: () => showToast(
                    'Online payment not confirmed. Invoice remains pending.', 'info') }
            });
            checkout.open();
        } catch (error) {
            showToast('Online checkout: ' + error.message, 'warning');
        }
    };

''' + anchor
    assert anchor in s
    s = s.replace(anchor, integration, 1)
p.write_text(s, encoding="utf-8")
print("Frontend API adapter updated")
