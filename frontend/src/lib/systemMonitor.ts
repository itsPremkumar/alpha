import { get } from "./http";

/** RAM snapshot — the primary metric of the system monitor. */
export interface SystemRam {
  total_mb: number;
  used_mb: number;
  available_mb: number;
  free_mb: number;
  percent: number;
}

export interface SystemSwap {
  total_mb: number;
  used_mb: number;
  free_mb: number;
  percent: number;
}

export interface SystemCpuCore {
  core: number;
  percent: number;
  frequency_mhz: number | null;
}

export interface SystemCpu {
  percent: number;
  cores: number;
  physical_cores: number | null;
  frequency_mhz: number | null;
  min_frequency_mhz: number | null;
  max_frequency_mhz: number | null;
  load: { one_minute: number; five_minutes: number; fifteen_minutes: number } | null;
  per_core: SystemCpuCore[];
}

export interface SystemDisk {
  mount: string;
  device: string;
  filesystem: string;
  total_mb: number;
  used_mb: number;
  free_mb: number;
  percent: number;
  read_only: boolean | null;
}

export interface SystemGpu {
  index: number;
  name: string;
  vendor: string;
  utilization_percent: number | null;
  memory_used_mb: number | null;
  memory_total_mb: number | null;
  memory_percent: number | null;
  temperature_c: number | null;
  source: string;
}

export interface SystemNetworkInterface {
  name: string;
  is_up: boolean;
  speed_mbps: number | null;
  ipv4: string[];
  ipv6: string[];
  bytes_sent: number;
  bytes_recv: number;
  packets_sent: number;
  packets_recv: number;
}

export interface SystemNetwork {
  bytes_sent: number;
  bytes_recv: number;
  upload_mbps: number;
  download_mbps: number;
  interfaces: SystemNetworkInterface[];
}

export interface SystemInternet {
  reachable: boolean;
  rtt_ms: number | null;
  host: string;
}

export interface SystemHostInfo {
  hostname: string;
  os: string;
  os_version: string;
  uptime_seconds: number;
}

export interface SystemAlert {
  key: string;
  severity: string;
  category: string;
  message: string;
  value: number | null;
  threshold: number | null;
  timestamp: number | null;
}

export interface SystemProcess {
  pid: number;
  name: string;
  status: string;
  cpu_percent: number;
  memory_percent: number;
  memory_mb: number;
}

/** Latest host snapshot with active alerts attached. */
export interface SystemVitals {
  timestamp: number;
  health: string;
  ram: SystemRam;
  swap: SystemSwap;
  disks: SystemDisk[];
  cpu: SystemCpu;
  gpus: SystemGpu[];
  network: SystemNetwork;
  internet: SystemInternet;
  system: SystemHostInfo;
  psutil_available: boolean;
  alerts: SystemAlert[];
}

/** One compact trend sample for charts. */
export interface SystemHistoryPoint {
  timestamp: number;
  ram_percent: number;
  swap_percent: number;
  cpu_percent: number;
  disk_percent: number;
  upload_mbps: number;
  download_mbps: number;
  gpu_percent: number | null;
  internet_reachable: boolean | null;
  internet_rtt_ms: number | null;
}

export interface SystemHistory {
  minutes: number;
  points: SystemHistoryPoint[];
}

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" ? (v as Rec) : {};
}

