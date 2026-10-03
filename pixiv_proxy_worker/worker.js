// Pixiv 反向代理 Worker（为 MikuBot miku_setu 插件定制）
//
// 路由约定（与 plugins/miku_setu 的 URL 构造一致）：
//   /app-api.pixiv.net/...        -> https://app-api.pixiv.net/...        （App API，透传 Authorization）
//   /oauth.secure.pixiv.net/...   -> https://oauth.secure.pixiv.net/...   （OAuth，透传 POST body 与 X-Client-* 头）
//   /public-api.secure.pixiv.net/ -> https://public-api.secure.pixiv.net/
//   其余路径（/img-original/... /img-master/... /c/... 等） -> https://i.pximg.net（自动补 Referer，pximg 无 Referer 返回 403）

const API_HOSTS = new Set([
  "app-api.pixiv.net",
  "oauth.secure.pixiv.net",
  "public-api.secure.pixiv.net",
]);

const APP_UA = "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)";

// Pixiv App 反篡改指纹（PixivPy3 公开的固定盐）：X-Client-Hash = sha256(secret + X-Client-Time)
const PIXIV_HASH_SECRET = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c";

async function sha256Hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function rfc3339Now() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/" || url.pathname === "") {
      return new Response(
        "pixiv-proxy worker OK\n" +
          "usage:\n" +
          "  /app-api.pixiv.net/...\n" +
          "  /oauth.secure.pixiv.net/...\n" +
          "  /img-original/... (i.pximg.net paths)\n",
        { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } }
      );
    }

    let target;
    const first = url.pathname.split("/")[1] ?? "";
    if (API_HOSTS.has(first)) {
      target = new URL("https://" + first + url.pathname.slice(first.length + 1) + url.search);
    } else {
      target = new URL("https://i.pximg.net" + url.pathname + url.search);
    }

    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("cookie");
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");
    headers.delete("x-forwarded-for");
    headers.delete("x-forwarded-proto");
    headers.delete("x-real-ip");

    if (target.hostname === "i.pximg.net") {
      headers.set("referer", "https://www.pixiv.net/");
      headers.set("user-agent", "Cloudflare Workers");
    } else if (target.hostname === "app-api.pixiv.net") {
      headers.set("user-agent", APP_UA);
      headers.set("app-os", "android");
      headers.set("app-os-version", "11");
      headers.set("accept-language", "zh-CN");
      if (!headers.has("x-client-time")) {
        const t = rfc3339Now();
        headers.set("x-client-time", t);
        headers.set("x-client-hash", await sha256Hex(PIXIV_HASH_SECRET + t));
      }
    } else if (target.hostname === "oauth.secure.pixiv.net") {
      headers.set("user-agent", APP_UA);
      headers.set("app-os", "android");
      headers.set("app-os-version", "11");
      if (!headers.has("x-client-time")) {
        const t = rfc3339Now();
        headers.set("x-client-time", t);
        headers.set("x-client-hash", await sha256Hex(PIXIV_HASH_SECRET + t));
      }
    }

    const upstreamReq = new Request(target.toString(), {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "follow",
    });

    const resp = await fetch(upstreamReq).catch((e) => {
      return new Response(JSON.stringify({ proxy_error: String(e) }), {
        status: 502,
        headers: { "content-type": "application/json" },
      });
    });
    const out = new Response(resp.body, resp);
    out.headers.set("access-control-allow-origin", "*");
    return out;
  },
};
