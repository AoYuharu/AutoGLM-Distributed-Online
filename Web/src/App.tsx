import React, { useState, useEffect } from 'react';
import { Layout, ConfigProvider, theme as antdTheme, Modal, Tabs, Spin, message } from 'antd';
import { AppHeader } from './components/common/Header';
import { AppSidebar } from './components/common/Sidebar';
import { DeviceList } from './components/device/DeviceList';
import { AgentWindow } from './components/agent/AgentWindow';
import { LogPanel } from './components/log/LogPanel';
import { BatchTaskView } from './components/batch/BatchTaskView';
import { useAppStore } from './stores/appStore';
import { useAgentStore } from './stores/agentStore';
import { useDeviceStore } from './stores/deviceStore';
import { healthApi } from './services/api';

const { Content } = Layout;

const App: React.FC = () => {
  const { viewMode, theme: currentTheme } = useAppStore();
  const { initSession, currentDeviceId, endSession } = useAgentStore();
  const { fetchDevices } = useDeviceStore();

  const [agentWindowVisible, setAgentWindowVisible] = useState(false);
  const [logPanelVisible, setLogPanelVisible] = useState(false);
  const [selectedLogDeviceId, setSelectedLogDeviceId] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(true);

  // Initialize app - fetch devices from backend
  useEffect(() => {
    const init = async () => {
      try {
        // Check backend health
        const health = await healthApi.check();
        console.log('Backend health:', health);

        // Fetch devices
        await fetchDevices();

        useAppStore.getState().setWsConnected(true);
      } catch (error) {
        console.error('Failed to initialize:', error);
        message.error('无法连接到后端服务器');
      } finally {
        setInitializing(false);
      }
    };

    init();
  }, []);

  // Poll device status every 3 seconds
  useEffect(() => {
    const pollDevices = async () => {
      try {
        await fetchDevices();
      } catch (error) {
        console.error('Failed to poll devices:', error);
      }
    };

    // Start polling after initialization
    if (!initializing) {
      const interval = setInterval(pollDevices, 3000);
      return () => clearInterval(interval);
    }
  }, [initializing, fetchDevices]);

  // Show loading screen while initializing
  if (initializing) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        background: '#f5f5f5'
      }}>
        <Spin size="large" tip="正在连接服务器..." />
      </div>
    );
  }

  const handleAgentClick = (deviceId: string) => {
    initSession(deviceId);
    setAgentWindowVisible(true);
  };

  const handleLogClick = (deviceId: string) => {
    setSelectedLogDeviceId(deviceId);
    setLogPanelVisible(true);
  };

  const handleCloseAgentWindow = () => {
    endSession();
    setAgentWindowVisible(false);
  };

  const renderContent = () => {
    switch (viewMode) {
      case 'monitor':
        return (
          <div className="p-6">
            <div className="flex items-center justify-between mb-6">
              <h1 className="text-2xl font-bold m-0">设备监控</h1>
              <div className="flex gap-2">
                <Tabs
                  size="small"
                  items={[
                    { key: 'all', label: '全部' },
                    { key: 'idle', label: '空闲' },
                    { key: 'busy', label: '忙碌' },
                    { key: 'offline', label: '离线' },
                  ]}
                  onChange={(key) => {
                    if (key === 'all') {
                      useDeviceStore.getState().setFilter({ status: undefined });
                    } else {
                      useDeviceStore.getState().setFilter({ status: key as any });
                    }
                  }}
                />
              </div>
            </div>
            <DeviceList
              onAgentClick={handleAgentClick}
              onLogClick={handleLogClick}
            />
          </div>
        );
      case 'agent':
        return (
          <div className="p-6">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-2xl font-bold m-0">Agent 控制</h1>
                <p className="text-sm text-gray-500 mt-2 mb-0">选择设备后打开 Agent 会话窗口。</p>
              </div>
            </div>
            <DeviceList
              onAgentClick={handleAgentClick}
              onLogClick={handleLogClick}
            />
          </div>
        );
      case 'batch':
        return <BatchTaskView />;
      default:
        return null;
    }
  };

  return (
    <ConfigProvider
      theme={{
        algorithm:
          currentTheme === 'dark'
            ? antdTheme.darkAlgorithm
            : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: '#6366f1',
        },
      }}
    >
      <Layout className="min-h-screen">
        <AppHeader />
        <Layout>
          <AppSidebar />
          <Content
            className="overflow-auto"
            style={{
              background: currentTheme === 'dark' ? '#141414' : '#f5f5f5',
            }}
          >
            {renderContent()}
          </Content>
        </Layout>
      </Layout>

      {/* Agent Window Modal */}
      <Modal
        open={agentWindowVisible}
        onCancel={handleCloseAgentWindow}
        footer={null}
        width="90vw"
        style={{ top: 20 }}
        styles={{ body: { padding: 0, height: 'calc(90vh - 100px)', overflow: 'hidden' } }}
        title={null}
        closable={false}
      >
        <AgentWindow
          deviceId={currentDeviceId || ''}
          onClose={handleCloseAgentWindow}
        />
      </Modal>

      {/* Log Panel Modal */}
      <Modal
        open={logPanelVisible}
        onCancel={() => setLogPanelVisible(false)}
        footer={null}
        width="90vw"
        style={{ top: 20 }}
        styles={{ body: { padding: 0, height: 'calc(90vh - 100px)', overflow: 'hidden' } }}
        title={null}
        destroyOnClose
      >
        {selectedLogDeviceId && (
          <LogPanel
            deviceId={selectedLogDeviceId}
            onClose={() => setLogPanelVisible(false)}
          />
        )}
      </Modal>
    </ConfigProvider>
  );
};

export default App;
