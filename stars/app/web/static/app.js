// ==============================================================
// StarVault - Production Web & Telegram Mini App Engine v3.1.0
// ==============================================================

let currentUserId = null;
let currentTelegramId = null;
let currentProfile = null;
let activeTab = 'premium';
let currentActiveDepositId = null;

let selectedPremiumMonths = 3;
let selectedStarsCount = 250;
let depositAmount = 500;
let selectedDepositMethod = 'yoomoney';

// Wheel variables
let wheelCanvas = null;
let wheelCtx = null;
let isSpinning = false;
let currentWheelRotation = 0;
const WHEEL_SECTORS = [
    { label: "25 ⭐", color: "#7928ca", text: "#fff" },
    { label: "50 ₽", color: "#0070f3", text: "#fff" },
    { label: "10 ⭐", color: "#00df72", text: "#07080a" },
    { label: "-10%", color: "#f5a623", text: "#07080a" },
    { label: "100 ⭐", color: "#eb3678", text: "#fff" },
    { label: "+1 Спин", color: "#4d4dff", text: "#fff" },
    { label: "5 ⭐", color: "#00c7b7", text: "#07080a" },
    { label: "100 ₽", color: "#8b5cf6", text: "#fff" }
];

document.addEventListener('DOMContentLoaded', async () => {
    initWheelCanvas();
    await initTelegramAndUser();
    await loadTasksList();
    setupEventListeners();
});

// ==============================================================
// HAPTICS & TOAST NOTIFICATIONS
// ==============================================================
function tgHaptic(type = 'impact') {
    if (window.Telegram?.WebApp?.HapticFeedback) {
        try {
            if (type === 'impact') {
                window.Telegram.WebApp.HapticFeedback.impactOccurred('medium');
            } else if (type === 'success') {
                window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
            } else if (type === 'error') {
                window.Telegram.WebApp.HapticFeedback.notificationOccurred('error');
            }
        } catch (e) {}
    }
}

