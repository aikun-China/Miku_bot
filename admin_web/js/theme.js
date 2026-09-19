/* 主题切换模块 —— 三套主题：ミク / 桜ミク / 雪ミク */
(function () {
    var THEMES = {
        miku:    { label: "ミク",   color: "#39C5BB" },
        sakura:  { label: "桜ミク", color: "#FFB7C5" },
        snow:    { label: "雪ミク", color: "#A0D8EF" }
    };
    var STORAGE_KEY = "mikubot_theme";

    function getCurrentTheme() {
        var saved = null;
        try { saved = localStorage.getItem(STORAGE_KEY); } catch (e) {}
        if (saved && THEMES[saved]) return saved;
        return "miku";
    }

    function applyTheme(theme) {
        if (!THEMES[theme]) theme = "miku";
        var root = document.documentElement;
        if (!root) return;
        root.setAttribute("data-theme", theme);
        try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}

        // 更新所有主题按钮的 active 状态
        var buttons = document.querySelectorAll("[data-theme-btn]");
        buttons.forEach(function (btn) {
            if (btn.getAttribute("data-theme-btn") === theme) {
                btn.classList.add("active");
            } else {
                btn.classList.remove("active");
            }
        });
    }

    function buildSwitcherHtml(current) {
        var html = ['<div class="theme-switcher">'];
        html.push('<span class="theme-switcher-label">🎨</span>');
        Object.keys(THEMES).forEach(function (key) {
            var t = THEMES[key];
            var cls = "theme-btn theme-btn-" + key + (key === current ? " active" : "");
            html.push('<button class="' + cls + '" data-theme-btn="' + key + '" title="' + t.label + '"></button>');
        });
        html.push('</div>');
        return html.join("");
    }

    function bindSwitcher() {
        var buttons = document.querySelectorAll("[data-theme-btn]");
        buttons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var theme = btn.getAttribute("data-theme-btn");
                applyTheme(theme);
            });
        });
    }

    // 初始化：先应用主题（避免页面闪烁），页面加载完成后再绑定按钮
    applyTheme(getCurrentTheme());

    // 暴露给全局
    window.ThemeManager = {
        apply: applyTheme,
        current: getCurrentTheme,
        render: function (containerSelector) {
            var container = document.querySelector(containerSelector);
            if (!container) return;
            container.innerHTML = buildSwitcherHtml(getCurrentTheme());
            bindSwitcher();
        }
    };
})();
