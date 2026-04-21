import React, { useEffect, useMemo, useState } from 'react';
import {
  Card,
  Button,
  Checkbox,
  Input,
  Radio,
  Progress,
  Tag,
  Space,
  Steps,
  message,
  Alert,
} from 'antd';
import {
  PlayCircleOutlined,
  StopOutlined,
  CheckCircleOutlined,
  LoadingOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  PlusOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons';
import clsx from 'clsx';
import { useDeviceStore } from '../../stores/deviceStore';
import { useAppStore } from '../../stores/appStore';
import { useBatchStore, type BatchTaskExecution } from '../../stores/batchStore';

const { TextArea } = Input;

interface BatchTaskConfig {
  instruction: string;
  modePolicy: 'force_cautious' | 'force_normal' | 'default';
  stopOnError: boolean;
}

export const BatchTaskView: React.FC = () => {
  const [step, setStep] = useState(0); // 0: select devices, 1: configure, 2: execute
  const [selectedDevices, setSelectedDevices] = useState<string[]>([]);
  const [taskConfig, setTaskConfig] = useState<BatchTaskConfig>({
    instruction: '',
    modePolicy: 'default',
    stopOnError: false,
  });

  const { devices } = useDeviceStore();
  const { setViewMode } = useAppStore();
  const {
    executions,
    isRunning,
    totalDevices,
    completedCount,
    failedCount,
    initBatchSession,
    endBatchSession,
    startBatchTask,
    interruptAll,
  } = useBatchStore();

  useEffect(() => {
    if (step !== 2 || selectedDevices.length === 0) {
      return;
    }

    initBatchSession(selectedDevices);
    return () => {
      endBatchSession();
    };
  }, [step, selectedDevices, initBatchSession, endBatchSession]);

  useEffect(() => {
    return () => {
      endBatchSession();
    };
  }, [endBatchSession]);

  const availableDevices = Object.values(devices).filter(
    (device) => device.status === 'idle' || device.status === 'error'
  );

  const selectedDevicesData = selectedDevices.map((id) => devices[id]).filter(Boolean);

  const executionsArray = useMemo(() => Object.values(executions), [executions]);
  const sortedExecutions = useMemo(
    () => [...executionsArray].sort((a, b) => a.deviceId.localeCompare(b.deviceId)),
    [executionsArray]
  );
  const hasExecutions = executionsArray.length > 0;
  const canShowResetActions = hasExecutions && !isRunning;

  const resetBatchFlow = () => {
    endBatchSession();
    setStep(0);
    setSelectedDevices([]);
    setTaskConfig({ instruction: '', modePolicy: 'default', stopOnError: false });
  };

  const handleDeviceToggle = (deviceId: string) => {
    setSelectedDevices((prev) =>
      prev.includes(deviceId)
        ? prev.filter((id) => id !== deviceId)
        : [...prev, deviceId]
    );
  };

  const handleSelectAll = () => {
    if (selectedDevices.length === availableDevices.length) {
      setSelectedDevices([]);
    } else {
      setSelectedDevices(availableDevices.map((device) => device.device_id));
    }
  };

  const handleNext = () => {
    if (step === 0 && selectedDevices.length === 0) {
      message.warning('请至少选择一个设备');
      return;
    }
    if (step === 1 && !taskConfig.instruction.trim()) {
      message.warning('请输入任务指令');
      return;
    }
    setStep((prev) => prev + 1);
  };

  const handleBack = () => {
    setStep((prev) => prev - 1);
  };

  const handleCancelBatch = () => {
    endBatchSession();
    setViewMode('monitor');
  };

  const handleStartExecution = async () => {
    if (!taskConfig.instruction.trim()) {
      message.error('请输入任务指令');
      return;
    }

    await startBatchTask({
      device_ids: selectedDevices,
      instruction: taskConfig.instruction,
      mode_policy: taskConfig.modePolicy,
      max_steps: 100,
      stop_on_error: taskConfig.stopOnError,
    });

    message.success(
      `已向 ${selectedDevices.length} 台设备提交启动请求，等待 task_created 回填任务信息`
    );
  };

  const handleInterruptAll = async () => {
    await interruptAll();
    message.success('已向所有运行中的设备发送中断请求');
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'pending':
        return <ClockCircleOutlined className="text-gray-400" />;
      case 'starting':
        return <ClockCircleOutlined className="text-blue-400" />;
      case 'running':
        return <LoadingOutlined className="text-blue-500" />;
      case 'completed':
        return <CheckCircleOutlined className="text-green-500" />;
      case 'failed':
        return <ExclamationCircleOutlined className="text-red-500" />;
      case 'interrupted':
        return <StopOutlined className="text-orange-500" />;
      default:
        return null;
    }
  };

  const getStatusText = (status: string) => {
    const texts: Record<string, string> = {
      pending: '等待中',
      starting: '等待创建任务',
      running: '执行中',
      completed: '已完成',
      failed: '失败',
      interrupted: '已中断',
    };
    return texts[status] || status;
  };

  const getStatusTagColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'success';
      case 'failed':
        return 'error';
      case 'running':
        return 'processing';
      case 'starting':
        return 'blue';
      case 'interrupted':
        return 'orange';
      default:
        return 'default';
    }
  };

  const getExecutionStepText = (
    status: string,
    currentStep: number,
    maxSteps: number,
    hasTaskId: boolean
  ) => {
    if (status === 'starting' && !hasTaskId) {
      return '等待 task_created';
    }

    if (!hasTaskId && status === 'pending') {
      return '等待启动';
    }

    return `${currentStep} / ${maxSteps} 步`;
  };

  const getExecutionPercent = (currentStep: number, maxSteps: number) => {
    if (!maxSteps || maxSteps <= 0) {
      return 0;
    }
    return Math.min(100, Math.round((currentStep / maxSteps) * 100));
  };

  const getExecutionProgressStatus = (status: string) => {
    if (status === 'failed') {
      return 'exception' as const;
    }
    if (status === 'completed') {
      return 'success' as const;
    }
    return undefined;
  };

  const getExecutionDetailText = (execution: BatchTaskExecution) => {
    if (execution.statusMessage) {
      return execution.statusMessage;
    }
    if (execution.error) {
      return execution.error;
    }
    return null;
  };

  const stopOnErrorEnabled = taskConfig.stopOnError;
  const stepThreeHelpText = stopOnErrorEnabled
    ? '已启用：任一设备失败时将自动中断其余运行中的设备'
    : '未启用失败联停；单设备失败不会自动中断其他设备';

  return (
    <div className="p-6">
      <div className="mb-6">
        <Steps
          current={step}
          items={[
            { title: '选择设备', content: '选择要执行任务的设备' },
            { title: '配置任务', content: '设置任务指令和参数' },
            { title: '执行监控', content: '查看执行进度和结果' },
          ]}
        />
      </div>

      {step === 0 && (
        <Card title="选择设备">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm text-gray-500">
              共 {availableDevices.length} 台可用设备，已选择 {selectedDevices.length} 台
            </div>
            <Button onClick={handleSelectAll}>
              {selectedDevices.length === availableDevices.length ? '取消全选' : '全选'}
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {availableDevices.map((device) => (
              <Card
                key={device.device_id}
                className={clsx(
                  'cursor-pointer transition-all',
                  selectedDevices.includes(device.device_id) &&
                    'ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-900/20'
                )}
                onClick={() => handleDeviceToggle(device.device_id)}
                size="small"
              >
                <div className="flex items-center gap-3">
                  <Checkbox
                    checked={selectedDevices.includes(device.device_id)}
                    onChange={() => handleDeviceToggle(device.device_id)}
                  />
                  <div className="flex-1">
                    <div className="font-medium">{device.device_name}</div>
                    <div className="text-xs text-gray-500">{device.device_id}</div>
                  </div>
                  <Tag color={device.status === 'idle' ? 'success' : 'warning'}>
                    {device.status === 'idle' ? '空闲' : '异常'}
                  </Tag>
                </div>
              </Card>
            ))}
          </div>

          {availableDevices.length === 0 && (
            <Alert
              type="warning"
              title="暂无可用设备"
              description="所有设备都在忙碌中或离线，请等待设备空闲后重试"
              className="mt-4"
            />
          )}

          <div className="flex justify-end mt-6">
            <Space>
              <Button onClick={handleCancelBatch}>取消</Button>
              <Button
                type="primary"
                onClick={handleNext}
                disabled={selectedDevices.length === 0}
              >
                下一步 <ArrowRightOutlined />
              </Button>
            </Space>
          </div>
        </Card>
      )}

      {step === 1 && (
        <Card title="配置任务">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-2">
                任务指令 <span className="text-red-500">*</span>
              </label>
              <TextArea
                value={taskConfig.instruction}
                onChange={(e) =>
                  setTaskConfig({ ...taskConfig, instruction: e.target.value })
                }
                placeholder="输入自然语言任务指令，例如：打开微信搜索附近的人"
                rows={4}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-2">执行模式策略</label>
              <Radio.Group
                value={taskConfig.modePolicy}
                onChange={(e) =>
                  setTaskConfig({ ...taskConfig, modePolicy: e.target.value })
                }
              >
                <Space direction="vertical">
                  <Radio value="default">
                    <span>跟随会话设置（保留服务端默认行为）</span>
                    <span className="text-xs text-gray-500 ml-2">
                      不主动改写 create_task 的默认 mode
                    </span>
                  </Radio>
                  <Radio value="force_normal">
                    <span>强制全部非谨慎</span>
                    <span className="text-xs text-gray-500 ml-2">
                      所有设备自动执行所有动作
                    </span>
                  </Radio>
                  <Radio value="force_cautious">
                    <span>强制全部谨慎</span>
                    <span className="text-xs text-gray-500 ml-2">
                      所有设备每个动作都需要确认
                    </span>
                  </Radio>
                </Space>
              </Radio.Group>
            </div>

            <div>
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={taskConfig.stopOnError}
                  onChange={(e) =>
                    setTaskConfig({
                      ...taskConfig,
                      stopOnError: e.target.checked,
                    })
                  }
                />
                <span className="text-sm">单设备失败时停止全部任务</span>
              </label>
            </div>

            <div className="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
              <div className="text-sm font-medium mb-2">已选择的设备</div>
              <div className="flex flex-wrap gap-2">
                {selectedDevicesData.map((device) => (
                  <Tag key={device.device_id}>{device.device_name}</Tag>
                ))}
              </div>
            </div>
          </div>

          <div className="flex justify-end mt-6">
            <Space>
              <Button onClick={handleBack}>上一步</Button>
              <Button onClick={handleCancelBatch}>取消</Button>
              <Button type="primary" onClick={handleNext}>
                开始执行 <ArrowRightOutlined />
              </Button>
            </Space>
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card
          title="执行进度"
          extra={
            <Space>
              <Tag color="blue">
                {completedCount} / {totalDevices} 完成
              </Tag>
              {failedCount > 0 && <Tag color="red">{failedCount} 失败</Tag>}
              {isRunning && (
                <Button danger icon={<StopOutlined />} onClick={handleInterruptAll}>
                  全部中断
                </Button>
              )}
            </Space>
          }
        >
          <div className="mb-4 text-sm text-gray-500">{stepThreeHelpText}</div>

          {!hasExecutions && (
            <div className="text-center py-12 space-y-4">
              <div className="text-sm text-gray-500">
                当前还没有执行项。点击“开始执行”后，会先按设备提交 create_task，再等待
                task_created 回填真实 taskId。
              </div>
              <Button
                type="primary"
                size="large"
                icon={<PlayCircleOutlined />}
                onClick={handleStartExecution}
              >
                开始执行
              </Button>
            </div>
          )}

          {hasExecutions && (
            <div className="space-y-4">
              {sortedExecutions.map((execution) => {
                const progress = getExecutionPercent(
                  execution.currentStep,
                  execution.maxSteps
                );
                const detailText = getExecutionDetailText(execution);
                return (
                  <Card key={execution.deviceId} size="small">
                    <div className="flex items-center gap-4">
                      <div className="w-8">{getStatusIcon(execution.status)}</div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-2 flex-wrap">
                          <span className="font-medium">
                            {devices[execution.deviceId]?.device_name || execution.deviceId}
                          </span>
                          <Tag>{execution.deviceId}</Tag>
                          <Tag color={getStatusTagColor(execution.status)}>
                            {getStatusText(execution.status)}
                          </Tag>
                          {execution.taskId && (
                            <Tag color="geekblue">taskId: {execution.taskId}</Tag>
                          )}
                        </div>
                        <Progress
                          percent={progress}
                          size="small"
                          status={getExecutionProgressStatus(execution.status)}
                        />
                        {detailText && (
                          <div
                            className={`text-xs mt-1 ${execution.status === 'failed' ? 'text-red-500' : 'text-gray-500'}`}
                          >
                            {detailText}
                          </div>
                        )}
                      </div>
                      <div className="text-sm text-gray-500 text-right min-w-[120px]">
                        <div>
                          {getExecutionStepText(
                            execution.status,
                            execution.currentStep,
                            execution.maxSteps,
                            !!execution.taskId
                          )}
                        </div>
                      </div>
                    </div>
                  </Card>
                );
              })}

              {canShowResetActions && (
                <div className="flex justify-center gap-4 mt-6">
                  <Button onClick={resetBatchFlow}>重新选择设备</Button>
                  <Button type="primary" icon={<PlusOutlined />} onClick={resetBatchFlow}>
                    新建批处理
                  </Button>
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
};
