/**
 * MikuBot Admin - 全局逻辑
 * 页面访问: /dashboard, /plugins, ...
 */

// ====================== 通知 Toast ======================
var Toast = Toast || {
    init: function () {
        if (this._inited) return;
        this._inited = true;
    },

    show: function (message, type, duration) {
        type = type || "info";
        duration = duration || 3000;
        this.init();

        var container = document.getElementById("toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "toast-container";
            container.style.cssText = "position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:12px;";
            document.body.appendChild(container);
        }

        var icons = { success: "\u2714", error: "\u2716", warning: "\u26A0", info: "\u2139" };
        var colors = { success: "#2BA86A", error: "#D64545", warning: "#E8842F", info: "#2BA89E" };

        var toast = document.createElement("div");
        toast.style.cssText = [
            "padding:14px 20px;",
            "background:rgba(255,255,255,0.72);",
            "backdrop-filter:blur(18px) saturate(180%);",
            "-webkit-backdrop-filter:blur(18px) saturate(180%);",
            "border:1px solid rgba(255,255,255,0.6);",
            "border-left:4px solid " + colors[type] + ";",
            "border-radius:10px;",
            "color:#1F3A4A;",
            "font-size:14px;",
            "font-weight:500;",
            "box-shadow:0 8px 32px rgba(43,168,158,0.18),inset 0 1px 0 rgba(255,255,255,0.7);",
            "min-width:260px;",
            "animation:slideIn 0.3s ease-out;"
        ].join("");

        toast.innerHTML = '<span style="color:' + colors[type] + ";margin-right:8px;font-weight:600;\">" + icons[type] + "</span>" + message;
        container.appendChild(toast);

        setTimeout(function () {
            toast.style.opacity = "0";
            toast.style.transition = "opacity 0.3s";
            setTimeout(function () { toast.remove(); }, 300);
        }, duration);
    },

    success: function (msg) { this.show(msg, "success"); },
    error: function (msg) { this.show(msg, "error"); },
    warning: function (msg) { this.show(msg, "warning"); },
    info: function (msg) { this.show(msg, "info"); }
};

// 添加动画 keyframes
(function () {
    var style = document.createElement("style");
    style.textContent = "@keyframes slideIn{from{transform:translateX(100px);opacity:0;}to{transform:translateX(0);opacity:1;}}";
    document.head.appendChild(style);
})();


// ====================== 认证检查（除登录页外） ======================
async function requireAuth() {
    if (!window.TokenManager || !window.TokenManager.has()) {
        window.location.href = "/";
        return false;
    }

    try {
        await window.api.dashboard.get();
        return true;
    } catch (e) {
        window.TokenManager.clear();
        window.location.href = "/";
        return false;
    }
}

// ====================== 侧边栏（桌面 + 移动端适配） ======================
function renderSidebar(currentPage) {
    var pages = [
        { page: "dashboard", title: "仪表盘", icon: "\u2302" },
        { page: "chat", title: "聊天对话", icon: "\u2709" },
        { page: "requests", title: "申请管理", icon: "\u2705" },
        { page: "devices", title: "设备管理", icon: "\u26A1" },
        { page: "groups", title: "群组列表", icon: "\u260F" },
        { page: "users", title: "用户列表", icon: "\u263A" },
        { page: "plugins", title: "插件管理", icon: "\u2699" },
        { page: "blacklist", title: "黑白名单", icon: "\u2718" },
        { page: "config", title: "配置编辑", icon: "\u270E" },
        { page: "logs", title: "运行日志", icon: "\u2630" }
    ];

    var html = [
        // 移动端遮罩层（点击可关闭侧边栏）
        '<div id="mobile-overlay" style="display:none;position:fixed;inset:0;background:rgba(31,58,74,0.35);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);z-index:98;" onclick="toggleSidebar()"></div>',

        // 移动端菜单按钮（仅小屏显示）
        '<div id="mobile-menu-btn" onclick="toggleSidebar()" class="mobile-menu-btn-element"></div>',

        '<aside id="sidebar-elem" class="sidebar-aside">',
        '<div class="sidebar-header">',
        '<div class="sidebar-logo">',
        '<div class="sidebar-logo-icon">♪</div>',
        '<div class="sidebar-logo-text">',
        "MikuBot",
        '<div style="font-size:11px;color:var(--text-muted);font-weight:400;">Admin Console</div>',
        '</div>',
        '</div>',
        '</div>',
        '<nav style="padding:16px 12px;flex:1;overflow-y:auto;" class="sidebar-nav">'
    ];

    pages.forEach(function (p) {
        var isActive = p.page === currentPage;
        var activeClass = isActive ? " sidebar-nav-link-active" : "";
        html.push(
            '<a href="/' + p.page + '" class="sidebar-nav-link' + activeClass + '">' +
            '<span class="sidebar-nav-icon">' + p.icon + "</span>" + p.title +
            "</a>"
        );
    });

    html.push("</nav>");
    html.push('<div style="padding:16px 20px;border-top:1px solid var(--border-color);background:linear-gradient(0deg,var(--bg-hover) 0%,transparent 100%);">');
    html.push('<button onclick="logout()" class="sidebar-logout-btn">\u2716 退出登录</button>');
    html.push("</div>");
    html.push("</aside>");

    document.getElementById("sidebar").innerHTML = html.join("");
    // 应用移动端适配
    applyMobileSidebar();
}

