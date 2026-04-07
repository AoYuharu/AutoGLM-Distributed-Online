import React from 'react';
import { Empty } from 'antd';
import { DeviceCard } from './DeviceCard';
import { useDeviceStore } from '../../stores/deviceStore';

interface DeviceListProps {
  showCheckbox?: boolean;
  onAgentClick?: (deviceId: string) => void;
  onLogClick?: (deviceId: string) => void;
  onRemarkChange?: (deviceId: string, remark: string) => void;
  onDelete?: (deviceId: string) => void;
  compact?: boolean;
}

export const DeviceList: React.FC<DeviceListProps> = ({
  showCheckbox = false,
  onAgentClick,
  onLogClick,
  onRemarkChange,
  onDelete,
  compact = false,
}) => {
  const { getFilteredDevices, selectedDevices, toggleDevice, updateDevice, removeDevice } = useDeviceStore();
  const devices = getFilteredDevices();

  const handleRemarkChange = (deviceId: string, remark: string) => {
    updateDevice(deviceId, { remark });
    onRemarkChange?.(deviceId, remark);
  };

  const handleDelete = (deviceId: string) => {
    removeDevice(deviceId);
    onDelete?.(deviceId);
  };

  if (devices.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="暂无设备"
        className="py-12"
      />
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {devices.map((device) => (
        <DeviceCard
          key={device.device_id}
          device={device}
          selected={selectedDevices.has(device.device_id)}
          showCheckbox={showCheckbox}
          onSelect={showCheckbox ? toggleDevice : undefined}
          onAgentClick={onAgentClick}
          onLogClick={onLogClick}
          onRemarkChange={handleRemarkChange}
          onDelete={handleDelete}
          compact={compact}
        />
      ))}
    </div>
  );
};
