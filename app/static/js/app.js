// FinBud - Financial Buddy Frontend Logic

let accounts = [];
let transactions = [];
let recurringRules = [];
let settings = {};
let forecastData = null;
let chartInstance = null;
let onlyActivityDays = false;
let previewRecords = [];
let txSearchTimeout = null;
let selectedTxIds = new Set();
let activeTab = 'forecast';
let dirtyTabs = new Set(['accounts', 'transactions', 'recurring']);
let txLoadedOnce = false;
let recLoadedOnce = false;

// Coalesce Lucide icon scans into a single animation-frame pass to eliminate DOM thrashing
let _lucideRafPending = false;
const _origLucideCreateIcons = (typeof lucide !== "undefined" && lucide.createIcons)
  ? lucide.createIcons.bind(lucide)
  : null;
if (_origLucideCreateIcons) {
  lucide.createIcons = function(opts) {
    if (opts && opts.root) {
      return _origLucideCreateIcons(opts);
    }
    if (_lucideRafPending) return;
    _lucideRafPending = true;
    requestAnimationFrame(() => {
      _lucideRafPending = false;
      _origLucideCreateIcons();
    });
  };
}

// Global Date Picker: Click anywhere on any date input to immediately open the dropdown calendar
document.addEventListener("click", (e) => {
  const dateInput = e.target.closest('input[type="date"]');
  if (dateInput && !dateInput.disabled && !dateInput.readOnly && typeof dateInput.showPicker === "function") {
    try {
      dateInput.showPicker();
    } catch (_) {
      // Ignored if already open or unsupported
    }
  }
});

// Helper: Format Currency
function formatCurrency(val) {
  const num = parseFloat(val) || 0;
  const sign = num < 0 ? "-" : "";
  const abs = Math.abs(num);
  return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Helper: Format Date String to friendly display
function formatDate(dStr) {
  if (!dStr) return "";
  const parts = dStr.split("-");
  if (parts.length === 3) {
    const d = new Date(parts[0], parts[1] - 1, parts[2]);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  return dStr;
}

// Show Toast Notification
function showToast(msg, type = "success") {
  const toast = document.getElementById("toast");
  const toastMsg = document.getElementById("toast-message");
  const toastIcon = document.getElementById("toast-icon");

  toastMsg.textContent = msg;
  if (type === "error") {
    toastIcon.setAttribute("data-lucide", "alert-circle");
    toastIcon.className = "w-4 h-4 text-rose-400";
  } else {
    toastIcon.setAttribute("data-lucide", "check-circle-2");
    toastIcon.className = "w-4 h-4 text-emerald-400";
  }
  lucide.createIcons();

  toast.classList.remove("hidden");
  setTimeout(() => {
    toast.classList.add("hidden");
  }, 3500);
}

// --- Futuristic HUD Theme Engine ---
const FUTURISTIC_THEMES = {
  cyber: {
    label: "CYBER",
    name: "Cyber Cyan & Electric Violet",
    stroke: "#00f0ff",
    gradTop: "rgba(0, 240, 255, 0.36)",
    gradMid: "rgba(139, 92, 246, 0.14)",
    gradBot: "rgba(0, 240, 255, 0.0)",
    tooltipBorder: "rgba(0, 240, 255, 0.4)"
  },
  quantum: {
    label: "QUANTUM",
    name: "Quantum Ultraviolet & Plasma",
    stroke: "#c084fc",
    gradTop: "rgba(192, 132, 252, 0.38)",
    gradMid: "rgba(56, 189, 248, 0.14)",
    gradBot: "rgba(192, 132, 252, 0.0)",
    tooltipBorder: "rgba(192, 132, 252, 0.4)"
  },
  solar: {
    label: "SOLAR",
    name: "Solar Gold & Carbon Cyberpunk",
    stroke: "#fbbf24",
    gradTop: "rgba(251, 191, 36, 0.36)",
    gradMid: "rgba(34, 211, 238, 0.12)",
    gradBot: "rgba(251, 191, 36, 0.0)",
    tooltipBorder: "rgba(251, 191, 36, 0.4)"
  },
  matrix: {
    label: "MATRIX",
    name: "Bio-Neon Matrix Mint",
    stroke: "#00ffa3",
    gradTop: "rgba(0, 255, 163, 0.36)",
    gradMid: "rgba(0, 210, 255, 0.13)",
    gradBot: "rgba(0, 255, 163, 0.0)",
    tooltipBorder: "rgba(0, 255, 163, 0.4)"
  }
};

function setFuturisticTheme(themeKey, silent = false) {
  const key = FUTURISTIC_THEMES[themeKey] ? themeKey : "cyber";
  const meta = FUTURISTIC_THEMES[key];
  document.documentElement.setAttribute("data-theme", key);
  try {
    localStorage.setItem("finbud_theme", key);
  } catch (_) {}

  const labelEl = document.getElementById("active-theme-label");
  if (labelEl) labelEl.textContent = meta.label;

  document.querySelectorAll("[data-theme-btn]").forEach(btn => {
    const isAct = btn.getAttribute("data-theme-btn") === key;
    if (isAct) {
      btn.classList.add("border-emerald-500/40", "bg-emerald-500/15", "text-white");
      btn.classList.remove("border-slate-800/80", "bg-slate-900/60", "text-slate-400");
    } else {
      btn.classList.remove("border-emerald-500/40", "bg-emerald-500/15", "text-white");
      btn.classList.add("border-slate-800/80", "bg-slate-900/60", "text-slate-400");
    }
  });

  if (forecastData) {
    renderForecastChart();
  }
  if (!silent) {
    showToast(`Theme switched to ${meta.name}`);
  }
}

// --- Initialization ---
document.addEventListener("DOMContentLoaded", async () => {
  // Restore saved futuristic theme (default: 'cyber')
  let savedTheme = "cyber";
  try {
    savedTheme = localStorage.getItem("finbud_theme") || "cyber";
  } catch (_) {}
  setFuturisticTheme(savedTheme, true);

  // Set default dates
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const todayStr = `${yyyy}-${mm}-${dd}`;

  // Default target date: +60 days
  const targetDate = new Date();
  targetDate.setDate(targetDate.getDate() + 60);
  const ty = targetDate.getFullYear();
  const tm = String(targetDate.getMonth() + 1).padStart(2, '0');
  const td = String(targetDate.getDate()).padStart(2, '0');

  document.getElementById("forecast-target-date").value = `${ty}-${tm}-${td}`;
  document.getElementById("tx-form-date").value = todayStr;
  document.getElementById("rec-form-start-date").value = todayStr;

  // Setup dropzone
  setupDropZone();

  // Load critical visible view (Forecast + Accounts) immediately in parallel
  updateTxSortUI();
  updateAccountsSortUI();
  updateRecurringSortUI();
  updateDailySortUI();
  await Promise.all([
    loadCurrentUser(),
    loadSettings(),
    loadAccounts(),
    loadForecast()
  ]);

  lucide.createIcons();

  // Prefetch secondary ledger & recurring data non-blockingly in the background
  setTimeout(() => {
    loadTransactions();
    loadRecurring();
  }, 100);
});

function applyCurrentUserPayload(me) {
  if (!me) return;
  const targets = [
    {
      nameEl: document.getElementById("user-display-name"),
      emailEl: document.getElementById("user-email-label"),
      badgeEl: document.getElementById("user-vault-badge"),
      avatarBox: document.getElementById("user-avatar-box"),
      actionBtn: document.getElementById("user-auth-action-btn"),
      actionLabel: null
    },
    {
      nameEl: document.getElementById("mob-user-display-name"),
      emailEl: document.getElementById("mob-user-email-label"),
      badgeEl: document.getElementById("mob-user-vault-badge"),
      avatarBox: document.getElementById("mob-user-avatar-box"),
      actionBtn: document.getElementById("mob-user-auth-action-btn"),
      actionLabel: document.getElementById("mob-user-auth-label")
    }
  ];

  targets.forEach(t => {
    if (t.nameEl) t.nameEl.textContent = me.name || "User";
    if (t.emailEl) t.emailEl.textContent = me.email || "";
    if (t.badgeEl) t.badgeEl.textContent = me.vault_label || "Vault";

    if (me.authenticated) {
      if (t.avatarBox) {
        if (me.avatar) {
          t.avatarBox.innerHTML = `<img src="${escapeHtml(me.avatar)}" alt="" class="w-full h-full object-cover">`;
        } else {
          const initials = (me.name || me.email || "U").trim().slice(0, 2).toUpperCase();
          t.avatarBox.textContent = initials;
        }
      }
      if (t.actionBtn) {
        t.actionBtn.href = "/logout";
        t.actionBtn.title = "Sign Out / Switch Vault";
        if (t.actionLabel) {
          t.actionBtn.innerHTML = `<i data-lucide="log-out" class="w-3.5 h-3.5"></i><span>Sign Out</span>`;
        } else {
          t.actionBtn.innerHTML = `<i data-lucide="log-out" class="w-3.5 h-3.5"></i>`;
        }
      }
    }
  });
}

async function loadCurrentUser() {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) return;
    const me = await res.json();
    applyCurrentUserPayload(me);
  } catch (e) {
    console.error("Failed to load user profile:", e);
  }
}

async function loadBootstrap() {
  try {
    const targetDate = document.getElementById("forecast-target-date")?.value || "";
    const startInput = document.getElementById("forecast-start-date");
    const startDate = (typeof isForecastStartCustom !== "undefined" && isForecastStartCustom && startInput) ? startInput.value : "";
    const includeRecurring = document.getElementById("forecast-include-recurring")?.checked ?? true;
    const showSettled = document.getElementById("tx-show-settled")?.checked || false;

    const params = new URLSearchParams();
    if (targetDate) params.append("target_date", targetDate);
    if (startDate) params.append("start_date", startDate);
    params.append("include_recurring", includeRecurring);
    params.append("tx_status", showSettled ? "" : "pending");
    params.append("tx_order_by", txSortBy || "date");
    params.append("tx_order_dir", txSortDir || "asc");

    const res = await fetch(`/api/bootstrap?${params.toString()}`);
    if (!res.ok) throw new Error("Bootstrap failed");
    const data = await res.json();

    // 1. User Profile
    if (data.me) applyCurrentUserPayload(data.me);

    // 2. Settings + Reserves
    settings = data.settings || {};
    const baseReserves = parseFloat(settings.base_cash_reserves || 0);
    const autoCalc = settings.auto_calculate_reserves !== "false";
    const autoCalcCheckbox = document.getElementById("settings-auto-calc-reserves");
    if (autoCalcCheckbox) autoCalcCheckbox.checked = autoCalc;
    const baseResInput = document.getElementById("settings-base-reserves");
    if (baseResInput) baseResInput.value = baseReserves.toFixed(2);
    const baseDateInput = document.getElementById("settings-base-date");
    if (baseDateInput) baseDateInput.value = settings.base_date || "2026-09-29";
    toggleAutoCalcReservesUI();

    if (data.cash_summary) {
      const effectiveTotal = autoCalc ? data.cash_summary.total_cash_reserves : baseReserves;
      const navBase = document.getElementById("nav-base-reserves");
      if (navBase) navBase.textContent = formatCurrency(effectiveTotal);
      const mobBase = document.getElementById("mob-nav-base-reserves");
      if (mobBase) mobBase.textContent = formatCurrency(effectiveTotal);
      const bannerTotal = document.getElementById("accounts-banner-total");
      if (bannerTotal) bannerTotal.textContent = formatCurrency(data.cash_summary.total_cash_reserves);
    }

    // 3. Accounts
    accounts = data.accounts || [];
    const txSelect = document.getElementById("tx-form-account");
    const filterSelect = document.getElementById("tx-filter-account");
    const recSelect = document.getElementById("rec-form-account");
    const currentFilterVal = filterSelect ? filterSelect.value : "";
    const optionsHtml = accounts.map(a => `<option value="${a.account_id}">${a.account_id} - ${a.account_name} (${a.type})</option>`).join("");
    if (txSelect) txSelect.innerHTML = optionsHtml;
    if (recSelect) recSelect.innerHTML = optionsHtml;
    if (filterSelect) {
      filterSelect.innerHTML = `<option value="">All Accounts</option>` + optionsHtml;
      filterSelect.value = currentFilterVal;
    }
    const accBadge = document.getElementById("nav-badge-acc");
    if (accBadge) accBadge.textContent = accounts.length;
    renderAccountsTable();
    updateAccBulkActionBar();

    // 4. Forecast
    forecastData = data.forecast;
    if (forecastData && forecastData.summary) {
      if (startInput && (!isForecastStartCustom || !startInput.value) && forecastData.summary.start_date) {
        startInput.value = forecastData.summary.start_date;
      }
      const rangeLabel = document.getElementById("forecast-range-label");
      if (rangeLabel) {
        rangeLabel.textContent = `${forecastData.summary.start_date} → ${forecastData.summary.target_date}`;
      }
      renderForecastKPIs();
      renderForecastChart();
      renderDailyLedgerTable();
    }

    // 5. Transactions
    transactions = data.transactions || [];
    const txBadge = document.getElementById("nav-badge-tx");
    if (txBadge) txBadge.textContent = transactions.filter(t => (t.status || 'pending') === 'pending').length;
    sortTransactionsInMemory();
    renderTransactionsTable();
    updateBulkActionBar();

    // 6. Recurring
    recurringRules = data.recurring || [];
    const recBadge = document.getElementById("nav-badge-rec");
    if (recBadge) recBadge.textContent = recurringRules.filter(r => r.active).length;
    renderRecurringTable();
  } catch (err) {
    console.warn("Bootstrap fallback to parallel load:", err);
    await Promise.all([
      loadCurrentUser(),
      loadSettings(),
      loadAccounts(),
      loadForecast(),
      loadTransactions(),
      loadRecurring()
    ]);
  }
}

function openMobileDrawer() {
  const drawer = document.getElementById("modal-mobile-drawer");
  if (!drawer) return;
  const todayCash = document.getElementById("nav-today-cash");
  const baseRes = document.getElementById("nav-base-reserves");
  const mobToday = document.getElementById("mob-nav-today-cash");
  const mobBase = document.getElementById("mob-nav-base-reserves");
  if (todayCash && mobToday) {
    mobToday.textContent = todayCash.textContent;
    mobToday.className = `text-base font-extrabold font-mono mt-0.5 block ${todayCash.textContent.startsWith("-") ? "text-rose-400" : "text-emerald-400"}`;
  }
  if (baseRes && mobBase) {
    mobBase.textContent = baseRes.textContent;
  }
  drawer.classList.remove("hidden");
  lucide.createIcons();
}

function closeMobileDrawer() {
  const drawer = document.getElementById("modal-mobile-drawer");
  if (drawer) drawer.classList.add("hidden");
}

// Tab Switching & Header Context
const VIEW_META = {
  'forecast': { title: 'Cash Flow Forecast', subtitle: 'Real-time accumulator & runway trajectory' },
  'transactions': { title: 'Transactions Ledger', subtitle: 'Search, filter, and reconcile pending & settled entries' },
  'accounts': { title: 'Chart of Accounts', subtitle: 'Grouped accounts, starting reserves, and net balances' },
  'account-detail': { title: 'Account Ledger Detail', subtitle: 'Individual account reconciliation & running balance' },
  'recurring': { title: 'Recurring Rules', subtitle: 'Automated paychecks, bills, and subscription schedules' },
  'import': { title: 'Excel Import & Export', subtitle: 'Multi-sheet workbook ingestion & full ledger backups' }
};

function switchTab(tabName) {
  activeTab = tabName;
  const tabs = ['forecast', 'transactions', 'accounts', 'account-detail', 'recurring', 'import'];
  tabs.forEach(t => {
    const view = document.getElementById(`view-${t}`);
    const btn = document.getElementById(`tab-btn-${t}`);
    const mobBtn = document.getElementById(`mob-tab-${t}`);
    const isActive = (t === tabName || (tabName === 'account-detail' && t === 'accounts'));

    if (view) {
      if (t === tabName) {
        view.classList.remove("hidden");
      } else {
        view.classList.add("hidden");
      }
    }
    if (btn) {
      if (isActive) {
        btn.classList.add("nav-item-active");
        btn.classList.remove("text-slate-400");
      } else {
        btn.classList.remove("nav-item-active");
        btn.classList.add("text-slate-400");
      }
    }
    if (mobBtn) {
      if (isActive) {
        mobBtn.className = "flex flex-col items-center py-1 px-2.5 rounded-xl text-emerald-400 bg-emerald-500/10";
      } else {
        mobBtn.className = "flex flex-col items-center py-1 px-2.5 rounded-xl text-slate-400 hover:text-slate-200";
      }
    }
  });

  const meta = VIEW_META[tabName] || VIEW_META['forecast'];
  const titleEl = document.getElementById("header-view-title");
  const subEl = document.getElementById("header-view-subtitle");
  if (titleEl) titleEl.textContent = meta.title;
  if (subEl) subEl.textContent = meta.subtitle;

  // Render deferred table if user switches to a tab that was updated in background
  if (tabName === 'accounts' && dirtyTabs.has('accounts')) {
    dirtyTabs.delete('accounts');
    renderAccountsTable();
  } else if (tabName === 'transactions') {
    if (!txLoadedOnce) {
      loadTransactions();
    } else if (dirtyTabs.has('transactions')) {
      dirtyTabs.delete('transactions');
      renderTransactionsTable();
    }
  } else if (tabName === 'recurring') {
    if (!recLoadedOnce) {
      loadRecurring();
    } else if (dirtyTabs.has('recurring')) {
      dirtyTabs.delete('recurring');
      renderRecurringTable();
    }
  }

  if (tabName === 'forecast' && chartInstance) {
    chartInstance.resize();
  }
  lucide.createIcons();
}