function showToast(message, type = 'success') {
    let container = document.getElementById('appToastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'appToastContainer';
        container.className = 'app-toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `app-toast ${type}`;
    const icon = type === 'success' ? '✅' : (type === 'error' ? '❌' : 'ℹ️');
    toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ==============================================================
// LUXURY GLASS MODAL DIALOG ENGINE
// ==============================================================
function showModal({ title, subtitle, icon, html, buttons = [], onClose = null }) {
    closeModal();

    const overlay = document.createElement('div');
    overlay.id = 'activeAppModalOverlay';
    overlay.className = 'app-modal-overlay';

    let btnsHtml = '';
    if (buttons && buttons.length > 0) {
        btnsHtml = `
            <div class="modal-actions-stack">
                ${buttons.map((b, idx) => `
                    <button class="${b.class || 'btn-modal-primary'}" id="modalBtn_${idx}">
                        ${b.text}
                    </button>
                `).join('')}
            </div>
        `;
    }

    overlay.innerHTML = `
        <div class="app-modal-card">
            <button class="modal-close-btn" id="modalCloseBtnTop">✕</button>
            ${icon ? `<div class="modal-header-icon">${icon}</div>` : ''}
            ${title ? `<div class="modal-title-main">${title}</div>` : ''}
            ${subtitle ? `<div class="modal-subtitle-text">${subtitle}</div>` : ''}
            ${html || ''}
            ${btnsHtml}
        </div>
    `;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('active'));

    const handleClose = () => {
        overlay.classList.remove('active');
        setTimeout(() => overlay.remove(), 250);
        if (onClose) onClose();
    };

    const closeBtn = overlay.querySelector('#modalCloseBtnTop');
    if (closeBtn) closeBtn.onclick = handleClose;

    overlay.onclick = (e) => {
        if (e.target === overlay) handleClose();
    };

    buttons.forEach((b, idx) => {
        const btnEl = overlay.querySelector(`#modalBtn_${idx}`);
        if (btnEl) {
            btnEl.onclick = async () => {
                if (b.onClick) {
                    const shouldClose = await b.onClick();
                    if (shouldClose !== false) handleClose();
                } else {
                    handleClose();
                }
            };
        }
    });
}

function closeModal() {
    const existing = document.getElementById('activeAppModalOverlay');
    if (existing) existing.remove();
}

// ==============================================================
// USER EXTRACTION & AUTHENTICATION
// ==============================================================
function parseParamsString(str) {
    if (!str) return {};
    let s = str.trim();
    if (s.startsWith('?') || s.startsWith('#')) s = s.substring(1);
    s = s.replace(/&amp;/gi, '&').replace(/&amp%3B/gi, '&');
    
    const map = {};
    const pairs = s.split('&');
    for (const p of pairs) {
        if (!p) continue;
        const idx = p.indexOf('=');
        let k = idx > -1 ? p.substring(0, idx) : p;
        let v = idx > -1 ? p.substring(idx + 1) : '';
        try {
            k = decodeURIComponent(k).replace(/^amp;/i, '').trim();
            v = decodeURIComponent(v).trim();
        } catch (e) {
            k = k.replace(/^amp;/i, '').trim();
        }
        map[k] = v;
    }
    return map;
}

function extractTelegramUser() {
    let resolvedTgId = null;
    let resolvedUsername = null;
    let resolvedFirstName = null;
    let resolvedAvatar = null;
    let resolvedRef = null;
    let resolvedIsPremium = null;
    let initDataRaw = '';

    const tg = window.Telegram?.WebApp;
    if (tg) {
        try {
            tg.ready();
            tg.expand();
            tg.setHeaderColor('#07080a');
            tg.setBackgroundColor('#07080a');
        } catch (e) {}

        const tgUser = tg.initDataUnsafe?.user;
        if (tgUser && tgUser.id) {
            resolvedTgId = parseInt(tgUser.id);
            resolvedUsername = tgUser.username || null;
            resolvedFirstName = `${tgUser.first_name || ''} ${tgUser.last_name || ''}`.trim() || null;
            resolvedAvatar = tgUser.photo_url || null;
            resolvedIsPremium = typeof tgUser.is_premium === 'boolean' ? tgUser.is_premium : null;
        }

        if (tg.initData) {
            initDataRaw = tg.initData;
        }
    }

    const hashMap = parseParamsString(window.location.hash || '');
    const searchMap = parseParamsString(window.location.search || '');

    const rawTgData = initDataRaw || hashMap.tgWebAppData || searchMap.tgWebAppData;
    if (rawTgData) {
        initDataRaw = rawTgData;
        const inner = parseParamsString(rawTgData);
        if (inner.user) {
            try {
                const u = typeof inner.user === 'string' ? JSON.parse(inner.user) : inner.user;
                if (u && u.id) {
                    resolvedTgId = resolvedTgId || parseInt(u.id);
                    resolvedUsername = resolvedUsername || u.username;
                    resolvedFirstName = resolvedFirstName || `${u.first_name || ''} ${u.last_name || ''}`.trim();
                    resolvedAvatar = resolvedAvatar || u.photo_url;
                }
            } catch (e) {
                try {
                    const u2 = JSON.parse(decodeURIComponent(inner.user));
                    if (u2 && u2.id) {
                        resolvedTgId = resolvedTgId || parseInt(u2.id);
                        resolvedUsername = resolvedUsername || u2.username;
                        resolvedFirstName = resolvedFirstName || `${u2.first_name || ''} ${u2.last_name || ''}`.trim();
                        resolvedAvatar = resolvedAvatar || u2.photo_url;
                    }
                } catch (e2) {}
            }
        }
        if (inner.start_param) resolvedRef = resolvedRef || inner.start_param;
    }

    const idParam = searchMap.tg_id || searchMap.user_id || searchMap.id || hashMap.tg_id || hashMap.user_id || hashMap.id;
    if (idParam && !isNaN(parseInt(idParam))) {
        resolvedTgId = resolvedTgId || parseInt(idParam);
    }
    resolvedUsername = resolvedUsername || searchMap.username || searchMap.tg_user || hashMap.username || hashMap.tg_user;
    resolvedFirstName = resolvedFirstName || searchMap.first_name || searchMap.tg_name || hashMap.first_name || hashMap.tg_name;
    resolvedAvatar = resolvedAvatar || searchMap.avatar_url || searchMap.photo_url || hashMap.avatar_url || hashMap.photo_url;
    resolvedRef = resolvedRef || searchMap.ref || searchMap.start_param || hashMap.ref || hashMap.start_param;

    return { resolvedTgId, resolvedUsername, resolvedFirstName, resolvedAvatar, resolvedRef, resolvedIsPremium, initDataRaw };
}

async function initTelegramAndUser() {
    const extracted = extractTelegramUser();
    let resolvedTgId = extracted.resolvedTgId;
    let resolvedUsername = extracted.resolvedUsername;
    let resolvedFirstName = extracted.resolvedFirstName;
    let resolvedAvatar = extracted.resolvedAvatar;
    let resolvedRef = extracted.resolvedRef;
    let resolvedIsPremium = extracted.resolvedIsPremium;
    let initDataRaw = extracted.initDataRaw;

    if (resolvedTgId) {
        const storedTg = localStorage.getItem('stars_tg_id');
        if (storedTg && parseInt(storedTg) !== resolvedTgId) {
            localStorage.removeItem('stars_tg_id');
            localStorage.removeItem('stars_user_id');
        }
    } else {
        const savedTg = localStorage.getItem('stars_tg_id');
        if (savedTg && !isNaN(parseInt(savedTg)) && parseInt(savedTg) !== 123456789 && parseInt(savedTg) !== 1) {
            resolvedTgId = parseInt(savedTg);
        }
    }

    if (!resolvedTgId) {
        let guestId = localStorage.getItem('stars_guest_tg_id');
        if (!guestId) {
            guestId = String(Math.floor(100000000 + Math.random() * 899999999));
            localStorage.setItem('stars_guest_tg_id', guestId);
        }
        resolvedTgId = parseInt(guestId);
        resolvedUsername = `guest_${guestId.slice(-4)}`;
        resolvedFirstName = 'Гость';
    }

    try {
        const res = await fetch('/api/user/auth-telegram', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-Telegram-Id': String(resolvedTgId)
            },
            body: JSON.stringify({
                telegram_id: resolvedTgId,
                username: resolvedUsername || `user_${resolvedTgId}`,
                first_name: resolvedFirstName || 'Пользователь',
                avatar_url: resolvedAvatar || null,
                referrer_id: resolvedRef || null,
                init_data: initDataRaw || null,
                is_premium: resolvedIsPremium
            })
        });
        const data = await res.json();
        if (data && data.user) {
            currentUserId = data.user.id;
            currentTelegramId = data.user.telegram_id;
            localStorage.setItem('stars_user_id', currentUserId);
            localStorage.setItem('stars_tg_id', currentTelegramId);
            currentProfile = data;
            renderUserProfile(data);
            return;
        }
    } catch (e) {
        console.error('Error authenticating user:', e);
    }
}

async function loadUserProfile() {
    if (!currentUserId && !currentTelegramId) return;
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch(`/api/user/me?user_id=${uid}`);
        const data = await res.json();
        if (data && data.user) {
            currentProfile = data;
            currentUserId = data.user.id;
            currentTelegramId = data.user.telegram_id;
            localStorage.setItem('stars_user_id', currentUserId);
            localStorage.setItem('stars_tg_id', currentTelegramId);
            renderUserProfile(data);
        }
    } catch (e) {
        console.error('Error loading user profile:', e);
    }
}

function renderAvatar(avatarWrapper, avatarUrl, name, tgId) {
    if (!avatarWrapper) return;
    const initial = ((name || 'U').trim()[0] || 'U').toUpperCase();
    const gradients = [
        'linear-gradient(135deg, #7928ca, #ff0080)',
        'linear-gradient(135deg, #0070f3, #00df72)',
        'linear-gradient(135deg, #ff4b1f, #ff9068)',
        'linear-gradient(135deg, #8a2387, #e94057)',
        'linear-gradient(135deg, #11998e, #38ef7d)'
    ];
    const gradIndex = Math.abs(Number(tgId || 0)) % gradients.length;
    const bgGrad = gradients[gradIndex];

    const defaultHtml = `
        <div style="width:100%; height:100%; border-radius:50%; background:${bgGrad}; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:16px; color:#fff; text-shadow:0 1px 4px rgba(0,0,0,0.4); box-shadow:0 0 14px rgba(144,85,255,0.4); user-select:none;">
            ${initial}
        </div>
    `;

    // Render default immediately so no broken image box or alt text is ever displayed
    avatarWrapper.innerHTML = defaultHtml;

    if (!avatarUrl || avatarUrl === 'null' || avatarUrl === 'undefined' || !avatarUrl.startsWith('http')) {
        return;
    }

    // Preload image in memory before inserting into DOM to guarantee zero broken icons
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
        avatarWrapper.innerHTML = `
            <img src="${avatarUrl}" alt="" 
                 style="width:100%; height:100%; object-fit:cover; border-radius:50%; display:block; border:1.5px solid rgba(255,255,255,0.18);" />
        `;
    };
    img.onerror = () => {
        avatarWrapper.innerHTML = defaultHtml;
    };
    img.src = avatarUrl;
}

