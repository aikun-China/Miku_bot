/**
 * MikuBot Admin - API 调用封装
 * 路径: /api/...
 * 认证机制：密码 token + 设备 device_token 双层
 */

// ============== 基础路径 ==============
// 从当前 URL 推断 base，支持域名/IP/localhost:3108
var API_BASE = "/api";

// ============== Token 管理 ==============
var TokenManager = TokenManager || {
    key: "admin_token",

    set: function (token) {
        try { localStorage.setItem(this.key, token); } catch (e) {}
    },

    get: function () {
        try { return localStorage.getItem(this.key) || ""; } catch (e) { return ""; }
    },

    clear: function () {
        try { localStorage.removeItem(this.key); } catch (e) {}
    },

    has: function () {
        return !!this.get();
    }
};

// ============== 设备令牌管理 ==============
var DeviceManager = DeviceManager || {
    key: "device_token",

    set: function (token) {
        try {
            localStorage.setItem(this.key, token);
            document.cookie = "device_token=" + token + "; path=/; SameSite=Lax; max-age=31536000";
        } catch (e) {
            try { document.cookie = "device_token=" + token + "; path=/; max-age=31536000"; } catch (ee) {}
        }
    },

    get: function () {
        try { return localStorage.getItem(this.key) || ""; } catch (e) { return ""; }
    },

    clear: function () {
        try {
            localStorage.removeItem(this.key);
            document.cookie = "device_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
        } catch (e) {}
    },

    has: function () {
        return !!this.get();
    }
};

