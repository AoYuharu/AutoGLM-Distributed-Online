import React from 'react';
import { Layout, Button, Tag, Dropdown, Avatar, theme } from 'antd';
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ReloadOutlined,
  SettingOutlined,
  UserOutlined,
  WifiOutlined,
  DisconnectOutlined,
} from '@ant-design/icons';
import { useAppStore } from '../../stores/appStore';

const { Header } = Layout;

export const AppHeader: React.FC = () => {
  const { sidebarCollapsed, toggleSidebar, wsConnected, theme: currentTheme, toggleTheme } = useAppStore();
  const { token } = theme.useToken();

  return (
    <Header
      className="flex items-center justify-between px-4 border-b border-gray-200 dark:border-gray-700"
      style={{
        background: token.colorBgContainer,
        height: 56,
        lineHeight: '56px',
        position: 'relative',
      }}
    >
      {/* Left section */}
      <div className="flex items-center gap-4">
        <Button
          type="text"
          icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={toggleSidebar}
        />
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center">
            <span className="text-white font-bold text-sm">AG</span>
          </div>
          <span className="font-semibold text-lg">Open-AutoGLM</span>
        </div>
      </div>

      {/* Center section - Connection status */}
      <div className="flex items-center">
        <Tag
          color={wsConnected ? 'green' : 'default'}
          icon={wsConnected ? <WifiOutlined /> : <DisconnectOutlined />}
        >
          {wsConnected ? '已连接' : '未连接'}
        </Tag>
        <Button
          type="text"
          icon={<ReloadOutlined />}
          className="ml-2"
          onClick={() => window.location.reload()}
        >
          刷新
        </Button>
      </div>

      {/* Right section */}
      <div className="flex items-center gap-2">
        <Button
          type="text"
          icon={currentTheme === 'dark' ? '🌙' : '☀️'}
          onClick={toggleTheme}
        />

        <Dropdown
          menu={{
            items: [
              { key: 'settings', icon: <SettingOutlined />, label: '设置' },
              { type: 'divider' },
              { key: 'profile', icon: <UserOutlined />, label: '个人中心' },
            ],
          }}
        >
          <Avatar
            style={{ cursor: 'pointer' }}
            icon={<UserOutlined />}
            className="bg-blue-500"
          />
        </Dropdown>
      </div>
    </Header>
  );
};