// --- Settings ---
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    settings = await res.json();
    const baseReserves = parseFloat(settings.base_cash_reserves || 0);
    const autoCalc = settings.auto_calculate_reserves !== "false";

    const autoCalcCheckbox = document.getElementById("settings-auto-calc-reserves");
    if (autoCalcCheckbox) autoCalcCheckbox.checked = autoCalc;
    document.getElementById("settings-base-reserves").value = baseReserves.toFixed(2);
    document.getElementById("settings-base-date").value = settings.base_date || "2026-09-29";

    toggleAutoCalcReservesUI();
    if (!autoCalc) {
      document.getElementById("nav-base-reserves").textContent = formatCurrency(baseReserves);
      const mobBase = document.getElementById("mob-nav-base-reserves");
      if (mobBase) mobBase.textContent = formatCurrency(baseReserves);
    }
  } catch (err) {
    console.error("Error loading settings:", err);
  }
}

function toggleAutoCalcReservesUI() {
  const checkbox = document.getElementById("settings-auto-calc-reserves");
  const isAuto = checkbox ? checkbox.checked : true;
  const input = document.getElementById("settings-base-reserves");
  const hint = document.getElementById("settings-reserves-hint");
  if (!input) return;

  if (isAuto) {
    input.disabled = true;
    input.classList.add("opacity-60", "cursor-not-allowed");
    if (hint) hint.innerHTML = `<span class="text-emerald-400 font-semibold">Auto-calculated</span> from starting balances of all active Cash accounts in the Chart of Accounts.`;
  } else {
    input.disabled = false;
    input.classList.remove("opacity-60", "cursor-not-allowed");
    if (hint) hint.textContent = "Starting baseline liquid cash balance (checking/savings/cash) before subsequent transactions.";
  }
}

function openSettingsModal() {
  openModal('modal-settings');
}

async function saveSettings() {
  const isAuto = document.getElementById("settings-auto-calc-reserves") ? document.getElementById("settings-auto-calc-reserves").checked : true;
  const baseReserves = parseFloat(document.getElementById("settings-base-reserves").value) || 0;
  const baseDate = document.getElementById("settings-base-date").value;

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_cash_reserves: baseReserves,
        base_date: baseDate,
        auto_calculate_reserves: isAuto
      })
    });
    if (!res.ok) throw new Error("Failed to save settings");
    closeModal('modal-settings');
    showToast("Forecast settings updated!");
    await loadSettings();
    await loadForecast();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// --- Accounts ---
let selectedAccountIds = new Set();
let accountModalContext = 'accounts'; // 'accounts' | 'transaction' | 'recurring'

const ACCOUNT_TYPE_NUMBER_RANGES = {
  "Cash": { start: 101, min: 101, max: 149 },
  "Assets": { start: 101, min: 101, max: 149 },
  "Accounts Receivable": { start: 151, min: 150, max: 199 },
  "Credit Card": { start: 201, min: 200, max: 229 },
  "Installment": { start: 231, min: 230, max: 239 },
  "Student Loans": { start: 241, min: 240, max: 249 },
  "Auto Loan": { start: 251, min: 250, max: 259 },
  "Mortgage": { start: 261, min: 260, max: 299 },
  "Revenues": { start: 401, min: 400, max: 499 },
  "Income": { start: 401, min: 400, max: 499 },
  "Expenses": { start: 501, min: 500, max: 529 },
  "Subscriptions": { start: 531, min: 530, max: 599 }
};

function suggestNextAccountId(type) {
  const normType = (type || "").trim();
  const cfg = ACCOUNT_TYPE_NUMBER_RANGES[normType] || { start: 101, min: 100, max: 999 };
  const allTakenIds = new Set((accounts || []).map(a => parseInt(a.account_id)).filter(id => !isNaN(id)));

  let maxMatching = 0;
  (accounts || []).forEach(a => {
    const id = parseInt(a.account_id);
    if (isNaN(id)) return;
    const aType = (a.type || "").trim();
    if (aType.toLowerCase() === normType.toLowerCase() || (id >= cfg.min && id <= cfg.max)) {
      if (id > maxMatching) maxMatching = id;
    }
  });

  let candidate = maxMatching > 0 ? maxMatching + 1 : cfg.start;
  while (allTakenIds.has(candidate)) {
    candidate++;
  }
  return candidate;
}

function onAccFormTypeChange() {
  const mode = document.getElementById("acc-form-mode")?.value || "create";
  if (mode !== "create") return;
  const type = document.getElementById("acc-form-type")?.value || "Cash";
  const nextId = suggestNextAccountId(type);
  const idInput = document.getElementById("acc-form-id");
  const hintEl = document.getElementById("acc-form-id-hint");
  if (idInput) idInput.value = nextId;
  if (hintEl) hintEl.textContent = `Suggested next ID for ${type}: #${nextId} (you can edit if desired)`;
}

function openAddAccountModalFromContext(context = 'accounts') {
  accountModalContext = context;
  if (context === 'transaction') {
    document.getElementById('modal-add-transaction')?.classList.add('hidden');
  } else if (context === 'recurring') {
    document.getElementById('modal-add-recurring')?.classList.add('hidden');
  }

  document.getElementById("acc-form-mode").value = "create";
  const idInput = document.getElementById("acc-form-id");
  if (idInput) idInput.disabled = false;
  const nameInput = document.getElementById("acc-form-name");
  if (nameInput) nameInput.value = "";
  const startBalInput = document.getElementById("acc-form-starting-balance");
  if (startBalInput) startBalInput.value = "0.00";
  const hiddenCheck = document.getElementById("acc-form-hidden");
  if (hiddenCheck) hiddenCheck.checked = false;
  const title = document.getElementById("modal-acc-title");
  if (title) title.textContent = "New Account";

  const typeSelect = document.getElementById("acc-form-type");
  if (typeSelect && !typeSelect.value) typeSelect.value = "Cash";
  onAccFormTypeChange();
  openModal('modal-add-account');
}

async function recalculateAllBalances() {
  try {
    const tasks = [loadAccounts(), loadForecast()];
    if (activeTab === 'transactions') {
      tasks.push(loadTransactions());
    } else {
      txLoadedOnce = false;
      dirtyTabs.add('transactions');
    }
    if (typeof currentDetailAccountId !== 'undefined' && currentDetailAccountId && activeTab === 'account-detail') {
      tasks.push(loadAccountDetail(currentDetailAccountId));
    }
    await Promise.all(tasks);
  } catch (err) {
    console.error("Error in recalculateAllBalances:", err);
  }
}

async function loadAccounts() {
  try {
    const res = await fetch("/api/accounts");
    accounts = await res.json();

    // Clean up selectedAccountIds for deleted items
    const currentIdSet = new Set(accounts.map(a => a.account_id));
    for (const id of selectedAccountIds) {
      if (!currentIdSet.has(id)) selectedAccountIds.delete(id);
    }

    // Populate dropdowns
    const txSelect = document.getElementById("tx-form-account");
    const filterSelect = document.getElementById("tx-filter-account");
    const recSelect = document.getElementById("rec-form-account");

    const currentFilterVal = filterSelect.value;

    let optionsHtml = accounts.map(a => `<option value="${a.account_id}">${a.account_id} - ${a.account_name} (${a.type})</option>`).join("");
    txSelect.innerHTML = optionsHtml;
    recSelect.innerHTML = optionsHtml;
    filterSelect.innerHTML = `<option value="">All Accounts</option>` + optionsHtml;
    filterSelect.value = currentFilterVal;

    // Compute Cash Reserves directly from loaded accounts (zero extra network trip)
    const totalCashReserves = round2(
      accounts
        .filter(a => String(a.type || "").toLowerCase() === "cash" && !a.is_hidden)
        .reduce((sum, a) => sum + (parseFloat(a.starting_balance) || 0), 0)
    );
    const bannerTotal = document.getElementById("accounts-banner-total");
    if (bannerTotal) bannerTotal.textContent = formatCurrency(totalCashReserves);
    if (settings.auto_calculate_reserves !== "false") {
      document.getElementById("nav-base-reserves").textContent = formatCurrency(totalCashReserves);
      const mobBase = document.getElementById("mob-nav-base-reserves");
      if (mobBase) mobBase.textContent = formatCurrency(totalCashReserves);
    }

    // Update sidebar badge
    const accBadge = document.getElementById("nav-badge-acc");
    if (accBadge) accBadge.textContent = accounts.length;

    if (activeTab === 'accounts') {
      dirtyTabs.delete('accounts');
      renderAccountsTable();
    } else {
      dirtyTabs.add('accounts');
    }
    updateAccBulkActionBar();
  } catch (err) {
    console.error("Error loading accounts:", err);
  }
}

let accSortBy = "account_id";
let accSortDir = "asc";
let accountsViewMode = "grouped"; // 'grouped' | 'flat'

function setAccountsViewMode(mode) {
  accountsViewMode = mode;
  const gBtn = document.getElementById("btn-acc-view-grouped");
  const fBtn = document.getElementById("btn-acc-view-flat");
  if (gBtn && fBtn) {
    if (mode === "grouped") {
      gBtn.className = "px-2.5 py-1 rounded-lg font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition";
      fBtn.className = "px-2.5 py-1 rounded-lg font-medium text-slate-400 hover:text-white border border-transparent transition";
    } else {
      fBtn.className = "px-2.5 py-1 rounded-lg font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition";
      gBtn.className = "px-2.5 py-1 rounded-lg font-medium text-slate-400 hover:text-white border border-transparent transition";
    }
  }
  renderAccountsTable();
}

function classifyAccountGroup(a) {
  const id = parseInt(a.account_id) || 0;
  const t = (a.type || "").toLowerCase();
  if (t.includes("cash") || t.includes("asset") || (id >= 100 && id <= 149)) {
    return { key: "100_cash", label: "100s · Cash & Liquid Reserves", color: "text-emerald-400", badge: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" };
  }
  if (t.includes("receivable") || (id >= 150 && id <= 199)) {
    return { key: "150_ar", label: "150s · Accounts Receivable", color: "text-teal-400", badge: "bg-teal-500/15 text-teal-300 border-teal-500/30" };
  }
  if (t.includes("credit card") || t.includes("loan") || t.includes("mortgage") || t.includes("installment") || (id >= 200 && id <= 299)) {
    return { key: "200_debt", label: "200s · Credit Cards & Loan Liabilities", color: "text-amber-400", badge: "bg-amber-500/15 text-amber-300 border-amber-500/30" };
  }
  if (t.includes("revenue") || t.includes("income") || (id >= 400 && id <= 499)) {
    return { key: "400_rev", label: "400s · Revenues & Income Streams", color: "text-cyan-400", badge: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30" };
  }
  if (t.includes("expense") || t.includes("subscription") || (id >= 500 && id <= 599)) {
    return { key: "500_exp", label: "500s · Operating Expenses & Subscriptions", color: "text-rose-400", badge: "bg-rose-500/15 text-rose-300 border-rose-500/30" };
  }
  return { key: "900_other", label: "Other Accounts", color: "text-slate-300", badge: "bg-slate-800 text-slate-300 border-slate-700" };
}

function toggleSortAccounts(col) {
  if (accSortBy === col) {
    accSortDir = accSortDir === "asc" ? "desc" : "asc";
  } else {
    accSortBy = col;
    accSortDir = (col === "account_name" || col === "type" || col === "account_id") ? "asc" : "desc";
  }
  updateAccountsSortUI();
  renderAccountsTable();
}

function updateAccountsSortUI() {
  const cols = ["account_id", "account_name", "type", "starting_balance", "is_hidden", "transaction_count", "total_amount"];
  cols.forEach(c => {
    const icon = document.getElementById(`sort-icon-acc-${c}`);
    const th = document.getElementById(`th-acc-${c}`);
    if (!icon) return;
    if (accSortBy === c) {
      icon.textContent = accSortDir === "asc" ? "▲" : "▼";
      icon.className = "text-emerald-400 font-bold text-[11px]";
      if (th) th.className = "text-emerald-400 font-bold";
    } else {
      icon.textContent = "↕";
      icon.className = "text-slate-600 group-hover:text-slate-400 text-[11px]";
      if (th) th.className = "text-slate-400 group-hover:text-white";
    }
  });
}

function sortAccountsInMemory() {
  if (!accounts || accounts.length === 0) return;
  const dir = accSortDir === "asc" ? 1 : -1;
  accounts.sort((a, b) => {
    if (accSortBy === "account_id") {
      return ((a.account_id || 0) - (b.account_id || 0)) * dir;
    }
    if (accSortBy === "account_name") {
      return (a.account_name || "").localeCompare(b.account_name || "") * dir;
    }
    if (accSortBy === "type") {
      return (a.type || "").localeCompare(b.type || "") * dir;
    }
    if (accSortBy === "starting_balance") {
      return ((parseFloat(a.starting_balance) || 0) - (parseFloat(b.starting_balance) || 0)) * dir;
    }
    if (accSortBy === "is_hidden") {
      const hA = (a.is_hidden === 1 || a.is_hidden === true) ? 1 : 0;
      const hB = (b.is_hidden === 1 || b.is_hidden === true) ? 1 : 0;
      return (hA - hB) * dir;
    }
    if (accSortBy === "transaction_count") {
      return ((a.transaction_count || 0) - (b.transaction_count || 0)) * dir;
    }
    if (accSortBy === "total_amount") {
      return ((parseFloat(a.total_amount) || 0) - (parseFloat(b.total_amount) || 0)) * dir;
    }
    return 0;
  });
}

function onToggleAccSelect(accId, isChecked) {
  if (isChecked) {
    selectedAccountIds.add(accId);
  } else {
    selectedAccountIds.delete(accId);
  }
  updateAccBulkActionBar();
  const row = document.getElementById(`acc-row-${accId}`);
  if (row) {
    if (isChecked) {
      row.classList.add("bg-emerald-950/25");
    } else {
      row.classList.remove("bg-emerald-950/25");
    }
  }
}

function toggleSelectAllAccounts(isChecked) {
  if (isChecked) {
    (accounts || []).forEach(a => selectedAccountIds.add(a.account_id));
  } else {
    selectedAccountIds.clear();
  }
  renderAccountsTable();
  updateAccBulkActionBar();
}

function clearSelectedAccounts() {
  selectedAccountIds.clear();
  const selectAll = document.getElementById("acc-select-all");
  if (selectAll) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  }
  renderAccountsTable();
  updateAccBulkActionBar();
}

function updateAccBulkActionBar() {
  const bar = document.getElementById("acc-bulk-bar");
  const countEl = document.getElementById("acc-bulk-count");
  const selectAll = document.getElementById("acc-select-all");
  const count = selectedAccountIds.size;
  const total = (accounts || []).length;

  if (countEl) countEl.textContent = count;
  if (bar) {
    if (count > 0) {
      bar.classList.remove("hidden");
      bar.classList.add("flex");
    } else {
      bar.classList.add("hidden");
      bar.classList.remove("flex");
    }
  }
  if (selectAll && total > 0) {
    selectAll.checked = (count === total);
    selectAll.indeterminate = (count > 0 && count < total);
  }
}

async function bulkDeleteSelectedAccounts() {
  const ids = Array.from(selectedAccountIds);
  if (ids.length === 0) return;

  const count = ids.length;
  const confirmed = await askConfirm(
    `Delete ${count} Account${count > 1 ? 's' : ''}?`,
    `Are you sure you want to delete ${count} selected account(s)? This will also remove any linked transactions and recurring rules for these accounts.`,
    `Delete ${count} Account${count > 1 ? 's' : ''}`
  );
  if (!confirmed) return;

  try {
    const res = await fetch("/api/accounts/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_ids: ids, cascade: true })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to delete selected accounts");
    }
    const data = await res.json();

    // If active detail view was deleted, close detail view
    if (typeof currentDetailAccountId !== 'undefined' && currentDetailAccountId && selectedAccountIds.has(currentDetailAccountId)) {
      closeAccountDetail();
    }

    selectedAccountIds.clear();
    updateAccBulkActionBar();
    showToast(`Deleted ${data.deleted_accounts || count} account(s)`);
    await recalculateAllBalances();
  } catch (err) {
    console.error("Bulk delete accounts error:", err);
    showToast(err.message || "Bulk deletion failed", "error");
  }
}