// ============== 通用请求封装 ==============
async function request(method, endpoint, data) {
    if (data === undefined) data = null;
    var headers = { "Content-Type": "application/json" };

    if (TokenManager.has()) {
        headers["Authorization"] = "Bearer " + TokenManager.get();
    }
    if (DeviceManager.has()) {
        headers["X-Device-Token"] = DeviceManager.get();
    }

    var options = { method: method, headers: headers };
    if (data && (method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE")) {
        options.body = JSON.stringify(data);
    }

    // 15 秒超时保护（仅在浏览器支持时启用）
    var timeoutId = null;
    if (typeof AbortController !== "undefined") {
        var controller = new AbortController();
        timeoutId = setTimeout(function () { controller.abort(); }, 15000);
        options.signal = controller.signal;
    } else {
        // 不支持 AbortController 的老浏览器：用一个更长的 timeout 作为兜底
        timeoutId = setTimeout(function () { /* 只做提示，无法强制中断 */ }, 15000);
    }

    try {
        var response = await fetch(API_BASE + endpoint, options);
        if (timeoutId) clearTimeout(timeoutId);

        if (response.status === 401) {
            TokenManager.clear();
            DeviceManager.clear();
            throw new Error("密码错误或登录已过期");
        }

        if (response.status === 412) {
            // 设备等待授权 — 由调用方根据返回的 device_status 处理
            // 不在这里强制跳转，避免登录流程被打断
        }

        if (response.status === 403) {
            DeviceManager.clear();
            throw new Error("设备访问已被拒绝，请重新登录");
        }

        if (response.status === 428) {
            DeviceManager.clear();
            throw new Error("需要完成设备授权");
        }

        if (!response.ok) {
            var msg = "HTTP " + response.status;
            try {
                var errData = await response.json();
                if (errData && errData.detail) msg = errData.detail;
            } catch (e2) {
                // 不是 JSON 响应，保留默认消息
            }
            throw new Error(msg);
        }

        var contentType = response.headers.get("content-type") || "";
        if (contentType.indexOf("application/json") !== -1) {
            return await response.json();
        }
        // 非 JSON 响应：包装成对象
        var text = await response.text();
        try { return JSON.parse(text); } catch (e) { return { raw: text }; }
    } catch (err) {
        var errMsg = err ? (err.message || String(err)) : "未知错误";
        if (err instanceof TypeError && /fetch|Failed to fetch|Network/i.test(errMsg)) {
            throw new Error("网络连接失败，请确认 Bot 正在运行");
        }
        if (err.name === "AbortError") {
            throw new Error("请求超时（15秒未响应）");
        }
        throw new Error(errMsg);
    }
}

// ============== 具体 API 方法 ==============
var api = api || {};
api.auth = {
    login: function (token, deviceName) {
        var body = { token: token };
        if (deviceName) body.device_name = deviceName;
        if (DeviceManager.has()) body.device_token = DeviceManager.get();
        return request("POST", "/auth", body);
    },
    check: function () {
        return request("GET", "/dashboard");
    }
};

api.device = {
    register: function (deviceName) {
        return request("POST", "/device/register", { device_name: deviceName || "" });
    },
    status: function () {
        return request("GET", "/device/status");
    },
    list: function () {
        return request("GET", "/devices");
    },
    approve: function (token) {
        return request("POST", "/devices/" + encodeURIComponent(token) + "/approve");
    },
    reject: function (token) {
        return request("POST", "/devices/" + encodeURIComponent(token) + "/reject");
    },
    remove: function (token) {
        return request("DELETE", "/devices/" + encodeURIComponent(token));
    },
    rename: function (token, name) {
        return request("POST", "/devices/" + encodeURIComponent(token) + "/rename", { name: name });
    }
};

api.dashboard = { get: function () { return request("GET", "/dashboard"); } };
api.bot = { restart: function () { return request("POST", "/restart"); } };
api.groups = { list: function () { return request("GET", "/groups"); } };
api.users = { list: function () { return request("GET", "/users"); } };
api.plugins = {
    list: function () { return request("GET", "/plugins"); },
    get: function (name) { return request("GET", "/plugins/" + encodeURIComponent(name)); },
    update: function (name, data) { return request("PUT", "/plugins/" + encodeURIComponent(name), data); }
};
api.config = {
    get: function () { return request("GET", "/config"); },
    update: function (content) { return request("PUT", "/config", { content: content }); },
    restoreBackup: function (timestamp) { return request("POST", "/config/backup/" + timestamp + "/restore"); }
};
api.blacklist = {
    get: function () { return request("GET", "/blacklist"); },
    update: function (data) { return request("PUT", "/blacklist", data); },
    addGroup: function (groupId, plugin) { return request("POST", "/blacklist/group/" + encodeURIComponent(groupId), { plugin: plugin }); },
    removeGroup: function (groupId, plugin) { return request("DELETE", "/blacklist/group/" + encodeURIComponent(groupId) + "/" + encodeURIComponent(plugin)); },
    addUser: function (userId, plugin) { return request("POST", "/blacklist/user/" + encodeURIComponent(userId), { plugin: plugin }); },
    removeUser: function (userId, plugin) { return request("DELETE", "/blacklist/user/" + encodeURIComponent(userId) + "/" + encodeURIComponent(plugin)); }
};
api.logs = {
    list: function () { return request("GET", "/logs"); },
    get: function (filename, maxLines) {
        var url = "/logs/" + encodeURIComponent(filename);
        if (maxLines) url += "?max_lines=" + maxLines;
        return request("GET", url);
    },
    tail: function (filename, lines) {
        var url = "/logs/" + encodeURIComponent(filename) + "/tail";
        if (lines) url += "?lines=" + lines;
        return request("GET", url);
    }
};

api.requests = {
    list: function () { return request("GET", "/requests"); },
    approve: function (id) { return request("POST", "/requests/" + id + "/approve"); },
    reject: function (id, reason) { return request("POST", "/requests/" + id + "/reject", { reason: reason || "" }); }
};

api.chat = {
    send: function (type, targetId, message) {
        return request("POST", "/chat/send", { type: type, target_id: targetId, message: message });
    },
    history: function (type, targetId, limit) {
        var msgType = (type === 'friend' || type === 'private') ? 'private' : 'group';
        var url = "/chat/history?type=" + msgType + "&target_id=" + encodeURIComponent(targetId);
        if (limit) url += "&limit=" + limit;
        return request("GET", url);
    }
};

window.api = api;
window.TokenManager = TokenManager;
window.DeviceManager = DeviceManager;
