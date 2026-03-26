// Initialize Telegram Web App
const tg = window.Telegram.WebApp;

// Expand to full height
tg.expand();
tg.ready();

// Set up user info if available
if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
    const user = tg.initDataUnsafe.user;
    const nameEl = document.getElementById('profile-name');
    if (nameEl) {
        nameEl.innerText = `@${user.username || user.first_name}`;
    }
}

// Parse URL Parameters passed from the Bot
const params = new URLSearchParams(window.location.search);
const pUuid = params.get('uuid');
const pEnd = params.get('end');
const pBal = params.get('bal');
const pTid = params.get('tid');

if (pTid) {
    document.getElementById('ref-link').value = `https://t.me/guepardvpn_bot?start=ref_${pTid}`;
}

const statusEl = document.getElementById('sub-status');
const endEl = document.getElementById('sub-end');
const keyEl = document.getElementById('vpn-key');

if (pEnd && pEnd !== 'None') {
    statusEl.innerHTML = '● Активна';
    statusEl.style.color = 'var(--success)';
    endEl.innerText = pEnd;
} else {
    statusEl.innerHTML = '❌ Неактивна';
    statusEl.style.color = 'var(--error)';
    endEl.innerText = '—';
}

if (pUuid && pUuid !== 'None') {
    // We assume the bot passes the full vless link or we construct it.
    // If bot passes uuid, we construct a dummy vless link or the bot passes the full link.
    // Let's assume the bot passes the panel URL hostname.
    const panelHost = "sub.guepardvpn.top:9684";
    const pbk = "xxx"; // Needs Reality public key if we building VLESS. 
    // It's better if bot passes a direct sub link
    keyEl.value = pUuid; // the bot will pass the full sub link in the uuid param
} else {
    keyEl.value = 'Подписка не активна';
}

// Navigation Logic
function navTo(viewId) {
    // Hide all views
    document.querySelectorAll('.view').forEach(v => {
        v.classList.remove('active');
    });
    // Show target view
    document.getElementById(viewId).classList.add('active');
    
    // Manage BackButton
    if (viewId === 'view-main') {
        tg.BackButton.hide();
    } else {
        tg.BackButton.show();
        // Since we bind multiple times if not careful, better use a global state or simple routing
        tg.BackButton.onClick(() => {
            navTo('view-main');
        });
    }
}

// Payment Modal Logic
let selectedDays = 0;

function openPayment(days) {
    selectedDays = days;
    const overlay = document.getElementById('payment-overlay');
    overlay.style.display = 'block';
    // Small timeout for transition
    setTimeout(() => {
        overlay.style.opacity = '1';
        document.getElementById('payment-sheet').classList.add('open');
    }, 10);
}

function closePayment() {
    const overlay = document.getElementById('payment-overlay');
    document.getElementById('payment-sheet').classList.remove('open');
    overlay.style.opacity = '0';
    setTimeout(() => {
        overlay.style.display = 'none';
    }, 300);
}

function processPayment(method) {
    const payload = `buy_${method}_${selectedDays}`;
    const botRawUrl = "https://t.me/guepardvpn_bot";
    tg.openTelegramLink(`${botRawUrl}?start=${payload}`);
    tg.close();
}

// Utility
function copyKey() {
    const input = document.getElementById('vpn-key');
    input.select();
    document.execCommand('copy');
    tg.showAlert('Ключ скопирован в буфер обмена!');
}

function copyRef() {
    const input = document.getElementById('ref-link');
    input.select();
    document.execCommand('copy');
    tg.showAlert('Ссылка скопирована!');
}

function shareRef() {
    const link = document.getElementById('ref-link').value;
    const shareText = "Попробуй GuepardVPN! Быстрый и надежный.";
    const tgShareLink = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(shareText)}`;
    tg.openTelegramLink(tgShareLink);
}

function navToSupport() {
    tg.openTelegramLink('https://t.me/guepardvpn_bot?start=support');
    tg.close();
}

function toggleDetails(id) {
    const el = document.getElementById(id);
    if (el.style.display === 'none') {
        el.style.display = 'block';
    } else {
        el.style.display = 'none';
    }
}