function renderSingleAccountRow(a) {
  const total = parseFloat(a.total_amount) || 0;
  const totalColor = total >= 0 ? "text-emerald-400" : "text-rose-400";
  const isHidden = a.is_hidden === 1 || a.is_hidden === true;
  const startBal = parseFloat(a.starting_balance) || 0;
  const isCash = (a.type || "").toLowerCase().includes("cash");
  const isSelected = selectedAccountIds.has(a.account_id);
  const startBalDisplay = startBal !== 0 
    ? `<span class="font-bold ${startBal > 0 ? 'text-emerald-400' : 'text-slate-300'}">${formatCurrency(startBal)}</span>`
    : `<span class="text-slate-600 font-sans text-xs">$0.00</span>`;

  const statusBtn = isHidden
    ? `<button onclick="event.stopPropagation(); toggleAccountHidden(${a.account_id})" class="inline-flex items-center space-x-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full bg-slate-800/90 text-amber-400 hover:text-emerald-300 border border-amber-500/30 hover:border-emerald-500/40 transition" title="Currently excluded from calculations. Click to include.">
         <i data-lucide="eye-off" class="w-3.5 h-3.5"></i>
         <span>Excluded</span>
       </button>`
    : `<button onclick="event.stopPropagation(); toggleAccountHidden(${a.account_id})" class="inline-flex items-center space-x-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 hover:bg-amber-500/10 hover:text-amber-300 border border-emerald-500/30 hover:border-amber-500/30 transition" title="Active in calculations. Click to exclude.">
         <i data-lucide="eye" class="w-3.5 h-3.5"></i>
         <span>Included</span>
       </button>`;

  return `
    <tr id="acc-row-${a.account_id}" onclick="openAccountDetail(${a.account_id})" class="hover:bg-slate-800/50 transition cursor-pointer group ${isSelected ? 'bg-emerald-950/25' : ''}" title="Click to open full editable ledger for Account #${a.account_id}">
      <td class="py-3 px-3 text-center" onclick="event.stopPropagation()">
        <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="onToggleAccSelect(${a.account_id}, this.checked)" class="rounded border-slate-700 bg-slate-950 text-emerald-500 focus:ring-0 w-3.5 h-3.5 cursor-pointer" title="Select account #${a.account_id}">
      </td>
      <td class="py-3 px-4 font-semibold font-mono">
        <button type="button" onclick="openAccountDetail(${a.account_id})" class="text-emerald-400 hover:text-emerald-300 font-bold inline-flex items-center space-x-1 group/btn hover:underline cursor-pointer" title="Open Account #${a.account_id} Ledger">
          <span>#${a.account_id}</span>
          <i data-lucide="arrow-up-right" class="w-3.5 h-3.5 opacity-60 group-hover/btn:opacity-100"></i>
        </button>
      </td>
      <td class="py-3 px-4 font-sans text-slate-200">
        <button type="button" onclick="openAccountDetail(${a.account_id})" class="text-left font-medium text-slate-100 hover:text-emerald-300 transition cursor-pointer flex items-center space-x-2" title="Open Account #${a.account_id} Ledger">
          <span class="group-hover:text-emerald-300 font-semibold transition">${escapeHtml(a.account_name)}</span>
          ${isCash ? '<span class="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-mono font-semibold">CASH</span>' : ''}
        </button>
      </td>
      <td class="py-3 px-4 font-sans">
        <span class="inline-flex px-2 py-0.5 rounded-md text-[11px] font-medium ${getCategoryBadgeStyle(a.type)}">
          ${escapeHtml(a.type || 'General')}
        </span>
      </td>
      <td class="py-3 px-4 text-right">${startBalDisplay}</td>
      <td class="py-3 px-4 text-center font-sans">${statusBtn}</td>
      <td class="py-3 px-4 text-right text-slate-300 font-mono">${a.transaction_count}</td>
      <td class="py-3 px-4 text-right font-bold font-mono ${totalColor}">${formatCurrency(total)}</td>
      <td class="py-3 px-4 text-right space-x-1.5 whitespace-nowrap">
        <button type="button" onclick="openAccountDetail(${a.account_id})" class="inline-flex items-center space-x-1 px-2.5 py-1 text-xs font-semibold rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30 transition cursor-pointer shadow-sm" title="View & edit account ledger and transactions">
          <i data-lucide="book-open" class="w-3.5 h-3.5"></i>
          <span>View Ledger</span>
        </button>
        <button type="button" onclick="event.stopPropagation(); editAccount(${a.account_id})" class="text-slate-400 hover:text-emerald-400 p-1.5 rounded-lg hover:bg-slate-800 transition" title="Edit account settings">
          <i data-lucide="edit-3" class="w-4 h-4"></i>
        </button>
        <button type="button" onclick="event.stopPropagation(); deleteAccount(${a.account_id})" class="text-slate-400 hover:text-rose-400 p-1.5 rounded-lg hover:bg-slate-800 transition" title="Delete account">
          <i data-lucide="trash-2" class="w-4 h-4"></i>
        </button>
      </td>
    </tr>
  `;
}