// 移动端侧边栏切换
function toggleSidebar() {
    var overlay = document.getElementById("mobile-overlay");
    var sidebar = document.getElementById("sidebar-elem");
    if (!overlay || !sidebar) return;

    if (sidebar.classList.contains("open")) {
        sidebar.classList.remove("open");
        overlay.style.display = "none";
    } else {
        sidebar.classList.add("open");
        overlay.style.display = "block";
    }
}

// 注入移动端侧边栏 CSS
function applyMobileSidebar() {
    if (document.getElementById("sidebar-mobile-style")) return;
    var style = document.createElement("style");
    style.id = "sidebar-mobile-style";
    style.textContent = [
        ".sidebar-aside {",
        "  width:240px;",
        "  min-height:100vh;",
        "  background:linear-gradient(180deg,rgba(255,255,255,0.78) 0%,rgba(255,255,255,0.65) 100%);",
        "  backdrop-filter:blur(18px) saturate(180%);",
        "  -webkit-backdrop-filter:blur(18px) saturate(180%);",
        "  border-right:1px solid var(--border-color);",
        "  box-shadow:2px 0 20px rgba(0,0,0,0.06),inset -1px 0 0 rgba(255,255,255,0.4);",
        "  position:fixed;",
        "  left:0;",
        "  top:0;",
        "  padding:0;",
        "  box-sizing:border-box;",
        "  z-index:99;",
        "  display:flex;",
        "  flex-direction:column;",
        "}",
        "",
        "/* 菜单链接（移动端 + 桌面端统一 */",
        ".sidebar-nav-link {",
        "  display:flex;",
        "  align-items:center;",
        "  padding:12px 16px;",
        "  border-radius:10px;",
        "  color:var(--text-secondary);",
        "  text-decoration:none;",
        "  font-size:14px;",
        "  margin-bottom:4px;",
        "  transition:all 0.25s;",
        "}",
        ".sidebar-nav-icon { margin-right:10px;color:inherit; }",
        ".sidebar-nav-link:hover {",
        "  background:var(--bg-hover);",
        "  color:var(--theme-primary);",
        "  transform:translateX(2px);",
        "}",
        ".sidebar-nav-link-active {",
        "  background:linear-gradient(135deg,var(--theme-glow) 0%,var(--bg-hover) 100%);",
        "  color:var(--theme-primary);",
        "  font-weight:600;",
        "  box-shadow:0 2px 12px var(--theme-glow),inset 0 1px 0 rgba(255,255,255,0.5);",
        "  border:1px solid var(--border-color);",
        "  padding:11px 15px;",
        "}",
        "",
        ".sidebar-logout-btn {",
        "  width:100%;",
        "  padding:10px 16px;",
        "  background:rgba(255,255,255,0.6);",
        "  color:var(--text-secondary);",
        "  border:1px solid var(--border-color);",
        "  border-radius:10px;",
        "  cursor:pointer;",
        "  font-size:14px;",
        "  transition:all 0.25s;",
        "  box-shadow:inset 0 1px 0 rgba(255,255,255,0.6);",
        "}",
        ".sidebar-logout-btn:hover {",
        "  background:var(--bg-hover);",
        "  color:var(--danger);",
        "  border-color:var(--danger);",
        "  transform:translateX(-2px);",
        "}",
        "",
        "/* 移动端菜单按钮 */",
        ".mobile-menu-btn-element {",
        "  display:none;",
        "  position:fixed;",
        "  top:12px;",
        "  left:12px;",
        "  z-index:100;",
        "  width:44px;",
        "  height:44px;",
        "  background:rgba(255,255,255,0.7);",
        "  backdrop-filter:blur(10px);",
        "  -webkit-backdrop-filter:blur(10px);",
        "  border:1px solid var(--border-color);",
        "  border-radius:10px;",
        "  display:flex;",
        "  align-items:center;",
        "  justify-content:center;",
        "  cursor:pointer;",
        "  font-size:20px;",
        "  color:var(--theme-primary);",
        "  font-weight:700;",
        "  box-shadow:0 4px 16px var(--theme-glow),inset 0 1px 0 rgba(255,255,255,0.6);",
        "  transition:all 0.25s;",
        "}",
        ".mobile-menu-btn-element:hover {",
        "  box-shadow:0 6px 24px var(--theme-glow),inset 0 1px 0 rgba(255,255,255,0.7);",
        "  transform:scale(1.05);",
        "}",
        ".mobile-menu-btn-element::before {",
        "  content:'\\2630';",
        "}",
        "",
        "@media (max-width: 768px) {",
        "  .mobile-menu-btn-element { display:flex !important; }",
        "  .sidebar-aside {",
        "    left:-260px;",
        "    transition:left 0.25s cubic-bezier(0.4,0,0.2,1);",
        "    box-shadow:0 8px 32px rgba(0,0,0,0.08);",
        "  }",
        "  .sidebar-aside.open { left:0; }",
        "  .main { margin-left:0 !important; padding-top:68px !important; }",
        "  body { overflow-x:hidden; }",
        "}"
    ].join("");
    document.head.appendChild(style);
}

