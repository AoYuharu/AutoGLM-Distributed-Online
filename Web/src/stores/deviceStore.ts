import { create } from 'zustand';
import type { Device, DeviceStatus } from '../types';
import { deviceApi } from '../services/api';
import { deviceStoreLogger } from '../hooks/useLogger';

function mergeDeviceState(existing: Device | undefined, incoming: Partial<Device> & Pick<Device, 'device_id'>): Device {
  const merged = {
    ...existing,
    ...incoming,
  } as Device;

  if (incoming.current_task_id === undefined && existing?.current_task_id !== undefined) {
    merged.current_task_id = existing.current_task_id;
  }

  if (incoming.status === 'idle' && merged.current_task_id) {
    merged.current_task_id = undefined;
  }

  return merged;
}

interface DeviceState {
  devices: Record<string, Device>;
  selectedDevices: Set<string>;
  filter: {
    platform?: string;
    status?: DeviceStatus;
    search?: string;
  };
  loading: boolean;
  error: string | null;
  // Track offline devices for recovery on reconnect
  _offlineDevices: string[];

  // Actions
  fetchDevices: () => Promise<void>;
  setDevices: (devices: Device[]) => void;
  updateDevice: (deviceId: string, data: Partial<Device>) => void;
  addDevice: (device: Device) => void;
  removeDevice: (deviceId: string) => void;
  setDeviceOffline: (deviceId: string) => void;

  // Selection
  selectDevice: (deviceId: string) => void;
  deselectDevice: (deviceId: string) => void;
  toggleDevice: (deviceId: string) => void;
  selectAll: () => void;
  deselectAll: () => void;
  setSelectedDevices: (deviceIds: string[]) => void;

  // Filter
  setFilter: (filter: Partial<DeviceState['filter']>) => void;

  // Computed
  getFilteredDevices: () => Device[];
  getDeviceById: (deviceId: string) => Device | undefined;
  canOperateDevice: (deviceId: string) => boolean;
}

