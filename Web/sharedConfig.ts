import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

export interface SharedWebConfig {
  configPath: string;
  yamlExists: boolean;
  serverPublicBaseUrl: string;
  serverWebSocketPublicUrl: string;
  webDevHost: string;
  webDevPort: number;
  mockServerHost: string;
  mockServerPort: number;
}

const CURRENT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CURRENT_DIR, '..');
const SHARED_CONFIG_PATH = path.resolve(REPO_ROOT, 'config', 'server-web.yaml');

const DEFAULT_CONFIG: SharedWebConfig = {
  configPath: SHARED_CONFIG_PATH,
  yamlExists: false,
  serverPublicBaseUrl: 'http://localhost:8000',
  serverWebSocketPublicUrl: 'ws://localhost:8000',
  webDevHost: 'localhost',
  webDevPort: 5173,
  mockServerHost: 'localhost',
  mockServerPort: 8888,
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function toWebSocketBaseUrl(value: string): string {
  try {
    const url = new URL(value);
    if (url.protocol === 'http:') {
      url.protocol = 'ws:';
    } else if (url.protocol === 'https:') {
      url.protocol = 'wss:';
    }
    url.pathname = '';
    url.search = '';
    url.hash = '';
    return url.toString().replace(/\/+$/, '');
  } catch {
    return value.replace(/\/+$/, '');
  }
}

export function loadSharedWebConfig(): SharedWebConfig {
  if (!fs.existsSync(SHARED_CONFIG_PATH)) {
    return { ...DEFAULT_CONFIG };
  }

  try {
    const raw = fs.readFileSync(SHARED_CONFIG_PATH, 'utf-8');
    const data = asRecord(parse(raw) ?? {});
    const server = asRecord(data.server);
    const web = asRecord(data.web);
    const webDev = asRecord(web.dev);
    const mockServer = asRecord(web.mock_server);

    const serverPublicBaseUrl = readString(server.public_base_url, DEFAULT_CONFIG.serverPublicBaseUrl);
    const serverWebSocketPublicUrl = readString(
      server.websocket_public_url,
      toWebSocketBaseUrl(serverPublicBaseUrl),
    );

    return {
      configPath: SHARED_CONFIG_PATH,
      yamlExists: true,
      serverPublicBaseUrl,
      serverWebSocketPublicUrl,
      webDevHost: readString(webDev.host ?? web.host, DEFAULT_CONFIG.webDevHost),
      webDevPort: readNumber(webDev.port ?? web.port, DEFAULT_CONFIG.webDevPort),
      mockServerHost: readString(mockServer.host, DEFAULT_CONFIG.mockServerHost),
      mockServerPort: readNumber(mockServer.port, DEFAULT_CONFIG.mockServerPort),
    };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export function getAccessibleHost(host: string): string {
  return host === '0.0.0.0' || host === '::' ? 'localhost' : host;
}

export function getWebDevUrl(config: SharedWebConfig = loadSharedWebConfig()): string {
  return `http://${getAccessibleHost(config.webDevHost)}:${config.webDevPort}`;
}
