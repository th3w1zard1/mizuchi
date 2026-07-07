#!/usr/bin/env node
/**
 * FlareSolverr-compatible HTTP API using Patchright (undetected Chromium).
 * Uses headed Chrome/Chromium inside Xvfb on Linux for Cloudflare managed challenges.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { randomUUID } = require('crypto');
const { chromium } = require('patchright');

const PORT = parseInt(process.env.PORT || '8192', 10);
const HOST = process.env.HOST || '127.0.0.1';
const VERSION = process.env.PATCHRIGHT_PROXY_VERSION || '1.1.0-patchright';
const MAX_TIMEOUT_MS = parseInt(process.env.MAX_TIMEOUT_MS || '120000', 10);
const SESSION_TTL_MS = parseInt(process.env.SESSION_TTL_MS || '600000', 10);
const HEADLESS = process.env.HEADLESS === 'true';
const BROWSER_CHANNEL = process.env.BROWSER_CHANNEL || '';
const EXECUTABLE_PATH = process.env.EXECUTABLE_PATH || '/usr/bin/chromium-browser';
const PROFILES_DIR = process.env.PROFILES_DIR || path.join(__dirname, 'profiles');

/** @type {Map<string, { context: import('patchright').BrowserContext, lastUsed: number }>} */
const sessions = new Map();

fs.mkdirSync(PROFILES_DIR, { recursive: true });

function launchOptions() {
  const opts = {
    headless: HEADLESS,
    viewport: null,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  };
  if (EXECUTABLE_PATH && fs.existsSync(EXECUTABLE_PATH)) {
    opts.executablePath = EXECUTABLE_PATH;
  } else if (BROWSER_CHANNEL) {
    opts.channel = BROWSER_CHANNEL;
  }
  return opts;
}

async function createSession(sessionId) {
  const profileDir = path.join(PROFILES_DIR, sessionId);
  fs.mkdirSync(profileDir, { recursive: true });
  const context = await chromium.launchPersistentContext(profileDir, launchOptions());
  sessions.set(sessionId, { context, lastUsed: Date.now() });
  return sessionId;
}

async function destroySession(sessionId) {
  const s = sessions.get(sessionId);
  if (s) {
    await s.context.close().catch(() => {});
    sessions.delete(sessionId);
    const profileDir = path.join(PROFILES_DIR, sessionId);
    fs.rmSync(profileDir, { recursive: true, force: true });
  }
}

function isChallengePage(title, html, cookies) {
  if (cookies.some((c) => c.name === 'cf_clearance')) return false;
  const t = (title || '').toLowerCase();
  const h = (html || '').slice(0, 12000).toLowerCase();
  return (
    t.includes('just a moment') ||
    t.includes('attention required') ||
    t.includes('checking your browser') ||
    t.includes('verify you are human') ||
    h.includes('cf-challenge') ||
    h.includes('challenge-platform') ||
    h.includes('turnstile') ||
    h.includes('cf-turnstile')
  );
}

async function waitForClearance(page, context, maxMs) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const title = await page.title().catch(() => '');
    const html = await page.content().catch(() => '');
    const cookies = await context.cookies().catch(() => []);
    if (!isChallengePage(title, html, cookies)) return true;
    await page.waitForTimeout(2000 + Math.floor(Math.random() * 1000));
  }
  return false;
}

async function requestGet(url, sessionId, maxTimeout) {
  const timeout = Math.min(maxTimeout || MAX_TIMEOUT_MS, MAX_TIMEOUT_MS);
  let sid = sessionId;
  if (!sid || !sessions.has(sid)) {
    sid = randomUUID();
    await createSession(sid);
  }
  const entry = sessions.get(sid);
  entry.lastUsed = Date.now();
  const { context } = entry;

  const page = context.pages()[0] || (await context.newPage());
  const startTs = Date.now();
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
    const remaining = timeout - (Date.now() - startTs);
    const cleared = await waitForClearance(page, context, remaining);
    if (!cleared) {
      throw new Error(`Challenge not cleared within ${timeout}ms`);
    }
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});

    const response = await page.content();
    const cookies = await context.cookies(url);
    const userAgent = await page.evaluate(() => navigator.userAgent);
    const finalUrl = page.url();

    return {
      status: 'ok',
      message: '',
      startTimestamp: startTs,
      endTimestamp: Date.now(),
      version: VERSION,
      solution: {
        url: finalUrl,
        status: 200,
        headers: {},
        response,
        cookies: cookies.map((c) => ({
          name: c.name,
          value: c.value,
          domain: c.domain,
          path: c.path,
          expires: c.expires,
          httpOnly: c.httpOnly,
          secure: c.secure,
          sameSite: c.sameSite,
        })),
        userAgent,
      },
    };
  } finally {
    if (context.pages().length > 1) {
      await page.close().catch(() => {});
    }
  }
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function handleCommand(body) {
  const cmd = body.cmd;
  const startTs = Date.now();

  if (cmd === 'sessions.list') {
    return {
      status: 'ok',
      message: '',
      startTimestamp: startTs,
      endTimestamp: Date.now(),
      version: VERSION,
      sessions: [...sessions.keys()],
    };
  }

  if (cmd === 'sessions.create') {
    const sid = randomUUID();
    await createSession(sid);
    return {
      status: 'ok',
      message: 'Session created',
      startTimestamp: startTs,
      endTimestamp: Date.now(),
      version: VERSION,
      session: sid,
    };
  }

  if (cmd === 'sessions.destroy') {
    await destroySession(body.session);
    return {
      status: 'ok',
      message: 'Session destroyed',
      startTimestamp: startTs,
      endTimestamp: Date.now(),
      version: VERSION,
    };
  }

  if (cmd === 'request.get') {
    if (!body.url) throw new Error('url is required');
    return requestGet(body.url, body.session, body.maxTimeout);
  }

  throw new Error(`Unknown command: ${cmd}`);
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
    const payload = {
      status: 'error',
      message: err.message || String(err),
      startTimestamp: Date.now(),
      endTimestamp: Date.now(),
      version: VERSION,
    };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(payload));
  }
});

async function cleanupIdleSessions() {
  const now = Date.now();
  for (const [id, s] of sessions) {
    if (now - s.lastUsed > SESSION_TTL_MS) {
      await destroySession(id);
    }
  }
}

setInterval(() => cleanupIdleSessions().catch(console.error), 60000);

process.on('SIGTERM', async () => {
  for (const id of [...sessions.keys()]) await destroySession(id);
  process.exit(0);
});

server.listen(PORT, HOST, () => {
  console.log(
    `patchright-flaresolverr-proxy listening on http://${HOST}:${PORT}/v1 (headless=${HEADLESS}, browser=${EXECUTABLE_PATH || BROWSER_CHANNEL})`
  );
});
