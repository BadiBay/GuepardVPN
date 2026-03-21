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
    // Send data back to the bot
    const data = JSON.stringify({
        action: 'buy',
        days: selectedDays,
        method: method
    });
    
    // Close modal
    closePayment();
    
    // Notify bot
    tg.sendData(data);
    
    // Or close app entirely
    // tg.close();
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
