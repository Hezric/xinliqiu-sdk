# 心力球 SDK

从你自己的代码访问 **OPPO AI 心力球**（型号 `CNMABWC301`）。

不需要 OPPO 手机，不需要 OPPO 账号，不需要 root，不需要任何外部二进制。

```bash
pip install "xinliqiu-sdk[all]"
```

---

## 能做什么

| 能力 | 说明 |
|---|---|
| 配对 | 与设备建立自己的凭据，占用两个槽位之一 |
| 状态 | 是否在录音、电量、错误码 |
| 库存 | 时间范围内的条数与字节数 |
| 录音控制 | 开始 / 停止 |
| 声纹采集 | 1–127 秒的单次采集 |
| 同步 | 把录音容器取回本地 |
| 解密 | 用你持有的私钥解出 Opus |

做不到的：高亮标记、手动归档、AI 键对话模式。这几项在固件里没有可达触发点。

---

## 快速开始

```bash
xinliqiu scan                                  # 找到设备 id
xinliqiu pair --device <id> --out-dir ./creds  # 配对，自动记住凭据
xinliqiu status                                # 应能正常返回
xinliqiu sync --start-ms 0 --out-dir ./captures
```

配对约两分钟，不会格式化、不会刷写任何东西。

### 代码里用

```python
from pathlib import Path
from aipin_sdk import Xinliqiu, Credentials, container

ball = Xinliqiu(Credentials(
    device_id="...",
    peer_id_file=Path("./creds/peer-id.txt"),
    key_file=Path("./creds/pairing.json"),
))

print(ball.status())          # recording / battery / error
print(ball.storage_info())    # 用量与阈值

got = ball.sync_range(0, now, output_dir=Path("./captures"))
parsed = container.parse_cmpt_file(Path(got["files"][0]))
plain = container.decrypt_enpt(parsed.by_kind("audio")[0].payload, Path("./owner.pem"))
```

---

## 关于加密

设备用**主人装进设备的公钥**加密录音，只有对应私钥能读回。

- **私钥丢了，之后录的也读不了。** 不是“旧的读不了”，是设备会继续用那把已
  失效的公钥加密新录音。没有重置，没有找回。
- **手机 App 配过的设备，旧录音永远读不出来。** 那把私钥在 OPPO 手里。SDK
  能配对、能取回密文，但解不开；能解开的是你自己 `init` 之后的录音。

---

## 两个槽位

固件只有两个凭据槽位，按最近使用淘汰。手机和这台电脑可以共存，但最多两台。

再次配对时 SDK 复用第一次的 alias，设备认出同一 alias 就**原地更新**，不走
淘汰，所以不会挤掉手机。

> 快速反复配对时设备会间歇性拒绝新凭据。等几秒再配一次即可，
> 见 [docs/device-state-notes.md](docs/device-state-notes.md)。

---

## 存储

`storage_info()` 只报用量，`free_bytes` 和 `total_bytes` 是 `None`——固件不暴露容量。

要紧的是几个固定阈值：剩余 50 MiB 告警，10 MiB 时设备**自行删除最旧的一整天**，
低于 5 MiB 不再创建新分段。音频约 2,000 B/s，50 MiB 差不多 7.3 小时连续说话。

**每天同步一次。**

---

## 协议

自己实现，运行在 `bleak` 上：

```
UKEY2 握手 → HMAC-SHA512 挑战应答 → EPI 协商
  → 能力查询 → 服务连接 → Agent 认证 → 绑定
  → AES-128-GCM 业务信道 → SystemFT 文件流
```

分层说明见 `python/aipin_sdk/protocol/`。

---

## 文档

| 文件 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 给 agent 的完整说明 |
| [AGENT-SETUP.md](AGENT-SETUP.md) | 从零配对的详细步骤 |
| [docs/device-state-notes.md](docs/device-state-notes.md) | 设备状态与重试节奏 |
| [docs/storage.md](docs/storage.md) | 自建密文存储 |
| [SECURITY.md](SECURITY.md) | 安全边界 |
| [LEGAL.md](LEGAL.md) | 范围与商标声明 |

---

MIT · 与 OPPO 无关，非官方项目