function renderUserProfile(data) {
    if (!data || !data.user) return;
    
    // Top profile widget
    const nameEl = document.getElementById('userDisplayName');
    if (nameEl) nameEl.textContent = data.user.first_name || 'Пользователь';

    const handleEl = document.getElementById('userUsernameTag');
    if (handleEl) handleEl.textContent = `@${data.user.username || data.user.telegram_id}`;

    const balEl = document.getElementById('topBalanceValue');
    if (balEl) balEl.textContent = `${Number(data.user.balance || 0).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;

    const starsEl = document.getElementById('userStarsBalance');
    if (starsEl) starsEl.textContent = `${Math.round(data.user.stars_balance || 0)} Stars`;

    const spinsEl = document.getElementById('wheelSpinsAvailableLabel');
    if (spinsEl) spinsEl.textContent = `Спинов: ${data.user.spins_count || 0}`;

    // Render Avatar with safe fallback
    const avatarWrapper = document.getElementById('userAvatarWrapper');
    renderAvatar(avatarWrapper, data.user.avatar_url, data.user.first_name || data.user.username, data.user.telegram_id);

    // Default recipient inputs
    const cleanUname = `@${data.user.username || data.user.telegram_id}`;
    const premRec = document.getElementById('premiumRecipientInput');
    if (premRec && (!premRec.value || premRec.value === '@dx1one' || premRec.value === '@...')) {
        premRec.value = cleanUname;
    }

    const starsRec = document.getElementById('starsRecipientInput');
    if (starsRec && (!starsRec.value || starsRec.value === '@dx1one' || starsRec.value === '@...')) {
        starsRec.value = cleanUname;
    }

    // Referral link & percent
    const refInp = document.getElementById('refLinkInputField');
    if (refInp && data.referrals) {
        refInp.value = data.referrals.ref_link || `https://t.me/StarVaultRoBot?start=ref_${data.user.telegram_id}`;
    }

    const refPctLabel = document.getElementById('userRefPercentLabel');
    if (refPctLabel) {
        refPctLabel.textContent = `${data.user.ref_percent || 5}%`;
    }

    renderOrdersHistory(data.orders, data.transactions);
}

// ==============================================================
// Navigation Tabs
// ==============================================================
function switchTab(tabName) {
    tgHaptic('impact');
    activeTab = tabName;

    document.querySelectorAll('.screen-tab-pane').forEach(el => el.classList.remove('active'));
    const targetPane = document.getElementById(`tabPane${capitalize(tabName)}`);
    if (targetPane) targetPane.classList.add('active');

    document.querySelectorAll('.dock-tab-btn').forEach(el => {
        el.classList.remove('active');
        el.classList.remove('pill-highlight');
    });

    const dockBtn = document.getElementById(`dock${capitalize(tabName)}Btn`);
    if (dockBtn) {
        dockBtn.classList.add('active');
        dockBtn.classList.add('pill-highlight');
    }

    if (tabName === 'history') {
        loadUserProfile();
    } else if (tabName === 'tasks') {
        loadTasksList();
    } else if (tabName === 'wheel') {
        drawWheel(currentWheelRotation);
        loadGameHub();
    }
}

function capitalize(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
}

// ==============================================================
// 1. TELEGRAM PREMIUM LOGIC
// ==============================================================
function selectPremiumOption(months) {
    tgHaptic('impact');
    selectedPremiumMonths = months;

    document.querySelectorAll('.plan-card-row').forEach(row => row.classList.remove('selected'));
    const selectedRow = document.getElementById(`planRow_${months}`);
    if (selectedRow) selectedRow.classList.add('selected');

    const prices = { 3: '1 056.00', 6: '1 410.00', 12: '2 554.00' };
    const btn = document.getElementById('buyPremiumActionBtn');
    if (btn) {
        btn.textContent = `КУПИТЬ ЗА ${prices[months] || '1 056.00'} ₽`;
    }
}

async function handleBuyPremium() {
    tgHaptic('impact');
    const recipient = document.getElementById('premiumRecipientInput').value.trim();
    if (!recipient) {
        showToast('Пожалуйста, укажите @username получателя', 'error');
        return;
    }

    const prices = { 3: 1056.0, 6: 1410.0, 12: 2554.0 };
    const cost = prices[selectedPremiumMonths] || 1056.0;

    if (!currentProfile || currentProfile.user.balance < cost) {
        tgHaptic('error');
        showModal({
            icon: '💳',
            title: 'Недостаточно средств',
            subtitle: `На вашем балансе: ${currentProfile?.user?.balance?.toFixed(2) || 0} ₽\nСтоимость подписки: ${cost.toFixed(2)} ₽`,
            buttons: [
                { text: '➕ Пополнить баланс', class: 'btn-modal-primary', onClick: () => { switchTab('balance'); return true; } },
                { text: 'Отмена', class: 'btn-modal-secondary' }
            ]
        });
        return;
    }

    const btn = document.getElementById('buyPremiumActionBtn');
    btn.disabled = true;
    btn.textContent = '⏳ ОФОРМЛЕНИЕ И ДОСТАВКА В TON...';

    try {
        const res = await fetch('/api/order/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUserId || currentTelegramId,
                type: 'premium',
                amount: selectedPremiumMonths,
                recipient_username: recipient,
                payment_method: 'balance'
            })
        });
        const data = await res.json();

        if (!res.ok) {
            tgHaptic('error');
            showModal({
                icon: '❌',
                title: 'Ошибка заказа',
                subtitle: data.detail || 'Не удалось оформить заказ',
                buttons: [{ text: 'Закрыть', class: 'btn-modal-secondary' }]
            });
            return;
        }

        tgHaptic('success');
        showModal({
            icon: '👑',
            title: 'Премиум успешно оформлен!',
            subtitle: `Заказ ${data.order_uid}\nTelegram Premium (${selectedPremiumMonths} мес.) моментально отправлен на @${data.recipient_username} через блокчейн TON.`,
            buttons: [
                { text: '📜 Посмотреть в истории', class: 'btn-modal-primary', onClick: () => { switchTab('history'); return true; } }
            ]
        });
        await loadUserProfile();

    } catch (e) {
        console.error(e);
        tgHaptic('error');
        showToast('Ошибка при связи с сервером', 'error');
    } finally {
        btn.disabled = false;
        selectPremiumOption(selectedPremiumMonths);
    }
}

