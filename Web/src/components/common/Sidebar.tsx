import React from 'react';
import { Layout, Menu, Input, Select, theme } from 'antd';
import {
  MonitorOutlined,
  RobotOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { useAppStore } from '../../stores/appStore';
import { useDeviceStore } from '../../stores/deviceStore';
import type { ViewMode } from '../../types';

const { Sider } = Layout;

export const AppSidebar: React.FC = () => {
  const { sidebarCollapsed, viewMode, setViewMode } = useAppStore();
  const { setFilter, devices } = useDeviceStore();
  const { token } = theme.useToken();

  const menuItems: MenuProps['items'] = [
    {
      key: 'monitor',
      icon: <MonitorOutlined />,
      label: '设备监控',
    },
    {
      key: 'agent',
      icon: <RobotOutlined />,
      label: 'Agent 控制',
    },
    {
      key: 'batch',
      icon: <AppstoreOutlined />,
      label: '批处理',
    },
  ];

  const statusCounts = React.useMemo(() => {
    const allDevices = Object.values(devices);
    return {
      total: allDevices.length,
      idle: allDevices.filter((d) => d.status === 'idle').length,
      busy: allDevices.filter((d) => d.status === 'busy').length,
      offline: allDevices.filter((d) => d.status === 'offline').length,
      error: allDevices.filter((d) => d.status === 'error').length,
    };
  }, [devices]);

  return (
    <Sider
      collapsible
      collapsed={sidebarCollapsed}
      trigger={null}
      width={280}
      className="border-r border-gray-200 dark:border-gray-700 overflow-auto"
      style={{
        background: token.colorBgContainer,
      }}
    >
      {/* Menu */}
      <div className="p-4">
        <Menu
          mode="inline"
          selectedKeys={[viewMode]}
          items={menuItems}
          onClick={({ key }) => setViewMode(key as ViewMode)}
          className="border-none"
        />
      </div>

      {/* Stats */}
      {!sidebarCollapsed && (
        <div className="px-4 pb-4">
          <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
            <div className="text-sm font-medium mb-2 text-gray-600 dark:text-gray-400">
              设备统计
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-green-500"></span>
                <span>空闲: {statusCounts.idle}</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-yellow-500"></span>
                <span>忙碌: {statusCounts.busy}</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-gray-400"></span>
                <span>离线: {statusCounts.offline}</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-red-500"></span>
                <span>异常: {statusCounts.error}</span>
              </div>
            </div>
            <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700 text-center text-sm font-medium">
              总计: {statusCounts.total} 台设备
            </div>
          </div>
        </div>
      )}

      {/* Quick filters */}
      {!sidebarCollapsed && (
        <div className="px-4 pb-4 space-y-3">
          <Input.Search
            placeholder="搜索设备..."
            allowClear
            onSearch={(value) => setFilter({ search: value })}
            onChange={(e) => setFilter({ search: e.target.value })}
          />
          <Select
            placeholder="筛选平台"
            allowClear
            style={{ width: '100%' }}
            onChange={(value) => setFilter({ platform: value })}
            options={[
              { value: 'android', label: 'Android' },
              { value: 'harmonyos', label: 'HarmonyOS' },
              { value: 'ios', label: 'iOS' },
            ]}
          />
        </div>
      )}

      {/* Footer */}
      <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-center">
          <span className="text-xs text-gray-400">v1.0.0</span>
        </div>
      </div>
    </Sider>
  );
};
