/* VyaparFlow live API adapter: replaces in-browser demo mutations. */
(() => {
    'use strict';
    let liveDashboard = null;
    let checkoutPending = false;
    let gatewayEnabled = false;

    async function api(path, options = {}) {
        const config = {
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            ...options
        };
        const response = await fetch(path, config);
        let data = null;
        try { data = await response.json(); } catch (_) { /* no JSON */ }
        if (!response.ok) {
            const detail = data && data.detail;
            const message = typeof detail === 'string'
                ? detail : (Array.isArray(detail) ? 'Please check the entered fields.' : 'Request failed (' + response.status + ')');
            throw new Error(message);
        }
        return data;
    }

    async function loadStore() {
        const data = await api('/api/bootstrap');
        inventory = data.inventory;
        bills = data.bills;
        currentOwner = data.owner;
        invoiceCounter = data.invoiceCounter;
        liveDashboard = data.dashboard;
        gatewayEnabled = Boolean(data.gatewayEnabled);
        const onlineOption = document.getElementById('payment-razorpay-option');
        if (onlineOption) onlineOption.classList.toggle('hidden', !gatewayEnabled);
        pastSixDaysRevenue.splice(0, pastSixDaysRevenue.length, ...liveDashboard.weeklyRevenue.slice(0, 6));
        weeklyDays.splice(0, weeklyDays.length, ...liveDashboard.weeklyDays);
        cart = cart.filter(item => {
            const product = inventory.find(p => p.id === item.id);
            if (!product) return false;
            item.qty = Math.min(item.qty, product.stock);
            item.price = product.price;
            item.name = product.name;
            return item.qty > 0;
        });
        refreshAllViews();
        addPayButtons();
    }

    function authError(message) {
        document.getElementById('auth-error-text').textContent = message;
        document.getElementById('auth-error').classList.remove('hidden');
    }

    window.handleAuthSubmit = async function(event) {
        event.preventDefault();
        const email = document.getElementById('input-email').value.trim();
        const mobile = document.getElementById('input-mobile').value.trim();
        const password = document.getElementById('input-password').value;
        const businessName = document.getElementById('input-biz-name').value.trim();
        const selectedType = document.querySelector('input[name="bizType"]:checked');
        document.getElementById('auth-error').classList.add('hidden');
        if (!email || !/^[0-9]{10}$/.test(mobile) || !password) {
            authError('Enter a valid email, 10-digit mobile number, and password.');
            return;
        }
        if (authMode === 'signup' && (!businessName || password.length < 8)) {
            authError('Enter a business name and a password of at least 8 characters.');
            return;
        }
        try {
            const payload = authMode === 'signup'
                ? { email, mobile, password, businessName, businessType: selectedType?.value || 'Supermart' }
                : { email, mobile, password };
            const route = authMode === 'signup' ? '/api/auth/register' : '/api/auth/login';
            const result = await api(route, { method: 'POST', body: JSON.stringify(payload) });
            currentOwner = result.owner;
            await loadStore();
            enterDashboard();
            showToast('Welcome to ' + currentOwner.businessName, 'success');
        } catch (error) {
            authError(error.message);
        }
    };

    window.quickDemoLogin = async function() {
        try {
            const result = await api('/api/auth/demo', { method: 'POST', body: '{}' });
            currentOwner = result.owner;
            await loadStore();
            enterDashboard();
            showToast('Fresh demo store loaded with saved sample data!', 'success');
        } catch (error) {
            authError(error.message);
        }
    };

    window.logoutUser = async function() {
        try {
            await api('/api/auth/logout', { method: 'POST', body: '{}' });
        } catch (_) { /* Still reset the visible UI when offline. */ }
        currentOwner = null;
        inventory = [];
        bills = [];
        cart = [];
        liveDashboard = null;
        document.getElementById('dashboard-screen').classList.add('hidden');
        document.getElementById('auth-screen').classList.remove('hidden');
        showToast('Owner session closed.', 'info');
    };

    window.quickRestock = async function(itemId, qty) {
        try {
            await api('/api/products/' + itemId + '/restock', {
                method: 'POST', body: JSON.stringify({ qty })
            });
            await loadStore();
            showToast('Restocked ' + qty + ' units successfully.', 'success');
        } catch (error) { showToast(error.message, 'warning'); }
    };

    window.handleAddProduct = async function(event) {
        event.preventDefault();
        const name = document.getElementById('new-prod-name').value.trim();
        const category = document.getElementById('new-prod-category').value;
        const price = Number(document.getElementById('new-prod-price').value);
        const stock = Number(document.getElementById('new-prod-stock').value);
        const minStock = Number(document.getElementById('new-prod-min').value);
        const costField = document.getElementById('new-prod-cost');
        const costPrice = costField && costField.value.trim() !== '' ? Number(costField.value) : null;
        try {
            await api('/api/products', {
                method: 'POST',
                body: JSON.stringify({ name, category, price, stock, minStock, costPrice })
            });
            event.target.reset();
            closeAddProductModal();
            await loadStore();
            showToast('Product saved to the database.', 'success');
        } catch (error) { showToast(error.message, 'warning'); }
    };

    window.deleteInventoryItem = async function(id) {
        try {
            await api('/api/products/' + id, { method: 'DELETE' });
            await loadStore();
            showToast('Product removed; earlier invoices are preserved.', 'info');
        } catch (error) { showToast(error.message, 'warning'); }
    };

    window.checkoutBill = async function() {
        if (checkoutPending) return;
        if (!cart.length) {
            showToast('Add at least one item to the cart.', 'warning');
            return;
        }
        checkoutPending = true;
        const payload = {
            items: cart.map(i => ({ id: i.id, qty: i.qty })),
            customer: document.getElementById('pos-customer-name').value.trim() || 'Walk-in Customer',
            payment: document.querySelector('input[name="paymentMode"]:checked')?.value || 'UPI / QR',
            includeGst: document.getElementById('pos-gst-toggle').checked
        };
        try {
            const invoice = await api('/api/invoices', {
                method: 'POST', body: JSON.stringify(payload)
            });
            cart = [];
            document.getElementById('pos-customer-name').value = '';
            await loadStore();
            openReceiptModal(invoice);
            if (invoice.paymentStatus === 'pending') {
                showToast('Online invoice created. Complete payment to confirm.', 'info');
                await window.payOnlineInvoice(invoice.id);
            } else {
                showToast('Invoice ' + invoice.id + ' saved and stock deducted.', 'success');
            }
        } catch (error) {
            showToast(error.message, 'warning');
            await loadStore().catch(() => {});
        } finally {
            checkoutPending = false;
        }
    };

    window.getTodayTotalSales = function() {
        return liveDashboard?.todaySales || 0;
    };

    window.updateOverviewKPIs = function() {
        const m = liveDashboard || {
            todaySales: 0, todayBills: 0, averageBill: 0,
            lowStock: 0, totalProducts: 0
        };
        document.getElementById('kpi-today-sales').textContent = formatINR(m.todaySales);
        document.getElementById('kpi-total-bills').textContent = m.todayBills;
        document.getElementById('kpi-avg-bill').textContent = formatINR(m.averageBill);
        document.getElementById('kpi-low-stock').textContent = m.lowStock;
        document.getElementById('kpi-total-skus').textContent = m.totalProducts;
        document.getElementById('kpi-net-profit').textContent =
            m.profitAvailable && m.grossProfit !== undefined ? formatINR(m.grossProfit) : 'N/A';
        document.getElementById('nav-low-stock-badge').textContent = m.lowStock;
    };

    window.editProductCost = async function(id) {
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
            const check = document.createElement('button');
            check.textContent = 'Check Status';
            check.className = 'retry-payment ml-1 px-2.5 py-1 rounded-lg border border-indigo-200 text-indigo-700 font-semibold text-xs';
            check.onclick = () => window.reconcileOnlineInvoice(bill.id);
            cell.appendChild(check);
        });
    }

    window.reconcileOnlineInvoice = async function(invoiceId) {
        const number = Number(String(invoiceId).replace('INV-', '')) - 1000;
        if (!Number.isInteger(number) || number < 1) return;
        try {
            const response = await api('/api/payments/reconcile/' + number, {
                method: 'POST', body: '{}'
            });
            await loadStore();
            if (response.verified) {
                openReceiptModal(response.invoice);
                showToast('Payment capture verified with provider.', 'success');
            } else {
                showToast('No captured payment yet; invoice remains pending.', 'info');
            }
        } catch (error) { showToast(error.message, 'warning'); }
    };

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

    window.callGeminiWithBackoff = async function(_systemInstruction, userQuery) {
        const result = await api('/api/assistant', {
            method: 'POST', body: JSON.stringify({ message: userQuery })
        });
        return result.reply;
    };

    window.addEventListener('DOMContentLoaded', async function() {
        try {
            const status = await api('/api/health');
            if (status.demoOnly) {
                const notice = document.createElement('div');
                notice.setAttribute('role', 'status');
                notice.className = 'fixed inset-x-0 top-0 z-[9999] text-center px-4 py-2 bg-amber-100 text-amber-900 border-b border-amber-300 text-xs font-semibold shadow';
                notice.textContent = 'Temporary hosted demo — inventory, invoices and sessions may reset after a restart. Do not enter real business data.';
                document.body.appendChild(notice);
            }
        } catch (_) { /* Optional availability banner. */ }
        try {
            const result = await api('/api/session');
            currentOwner = result.owner;
            await loadStore();
            enterDashboard();
        } catch (_) {
            // A fresh visitor sees the login screen; no fake local authentication.
        }
    });
})();
