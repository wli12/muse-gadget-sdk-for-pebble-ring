<!--
Copyright (c) Meta Platforms, Inc. and affiliates.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Pebble Index 01 → Google Cloud Run → Muse

[English](README.md) | 简体中文

把 Pebble Index 01 的转写和录音通过自己的 Google Cloud Run 发送到 Meta 的 Muse 主聊天。本指南涵盖准备条件、配对、部署、手机设置、代码来源、验证和运行维护。部署项目、运行账户和 Webhook 地址均为占位符；公开源码使用实际 GitHub 地址。仓库不包含真实设备凭证、密钥、聊天或录音。

**当前状态（2026-11-17）：** Cloud Run 健康、签名与鉴权检查通过；Muse Link 的 WebSocket 握手仍返回 502，在发送前失败，最新真实投递尚未通过。2026-11-16 的成功验收属于历史记录，详见第 1、11 节。

## 0. 开始前需要什么

| 准备项 | 具体要求与官方入口 |
| --- | --- |
| 戒指 | 一枚 [Pebble Index 01](https://repebble.com/products/pebble-index-01)，先按[官方入门指南](https://help.repebble.com/en/articles/15434751-index-01-getting-started-guide)与手机配对。无需购买 Pebble 手表。 |
| 手机 | **iPhone 或 Android 都可以，不限于 Google Pixel。** iPhone 的整套路径当前要求 **iOS 18 或更高版本**，这是 Muse 的要求；Android 可使用能够安装下列两款 App 的手机，包括兼容的 Pixel 型号。Android 的实际最低系统版本以两款 App 的商店兼容性为准，不能把 Pebble 单独要求的 Android 8.0 当作整套方案的最低版本。 |
| 手机 App、Muse 账户与 token | 安装 [Pebble App](https://repebble.com/app) 和 Meta 的 Muse（[iPhone](https://apps.apple.com/us/app/muse-from-meta/id6760173601)、[Android](https://play.google.com/store/apps/details?id=com.facebook.aura)）。登录自己的 Muse 账户，申请 [Muse Gadget SDK token](https://gadgets.muse.ai/settings/sdk-tokens)，并阅读 [SDK 条款](https://gadgets.muse.ai/sdk-terms)。Pebble 应已能接收、转写戒指录音并提供签名 Webhook 设置。 |
| Google Cloud | 一个 Google 账户、可用的 Cloud Billing 结算账户，以及关联该结算账户的 Google Cloud 项目；下面有注册步骤。已有符合条件的账户和项目可复用，独立项目更容易管理和清理。 |
| 首次设置用的电脑 | Python 3.12、Git 和 [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)。Muse 设备首次配对还需要带 BLE 的 Linux 主机，或使用本项目实验性适配器的 Mac；两种配对方式选一种。之后可用本机或 [Cloud Shell](https://docs.cloud.google.com/shell/docs/launching-cloud-shell) 执行部署。 |
| 日常连接 | 戒指通过手机蓝牙传输录音；手机需要联网，才能将 Webhook 发到 Google Cloud。只在首次设置时使用的电脑不必常开，Cloud Run 不需要蓝牙。 |

“蓝牙只用于一次配对”指 **Muse 手机 App 与 Mac/Linux 设备身份的设置**；日常戒指到手机的蓝牙传输仍然需要。Apple Watch 是可选设备，不是前置条件；回答同步或通知仍需单独验证。

### 注册 Google Cloud 并准备项目

1. 打开 [Google Cloud 官方免费入口](https://cloud.google.com/free)，用已有 Google 账户登录，或按页面创建 Google 账户。符合条件的新用户可以选择官方免费试用；额度、期限和资格以该页面为准。
2. 按 Google 注册页面填写实际地区及所需信息，完成身份和付款方式验证。**即使用免费额度，项目仍需要有效的结算账户。** 详见[结算账户说明](https://docs.cloud.google.com/billing/docs/how-to/create-billing-account)与[免费试用 FAQ](https://cloud.google.com/signup-faqs)。
3. 进入 [Google Cloud Console](https://console.cloud.google.com/)，打开项目选择器，选择 **New project / 新建项目**，创建本方案专用项目；详见[创建项目](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects)。记下 **Project ID**，后面填入 `PROJECT_ID`；它不是项目显示名称或项目数字编号。
4. 在项目的 **Billing / 结算** 页面，确认项目已关联可用结算账户；如未关联，按[关联结算账户说明](https://docs.cloud.google.com/billing/docs/how-to/modify-project)操作。项目与结算账户是两个不同概念。
5. 在 Console 右上角启动 **Cloud Shell**（已预装 `gcloud`），或在本机安装 Google Cloud CLI。后面的第 4 节会执行 `gcloud auth login` 并选择项目；BLE 配对仍在本地 Mac/Linux 完成。
6. 可为该项目设置[预算提醒](https://docs.cloud.google.com/billing/docs/how-to/budgets)。提醒型预算不会自动停止消费。少量使用可能落入免费额度，但不能保证永久零账单；试用不会自动升级成付费账户，试用到期后资源可能停止，是否升级由账户所有者决定。费用与资源限制见第 3 节。

## 1. 最终行为与验证范围

最终链路是：

```text
Index 01 戒指
    │ 蓝牙
    ▼
手机 Pebble App：根据所选手势发送转写和录音 Webhook
    │ HTTPS + Bearer + Pebble v1 HMAC 签名
    ▼
Google Cloud Run：校验 → 排除测试事件 → 本地音频转换 / 文本直通 → 去重
    │                         │
    │                         └─ 私有 Cloud Storage：设备状态、租约、投递记录
    ▼
Meta Muse：同一账户的主聊天 → Muse 的回答显示在 App 中
```

**手机实际发送到这个 Webhook 的每条有效录音或真实转写，都会进入同一个 Muse 主聊天。** 当前推荐手机选择 **Both**，同时保留 Pebble 的转写和录音。[Pebble 1.18.0 的发送实现](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/service/recordings/button/IndexWebhookUploadRecordingOperation.kt)会在 Both 模式等待转写完成后上传两者；Google 用本地 FFmpeg 把 M4A 转成官方 SDK 接受的 WAV。这里的“原始录音”指保留语音内容，音频容器和采样格式仍会转换。

如果载荷同时有音频和转写，调用官方 `send_voice(wav, message=transcription, output_modality='text')`，在**同一条消息**里提交两者；不另发第二条文字消息，也不添加指令包装。Recording 模式只发语音，Transcription 模式只发文字。文字仅做 UTF-8 解码和首尾空白清理。音频无效时明确报错，不悄悄改用同载荷中的转写。不要求说“Muse”，不删除“Hey Muse”，不按内容分类、提取指令或决定聊天去向。没有内容过滤，不代表跳过鉴权、格式、大小或时间戳检查。

Muse App 只显示文字，不能据此判断录音是否送达、保留或提供播放控件；SDK 提交确认也不能证明 App 的展示方式。桥接负责把两者一并提交，不保证 Muse 如何权衡转写与语音。早先录音优先版收到 Both 时只提交音频、未使用 Pebble 转写；本版本已移除这一行为。

Pebble 的 **Send test event** 是连接测试，明确不发给 Muse。戒指的其他手势如果仍走 Index Agent，就继续由 Index Agent 处理。当前配置保留 **Hold & talk → Index Agent**、**Double click & hold → Webhook only**；只有后者产生的 Webhook 进入这条链路。想让其他手势也进入 Muse，才需要在手机里调整对应手势。

验证记录：实物链路验收截至 **2026-11-16**，实现源码检查与独立审查截至 **2026-11-17**；下表将历史成功验收与当前运行状态分开记录。

| 项目 | 结果与含义 |
| --- | --- |
| 离线测试 | 本轮收集 117 项，包含官方原示例兼容性测试；Mac 上 112 项通过，5 项真实 FFmpeg 检查因未安装解码器跳过，镜像构建中执行；覆盖签名、无前缀文本、Both、去重、令牌轮换／撤销及 Mac 蓝牙批次 |
| 已部署云端的受控真实消息 | 无“Muse”前缀的“请只回复：无需口令直通成功”收到 SDK 投递确认，HTTP 200 |
| 同一 delivery ID 再次请求 | HTTP 200、`duplicate: true`；没有再次提交聊天消息 |
| SDK 令牌刷新和私有状态保存 | 云端链路已验证；刷新后的状态成为后续请求的依据 |
| 戒指实物文字链路 | **用户已确认真实戒指消息和 Muse 回答都出现**；旧版的前缀限制已移除 |
| 新录音链路 | 音频/SDK 离线测试及 Linux 镜像内真实 WAV、AAC/M4A、超长拒绝、时间戳间隙保留检查已通过；已部署服务用短合成英语 M4A、有效签名取得语音提交 ACK 200，同 ID 再投返回 duplicate 200，未附加 Pebble 转写 |
| 录音端到端验收 | **用户已确认 Recording 模式的真实戒指语音与 Muse 回答都收到**；用户比较后认为 Muse 中文识别不如 Pebble，选择恢复 Both |
| Both 同条提交验收 | 合成 M4A 与文字取得同条提交 ACK 200，重复同 ID 返回 duplicate 200；离线测试验证官方 SDK 请求含文字项和完整音频项。用户随后确认真实戒指 Recent runs 为 recording + transcription，Muse 出现 Pebble 原文、完整可播放录音及回答；还出现额外的识别文字，不能保证 Muse 优先采用哪一份输入 |
| 2026-11-17 最新云端复验 | `/health` 200、签名连接测试 200、错误鉴权 401；Muse Link 的 WebSocket 握手仍返回 502，中转返回 503，记录为 `failed_before_send`。新旧运行版本均复现；当前未获得新的投递确认 |
| 长时间运行、所有冷启动与网络故障组合 | 尚未全面实测；不能据此承诺永不掉线或零丢失 |
| Apple Watch | [官方 Muse App 页面](https://apps.apple.com/us/app/muse-from-meta/id6760173601)列出 watchOS 10.0+，手表已安装官方 Muse；SDK 触发的回答是否同步或推送到 Watch **尚未验证** |

SDK 的 `acknowledged` 表示消息提交得到确认，不能等同于回答已生成、任务已完成，或 Watch 收到了通知。回答是否按预期执行，要在 Muse 中核对。

## 2. 部署位置、文件来源与官方示例的区别

### 代码实际在哪里运行

| 内容 | 运行或保存位置 | 用途 |
| --- | --- | --- |
| 共享的 `pebble_ring_bridge.py` 与 `app.py`、`muse_sender.py`、`audio.py` | 用户自己的 Google Cloud Run，`us-central1` | 在线接收 Webhook 并提交给 Muse；空闲时可缩到零 |
| 配对状态、Webhook 密钥、租约、投递记录 | 同一项目的私有 Cloud Storage 桶 | 保存设备身份和去重状态；录音、转写正文不写入桶 |
| `mac_pairing.py` | 带蓝牙的用户 Mac | 仅在首次配对时运行，完成后退出 |
| `deploy.sh`、`verify_webhook.py`、测试 | 用户 Mac、Cloud Shell 或构建环境 | 部署和验证，不是常驻中转进程 |
| fork、分支、PR、双语 README | GitHub | 保存公开源码、文档和审阅记录；GitHub 本身不接收戒指 Webhook |
| Muse 的识别与任务执行 | Meta 的 Muse 服务 | 处理文字、录音并产生回答 |

本方案没有在 Codex/OpenAI 的机器上托管中转服务。Codex 用于协助编辑工作区、调用部署和检查工具。Mac 保存源码及本地工具；正常使用时，戒指通过手机直接访问 Google Cloud Run，不要求 Mac 开机。GitHub 上传、PR 合并与 Google Cloud 部署是三个独立动作，本项目没有配置“合并即部署”。

### 哪些来自官方，哪些是新增

本项目 fork 自 [Meta 官方 Muse Gadget SDK](https://github.com/facebookincubator/muse-gadget-sdk)。完整 SDK 介绍见[原版 README](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/README.md)。

原示例 [`linux/examples/pebble_ring_bridge.py`](linux/examples/pebble_ring_bridge.py) 是文本 Webhook → `musegadget send-user-msg` → 已运行的 Linux SDK 服务和 Unix socket。本轮对它只做小范围提取：把原来的 MIME 解析与字段读取集中成共享函数 `multipart_fields()`，原 `parse_body()` 改为调用该函数；原本地 HTTP 服务、CLI 发送、JSON／纯文本输入和默认文字解码行为保留。

Cloud Run 的 `app.py` **直接导入这个原示例模块**，复用共享的 multipart 字段读取、`transcription()` 和 `presented_token()`，不再维护第二份 MIME 解析引擎。 导入不会执行原文件的 `main()`，也不会另外启动本地 HTTP 服务；这一点有专门测试。云端只在共享结果上增加录音二进制处理、严格字段检查、Pebble v1 签名、GCS 状态和去重。令牌提取只传入 `Authorization`，并先检查 `Bearer ` 前缀，不把原本地示例支持的其他令牌来源带到公网入口。

| 官方实现 | Cloud Run 如何复用 | 必需的新增逻辑 |
| --- | --- | --- |
| 原 `parse_body()` 中的 MIME 解析和字段读取 | 提取后由原 `parse_body()` 与 `app.py` 共同调用 | 录音字节保留、字段数量／重复检查、严格 UTF-8 |
| `transcription()` | `app.py` 直接调用 | 文字长度与有无录音的校验 |
| `presented_token()` | `app.py` 直接调用，并限定为 Authorization | Bearer 格式、签名、时间戳和事件协议校验 |
| `send_user_msg()` | 继续服务于原 Linux 示例；Cloud Run 复用其下面的官方 SDK | 请求期间建立连接、保存轮换凭证和禁止不确定投递重发 |
| SDK 的配对、TLS、Noise、消息协议 | 直接调用未修改的 `musegadget` SDK | Mac 仅适配平台蓝牙；音频转换满足官方 WAV 接口 |

`linux/src/` 和 `protocols/` 没有修改。`linux/examples/pebble_ring_bridge_cloudrun/` 是云端适配与工具，不是复制后的第二套本地示例。共享示例模块的新增测试是 [`linux/examples/pebble_ring_bridge_test.py`](linux/examples/pebble_ring_bridge_test.py)；官方既有 `linux/tests/test_pebble_ring_bridge.py` 保留原样并一起运行，确认旧的本地文字行为兼容。

| 新增文件 | 职责与必要性 | 对应测试 |
| --- | --- | --- |
| `app.py` | HTTP 入口、签名、multipart、私有状态、跨实例租约、投递去重 | `app_test.py` |
| `muse_sender.py` | 在单次请求内运行官方 SDK；保存轮换令牌；提交到 Muse 主聊天 | `muse_sender_test.py` |
| `audio.py` | M4A/WAV 本地转为有大小和时长边界的 WAV | `audio_test.py`，包含 mock 边界和真实 FFmpeg 检查 |
| `verify_webhook.py` | 管理员主动运行的连通性检查；显式选择时发送一次无害文字测试 | `verify_webhook_test.py` |
| `mac_pairing.py` | 可选的一次性 CoreBluetooth 适配器，复用官方配对流程 | `mac_pairing_test.py` |
| `Dockerfile`、`requirements.txt` | 云端镜像与直接 Python 依赖；镜像最终层不包含测试工具 | 镜像构建运行音频测试和非 root 健康检查 |
| `deploy.sh` | 显式分阶段创建资源、构建、上传初次配对状态、部署和发布 | `bash -n`、只读 `plan`、实际构建与部署验证 |
| `requirements-macos.txt` | 仅 Mac 配对需要的 PyObjC 依赖，不进入云端镜像 | Mac 工具验证 |
| `README.md`、`.gitignore` | 简短入口、文件职责和防止误提交私人文件 | 文档链接与发布内容检查 |

表中云端目录内的路径均相对于 `linux/examples/pebble_ring_bridge_cloudrun/`。本次新增 Python 模块与其 `模块名_test.py` 一一对应；既有官方测试保留原文件名。测试文件不会运行在已部署的服务中；真实编解码检查只在构建阶段运行。

### 相对原始 Muse 官方示例增加或改变的功能

| 行为 | 原官方示例 | 本方案 |
| --- | --- | --- |
| 主机和连接 | Linux 常驻服务，依赖本机 SDK socket | Cloud Run 按请求连接官方 SDK；无常开 Mac、VPN 或隧道 |
| 聊天目标 | 必须指定 side chat；追加 `[via Pebble Ring]` 文本后缀 | 固定 Muse 主聊天；转写仅清理首尾空白，不加后缀、暗号或路由 |
| 输入 | 提取文字，不把录音作为语音提交 | Both 同条提交转写和录音；兼容仅文字、仅录音 |
| 音频 | 没有音频转换 | 本地 FFmpeg 转 WAV，超限整条拒绝，不截断后执行 |
| Webhook 协议 | `/ingest`；multipart、JSON 或纯文本；共享令牌 | `/index`；Pebble v1 multipart、Bearer、HMAC、时间戳窗口 |
| 测试事件 | 没有 Pebble v1 测试事件的专门处理 | 验签后只返回连通结果，不向 Muse 下达测试任务 |
| 云端状态 | 交给本地常驻 SDK 服务 | 私有 GCS 保存轮换状态、CAS 租约与投递记录 |
| 重复与不确定投递 | 没有持久投递记录 | 同一 ID 去重；已尝试但确认不明时不自动重发 |
| 主机命令权限 | 依赖已安装的通用 SDK 服务 | 中转不注册 shell、文件读写或其他主机命令 |
| 部署、验证、复现 | 一个本地监听示例 | 增加容器、最小权限部署、对应模块测试及本指南 |

共享 MIME 解析的严格检查仅由 Cloud Run 启用；原本地入口保持默认兼容模式。

本方案**没有**增加语音识别引擎、内容分类、关键词过滤或 Apple Watch 专用服务。Muse 对录音的再次识别和 Watch 展示由 Meta 的应用与服务决定。

运行镜像固定官方 SDK 提交：

```text
08f89ed329918c27d53b11192065ad6b2cb0e868
```

容器从 `facebookincubator/muse-gadget-sdk` 下载该提交以安装未修改的 SDK 包。共享示例模块不在 pip 包内，所以构建会另外复制本 fork 的 `pebble_ring_bridge.py`，并在测试阶段验证后带入最终镜像；不会误用缺少共享入口的原版脚本。直接 Python 依赖固定版本；传递依赖、`python:3.12-slim` 镜像 digest 仍可能变化，因此不保证重复构建的镜像逐字节一致。需要保留完全相同的产物时，记录并保留镜像 digest。源码位于原仓库的 [公开 fork](https://github.com/wli12/muse-gadget-sdk-for-pebble-ring)，功能分支为 `pebble-cloudrun-pass-through`。

## 3. 隐私、权限和费用

这次采用用户自己的 Google Cloud 项目。Google Cloud Run 会处理录音或转写，私有 Cloud Storage 会保存 Muse 的设备身份、访问/刷新令牌、SDK token 和 Webhook 密钥；Muse 接收对应语音笔记或文字并执行任务。这是使用 Google 的云端方案。手机原有的录音、转写环节仍受 Pebble 自身实现和设置影响。

推荐 **Both** 载荷；仅录音、仅转写仍兼容。录音只在请求内存和 `0700` 临时目录中的 `0600` 文件中转换，使用后清理，不写入 GCS 投递记录。桥接不将录音、笔记正文或 Muse 聊天内容写入日志，关闭普通 HTTP 访问日志和 SDK 内容日志；Google 平台仍会记录请求元数据、构建与服务运行信息。投递记录只留所提交内容的 SHA-256 摘要，不应将摘要当成匿名化保证。桥接未实现回答播放或实时语音通话。

转换上限为 **8 MiB 输入、60 秒、PCM16LE / 单声道 / 16 kHz WAV**，满足官方 SDK 的 **2 MiB WAV** 上限；整个 HTTP body 上限 9 MiB，留出 multipart 开销。超限整条拒绝，不截掉后半句再执行。FFmpeg 只接受选定的 MOV/WAV demuxer 和本地 file/pipe 协议，禁用 MOV 外部数据引用，15 秒超时，输出按解码后的字节数限制。

关键权限与隔离：

- 状态桶和构建桶分别创建，启用 uniform bucket-level access 和 public access prevention。
- 运行服务账号只在专用状态桶拥有 `roles/storage.objectUser`；不创建或下载服务账号 JSON 私钥。
- 构建服务账号只读取构建桶，在专用镜像仓库写入，并具有日志写入权限；脚本不给它状态桶权限。
- 项目管理员仍可能读取状态，组织已有的继承权限也仍然有效；专用服务账号不能限制项目所有者。
- 构建上下文只含云端模块、音频测试、共享的 `pebble_ring_bridge.py` 及其测试、依赖清单和 Dockerfile；配对文件、密钥和设置页不会进入镜像。
- 云端桥接不向 Muse 注册 shell、文件读写或其他主机命令。用于一次配对的标准 Linux 安装器则会安装通用命令服务，因此下面使用单独账户，并在迁移前停止服务。

Cloud Run 配置为 **us-central1、1 vCPU、1 GiB、按请求分配 CPU、min 0、max 1、concurrency 1、120 秒请求超时**。服务和修订版都设置实例上限；跨修订版的设备互斥仍依靠 GCS 租约。空闲可缩到零，适合少量戒指笔记。

**低用量有机会落在免费额度内，不能保证每月零费用。** Cloud Run 免费额度按结算账户汇总；存储、存储操作、网络、构建、镜像和日志另有计费规则。启用结算不等于会收费，免费额度和预算提醒也不等于硬性费用上限。部署前按自己的使用量查看 [Cloud Run 定价](https://cloud.google.com/run/pricing)、[Cloud Storage 定价](https://cloud.google.com/storage/pricing)、[Cloud Build 定价](https://cloud.google.com/build/pricing) 和 [Artifact Registry 定价](https://cloud.google.com/artifact-registry/pricing)，设置合适的预算提醒。

脚本让构建源文件在一天后过期，镜像采用“旧于七天清理、至少保留最新一个版本”的策略。状态桶关闭版本保留和 soft delete，避免旧刷新令牌长期保留；这也意味着不能依靠桶的历史版本找回误删状态。投递记录没有自动清理，删除它会结束对应 delivery ID 的去重保护。

## 4. 准备代码和工具

需要：支持 Index 转写和签名 Webhook 的 Pebble App、Muse 账户和 SDK token、启用结算的 Google Cloud 项目、Python 3.12、Git、已登录的 Google Cloud CLI。一次配对可选带 BLE 的 Linux 主机，或下面的 macOS 适配器；**只选一种配对方式即可**。云端不需要另外租一台一直运行的 VPS。

可直接克隆包含本功能分支的公开 fork。若要使用自己的 fork，先确认其中保留了 `pebble-cloudrun-pass-through` 分支，再替换 `GITHUB_REPO`；只复制官方 `main` 分支不会带入本方案。Google Cloud 项目和部署账户仍应使用自己的值：

```sh
GITHUB_REPO='wli12/muse-gadget-sdk-for-pebble-ring'
git clone "https://github.com/${GITHUB_REPO}.git" muse-ring-sdk
cd muse-ring-sdk
git switch pebble-cloudrun-pass-through
REPO_DIR="$PWD"
BRIDGE_DIR="$REPO_DIR/linux/examples/pebble_ring_bridge_cloudrun"

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r "$BRIDGE_DIR/requirements.txt" pytest
python -m pip install ./linux

PYTHONPATH="$REPO_DIR/linux/src:$REPO_DIR/linux/examples:$BRIDGE_DIR" \
  python -m pytest -q \
  "$REPO_DIR/linux/examples/pebble_ring_bridge_test.py" \
  "$REPO_DIR/linux/tests/test_pebble_ring_bridge.py" "$BRIDGE_DIR"
bash -n "$BRIDGE_DIR/deploy.sh"
```

无需在本机安装 Docker：镜像由 Cloud Build 构建。CLI 可按 [Google 官方安装说明](https://cloud.google.com/sdk/docs/install) 安装，再执行：

```sh
gcloud auth login
PROJECT_ID='your-project-id'
gcloud config set project "$PROJECT_ID"

PRIVATE_WORK_DIR="$HOME/.local/state/muse-ring-bridge"
mkdir -p "$PRIVATE_WORK_DIR"
chmod 700 "$PRIVATE_WORK_DIR"
export BRIDGE_DEPLOY_DATA="$PRIVATE_WORK_DIR/deploy/$PROJECT_ID"
```

`PRIVATE_WORK_DIR` 在 Git 仓库外。配对目录设为 `0700`，密钥文件设为 `0600`。后面的命令默认沿用同一终端中的这些变量；重新开终端时需重新设置变量并激活虚拟环境。

如果使用 Google Cloud Shell，代码、测试和部署命令相同；先确认项目与 Google 的授权弹窗。BLE 配对仍发生在本地 Mac/Linux 上，配对文件进入 Cloud Shell 本身也是向 Google 转移凭证，应该在已选择该方案之后才上传。不要使用公开文件链接或聊天附件转移这些文件。

## 5. 一次性配对：优先官方 Linux 路径

完整协议说明见 [Linux SDK README](linux/README.md#set-it-up-with-muse)。使用支持 BlueZ/BLE 的 Debian、Ubuntu 或树莓派系统；普通云端主机没有可供手机配对的本地蓝牙。以下命令在**该 Linux 主机**上的同一代码仓库执行。

先在 [Muse SDK tokens](https://gadgets.muse.ai/settings/sdk-tokens) 创建 SDK token，阅读对应条款。推荐给临时配对服务单独创建一个无 sudo 权限的账户。下面的账户名只是本方案约定；如果已存在合适账户，使用该账户代替：

```sh
sudo useradd --create-home --shell /usr/sbin/nologin musepair
bash linux/install.sh --from "$PWD/linux" --run-as musepair
```

安装器会隐藏输入地询问 SDK token；不要把真实 token 填入命令行参数或 Git 文件。安装器会修改这台 Linux 主机的系统依赖、蓝牙配置和 systemd 服务，按它显示的内容检查后继续。配对窗口打开后，在手机 Muse App 中：

1. 打开 **Settings → Devices → Developer mode**。
2. **Add Device / +**，选择安装器显示的 `MuseGadgetXXXXXX`。
3. 确认这是自己正在配对的社区设备。
4. 如询问 Wi-Fi，选择主机已连接的网络，按官方说明完成，无需另填网络密码。

配对窗口约十分钟，超时可运行 `sudo musegadget pair` 重新打开；“十分钟”是窗口长度，不是永久在线的保证。使用自己信任的配对环境。成功后，停止本地服务，再导出三个文件供上传：

```sh
sudo systemctl disable --now musegadget
PAIRING_DIR="$PRIVATE_WORK_DIR/muse-state"
mkdir -p "$PAIRING_DIR"
chmod 700 "$PAIRING_DIR"
for filename in identity.json pairing.json sdk_token; do
  sudo install -m 600 -o "$(id -u)" -g "$(id -g)" \
    "/var/lib/musegadget/$filename" "$PAIRING_DIR/$filename"
done
```

如果部署在另一台机器，安全转移整个目录，保持权限，并在部署端设置相应的绝对路径。三个文件属于同一个设备身份，不能混用。不要在云端开始刷新令牌后重新启动这个本地身份；本地副本届时可能已经过期。

## 6. 可选：使用 Mac 配对

本次实际使用的适配器是社区实验性工具，**不是 Meta 官方支持 macOS 的声明**。它只替换蓝牙平台适配层，复用官方配对流程；不启动通用主机命令服务。需要 macOS 蓝牙、Python 3.12 和 PyObjC wheel。

在前面创建的虚拟环境中执行：

```sh
python -m pip install --only-binary=:all: \
  -r "$BRIDGE_DIR/requirements-macos.txt"

PAIRING_DIR="$PRIVATE_WORK_DIR/muse-state"
python "$BRIDGE_DIR/mac_pairing.py" --state-dir "$PAIRING_DIR"
```

SDK token 通过隐藏输入提示提供，程序不接受命令行 token。macOS 如要求蓝牙权限，允许运行程序所用的 Terminal/Python，然后在 Muse App 按上一节相同路径添加显示的设备。工具会拒绝覆盖已经完成的配对；不要为了解决云端临时错误就删除有效身份重新配对。

配对完成后结束该工具，确认本地没有用同一身份运行的 SDK 服务，再上传。**iPhone 接 Mac 的 USB 线不是中转链路**；如果系统显示普通的“信任此电脑”，那是 USB 设备访问授权，与 Muse BLE 配对、戒指 Webhook 是不同环节。正常使用不需要保持 USB 或 Mac 在线。

## 7. Google Cloud 分阶段部署

使用单独的新项目或确认这些专用资源名称没有被其他应用使用。脚本会在专用桶上调整版本、soft delete 和 IAM 设置，不能把已有通用桶换名接入后盲目执行。

```sh
bash "$BRIDGE_DIR/deploy.sh" plan "$PROJECT_ID"
```

`plan` 只显示本地计划，不调用云端、不读取凭证。项目参数决定以下确切资源：

| 资源 | 名称 |
| --- | --- |
| 区域 | `us-central1` |
| Cloud Run 服务 | `muse-ring-bridge` |
| 私有状态桶 | `${PROJECT_ID}-muse-ring-state` |
| 构建源文件桶 | `${PROJECT_ID}-muse-ring-build` |
| Artifact Registry 仓库 | `muse-ring` |
| 运行服务账号 | `muse-ring-runtime@${PROJECT_ID}.iam.gserviceaccount.com` |
| 构建服务账号 | `muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com` |

检查计划后，创建资源并构建。执行账户需要相应的 API、资源创建和服务账号使用权限；示例不是给日常运行账号授予项目 Owner：

```sh
bash "$BRIDGE_DIR/deploy.sh" bootstrap "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" build "$PROJECT_ID"
```

接下来执行 `upload-pairing`，将设备访问/刷新令牌、身份、SDK token 和新生成的 Webhook 密钥上传到上述 Google 私有桶。先确认目标项目；此命令会实际传输凭证。配对文件不会进入构建：

```sh
bash "$BRIDGE_DIR/deploy.sh" upload-pairing "$PROJECT_ID" "$PAIRING_DIR"
bash "$BRIDGE_DIR/deploy.sh" deploy-private "$PROJECT_ID"
```

`upload-pairing` 自动生成强随机 Bearer 和签名密钥，本地副本位于：

```sh
KEYS_FILE="$BRIDGE_DEPLOY_DATA/webhook.json"
```

云端两个对象是 `state/muse.json` 和 `state/webhook.json`，上传使用“必须不存在”的 generation 前置条件，不能覆盖已运行身份的最新刷新令牌。重复运行上传遇到对象已存在，应先检查状态，不能删掉有效对象后重试。

如果初次上传的 `muse.json` 已成功、第二个 `webhook.json` 上传失败，完整重跑会在已存在的第一个对象处停止。先核对这确实是**同一次初始设置**、有效设备状态已上传，并只检查对象元数据：

```sh
gcloud storage objects describe \
  "gs://${PROJECT_ID}-muse-ring-state/state/muse.json" \
  --project="$PROJECT_ID" --format='yaml(name,generation,timeCreated)'
gcloud storage objects describe \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --format='yaml(name,generation)'
```

只有第二条明确返回 **404 Not Found**，且本地 `KEYS_FILE` 是该次设置原本生成的密钥文件，才单独补传缺失对象。403 或网络错误不代表对象不存在：

```sh
gcloud storage cp "$KEYS_FILE" \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --if-generation-match=0
```

如果两对象都已存在，确认本次设置后直接继续私有部署。恢复过程**不删除、不覆盖 `state/muse.json`**，不重新上传旧令牌；`--if-generation-match=0` 也防止覆盖同时创建的密钥对象。

`deploy-private` 创建需要 Google IAM 登录的服务。检查私有部署成功后，选择让手机能访问公网 HTTPS 地址：

```sh
bash "$BRIDGE_DIR/deploy.sh" publish "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" status "$PROJECT_ID"

SERVICE_URL=$(gcloud run services describe muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 --format='value(status.url)')
curl --fail --show-error --silent "$SERVICE_URL/health"
```

`publish` 给 `allUsers` 授予这个服务的调用权限；公网入口仍要求应用自己的 Bearer 和有效签名，普通访问者不能发聊天消息。`GET /health` 仅返回静态健康状态，不触碰设备凭证或测试 Muse 网络。组织策略如禁止公开 Cloud Run，需由项目管理员选择组织允许的架构，不能关闭组织保护来绕过。

部署时唯一必需的运行环境变量是 `BRIDGE_BUCKET`；密钥从私有桶加载，不放在命令参数、镜像或 Cloud Run 环境变量中。所有消息进入 Muse 主聊天，不提供聊天选择或内容路由选项。

## 8. 手机设置与实际戒指测试

需要 URL：`$SERVICE_URL/index`，以及私有 `KEYS_FILE` 中的 `bearer` 和 `signing_secret`。在本机文本编辑器中打开该文件，复制到手机对应字段；不要把文件、密钥或包含密钥的截图上传到 GitHub 或聊天。手机配置步骤直接写在下方，不需要额外的配置页面生成器。

Pebble **1.18.0 iPhone** 的路径如下，见 [官方 Index 设置源码](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/ui/screens/settings/IndexSettings.kt) 和 [官方按钮/Webhook 设置源码](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/ui/screens/settings/ButtonSwitchboard.kt)：

1. 底部 **Index**；如底部没有 Index，先 **More → Index**。
2. Index 页面右上角齿轮 → **Settings → Ring Button**。
3. 保留 **Hold & talk → Index Agent**；选择 **Double click & hold → Webhook only**。
4. 打开这个手势下的 **Webhook settings**，页面标题应对应 **Webhook · Double click & hold**。
5. URL 填自己的 HTTPS `/index` 地址。新增 Header：名称 `Authorization`，值是 `Bearer ` 加上本地 `bearer`，中间有一个空格。
6. 启用 **Sign requests**，填本地 `signing_secret`。
7. Payload 选 **Both** 并保存，等待 Pebble 转写完成后同时发送文字和录音。桥接在一条 Muse 消息中提交两者。若选择 **Recording**，会在 Pebble ASR 前只发送语音；**Transcription** 则只发送文字。
8. 点 **Send test event**。成功只说明 Webhook 连通、鉴权通过，不应在 Muse 里新增测试聊天。
9. 使用 **Double click & hold**：先按一下并松开，随即再次按下并保持，讲话后松开。实际说：“请只回复戒指连接成功。”在 Pebble 核对转写和录音，再在 Muse 主聊天确认消息及回答。App 是否显示音频播放控件不属于桥接能保证的展示行为；仅看到文字不能证明录音丢失。

如果关闭了设置页，重新进入**相同手势**的 Webhook 页面，不要误改另一手势。Android 支持相同协议但 UI 可能不同；没有把 iPhone 的点击路径当成 Android 的已验证步骤。

录音或转写在 Pebble 中成功，不代表手机发送过 Webhook；应同时确认所用手势。转写即使没有“Muse”、被 ASR 写成“Hey Muse”，也会完整进入 Muse，不再被旧的前缀条件拦截。Both 使用 Pebble 转写和语音；Recording 只使用语音。

手机在后台接收戒指音频、排队和发出 Webhook 仍受 iOS/Pebble 的运行状态影响。Both 包含 Pebble ASR 等待；Recording 可避开这段等待，但不能保证后台即时送达。若打开 Pebble 后才开始处理，应分别记录“手机接收/发出”和“云端接收”的时间；Google 云端无法修复尚未发出的录音，也不能强制 iOS 后台 ASR 立即运行。

## 9. 可重复的云端验证

先执行不发聊天的连接检查：

```sh
python "$BRIDGE_DIR/verify_webhook.py" --url "$SERVICE_URL" --keys "$KEYS_FILE"
```

默认检查有效签名合成事件返回 200、错误鉴权返回 401。不会发送真实 Muse 消息。下一命令显式发送**一条**无害测试消息，再用相同 delivery ID 重放以检查去重：

```sh
python "$BRIDGE_DIR/verify_webhook.py" --url "$SERVICE_URL" --keys "$KEYS_FILE" \
  --send --report "$PRIVATE_WORK_DIR/smoke-report.json"
```

每次单独运行 `--send` 都产生新 ID，因而会新增一条消息。脚本不自动重试网络失败，不跟随重定向；报告只保留 delivery ID 和检查元数据，不含转写或密钥。测试文字不需要“Muse”等前缀；离线测试及 2026-11-16 的受控云端验证已经覆盖没有此前缀的文本。

`verify_webhook.py --send` 检查文字提交，不证明语音或 Both 提交成功。镜像构建中的 `audio_test.py` 使用合成音频验证真实 FFmpeg 转换，不访问 Muse。用户已确认 Recording 和 Both 模式的真实戒指消息及回答；Both 同时显示原转写和完整可播放录音。只显示文字或仅得到 SDK ACK，均不能证明 App 是否保留并展示音频。

验收顺序应为：离线测试 → 容器 codec smoke 与 HTTP 启动 → 签名合成事件 → 一次显式真实消息及去重 → 真实戒指所选载荷。前几项不能代替最后一项。应在 Muse 中亲自确认消息位于正确账户和主聊天。

## 10. 状态、并发与重复投递

状态桶中仅使用以下专用路径：

| 对象 | 内容与作用 |
| --- | --- |
| `state/muse.json` | `{identity, pairing, sdk_token}`；包括会刷新的设备凭证 |
| `state/webhook.json` | `{bearer, signing_secret}`；手机入口鉴权 |
| `runtime/lease.json` | 有效期 300 秒的全局设备会话租约 |
| `ledger/<SHA256(delivery ID)>.json` | 文本或标准化 WAV 的摘要、投递状态、时间及受限诊断；不保存原文/音频 |

GCS 对象 generation 前置条件用于 compare-and-swap。刷新状态先持久化，再进入实际发送；租约防止两个请求或新旧修订版同时使用同一设备身份。容器内还有线程互斥。Cloud Run 的实例上限并不能替代跨修订版租约。

关键发送边界：先写入 `sending` 投递记录，再调用官方 SDK 的 `send_chat` 或 `send_voice`；拿到确认才标记 `delivered`。两条路径共享同一发送边界和去重逻辑。这是降低重复执行风险的保守处理，不能让第三方聊天协议自动获得严格的 exactly-once 语义。

| 返回/状态 | 处理方式 |
| --- | --- |
| `200 delivered` | SDK 已确认提交；同一 ID 和同一内容再来返回 `duplicate: true` |
| `202 needs_review` | 已进入发送阶段但确认不完整；保留记录，重复请求不自动再发 |
| `503 not_delivered_retry_later` | 未进入发送阶段；可以稍后重新尝试 |
| `503 busy_retry_later` | 设备会话正忙，或尚有有效租约 |
| `409 delivery_id_reused` | 同一个 ID 出现不同正文摘要，拒绝覆盖 |
| `401 unauthorized` | Bearer、签名或时间戳不正确 |
| `200 test: true` | 合成连接测试；没有向 Muse 发消息 |

对 `sending` / `uncertain`，先到 Muse 看是否已有消息或已执行任务。**不要删掉 ledger 后盲目重放**，否则可能让同一指令执行两遍。崩溃留下的租约正常应等到过期；不要删除还在使用的租约强行并发。

## 11. 排障记录

### Apple 安装 Python/Git 时 Software Update 失败

`/usr/bin/python3`、`/usr/bin/git` 可能触发 Apple Command Line Tools 的安装弹窗，并不代表工具已安装。出现“software is not currently available from the Software Update server”后，重复点安装不会解决桥接本身的问题。

使用真正的预编译 Python/Git，避免从源码构建。Python 可选 [python.org 的 macOS 安装包](https://www.python.org/downloads/macos/)；Git 操作也可以直接在 Google Cloud Shell 的预装 Git 中完成，然后只把公开代码下载到 Mac 做 BLE 配对。若选择本地 Git，使用可信发布来源提供的现成二进制并确认版本。用明确的 Python 3.12 路径创建虚拟环境，后续调用该环境的 `python`；不要再调用会触发弹窗的系统占位程序。本方案不依赖反复执行 `xcode-select --install`。

### 蓝牙配对不出现或中断

确认运行的是配对工具，允许对应进程的蓝牙权限，手机 Muse 已开启 Developer mode，设备仍在配对窗口内。Mac 适配器是实验性路径，遇到平台兼容问题可使用官方 Linux BLE 路径；不要用改变官方加密协议来解决平台问题。

### 新项目 Cloud Build 首次提交 403

实际初次初始化中，源文件上传已成功，但创建 Build 的请求暂时返回 403；等待新服务账号/服务代理 IAM 生效后，一次受控重试成功。先核对当前项目、执行账户、Cloud Build API、自定义构建服务账号及脚本列出的权限，不要因临时传播延迟给运行账号增加项目 Owner。如果问题持续，应按 Google 返回的具体权限排查，不能把所有 403 都认定为传播延迟。

### Cloud Run 看似 Ready，实际请求却失败

曾出现源码文件为 `0600`，Docker `COPY` 后由 root 持有，运行用户 `bridge` 无法导入 `app.py`；只检查 TCP 端口会过早认为服务可用。最终 Dockerfile 使用 `COPY --chown=bridge:bridge`，并在镜像构建中以非 root 用户导入应用、检查 `/health`。部署脚本还配置 HTTP startup probe：`/health`、端口 8080、初始延迟 0、周期 5 秒、超时 2 秒、失败阈值 12。

这只能确认应用成功启动，不能替代真实的 GCS 和 Muse 连通测试。不要通过将容器改为 root 来掩盖文件所有权问题。

### Muse WebSocket 握手 HTTP 502

曾在 Meta WSS 建立阶段遇到 HTTP 502。现在只在**尚未开始发送消息**时，对明确的握手 HTTP 429/500/502/503/504 做最多三次连接尝试，共用 55 秒 SDK 等待预算。实际 `send_chat` / `send_voice` 不自动重试；进入发送阶段后发生不确定错误会记为需人工核对。

2026-11-16 的受控测试曾取得提交确认和重复投递检查结果；2026-11-17 的最新复验再次遇到握手 502，新旧运行版本均复现，记录为发送前失败，尚未取得新的投递确认。这不能据此断言 Meta 全局故障。不要为此重置有效配对或关闭 TLS/签名。诊断只记录阶段、异常类别、HTTP 状态等受限字段，不记录完整异常文本或远端响应正文。

### 没有“Muse”前缀就不进聊天

这是先前实现中的内容条件，最终代码已删除。真实转写如“Hey Muse，……”以及普通中文都进入同一聊天；首词不会被当成路由条件。若部署后仍出现此行为，检查运行镜像是否为本功能分支构建、是否仍给旧修订版分配流量，以及手机有没有把该手势发到正确的 `/index`。

### Muse 只有文字，没有录音条

先检查 Pebble 对应手势的 **Webhook settings → Recent runs**。成功记录中的 `recording + transcription` 表示实际上传了两者；`transcription` 表示只上传文字。这些标签由[实际载荷内容生成](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/external/indexwebhook/IndexWebhookApi.kt#L368-L373)，比仅看所选模式更直接。

Webhook 配置按手势分别保存。确认页面标题为 **Webhook · Double click & hold**，选 **Both** 后点 **Save**，再用该手势录制一条**全新消息**。修改模式不会给旧的排队任务补上录音：发送操作先[选定载荷并保存文件 ID](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/service/recordings/button/IndexWebhookUploadRecordingOperation.kt#L34-L95)，队列随后使用[任务中已有的文件 ID 和转写](https://github.com/coredevices/mobileapp/blob/1.18.0/experimental/src/commonMain/kotlin/coredevices/ring/external/indexwebhook/IndexWebhookDeliveryQueue.kt#L47-L84)。重试旧的文字任务仍然只发文字。

若 Recent runs 确认同时上传，再核对云端接收和提交记录。App 中只有文字本身不足以证明音频在中转或 Muse 中丢失。桥接收到无效或超限音频会返回错误，不会静默改发文字；用于比较的测试必须是同一条新录音。

Both 中的文字是独立文本项，不是告诉 Muse“录音已经完成识别”的元数据。官方[语音接口](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/linux/src/musegadget/link_client.py#L404-L434)把文字和录音一并提交，官方[说明](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/esp32/README.md#L341-L343)写明 Muse 会转写语音笔记。实测出现“Pebble 原文后面附加另一段识别文字”，与 Muse 再次识别录音相符；具体后台拼接方式仍是推断。桥接不会生成或追加这段识别内容，公开接口也没有关闭录音识别或指定权威转写的参数。

### Muse 手机有回答，Apple Watch 没提示

官方 [Muse from Meta App Store 页面](https://apps.apple.com/us/app/muse-from-meta/id6760173601) 的 Watch 支持说明，不代表 Gadget SDK 触发的回答必然产生 Watch 推送。目前 Watch App 已安装，账户同步和回答推送仍待验证。本方案没有接入私有通知 API；先以 Muse 主聊天中的消息与回答为准。

## 12. 代码审查、可读性要求与精简记录

更新文档时，请在同一 PR 中同步修改 README.md 和 README.zh-CN.md 的对应章节；两种语言保持相同的操作步骤、命令、配置含义、链接和已知限制。

本分支按 [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) 与 [Google Code Review Developer Guide](https://google.github.io/eng-practices/review/reviewer/) 检查命名、职责、复杂度、错误处理、测试和文档；使用 Google 风格格式化和 Pylint 检查。这是采用公开规范的审查，不表示获得 Google 内部 Readability 审批。

### 本轮发现并修复的问题

| 问题 | 修改与验证 |
| --- | --- |
| Muse 已解除设备配对时，中转过早把 SDK 的 `UNPAIRED` 结果转成 `STOPPED` | 保留官方解除配对处理，将清除后的状态保存到私有桶；测试确认没有发送或重新连接 |
| SDK 注册完成和连接关闭同时发生时，可能把未发送的消息标成结果不明 | 使用官方等待注册的方法，优先处理已经结束的连接；回归测试确认不提前记录发送 |
| Mac 对同批 BLE 写请求逐条响应，后续无效请求可能使前面请求已执行 | 整批验证成功后按顺序交给 SDK，仅对第一条请求响应一次；测试覆盖无效批次不部分执行、合法批次顺序和单次响应 |
| 暂时调试信息与未使用的状态使实现难读 | 删除日志文字匹配、代理/server 分类、未用错误字段、无效赋值与重复状态保存 |

### 删除或合并的非必要内容

- 删除公开的手机配置页生成器、对应测试和导航图片；手机点击步骤只在本指南维护。已有私人配置页不属于源码或运行服务。
- 删除 `upload-bundle` 迁移入口，初始上传统一使用官方配对产生的三个文件与 `upload-pairing`。
- 删除额外的 `APPROVED_*` 环境变量；部署脚本的每个阶段仍需显式调用，并保留项目检查、私有部署与首次上传的 create-only 保护。
- 删除未使用的聊天选择配置，固定进入 Muse 主聊天；删除额外的 Mac `--probe` 和自动搜索 SDK 路径功能。
- 删除与 GCS 跨实例租约重复的进程内 HTTP 互斥锁、未使用字段和初始化、未使用赋值与重复保存。
- 把真实编解码检查并入 `audio_test.py`，不再维护单独的 codec smoke 脚本。最终镜像不包含 pytest 或测试文件。
- 把人工连通性检查命名为 `verify_webhook.py`，对应 `verify_webhook_test.py`；所有 Python 模块按 `模块名_test.py` 对应。
- 云端目录的 README 仅保留用途、主要文件和测试入口；完整安装、部署、手机配置和运行维护分别在根目录的英文 README.md 与中文 README.zh-CN.md 中维护。
- 按用户要求进一步把 MIME 解析与字段读取提取回官方示例模块，本地入口与 Cloud Run 共同调用；文字和令牌提取直接复用官方函数，保留原示例回归测试。

### 保留的必要边界

GCS CAS 和跨实例租约保护轮换凭证；发送前的持久记录避免同一任务重复执行；取消时等待令牌刷新完成，减少丢失替换令牌的窗口。音频解码保留格式、大小、时长、协议和执行时间边界，整条拒绝超限音频，不截断后执行。中转不注册主机命令，也不记录录音、转写或异常消息正文。这些对应已验证的运行风险，不能仅为了少几行代码而删除。

测试保留独立的签名生产和验证路径、真实 SDK 序列化契约、发送前后重试边界、存储冲突、取消与撤销状态、实际 FFmpeg 转换以及 Mac 批次验证。不会把测试和生产鉴权合并成同一实现来减少重复，因为那会降低发现协议错误的能力。

### 提交前的独立审查

以下为已发布实现版本的审查历史；本次双语文档整理的审查结果另记在同一 PR 中，不把旧版本的结论当成本次文档的批准。

实现版本 `987f8d1` 提交前实际运行了 Google [Antigravity CLI](https://antigravity.google/docs/getting-started?tab=cli) **1.5.10**，模型 ID 为 **`gemini-4-argon-medium`**。2026-11-17 分为 6 个全新上下文审查，每批低于 80 KB，覆盖全部 19 个拟发布文件及 6 个官方 SDK 参考文件。每批正常返回，文件末行、正文摘要和独立结束标记均已核对；另一轮独立检查也逐行核对了输入覆盖。

该实现版本的最终冻结审查返回 **`READY_FOR_COMMIT_NO_ACTIONABLE_FINDINGS`**。前 5 批没有发现需要修改的问题；文档批发现两处过期指引：已删除的编解码脚本名称，以及密钥轮换后重新生成设置页的要求。两处已分别改为 `audio_test.py` 和按第 8 节直接配置手机。修复后的完整文档复审返回 **“NO_ACTIONABLE_FINDINGS / Disposition: APPROVED”**，文件覆盖和结束标记均已核对。审查发现已全部处理，没有未解决的修改项。

首轮长输入审查因模型明确报告尾部截断而作废，没有将其“无发现”结论算作完整通过。分批材料来自 322,830 字节的脱敏快照，SHA-256 为 `f33d1786056a05b0b70ad91e0a40f2a930b1373fd82e6c8f11cda88867c5b0bd`；文档修复及本段审查记录随后补入。

审查工具在用户自己的 Cloud Shell 临时目录运行，通过 Google 个人账号登录，没有启用付费 credits。它只收到源码与文档，未收到配对状态、Webhook 密钥、录音或聊天；严格权限拒绝文件读写、命令、URL 和 MCP 工具执行，事件记录确认没有工具调用。因此这是独立模型的静态审查，不能把上表的实际测试说成审查 agent 执行的测试，也不表示 Google 内部 Readability 审批。

边界仍需明确：Muse 提交后确认丢失会留下 `needs_review`，不自动重发；设备 token 已在远端轮换但云端尚未保存时遭遇进程强杀或存储故障，仍可能需要重新配对。手机后台排队、ASR 延迟、Watch 同步/推送也不是这个云端监听器能保证的能力。

## 13. 更新、密钥轮换、回滚和停用

### 更新代码

重新运行离线测试、`build`、`deploy-private`。检查服务 IAM 与最新修订版后，若需要对手机重新开放，运行 `publish`。更新应用**不需要**重新上传配对文件。云端 `state/muse.json` 是已经刷新的有效状态，不能用原 Mac/Linux 的旧文件覆盖它。

```sh
PYTHONPATH="$REPO_DIR/linux/src:$REPO_DIR/linux/examples:$BRIDGE_DIR" \
  python -m pytest -q \
  "$REPO_DIR/linux/examples/pebble_ring_bridge_test.py" \
  "$REPO_DIR/linux/tests/test_pebble_ring_bridge.py" "$BRIDGE_DIR"
bash "$BRIDGE_DIR/deploy.sh" build "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" deploy-private "$PROJECT_ID"
bash "$BRIDGE_DIR/deploy.sh" status "$PROJECT_ID"
```

### 轮换 Webhook 密钥

先暂停公网调用，生成新的本地文件，以 generation 前置条件替换 `state/webhook.json`，再更新手机相同手势中的两项密钥并重新开放。密钥会在进程中缓存最多 60 秒；更新时留出缓存过期时间，或部署新修订版后确认生效。下面不读取或打印旧密钥：

```sh
gcloud run services remove-iam-policy-binding muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 \
  --member=allUsers --role=roles/run.invoker

ROTATED_KEYS="$PRIVATE_WORK_DIR/webhook-next.json"
python - "$ROTATED_KEYS" <<'PY'
import json, os, secrets, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump({'bearer': secrets.token_urlsafe(32),
               'signing_secret': secrets.token_urlsafe(32)}, stream)
PY

CURRENT_GENERATION=$(gcloud storage objects describe \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --format='value(generation)')
gcloud storage cp "$ROTATED_KEYS" \
  "gs://${PROJECT_ID}-muse-ring-state/state/webhook.json" \
  --project="$PROJECT_ID" --if-generation-match="$CURRENT_GENERATION"
KEYS_FILE="$ROTATED_KEYS"
```

按第 8 节用新 `KEYS_FILE` 修改手机对应手势的密钥，确认 60 秒缓存窗口过去，再执行 `publish` 和不发聊天的连通性检查。以后继续使用新的 keys 路径；不要再用旧密钥或旧设置页。若设备凭证泄露，除轮换入口密钥外，还需在 Muse 中撤销相应设备/SDK token，重新配对；改变 Webhook 密钥不能撤销已经泄露的 Muse 设备权限。

### 回滚应用

先查看修订版，选择确知兼容当前状态格式和最终直通行为的版本；只回滚应用，不回滚令牌对象。旧镜像可能被七天清理策略删除，计划回滚时需确认它仍可用。

```sh
gcloud run revisions list --service=muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1
PREVIOUS_REVISION='your-compatible-previous-revision'
gcloud run services update-traffic muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 \
  --to-revisions="$PREVIOUS_REVISION=100"
```

回滚后检查 `/health`、签名测试和一次明确选择的真实消息。不要把曾有前缀过滤或文件权限故障的旧版本当成可用回滚点。

### 暂停或完全删除

临时暂停可使用上面的 `remove-iam-policy-binding`，并在 Pebble 中关闭该手势的 Webhook。IAM 更改前已开始的请求可能仍会结束，暂停不等于撤回已经给 Muse 的任务。

完全删除前先决定是否还要保留这一设备身份。要恢复本地使用，只能安全导出**云端当前**的配对 bundle 并停止云端后迁移；不要启动已经过期的本地副本。若不再使用，在 Muse 中删除对应设备、撤销相关 SDK token，并清理本地配对文件、设置页和密钥。

下列命令会删除本方案的云资源和凭证，运行前确认 `PROJECT_ID` 为自己的专用项目、桶没有其他数据：

```sh
gcloud run services delete muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1
gcloud artifacts repositories delete muse-ring \
  --project="$PROJECT_ID" --location=us-central1
gcloud storage rm --recursive "gs://${PROJECT_ID}-muse-ring-build" \
  --project="$PROJECT_ID"
gcloud storage rm --recursive "gs://${PROJECT_ID}-muse-ring-state" \
  --project="$PROJECT_ID"
gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/logging.logWriter --condition=None
gcloud iam service-accounts delete \
  "muse-ring-runtime@${PROJECT_ID}.iam.gserviceaccount.com" --project="$PROJECT_ID"
gcloud iam service-accounts delete \
  "muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com" --project="$PROJECT_ID"
```

不要为清理本方案关闭共享项目中其他服务所需的 API，或删除其他应用的日志与账单记录。本方案的源码可以公开分享；真实凭证、私有生成页、录音、聊天记录和个人部署身份始终留在仓库之外。

## 14. 许可证

Muse Gadgets 使用 [Apache License 2.0](LICENSE)。下列第三方文件保留各自的上游许可证；此 fork 保留原有文件和许可证说明。此表对应[原版 SDK 的许可证清单](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/README.md#license)。

| 路径 | 上游来源 | 许可证 |
| --- | --- | --- |
| [`esp32/components/minimp3/include/minimp3.h`](esp32/components/minimp3) | [lieff/minimp3](https://github.com/lieff/minimp3) | CC0-1.0，见 [`LICENSE`](esp32/components/minimp3/LICENSE) |
| [`ostb_echoear_display_init.h`](esp32/components/muse/boards/ostb_echoear_display_init.h) | [78/xiaozhi-esp32 ESP-VoCat 面板初始化序列](https://github.com/78/xiaozhi-esp32/blob/0d576d3d4c049c6f55eaf879725dc23e516511b4/main/boards/espressif/esp-vocat/esp_vocat.cc) | MIT，文件头保留声明 |
| [`board_atk_dnesp32s3_box2.c`](esp32/components/muse/boards/board_atk_dnesp32s3_box2.c) 中的 ST7789 调整表 | [78/xiaozhi-esp32 ATK-DNESP32S3-BOX2 板](https://github.com/78/xiaozhi-esp32/blob/a78ca19d5bc9a12ee0853b9f509530276d36aa03/main/boards/alientek/atk-dnesp32s3-box2-wifi/atk_dnesp32s3_box2.cc) | MIT，见 [`xiaozhi-esp32.txt`](esp32/devices/licenses/xiaozhi-esp32.txt) |
| [`board_seeed_xiao_s3_147.c`](esp32/components/muse/boards/board_seeed_xiao_s3_147.c) 中的 JD9853A 初始化序列 | [Seeed-Studio/Seeed_GFX2](https://github.com/Seeed-Studio/Seeed_GFX2) | MIT，见 [`Seeed_GFX2.txt`](esp32/devices/licenses/Seeed_GFX2.txt) |
| [`viewe_smartring_display_init.h`](esp32/components/muse/boards/viewe_smartring_display_init.h) | [VIEWESMART/smartring_plus](https://github.com/VIEWESMART/smartring_plus) 面板初始化序列 | Apache-2.0，文件头保留 VIEWE 版权声明 |
| [`waveshare_s3_185b_init.h`](esp32/components/muse/boards/waveshare_s3_185b_init.h) | [waveshareteam/ESP32-S3-Touch-LCD-1.85B](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85B/tree/7232af4c501c64d34215b73e1d8319376dfd9c19/examples/esp-idf/05_bq27220/components/esp32_s3_touch_lcd_1_85B) 面板序列 | Apache-2.0，文件头保留声明 |
| [`esp32/main/pixel_font.c`](esp32/main/pixel_font.c) | Adafruit GFX `glcdfont.c` | BSD-2-Clause，文件头保留声明 |
| [`esp32/components/muse/fonts/`](esp32/components/muse/fonts) | GNU Unifont 16.0.04、Noto Sans SC、Montserrat Medium，经 LVGL 整理；见目录 README | SIL Open Font License 1.1；Unifont 另有 GPL-2.0+ 及字体嵌入例外 |
| [`montserrat.ttf`](esp32/components/muse/eink/fonts) | Montserrat Medium，经 LVGL v9.5.0 | SIL Open Font License 1.1 |
| [`unscii.ttf`](esp32/components/muse/eink/fonts) | unscii-8 | 公有领域 |
| [`symbols.ttf`](esp32/components/muse/eink/fonts) | Font Awesome 5 Free，经 LVGL v9.5.0 | SIL Open Font License 1.1；图标为 CC BY 4.0 |
| [`esp32/main/epd_ssd1677.c`](esp32/main/epd_ssd1677.c) | Seeed-reTerminal-E10xx-Firmware 的 SSD1677 刷新序列 | MIT，文件头保留声明 |
| [`esp32/components/tinyh264/src`](esp32/components/tinyh264) | Android Open Source Project 的 H.264 解码器，经 [udevbe/tinyh264](https://github.com/udevbe/tinyh264) | Apache-2.0，保留 AOSP 版权，见 [`LICENSE`](esp32/components/tinyh264/LICENSE) |
| [`esp32/components/libvpx_vp8/src`](esp32/components/libvpx_vp8) | [libvpx](https://chromium.googlesource.com/webm/libvpx) v1.16.0 的 VP8 解码器裁剪版 | BSD-3-Clause，保留 WebM Project 版权；见 [`LICENSE`](esp32/components/libvpx_vp8/LICENSE) 及 Google 专利授权 [`PATENTS`](esp32/components/libvpx_vp8/PATENTS) |

构建时下载的依赖各自使用其上游许可证：包括 ESP-IDF 组件（`esp32/managed_components/`），以及模拟器使用的 LVGL 和 SDL，见 [`esp32/simulator/THIRD_PARTY.md`](esp32/simulator/THIRD_PARTY.md)。

Apache 许可证不适用于 [Jollybot 头像](esp32/avatar)。

Muse Gadgets Test 的示例视频不在本仓库中；播放时应用会下载 Blender Foundation 开放影片中的短片段（CC BY 3.0），并展示署名。

Muse Gadgets Test 可以在手机端使用 x264（GPL-2.0），本仓库不包含其代码。将其构建进 APK 或 iPhone App 时，产物使用 GPL；详见 [Android](android/musegadgetstest/README.md) 和 [iPhone](ios/musegadgetstest/README.md) 说明。
