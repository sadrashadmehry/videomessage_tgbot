export default {
  async fetch(request, env) {
    if (!env.RELAY_SECRET || !env.BOT_TOKEN) {
      return new Response('Relay not configured', { status: 503 });
    }
    if (request.headers.get('X-Relay-Secret') !== env.RELAY_SECRET) {
      return new Response('Unauthorized', { status: 401 });
    }
    const url = new URL(request.url);
    let path;
    if (request.method === 'POST' && /^\/api\/[A-Za-z][A-Za-z0-9]*$/.test(url.pathname)) {
      path = `/bot${env.BOT_TOKEN}/${url.pathname.slice(5)}`;
    } else if (request.method === 'GET' && /^\/file\/[A-Za-z0-9_/-]+\.[A-Za-z0-9]+$/.test(url.pathname)) {
      path = `/file/bot${env.BOT_TOKEN}/${url.pathname.slice(6)}`;
    } else {
      return new Response('Not found', { status: 404 });
    }
    if (url.search) return new Response('Unexpected query', { status: 400 });
    const headers = new Headers();
    if (request.headers.has('Content-Type')) headers.set('Content-Type', request.headers.get('Content-Type'));
    try {
      const upstream = await fetch(`https://api.telegram.org${path}`, {
        method: request.method, headers, body: request.body, redirect: 'manual',
      });
      const outputHeaders = new Headers({ 'Cache-Control': 'no-store' });
      for (const name of ['Content-Type', 'Content-Length', 'Retry-After']) {
        if (upstream.headers.has(name)) outputHeaders.set(name, upstream.headers.get(name));
      }
      return new Response(upstream.body, { status: upstream.status, headers: outputHeaders });
    } catch {
      // Never log request URLs, bot credentials, or user media.
      return new Response('Telegram upstream unavailable; delivery uncertain', { status: 502 });
    }
  },
};