function renderAccountsTable() {
  const tbody = document.getElementById("accounts-table-tbody");
  sortAccountsInMemory();

  const searchQuery = (document.getElementById("acc-search-input")?.value || "").trim().toLowerCase();
  const filtered = (accounts || []).filter(a => {
    if (!searchQuery) return true;
    return String(a.account_id).includes(searchQuery) ||
      (a.account_name || "").toLowerCase().includes(searchQuery) ||
      (a.type || "").toLowerCase().includes(searchQuery);
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center py-12 text-slate-500 font-sans">No accounts match your filter.</td></tr>`;
    updateAccBulkActionBar();
    return;
  }

  if (accountsViewMode === "grouped") {
    const groupsMap = new Map();
    const groupOrder = ["100_cash", "150_ar", "200_debt", "400_rev", "500_exp", "900_other"];
    groupOrder.forEach(k => groupsMap.set(k, { meta: null, items: [] }));

    filtered.forEach(a => {
      const g = classifyAccountGroup(a);
      if (!groupsMap.has(g.key)) {
        groupsMap.set(g.key, { meta: g, items: [] });
      }
      const entry = groupsMap.get(g.key);
      entry.meta = g;
      entry.items.push(a);
    });

    let html = "";
    for (const [, group] of groupsMap.entries()) {
      if (!group.items.length || !group.meta) continue;
      const sumStart = group.items.reduce((acc, item) => acc + (parseFloat(item.starting_balance) || 0), 0);
      const sumNet = group.items.reduce((acc, item) => acc + (parseFloat(item.total_amount) || 0), 0);
      const sumTx = group.items.reduce((acc, item) => acc + (parseInt(item.transaction_count) || 0), 0);
      const netColor = sumNet >= 0 ? "text-emerald-400" : "text-rose-400";

      html += `
        <tr class="bg-slate-950/85 border-y border-slate-800/90 select-none">
          <td colspan="4" class="py-2 px-4 font-sans">
            <div class="flex items-center space-x-2">
              <span class="text-xs font-bold ${group.meta.color}">${group.meta.label}</span>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-slate-900 text-slate-400 border border-slate-800">${group.items.length} account${group.items.length > 1 ? 's' : ''}</span>
            </div>
          </td>
          <td class="py-2 px-4 text-right font-mono text-[11px] text-slate-400">
            ${sumStart !== 0 ? `Start: <b class="text-slate-200">${formatCurrency(sumStart)}</b>` : ''}
          </td>
          <td class="py-2 px-4"></td>
          <td class="py-2 px-4 text-right font-mono text-[11px] text-slate-400">${sumTx} tx</td>
          <td class="py-2 px-4 text-right font-mono text-xs font-bold ${netColor}">${formatCurrency(sumNet)}</td>
          <td class="py-2 px-4"></td>
        </tr>
      `;
      html += group.items.map(a => renderSingleAccountRow(a)).join("");
    }
    tbody.innerHTML = html;
  } else {
    tbody.innerHTML = filtered.map(a => renderSingleAccountRow(a)).join("");
  }

  lucide.createIcons();
  updateAccBulkActionBar();
}

async function toggleAccountHidden(accId) {
  try {
    const res = await fetch(`/api/accounts/${accId}/toggle-hidden`, { method: "PUT" });
    if (!res.ok) throw new Error("Toggle failed");
    const updated = await res.json();
    const isNowHidden = updated.is_hidden === 1 || updated.is_hidden === true;
    showToast(isNowHidden ? `Account #${accId} excluded from cash calculations` : `Account #${accId} included in cash calculations`);
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function editAccount(accId) {
  const acc = (accounts || []).find(a => a.account_id === accId);
  if (!acc) return;
  accountModalContext = 'accounts';
  document.getElementById("acc-form-mode").value = "edit";
  const origIdEl = document.getElementById("acc-form-orig-id");
  if (origIdEl) origIdEl.value = acc.account_id;
  document.getElementById("acc-form-id").value = acc.account_id;
  document.getElementById("acc-form-id").disabled = false;
  document.getElementById("acc-form-name").value = acc.account_name;
  document.getElementById("acc-form-type").value = acc.type || "Cash";
  document.getElementById("acc-form-starting-balance").value = (acc.starting_balance || 0).toFixed(2);
  document.getElementById("acc-form-hidden").checked = (acc.is_hidden === 1 || acc.is_hidden === true);
  document.getElementById("modal-acc-title").textContent = `Edit Account #${acc.account_id}`;
  const hintEl = document.getElementById("acc-form-id-hint");
  if (hintEl) hintEl.textContent = `Current ID: #${acc.account_id} (editing this updates all linked transactions & schedules)`;
  openModal('modal-add-account');
}

async function submitAccountForm() {
  const mode = document.getElementById("acc-form-mode").value;
  const origAccId = parseInt(document.getElementById("acc-form-orig-id")?.value);
  const accId = parseInt(document.getElementById("acc-form-id").value);
  const name = document.getElementById("acc-form-name").value.trim();
  const type = document.getElementById("acc-form-type").value;
  const startBal = parseFloat(document.getElementById("acc-form-starting-balance").value) || 0;
  const isHidden = document.getElementById("acc-form-hidden").checked;

  if (!accId || !name) {
    showToast("Please provide account ID and name.", "error");
    return;
  }

  try {
    let res;
    const targetUrlId = (mode === "edit" && origAccId) ? origAccId : accId;
    if (mode === "edit") {
      res = await fetch(`/api/accounts/${targetUrlId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          new_account_id: accId,
          account_name: name,
          type: type,
          starting_balance: startBal,
          is_hidden: isHidden
        })
      });
    } else {
      res = await fetch("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: accId, account_name: name, type: type, starting_balance: startBal, is_hidden: isHidden })
      });
    }

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Account save failed");
    }

    if (mode === "edit" && origAccId && origAccId !== accId) {
      if (typeof currentDetailAccountId !== 'undefined' && currentDetailAccountId === origAccId) {
        currentDetailAccountId = accId;
        const accIdLabel = document.getElementById("quick-tx-acc-id-label");
        if (accIdLabel) accIdLabel.textContent = accId;
      }
      if (selectedAccountIds.has(origAccId)) {
        selectedAccountIds.delete(origAccId);
        selectedAccountIds.add(accId);
      }
    }

    const returnContext = accountModalContext;
    accountModalContext = 'accounts';
    closeModal('modal-add-account');
    showToast(mode === "edit" ? (origAccId && origAccId !== accId ? `Account #${origAccId} renumbered to #${accId}!` : "Account updated!") : `Account #${accId} created!`);
    await recalculateAllBalances();

    // If account was created while adding recurring rule or transaction, auto-select it and restore parent modal
    if (mode !== "edit") {
      if (returnContext === 'transaction') {
        const txSelect = document.getElementById("tx-form-account");
        if (txSelect) txSelect.value = accId;
        document.getElementById('modal-add-transaction')?.classList.remove('hidden');
        showToast(`Account #${accId} created and selected!`);
      } else if (returnContext === 'recurring') {
        const recSelect = document.getElementById("rec-form-account");
        if (recSelect) recSelect.value = accId;
        document.getElementById('modal-add-recurring')?.classList.remove('hidden');
        showToast(`Account #${accId} created and selected!`);
      }
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteAccount(accId) {
  const acc = (accounts || []).find(a => a.account_id === accId);
  const name = acc ? ` "${acc.account_name}"` : '';
  const confirmed = await askConfirm(
    `Delete Account #${accId}?`,
    `Are you sure you want to delete Account #${accId}${name}? This will also delete any linked transactions.`,
    `Delete Account`
  );
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/accounts/${accId}?cascade=true`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to delete account");
    }
    if (typeof currentDetailAccountId !== 'undefined' && currentDetailAccountId === accId) {
      closeAccountDetail();
    }
    selectedAccountIds.delete(accId);
    updateAccBulkActionBar();
    showToast(`Account #${accId} deleted`);
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// --- Forecast & Daily Projection ---

function setForecastPreset(preset) {
  const today = new Date();
  let target = new Date();

  if (preset === 'today') {
    target = today;
  } else if (preset === 'eom') {
    target = new Date(today.getFullYear(), today.getMonth() + 1, 0);
  } else if (preset === '30d') {
    target.setDate(today.getDate() + 30);
  } else if (preset === '60d') {
    target.setDate(today.getDate() + 60);
  } else if (preset === '90d') {
    target.setDate(today.getDate() + 90);
  } else if (preset === 'eoy') {
    target = new Date(today.getFullYear(), 11, 31);
  }

  document.querySelectorAll('.forecast-preset-btn').forEach(btn => {
    if (btn.getAttribute('data-preset') === preset) {
      btn.className = "forecast-preset-btn px-2.5 py-1 rounded-lg text-[11px] font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition";
    } else {
      btn.className = "forecast-preset-btn px-2.5 py-1 rounded-lg text-[11px] font-medium text-slate-400 hover:text-white transition";
    }
  });

  const y = target.getFullYear();
  const m = String(target.getMonth() + 1).padStart(2, '0');
  const d = String(target.getDate()).padStart(2, '0');
  document.getElementById("forecast-target-date").value = `${y}-${m}-${d}`;
  loadForecast();
}

let isForecastStartCustom = false;

function onForecastStartDateChange() {
  const startInput = document.getElementById("forecast-start-date");
  isForecastStartCustom = Boolean(startInput && startInput.value);
  loadForecast();
}

function resetForecastStartDate() {
  isForecastStartCustom = false;
  const startInput = document.getElementById("forecast-start-date");
  if (startInput) startInput.value = "";
  loadForecast();
}

async function loadForecast() {
  const startInput = document.getElementById("forecast-start-date");
  const startDate = (isForecastStartCustom && startInput) ? startInput.value : "";
  const targetDate = document.getElementById("forecast-target-date").value;
  const includeRecurring = document.getElementById("forecast-include-recurring").checked;

  try {
    const params = new URLSearchParams();
    if (startDate) params.append("start_date", startDate);
    if (targetDate) params.append("target_date", targetDate);
    params.append("include_recurring", includeRecurring);

    const res = await fetch(`/api/cash-projection?${params.toString()}`);
    if (!res.ok) throw new Error("Failed to calculate projection");

    forecastData = await res.json();

    // Populate start date input automatically when using Earliest Pending Tx mode
    if (startInput && (!isForecastStartCustom || !startInput.value) && forecastData.summary.start_date) {
      startInput.value = forecastData.summary.start_date;
    }

    const rangeLabel = document.getElementById("forecast-range-label");
    if (rangeLabel && forecastData.summary) {
      rangeLabel.textContent = `${forecastData.summary.start_date} → ${forecastData.summary.target_date}`;
    }

    renderForecastKPIs();
    renderForecastChart();
    renderDailyLedgerTable();
  } catch (err) {
    console.error("Forecast error:", err);
    showToast("Error computing cash forecast", "error");
  }
}

function renderForecastKPIs() {
  const sum = forecastData.summary;

  // Sidebar / Header Nav Pill Today's Cash
  document.getElementById("nav-today-cash").textContent = formatCurrency(sum.starting_cash);
  document.getElementById("nav-today-cash").className = `text-sm font-extrabold font-mono ${sum.starting_cash >= 0 ? "text-emerald-400" : "text-rose-400"}`;
  const mobTodayEl = document.getElementById("mob-nav-today-cash");
  if (mobTodayEl) {
    mobTodayEl.textContent = formatCurrency(sum.starting_cash);
    mobTodayEl.className = `text-base font-extrabold font-mono mt-0.5 block ${sum.starting_cash >= 0 ? "text-emerald-400" : "text-rose-400"}`;
  }

  // Target Date Stat Card
  const projEl = document.getElementById("stat-projected-cash");
  projEl.textContent = formatCurrency(sum.projected_cash_on_hand);
  projEl.className = `text-2xl xl:text-3xl font-extrabold font-mono tracking-tight ${sum.projected_cash_on_hand >= 0 ? "text-emerald-400" : "text-rose-400"}`;
  document.getElementById("stat-target-date-label").textContent = `As of ${formatDate(sum.target_date)}`;

  const deltaEl = document.getElementById("stat-projected-delta");
  if (deltaEl) {
    const diff = (sum.projected_cash_on_hand || 0) - (sum.starting_cash || 0);
    const sign = diff >= 0 ? "+" : "";
    deltaEl.textContent = `${sign}${formatCurrency(diff)} vs base`;
    deltaEl.className = `text-[11px] font-mono ${diff >= 0 ? "text-emerald-400/90" : "text-rose-400/90"}`;
  }

  const badgeEl = document.getElementById("stat-projected-badge");
  if (sum.projected_cash_on_hand >= 0) {
    badgeEl.textContent = "Positive Reserve";
    badgeEl.className = "inline-flex text-[11px] font-semibold text-emerald-300 bg-emerald-500/10 px-2 py-0.5 rounded-md w-max";
  } else {
    badgeEl.textContent = "Deficit / Shortfall";
    badgeEl.className = "inline-flex text-[11px] font-semibold text-rose-300 bg-rose-500/10 px-2 py-0.5 rounded-md w-max";
  }

  // Net Flow & Inflow/Outflow Bar
  const netEl = document.getElementById("stat-net-flow");
  netEl.textContent = formatCurrency(sum.net_cash_flow);
  netEl.className = `text-2xl font-extrabold font-mono tracking-tight ${sum.net_cash_flow >= 0 ? "text-emerald-400" : "text-rose-400"}`;
  document.getElementById("stat-inflows").textContent = `+${formatCurrency(sum.total_inflows)}`;
  document.getElementById("stat-outflows").textContent = `-${formatCurrency(sum.total_outflows)}`;
  document.getElementById("stat-tx-count").textContent = `${forecastData.daily_timeline.reduce((acc, d) => acc + d.transaction_count, 0)} pending events`;

  const barIn = document.getElementById("stat-flow-bar-in");
  const barOut = document.getElementById("stat-flow-bar-out");
  if (barIn && barOut) {
    const totAbs = (Math.abs(sum.total_inflows) || 0) + (Math.abs(sum.total_outflows) || 0);
    const inPct = totAbs > 0 ? Math.round((Math.abs(sum.total_inflows) / totAbs) * 100) : 50;
    barIn.style.width = `${inPct}%`;
    barOut.style.width = `${100 - inPct}%`;
  }

  // Lowest Cash Dip
  const minCashEl = document.getElementById("stat-min-cash");
  minCashEl.textContent = formatCurrency(sum.min_cash_balance);
  minCashEl.className = `text-2xl font-extrabold font-mono tracking-tight ${sum.min_cash_balance >= 0 ? "text-white" : "text-rose-400"}`;
  document.getElementById("stat-min-date").textContent = `on ${formatDate(sum.min_cash_date)}`;

  const dipBadge = document.getElementById("stat-dip-status");
  if (sum.min_cash_balance < 0) {
    dipBadge.textContent = "⚠️ Negative Cash Alert";
    dipBadge.className = "inline-flex text-[11px] font-bold text-rose-300 bg-rose-500/20 border border-rose-500/30 px-2 py-0.5 rounded-md w-max animate-pulse";
  } else {
    dipBadge.textContent = "Safe Cushion";
    dipBadge.className = "inline-flex text-[11px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-md w-max";
  }

  // Hidden Accounts Indicator Badge
  const hiddenBadge = document.getElementById("forecast-hidden-badge");
  const hiddenText = document.getElementById("forecast-hidden-text");
  if (sum.hidden_accounts_count > 0) {
    hiddenBadge.classList.remove("hidden");
    hiddenBadge.classList.add("inline-flex");
    hiddenText.textContent = `${sum.hidden_accounts_count} account${sum.hidden_accounts_count > 1 ? 's' : ''} excluded`;
  } else {
    hiddenBadge.classList.add("hidden");
    hiddenBadge.classList.remove("inline-flex");
  }

  // Settled (Paid / Received) Transactions Indicator Badge
  const settledBadge = document.getElementById("forecast-settled-badge");
  const settledText = document.getElementById("forecast-settled-text");
  if (settledBadge && settledText) {
    if (sum.settled_transactions_count > 0) {
      settledBadge.classList.remove("hidden");
      settledBadge.classList.add("inline-flex");
      settledText.textContent = `${sum.settled_transactions_count} paid/received excluded`;
    } else {
      settledBadge.classList.add("hidden");
      settledBadge.classList.remove("inline-flex");
    }
  }
  lucide.createIcons();
}

function renderForecastChart() {
  const ctx = document.getElementById("cashForecastChart").getContext("2d");
  const timeline = forecastData.daily_timeline;

  const labels = timeline.map(d => d.date);
  const balances = timeline.map(d => d.cash_on_hand);
  const hasDeficit = balances.some(b => b < 0);

  const activeThemeKey = document.documentElement.getAttribute("data-theme") || "cyber";
  const themeCfg = FUTURISTIC_THEMES[activeThemeKey] || FUTURISTIC_THEMES.cyber;

  if (chartInstance) {
    chartInstance.destroy();
  }

  // Rich multi-stop vertical gradient synced with active futuristic theme
  const gradient = ctx.createLinearGradient(0, 0, 0, 320);
  if (hasDeficit) {
    gradient.addColorStop(0, themeCfg.gradTop);
    gradient.addColorStop(0.65, "rgba(244, 63, 94, 0.16)");
    gradient.addColorStop(1, "rgba(244, 63, 94, 0.02)");
  } else {
    gradient.addColorStop(0, themeCfg.gradTop);
    gradient.addColorStop(0.55, themeCfg.gradMid);
    gradient.addColorStop(1, themeCfg.gradBot);
  }

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Cash on Hand',
          data: balances,
          borderColor: themeCfg.stroke,
          backgroundColor: gradient,
          fill: true,
          tension: 0.32,
          borderWidth: 2.5,
          pointRadius: timeline.length > 60 ? 0 : 2.5,
          pointHoverRadius: 6,
          pointBackgroundColor: themeCfg.stroke,
          pointBorderColor: '#030612',
          pointBorderWidth: 2
        },
        {
          label: 'Threshold ($0)',
          data: timeline.map(() => 0),
          borderColor: 'rgba(244, 63, 94, 0.55)',
          borderDash: [5, 5],
          borderWidth: 1.5,
          fill: false,
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(4, 8, 20, 0.96)',
          borderColor: themeCfg.tooltipBorder,
          borderWidth: 1,
          padding: 14,
          cornerRadius: 12,
          titleFont: { size: 12, weight: 'bold', family: 'Inter' },
          bodyFont: { size: 12, family: 'JetBrains Mono' },
          callbacks: {
            title: (items) => formatDate(items[0].label),
            label: (item) => {
              if (item.datasetIndex === 0) {
                return ` Projected Cash: ${formatCurrency(item.raw)}`;
              }
              return null;
            },
            afterBody: (items) => {
              const idx = items[0].dataIndex;
              const day = timeline[idx];
              if (!day || day.transaction_count === 0) return ["No transactions this day"];
              const lines = ["", "Activity:"];
              day.transactions.forEach(t => {
                const amt = formatCurrency(t.transaction_amount);
                const desc = t.description ? ` (${t.description})` : "";
                lines.push(`• ${t.account_name}: ${amt}${desc}`);
              });
              return lines;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(51, 65, 85, 0.3)' },
          ticks: {
            color: '#94a3b8',
            maxTicksLimit: 10,
            callback: function(val, index) {
              return formatDate(labels[index]);
            }
          }
        },
        y: {
          grid: { color: 'rgba(51, 65, 85, 0.3)' },
          ticks: {
            color: '#94a3b8',
            callback: function(val) {
              return `$${val.toLocaleString()}`;
            }
          }
        }
      }
    }
  });
}

function toggleOnlyActivityDays() {
  onlyActivityDays = !onlyActivityDays;
  const btn = document.getElementById("btn-toggle-activity-days");
  if (onlyActivityDays) {
    btn.textContent = "Show All Days";
    btn.classList.add("bg-emerald-500/20", "text-emerald-400");
  } else {
    btn.textContent = "Show Days with Transactions Only";
    btn.classList.remove("bg-emerald-500/20", "text-emerald-400");
  }
  renderDailyLedgerTable();
}

let dailySortBy = "date";
let dailySortDir = "asc";

function toggleSortDaily(col) {
  if (dailySortBy === col) {
    dailySortDir = dailySortDir === "asc" ? "desc" : "asc";
  } else {
    dailySortBy = col;
    dailySortDir = (col === "date") ? "asc" : "desc";
  }
  updateDailySortUI();
  renderDailyLedgerTable();
}

function updateDailySortUI() {
  const cols = ["date", "inflow", "outflow", "net_change", "cash_on_hand"];
  cols.forEach(c => {
    const icon = document.getElementById(`sort-icon-daily-${c}`);
    const th = document.getElementById(`th-daily-${c}`);
    if (!icon) return;
    if (dailySortBy === c) {
      icon.textContent = dailySortDir === "asc" ? "▲" : "▼";
      icon.className = "text-emerald-400 font-bold text-[11px]";
      if (th) th.className = "text-emerald-400 font-bold";
    } else {
      icon.textContent = "↕";
      icon.className = "text-slate-600 group-hover:text-slate-400 text-[11px]";
      if (th) th.className = "text-slate-400 group-hover:text-white";
    }
  });
}

function renderDailyLedgerTable() {
  const tbody = document.getElementById("daily-timeline-tbody");
  if (!forecastData || !forecastData.daily_timeline) return;

  let rows = forecastData.daily_timeline.filter(d => !onlyActivityDays || d.transaction_count > 0);

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-500">No transactions recorded in this period.</td></tr>`;
    return;
  }

  const dir = dailySortDir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    if (dailySortBy === "date") return (a.date || "").localeCompare(b.date || "") * dir;
    if (dailySortBy === "inflow") return ((parseFloat(a.inflow) || 0) - (parseFloat(b.inflow) || 0)) * dir;
    if (dailySortBy === "outflow") return ((parseFloat(a.outflow) || 0) - (parseFloat(b.outflow) || 0)) * dir;
    if (dailySortBy === "net_change") return ((parseFloat(a.net_change) || 0) - (parseFloat(b.net_change) || 0)) * dir;
    if (dailySortBy === "cash_on_hand") return ((parseFloat(a.cash_on_hand) || 0) - (parseFloat(b.cash_on_hand) || 0)) * dir;
    return 0;
  });

  tbody.innerHTML = rows.map(day => {
    const isDip = day.cash_on_hand < 0;
    const balanceColor = isDip ? "text-rose-400 font-extrabold bg-rose-500/10 px-2 py-0.5 rounded" : "text-emerald-400 font-bold";
    
    // Transactions breakdown pills
    const txBadges = day.transactions.map(t => {
      const isPos = t.transaction_amount >= 0;
      const color = isPos ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20" : "bg-rose-500/10 text-rose-300 border-rose-500/20";
      const recurringTag = t.is_projected ? `<span class="opacity-75 font-normal">[Rec]</span> ` : "";
      return `
        <span class="inline-flex items-center text-[10px] font-sans px-2 py-0.5 rounded border ${color} mr-1 mb-1">
          ${recurringTag}${escapeHtml(t.account_name)}: <b class="ml-1 font-mono">${formatCurrency(t.transaction_amount)}</b>
        </span>
      `;
    }).join("");

    return `
      <tr class="hover:bg-slate-800/40 transition">
        <td class="py-2.5 px-4 font-semibold text-slate-200 whitespace-nowrap">${day.date}</td>
        <td class="py-2.5 px-4 text-right text-emerald-400">${day.inflow > 0 ? `+${formatCurrency(day.inflow)}` : "-"}</td>
        <td class="py-2.5 px-4 text-right text-rose-400">${day.outflow > 0 ? `-${formatCurrency(day.outflow)}` : "-"}</td>
        <td class="py-2.5 px-4 text-right ${day.net_change >= 0 ? "text-emerald-400" : "text-rose-400"}">${day.net_change !== 0 ? formatCurrency(day.net_change) : "-"}</td>
        <td class="py-2.5 px-4 text-right ${balanceColor}">${formatCurrency(day.cash_on_hand)}</td>
        <td class="py-2.5 px-4">${txBadges || '<span class="text-slate-500 text-[11px] font-sans">No activity</span>'}</td>
      </tr>
    `;
  }).join("");
}

// --- Transactions Ledger & Sorting Mechanism ---

let txSortBy = "date"; // 'date' | 'account' | 'category' | 'amount' | 'description'
let txSortDir = "asc"; // 'asc' | 'desc' (Ascending chronological by default)

function toggleSortTx(col) {
  if (txSortBy === col) {
    txSortDir = txSortDir === "asc" ? "desc" : "asc";
  } else {
    txSortBy = col;
    // Default text and date columns to asc; amount to desc
    txSortDir = (col === "amount") ? "desc" : "asc";
  }
  updateTxSortUI();
  sortTransactionsInMemory();
  renderTransactionsTable();
}

function onSelectSortTx(val) {
  txSortBy = val;
  updateTxSortUI();
  sortTransactionsInMemory();
  renderTransactionsTable();
}

function toggleSortDir() {
  txSortDir = txSortDir === "asc" ? "desc" : "asc";
  updateTxSortUI();
  sortTransactionsInMemory();
  renderTransactionsTable();
}

function updateTxSortUI() {
  const sel = document.getElementById("tx-sort-select");
  if (sel) sel.value = txSortBy;

  const orderLabel = document.getElementById("tx-sort-order-label");
  if (orderLabel) {
    orderLabel.textContent = txSortDir === "asc" ? "ASC ▲" : "DESC ▼";
    orderLabel.className = txSortDir === "asc"
      ? "text-[10px] font-semibold tracking-wider font-mono text-emerald-400"
      : "text-[10px] font-semibold tracking-wider font-mono text-blue-400";
  }

  const cols = ["date", "account", "category", "amount", "description", "status"];
  cols.forEach(c => {
    const iconEl = document.getElementById(`sort-icon-${c}`);
    const thLabel = document.getElementById(`th-label-${c}`);
    if (!iconEl) return;
    if (txSortBy === c) {
      iconEl.textContent = txSortDir === "asc" ? "▲" : "▼";
      iconEl.className = "text-emerald-400 font-bold text-[11px]";
      if (thLabel) thLabel.className = "text-emerald-400 font-bold";
    } else {
      iconEl.textContent = "↕";
      iconEl.className = "text-slate-600 group-hover:text-slate-400 text-[11px]";
      if (thLabel) thLabel.className = "text-slate-400 group-hover:text-white";
    }
  });
}

function sortTransactionsInMemory() {
  if (!transactions || transactions.length === 0) return;
  const dir = txSortDir === "asc" ? 1 : -1;
  transactions.sort((a, b) => {
    if (txSortBy === "date") {
      const cmp = (a.transaction_date || "").localeCompare(b.transaction_date || "");
      return cmp !== 0 ? cmp * dir : ((a.transaction_id || 0) - (b.transaction_id || 0)) * dir;
    }
    if (txSortBy === "account") {
      const nameA = (a.account_name || "").toLowerCase();
      const nameB = (b.account_name || "").toLowerCase();
      const cmp = nameA.localeCompare(nameB);
      return cmp !== 0 ? cmp * dir : (a.transaction_date || "").localeCompare(b.transaction_date || "");
    }
    if (txSortBy === "category") {
      const typeA = (a.account_type || "").toLowerCase();
      const typeB = (b.account_type || "").toLowerCase();
      const cmp = typeA.localeCompare(typeB);
      if (cmp !== 0) return cmp * dir;
      return (a.account_name || "").toLowerCase().localeCompare((b.account_name || "").toLowerCase());
    }
    if (txSortBy === "amount") {
      const amtA = Number(a.transaction_amount) || 0;
      const amtB = Number(b.transaction_amount) || 0;
      const cmp = (amtA - amtB) * dir;
      return cmp !== 0 ? cmp : (a.transaction_date || "").localeCompare(b.transaction_date || "");
    }
    if (txSortBy === "description") {
      const descA = (a.description || "").toLowerCase();
      const descB = (b.description || "").toLowerCase();
      const cmp = descA.localeCompare(descB);
      return cmp !== 0 ? cmp * dir : (a.transaction_date || "").localeCompare(b.transaction_date || "");
    }
    if (txSortBy === "status") {
      const sA = (a.status || "pending").toLowerCase();
      const sB = (b.status || "pending").toLowerCase();
      const cmp = sA.localeCompare(sB);
      return cmp !== 0 ? cmp * dir : (a.transaction_date || "").localeCompare(b.transaction_date || "");
    }
    return 0;
  });
}

function debounceTxSearch() {
  clearTimeout(txSearchTimeout);
  txSearchTimeout = setTimeout(() => {
    loadTransactions();
  }, 300);
}

function resetTxFilters() {
  document.getElementById("tx-search-input").value = "";
  document.getElementById("tx-filter-account").value = "";
  const showSettledEl = document.getElementById("tx-show-settled");
  if (showSettledEl) showSettledEl.checked = false;
  document.getElementById("tx-filter-start").value = "";
  document.getElementById("tx-filter-end").value = "";
  txSortBy = "date";
  txSortDir = "asc";
  updateTxSortUI();
  loadTransactions();
}

async function loadTransactions() {
  const search = document.getElementById("tx-search-input").value.trim();
  const accountId = document.getElementById("tx-filter-account").value;
  const showSettled = document.getElementById("tx-show-settled")?.checked || false;
  const statusVal = showSettled ? "" : "pending";
  const startDate = document.getElementById("tx-filter-start").value;
  const endDate = document.getElementById("tx-filter-end").value;

  const params = new URLSearchParams();
  if (search) params.append("search", search);
  if (accountId) params.append("account_id", accountId);
  if (statusVal) params.append("status", statusVal);
  if (startDate) params.append("start_date", startDate);
  if (endDate) params.append("end_date", endDate);
  params.append("order_by", txSortBy);
  params.append("order_dir", txSortDir);
  params.append("limit", "5000");

  try {
    const res = await fetch(`/api/transactions?${params.toString()}`);
    transactions = await res.json();
    txLoadedOnce = true;
    const txBadge = document.getElementById("nav-badge-tx");
    if (txBadge) txBadge.textContent = transactions.filter(t => (t.status || 'pending') === 'pending').length;
    if (activeTab === 'transactions') {
      dirtyTabs.delete('transactions');
      renderTransactionsTable();
    } else {
      dirtyTabs.add('transactions');
    }
  } catch (err) {
    console.error("Error loading transactions:", err);
  }
}

function renderTxStatusBadge(tx) {
  const status = (tx.status || "pending").toLowerCase();
  const amt = parseFloat(tx.transaction_amount) || 0;
  const isPending = status === "pending";
  const isExpense = amt < 0;

  if (isPending) {
    if (isExpense) {
      return `
        <button type="button" onclick="toggleTxStatus(${tx.transaction_id})" class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500 hover:text-slate-950 border border-emerald-500/30 hover:border-emerald-500 transition shadow-sm group" title="Pending expense. Click to Mark as Paid">
          <i data-lucide="check" class="w-3.5 h-3.5 text-emerald-400 group-hover:text-slate-950"></i>
          <span>Mark as Paid</span>
        </button>
      `;
    } else {
      return `
        <button type="button" onclick="toggleTxStatus(${tx.transaction_id})" class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-500/15 text-teal-300 hover:bg-teal-500 hover:text-slate-950 border border-teal-500/30 hover:border-teal-500 transition shadow-sm group" title="Pending income. Click to Mark as Received">
          <i data-lucide="check-circle" class="w-3.5 h-3.5 text-teal-400 group-hover:text-slate-950"></i>
          <span>Mark as Received</span>
        </button>
      `;
    }
  } else {
    // Paid or Received
    if (isExpense) {
      return `
        <button type="button" onclick="toggleTxStatus(${tx.transaction_id})" class="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-500/20 text-emerald-300 hover:bg-slate-800 hover:text-amber-300 border border-emerald-500/40 hover:border-amber-500/40 transition group" title="Status: Paid! Click to undo back to Pending">
          <i data-lucide="check-circle" class="w-3.5 h-3.5 text-emerald-400 group-hover:text-amber-400"></i>
          <span>Paid</span>
          <span class="text-[10px] text-slate-400 group-hover:text-amber-300 ml-0.5 opacity-0 group-hover:opacity-100 transition">(undo)</span>
        </button>
      `;
    } else {
      return `
        <button type="button" onclick="toggleTxStatus(${tx.transaction_id})" class="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-500/20 text-teal-300 hover:bg-slate-800 hover:text-amber-300 border border-teal-500/40 hover:border-amber-500/40 transition group" title="Status: Received! Click to undo back to Pending">
          <i data-lucide="check-circle-2" class="w-3.5 h-3.5 text-teal-400 group-hover:text-amber-400"></i>
          <span>Received</span>
          <span class="text-[10px] text-slate-400 group-hover:text-amber-300 ml-0.5 opacity-0 group-hover:opacity-100 transition">(undo)</span>
        </button>
      `;
    }
  }
}

async function toggleTxStatus(txId) {
  try {
    const res = await fetch(`/api/transactions/${txId}/toggle-status`, {
      method: "PUT"
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to update transaction status");
    }
    const updated = await res.json();
    const amt = parseFloat(updated.transaction_amount) || 0;
    const isPending = (updated.status || 'pending').toLowerCase() === 'pending';

    // Immediately update UI in-memory so Paid/Received items hide without waiting
    const showSettled = document.getElementById("tx-show-settled")?.checked || false;
    if (!isPending && !showSettled) {
      transactions = transactions.filter(t => t.transaction_id !== txId);
      renderTransactionsTable();
    }
    const detailShowSettled = document.getElementById("detail-tx-show-settled")?.checked || false;
    if (!isPending && !detailShowSettled && typeof currentDetailTransactions !== 'undefined' && Array.isArray(currentDetailTransactions)) {
      currentDetailTransactions = currentDetailTransactions.filter(t => t.transaction_id !== txId);
      if (typeof currentDetailAccountId !== 'undefined' && currentDetailAccountId) {
        renderAccountDetailUI();
      }
    }

    if (isPending) {
      showToast("Restored to Pending — included in cash calculation");
    } else if (amt < 0) {
      showToast("Marked as Paid — hidden & removed from cash calculation");
    } else {
      showToast("Marked as Received — hidden & removed from cash calculation");
    }

    await recalculateAllBalances();
  } catch (err) {
    console.error("Error toggling status:", err);
    showToast(err.message || "Error updating status", "error");
  }
}

function updateBulkActionBar() {
  const bar = document.getElementById("tx-bulk-bar");
  const countBadge = document.getElementById("tx-bulk-count");
  const selectAllEl = document.getElementById("tx-select-all");

  const count = selectedTxIds.size;
  if (countBadge) countBadge.textContent = count;

  if (bar) {
    if (count > 0) {
      bar.classList.remove("hidden");
      bar.classList.add("flex");
    } else {
      bar.classList.add("hidden");
      bar.classList.remove("flex");
    }
  }

  if (selectAllEl) {
    if (transactions.length > 0 && count === transactions.length) {
      selectAllEl.checked = true;
      selectAllEl.indeterminate = false;
    } else if (count > 0 && count < transactions.length) {
      selectAllEl.checked = false;
      selectAllEl.indeterminate = true;
    } else {
      selectAllEl.checked = false;
      selectAllEl.indeterminate = false;
    }
  }
}

function onToggleTxSelect(txId, isChecked) {
  if (isChecked) {
    selectedTxIds.add(txId);
  } else {
    selectedTxIds.delete(txId);
  }
  updateBulkActionBar();
  // Highlight row
  const row = document.getElementById(`tx-row-${txId}`);
  if (row) {
    if (isChecked) {
      row.classList.add("bg-emerald-950/25", "border-l-2", "border-emerald-400");
    } else {
      row.classList.remove("bg-emerald-950/25", "border-l-2", "border-emerald-400");
    }
  }
}

function toggleSelectAllTransactions(isChecked) {
  if (isChecked) {
    transactions.forEach(t => selectedTxIds.add(t.transaction_id));
  } else {
    selectedTxIds.clear();
  }
  renderTransactionsTable();
}

function clearSelectedTransactions() {
  selectedTxIds.clear();
  renderTransactionsTable();
}

async function bulkSetTxStatus(targetStatus) {
  const ids = Array.from(selectedTxIds);
  if (ids.length === 0) return;

  const showSettled = document.getElementById("tx-show-settled")?.checked || false;
  const isSettledTarget = targetStatus !== "pending";

  // Instant in-memory hide if viewing Pending Only and setting to Paid/Received
  if (!showSettled && isSettledTarget) {
    const idSet = new Set(ids);
    transactions = transactions.filter(t => !idSet.has(t.transaction_id));
    selectedTxIds.clear();
    renderTransactionsTable();
  }

  try {
    const res = await fetch("/api/transactions/bulk-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transaction_ids: ids,
        status: targetStatus
      })
    });
    if (!res.ok) throw new Error("Bulk update failed");
    const data = await res.json();
    
    selectedTxIds.clear();
    if (isSettledTarget) {
      showToast(`${data.count || ids.length} transactions marked as Paid / Received & removed from cash calculation!`);
    } else {
      showToast(`${data.count || ids.length} transactions restored to Pending!`);
    }

    await recalculateAllBalances();
  } catch (err) {
    console.error("Bulk status error:", err);
    showToast(err.message || "Failed to update transactions", "error");
    loadTransactions();
  }
}

async function bulkDeleteSelectedTransactions() {
  const ids = Array.from(selectedTxIds);
  if (ids.length === 0) return;

  const confirmed = await askConfirm(
    `Delete ${ids.length} Transaction${ids.length > 1 ? 's' : ''}?`,
    `Are you sure you want to permanently delete ${ids.length} selected transaction${ids.length > 1 ? 's' : ''}?`,
    `Delete ${ids.length} Transaction${ids.length > 1 ? 's' : ''}`
  );
  if (!confirmed) return;

  // Instant in-memory removal
  const idSet = new Set(ids);
  transactions = transactions.filter(t => !idSet.has(t.transaction_id));
  selectedTxIds.clear();
  renderTransactionsTable();

  try {
    const res = await fetch("/api/transactions/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transaction_ids: ids })
    });
    if (!res.ok) throw new Error("Bulk delete failed");
    showToast(`${ids.length} transactions deleted`);

    await recalculateAllBalances();
  } catch (err) {
    console.error("Bulk delete error:", err);
    showToast(err.message || "Failed to delete transactions", "error");
    loadTransactions();
  }
}