function logout() {
    if (window.TokenManager) window.TokenManager.clear();
    if (window.DeviceManager) window.DeviceManager.clear();
    window.Toast.success("已退出登录");
    setTimeout(function () { window.location.href = "/"; }, 500);
}

// ====================== 通用工具 ======================
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(function () {
        window.Toast.success("已复制到剪贴板");
    }).catch(function () { window.Toast.error("复制失败"); });
}

// ====================== 模态框 ======================
function showModal(title, bodyHtml, onConfirm, confirmText) {
    confirmText = confirmText || "\u786E\u5B9A";

    var overlay = document.getElementById("global-modal");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "global-modal";
        overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9998;display:none;align-items:center;justify-content:center;";
        overlay.innerHTML = [
            '<div id="modal-box" style="background:#232340;border:1px solid #3a3a5a;border-radius:12px;padding:24px;width:100%;max-width:500px;max-height:80vh;overflow-y:auto;">',
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">',
            '<h3 id="modal-title" style="font-size:18px;color:white;margin:0;"></h3>',
            '<button onclick="closeModal()" style="background:transparent;border:none;color:#a0a0c0;font-size:20px;cursor:pointer;">\u2716</button>',
            "</div>",
            '<div id="modal-body"></div>',
            '<div id="modal-actions" style="margin-top:20px;padding-top:16px;border-top:1px solid #3a3a5a;display:flex;gap:12px;justify-content:flex-end;"></div>',
            "</div>"
        ].join("");
        document.body.appendChild(overlay);
    }

    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").innerHTML = bodyHtml;

    var actions = document.getElementById("modal-actions");
    actions.innerHTML =
        '<button onclick="closeModal()" style="padding:10px 20px;background:#2a2a4a;border:1px solid #3a3a5a;border-radius:8px;color:#a0a0c0;cursor:pointer;">\u53D6\u6D88</button>' +
        '<button id="modal-ok" style="padding:10px 20px;background:linear-gradient(135deg,#9370db,#00b8ba);border:none;border-radius:8px;color:white;font-weight:600;cursor:pointer;">' + confirmText + "</button>";

    document.getElementById("modal-ok").onclick = function () {
        if (onConfirm) {
            var result = onConfirm();
            if (result === false) return;
        }
        closeModal();
    };

    overlay.style.display = "flex";
}

function closeModal() {
    var overlay = document.getElementById("global-modal");
    if (overlay) overlay.style.display = "none";
}

// ====================== 全局导出 ======================
window.Toast = Toast;
window.requireAuth = requireAuth;
window.renderSidebar = renderSidebar;
window.logout = logout;
window.copyToClipboard = copyToClipboard;
window.showModal = showModal;
window.closeModal = closeModal;