export const useDeviceStore = create<DeviceState>((set, get) => ({
  devices: {},
  selectedDevices: new Set(),
  filter: {},
  loading: false,
  error: null,
  _offlineDevices: [],

  fetchDevices: async () => {
    deviceStoreLogger.debug('[fetchDevices] Fetching devices');
    set({ loading: true, error: null });
    try {
      const response = await deviceApi.list();
      const currentDevices = get().devices;
      const devicesMap = response.devices.reduce((acc: Record<string, Device>, device: Device) => {
        acc[device.device_id] = mergeDeviceState(currentDevices[device.device_id], device);
        return acc;
      }, {});
      deviceStoreLogger.info('[fetchDevices] Devices fetched', { count: response.devices.length, devices: JSON.stringify(response.devices) });
      set({ devices: devicesMap, loading: false });
    } catch (error: any) {
      deviceStoreLogger.error('[fetchDevices] Failed to fetch devices', { error: error.message });
      set({ error: error.message || 'Failed to fetch devices', loading: false });
    }
  },

  setDevices: (devices) => {
    deviceStoreLogger.debug('[setDevices] Setting devices', { count: devices.length });
    const devicesMap = devices.reduce((acc, device) => {
      acc[device.device_id] = device;
      return acc;
    }, {} as Record<string, Device>);
    set({ devices: devicesMap });
  },

  updateDevice: (deviceId, data) => {
    const oldDevice = get().devices[deviceId];
    const oldStatus = oldDevice?.status;
    const nextDevice = mergeDeviceState(oldDevice, {
      ...(oldDevice || { device_id: deviceId } as Device),
      ...data,
      device_id: deviceId,
    });
    const newStatus = nextDevice.status;

    deviceStoreLogger.info('[updateDevice] Device status changed', {
      deviceId,
      oldStatus,
      newStatus,
      hasTask: !!nextDevice.current_task_id,
    });

    set((state) => ({
      devices: {
        ...state.devices,
        [deviceId]: nextDevice,
      },
    }));
  },

  addDevice: (device) => {
    deviceStoreLogger.info('[addDevice] Device added', {
      deviceId: device.device_id,
      platform: device.platform,
      status: device.status,
    });
    set((state) => ({
      devices: {
        ...state.devices,
        [device.device_id]: device,
      },
    }));
  },

  removeDevice: (deviceId) => {
    deviceStoreLogger.info('[removeDevice] Device removed', { deviceId });
    set((state) => {
      const { [deviceId]: _, ...rest } = state.devices;
      return { devices: rest };
    });
  },

  setDeviceOffline: (deviceId) => {
    const oldDevice = get().devices[deviceId];
    const oldStatus = oldDevice?.status;
    deviceStoreLogger.warn('[setDeviceOffline] Device went offline', {
      deviceId,
      oldStatus,
    });
    set((state) => ({
      devices: {
        ...state.devices,
        [deviceId]: {
          ...state.devices[deviceId],
          status: 'offline' as DeviceStatus,
        },
      },
      // 记录离线设备，用于重连后恢复
      _offlineDevices: state._offlineDevices.includes(deviceId)
        ? state._offlineDevices
        : [...state._offlineDevices, deviceId],
    }));
  },

  selectDevice: (deviceId) => {
    deviceStoreLogger.debug('[selectDevice] Device selected', { deviceId });
    set((state) => ({
      selectedDevices: new Set([...state.selectedDevices, deviceId]),
    }));
  },

  deselectDevice: (deviceId) => {
    deviceStoreLogger.debug('[deselectDevice] Device deselected', { deviceId });
    set((state) => {
      const newSet = new Set(state.selectedDevices);
      newSet.delete(deviceId);
      return { selectedDevices: newSet };
    });
  },

  toggleDevice: (deviceId) => {
    const isSelected = get().selectedDevices.has(deviceId);
    deviceStoreLogger.debug(`[toggleDevice] Device ${isSelected ? 'deselected' : 'selected'}`, { deviceId });
    set((state) => {
      const newSet = new Set(state.selectedDevices);
      if (newSet.has(deviceId)) {
        newSet.delete(deviceId);
      } else {
        newSet.add(deviceId);
      }
      return { selectedDevices: newSet };
    });
  },

  selectAll: () => {
    const devices = get().getFilteredDevices();
    deviceStoreLogger.debug('[selectAll] All devices selected', { count: devices.length });
    set({ selectedDevices: new Set(devices.map((d) => d.device_id)) });
  },

  deselectAll: () => {
    deviceStoreLogger.debug('[deselectAll] All devices deselected');
    set({ selectedDevices: new Set() });
  },

  setSelectedDevices: (deviceIds) => {
    deviceStoreLogger.debug('[setSelectedDevices] Selected devices set', { count: deviceIds.length });
    set({ selectedDevices: new Set(deviceIds) });
  },

  setFilter: (filter) => {
    deviceStoreLogger.debug('[setFilter] Filter updated', filter);
    set((state) => ({
      filter: { ...state.filter, ...filter },
    }));
  },

  getFilteredDevices: () => {
    const { devices, filter } = get();
    let result = Object.values(devices);

    if (filter.platform) {
      result = result.filter((d) => d.platform === filter.platform);
    }

    if (filter.status) {
      result = result.filter((d) => d.status === filter.status);
    }

    if (filter.search) {
      const search = filter.search.toLowerCase();
      result = result.filter(
        (d) =>
          (d.device_name?.toLowerCase() || '').includes(search) ||
          (d.device_id?.toLowerCase() || '').includes(search)
      );
    }

    return result;
  },

  getDeviceById: (deviceId) => {
    return get().devices[deviceId];
  },

  canOperateDevice: (deviceId) => {
    const device = get().devices[deviceId];
    if (!device) return false;
    // 离线或错误状态不能操作
    if (device.status === 'offline' || device.status === 'error') return false;
    // 忙碌状态不能启动新任务
    if (device.status === 'busy') return false;
    return true;
  },
}));