// ==============================================================
// 2. ЗВЁЗДЫ TELEGRAM
// ==============================================================
function setStarsCount(count) {
    tgHaptic('impact');
    selectedStarsCount = count;
    const slider = document.getElementById('starsSliderRange');
    if (slider) slider.value = count;
    updateStarsLiveDisplay(count);

    document.querySelectorAll('#tabPaneStars .chip-btn').forEach(chip => {
        if (parseInt(chip.textContent.replace(/\s/g, '')) === count) {
            chip.classList.add('active');
        } else {
            chip.classList.remove('active');
        }
    });
}

function updateStarsLiveDisplay(count) {
    const rate = 1.653;
    const totalRub = (count * rate).toFixed(2);

    const countDisplay = document.getElementById('starsSelectedCountDisplay');
    if (countDisplay) countDisplay.textContent = `${count.toLocaleString('ru-RU')} ⭐`;

    const subDisplay = document.getElementById('starsSelectedRubSubtitle');
    if (subDisplay) subDisplay.textContent = `Итого к оплате: ${totalRub} ₽`;

    const btn = document.getElementById('buyStarsActionBtn');
    if (btn) btn.textContent = `КУПИТЬ ЗА ${totalRub} ₽`;
}

async function handleBuyStars() {
    tgHaptic('impact');
    const recipient = document.getElementById('starsRecipientInput').value.trim();
    if (!recipient) {
        showToast('Пожалуйста, укажите @username получателя', 'error');
        return;
    }

    const rate = 1.653;
    const cost = selectedStarsCount * rate;

    if (!currentProfile || currentProfile.user.balance < cost) {
        tgHaptic('error');
        showModal({
            icon: '💳',
            title: 'Недостаточно средств',
            subtitle: `На вашем балансе: ${currentProfile?.user?.balance?.toFixed(2) || 0} ₽\nСтоимость Stars: ${cost.toFixed(2)} ₽`,
            buttons: [
                { text: '➕ Пополнить баланс', class: 'btn-modal-primary', onClick: () => { switchTab('balance'); return true; } },
                { text: 'Отмена', class: 'btn-modal-secondary' }
            ]
        });
        return;
    }

    const btn = document.getElementById('buyStarsActionBtn');
    btn.disabled = true;
    btn.textContent = '⏳ ОТПРАВКА ЗВЁЗД В TON...';

    try {
        const res = await fetch('/api/order/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUserId || currentTelegramId,
                type: 'stars',
                amount: selectedStarsCount,
                recipient_username: recipient,
                payment_method: 'balance'
            })
        });
        const data = await res.json();

        if (!res.ok) {
            tgHaptic('error');
            showModal({
                icon: '❌',
                title: 'Ошибка заказа Stars',
                subtitle: data.detail || 'Не удалось отправить Звёзды',
                buttons: [{ text: 'Закрыть', class: 'btn-modal-secondary' }]
            });
            return;
        }

        tgHaptic('success');
        showModal({
            icon: '⭐',
            title: 'Звёзды успешно доставлены!',
            subtitle: `Заказ ${data.order_uid}\n${selectedStarsCount.toLocaleString('ru-RU')} Telegram Stars отправлены на @${data.recipient_username} через TON смарт-контракт.`,
            buttons: [
                { text: '📜 Посмотреть в истории', class: 'btn-modal-primary', onClick: () => { switchTab('history'); return true; } }
            ]
        });
        await loadUserProfile();

    } catch (e) {
        console.error(e);
        tgHaptic('error');
        showToast('Ошибка при связи с сервером', 'error');
    } finally {
        btn.disabled = false;
        updateStarsLiveDisplay(selectedStarsCount);
    }
}

// ==============================================================
// 3. WHEEL OF FORTUNE (LUCKY SPIN)
// ==============================================================
function initWheelCanvas() {
    wheelCanvas = document.getElementById('fortuneWheelCanvas');
    if (!wheelCanvas) return;
    wheelCtx = wheelCanvas.getContext('2d');
    drawWheel(0);
}

function drawWheel(angleOffset) {
    if (!wheelCtx || !wheelCanvas) return;
    const ctx = wheelCtx;
    const width = wheelCanvas.width;
    const height = wheelCanvas.height;
    const cx = width / 2;
    const cy = height / 2;
    const radius = cx - 15;
    const numSectors = WHEEL_SECTORS.length;
    const arc = (2 * Math.PI) / numSectors;

    ctx.clearRect(0, 0, width, height);

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, radius + 8, 0, 2 * Math.PI);
    ctx.strokeStyle = '#00df72';
    ctx.lineWidth = 3;
    ctx.shadowColor = '#00df72';
    ctx.shadowBlur = 15;
    ctx.stroke();
    ctx.restore();

    for (let i = 0; i < numSectors; i++) {
        const startAngle = angleOffset + i * arc;
        const endAngle = startAngle + arc;

        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, radius, startAngle, endAngle);
        ctx.closePath();
        ctx.fillStyle = WHEEL_SECTORS[i].color;
        ctx.fill();
        ctx.strokeStyle = '#07080a';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(startAngle + arc / 2);
        ctx.textAlign = 'right';
        ctx.fillStyle = WHEEL_SECTORS[i].text;
        ctx.font = 'bold 13px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.shadowColor = 'rgba(0,0,0,0.5)';
        ctx.shadowBlur = 3;
        ctx.fillText(WHEEL_SECTORS[i].label, radius - 20, 5);
        ctx.restore();
    }

    ctx.beginPath();
    ctx.arc(cx, cy, 26, 0, 2 * Math.PI);
    ctx.fillStyle = '#07080a';
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = '#00df72';
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(cx, cy, 12, 0, 2 * Math.PI);
    ctx.fillStyle = '#00df72';
    ctx.fill();
}

