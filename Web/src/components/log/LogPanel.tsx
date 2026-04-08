import React, { useEffect, useMemo, useState } from 'react';
import {
  Card,
  Table,
  Tag,
  Button,
  Input,
  Select,
  Space,
  Modal,
  Upload,
  message,
  Checkbox,
  Alert,
  Typography,
} from 'antd';
import {
  DownloadOutlined,
  UploadOutlined,
  SearchOutlined,
  InfoCircleOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  MobileOutlined,
  EyeOutlined,
  ReloadOutlined,
  FileImageOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useLogStore } from '../../stores/logStore';
import { logApi } from '../../services/api';
import type { LogEntry, LogLevel } from '../../types';

const { Text } = Typography;

interface LogPanelProps {
  deviceId: string;
  visible?: boolean;
  onClose?: () => void;
}

const getLogIcon = (level: LogLevel) => {
  switch (level) {
    case 'info':
      return <InfoCircleOutlined className="text-blue-500" />;
    case 'success':
      return <CheckCircleOutlined className="text-green-500" />;
    case 'warning':
      return <WarningOutlined className="text-yellow-500" />;
    case 'error':
      return <CloseCircleOutlined className="text-red-500" />;
    default:
      return <InfoCircleOutlined className="text-gray-500" />;
  }
};

const getLogTypeName = (type: string): string => {
  if (type.startsWith('chat_')) {
    const role = type.replace('chat_', '');
    return role === 'agent' ? 'Agent 对话' : role === 'user' ? '用户对话' : `对话 ${role}`;
  }

  if (type.startsWith('react_')) {
    const phase = type.replace('react_', '');
    return phase === 'reason' ? 'ReAct 思考' : phase === 'act' ? 'ReAct 执行' : phase === 'observe' ? 'ReAct 观察' : `ReAct ${phase}`;
  }

  const names: Record<string, string> = {
    observe_result: '观察结果',
    artifact_screenshot: '截图归档',
    device_log: '设备日志',
    system: '系统消息',
  };
  return names[type] || type;
};

const getLogTypeColor = (type: string): string => {
  if (type.startsWith('chat_')) return 'gold';
  if (type.startsWith('react_')) return 'purple';
  if (type.includes('screenshot')) return 'orange';
  if (type.startsWith('observe') || type.startsWith('device')) return 'blue';
  return 'default';
};

const getLevelName = (level: LogLevel) => {
  switch (level) {
    case 'info':
      return '信息';
    case 'success':
      return '成功';
    case 'warning':
      return '警告';
    case 'error':
      return '错误';
    default:
      return level;
  }
};

const formatTimestamp = (timestamp: string) => dayjs(timestamp).format('YYYY-MM-DD HH:mm:ss.SSS');

const formatDetails = (details?: Record<string, unknown>) => {
  if (!details) {
    return '';
  }
  try {
    return JSON.stringify(details, null, 2);
  } catch {
    return String(details);
  }
};

const csvEscape = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;

