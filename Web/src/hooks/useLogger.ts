/**
 * useLogger Hook - Unified logging interface for Web frontend
 *
 * Provides consistent logging across components with support for:
 * - Structured logging with details
 * - Log levels (debug, info, warn, error)
 * - Source/module tagging
 * - Console output with formatting
 * - Optional remote log ingestion
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEntry {
  timestamp: string;
  level: LogLevel;
  source: string;
  message: string;
  details?: Record<string, any>;
}

export interface Logger {
  debug: (message: string, details?: Record<string, any>) => void;
  info: (message: string, details?: Record<string, any>) => void;
  warn: (message: string, details?: Record<string, any>) => void;
  error: (message: string, details?: Record<string, any>) => void;
}

const LOG_LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

// Configure minimum log level (can be changed via setMinLevel)
let MIN_LOG_LEVEL: LogLevel = 'debug';

// Console styling for different log levels
const LOG_STYLES: Record<LogLevel, string> = {
  debug: 'color: #888; font-style: italic',
  info: 'color: #2196F3; font-weight: bold',
  warn: 'color: #FF9800; font-weight: bold',
  error: 'color: #F44336; font-weight: bold',
};

/**
 * Create a logger instance for a specific source/module
 */
export function useLogger(source: string): Logger {
  const formatMessage = (level: LogLevel, message: string, details?: Record<string, any>): string => {
    const timestamp = new Date().toISOString();
    const detailsStr = details ? ` ${JSON.stringify(details)}` : '';
    return `[${timestamp}] [${level.toUpperCase()}] [${source}] ${message}${detailsStr}`;
  };

  const log = (level: LogLevel, message: string, details?: Record<string, any>) => {
    // Check if we should log this level
    if (LOG_LEVEL_PRIORITY[level] < LOG_LEVEL_PRIORITY[MIN_LOG_LEVEL]) {
      return;
    }

    const formattedMessage = formatMessage(level, message, details);
    const style = LOG_STYLES[level];

    // Console output with styling
    switch (level) {
      case 'debug':
        console.debug(`%c${formattedMessage}`, style);
        break;
      case 'info':
        console.info(`%c${formattedMessage}`, style);
        break;
      case 'warn':
        console.warn(`%c${formattedMessage}`, style);
        break;
      case 'error':
        console.error(`%c${formattedMessage}`, style);
        break;
    }

    // Build structured log entry for potential remote ingestion

    // TODO: Optionally send to remote logging service
    // _sendToRemoteLog(logEntry);
  };

  return {
    debug: (message: string, details?: Record<string, any>) => log('debug', message, details),
    info: (message: string, details?: Record<string, any>) => log('info', message, details),
    warn: (message: string, details?: Record<string, any>) => log('warn', message, details),
    error: (message: string, details?: Record<string, any>) => log('error', message, details),
  };
}

/**
 * Set minimum log level globally
 */
export function setMinLevel(level: LogLevel): void {
  MIN_LOG_LEVEL = level;
}

/**
 * Get current minimum log level
 */
export function getMinLevel(): LogLevel {
  return MIN_LOG_LEVEL;
}

// Pre-configured loggers for common modules
export const deviceStoreLogger = useLogger('deviceStore');
export const agentStoreLogger = useLogger('agentStore');
export const logStoreLogger = useLogger('logStore');
export const appStoreLogger = useLogger('appStore');
export const wsLogger = useLogger('WebSocket');
export const apiLogger = useLogger('API');
export const wsConsoleLogger = useLogger('wsConsole');
export const batchStoreLogger = useLogger('batchStore');
