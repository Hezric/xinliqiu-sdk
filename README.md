<div align="center">

# 心力球 SDK

**从你自己的代码里访问 OPPO AI 心力球。**

录音 · 取回 · 解密 · 存储 · 交给 agent

[快速开始](#快速开始) · [能力边界](#能力边界) · [MCP](#mcp) · [存储](#存储) · [English](README.en.md)

</div>

---

### 把这个复制给你的 agent

> 帮我用 **心力球 SDK** 接管我的 OPPO AI 心力球（`CNMABWC301`）。
>
> 项目地址：https://github.com/kiyoakii/xinliqiu-sdk
>
> 你需要：
> 1. 先把仓库克隆到本地，并完整阅读仓库根目录的 `AGENTS.md` 与 `AGENT-SETUP.md`
> 2. 判断应该用 MCP、Python 库、TypeScript 还是 CLI，告诉我理由
> 3. 按 `AGENT-SETUP.md` 完成配对与验证，每一步展示真实命令输出
> 4. 用 `xinliqiu capabilities` 确认能力边界，不要基于 unavailable 项做设计
>
> 底线只有一条：**不要刷固件、不要格式化设备、不要重复执行 `init`**（它可能清掉设备里已有的录音）。
> 任何一步失败就停下来告诉我卡在哪里，不要反复重试。

---

## 这是什么

心力球是一个录音小设备。**录音用它自己里面安装的公钥加密，只有对应私钥的主人能读回来。** 这个 SDK 就是那条读回路径。

```
设备 ──蓝牙──► 你的代码 ──► 你的存储 ──► 你的 agent
                  │            │             │
            Python / TS    Cloudflare    MCP server
```

四种接口，同一套实现：Python 库、MCP server、CLI、TypeScript。

> **非官方项目。** 与 OPPO 无关联。只用于你自己拥有的设备和你自己的凭据。
> [法律声明](LEGAL.md)

---

## 能力边界

这张表就是 SDK 运行时返回的数据，会原样交给你的代码和 agent。

**可用**

| 能力 | 说明 |
|---|---|
| 鉴权 | 复用已有凭据，不需重新配对 |
| 读状态 | 是否在录、电量、错误码 |
| 库存统计 | 某时间段内的条目数与字节数 |
| 按范围取回 | 拿到设备时钟窗口内的容器 |
| 解密 | RSA 解封装 + AES-256-GCM，用你的私钥 |

**可用，但有前提**

| 能力 | 限制 |
|---|---|
| 录音 | 只在检测到人声时生成文件。全程没说话，就什么也没有 |
| 分段 | 语音间有静音会切成多个文件；单个文件约 60 秒。尾部静音会保留一小段 |
| 声纹采集 | 单次 1–127 秒，适合短采样，不适合长录音 |
| Opus → WAV | 容器里没有原生时长，只能按包数×20ms 推算 |

**不可用**

| 能力 | 原因 |
|---|---|
| 标记重点 (`mark`) | 蓝牙、App、实体键都没能触发它 |
| 笔记归档 (`.note.opus`) | 蓝牙上无法进入该路径 |
| AI 键对话模式 | 该键只是录音开关，松手不会结束 |

三项都从多个方向验证过，确实不存在。

---

## 开始使用

**不需要 OPPO 手机、不需要 OPPO 账号、不需要 root。**

```bash
pip install "xinliqiu-sdk[crypto]"

xinliqiu scan                                    # 找到设备 id
xinliqiu pair --device <id> --out-dir ./creds    # 配对，自动记住凭据
xinliqiu status                                  # 应能正常返回
```

配对约两分钟，**不会格式化、不会刷写任何东西**。

### 关于配对：两个槽位，先到先得

设备固件里只有**两个**凭据槽位（`ksc_write` 里 `if (1 < slot) return -1`）。

```
槽位 0 = 你的手机
槽位 1 = 这台电脑     ← SDK 配对后占用
```

手机和电脑可以**同时使用**，互不影响。但**同时最多只有两台**。

#### 再配一次不会挤掉手机

SDK 会把你第一次配对时用的槽位标识（alias）记在配置里。再次配对时
**复用它**，设备认出同一个 alias 就会**原地更新那个槽位**，不走淘汰。

所以你重复跑 `pair` 不会把手机踢出去。只有第三台全新设备才会挤掉最旧的。

#### 手机录的旧录音，解不开

手机配对时，录音公钥是 OPPO 服务器下发的（`device_security_config`），
私钥在服务器那边。所以：

| 步骤 | 能做到吗 |
|---|---|
| 电脑配对 | 能 |
| 把已有录音的密文拉下来 | 能 |
| **解成能听的音频** | **不能，永远不能** |
| `init` 之后新录的 | 能，那之后用的是你的公钥 |

这个 SDK 对已有用户的意义是：**从今往后的录音归你自己。**

### 想解密，必须做 `init`，而且必须保住那把私钥

配对（`pair`）只建立连接，不管加密密钥。**不跑 `init`，录音就无法被你自己解密**——
设备用它出厂的密钥加密，那把密钥不在你手上。

`init` 会把**你的公钥**装进设备，之后录的东西才能用你的私钥解开。它有两点必须知道：

- **可以丢弃设备上已有的录音**，所以命令要求 `--acknowledge-risk` 才会执行
- **每台设备只能做一次**，不能重做

建议顺序：拿到全新设备后**先 `init`，再开始录音**。

#### 私钥丢了 = 录音全没了

这不是提醒，是事实。设备只存公钥，**私钥只在你这台机器上**。

丢了私钥，后果准确地说是：

- **已经录下的录音**：解不开了
- **设备本身**：没坏，继续正常录音
- **新录的录音**：仍然用设备里那把公钥加密，而你手上已经没有对应的私钥了，**同样解不开**

所以丢了私钥 ≈ 失去了从这个设备读出任何可听内容的能力。没有恢复、
没有重置、没有人能帮你。

所以：

```bash
xinliqiu keygen     # 生成到 ~/.config/xinliqiu/owner.pem，权限 0400
```

**生成后立即把 `~/.config/xinliqiu/owner.pem` 复制到本机以外的地方**：
密码管理器、加密备份、或一个抽屉里的 U 盘。不要把它留在项目目录里——
`cd` 一下或删个文件夹就可能没了。

找 agent 帮你接入时，它会在 `init` 之前停下来要求你先备份并确认。

详细步骤：[docs/getting-started.md](docs/getting-started.md)

---

## Python

```python
from pathlib import Path
from aipin_sdk import Xinliqiu, Credentials, parse_cmpt_file, decrypt_enpt, to_wav

ball = Xinliqiu(Credentials(
    device_id="<id>",
    peer_id_file=Path("~/xinliqiu/peer-id.txt").expanduser(),
    key_file=Path("~/xinliqiu/key.json").expanduser(),
))

print(ball.capabilities())      # 先看能做什么
print(ball.status())

result = ball.sync_range(0, 1_800_000_000_000)
for path in sorted(Path(result["_output_dir"]).glob("*.bin")):
    for entry in parse_cmpt_file(path).by_kind("audio"):
        wav = to_wav(decrypt_enpt(entry.payload, Path("~/owner.pem").expanduser()))
        Path(f"{entry.name}.wav").write_bytes(wav)
```

常用方法：`status()`、`inventory_totals()`、`sync_range()`、`collect_voiceprint()`、`record_start()` / `record_stop()`。

> **时长怎么算：** 容器里没有原生样本数。得到的时长是**包数 × 20ms**，与麦克风实际录制时长不等。

---

## CLI

```bash
xinliqiu capabilities          # 能力边界
xinliqiu status                # 是否在录
xinliqiu inventory             # 存量统计
xinliqiu storage               # 占用与低空间阈值
xinliqiu record start | stop
xinliqiu voiceprint 30 --out ./captures
xinliqiu sync 0 1800000000000 --out ./captures
xinliqiu sync 0 1800000000000 --store cloudflare
```

凭据：配对后自动记住；也可用 `XINLIQIU_DEVICE_ID` 等环境变量或命令行参数覆盖。

---

## MCP

```bash
./mcp/run.sh      # 通过 stdio 提供 MCP 服务
```

```json
{
  "mcpServers": {
    "xinliqiu": {
      "command": "/absolute/path/to/xinliqiu-sdk/mcp/run.sh",
      "env": {
        "XINLIQIU_DEVICE_ID": "...",
        "XINLIQIU_PEER_ID_FILE": "/abs/peer-id.txt",
        "XINLIQIU_KEY_FILE": "/abs/key.json",
        "XINLIQIU_OWNER_KEY": "/abs/owner.pem"
      }
    }
  }
}
```

十一个工具：`capabilities`、`status`、`inventory`、`record_start`、`record_stop`、`collect_voiceprint`、`sync`、`task_status`、`pull_changes`、`digest`、`fetch_audio`（均带 `xinliqiu_` 前缀）。

**对 agent 的三个设计约束：**

1. **长操作用任务制。** `record_start`、`collect_voiceprint`、`sync` 立即返回 `task_id`，用 `task_status` 轮询。
2. **音频默认不解密。** 只有 `fetch_audio` 且显式提供私钥才产出明文。
3. **错误结构化。** `{ok: false, error: {type, message, retryable}}`，看 `retryable` 再决定要不要重试。

---

## 存储（可选）

不跨机器用就跳过这一节。

### 设备里能存多少

```python
>>> ball.storage_info()
{'item_count': 55, 'used_bytes': 1875120,
 'free_bytes': None, 'total_bytes': None, 'pressure': 'unknown', ...}
```

**设备只报用了多少，不报总共多少。** `free_bytes` 与 `total_bytes` 是 `None`，不是编造的数字。

但固件里的低空间阈值是明确的（静态常量）：

| 剩余空间 | 设备行为 |
|---|---|
| ≤ 50 MiB | 置位 `ALMOST_FULL`，发出警告 |
| ≤ 10 MiB | **开始删除最旧的一整天**，直到回到 50 MiB |
| < 5 MiB | 拒绝创建新文件，新录音直接丢失（状态仍显示在录） |

**多久该同步一次：** 音频码率 2,000 B/s（16 kbps），50 MiB 约等于 **7.3 小时**连续语音。
实际远远长于这个数，因为静音不生成文件。

**实践建议：每天同步一次。** 只要不积累到触发 10 MiB 阈值，就不会丢东西。

### 只存密文

只存密文：Worker 只做 API，R2 存加密字节，D1 存索引。

```
      你的数据（仍是密文）
              ▼
    ┌──────────────────┐
    │  Worker（仅 API） │
    └────┬────────┬────┘
         ▼        ▼
    ┌───────┐  ┌──────┐
    │  R2   │  │  D1  │
    │ 字节  │  │ 索引 │
    └───────┘  └──────┘
```

即使存储泄露，攻击者拿到的也只是用**你的**公钥加封的密文。

```bash
cd cloudflare
./setup.sh        # 建桶、建库、建表、生成 token
npm run deploy
```

```bash
export XINLIQIU_STORE=cloudflare
export XINLIQIU_STORE_URL=https://xinliqiu-store.<你>.workers.dev
export XINLIQIU_STORE_TOKEN=<脚本打印的 token>
```

**三种同步操作：**

| 操作 | 方向 | 需要私钥 |
|---|---|---|
| `sync_range` | 设备 → 你 | 否 |
| `push_range` | 你 → 存储 | 否 |
| `pull_changes` | 存储 → 你 | 否 |

> **游标用序号，不用时间戳。** 设备时钟会重复、重叠、回退；存储侧在写入时分配单调序号。

多端一致性用摘要比对，不用搬数据：`store.digest(device_id)`。

详细说明：[docs/sync.md](docs/sync.md)、[docs/storage.md](docs/storage.md)

---

## 数据格式

```
CMPT 容器
  "CMPT" | u16 条目数 | u16 数据偏移 | u32 声明长度
  条目表：每条 68 字节 = 64 字节名称 + u32 负载长度
  负载紧接其后，按表顺序排列

enpt 信封（音频条目内部）
  "enpt" | u16 密钥长 | u16 nonce长
         | RSA-2048 PKCS#1 v1.5 包裹的 32 字节 AES 密钥
         | 12 字节 nonce | u16 0x0118 | u8 密钥版本 | u8 保留
         | 密文 | 16 字节 GCM tag | u16 16 | u16 24 | "enpt"
```

明文是 **40 字节 / 20 毫秒一包**的 Opus，单声道 16 kHz。尾部没有样本计数字段。

---

## 安全

- 凭据与私钥由你提供，不离开进程
- 存储后端只接收密文
- 解密总是显式调用
- 无遥测、无统计、不回传

详见 [docs/security.md](docs/security.md)

---

## 常见问题

**为什么不直接用官方 App？**
官方 App 是正常路径。这个项目是给想自己掌控录音、不想走云端的人。

**能读别人的设备吗？**
不能。你需要那台设备的凭据和对应私钥。没有后门，以后也不会有。

**为什么标记重点被列为不可用？**
因为在我们测过的硬件上它没有可达的触发路径。如实列出来比含糊其辞有用。

**会刷写或修改我的设备吗？**
不会。SDK 里根本没有刷机、格式化、配对、安装密钥这类操作。这是刻意的。

**丢私钥会怎样？**
之前的录音就永久解不开了。这是加密在正常工作，没有恢复途径。

---

## 项目结构

```
xinliqiu-sdk/
├── python/aipin_sdk/   核心 SDK、CLI、MCP server
├── ts/                 TypeScript 封装
├── cloudflare/         Worker + R2 + D1 存储
├── mcp/run.sh          MCP 启动器
├── docs/               专项文档
├── examples/           示例
└── tests/              离线测试，不需要硬件
```

**文档：** [AGENTS.md](AGENTS.md)（给 agent 的接入说明）· [AGENT-SETUP.md](AGENT-SETUP.md)（逐步配置）· [docs/](docs/)（能力、安全、存储、同步）

---

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。两条最重要：**不提交厂商材料**、**不提交任何凭据**。

安全问题请走 [SECURITY.md](SECURITY.md)。

---

## 法律

非官方项目，与 OPPO 无关联。“OPPO”和“心力球”是其所有者的商标，此处仅作描述性使用。

本项目不绕过鉴权、不破解加密、不包含厂商二进制或逆向材料。**请在自己拥有的设备上、使用属于你自己的凭据和密钥。**

完整声明：**[LEGAL.md](LEGAL.md)**

---

<div align="center">

MIT · [English](README.en.md) · [LEGAL](LEGAL.md) · [SECURITY](SECURITY.md) · [CONTRIBUTING](CONTRIBUTING.md)

</div>