async function handleSpinWheel() {
    if (isSpinning) return;
    tgHaptic('impact');

    const btn = document.getElementById('spinWheelActionBtn');
    btn.disabled = true;
    btn.textContent = '🌀 ВРАЩЕНИЕ...';
    isSpinning = true;

    try {
        const res = await fetch('/api/wheel/spin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: currentUserId || currentTelegramId })
        });
        const data = await res.json();

        if (!res.ok) {
            showToast(data.detail || 'Ошибка спина', 'error');
            btn.disabled = false;
            btn.textContent = 'КРУТИТЬ КОЛЕСО';
            isSpinning = false;
            return;
        }

        const targetSectorIndex = data.sector_index;
        const numSectors = WHEEL_SECTORS.length;
        const arc = (2 * Math.PI) / numSectors;

        const targetAngle = (3 * Math.PI / 2) - (targetSectorIndex * arc + arc / 2);
        const fullRotations = 6 * 2 * Math.PI;
        const finalRotation = fullRotations + targetAngle;

        const startRot = currentWheelRotation % (2 * Math.PI);
        const totalDelta = finalRotation - startRot;
        const duration = 4000;
        const startTime = performance.now();

        function easeOutCubic(t) {
            return 1 - Math.pow(1 - t, 3);
        }

        function animate(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = easeOutCubic(progress);

            currentWheelRotation = startRot + totalDelta * eased;
            drawWheel(currentWheelRotation);

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                isSpinning = false;
                btn.disabled = false;
                btn.textContent = 'КРУТИТЬ КОЛЕСО';
                tgHaptic('success');
                showWinModal(data.reward);
                loadUserProfile();
            }
        }

        requestAnimationFrame(animate);

    } catch (e) {
        console.error(e);
        showToast('Ошибка при вращении колеса', 'error');
        isSpinning = false;
        btn.disabled = false;
        btn.textContent = 'КРУТИТЬ КОЛЕСО';
    }
}

function showWinModal(reward) {
    showModal({
        icon: '🎉',
        title: 'Поздравляем с выигрышем!',
        subtitle: `Вы получили приз в Колесе Фортуны:`,
        html: `
            <div style="background: rgba(144, 85, 255, 0.12); border: 1px solid rgba(144, 85, 255, 0.4); border-radius: 16px; padding: 18px; text-align: center; margin: 12px 0;">
                <div style="font-size: 28px; font-weight: 800; color: #00df72; margin-bottom: 4px;">${reward.title}</div>
                <div style="font-size: 12px; color: var(--text-muted);">Награда зачислена на ваш профиль</div>
            </div>
        `,
        buttons: [
            { text: '🎁 Забрать в профиль', class: 'btn-modal-green', onClick: () => { loadUserProfile(); return true; } }
        ]
    });
}

// ==============================================================
// GAME HUB: DAILY STREAK, CASES, LEVELS AND REFERRAL LEADERBOARD
// ==============================================================
async function loadGameHub() {
    const uid = currentUserId || currentTelegramId;
    if (!uid) return;
    try {
        const [profileRes, casesRes] = await Promise.all([
            fetch(`/api/game/profile?user_id=${uid}`, {cache: 'no-store'}),
            fetch(`/api/cases?user_id=${uid}`, {cache: 'no-store'})
        ]);
        if (!profileRes.ok || !casesRes.ok) throw new Error('game API unavailable');
        const profile = await profileRes.json();
        const casesData = await casesRes.json();
        document.getElementById('gameLevelName').textContent = profile.vip ? `💎 ${profile.level.name}` : profile.level.name;
        document.getElementById('gameXpLabel').textContent = `${profile.level.xp} XP`;
        document.getElementById('gameStreakLabel').textContent = `🔥 ${profile.streak}`;
        document.getElementById('gameKeysLabel').textContent = `🗝 ${profile.case_keys}`;
        document.getElementById('gameXpProgress').style.width = `${profile.level.progress}%`;
        document.getElementById('caseBalanceLabel').textContent = `🗝 ${profile.case_keys}`;
        const vipButton = document.getElementById('vipBuyBtn');
        if (vipButton) vipButton.textContent = profile.vip ? `АКТИВЕН ДО ${profile.vip_until}` : 'ПОДКЛЮЧИТЬ';
        const daily = document.getElementById('dailyClaimBtn');
        daily.disabled = !profile.daily_available;
        daily.textContent = profile.daily_available ? '🎁 ЗАБРАТЬ ЕЖЕДНЕВНУЮ НАГРАДУ' : '✅ НАГРАДА СЕГОДНЯ ПОЛУЧЕНА';
        renderCases(casesData.cases || []);
    } catch (e) {
        console.error('Game hub:', e);
        const grid = document.getElementById('casesGrid');
        if (grid) grid.innerHTML = '<div class="case-loading">Не удалось загрузить сундуки</div>';
    }
}

function renderCases(cases) {
    const grid = document.getElementById('casesGrid');
    if (!grid) return;
    grid.innerHTML = cases.map(c => `
        <div class="case-card">
            <div class="case-icon">${c.icon}</div><div class="case-title">${c.title}</div>
            <div class="case-payments">
                <button class="case-open-btn" onclick="openCase('${c.id}','key')">🗝 ${c.prices.key}</button>
                <button class="case-open-btn" onclick="openCase('${c.id}','stars')">⭐ ${c.prices.stars}</button>
                <button class="case-open-btn" onclick="openCase('${c.id}','rub')">₽ ${c.prices.rub}</button>
            </div>
        </div>`).join('');
}

async function claimDailyReward() {
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch('/api/daily/claim', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:uid})});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Ошибка');
        tgHaptic('success'); showToast(data.message); await loadGameHub(); await loadUserProfile();
    } catch (e) { showToast(e.message, 'error'); }
}

async function openCase(caseId, paymentMethod) {
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch(`/api/cases/${caseId}/open`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:uid, payment_method:paymentMethod})});
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Не удалось открыть сундук');
        tgHaptic('success');
        showModal({icon:'🎉', title:'Сундук открыт!', subtitle:data.message, buttons:[{text:'Забрать приз',class:'btn-modal-green',onClick:()=>true}]});
        await loadGameHub(); await loadUserProfile();
    } catch (e) { tgHaptic('error'); showToast(e.message, 'error'); }
}