function num(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function optNum(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function toAlert(a: unknown): SystemAlert {
  const r = rec(a);
  return {
    key: String(r.key ?? ""),
    severity: String(r.severity ?? ""),
    category: String(r.category ?? "system"),
    message: String(r.message ?? ""),
    value: optNum(r.value),
    threshold: optNum(r.threshold),
    timestamp: optNum(r.timestamp),
  };
}

/** Latest snapshot: RAM (primary), disk, CPU, GPU, network, internet, host info, alerts. */
export async function fetchSystemVitals(): Promise<SystemVitals> {
  const d = await get<Rec>("/system/vitals");
  const memory = rec(d.memory);
  const ram = rec(memory.ram ?? d.ram);
  const swap = rec(memory.swap);
  const cpu = rec(d.cpu);
  const network = rec(d.network);
  const internet = rec(d.internet);
  const system = rec(d.system);
  return {
    timestamp: num(d.timestamp),
    health: String(d.health ?? "unknown"),
    ram: {
      total_mb: num(ram.total_mb),
      used_mb: num(ram.used_mb),
      available_mb: num(ram.available_mb ?? ram.free_mb),
      free_mb: num(ram.free_mb),
      percent: num(ram.percent),
    },
    swap: {
      total_mb: num(swap.total_mb),
      used_mb: num(swap.used_mb),
      free_mb: num(swap.free_mb),
      percent: num(swap.percent),
    },
    disks: Array.isArray(d.disks)
      ? d.disks.map((x) => {
          const r = rec(x);
          return {
            mount: String(r.mount ?? "?"),
            device: String(r.device ?? ""),
            filesystem: String(r.filesystem ?? ""),
            total_mb: num(r.total_mb),
            used_mb: num(r.used_mb),
            free_mb: num(r.free_mb),
            percent: num(r.percent),
            read_only: typeof r.read_only === "boolean" ? (r.read_only as boolean) : null,
          };
        })
      : [],
    cpu: {
      percent: num(cpu.percent),
      cores: num(cpu.cores),
      physical_cores: optNum(cpu.physical_cores),
      frequency_mhz: optNum(cpu.frequency_mhz ?? cpu.freq_mhz),
      min_frequency_mhz: optNum(cpu.min_frequency_mhz),
      max_frequency_mhz: optNum(cpu.max_frequency_mhz),
      load: null,
      per_core: [],
    },
    gpus: Array.isArray(d.gpus)
      ? d.gpus.map((x, i) => {
          const r = rec(x);
          return {
            index: num(r.index, i),
            name: String(r.name ?? "GPU"),
            vendor: String(r.vendor ?? ""),
            utilization_percent: optNum(r.utilization_percent),
            memory_used_mb: optNum(r.memory_used_mb),
            memory_total_mb: optNum(r.memory_total_mb),
            memory_percent: optNum(r.memory_percent),
            temperature_c: optNum(r.temperature_c),
            source: String(r.source ?? ""),
          };
        })
      : [],
    network: {
      bytes_sent: num(network.bytes_sent),
      bytes_recv: num(network.bytes_recv),
      upload_mbps: num(network.upload_mbps ?? network.up_mbps),
      download_mbps: num(network.download_mbps ?? network.down_mbps),
      interfaces: [],
    },
    internet: {
      reachable: Boolean(internet.reachable),
      rtt_ms: optNum(internet.rtt_ms),
      host: String(internet.host ?? ""),
    },
    system: {
      hostname: String(system.hostname ?? ""),
      os: String(system.os ?? ""),
      os_version: String(system.os_version ?? ""),
      uptime_seconds: num(system.uptime_seconds),
    },
    psutil_available: Boolean(d.psutil_available),
    alerts: Array.isArray(d.alerts) ? d.alerts.map(toAlert) : [],
  };
}

/** Recent time series for the requested window (default last 5 minutes). */
export async function fetchSystemHistory(minutes = 5): Promise<SystemHistory> {
  const d = await get<Rec>(`/system/history?minutes=${minutes}`);
  const points = Array.isArray(d.points) ? d.points : [];
  return {
    minutes: num(d.minutes, minutes),
    points: points.map((p) => {
      const r = rec(p);
      return {
        timestamp: num(r.timestamp),
        ram_percent: num(r.ram_percent),
        swap_percent: num(r.swap_percent),
        cpu_percent: num(r.cpu_percent),
        disk_percent: num(r.disk_percent),
        upload_mbps: num(r.upload_mbps),
        download_mbps: num(r.download_mbps),
        gpu_percent: optNum(r.gpu_percent),
        internet_reachable: typeof r.internet_reachable === "boolean" ? (r.internet_reachable as boolean) : null,
        internet_rtt_ms: optNum(r.internet_rtt_ms),
      };
    }),
  };
}

/** Currently active alerts. */
export async function fetchSystemAlerts(): Promise<SystemAlert[]> {
  const d = await get<Rec>("/system/alerts");
  const list = Array.isArray(d.alerts) ? d.alerts : [];
  return (list as unknown[]).map(toAlert);
}

/** Top host processes (safe telemetry only — no command lines or env). */
export async function fetchSystemProcesses(limit = 10, sort: "cpu" | "memory" = "cpu"): Promise<SystemProcess[]> {
  const d = await get<Rec>(`/system/processes?limit=${limit}&sort=${sort}`);
  const procs = rec(d.processes);
  const key = sort === "memory" ? "top_memory" : "top_cpu";
  const list = Array.isArray(procs[key]) ? (procs[key] as unknown[]) : [];
  return list.map((p) => {
    const r = rec(p);
    return {
      pid: num(r.pid),
      name: String(r.name ?? "unknown"),
      status: String(r.status ?? ""),
      cpu_percent: num(r.cpu_percent),
      memory_percent: num(r.memory_percent),
      memory_mb: num(r.memory_mb),
    };
  });
}

/** Per-interface network state and counters. */
export async function fetchNetworkInterfaces(): Promise<SystemNetworkInterface[]> {
  const d = await get<Rec>("/system/network/interfaces");
  const list = Array.isArray(d.interfaces) ? d.interfaces : [];
  return (list as unknown[]).map((x) => {
    const r = rec(x);
    return {
      name: String(r.name ?? "?"),
      is_up: Boolean(r.is_up),
      speed_mbps: optNum(r.speed_mbps),
      ipv4: Array.isArray(r.ipv4) ? (r.ipv4 as unknown[]).map(String) : [],
      ipv6: Array.isArray(r.ipv6) ? (r.ipv6 as unknown[]).map(String) : [],
      bytes_sent: num(r.bytes_sent),
      bytes_recv: num(r.bytes_recv),
      packets_sent: num(r.packets_sent),
      packets_recv: num(r.packets_recv),
    };
  });
}
