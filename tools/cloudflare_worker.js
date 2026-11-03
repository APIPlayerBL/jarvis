// Cloudflare Worker: простой релей к Groq/OpenRouter для Jarvis.
//
// Зачем: api.groq.com и openrouter.ai недоступны из РФ напрямую. Worker
// находится на Cloudflare и пересылает запросы, ключи остаются у клиента.
//
// Установка:
//   1. Создай Worker в Cloudflare Dashboard, вставь этот код.
//   2. Settings -> Variables -> добавь секрет RELAY_TOKEN (длинная строка).
//   3. В .env Jarvis:
//        JARVIS_GROQ_BASE_URL=https://<worker>.workers.dev/groq
//        JARVIS_OPENROUTER_BASE_URL=https://<worker>.workers.dev/openrouter
//        JARVIS_RELAY_TOKEN=<тот же секрет>
//   4. python main.py --health
//
// Пути: /groq/*      -> https://api.groq.com/openai/v1/*
//        /openrouter/* -> https://openrouter.ai/api/v1/*
//
// Внимание: Cloudflare может быть ограничен провайдером. Альтернатива —
// свой VPS с nginx и proxy_pass.

export default {
  async fetch(request, env) {
    const token = request.headers.get("x-relay-token") || "";
    if (!env.RELAY_TOKEN || token !== env.RELAY_TOKEN) {
      return new Response("Forbidden", { status: 403 });
    }

    const url = new URL(request.url);
    let targetBase;
    let rest;

    if (url.pathname.startsWith("/groq/")) {
      targetBase = "https://api.groq.com/openai/v1/";
      rest = url.pathname.slice("/groq/".length);
    } else if (url.pathname.startsWith("/openrouter/")) {
      targetBase = "https://openrouter.ai/api/v1/";
      rest = url.pathname.slice("/openrouter/".length);
    } else {
      return new Response("Not found", { status: 404 });
    }

    const target = new URL(rest + url.search, targetBase);
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("x-relay-token");

    const init = { method: request.method, headers };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }
    return fetch(target.toString(), init);
  },
};