function showVipPurchase() {
    showModal({icon:'💎',title:'StarVault VIP',subtitle:'30 дней: двойной XP и один ключ каждый день',html:'<div style="text-align:center;color:var(--text-muted)">Выберите способ оплаты</div>',buttons:[
        {text:'⭐ 150 внутренних Stars',class:'btn-modal-primary',onClick:()=>{buyVip('stars');return true;}},
        {text:'₽ 199 с баланса',class:'btn-modal-green',onClick:()=>{buyVip('rub');return true;}}
    ]});
}

async function buyVip(paymentMethod) {
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch('/api/vip/buy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,payment_method:paymentMethod})});
        const data = await res.json(); if(!res.ok) throw new Error(data.detail || 'Ошибка покупки VIP');
        showToast(data.message); await loadGameHub(); await loadUserProfile();
    } catch(e) { showToast(e.message,'error'); }
}

async function showGameLeaderboard() {
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch(`/api/game/leaderboard?user_id=${uid}`); const data = await res.json();
        if(!res.ok) throw new Error(data.detail || 'Ошибка');
        const rows = data.leaders.length ? data.leaders.map((u,i)=>`<div class="referral-step-row"><div class="step-badge-num">${i+1}</div><div class="step-text-main">@${u.username}</div><b>${u.points} XP</b></div>`).join('') : '<div style="text-align:center;color:var(--text-muted)">Сезон только начался — будьте первым!</div>';
        showModal({icon:'🏅',title:`Сезон ${data.season}`,subtitle:`Ваше место: ${data.my_rank}, очки: ${data.my_points}. Призы: 10/5/3 ключа`,html:rows,buttons:[{text:'Продолжить играть',class:'btn-modal-primary',onClick:()=>true}]});
    } catch(e) { showToast(e.message,'error'); }
}