function renderTransactionsTable() {
  const tbody = document.getElementById("tx-table-tbody");
  const countEl = document.getElementById("tx-table-count");
  const showSettled = document.getElementById("tx-show-settled")?.checked || false;
  countEl.textContent = showSettled
    ? `${transactions.length} transactions shown (including Paid / Received)`
    : `${transactions.length} pending transactions shown`;

  const txBadge = document.getElementById("nav-badge-tx");
  if (txBadge) txBadge.textContent = transactions.length;

  updateBulkActionBar();

  if (transactions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-12 text-slate-500 font-sans">No transactions match your search.</td></tr>`;
    return;
  }

  tbody.innerHTML = transactions.map(t => {
    const isIncome = t.transaction_amount >= 0;
    const isSettled = (t.status || "pending").toLowerCase() !== "pending";
    const isSelected = selectedTxIds.has(t.transaction_id);
    const amtColor = isIncome ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
    const amtStyle = isSettled ? "line-through opacity-50" : "";
    const rowStyle = isSettled ? "bg-slate-900/30 opacity-75" : "";
    const selectedStyle = isSelected ? "bg-emerald-950/25 border-l-2 border-emerald-400" : "";
    const prefix = isIncome ? "+" : "";
    const isExcluded = t.account_is_hidden === 1 || t.account_is_hidden === true;
    const excludedBadge = isExcluded
      ? `<span class="inline-flex items-center text-[10px] font-sans px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20 ml-1.5" title="Account is excluded from cash calculations">Excluded</span>`
      : "";
    const recurringBadge = t.recurring_rule_id
      ? `<span class="inline-flex items-center text-[10px] font-sans px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 ml-1.5" title="Recurring Rule #${t.recurring_rule_id}"><i data-lucide="repeat" class="w-3 h-3 mr-0.5"></i>Recurring</span>`
      : "";

    return `
      <tr id="tx-row-${t.transaction_id}" class="hover:bg-slate-800/40 transition ${rowStyle} ${selectedStyle}">
        <td class="py-2.5 px-3 text-center" onclick="event.stopPropagation()">
          <input type="checkbox" class="tx-row-select rounded border-slate-600 bg-slate-900 text-emerald-500 focus:ring-0 w-3.5 h-3.5 cursor-pointer" data-id="${t.transaction_id}" ${isSelected ? 'checked' : ''} onchange="onToggleTxSelect(${t.transaction_id}, this.checked)">
        </td>
        <td class="py-2.5 px-4 font-semibold text-slate-200 whitespace-nowrap">${t.transaction_date}</td>
        <td class="py-2.5 px-4 font-sans text-white">
          <div class="flex items-center">
            <span class="font-medium">${escapeHtml(t.account_name || 'Account ' + t.account_id)}</span>
            ${excludedBadge}
          </div>
          <div class="text-[10px] text-slate-400">#${t.account_id}</div>
        </td>
        <td class="py-2.5 px-4 font-sans">
          <span class="inline-flex px-2 py-0.5 rounded text-[11px] font-medium ${getCategoryBadgeStyle(t.account_type)}">
            ${escapeHtml(t.account_type || 'General')}
          </span>
        </td>
        <td class="py-2.5 px-4 text-right ${amtColor} ${amtStyle}" title="${isSettled ? 'Excluded from cash calculation (already Paid/Received)' : 'Included in cash calculation'}">${prefix}${formatCurrency(t.transaction_amount)}</td>
        <td class="py-2.5 px-4 font-sans text-slate-300">
          <div class="flex items-center">
            <span>${escapeHtml(t.description || '-')}</span>
            ${recurringBadge}
          </div>
        </td>
        <td class="py-2.5 px-4 whitespace-nowrap">
          ${renderTxStatusBadge(t)}
        </td>
        <td class="py-2.5 px-4 text-right space-x-1 whitespace-nowrap">
          <button onclick="editTransaction(${t.transaction_id})" class="text-slate-400 hover:text-emerald-400 p-1">
            <i data-lucide="edit-3" class="w-4 h-4"></i>
          </button>
          <button onclick="deleteTransaction(${t.transaction_id})" class="text-slate-400 hover:text-rose-400 p-1">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        </td>
      </tr>
    `;
  }).join("");
  lucide.createIcons();
}

let txAmountSign = -1; // default expense

function setTxAmountSign(sign) {
  txAmountSign = sign;
  const incBtn = document.getElementById("btn-sign-income");
  const expBtn = document.getElementById("btn-sign-expense");
  if (sign > 0) {
    incBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
    expBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-rose-400 border border-transparent";
  } else {
    expBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30";
    incBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-emerald-400 border border-transparent";
  }
}

function setModalTxStatus(status) {
  const hiddenInput = document.getElementById("tx-form-status");
  if (hiddenInput) hiddenInput.value = status;
  const pendBtn = document.getElementById("btn-modal-status-pending");
  const clearBtn = document.getElementById("btn-modal-status-cleared");
  if (!pendBtn || !clearBtn) return;
  if (status === "pending") {
    pendBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30";
    clearBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-emerald-400 border border-transparent";
  } else {
    clearBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
    pendBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-amber-400 border border-transparent";
  }
}

function editTransaction(txId) {
  const tx = (typeof currentDetailTransactions !== 'undefined' && currentDetailTransactions && currentDetailTransactions.find(t => t.transaction_id === txId)) 
    || transactions.find(t => t.transaction_id === txId);
  if (!tx) {
    showToast("Transaction not found", "error");
    return;
  }

  document.getElementById("tx-form-id").value = tx.transaction_id;
  document.getElementById("tx-form-date").value = tx.transaction_date;
  document.getElementById("tx-form-account").value = tx.account_id;
  document.getElementById("tx-form-desc").value = tx.description || "";

  const amt = parseFloat(tx.transaction_amount);
  setTxAmountSign(amt >= 0 ? 1 : -1);
  document.getElementById("tx-form-amount").value = Math.abs(amt).toFixed(2);
  setModalTxStatus(tx.status || 'pending');

  document.getElementById("modal-tx-title").textContent = `Edit Transaction #${tx.transaction_id}`;
  openModal('modal-add-transaction');
}