export const LogPanel: React.FC<LogPanelProps> = ({ deviceId, visible = true, onClose }) => {
  const [searchText, setSearchText] = useState('');
  const [levelFilter, setLevelFilter] = useState<LogLevel | undefined>();
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [onlyLatestTask, setOnlyLatestTask] = useState(true);
  const [isImportModalVisible, setIsImportModalVisible] = useState(false);
  const [previewImageUrl, setPreviewImageUrl] = useState<string | null>(null);
  const [previewTitle, setPreviewTitle] = useState('截图预览');

  const {
    getLogsForDevice,
    getArtifactsForDevice,
    getLatestTaskIdForDevice,
    importLogs,
    fetchLogs,
    loading,
    error,
  } = useLogStore();

  const logs = getLogsForDevice(deviceId);
  const artifacts = getArtifactsForDevice(deviceId);
  const latestTaskId = getLatestTaskIdForDevice(deviceId);

  useEffect(() => {
    if (deviceId && visible) {
      fetchLogs(deviceId);
    }
  }, [deviceId, visible, fetchLogs]);

  const typeOptions = useMemo(() => (
    Array.from(new Set(logs.map((log) => log.type)))
      .sort((a, b) => a.localeCompare(b))
      .map((type) => ({ value: type, label: getLogTypeName(type) }))
  ), [logs]);

  const timelineLogs = useMemo(() => {
    if (!onlyLatestTask || !latestTaskId) {
      return logs;
    }
    return logs.filter((log) => log.task_id === latestTaskId || !log.task_id);
  }, [logs, latestTaskId, onlyLatestTask]);

  const filteredLogs = useMemo(() => timelineLogs.filter((log) => {
    if (levelFilter && log.level !== levelFilter) return false;
    if (typeFilter && log.type !== typeFilter) return false;
    if (!searchText) return true;

    const keyword = searchText.toLowerCase();
    const searchFields = [
      log.message,
      log.type,
      log.task_id,
      log.source,
      log.phase,
      log.role,
      formatDetails(log.details),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();

    return searchFields.includes(keyword);
  }), [timelineLogs, levelFilter, typeFilter, searchText]);

  const handleBlobDownload = (content: string, mimeType: string, filename: string) => {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDirectDownload = (url: string | null | undefined, filename?: string) => {
    const normalized = logApi.getRawDownloadUrl(url);
    if (!normalized) {
      message.warning('该归档文件暂不可用');
      return;
    }

    const a = document.createElement('a');
    a.href = normalized;
    if (filename) {
      a.download = filename;
    }
    a.rel = 'noopener noreferrer';
    a.click();
  };

  const handleExport = (format: 'json' | 'csv' | 'txt') => {
    const timestamp = dayjs().format('YYYYMMDD_HHmmss');
    const baseName = `device_timeline_${deviceId}_${timestamp}`;

    if (format === 'json') {
      handleBlobDownload(JSON.stringify(filteredLogs, null, 2), 'application/json', `${baseName}.json`);
      message.success('当前日志视图已导出为 JSON');
      return;
    }

    if (format === 'csv') {
      const headers = ['timestamp', 'task_id', 'type', 'level', 'source', 'phase', 'role', 'message', 'screenshot_url', 'artifact_path', 'details'];
      const rows = filteredLogs.map((log) => [
        formatTimestamp(log.timestamp),
        log.task_id || '',
        log.type,
        log.level,
        log.source || '',
        log.phase || '',
        log.role || '',
        log.message,
        log.screenshot_url || '',
        log.artifact_path || '',
        formatDetails(log.details),
      ]);
      const content = [headers.join(','), ...rows.map((row) => row.map(csvEscape).join(','))].join('\n');
      handleBlobDownload(content, 'text/csv;charset=utf-8', `${baseName}.csv`);
      message.success('当前日志视图已导出为 CSV');
      return;
    }

    const content = filteredLogs
      .map((log) => {
        const lines = [
          `[${formatTimestamp(log.timestamp)}] ${getLevelName(log.level)} ${getLogTypeName(log.type)}`,
          `task_id: ${log.task_id || '-'}`,
          `source: ${log.source || '-'}`,
          `message: ${log.message}`,
        ];

        if (log.screenshot_url) {
          lines.push(`screenshot_url: ${log.screenshot_url}`);
        }
        if (log.artifact_path) {
          lines.push(`artifact_path: ${log.artifact_path}`);
        }
        if (log.details) {
          lines.push(`details: ${formatDetails(log.details)}`);
        }

        return lines.join('\n');
      })
      .join('\n\n------------------------------\n\n');

    handleBlobDownload(content, 'text/plain;charset=utf-8', `${baseName}.txt`);
    message.success('当前日志视图已导出为 TXT');
  };

  const handleImport = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const importedLogs = Array.isArray(parsed) ? parsed : parsed?.logs;

      if (!Array.isArray(importedLogs)) {
        message.error('日志文件格式无效，需为 LogEntry 数组或包含 logs 数组');
        return false;
      }

      importLogs(deviceId, importedLogs as LogEntry[]);
      setIsImportModalVisible(false);
      message.success(`已导入 ${importedLogs.length} 条本地日志，仅当前页面可见`);
    } catch {
      message.error('解析日志文件失败');
    }
    return false;
  };

  const openScreenshotPreview = (record: LogEntry) => {
    if (!record.screenshot_url) {
      message.warning('该日志没有可预览的截图');
      return;
    }
    setPreviewImageUrl(record.screenshot_url);
    setPreviewTitle(`${getLogTypeName(record.type)} · ${dayjs(record.timestamp).format('HH:mm:ss')}`);
  };

  const rawDownloadButtons = [
    {
      key: 'latest-log',
      label: '最新日志',
      url: artifacts?.latest_log_download,
      icon: <FileTextOutlined />,
      filename: `latest-log-${deviceId}.jsonl`,
    },
    {
      key: 'react-records',
      label: 'ReAct 记录',
      url: artifacts?.react_records_download,
      icon: <FileTextOutlined />,
      filename: `react-records-${deviceId}.jsonl`,
    },
    {
      key: 'chat-history',
      label: 'Chat History',
      url: artifacts?.chat_history_download,
      icon: <FileTextOutlined />,
      filename: `chat-history-${deviceId}.json`,
    },
    {
      key: 'latest-screenshot',
      label: '最新截图',
      url: artifacts?.latest_screenshot_download,
      icon: <FileImageOutlined />,
      filename: `latest-screenshot-${deviceId}.png`,
    },
  ];

  const columns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 200,
      render: (timestamp: string) => (
        <span className="text-gray-500 font-mono text-xs">
          {dayjs(timestamp).format('HH:mm:ss.SSS')}
        </span>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 140,
      render: (type: string) => (
        <Tag color={getLogTypeColor(type)}>{getLogTypeName(type)}</Tag>
      ),
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (value: string, record: LogEntry) => (
        <div className="flex items-center gap-2 min-w-0">
          {getLogIcon(record.level)}
          <div className="min-w-0">
            <div className="truncate">{value}</div>
            <div className="text-xs text-gray-400 mt-1">
              {record.source || '-'}
              {record.step_number !== undefined ? ` · step ${record.step_number}` : ''}
            </div>
          </div>
        </div>
      ),
    },
    {
      title: '任务ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 180,
      render: (taskId?: string) => taskId ? <Tag>{taskId}</Tag> : <span className="text-gray-400">-</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 170,
      render: (_: unknown, record: LogEntry) => (
        <Space size={0} wrap>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            disabled={!record.screenshot_url}
            onClick={() => openScreenshotPreview(record)}
          >
            查看截图
          </Button>
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            disabled={!record.download_url}
            onClick={() => handleDirectDownload(record.download_url, record.artifact_path?.split('/').pop())}
          >
            下载
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      className="h-full"
      styles={{ body: { display: 'flex', flexDirection: 'column', height: '100%' } }}
      title={
        <div className="flex items-center gap-2">
          <MobileOutlined />
          <span>设备归档日志 - {deviceId}</span>
        </div>
      }
      extra={
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => fetchLogs(deviceId)}>
            刷新
          </Button>
          <Button icon={<UploadOutlined />} onClick={() => setIsImportModalVisible(true)}>
            导入本地日志
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport('json')}>
            JSON
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport('csv')}>
            CSV
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport('txt')}>
            TXT
          </Button>
          {onClose && (
            <Button onClick={onClose}>关闭</Button>
          )}
        </Space>
      }
    >
      <div className="flex flex-col gap-4 h-full">
        <div className="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
          <div className="flex flex-wrap items-center gap-3 mb-3">
            <Input
              placeholder="搜索消息 / 类型 / task_id / details"
              prefix={<SearchOutlined />}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              style={{ width: 260 }}
              allowClear
            />
            <Select
              placeholder="日志级别"
              allowClear
              style={{ width: 140 }}
              value={levelFilter}
              onChange={setLevelFilter}
              options={[
                { value: 'info', label: '信息' },
                { value: 'success', label: '成功' },
                { value: 'warning', label: '警告' },
                { value: 'error', label: '错误' },
              ]}
            />
            <Select
              placeholder="日志类型"
              allowClear
              style={{ width: 180 }}
              value={typeFilter}
              onChange={setTypeFilter}
              options={typeOptions}
            />
            <Checkbox
              checked={onlyLatestTask}
              disabled={!latestTaskId}
              onChange={(e) => setOnlyLatestTask(e.target.checked)}
            >
              仅看本次任务
            </Checkbox>
            <div className="flex-1 text-right text-sm text-gray-500 min-w-[180px]">
              共 {filteredLogs.length} 条
              {latestTaskId ? ` · 最近 task_id: ${latestTaskId}` : ' · 无 task_id 可过滤'}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Text type="secondary">原始归档下载:</Text>
            {rawDownloadButtons.map((item) => (
              <Button
                key={item.key}
                size="small"
                icon={item.icon}
                disabled={!item.url}
                onClick={() => handleDirectDownload(item.url, item.filename)}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </div>

        {error && (
          <Alert
            type="error"
            showIcon
            message="日志加载失败"
            description={error}
          />
        )}

        <div className="flex-1 min-h-0">
          <Table
            dataSource={filteredLogs}
            columns={columns}
            rowKey="id"
            size="small"
            loading={loading}
            pagination={{
              pageSize: 20,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }}
            scroll={{ y: 420, x: 980 }}
            className="log-table"
            expandable={{
              expandedRowRender: (record: LogEntry) => (
                <div className="py-2">
                  <div className="text-xs text-gray-500 mb-2">
                    {formatTimestamp(record.timestamp)}
                    {record.artifact_path ? ` · ${record.artifact_path}` : ''}
                  </div>
                  <pre className="text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded overflow-x-auto whitespace-pre-wrap break-all m-0">
                    {formatDetails(record.details) || record.message}
                  </pre>
                </div>
              ),
              rowExpandable: (record: LogEntry) => Boolean(record.details),
            }}
            locale={{ emptyText: loading ? '正在加载日志...' : '暂无日志数据' }}
          />
        </div>
      </div>

      <Modal
        title="导入本地 JSON 日志"
        open={isImportModalVisible}
        onCancel={() => setIsImportModalVisible(false)}
        footer={null}
      >
        <div className="py-4">
          <Upload.Dragger
            accept=".json"
            beforeUpload={handleImport}
            showUploadList={false}
          >
            <p className="text-lg mb-2">
              <UploadOutlined />
            </p>
            <p className="text-sm">点击或拖拽 JSON 日志文件到此区域</p>
            <p className="text-xs text-gray-400 mt-2">
              仅导入到当前浏览器会话中查看，不会上传到服务端
            </p>
          </Upload.Dragger>

          <div className="mt-4 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-sm">
            <p className="text-blue-700 dark:text-blue-300">
              <InfoCircleOutlined className="mr-1" />
              支持导入当前面板导出的 JSON，或包含 logs 数组的调试文件。
            </p>
          </div>
        </div>
      </Modal>

      <Modal
        title={previewTitle}
        open={Boolean(previewImageUrl)}
        onCancel={() => setPreviewImageUrl(null)}
        footer={[
          <Button key="download" icon={<DownloadOutlined />} disabled={!previewImageUrl} onClick={() => handleDirectDownload(previewImageUrl || undefined)}>
            下载截图
          </Button>,
          <Button key="close" onClick={() => setPreviewImageUrl(null)}>
            关闭
          </Button>,
        ]}
        width="80vw"
      >
        <div className="flex items-center justify-center bg-gray-50 dark:bg-gray-900 rounded-lg min-h-[400px] overflow-auto p-4">
          {previewImageUrl && (
            <img
              src={previewImageUrl}
              alt="日志截图"
              style={{ maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain' }}
            />
          )}
        </div>
      </Modal>
    </Card>
  );
};
