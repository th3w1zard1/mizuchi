#!/usr/bin/env node
/**
 * Hybrid FlareSolverr router: tries lightweight FlareSolverr first, falls back to Patchright.
 * Jackett points FlareSolverrUrl at this service (default port 8191).
 */
const http = require('http');

const PORT = parseInt(process.env.PORT || '8191', 10);
const HOST = process.env.HOST || '127.0.0.1';
const VERSION = process.env.HYBRID_VERSION || '1.0.0-hybrid';
const FLARESOLVERR_URL = process.env.FLARESOLVERR_URL || 'http://127.0.0.1:8193/v1';
const PATCHRIGHT_URL = process.env.PATCHRIGHT_URL || 'http://127.0.0.1:8192/v1';
const UPSTREAM_TIMEOUT_MS = parseInt(process.env.UPSTREAM_TIMEOUT_MS || '130000', 10);

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function isChallengeResponse(result) {
  if (!result || result.status !== 'ok') return true;
  const html = (result.solution && result.solution.response) || '';
  const lower = html.slice(0, 12000).toLowerCase();
  const titleMatch = lower.match(/<title[^>]*>([^<]+)<\/title>/i);
  const title = titleMatch ? titleMatch[1].toLowerCase() : '';
  const cookies = (result.solution && result.solution.cookies) || [];
  if (cookies.some((c) => c.name === 'cf_clearance')) return false;
  return (
    title.includes('just a moment') ||
    title.includes('attention required') ||
    lower.includes('cf-challenge') ||
    lower.includes('challenge-platform') ||
    lower.includes('turnstile')
  );
}

async function forward(url, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

async function handleCommand(body) {
  const cmd = body.cmd;
  if (cmd === 'sessions.list' || cmd === 'sessions.create' || cmd === 'sessions.destroy') {
    try {
      return await forward(PATCHRIGHT_URL, body);
    } catch {
      return await forward(FLARESOLVERR_URL, body);
    }
  }

  if (cmd === 'request.get') {
    let flareResult = null;
    try {
      flareResult = await forward(FLARESOLVERR_URL, body);
      if (!isChallengeResponse(flareResult)) {
        flareResult.version = VERSION;
        flareResult.message = (flareResult.message || '') + ' [via flaresolverr]';
        return flareResult;
      }
    } catch (err) {
      flareResult = { status: 'error', message: err.message };
    }

    const patchResult = await forward(PATCHRIGHT_URL, body);
    patchResult.version = VERSION;
    patchResult.message = (patchResult.message || '') + ' [via patchright fallback]';
    return patchResult;
  }

  return forward(FLARESOLVERR_URL, body);
}

const server = http.createServer(async (req, res) => {
  if (req.method !== 'POST' || req.url !== '/v1') {
    res.writeHead(404);
    res.end('Not found');
    return;
  }
  try {
    const raw = await readBody(req);
    const body = JSON.parse(raw || '{}');
    const result = await handleCommand(body);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(result));
  } catch (err) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        status: 'error',
        message: err.message || String(err),
        version: VERSION,
      })
    );
  }
});

server.listen(PORT, HOST, () => {
  console.log(`hybrid-flaresolverr-router on http://${HOST}:${PORT}/v1`);
  console.log(`  primary: ${FLARESOLVERR_URL}`);
  console.log(`  fallback: ${PATCHRIGHT_URL}`);
});