async function submitTransactionForm() {
  const txId = document.getElementById("tx-form-id").value;
  const dateVal = document.getElementById("tx-form-date").value;
  const accId = parseInt(document.getElementById("tx-form-account").value);
  const rawAmt = parseFloat(document.getElementById("tx-form-amount").value);
  const desc = document.getElementById("tx-form-desc").value.trim();
  const statusVal = document.getElementById("tx-form-status")?.value || "pending";

  if (!dateVal || !accId || isNaN(rawAmt)) {
    showToast("Please provide date, account, and amount.", "error");
    return;
  }

  const finalAmt = round2(txAmountSign * Math.abs(rawAmt));

  try {
    let res;
    if (txId) {
      res = await fetch(`/api/transactions/${txId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: accId,
          transaction_date: dateVal,
          transaction_amount: finalAmt,
          description: desc,
          status: statusVal
        })
      });
    } else {
      res = await fetch("/api/transactions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: accId,
          transaction_date: dateVal,
          transaction_amount: finalAmt,
          description: desc,
          status: statusVal
        })
      });
    }

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Transaction save failed");
    }

    closeModal('modal-add-transaction');
    showToast(txId ? "Transaction updated!" : "Transaction recorded!");
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteTransaction(txId) {
  const confirmed = await askConfirm(
    `Delete Transaction #${txId}?`,
    `Are you sure you want to delete transaction #${txId}?`,
    `Delete Transaction`
  );
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/transactions/${txId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    showToast("Transaction deleted");
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// --- Recurring Rules ---

let recAmountSign = -1;

function setRecAmountSign(sign) {
  recAmountSign = sign;
  const incBtn = document.getElementById("btn-rec-sign-income");
  const expBtn = document.getElementById("btn-rec-sign-expense");
  if (sign > 0) {
    incBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
    expBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-rose-400 border border-transparent";
  } else {
    expBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30";
    incBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-emerald-400 border border-transparent";
  }
}

let recSortBy = "start_date";
let recSortDir = "asc";

function toggleSortRecurring(col) {
  if (recSortBy === col) {
    recSortDir = recSortDir === "asc" ? "desc" : "asc";
  } else {
    recSortBy = col;
    recSortDir = (col === "description" || col === "account_name" || col === "frequency" || col === "start_date") ? "asc" : "desc";
  }
  updateRecurringSortUI();
  renderRecurringTable();
}

function updateRecurringSortUI() {
  const cols = ["description", "account_name", "amount", "frequency", "start_date", "active"];
  cols.forEach(c => {
    const icon = document.getElementById(`sort-icon-rec-${c}`);
    const th = document.getElementById(`th-rec-${c}`);
    if (!icon) return;
    if (recSortBy === c) {
      icon.textContent = recSortDir === "asc" ? "▲" : "▼";
      icon.className = "text-emerald-400 font-bold text-[11px]";
      if (th) th.className = "text-emerald-400 font-bold";
    } else {
      icon.textContent = "↕";
      icon.className = "text-slate-600 group-hover:text-slate-400 text-[11px]";
      if (th) th.className = "text-slate-400 group-hover:text-white";
    }
  });
}

function sortRecurringInMemory() {
  if (!recurringRules || recurringRules.length === 0) return;
  const dir = recSortDir === "asc" ? 1 : -1;
  recurringRules.sort((a, b) => {
    if (recSortBy === "description") {
      return (a.description || "").localeCompare(b.description || "") * dir;
    }
    if (recSortBy === "account_name") {
      return (a.account_name || "").localeCompare(b.account_name || "") * dir;
    }
    if (recSortBy === "amount") {
      return ((parseFloat(a.amount) || 0) - (parseFloat(b.amount) || 0)) * dir;
    }
    if (recSortBy === "frequency") {
      return (a.frequency || "").localeCompare(b.frequency || "") * dir;
    }
    if (recSortBy === "start_date") {
      return (a.start_date || "").localeCompare(b.start_date || "") * dir;
    }
    if (recSortBy === "active") {
      const aAct = (a.active ? 1 : 0);
      const bAct = (b.active ? 1 : 0);
      return (aAct - bAct) * dir;
    }
    return 0;
  });
}

async function loadRecurring() {
  try {
    const res = await fetch("/api/recurring");
    recurringRules = await res.json();
    recLoadedOnce = true;
    const recBadge = document.getElementById("nav-badge-rec");
    if (recBadge) recBadge.textContent = (recurringRules || []).filter(r => r.active).length;
    if (activeTab === 'recurring') {
      dirtyTabs.delete('recurring');
      renderRecurringTable();
    } else {
      dirtyTabs.add('recurring');
    }
  } catch (err) {
    console.error("Error loading recurring rules:", err);
  }
}

function renderRecurringTable() {
  const tbody = document.getElementById("recurring-table-tbody");
  sortRecurringInMemory();

  if (recurringRules.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-12 text-slate-500 font-sans">No recurring schedules set up yet. Add a paycheck or bill schedule!</td></tr>`;
    return;
  }

  tbody.innerHTML = recurringRules.map(r => {
    const isIncome = r.amount >= 0;
    const amtColor = isIncome ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
    const isActive = Boolean(r.active);

    return `
      <tr class="hover:bg-slate-800/40 transition">
        <td class="py-2.5 px-4 font-semibold text-white font-sans">${escapeHtml(r.description || 'Recurring Rule')}</td>
        <td class="py-2.5 px-4 font-sans text-slate-300">
          <button type="button" onclick="openAccountDetail(${r.account_id})" class="hover:text-emerald-400 underline decoration-slate-700 hover:decoration-emerald-400 underline-offset-2 transition text-left" title="Click to open account ledger">
            ${escapeHtml(r.account_name)} (#${r.account_id})
          </button>
        </td>
        <td class="py-2.5 px-4 text-right ${amtColor}">${formatCurrency(r.amount)}</td>
        <td class="py-2.5 px-4 font-sans capitalize text-slate-300">${r.frequency}${r.day_of_month ? ` (Day ${r.day_of_month})` : ''}</td>
        <td class="py-2.5 px-4 font-mono text-slate-400">${r.start_date} ${r.end_date ? 'to ' + r.end_date : ''}</td>
        <td class="py-2.5 px-4 font-sans">
          <button type="button" id="rec-status-btn-${r.id}" onclick="toggleRecurringStatus(${r.id}, ${!isActive})" class="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold transition ${isActive ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 hover:bg-amber-500/20 hover:text-amber-300" : "bg-slate-800 text-slate-400 border border-slate-700 hover:bg-emerald-500/20 hover:text-emerald-300"}" title="${isActive ? 'Schedule is Active. Click to pause and remove future scheduled occurrences.' : 'Schedule is Paused. Click to activate and generate scheduled transactions.'}">
            <i data-lucide="${isActive ? 'check-circle' : 'pause-circle'}" class="w-3.5 h-3.5"></i>
            <span>${isActive ? "Active" : "Paused"}</span>
          </button>
        </td>
        <td class="py-2.5 px-4 text-right space-x-1">
          <button onclick="deleteRecurring(${r.id})" class="text-slate-400 hover:text-rose-400 p-1" title="Delete recurring schedule">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        </td>
      </tr>
    `;
  }).join("");
  lucide.createIcons();
}

let isTogglingRecurring = false;

async function toggleRecurringStatus(id, newStatus) {
  if (isTogglingRecurring) return;
  isTogglingRecurring = true;
  const btn = document.getElementById(`rec-status-btn-${id}`);
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`/api/recurring/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: Boolean(newStatus) })
    });
    if (!res.ok) throw new Error("Update failed");
    await loadRecurring();
    showToast(newStatus ? "Recurring schedule activated — transactions added to ledger!" : "Recurring schedule paused — future transactions removed.");
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    isTogglingRecurring = false;
  }
}

async function submitRecurringForm() {
  const desc = document.getElementById("rec-form-desc").value.trim();
  const accId = parseInt(document.getElementById("rec-form-account").value);
  const rawAmt = parseFloat(document.getElementById("rec-form-amount").value);
  const freq = document.getElementById("rec-form-frequency").value;
  const dom = parseInt(document.getElementById("rec-form-dom").value) || null;
  const startDate = document.getElementById("rec-form-start-date").value;
  const endDate = document.getElementById("rec-form-end-date").value || null;

  if (!desc || !accId || isNaN(rawAmt) || !startDate) {
    showToast("Please fill all required fields.", "error");
    return;
  }

  const amount = round2(recAmountSign * Math.abs(rawAmt));

  try {
    const res = await fetch("/api/recurring", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: accId,
        amount: amount,
        description: desc,
        frequency: freq,
        day_of_month: dom,
        start_date: startDate,
        end_date: endDate,
        active: true
      })
    });
    if (!res.ok) throw new Error("Recurring rule creation failed");
    closeModal('modal-add-recurring');
    showToast("Recurring rule created!");
    await loadRecurring();
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteRecurring(id) {
  const confirmed = await askConfirm(
    `Delete Recurring Schedule?`,
    `Are you sure you want to delete this recurring schedule and remove its future scheduled occurrences?`,
    `Delete Schedule`
  );
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/recurring/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    showToast("Recurring rule deleted");
    await loadRecurring();
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// --- Excel Import & Export ---

function setupDropZone() {
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");

  dropZone.addEventListener("click", () => fileInput.click());

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("border-emerald-500", "bg-emerald-500/5");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("border-emerald-500", "bg-emerald-500/5");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-emerald-500", "bg-emerald-500/5");
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      handleFileSelect({ target: fileInput });
    }
  });
}

let parsedWorkbookData = null;

async function handleFileSelect(event) {
  const file = event.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  try {
    showToast("Analyzing and parsing workbook...", "info");
    const res = await fetch("/api/import/preview", {
      method: "POST",
      body: formData
    });
    if (!res.ok) throw new Error("Failed to parse file");

    parsedWorkbookData = await res.json();
    const data = parsedWorkbookData;

    // Render Preview Section
    document.getElementById("import-preview-section").classList.remove("hidden");
    document.getElementById("import-preview-title").textContent = `Workbook: ${data.filename}`;

    // Type badge
    const typeBadge = document.getElementById("import-preview-type-badge");
    if (data.is_multisheet) {
      typeBadge.textContent = `${data.summary.sheets_count} Sheets`;
      typeBadge.className = "text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
    } else {
      typeBadge.textContent = "Single File";
      typeBadge.className = "text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400 border border-blue-500/30";
    }

    document.getElementById("import-preview-stats").textContent = 
      `${(data.transactions || []).length} transactions and ${(data.accounts || []).length} accounts detected (${data.summary.total_errors} notes/warnings)`;

    // Update Counts in Preview Tab headers
    document.getElementById("prev-tx-count").textContent = (data.transactions || []).length;
    document.getElementById("prev-acc-count").textContent = (data.accounts || []).length;
    document.getElementById("prev-sheets-count").textContent = (data.sheets || []).length;

    // Reserves option
    const resOption = document.getElementById("import-reserves-option");
    const resLabel = document.getElementById("import-reserves-label");
    if (data.reserves && data.reserves.base_cash_reserves > 0) {
      resOption.classList.remove("hidden");
      resLabel.textContent = `Apply Starting Cash Reserves ($${formatCurrency(data.reserves.base_cash_reserves)} as of ${data.reserves.base_date || 'detected date'})`;
      document.getElementById("import-apply-reserves").checked = true;
    } else {
      resOption.classList.add("hidden");
      document.getElementById("import-apply-reserves").checked = false;
    }

    // Validation warnings
    const errBox = document.getElementById("import-errors-container");
    if (data.validation_errors && data.validation_errors.length > 0) {
      errBox.classList.remove("hidden");
      errBox.innerHTML = `<b>⚠️ Validation Notices (${data.validation_errors.length}):</b><br>` + 
        data.validation_errors.slice(0, 30).map(e => `• ${escapeHtml(e)}`).join("<br>") + 
        (data.validation_errors.length > 30 ? `<br><i>...and ${data.validation_errors.length - 30} more</i>` : '');
    } else {
      errBox.classList.add("hidden");
    }

    renderImportPreviewTxTable();
    renderImportPreviewAccTable();
    renderImportPreviewSheetsTable();

    updateImportModeUI();
    switchImportPreviewTab('tx');
    showToast("Workbook analyzed successfully!");
  } catch (err) {
    showToast(err.message, "error");
  }
}

let prevTxSort = { col: "transaction_date", dir: "asc" };
let prevAccSort = { col: "account_id", dir: "asc" };
let prevSheetsSort = { col: "sheet_name", dir: "asc" };

function renderImportPreviewTxTable() {
  if (!parsedWorkbookData) return;
  const txTbody = document.getElementById("import-preview-tbody");
  txTbody.innerHTML = (parsedWorkbookData.transactions || []).slice(0, 100).map(r => `
    <tr class="hover:bg-slate-800/40">
      <td class="py-2 px-3 whitespace-nowrap">${r.transaction_date}</td>
      <td class="py-2 px-3 font-semibold">${r.account_id}</td>
      <td class="py-2 px-3 font-sans">${escapeHtml(r.account_name)}</td>
      <td class="py-2 px-3 text-right font-bold ${r.transaction_amount >= 0 ? "text-emerald-400" : "text-rose-400"}">${formatCurrency(r.transaction_amount)}</td>
      <td class="py-2 px-3 font-sans text-slate-400">${escapeHtml(r.description || '-')}</td>
    </tr>
  `).join("");
}

function renderImportPreviewAccTable() {
  if (!parsedWorkbookData) return;
  const accTbody = document.getElementById("import-preview-acc-tbody");
  accTbody.innerHTML = (parsedWorkbookData.accounts || []).map(a => `
    <tr class="hover:bg-slate-800/40">
      <td class="py-2 px-3 font-semibold">${a.account_id}</td>
      <td class="py-2 px-3 font-sans">${escapeHtml(a.account_name)}</td>
      <td class="py-2 px-3 font-sans">
        <span class="inline-flex px-2 py-0.5 rounded text-[10px] font-medium ${getCategoryBadgeStyle(a.type)}">
          ${escapeHtml(a.type || 'Expenses')}
        </span>
      </td>
    </tr>
  `).join("");
}

function renderImportPreviewSheetsTable() {
  if (!parsedWorkbookData) return;
  const sheetsTbody = document.getElementById("import-preview-sheets-tbody");
  sheetsTbody.innerHTML = (parsedWorkbookData.sheets || []).map(s => `
    <tr class="hover:bg-slate-800/40">
      <td class="py-2 px-3 font-sans font-semibold text-white">${escapeHtml(s.sheet_name)}</td>
      <td class="py-2 px-3 font-sans text-slate-300">${s.role}</td>
      <td class="py-2 px-3 text-right font-mono text-emerald-400 font-bold">${s.record_count}</td>
      <td class="py-2 px-3 font-sans">
        ${s.errors_count > 0 
          ? `<span class="text-amber-400 font-medium">${s.errors_count} warnings</span>` 
          : `<span class="text-emerald-400 font-medium">Valid</span>`}
      </td>
    </tr>
  `).join("");
}

function toggleSortPreview(table, col) {
  if (!parsedWorkbookData) return;
  if (table === 'tx') {
    prevTxSort.dir = (prevTxSort.col === col && prevTxSort.dir === "asc") ? "desc" : "asc";
    prevTxSort.col = col;
    const dir = prevTxSort.dir === "asc" ? 1 : -1;
    (parsedWorkbookData.transactions || []).sort((a, b) => {
      if (col === "transaction_amount" || col === "account_id") {
        return ((parseFloat(a[col]) || 0) - (parseFloat(b[col]) || 0)) * dir;
      }
      return String(a[col] || "").localeCompare(String(b[col] || "")) * dir;
    });
    renderImportPreviewTxTable();
    updatePreviewSortIcons('tx', col, prevTxSort.dir, ["transaction_date", "account_id", "account_name", "transaction_amount", "description"]);
  } else if (table === 'acc') {
    prevAccSort.dir = (prevAccSort.col === col && prevAccSort.dir === "asc") ? "desc" : "asc";
    prevAccSort.col = col;
    const dir = prevAccSort.dir === "asc" ? 1 : -1;
    (parsedWorkbookData.accounts || []).sort((a, b) => {
      if (col === "account_id") return ((parseInt(a[col]) || 0) - (parseInt(b[col]) || 0)) * dir;
      return String(a[col] || "").localeCompare(String(b[col] || "")) * dir;
    });
    renderImportPreviewAccTable();
    updatePreviewSortIcons('acc', col, prevAccSort.dir, ["account_id", "account_name", "type"]);
  } else if (table === 'sheets') {
    prevSheetsSort.dir = (prevSheetsSort.col === col && prevSheetsSort.dir === "asc") ? "desc" : "asc";
    prevSheetsSort.col = col;
    const dir = prevSheetsSort.dir === "asc" ? 1 : -1;
    (parsedWorkbookData.sheets || []).sort((a, b) => {
      if (col === "record_count") return ((a[col] || 0) - (b[col] || 0)) * dir;
      return String(a[col] || "").localeCompare(String(b[col] || "")) * dir;
    });
    renderImportPreviewSheetsTable();
    updatePreviewSortIcons('sheets', col, prevSheetsSort.dir, ["sheet_name", "role", "record_count", "status"]);
  }
}

function updatePreviewSortIcons(table, activeCol, dir, allCols) {
  allCols.forEach(c => {
    const icon = document.getElementById(`sort-icon-prev-${table}-${c}`);
    if (!icon) return;
    if (c === activeCol) {
      icon.textContent = dir === "asc" ? "▲" : "▼";
      icon.className = "text-emerald-400 font-bold text-[10px]";
    } else {
      icon.textContent = "↕";
      icon.className = "text-slate-600 group-hover:text-slate-400 text-[10px]";
    }
  });
}

function updateImportModeUI() {
  const modeRadio = document.querySelector('input[name="import-mode"]:checked');
  const mode = modeRadio ? modeRadio.value : "merge";
  const btn = document.getElementById("btn-commit-import");
  const btnText = document.getElementById("btn-commit-import-text");

  if (mode === "overwrite") {
    btn.className = "px-5 py-2 text-xs font-semibold bg-rose-600 hover:bg-rose-500 text-white rounded-xl shadow-lg transition flex items-center space-x-1.5 animate-pulse";
    btnText.textContent = "⚠️ Confirm & Overwrite All Data";
  } else {
    btn.className = "px-5 py-2 text-xs font-semibold bg-emerald-500 hover:bg-emerald-400 text-slate-950 rounded-xl shadow-lg transition flex items-center space-x-1.5";
    btnText.textContent = "Confirm & Merge Records";
  }
}

function switchImportPreviewTab(tab) {
  const tabs = ['tx', 'acc', 'sheets'];
  tabs.forEach(t => {
    const el = document.getElementById(`prev-tab-${t}`);
    const btn = document.getElementById(`tab-btn-prev-${t}`);
    if (t === tab) {
      el.classList.remove("hidden");
      btn.className = "pb-2 border-b-2 border-emerald-400 text-emerald-400";
    } else {
      el.classList.add("hidden");
      btn.className = "pb-2 border-b-2 border-transparent text-slate-400 hover:text-white";
    }
  });
}

function cancelImportPreview() {
  document.getElementById("import-preview-section").classList.add("hidden");
  parsedWorkbookData = null;
  document.getElementById("file-input").value = "";
}

async function commitFullImport() {
  if (!parsedWorkbookData) return;
  const modeRadio = document.querySelector('input[name="import-mode"]:checked');
  const mode = modeRadio ? modeRadio.value : "merge";

  if (mode === "overwrite") {
    const confirmOverwrite = await askConfirm(
      "Overwrite Entire Database?",
      "Overwrite mode will CLEAR all current transactions and reset your accounts to match this workbook. Are you sure you want to proceed?",
      "Overwrite All Data"
    );
    if (!confirmOverwrite) return;
  }

  const applyReserves = document.getElementById("import-apply-reserves").checked;
  const autoCreate = document.getElementById("import-auto-create-missing").checked;

  try {
    showToast("Importing workbook records...", "info");
    const res = await fetch("/api/import/commit-full", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: mode,
        accounts: parsedWorkbookData.accounts || [],
        transactions: parsedWorkbookData.transactions || [],
        reserves: parsedWorkbookData.reserves,
        update_reserves: applyReserves,
        auto_create_missing_accounts: autoCreate
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Workbook import failed");
    }

    const result = await res.json();
    showToast(`Successfully imported ${result.transactions_imported} transactions and ${result.accounts_imported} accounts!`);
    cancelImportPreview();
    await recalculateAllBalances();
    switchTab('transactions');
  } catch (err) {
    showToast(err.message, "error");
  }
}

function exportTransactionsExcel() {
  window.location.href = "/api/export/transactions";
}

function downloadExcelTemplate() {
  window.location.href = "/api/template/excel";
}

// --- Account Detail View Logic ---

let currentDetailAccountId = null;
let currentDetailAccountData = null;
let currentDetailTransactions = [];
let currentDetailSortedIds = [];
let selectedDetailTxIds = new Set();
let lastClickedDetailTxId = null;
let detailTxSortBy = "date";
let detailTxSortDir = "asc";
let editingDetailTxId = null;
let quickTxAmountSign = -1; // -1 for outflow, 1 for inflow
let detailTxSearchTimeout = null;

function setQuickTxSign(sign) {
  quickTxAmountSign = sign;
  const expBtn = document.getElementById("btn-quick-sign-expense");
  const incBtn = document.getElementById("btn-quick-sign-income");
  if (sign > 0) {
    incBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
    expBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-rose-400 border border-transparent";
  } else {
    expBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30";
    incBtn.className = "flex-1 py-1 rounded-lg text-[11px] font-semibold bg-slate-800 text-slate-400 hover:text-emerald-400 border border-transparent";
  }
}

async function openAccountDetail(accountId) {
  currentDetailAccountId = accountId;
  editingDetailTxId = null;
  selectedDetailTxIds.clear();
  lastClickedDetailTxId = null;
  switchTab('account-detail');
  
  // Set default date for quick row
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const dateInput = document.getElementById("quick-tx-date");
  if (dateInput) dateInput.value = `${yyyy}-${mm}-${dd}`;
  
  const accIdLabel = document.getElementById("quick-tx-acc-id-label");
  if (accIdLabel) accIdLabel.textContent = accountId;

  // Reset filters
  const sInput = document.getElementById("detail-tx-search");
  if (sInput) sInput.value = "";
  const fShowSettled = document.getElementById("detail-tx-show-settled");
  if (fShowSettled) fShowSettled.checked = false;
  const fStart = document.getElementById("detail-tx-filter-start");
  if (fStart) fStart.value = "";
  const fEnd = document.getElementById("detail-tx-filter-end");
  if (fEnd) fEnd.value = "";

  detailTxSortBy = "date";
  detailTxSortDir = "asc";
  updateDetailTxSortUI();

  await loadAccountDetail(accountId);
}

function closeAccountDetail() {
  currentDetailAccountId = null;
  selectedDetailTxIds.clear();
  lastClickedDetailTxId = null;
  switchTab('accounts');
}

async function loadAccountDetail(accountId) {
  try {
    const accRes = await fetch(`/api/accounts/${accountId}`);
    if (!accRes.ok) throw new Error("Failed to load account details");
    currentDetailAccountData = await accRes.json();

    await loadAccountDetailTransactions();
  } catch (err) {
    console.error("Error loading account detail:", err);
    showToast(err.message, "error");
  }
}

async function loadAccountDetailTransactions() {
  if (!currentDetailAccountId) return;
  const search = (document.getElementById("detail-tx-search")?.value || "").trim();
  const showSettled = document.getElementById("detail-tx-show-settled")?.checked || false;
  const statusVal = showSettled ? "" : "pending";
  const startDate = document.getElementById("detail-tx-filter-start")?.value || "";
  const endDate = document.getElementById("detail-tx-filter-end")?.value || "";

  const params = new URLSearchParams();
  params.append("account_id", currentDetailAccountId);
  params.append("limit", "10000");
  if (search) params.append("search", search);
  if (statusVal) params.append("status", statusVal);
  if (startDate) params.append("start_date", startDate);
  if (endDate) params.append("end_date", endDate);

  try {
    const res = await fetch(`/api/transactions?${params.toString()}`);
    if (!res.ok) throw new Error("Failed to load account transactions");
    currentDetailTransactions = await res.json();

    // Clean up any selected IDs that are no longer in currentDetailTransactions
    const visibleIds = new Set(currentDetailTransactions.map(t => t.transaction_id));
    for (const id of selectedDetailTxIds) {
      if (!visibleIds.has(id)) selectedDetailTxIds.delete(id);
    }

    renderAccountDetailUI();
  } catch (err) {
    console.error("Error loading account transactions:", err);
    showToast(err.message, "error");
  }
}

function updateDetailBulkActionBar() {
  const bar = document.getElementById("detail-tx-bulk-bar");
  const countBadge = document.getElementById("detail-tx-bulk-count");
  const selectAllEl = document.getElementById("detail-tx-select-all");

  const count = selectedDetailTxIds.size;
  const total = currentDetailTransactions.length;
  if (countBadge) countBadge.textContent = count;

  if (bar) {
    if (count > 0) {
      bar.classList.remove("hidden");
      bar.classList.add("flex");
    } else {
      bar.classList.add("hidden");
      bar.classList.remove("flex");
    }
  }

  if (selectAllEl) {
    if (total > 0 && count === total) {
      selectAllEl.checked = true;
      selectAllEl.indeterminate = false;
    } else if (count > 0 && count < total) {
      selectAllEl.checked = false;
      selectAllEl.indeterminate = true;
    } else {
      selectAllEl.checked = false;
      selectAllEl.indeterminate = false;
    }
  }
}

function applyDetailRowSelectionStyle(txId, isChecked) {
  const row = document.getElementById(`detail-tx-row-${txId}`);
  const cb = document.getElementById(`detail-tx-cb-${txId}`);
  if (cb) cb.checked = isChecked;
  if (row) {
    if (isChecked) {
      row.classList.add("bg-emerald-950/25", "border-l-2", "border-emerald-400");
    } else {
      row.classList.remove("bg-emerald-950/25", "border-l-2", "border-emerald-400");
    }
  }
}

function onToggleDetailTxSelect(txId, isChecked, event) {
  if (event && event.shiftKey && lastClickedDetailTxId !== null && currentDetailSortedIds.length > 0) {
    const startIdx = currentDetailSortedIds.indexOf(lastClickedDetailTxId);
    const endIdx = currentDetailSortedIds.indexOf(txId);
    if (startIdx !== -1 && endIdx !== -1) {
      const low = Math.min(startIdx, endIdx);
      const high = Math.max(startIdx, endIdx);
      for (let i = low; i <= high; i++) {
        const rangeId = currentDetailSortedIds[i];
        if (isChecked) {
          selectedDetailTxIds.add(rangeId);
        } else {
          selectedDetailTxIds.delete(rangeId);
        }
        applyDetailRowSelectionStyle(rangeId, isChecked);
      }
      lastClickedDetailTxId = txId;
      updateDetailBulkActionBar();
      return;
    }
  }

  if (isChecked) {
    selectedDetailTxIds.add(txId);
  } else {
    selectedDetailTxIds.delete(txId);
  }
  lastClickedDetailTxId = txId;
  applyDetailRowSelectionStyle(txId, isChecked);
  updateDetailBulkActionBar();
}

function toggleSelectAllDetailTransactions(isChecked) {
  if (isChecked) {
    currentDetailTransactions.forEach(t => selectedDetailTxIds.add(t.transaction_id));
  } else {
    selectedDetailTxIds.clear();
  }
  renderAccountDetailUI();
}

function clearSelectedDetailTransactions() {
  selectedDetailTxIds.clear();
  lastClickedDetailTxId = null;
  renderAccountDetailUI();
}

async function bulkSetDetailTxStatus(targetStatus) {
  const ids = Array.from(selectedDetailTxIds);
  if (ids.length === 0) return;

  const showSettled = document.getElementById("detail-tx-show-settled")?.checked || false;
  const isSettledTarget = targetStatus !== "pending";

  if (!showSettled && isSettledTarget) {
    const idSet = new Set(ids);
    currentDetailTransactions = currentDetailTransactions.filter(t => !idSet.has(t.transaction_id));
    selectedDetailTxIds.clear();
    renderAccountDetailUI();
  }

  try {
    const res = await fetch("/api/transactions/bulk-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transaction_ids: ids,
        status: targetStatus
      })
    });
    if (!res.ok) throw new Error("Bulk update failed");
    const data = await res.json();

    selectedDetailTxIds.clear();
    if (isSettledTarget) {
      showToast(`${data.count || ids.length} transactions marked as Paid / Received & removed from calculation!`);
    } else {
      showToast(`${data.count || ids.length} transactions restored to Pending!`);
    }

    await recalculateAllBalances();
  } catch (err) {
    console.error("Bulk detail status error:", err);
    showToast(err.message || "Failed to update transactions", "error");
    loadAccountDetailTransactions();
  }
}

async function bulkDeleteSelectedDetailTransactions() {
  const ids = Array.from(selectedDetailTxIds);
  if (ids.length === 0) return;

  const confirmed = await askConfirm(
    `Delete ${ids.length} Transaction${ids.length > 1 ? 's' : ''}?`,
    `Are you sure you want to permanently delete ${ids.length} selected transaction${ids.length > 1 ? 's' : ''} from this account?`,
    `Delete ${ids.length} Transaction${ids.length > 1 ? 's' : ''}`
  );
  if (!confirmed) return;

  const idSet = new Set(ids);
  currentDetailTransactions = currentDetailTransactions.filter(t => !idSet.has(t.transaction_id));
  selectedDetailTxIds.clear();
  renderAccountDetailUI();

  try {
    const res = await fetch("/api/transactions/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transaction_ids: ids })
    });
    if (!res.ok) throw new Error("Bulk delete failed");
    showToast(`${ids.length} transactions deleted`);

    await recalculateAllBalances();
  } catch (err) {
    console.error("Bulk detail delete error:", err);
    showToast(err.message || "Failed to delete transactions", "error");
    loadAccountDetailTransactions();
  }
}

function debounceDetailTxSearch() {
  clearTimeout(detailTxSearchTimeout);
  detailTxSearchTimeout = setTimeout(() => {
    loadAccountDetailTransactions();
  }, 250);
}

function resetDetailTxFilters() {
  document.getElementById("detail-tx-search").value = "";
  const fShowSettled = document.getElementById("detail-tx-show-settled");
  if (fShowSettled) fShowSettled.checked = false;
  document.getElementById("detail-tx-filter-start").value = "";
  document.getElementById("detail-tx-filter-end").value = "";
  loadAccountDetailTransactions();
}

function toggleSortDetailTx(col) {
  if (detailTxSortBy === col) {
    detailTxSortDir = detailTxSortDir === "asc" ? "desc" : "asc";
  } else {
    detailTxSortBy = col;
    detailTxSortDir = (col === "amount") ? "desc" : "asc";
  }
  updateDetailTxSortUI();
  renderAccountDetailUI();
}

function updateDetailTxSortUI() {
  const cols = ["date", "description", "amount", "balance", "status"];
  cols.forEach(c => {
    const icon = document.getElementById(`sort-icon-detail-${c}`);
    const th = document.getElementById(`th-detail-${c}`);
    if (!icon) return;
    if (detailTxSortBy === c) {
      icon.textContent = detailTxSortDir === "asc" ? "▲" : "▼";
      icon.className = "text-emerald-400 font-bold text-[11px]";
      if (th) th.className = "text-emerald-400 font-bold";
    } else {
      icon.textContent = "↕";
      icon.className = "text-slate-600 group-hover:text-slate-400 text-[11px]";
      if (th) th.className = "text-slate-400 group-hover:text-white";
    }
  });
}

function renderAccountDetailUI() {
  if (!currentDetailAccountData) return;
  const a = currentDetailAccountData;
  const isHidden = a.is_hidden === 1 || a.is_hidden === true;

  // Header info
  document.getElementById("detail-account-name").textContent = a.account_name;
  document.getElementById("detail-account-id-badge").textContent = `#${a.account_id}`;
  
  const typeBadge = document.getElementById("detail-account-type-badge");
  typeBadge.textContent = a.type || "General";
  typeBadge.className = `text-xs px-2.5 py-0.5 rounded-full font-semibold ${getCategoryBadgeStyle(a.type)}`;

  // Forecast status button
  const statusBtn = document.getElementById("detail-toggle-hidden-btn");
  if (statusBtn) {
    if (isHidden) {
      statusBtn.className = "flex items-center space-x-1.5 px-3 py-2 rounded-xl text-xs font-semibold bg-slate-800 text-amber-400 hover:text-emerald-300 border border-amber-500/30 transition shadow";
      statusBtn.innerHTML = `<i data-lucide="eye-off" class="w-3.5 h-3.5"></i><span>Excluded from Forecast</span>`;
    } else {
      statusBtn.className = "flex items-center space-x-1.5 px-3 py-2 rounded-xl text-xs font-semibold bg-emerald-500/10 text-emerald-400 hover:text-amber-300 border border-emerald-500/30 transition shadow";
      statusBtn.innerHTML = `<i data-lucide="eye" class="w-3.5 h-3.5"></i><span>Included in Forecast</span>`;
    }
  }

  // Calculate stats from pending transactions in this account (paid/received are excluded from calculation)
  const startBal = parseFloat(a.starting_balance) || 0;
  
  // Compute totals (only pending items affect active calculation)
  let totalInflows = 0;
  let totalOutflows = 0;
  let netActivity = 0;
  let pendingCount = 0;
  let settledCount = 0;
  currentDetailTransactions.forEach(t => {
    const isSettled = (t.status || "pending").toLowerCase() !== "pending";
    if (isSettled) {
      settledCount++;
      return;
    }
    pendingCount++;
    const amt = parseFloat(t.transaction_amount) || 0;
    if (amt > 0) totalInflows += amt;
    else if (amt < 0) totalOutflows += Math.abs(amt);
    netActivity += amt;
  });

  const totalAccountSettled = Math.max(settledCount, parseInt(a.settled_count) || 0);
  const calculatedBal = startBal + netActivity;

  // Update KPI Cards
  document.getElementById("detail-stat-starting-balance").textContent = formatCurrency(startBal);
  document.getElementById("detail-stat-inflows").textContent = `+${formatCurrency(totalInflows)}`;
  document.getElementById("detail-stat-outflows").textContent = `-${formatCurrency(totalOutflows)}`;
  
  const netEl = document.getElementById("detail-stat-net");
  netEl.textContent = (netActivity >= 0 ? "+" : "") + formatCurrency(netActivity);
  netEl.className = `text-xl font-bold font-mono ${netActivity >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;

  const calcEl = document.getElementById("detail-stat-calculated-balance");
  calcEl.textContent = formatCurrency(calculatedBal);
  calcEl.className = `text-2xl font-extrabold font-mono ${calculatedBal >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;

  document.getElementById("detail-stat-tx-count").textContent = totalAccountSettled > 0
    ? `${pendingCount} pending (${totalAccountSettled} settled excluded)`
    : `${pendingCount} pending transactions`;
  const detailShowSettled = document.getElementById("detail-tx-show-settled")?.checked || false;
  document.getElementById("detail-tx-table-count").textContent = detailShowSettled
    ? `${currentDetailTransactions.length} transactions shown (${pendingCount} pending, ${settledCount} paid/received)`
    : `${pendingCount} pending transactions shown${totalAccountSettled > 0 ? ` (${totalAccountSettled} paid/received hidden)` : ''}`;

  // Compute Running Balance for each transaction:
  // Sort chronologically ascending to accumulate balance starting from startBal (excluding settled items)
  const chronological = [...currentDetailTransactions].sort((t1, t2) => {
    const cmp = (t1.transaction_date || "").localeCompare(t2.transaction_date || "");
    return cmp !== 0 ? cmp : (t1.transaction_id || 0) - (t2.transaction_id || 0);
  });

  let running = startBal;
  const runningMap = new Map();
  chronological.forEach(t => {
    const isSettled = (t.status || "pending").toLowerCase() !== "pending";
    if (!isSettled) {
      running += parseFloat(t.transaction_amount) || 0;
    }
    runningMap.set(t.transaction_id, running);
  });

  // Now sort transactions according to detailTxSortBy and detailTxSortDir
  const sortedTxs = [...currentDetailTransactions];
  const dir = detailTxSortDir === "asc" ? 1 : -1;
  sortedTxs.sort((t1, t2) => {
    if (detailTxSortBy === "date") {
      const cmp = (t1.transaction_date || "").localeCompare(t2.transaction_date || "");
      return cmp !== 0 ? cmp * dir : ((t1.transaction_id || 0) - (t2.transaction_id || 0)) * dir;
    }
    if (detailTxSortBy === "description") {
      const d1 = (t1.description || "").toLowerCase();
      const d2 = (t2.description || "").toLowerCase();
      return d1.localeCompare(d2) * dir;
    }
    if (detailTxSortBy === "amount") {
      return ((parseFloat(t1.transaction_amount) || 0) - (parseFloat(t2.transaction_amount) || 0)) * dir;
    }
    if (detailTxSortBy === "balance") {
      const b1 = runningMap.get(t1.transaction_id) || 0;
      const b2 = runningMap.get(t2.transaction_id) || 0;
      return (b1 - b2) * dir;
    }
    if (detailTxSortBy === "status") {
      const s1 = (t1.status || "pending").toLowerCase();
      const s2 = (t2.status || "pending").toLowerCase();
      return s1.localeCompare(s2) * dir;
    }
    return 0;
  });

  currentDetailSortedIds = sortedTxs.map(t => t.transaction_id);
  updateDetailBulkActionBar();

  // Render Table
  const tbody = document.getElementById("detail-tx-table-tbody");
  if (sortedTxs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-12 text-slate-500 font-sans">No transactions recorded for this account. Use the Quick Add form above to add rows!</td></tr>`;
    lucide.createIcons();
    return;
  }

  tbody.innerHTML = sortedTxs.map(t => {
    const amt = parseFloat(t.transaction_amount) || 0;
    const isIncome = amt >= 0;
    const isSettled = (t.status || "pending").toLowerCase() !== "pending";
    const isSelected = selectedDetailTxIds.has(t.transaction_id);
    const amtColor = isIncome ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
    const amtStyle = isSettled ? "line-through opacity-50" : "";
    const rowStyle = isSettled ? "bg-slate-900/30 opacity-75" : "";
    const selectedStyle = isSelected ? "bg-emerald-950/25 border-l-2 border-emerald-400" : "";
    const prefix = isIncome ? "+" : "";
    const runBal = runningMap.get(t.transaction_id) || 0;
    const runBalColor = isSettled ? "text-slate-500" : (runBal >= 0 ? "text-slate-200" : "text-rose-400 font-bold");
    const recurringBadge = t.recurring_rule_id
      ? `<span class="inline-flex items-center text-[10px] font-sans px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 ml-2" title="Recurring Rule #${t.recurring_rule_id}"><i data-lucide="repeat" class="w-3 h-3 mr-0.5"></i>Recurring</span>`
      : "";

    if (t.transaction_id === editingDetailTxId) {
      return `
        <tr id="detail-tx-row-${t.transaction_id}" class="bg-emerald-950/30 border-y-2 border-emerald-500/50 transition">
          <td class="py-2.5 px-3 text-center">
            <input type="checkbox" id="detail-tx-cb-${t.transaction_id}" ${isSelected ? 'checked' : ''} onclick="onToggleDetailTxSelect(${t.transaction_id}, this.checked, event)" class="rounded border-slate-600 bg-slate-900 text-emerald-500 focus:ring-0 w-3.5 h-3.5 cursor-pointer">
          </td>
          <td class="py-2.5 px-3 whitespace-nowrap">
            <input type="date" id="inline-date-${t.transaction_id}" value="${t.transaction_date}" class="bg-slate-950 border border-emerald-500 rounded-lg px-2 py-1 text-xs text-white font-mono focus:outline-none focus:ring-1 focus:ring-emerald-400">
          </td>
          <td class="py-2.5 px-3 font-sans">
            <input type="text" id="inline-desc-${t.transaction_id}" value="${escapeHtml(t.description || '')}" placeholder="Description / Memo" onkeydown="if(event.key==='Enter') saveInlineDetailTx(${t.transaction_id})" class="w-full bg-slate-950 border border-emerald-500 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-emerald-400">
          </td>
          <td class="py-2.5 px-3 text-right">
            <input type="number" step="0.01" id="inline-amt-${t.transaction_id}" value="${amt}" onkeydown="if(event.key==='Enter') saveInlineDetailTx(${t.transaction_id})" class="w-28 bg-slate-950 border border-emerald-500 rounded-lg px-2 py-1 text-xs text-right font-mono text-white focus:outline-none focus:ring-1 focus:ring-emerald-400" placeholder="+/- 0.00" title="Negative for expense/outflow, positive for income/inflow">
          </td>
          <td class="py-2.5 px-3 text-right font-mono text-xs text-slate-400 italic">
            Auto
          </td>
          <td class="py-2.5 px-3 whitespace-nowrap">
            <select id="inline-status-${t.transaction_id}" class="bg-slate-950 border border-emerald-500 rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-emerald-400">
              <option value="pending" ${(t.status || 'pending').toLowerCase() === 'pending' ? 'selected' : ''}>Pending</option>
              <option value="${amt < 0 ? 'paid' : 'received'}" ${(t.status || 'pending').toLowerCase() !== 'pending' ? 'selected' : ''}>${amt < 0 ? 'Paid' : 'Received'}</option>
            </select>
          </td>
          <td class="py-2.5 px-3 text-right space-x-1.5 whitespace-nowrap">
            <button type="button" onclick="saveInlineDetailTx(${t.transaction_id})" class="px-2.5 py-1 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-lg text-xs inline-flex items-center space-x-1 shadow transition" title="Save changes">
              <i data-lucide="check" class="w-3.5 h-3.5"></i>
              <span>Save</span>
            </button>
            <button type="button" onclick="cancelInlineDetailTx()" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs inline-flex items-center transition" title="Cancel edit">
              <i data-lucide="x" class="w-3.5 h-3.5"></i>
            </button>
          </td>
        </tr>
      `;
    }

    return `
      <tr id="detail-tx-row-${t.transaction_id}" class="hover:bg-slate-800/40 transition group ${rowStyle} ${selectedStyle}">
        <td class="py-2.5 px-3 text-center">
          <input type="checkbox" id="detail-tx-cb-${t.transaction_id}" ${isSelected ? 'checked' : ''} onclick="onToggleDetailTxSelect(${t.transaction_id}, this.checked, event)" class="rounded border-slate-600 bg-slate-900 text-emerald-500 focus:ring-0 w-3.5 h-3.5 cursor-pointer" title="Select transaction (Shift+Click to select range)">
        </td>
        <td class="py-2.5 px-4 font-semibold text-slate-200 whitespace-nowrap">${t.transaction_date}</td>
        <td class="py-2.5 px-4 font-sans text-slate-300">
          <div class="flex items-center">
            <span class="font-medium text-white">${escapeHtml(t.description || '-')}</span>
            ${recurringBadge}
          </div>
        </td>
        <td class="py-2.5 px-4 text-right font-mono ${amtColor} ${amtStyle}" title="${isSettled ? 'Excluded from balance calculation (already Paid/Received)' : 'Included in balance calculation'}">${prefix}${formatCurrency(amt)}</td>
        <td class="py-2.5 px-4 text-right font-mono ${runBalColor}">
          ${formatCurrency(runBal)}${isSettled ? '<span class="text-[10px] text-slate-500 font-sans ml-1">(excl)</span>' : ''}
        </td>
        <td class="py-2.5 px-4 whitespace-nowrap">
          ${renderTxStatusBadge(t)}
        </td>
        <td class="py-2.5 px-4 text-right space-x-1 whitespace-nowrap">
          <button type="button" onclick="startInlineEditDetailTx(${t.transaction_id})" class="text-slate-400 hover:text-emerald-400 p-1.5 rounded-lg hover:bg-slate-800 transition" title="Edit row directly in table">
            <i data-lucide="edit-3" class="w-4 h-4"></i>
          </button>
          <button type="button" onclick="editTransaction(${t.transaction_id})" class="text-slate-400 hover:text-blue-400 p-1.5 rounded-lg hover:bg-slate-800 transition" title="Open full edit dialog">
            <i data-lucide="maximize-2" class="w-4 h-4"></i>
          </button>
          <button type="button" onclick="deleteDetailTransaction(${t.transaction_id})" class="text-slate-400 hover:text-rose-400 p-1.5 rounded-lg hover:bg-slate-800 transition" title="Delete transaction">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        </td>
      </tr>
    `;
  }).join("");

  lucide.createIcons();
}

function startInlineEditDetailTx(txId) {
  editingDetailTxId = txId;
  renderAccountDetailUI();
  setTimeout(() => {
    const desc = document.getElementById(`inline-desc-${txId}`);
    if (desc) desc.focus();
  }, 40);
}

function cancelInlineDetailTx() {
  editingDetailTxId = null;
  renderAccountDetailUI();
}

async function saveInlineDetailTx(txId) {
  const dateInput = document.getElementById(`inline-date-${txId}`);
  const descInput = document.getElementById(`inline-desc-${txId}`);
  const amtInput = document.getElementById(`inline-amt-${txId}`);
  const statusInput = document.getElementById(`inline-status-${txId}`);
  if (!dateInput || !amtInput) return;

  const dateVal = dateInput.value;
  const descVal = descInput ? descInput.value.trim() : "";
  const amtVal = parseFloat(amtInput.value);
  const statusVal = statusInput ? statusInput.value : undefined;

  if (!dateVal || isNaN(amtVal)) {
    showToast("Please provide a valid date and amount", "error");
    return;
  }

  try {
    const res = await fetch(`/api/transactions/${txId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: currentDetailAccountId,
        transaction_date: dateVal,
        transaction_amount: round2(amtVal),
        description: descVal,
        status: statusVal
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to update transaction");
    }

    editingDetailTxId = null;
    showToast("Transaction updated!");
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function submitQuickAddDetailTx(event) {
  if (event) event.preventDefault();
  if (!currentDetailAccountId) return;

  const dateVal = document.getElementById("quick-tx-date").value;
  const rawAmt = parseFloat(document.getElementById("quick-tx-amount").value);
  const desc = document.getElementById("quick-tx-desc").value.trim();

  if (!dateVal || isNaN(rawAmt) || rawAmt <= 0) {
    showToast("Please enter a valid date and amount.", "error");
    return;
  }

  const finalAmt = round2(quickTxAmountSign * Math.abs(rawAmt));

  try {
    const res = await fetch("/api/transactions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: currentDetailAccountId,
        transaction_date: dateVal,
        transaction_amount: finalAmt,
        description: desc
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to add transaction");
    }

    showToast(`Added ${formatCurrency(finalAmt)} transaction!`);
    
    // Clear inputs (keep date)
    document.getElementById("quick-tx-amount").value = "";
    document.getElementById("quick-tx-desc").value = "";
    
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteDetailTransaction(txId) {
  const confirmed = await askConfirm(
    `Delete Transaction #${txId}?`,
    `Are you sure you want to delete transaction #${txId} from this account?`,
    `Delete Transaction`
  );
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/transactions/${txId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    showToast("Transaction deleted");
    await recalculateAllBalances();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openEditStartingBalancePrompt() {
  if (!currentDetailAccountId) return;
  editAccount(currentDetailAccountId);
}

function openEditCurrentAccountModal() {
  if (!currentDetailAccountId) return;
  editAccount(currentDetailAccountId);
}

function openAddTransactionForAccount() {
  if (!currentDetailAccountId) return;
  document.getElementById("tx-form-id").value = "";
  document.getElementById("tx-form-account").value = currentDetailAccountId;
  document.getElementById("modal-tx-title").textContent = `Add Transaction for Account #${currentDetailAccountId}`;
  openModal('modal-add-transaction');
}

async function toggleDetailAccountHidden() {
  if (!currentDetailAccountId) return;
  await toggleAccountHidden(currentDetailAccountId);
}

// --- Utilities ---

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}

function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
  if (id === 'modal-add-transaction') {
    document.getElementById("tx-form-id").value = "";
    document.getElementById("tx-form-amount").value = "";
    document.getElementById("tx-form-desc").value = "";
    setModalTxStatus('pending');
    document.getElementById("modal-tx-title").textContent = "Add Transaction";
  }
  if (id === 'modal-add-account') {
    document.getElementById("acc-form-mode").value = "create";
    document.getElementById("acc-form-id").value = "";
    document.getElementById("acc-form-id").disabled = false;
    document.getElementById("acc-form-name").value = "";
    document.getElementById("acc-form-starting-balance").value = "";
    document.getElementById("acc-form-hidden").checked = false;
    document.getElementById("modal-acc-title").textContent = "Add Account";
    if (accountModalContext === 'transaction') {
      document.getElementById('modal-add-transaction')?.classList.remove('hidden');
    } else if (accountModalContext === 'recurring') {
      document.getElementById('modal-add-recurring')?.classList.remove('hidden');
    }
    accountModalContext = 'accounts';
  }
}

function getCategoryBadgeStyle(cat) {
  const c = (cat || "").toLowerCase();
  if (c.includes("asset") || c.includes("cash")) return "bg-emerald-500/10 text-emerald-300 border border-emerald-500/20";
  if (c.includes("receivable")) return "bg-teal-500/10 text-teal-300 border border-teal-500/20";
  if (c.includes("revenue") || c.includes("income")) return "bg-cyan-500/10 text-cyan-300 border border-cyan-500/20";
  if (c.includes("credit card")) return "bg-amber-500/10 text-amber-300 border border-amber-500/20";
  if (c.includes("loan") || c.includes("mortgage") || c.includes("installment")) return "bg-purple-500/10 text-purple-300 border border-purple-500/20";
  if (c.includes("subscription")) return "bg-sky-500/10 text-sky-300 border border-sky-500/20";
  if (c.includes("expense")) return "bg-rose-500/10 text-rose-300 border border-rose-500/20";
  return "bg-slate-800 text-slate-300 border border-slate-700";
}

function round2(num) {
  return Math.round((num + Number.EPSILON) * 100) / 100;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str).replace(/[&<>"']/g, (m) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[m]);
}

// --- Custom Glass Confirmation Modal ---
function askConfirm(title, message, confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const modal = document.getElementById("modal-confirm");
    if (!modal) {
      resolve(window.confirm(message));
      return;
    }
    document.getElementById("confirm-modal-title").textContent = title || "Confirm Action";
    document.getElementById("confirm-modal-message").textContent = message || "Are you sure?";
    const okBtn = document.getElementById("confirm-modal-ok");
    const cancelBtn = document.getElementById("confirm-modal-cancel");
    okBtn.textContent = confirmLabel;

    const cleanup = (result) => {
      modal.classList.add("hidden");
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      resolve(result);
    };

    okBtn.onclick = () => cleanup(true);
    cancelBtn.onclick = () => cleanup(false);
    modal.classList.remove("hidden");
    lucide.createIcons();
  });
}

// --- Global Command Palette (Ctrl+K / Cmd+K) ---
let cmdPaletteItems = [];
let cmdPaletteSelectedIndex = 0;

function openCommandPalette() {
  const modal = document.getElementById("modal-command-palette");
  const input = document.getElementById("cmd-palette-input");
  if (!modal || !input) return;
  modal.classList.remove("hidden");
  input.value = "";
  cmdPaletteSelectedIndex = 0;
  renderCommandPaletteResults();
  setTimeout(() => input.focus(), 20);
}

function closeCommandPalette() {
  const modal = document.getElementById("modal-command-palette");
  if (modal) modal.classList.add("hidden");
}

function buildCommandPaletteCatalog(query) {
  const q = (query || "").trim().toLowerCase();
  const baseActions = [
    { category: "Actions", icon: "plus-circle", label: "New Transaction Entry", hint: "Add income or expense", run: () => openModal('modal-add-transaction') },
    { category: "Actions", icon: "folder-plus", label: "Create New Account", hint: "Auto-numbered chart of accounts", run: () => openAddAccountModalFromContext('accounts') },
    { category: "Actions", icon: "repeat", label: "New Recurring Schedule", hint: "Paychecks, bills, subscriptions", run: () => openModal('modal-add-recurring') },
    { category: "Actions", icon: "refresh-cw", label: "Recalculate All Balances", hint: "Sync reserves & projections", run: () => recalculateAllBalances().then(() => showToast("All balances recalculated")) },
    { category: "Actions", icon: "settings", label: "Cash Forecast Settings", hint: "Base reserves & baseline date", run: () => openSettingsModal() },
    { category: "Actions", icon: "download", label: "Export Full Ledger to Excel (.xlsx)", hint: "Download backup", run: () => exportTransactionsExcel() },
    { category: "Navigation", icon: "line-chart", label: "Go to Cash Flow Forecast", hint: "Trajectory & daily accumulator", run: () => switchTab('forecast') },
    { category: "Navigation", icon: "receipt", label: "Go to Transactions Ledger", hint: "Filter & bulk reconcile", run: () => switchTab('transactions') },
    { category: "Navigation", icon: "layers", label: "Go to Chart of Accounts", hint: "Grouped & flat views", run: () => switchTab('accounts') },
    { category: "Navigation", icon: "repeat", label: "Go to Recurring Rules", hint: "Manage active schedules", run: () => switchTab('recurring') },
    { category: "Navigation", icon: "file-spreadsheet", label: "Go to Excel Import & Export", hint: "Upload .xlsx or .csv", run: () => switchTab('import') },
    { category: "Forecast Horizon", icon: "calendar", label: "Forecast Horizon: +30 Days", hint: "Set target date", run: () => { switchTab('forecast'); setForecastPreset('30d'); } },
    { category: "Forecast Horizon", icon: "calendar", label: "Forecast Horizon: +60 Days", hint: "Set target date", run: () => { switchTab('forecast'); setForecastPreset('60d'); } },
    { category: "Forecast Horizon", icon: "calendar", label: "Forecast Horizon: +90 Days", hint: "Set target date", run: () => { switchTab('forecast'); setForecastPreset('90d'); } },
    { category: "Forecast Horizon", icon: "calendar", label: "Forecast Horizon: End of Year", hint: "Set target date", run: () => { switchTab('forecast'); setForecastPreset('eoy'); } },
    { category: "HUD Theme", icon: "sparkles", label: "Theme: Cyber Cyan & Electric Violet", hint: "Laser cyan + violet nebula", run: () => setFuturisticTheme('cyber') },
    { category: "HUD Theme", icon: "sparkles", label: "Theme: Quantum Ultraviolet & Plasma", hint: "Neon violet + plasma magenta", run: () => setFuturisticTheme('quantum') },
    { category: "HUD Theme", icon: "sparkles", label: "Theme: Solar Gold & Carbon Cyberpunk", hint: "Molten amber + tungsten obsidian", run: () => setFuturisticTheme('solar') },
    { category: "HUD Theme", icon: "sparkles", label: "Theme: Bio-Neon Matrix Mint", hint: "Hyper-luminance mint + cyan", run: () => setFuturisticTheme('matrix') }
  ];

  const accountItems = (accounts || []).map(a => ({
    category: "Accounts (Jump to Ledger)",
    icon: "landmark",
    label: `#${a.account_id} · ${a.account_name}`,
    hint: `${a.type || 'Account'} · Net ${formatCurrency(a.total_amount || 0)}`,
    run: () => openAccountDetail(a.account_id)
  }));

  const all = [...accountItems, ...baseActions];
  if (!q) {
    return [...baseActions.slice(0, 6), ...accountItems.slice(0, 12), ...baseActions.slice(6)];
  }
  return all.filter(item =>
    item.label.toLowerCase().includes(q) ||
    item.hint.toLowerCase().includes(q) ||
    item.category.toLowerCase().includes(q)
  );
}

function renderCommandPaletteResults() {
  const input = document.getElementById("cmd-palette-input");
  const container = document.getElementById("cmd-palette-results");
  if (!container) return;

  cmdPaletteItems = buildCommandPaletteCatalog(input ? input.value : "");
  if (cmdPaletteSelectedIndex >= cmdPaletteItems.length) {
    cmdPaletteSelectedIndex = 0;
  }

  if (cmdPaletteItems.length === 0) {
    container.innerHTML = `<div class="py-8 text-center text-slate-500">No matching commands or accounts found.</div>`;
    return;
  }

  container.innerHTML = cmdPaletteItems.map((item, idx) => {
    const isSel = idx === cmdPaletteSelectedIndex;
    return `
      <button type="button" onclick="executeCommandPaletteItem(${idx})" onmouseenter="cmdPaletteSelectedIndex=${idx}; highlightCommandPaletteActive()" data-cmd-idx="${idx}" class="cmd-palette-row w-full text-left px-3 py-2.5 rounded-xl flex items-center justify-between transition ${isSel ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' : 'text-slate-300 hover:bg-slate-800/60 border border-transparent'}">
        <div class="flex items-center space-x-3 min-w-0">
          <i data-lucide="${item.icon}" class="w-4 h-4 ${isSel ? 'text-emerald-400' : 'text-slate-400'} shrink-0"></i>
          <div class="truncate">
            <div class="font-semibold text-xs ${isSel ? 'text-white' : 'text-slate-200'} truncate">${escapeHtml(item.label)}</div>
            <div class="text-[10px] text-slate-500">${escapeHtml(item.category)}</div>
          </div>
        </div>
        <span class="text-[11px] font-mono ${isSel ? 'text-emerald-300' : 'text-slate-400'} ml-3 shrink-0">${escapeHtml(item.hint)}</span>
      </button>
    `;
  }).join("");
  lucide.createIcons();
}

function highlightCommandPaletteActive() {
  document.querySelectorAll(".cmd-palette-row").forEach((row, idx) => {
    if (idx === cmdPaletteSelectedIndex) {
      row.classList.add("bg-emerald-500/15", "text-emerald-300", "border-emerald-500/30");
      row.classList.remove("border-transparent");
      row.scrollIntoView({ block: "nearest" });
    } else {
      row.classList.remove("bg-emerald-500/15", "text-emerald-300", "border-emerald-500/30");
      row.classList.add("border-transparent");
    }
  });
}

function executeCommandPaletteItem(idx) {
  const item = cmdPaletteItems[idx];
  closeCommandPalette();
  if (item && typeof item.run === "function") {
    item.run();
  }
}

function handleCommandPaletteKeydown(e) {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    if (cmdPaletteItems.length > 0) {
      cmdPaletteSelectedIndex = (cmdPaletteSelectedIndex + 1) % cmdPaletteItems.length;
      highlightCommandPaletteActive();
    }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (cmdPaletteItems.length > 0) {
      cmdPaletteSelectedIndex = (cmdPaletteSelectedIndex - 1 + cmdPaletteItems.length) % cmdPaletteItems.length;
      highlightCommandPaletteActive();
    }
  } else if (e.key === "Enter") {
    e.preventDefault();
    executeCommandPaletteItem(cmdPaletteSelectedIndex);
  } else if (e.key === "Escape") {
    e.preventDefault();
    closeCommandPalette();
  }
}

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    const modal = document.getElementById("modal-command-palette");
    if (modal && !modal.classList.contains("hidden")) {
      closeCommandPalette();
    } else {
      openCommandPalette();
    }
  } else if (e.key === "Escape") {
    closeCommandPalette();
  }
});

