import React, { useState } from 'react';
import { Card, Badge, Button, Checkbox, Space, Popconfirm, Input, message, Tag } from 'antd';
import {
  AndroidOutlined,
  AppleOutlined,
  ApiOutlined,
  RobotOutlined,
  FileTextOutlined,
  DeleteOutlined,
  EditOutlined,
  CheckOutlined,
  CloseOutlined,
  DisconnectOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import type { Device, DeviceStatus } from '../../types';
import { deviceApi } from '../../services/api';
import clsx from 'clsx';

interface DeviceCardProps {
  device: Device;
  selected?: boolean;
  showCheckbox?: boolean;
  onSelect?: (deviceId: string) => void;
  onAgentClick?: (deviceId: string) => void;
  onLogClick?: (deviceId: string) => void;
  onRemarkChange?: (deviceId: string, remark: string) => void;
  onDelete?: (deviceId: string) => void;
  compact?: boolean;
}

const getPlatformIcon = (platform: Device['platform']) => {
  switch (platform) {
    case 'android':
      return <AndroidOutlined />;
    case 'harmonyos':
      return <ApiOutlined />;
    case 'ios':
      return <AppleOutlined />;
    default:
      return <AndroidOutlined />;
  }
};

const getStatusColor = (status: DeviceStatus) => {
  switch (status) {
    case 'idle':
      return 'success';
    case 'busy':
      return 'warning';
    case 'offline':
      return 'default';
    case 'error':
      return 'error';
    default:
      return 'default';
  }
};

const getStatusText = (status: DeviceStatus) => {
  switch (status) {
    case 'idle':
      return '空闲';
    case 'busy':
      return '忙碌中';
    case 'offline':
      return '离线';
    case 'error':
      return '异常';
    default:
      return '未知';
  }
};

const getStatusDotClass = (status: DeviceStatus) => {
  switch (status) {
    case 'idle':
      return 'bg-green-500';
    case 'busy':
      return 'bg-yellow-500';
    case 'offline':
      return 'bg-gray-400';
    case 'error':
      return 'bg-red-500';
    default:
      return 'bg-gray-400';
  }
};

export const DeviceCard: React.FC<DeviceCardProps> = ({
  device,
  selected = false,
  showCheckbox = false,
  onSelect,
  onAgentClick,
  onLogClick,
  onRemarkChange,
  onDelete,
  compact = false,
}) => {
  console.log('[DeviceCard] Rendering device:', device.device_id, 'device_name:', device.device_name, 'full:', JSON.stringify(device));
  const [isEditingRemark, setIsEditingRemark] = useState(false);
  const [remarkValue, setRemarkValue] = useState(device.remark || '');

  const handleSaveRemark = async () => {
    try {
      await deviceApi.updateRemark(device.device_id, remarkValue);
      onRemarkChange?.(device.device_id, remarkValue);
      setIsEditingRemark(false);
      message.success('备注已保存');
    } catch (error) {
      message.error('保存备注失败');
    }
  };

  const handleCancelRemark = () => {
    setRemarkValue(device.remark || '');
    setIsEditingRemark(false);
  };

  const handleDelete = async () => {
    try {
      await deviceApi.delete(device.device_id);
      onDelete?.(device.device_id);
      message.success('设备已删除');
    } catch (error) {
      message.error('删除设备失败');
    }
  };

  return (
    <Card
      className={clsx(
        'relative transition-all duration-200 cursor-pointer hover:shadow-md',
        selected && 'ring-2 ring-blue-500'
      )}
      styles={{
        body: { padding: compact ? 12 : 16 },
      }}
      onClick={() => onAgentClick?.(device.device_id)}
    >
      {/* Checkbox */}
      {showCheckbox && (
        <Checkbox
          checked={selected}
          onChange={(e) => {
            e.stopPropagation();
            onSelect?.(device.device_id);
          }}
          className="absolute top-3 left-3"
        />
      )}

      {/* Status indicator */}
      <div className="absolute top-3 right-3 flex items-center gap-2">
        <span
          className={clsx(
            'w-3 h-3 rounded-full',
            getStatusDotClass(device.status)
          )}
        />
      </div>

      {/* Content */}
      <div className={clsx(showCheckbox && 'ml-6')}>
        {/* Header */}
        <div className="flex items-start gap-3 mb-3">
          <div
            className={clsx(
              'w-12 h-12 rounded-lg flex items-center justify-center text-xl',
              device.platform === 'android' && 'bg-green-100 text-green-600 dark:bg-green-900 dark:text-green-300',
              device.platform === 'harmonyos' && 'bg-blue-100 text-blue-600 dark:bg-blue-900 dark:text-blue-300',
              device.platform === 'ios' && 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
            )}
          >
            {getPlatformIcon(device.platform)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-medium text-base truncate">{device.device_name}</div>
            <div className="text-xs text-gray-500 truncate">{device.device_id}</div>
          </div>
        </div>

        {/* Info */}
        <div className="flex items-center gap-3 mb-3">
          <Badge status={getStatusColor(device.status)} text={getStatusText(device.status)} />
          {device.status === 'offline' && (
            <Tag color="default" icon={<DisconnectOutlined />}>
              已离线
            </Tag>
          )}
          {device.status === 'error' && (
            <Tag color="error" icon={<ExclamationCircleOutlined />}>
              异常
            </Tag>
          )}
          <span className="text-xs text-gray-300 dark:text-gray-600">|</span>
          <span className="text-xs text-gray-500">{device.os_version}</span>
        </div>

        {/* Actions */}
        <Space size={8}>
          <Button
            size="small"
            icon={<FileTextOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              onLogClick?.(device.device_id);
            }}
          >
            日志
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<RobotOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              onAgentClick?.(device.device_id);
            }}
            disabled={
              device.status === 'offline' ||
              device.status === 'error' ||
              device.status === 'busy'
            }
          >
            Agent
          </Button>
          <Popconfirm
            title="删除设备"
            description="确定要删除此设备记录吗？"
            onConfirm={handleDelete}
            onCancel={(e) => e?.stopPropagation()}
            okText="删除"
            cancelText="取消"
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={(e) => e.stopPropagation()}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>

        {/* Remark */}
        <div className="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700">
          {isEditingRemark ? (
            <div className="flex items-center gap-2">
              <Input
                size="small"
                placeholder="添加备注..."
                value={remarkValue}
                onChange={(e) => setRemarkValue(e.target.value)}
                onPressEnter={handleSaveRemark}
                onClick={(e) => e.stopPropagation()}
                maxLength={500}
                style={{ flex: 1 }}
              />
              <Button
                size="small"
                type="text"
                icon={<CheckOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  handleSaveRemark();
                }}
                style={{ color: '#52c41a' }}
              />
              <Button
                size="small"
                type="text"
                icon={<CloseOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  handleCancelRemark();
                }}
                style={{ color: '#ff4d4f' }}
              />
            </div>
          ) : (
            <div
              className="flex items-center gap-2 cursor-pointer group"
              onClick={(e) => {
                e.stopPropagation();
                setIsEditingRemark(true);
              }}
            >
              <span className="text-xs text-gray-400">备注:</span>
              <span className="text-xs text-gray-600 dark:text-gray-300 flex-1 truncate">
                {device.remark || '点击添加备注...'}
              </span>
              <EditOutlined className="text-xs text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />
            </div>
          )}
        </div>

        {/* Current task */}
        {device.status === 'busy' && device.current_task_id && (
          <div className="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700">
            <div className="text-xs text-gray-500">
              当前任务: <span className="text-yellow-600">{device.current_task_id}</span>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
};