async function showReferralLeaderboard() {
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch(`/api/referrals/leaderboard?user_id=${uid}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Ошибка');
        const rows = data.leaders.map((u,i)=>`<div class="referral-step-row"><div class="step-badge-num">${i+1}</div><div class="step-text-main">@${u.username}</div><b>${u.referrals} 👥</b></div>`).join('');
        showModal({icon:'🏆',title:'Рейтинг рефералов',subtitle:`У вас ${data.my_referrals} приглашённых${data.next_goal ? `. Следующая цель: ${data.next_goal}` : ''}`,html:rows,buttons:[{text:'Пригласить друга',class:'btn-modal-primary',onClick:()=>{handleShareRefLink();return true;}}]});
    } catch(e) { showToast(e.message,'error'); }
}

// ==============================================================
// 4. TASKS / MISSIONS & REFERRAL HUB MODAL
// ==============================================================
async function loadTasksList() {
    if (!currentUserId && !currentTelegramId) return;
    const uid = currentUserId || currentTelegramId;
    try {
        const res = await fetch(`/api/tasks?user_id=${uid}`, { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok || !Array.isArray(data.tasks)) {
            throw new Error(data.detail || `HTTP ${res.status}`);
        }

        const container = document.getElementById('tasksListContainer');
        if (!container) return;

        const availableReward = data.tasks
            .filter(t => !t.is_completed)
            .reduce((sum, t) => sum + Number(t.reward_stars || 0), 0);
        const summary = document.getElementById('tasksSummaryBadge');
        if (summary) summary.textContent = `+${availableReward} ⭐ ДОСТУПНО`;

        if (data.tasks.length === 0) {
            container.innerHTML = '<div class="task-item-card">Сейчас доступных заданий нет. Загляните позже.</div>';
            return;
        }

        container.innerHTML = data.tasks.map(t => {
            const isReady = t.progress >= t.target;
            
            let actionBtnHtml = '';
            if (t.is_completed) {
                actionBtnHtml = `<button class="task-action-btn claimed" disabled>Выполнено ✅</button>`;
            } else if (t.action_type === 'link') {
                actionBtnHtml = `
                    <div class="task-actions-group">
                        <button class="task-action-btn" onclick="openChannelUrl('${t.action_url || ''}')">Канал 📢</button>
                        <button class="btn-task-verify" onclick="verifyTask('${t.id}')">Проверить 🔄</button>
                    </div>
                `;
            } else if (t.action_type === 'invite') {
                actionBtnHtml = `
                    <div class="task-actions-group">
                        <button class="task-action-btn" onclick="showReferralHubModal()">Друзья (${t.progress}/${t.target})</button>
                        ${isReady ? `<button class="btn-task-verify" onclick="verifyTask('${t.id}')">Забрать 🎁</button>` : ''}
                    </div>
                `;
            } else if (t.action_type === 'shop') {
                actionBtnHtml = `
                    <div class="task-actions-group">
                        <button class="task-action-btn" onclick="switchTab('premium')">Магазин 🛒</button>
                        ${isReady ? `<button class="btn-task-verify" onclick="verifyTask('${t.id}')">Забрать 🎁</button>` : ''}
                    </div>
                `;
            } else {
                actionBtnHtml = `
                    <button class="${isReady ? 'task-action-btn btn-green-gradient' : 'task-action-btn'}" onclick="verifyTask('${t.id}')">
                        ${isReady ? 'Забрать 🎁' : `${t.progress}/${t.target}`}
                    </button>
                `;
            }

            return `
                <div class="task-item-card">
                    <div class="task-left-info">
                        <div class="task-icon-box">${t.icon}</div>
                        <div class="task-text-box">
                            <span class="task-title">${t.title}</span>
                            <span class="task-desc">${t.desc}</span>
                            <span class="task-reward-pill">⭐ +${t.reward_stars} Stars</span>
                        </div>
                    </div>
                    ${actionBtnHtml}
                </div>
            `;
        }).join('');

    } catch (e) {
        console.error('Error loading tasks:', e);
        const container = document.getElementById('tasksListContainer');
        if (container) {
            container.innerHTML = '<div class="task-item-card">Не удалось обновить задания. <button class="btn-task-verify" onclick="loadTasksList()">Повторить</button></div>';
        }
    }
}

function openChannelUrl(url) {
    tgHaptic('impact');
    if (!url) {
        showToast('Это задание больше недоступно', 'error');
        loadTasksList();
        return;
    }
    if (window.Telegram?.WebApp?.openTelegramLink) {
        window.Telegram.WebApp.openTelegramLink(url);
    } else {
        window.open(url, '_blank');
    }
    showToast('Подпишитесь на канал и нажмите «Проверить» 🔄');
}

async function verifyTask(taskId) {
    tgHaptic('impact');
    try {
        const res = await fetch(`/api/tasks/${taskId}/claim`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: currentUserId || currentTelegramId })
        });
        const data = await res.json();

        if (!res.ok) {
            tgHaptic('error');
            showModal({
                icon: '⚠️',
                title: 'Задание не завершено',
                subtitle: data.detail || 'Проверка не пройдена. Выполните условия и попробуйте снова.',
                buttons: [
                    taskId === 'subscribe_channel' ? { text: '📢 Перейти в канал', class: 'btn-modal-primary', onClick: () => { openChannelUrl(); return true; } } : { text: 'Понятно', class: 'btn-modal-secondary' }
                ]
            });
            return;
        }

        tgHaptic('success');
        showModal({
            icon: '⭐',
            title: 'Награда получена!',
            subtitle: data.message || 'Вам успешно начислены Stars!',
            buttons: [
                { text: 'Отлично 🚀', class: 'btn-modal-green', onClick: () => { loadUserProfile(); loadTasksList(); return true; } }
            ]
        });
        await loadUserProfile();
        await loadTasksList();

    } catch (e) {
        showToast('Ошибка при проверке задания', 'error');
    }
}

// ==============================================================
// 5. REFERRAL HUB MODAL (С ИНСТРУКЦИЕЙ И ПРОГРЕССОМ)
// ==============================================================
function showReferralHubModal() {
    tgHaptic('impact');
    const refLink = currentProfile?.referrals?.ref_link || `https://t.me/StarVaultRoBot?start=ref_${currentTelegramId || ''}`;
    const refCount = currentProfile?.referrals?.count || 0;
    const refEarned = currentProfile?.referrals?.earned_rub || 0;
    const progressPct = Math.min(100, Math.round((refCount / 5) * 100));

    showModal({
        icon: '🤝',
        title: 'Реферальная программа',
        subtitle: 'Приглашайте друзей и получайте Stars + 5% с каждого заказа!',
        html: `
            <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px; padding: 14px; margin-bottom: 14px;">
                <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 700;">
                    <span style="color: #d1d9e6;">Приглашено друзей:</span>
                    <span style="color: #00df72;">${refCount} / 5</span>
                </div>
                <div class="modal-progress-track">
                    <div class="modal-progress-fill" style="width: ${progressPct}%;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-top: 6px;">
                    <span>Заработано: <b style="color:#fff;">${refEarned.toFixed(2)} ₽</b></span>
                    <span>Кэшбэк: <b style="color:#00df72;">5% пожизненно</b></span>
                </div>
            </div>

            <div style="margin-bottom: 14px;">
                <div class="referral-step-row">
                    <div class="step-badge-num">1</div>
                    <div class="step-text-main">Скопируйте вашу персональную реферальную ссылку ниже</div>
                </div>
                <div class="referral-step-row">
                    <div class="step-badge-num">2</div>
                    <div class="step-text-main">Отправьте другу в Telegram, группу или соцсеть</div>
                </div>
                <div class="referral-step-row">
                    <div class="step-badge-num">3</div>
                    <div class="step-text-main">Получите <b>+10 Stars</b> за 1 друга, <b>+100 Stars</b> за 5 друзей и <b>5%</b> с каждой его оплаты!</div>
                </div>
            </div>

            <div class="form-group" style="margin-bottom: 6px;">
                <input type="text" readonly class="input-clean-dark" id="modalRefLinkInput" value="${refLink}" style="font-size: 11px; text-align: center; color: #00df72;">
            </div>
        `,
        buttons: [
            {
                text: '✈️ Отправить другу в Telegram',
                class: 'btn-modal-primary',
                onClick: () => {
                    handleShareRefLink();
                    return true;
                }
            },
            {
                text: '📋 Скопировать ссылку',
                class: 'btn-modal-secondary',
                onClick: () => {
                    handleCopyRefLink();
                    showToast('Ссылка скопирована в буфер обмена!');
                    return false;
                }
            }
        ]
    });
}

// ==============================================================
// 6. РЕАЛЬНОЕ ПОПОЛНЕНИЕ БАЛАНСА ЧЕРЕЗ ШЛЮЗЫ
// ==============================================================
function setDepositAmount(amount) {
    tgHaptic('impact');
    depositAmount = amount;
    const inp = document.getElementById('depositAmountInput');
    if (inp) inp.value = amount;

    document.querySelectorAll('.chips-grid-3x2 .chip-btn').forEach(chip => {
        if (chip.textContent.replace(/\s/g, '').includes(`${amount}₽`)) {
            chip.classList.add('active');
        } else {
            chip.classList.remove('active');
        }
    });
}

function selectDepositMethod(method) {
    tgHaptic('impact');
    selectedDepositMethod = method;
    document.querySelectorAll('.payment-card-item').forEach(el => el.classList.remove('selected'));
    const item = document.getElementById(`payMethod_${method}`);
    if (item) item.classList.add('selected');
}

async function handleDepositSubmit() {
    tgHaptic('impact');
    const val = parseFloat(document.getElementById('depositAmountInput').value) || depositAmount;
    if (val < 50 || val > 100000) {
        showToast('Сумма пополнения должна быть от 50 до 100 000 ₽', 'error');
        return;
    }

    const btn = document.getElementById('confirmDepositActionBtn');
    btn.disabled = true;
    btn.textContent = '⏳ СОЗДАНИЕ СЧЁТА...';

    try {
        const res = await fetch('/api/balance/topup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUserId || currentTelegramId,
                amount_rub: val,
                payment_method: selectedDepositMethod
            })
        });
        const data = await res.json();

        if (!res.ok) {
            tgHaptic('error');
            showModal({
                icon: '❌',
                title: 'Ошибка создания счёта',
                subtitle: data.detail || 'Не удалось создать платёж',
                buttons: [{ text: 'Закрыть', class: 'btn-modal-secondary' }]
            });
            return;
        }

        currentActiveDepositId = data.deposit_id;
        const payUrl = data.payment_url;

        // Open custom deposit payment modal
        showModal({
            icon: '💳',
            title: `Счёт ${data.deposit_id}`,
            subtitle: `Сумма: ${val.toFixed(2)} ₽ (${selectedDepositMethod.toUpperCase()})\nПосле совершения перевода нажмите «Проверить оплату».`,
            buttons: [
                {
                    text: '🔗 Перейти к оплате ➔',
                    class: 'btn-modal-green',
                    onClick: () => {
                        if (window.Telegram?.WebApp?.openLink) {
                            window.Telegram.WebApp.openLink(payUrl);
                        } else {
                            window.open(payUrl, '_blank');
                        }
                        return false;
                    }
                },
                {
                    text: '🔄 Проверить оплату',
                    class: 'btn-modal-primary',
                    onClick: async () => {
                        await checkActiveDepositStatus();
                        return true;
                    }
                },
                {
                    text: 'Закрыть',
                    class: 'btn-modal-secondary'
                }
            ]
        });

        tgHaptic('success');

    } catch (e) {
        console.error(e);
        tgHaptic('error');
        showToast('Ошибка связи с сервером', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'ПЕРЕЙТИ К ОПЛАТЕ';
    }
}

