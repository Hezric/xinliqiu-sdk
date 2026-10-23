import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { Credentials, InventoryTotals, RecordStatus } from "./types.js";
import { CAPABILITIES, describe } from "./capabilities.js";

const execFileAsync = promisify(execFile);

export interface Transport {
  run(argv: string[], timeoutMs: number): Promise<Array<Record<string, unknown>>>;
}

export class SubprocessTransport implements Transport {
  constructor(private readonly binary: string) {}

  async run(argv: string[], timeoutMs: number): Promise<Array<Record<string, unknown>>> {
    const { stdout } = await execFileAsync(this.binary, argv, {
      timeout: timeoutMs,
      maxBuffer: 64 * 1024 * 1024,
    });
    const events: Array<Record<string, unknown>> = [];
    for (const line of stdout.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("{")) continue;
      try {
        events.push(JSON.parse(trimmed));
      } catch {
        // ignore non-JSON noise
      }
    }
    return events;
  }
}

export class Xinliqiu {
  private readonly transport: Transport;

  constructor(
    private readonly credentials: Credentials,
    options: {
      transport?: Transport;
      probeBinary?: string;
      agentPublicKey?: string;
      defaultTimeoutMs?: number;
    } = {},
  ) {
    const binary = options.probeBinary ?? process.env.XINLIQIU_PROBE ?? "ble-probe";
    this.transport = options.transport ?? new SubprocessTransport(binary);
    this.agentPublicKey = options.agentPublicKey ?? process.env.XINLIQIU_AGENT_PUBLIC_KEY;
    this.defaultTimeoutMs = options.defaultTimeoutMs ?? 60_000;
  }

  private readonly agentPublicKey?: string;
  private readonly defaultTimeoutMs: number;

  static capabilities() {
    return describe();
  }

  static requireCapability(name: string): void {
    const entry = CAPABILITIES[name];
    if (!entry) throw new Error(`unknown capability: ${name}`);
    if (entry.status === "unavailable") throw new Error(`${name}: ${entry.note}`);
  }

  private base(op: string, capture: string): string[] {
    const argv = [
      op,
      "--id", this.credentials.deviceId,
      "--peer-id-file", this.credentials.peerIdFile,
      "--key-file", this.credentials.keyFile,
      "--capture", capture,
    ];
    if (this.agentPublicKey) argv.push("--agent-public-key", this.agentPublicKey);
    return argv;
  }

  private async run(argv: string[], timeoutMs?: number): Promise<Record<string, unknown>> {
    const events = await this.transport.run(argv, timeoutMs ?? this.defaultTimeoutMs);
    const finished = events.find((e) => e.event === "finished");
    if (!finished) throw new Error("probe produced no terminal event");
    const code = finished.exitCode;
    if (code !== 0 && code !== 3) {
      throw new Error(`probe exit ${String(code)}: ${String(finished.reason)}`);
    }
    return finished;
  }

  async status(capturePath = "/tmp/xinliqiu-status.jsonl"): Promise<RecordStatus> {
    const result = await this.run([...this.base("record-status", capturePath), "--seconds", "45"]);
    const recordByte = Number(result.recordByte);
    return {
      recording: !(recordByte === 0 || recordByte === -1 || Number.isNaN(recordByte)),
      batteryPercent: typeof result.batteryByte === "number" ? result.batteryByte : null,
      errorCode: typeof result.errorByte === "number" ? result.errorByte : null,
    };
  }

  async inventoryTotals(startMs = 0, endMs = 9_999_999_999_999, capturePath = "/tmp/xinliqiu-storage.jsonl"): Promise<InventoryTotals> {
    const result = await this.run([
      ...this.base("storage-info", capturePath),
      "--start-ms", String(startMs),
      "--end-ms", String(endMs),
      "--seconds", "45",
    ]);
    const info = (result.storageInfo ?? {}) as Record<string, unknown>;
    return {
      count: Number(info.recordAndVoiceInputCount ?? 0),
      bytes: Number(info.recordAndVoiceInputBytes ?? 0),
      highlightCount: Number(info.highlightCount ?? 0),
      highlightBytes: Number(info.highlightBytes ?? 0),
      hasFileList: Boolean(info.fileListPresent),
      hasCursor: Boolean(info.cursorPresent),
    };
  }

  async recordStart(capturePath = "/tmp/xinliqiu-start.jsonl"): Promise<Record<string, unknown>> {
    return this.run([...this.base("record-start", capturePath), "--seconds", "45"]);
  }

  async recordStop(capturePath = "/tmp/xinliqiu-stop.jsonl"): Promise<Record<string, unknown>> {
    return this.run([...this.base("record-stop", capturePath), "--seconds", "45"]);
  }

  async collectVoiceprint(seconds = 6, outputDir: string, capturePath = "/tmp/xinliqiu-vp.jsonl"): Promise<Record<string, unknown>> {
    if (seconds < 1 || seconds > 127) throw new Error("voiceprint duration must be 1..127 seconds");
    const window = Math.max(45, seconds + 39);
    return this.run(
      [
        ...this.base("collect-voiceprint", capturePath),
        "--output-dir", outputDir,
        "--duration", String(seconds),
        "--seconds", String(window),
      ],
      (window + 30) * 1000,
    );
  }

  async syncRange(startMs: number, endMs: number, outputDir: string, seconds = 300, capturePath = "/tmp/xinliqiu-sync.jsonl"): Promise<Record<string, unknown>> {
    return this.run(
      [
        ...this.base("sync-recordings", capturePath),
        "--start-ms", String(startMs),
        "--end-ms", String(endMs),
        "--output-dir", outputDir,
        "--seconds", String(seconds),
      ],
      (seconds + 60) * 1000,
    );
  }
}
