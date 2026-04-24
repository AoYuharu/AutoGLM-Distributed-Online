export interface BrowserSharedConfig {
  serverPublicBaseUrl: string;
  serverWebSocketPublicUrl: string;
}

declare const __WEB_SHARED_CONFIG__: BrowserSharedConfig;

const DEFAULT_HTTP_BASE_URL = 'http://localhost:8000';
const DEFAULT_WS_BASE_URL = 'ws://localhost:8000';
const DEFAULT_API_BASE_URL = '';
const DEFAULT_WS_PATH = '/ws/console';

const injectedConfig: BrowserSharedConfig = typeof __WEB_SHARED_CONFIG__ !== 'undefined'
  ? __WEB_SHARED_CONFIG__
  : {
      serverPublicBaseUrl: DEFAULT_HTTP_BASE_URL,
      serverWebSocketPublicUrl: DEFAULT_WS_BASE_URL,
    };

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}

function normalizeHttpBaseUrl(value?: string | null): string {
  if (!value) {
    return DEFAULT_HTTP_BASE_URL;
  }

  try {
    const url = new URL(value, window.location.origin);
    url.pathname = '';
    url.search = '';
    url.hash = '';
    return trimTrailingSlash(url.toString());
  } catch {
    return DEFAULT_HTTP_BASE_URL;
  }
}

function normalizeWsBaseUrl(value?: string | null): string {
  if (!value) {
    return DEFAULT_WS_BASE_URL;
  }

  try {
    const url = new URL(value, window.location.origin);
    if (url.protocol === 'http:') {
      url.protocol = 'ws:';
    } else if (url.protocol === 'https:') {
      url.protocol = 'wss:';
    }
    url.pathname = '';
    url.search = '';
    url.hash = '';
    return trimTrailingSlash(url.toString());
  } catch {
    return DEFAULT_WS_BASE_URL;
  }
}

function resolveApiBaseUrl(): string {
  const viteOverride = import.meta.env.VITE_API_URL?.trim();
  if (viteOverride) {
    const overrideBaseUrl = normalizeHttpBaseUrl(viteOverride);
    if (typeof window !== 'undefined' && overrideBaseUrl === window.location.origin) {
      return DEFAULT_API_BASE_URL;
    }
    return overrideBaseUrl;
  }

  const configuredBaseUrl = normalizeHttpBaseUrl(injectedConfig.serverPublicBaseUrl);
  if (typeof window !== 'undefined' && configuredBaseUrl === window.location.origin) {
    return DEFAULT_API_BASE_URL;
  }

  return configuredBaseUrl;
}

function resolveWsConsoleUrl(): string {
  const viteOverride = import.meta.env.VITE_API_URL?.trim();
  if (viteOverride) {
    const wsBaseUrl = normalizeWsBaseUrl(viteOverride);
    const currentWsOrigin = typeof window !== 'undefined'
      ? window.location.origin.replace(/^http/, 'ws')
      : '';
    if (wsBaseUrl === currentWsOrigin) {
      return DEFAULT_WS_PATH;
    }
    return `${wsBaseUrl}${DEFAULT_WS_PATH}`;
  }

  const configuredWsBaseUrl = normalizeWsBaseUrl(injectedConfig.serverWebSocketPublicUrl);
  if (typeof window !== 'undefined') {
    const currentWsOrigin = window.location.origin.replace(/^http/, 'ws');
    if (configuredWsBaseUrl === currentWsOrigin) {
      return DEFAULT_WS_PATH;
    }
  }

  return `${configuredWsBaseUrl}${DEFAULT_WS_PATH}`;
}

export const browserConfig = {
  apiBaseUrl: resolveApiBaseUrl(),
  wsConsoleUrl: resolveWsConsoleUrl(),
  serverPublicBaseUrl: normalizeHttpBaseUrl(injectedConfig.serverPublicBaseUrl),
  serverWebSocketPublicUrl: normalizeWsBaseUrl(injectedConfig.serverWebSocketPublicUrl),
};