async function checkActiveDepositStatus() {
    if (!currentActiveDepositId) return;
    tgHaptic('impact');

    try {
        const res = await fetch(`/api/balance/check/${currentActiveDepositId}`);
        const data = await res.json();

        if (data.status === 'paid') {
            tgHaptic('success');
            showModal({
                icon: '✅',
                title: 'Оплата зачислена!',
                subtitle: `Платёж ${currentActiveDepositId} успешно зачислен на ваш баланс!`,
                buttons: [
                    { text: '🌟 Перейти к покупкам', class: 'btn-modal-primary', onClick: () => { switchTab('premium'); return true; } }
                ]
            });
            await loadUserProfile();
        } else {
            showToast('⏳ Платёж ещё обрабатывается платёжной системой. Средства поступят в течение минуты.', 'info');
        }
    } catch (e) {
        showToast('Ошибка проверки статуса платежа', 'error');
    }
}

// ==============================================================
// 7. ИСТОРИЯ, РЕФЕРАЛЫ & ПРОМО
// ==============================================================
async function handleActivatePromo() {
    tgHaptic('impact');
    const code = document.getElementById('promoCodeInputField').value.trim();
    if (!code) {
        showToast('Введите промокод', 'error');
        return;
    }

    try {
        const res = await fetch('/api/user/promo/redeem', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUserId || currentTelegramId,
                code: code
            })
        });
        const data = await res.json();

        if (data.success) {
            tgHaptic('success');
            showModal({
                icon: '🎁',
                title: 'Промокод активирован!',
                subtitle: data.message,
                buttons: [
                    { text: 'Отлично 🎉', class: 'btn-modal-green', onClick: () => { loadUserProfile(); return true; } }
                ]
            });
            await loadUserProfile();
        } else {
            tgHaptic('error');
            showToast(data.message || 'Недействительный промокод', 'error');
        }
    } catch (e) {
        tgHaptic('error');
        showToast('Ошибка при проверке промокода', 'error');
    }
}

function handleCopyRefLink() {
    tgHaptic('impact');
    const link = currentProfile?.referrals?.ref_link || document.getElementById('refLinkInputField')?.value || `https://t.me/StarVaultRoBot?start=ref_${currentTelegramId || ''}`;
    navigator.clipboard.writeText(link);
    showToast('Реферальная ссылка скопирована!');
}

function handleShareRefLink() {
    tgHaptic('impact');
    const link = currentProfile?.referrals?.ref_link || document.getElementById('refLinkInputField')?.value || `https://t.me/StarVaultRoBot?start=ref_${currentTelegramId || ''}`;
    const shareText = encodeURIComponent('Покупай Telegram Stars и Premium со скидкой 5% в StarVault!');
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${shareText}`;
    if (window.Telegram?.WebApp?.openTelegramLink) {
        window.Telegram.WebApp.openTelegramLink(shareUrl);
    } else {
        window.open(shareUrl, '_blank');
    }
}

function renderOrdersHistory(orders, txs) {
    const container = document.getElementById('userOrdersListContainer');
    if (!container) return;

    if ((!orders || orders.length === 0) && (!txs || txs.length === 0)) {
        container.innerHTML = `<div style="font-size:12px; color:var(--text-muted); text-align:center; padding:12px;">История пока пуста</div>`;
        return;
    }

    let html = '';
    if (orders && orders.length > 0) {
        orders.slice(0, 6).forEach(o => {
            const isCompleted = o.status === 'completed';
            const statusLabel = isCompleted ? 'ВЫПОЛНЕН' : (o.status === 'pending' ? 'ОЖИДАЕТ' : o.status.toUpperCase());
            const statusColor = isCompleted ? '#00e676' : (o.status === 'pending' ? '#f5a623' : '#e53935');
            html += `
                <div style="background:var(--bg-input); border:1px solid var(--border-subtle); border-radius:var(--radius-sm); padding:12px 14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-size:13px; font-weight:700; color:#fff;">${o.item_amount} ${o.type === 'stars' ? '⭐ Stars' : 'мес. Premium'} ➔ @${o.recipient_username}</div>
                        <div style="font-size:10px; color:var(--text-muted); margin-top:3px;">${o.order_uid} • ${o.created_at || 'недавно'}</div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:13px; font-weight:800; color:#fff;">${o.cost_rub.toFixed(2)} ₽</div>
                        <div style="font-size:10px; color:${statusColor}; font-weight:700;">${statusLabel}</div>
                    </div>
                </div>
            `;
        });
    }

    if (txs && txs.length > 0) {
        txs.slice(0, 4).forEach(t => {
            const isStars = t.stars_amount && t.stars_amount > 0;
            html += `
                <div style="background:rgba(255,255,255,0.02); border-radius:var(--radius-sm); padding:8px 12px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center; font-size:11px;">
                    <span style="color:var(--text-muted);">${t.description}</span>
                    <span style="font-weight:700; color:${isStars ? '#f5a623' : (t.amount_rub >= 0 ? '#00e676' : '#e53935')};">
                        ${isStars ? `+${t.stars_amount} ⭐` : `${t.amount_rub >= 0 ? '+' : ''}${t.amount_rub.toFixed(2)} ₽`}
                    </span>
                </div>
            `;
        });
    }

    container.innerHTML = html;
}

function setupEventListeners() {
    const slider = document.getElementById('starsSliderRange');
    if (slider) {
        slider.addEventListener('input', (e) => {
            selectedStarsCount = parseInt(e.target.value);
            updateStarsLiveDisplay(selectedStarsCount);
        });
    }

    const depInp = document.getElementById('depositAmountInput');
    if (depInp) {
        depInp.addEventListener('input', (e) => {
            depositAmount = parseFloat(e.target.value) || 0;
        });
    }
}
