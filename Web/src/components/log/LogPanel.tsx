import React, { useState, useEffect } from 'react';
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
} from 'antd';
import {
  DownloadOutlined,
  DeleteOutlined,
  UploadOutlined,
  SearchOutlined,
  InfoCircleOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  MobileOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useLogStore } from '../../stores/logStore';
import type { LogEntry, LogLevel } from '../../types';

interface LogPanelProps {
  deviceId: string;
  visible?: boolean;
  onClose?: () => void;
}

// Log type icons
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

// Log type display names
const getLogTypeName = (type: string): string => {
  const names: Record<string, string> = {
    device_connect: '设备连接',
    device_disconnect: '设备断开',
    task_start: '任务开始',
    task_complete: '任务完成',
    task_failed: '任务失败',
    action_execute: '动作执行',
    action_success: '动作成功',
    action_failed: '动作失败',
    screenshot_upload: '截图上传',
    system: '系统消息',
  };
  return names[type] || type;
};

// Log type tag colors
const getLogTypeColor = (type: string): string => {
  if (type.startsWith('device')) return 'blue';
  if (type.startsWith('task')) return 'purple';
  if (type.startsWith('action')) return 'cyan';
  if (type.startsWith('screenshot')) return 'orange';
  return 'default';
};

export const LogPanel: React.FC<LogPanelProps> = ({ deviceId, onClose }) => {
  const [searchText, setSearchText] = useState('');
  const [levelFilter, setLevelFilter] = useState<LogLevel | undefined>();
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [isUploadModalVisible, setIsUploadModalVisible] = useState(false);

  const { getLogsForDevice, clearLogs, addLogs, fetchLogs } = useLogStore();
  const logs = getLogsForDevice(deviceId);

  // Fetch logs from API when component mounts
  useEffect(() => {
    if (deviceId) {
      fetchLogs(deviceId);
    }
  }, [deviceId]);

  // Filter logs
  const filteredLogs = logs.filter((log) => {
    if (levelFilter && log.level !== levelFilter) return false;
    if (typeFilter && log.type !== typeFilter) return false;
    if (searchText && !log.message.toLowerCase().includes(searchText.toLowerCase())) {
      return false;
    }
    return true;
  });

  // Export logs
  const handleExport = (format: 'json' | 'csv' | 'txt') => {
    let content: string;
    let filename: string;
    let mimeType: string;

    if (format === 'json') {
      content = JSON.stringify(filteredLogs, null, 2);
      filename = `device_logs_${deviceId}_${dayjs().format('YYYYMMDD_HHmmss')}.json`;
      mimeType = 'application/json';
    } else if (format === 'csv') {
      const headers = ['时间', '类型', '级别', '消息', '任务ID'];
      const rows = filteredLogs.map((log) => [
        dayjs(log.timestamp).format('YYYY-MM-DD HH:mm:ss'),
        log.type,
        log.level,
        log.message,
        log.task_id || '',
      ]);
      content = [headers.join(','), ...rows.map((r) => r.map((c) => `"${c}"`).join(','))].join('\n');
      filename = `device_logs_${deviceId}_${dayjs().format('YYYYMMDD_HHmmss')}.csv`;
      mimeType = 'text/csv';
    } else {
      content = filteredLogs
        .map((log) => `[${dayjs(log.timestamp).format('HH:mm:ss')}] ${log.type}: ${log.message}`)
        .join('\n');
      filename = `device_logs_${deviceId}_${dayjs().format('YYYYMMDD_HHmmss')}.txt`;
      mimeType = 'text/plain';
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    message.success(`日志已导出为 ${format.toUpperCase()}`);
  };

  // Upload logs to server
  const handleUpload = async (file: File) => {
    try {
      const text = await file.text();
      const uploadedLogs: LogEntry[] = JSON.parse(text);

      // Validate and add logs
      if (Array.isArray(uploadedLogs)) {
        addLogs(deviceId, uploadedLogs.map((log, i) => ({
          ...log,
          id: `uploaded_${Date.now()}_${i}`,
          device_id: deviceId,
        })));
        message.success(`成功上传 ${uploadedLogs.length} 条日志`);
      } else {
        message.error('日志格式无效');
      }
    } catch (error) {
      message.error('解析日志文件失败');
    }
    return false; // Prevent default upload
  };

  const columns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 180,
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
      width: 120,
      render: (type: string) => (
        <Tag color={getLogTypeColor(type)}>{getLogTypeName(type)}</Tag>
      ),
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (message: string, record: LogEntry) => (
        <div className="flex items-center gap-2">
          {getLogIcon(record.level)}
          <span className="truncate">{message}</span>
        </div>
      ),
    },
    {
      title: '任务ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 150,
      render: (task_id: string) =>
        task_id ? (
          <Tag>{task_id}</Tag>
        ) : (
          <span className="text-gray-400">-</span>
        ),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: any, record: LogEntry) =>
        record.screenshot_url && (
          <Button type="link" size="small">
            查看截图
          </Button>
        ),
    },
  ];

  return (
    <Card
      title={
        <div className="flex items-center gap-2">
          <MobileOutlined />
          <span>日志 - {deviceId}</span>
        </div>
      }
      extra={
        <Space>
          <Button
            icon={<UploadOutlined />}
            onClick={() => setIsUploadModalVisible(true)}
          >
            上报日志
          </Button>
          <Button.Group>
            <Button icon={<DownloadOutlined />} onClick={() => handleExport('json')}>
              JSON
            </Button>
            <Button icon={<DownloadOutlined />} onClick={() => handleExport('csv')}>
              CSV
            </Button>
            <Button icon={<DownloadOutlined />} onClick={() => handleExport('txt')}>
              TXT
            </Button>
          </Button.Group>
          <Button
            danger
            icon={<DeleteOutlined />}
            onClick={() => clearLogs(deviceId)}
          >
            清空
          </Button>
          {onClose && (
            <Button onClick={onClose}>关闭</Button>
          )}
        </Space>
      }
    >
      {/* Filters */}
      <div className="flex items-center gap-4 mb-4 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
        <Input
          placeholder="搜索日志..."
          prefix={<SearchOutlined />}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{ width: 200 }}
          allowClear
        />
        <Select
          placeholder="日志级别"
          allowClear
          style={{ width: 120 }}
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
          style={{ width: 140 }}
          value={typeFilter}
          onChange={setTypeFilter}
          options={[
            { value: 'device_connect', label: '设备连接' },
            { value: 'task_start', label: '任务开始' },
            { value: 'task_complete', label: '任务完成' },
            { value: 'action_execute', label: '动作执行' },
            { value: 'action_success', label: '动作成功' },
            { value: 'action_failed', label: '动作失败' },
            { value: 'screenshot_upload', label: '截图上传' },
          ]}
        />
        <div className="flex-1 text-right text-sm text-gray-500">
          共 {filteredLogs.length} 条日志
        </div>
      </div>

      {/* Log table */}
      <Table
        dataSource={filteredLogs}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        scroll={{ y: 400 }}
        className="log-table"
      />

      {/* Upload modal */}
      <Modal
        title="上报日志到服务端"
        open={isUploadModalVisible}
        onCancel={() => setIsUploadModalVisible(false)}
        footer={null}
      >
        <div className="py-4">
          <Upload.Dragger
            accept=".json"
            beforeUpload={handleUpload}
            showUploadList={false}
          >
            <p className="text-lg mb-2">
              <UploadOutlined />
            </p>
            <p className="text-sm">点击或拖拽 JSON 日志文件到此区域</p>
            <p className="text-xs text-gray-400 mt-2">
              支持从客户端导出的 JSON 格式日志
            </p>
          </Upload.Dragger>

          <div className="mt-4 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-sm">
            <p className="text-blue-700 dark:text-blue-300">
              <InfoCircleOutlined className="mr-1" />
              日志上报说明:
            </p>
            <ul className="list-disc list-inside mt-2 text-blue-600 dark:text-blue-400">
              <li>选择从客户端设备导出的 JSON 格式日志</li>
              <li>日志将被上传到服务端进行统一存储和分析</li>
              <li>支持批量上传多个设备的日志</li>
            </ul>
          </div>
        </div>
      </Modal>
    </Card>
  );
};
