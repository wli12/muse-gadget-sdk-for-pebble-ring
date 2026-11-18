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

把 Pebble Index 01 的转写和录音通过自己的 Google Cloud Run 发送到 **Meta 的 Muse** 主聊天。本分支改造自[官方 Muse Gadget SDK 示例](linux/examples/pebble_ring_bridge.py)，复用其解析辅助函数，并增加按请求运行的云端状态与音频支持。

本指南涵盖设置、文件来源、部署、验证、隐私、费用、恢复与清理。部署项目、运行账户和 Webhook 地址均为占位符；公开源码使用实际 GitHub 地址。仓库不包含真实设备凭证、Webhook 密钥、个人聊天或录音。原版 SDK 概览见[固定提交的上游 README](https://github.com/facebookincubator/muse-gadget-sdk/blob/08f89ed329918c27d53b11192065ad6b2cb0e868/README.md)。

[准备清单](#1-开始前需要什么) · [隐私](#2-隐私与数据去向) · [部署步骤](#3-一步步完成部署) · [FYI](#4-fyi实现与维护)

## 1. 开始前需要什么

| 准备项 | 什么时候用 | 需要准备什么 |
| --- | --- | --- |
| 戒指 | 每次发送指令 | 一枚 [Pebble Index 01](https://repebble.com/products/pebble-index-01)，按[官方指南](https://help.repebble.com/en/articles/15434751-index-01-getting-started-guide)与手机配对。无需购买 Pebble 手表。 |
| 手机 | 每次发送指令 | **iPhone 或 Android**，需要蓝牙和网络。iPhone 因 Muse 要求需 **iOS 18+**；Android 需兼容下方两款 App，不能仅把 Pebble 的 Android 8.0 要求当作整套方案的最低版本。 |
| 手机 App、Muse 账户和 SDK token | 首次设置及日常使用 | 安装 [Pebble](https://repebble.com/app) 和 Meta 的 Muse（[iPhone](https://apps.apple.com/us/app/muse-from-meta/id6760173601)、[Android](https://play.google.com/store/apps/details?id=com.facebook.aura)）。Pebble 应已能接收／转写录音并提供签名 Webhook。登录 Muse，申请 [Gadget SDK token](https://gadgets.muse.ai/settings/sdk-tokens)，阅读[条款](https://gadgets.muse.ai/sdk-terms)。 |
| 自己的 Google Cloud 项目 | 日常云端中转 | 本指南使用 **Cloud Run 运行程序**、**私有 Cloud Storage 保存状态文件**，无需另外租一台常开的虚拟机。低频使用有机会落入免费额度。准备 Google 账户和关联结算账户的项目：[注册步骤](#44-注册-google-cloud-并准备项目)、[试用金](#45-免费试用金)、[查看实际花费](#46-查看实际花费)。 |
| 蓝牙配对电脑 | **只在首次设置时用一次** | 可用本地带 **BLE 蓝牙的 Linux 主机**，或使用实验性适配器的 **Mac**，将 Muse 与设备身份配对。完成配对、上传状态并设置好云端后即可关机。[两种配对方式选一种](#31-给用户交给-codex完成账号和手机操作)。 |
| 部署用终端和工具 | 首次部署、更新和检查 | 最直接的做法是用**上面同一台 Mac／Linux 电脑的终端**运行 Git、Python 3.12 和 `gcloud`。也可选择 **Cloud Shell，即 Google 网页内的云端终端**。它是执行部署命令的另一种方式，无需再买电脑；蓝牙配对仍在本地完成。[准备工具](#31-给用户交给-codex完成账号和手机操作)。 |
| Apple Watch（可选） | 可选的回答查看入口 | 如有需要，安装官方 Muse Watch App；本桥接触发的回答同步和通知仍未验证。 |

## 2. 隐私与数据去向

**日常数据链路：** 戒指 → **蓝牙** → 手机 Pebble App → **HTTPS** → 自己的 Google Cloud Run → **Muse Link** → Meta Muse。

| 参与方 | 能看到什么 | 不能看到什么（本方案不提供） |
| --- | --- | --- |
| 手机上的 Pebble App | 戒指录音／转写，以及配置在 App 中的 Webhook 地址、Bearer 和签名密钥。 | 中转不向 Pebble 返回 Muse 回答／聊天历史，也不提供 Muse 设备凭证或 SDK token。 |
| Pebble 选用的云服务（例如 Wispr Flow／OpenRouter，若启用） | Pebble 按识别、代理、备份和集成设置发送的录音／文字。 | 中转不直接调用它们，也不把 Muse 凭证或回答发给它们；Pebble 自己附带的数据由其设置与政策决定。 |
| 自己的 Google Cloud 项目 | 上传的录音／文字、设备凭证、Webhook 密钥及投递／请求元数据；有权限的管理员及 Google 受控运维、法定访问等仍适用。 | 中转不上传手机／电脑上的其他文件或账号密码；会处理笔记内容，但不把录音／正文保存在存储或日志中。 |
| Meta Muse | 提交的录音／文字和 Muse 设备／鉴权信息；由 Meta 处理、保存。 | 中转不向 Muse 提供自己使用的 Google Cloud IAM 凭证或 Webhook Bearer／签名密钥。 |
| Muse 调用的 App、工具或网站 | 按你的任务与授权分享的数据。 | 中转不会自动把保存的凭证包交给它们；单独授权的应用可能有其他访问权限。 |
| 一次性配对的 Mac／Linux 电脑 | 仅当前用户可读文件中的设备身份、访问／刷新凭证、SDK token；能访问电脑／账户的人仍可能读取。 | 迁移到云端后，日常录音／转写和回答不再经过这台电脑。 |
| GitHub、项目作者和审查模型 | 公开源码／文档、公开 GitHub 署名；审查输入为源码／文档。 | 正常中转不会向它们或 Codex/OpenAI 上传私有凭证、录音／转写或 Muse 聊天。 |

**“不能看到”的范围：** 指中转默认没有传输或开放这些数据。你主动放进指令的信息会一起发送；其他授权、服务商后台或管理员可能有其他访问途径，不等于对方绝对不可能获得。

**需要检查的设置：** Webhook only 不代表使用本地识别。Muse 政策说明默认使用脱敏对话／工具轨迹训练，可在设置中退出；中转不会替你修改该偏好。

政策与设置：[Pebble 隐私](https://repebble.com/privacy)、[Pebble 处理选项](https://help.repebble.com/en/articles/15434751-index-01-getting-started-guide)、[Google 访问控制](https://docs.cloud.google.com/docs/security/privileged-access-management)、[Muse 政策](https://muse.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse)。本地识别与本地代理是[独立选项](https://developer.repebble.com/index-01/)；也请核对 Muse 的训练设置和工具权限。

## 3. 一步步完成部署

### 3.1 给用户：交给 Codex，完成账号和手机操作

代码、依赖、云端私有状态、构建、部署和检查可以交给 Codex。你主要完成下面这些账号和实体设备操作：

| 步骤 | 你需要做什么 | Codex 负责什么 |
| --- | --- | --- |
| 1. 发起部署 | 把下方指令发给 Codex，说明手机是 iPhone 还是 Android、配对电脑是 Mac 还是 Linux。 | 读取本仓库，检查环境，一次列清需要你手动完成的事情。 |
| 2. Google Cloud | 登录，选择自己的、已关联结算账户的项目，完成 Google 的授权页面。[注册步骤](#44-注册-google-cloud-并准备项目)／[试用金](#45-免费试用金)。 | 准备专用云资源并部署中转，保持 min 0／max 1。 |
| 3. Muse token | 登录 Muse，申请 [SDK token](https://gadgets.muse.ai/settings/sdk-tokens)，在工具的本地隐藏输入提示中填写；不要把实际值写进 README 或公开消息。 | 启动配对工具，私下保存凭证。 |
| 4. 配对一次 | 手机中打开 **Muse → Settings → Devices → Developer mode → Add Device**，选择工具显示的设备并确认。 | 运行本地蓝牙适配器，导出／上传配对状态，停止本地身份运行。 |
| 5. 设置 Pebble | **Index → 齿轮 → Ring Button → Double click & hold → Webhook only → Webhook settings**。填入 Codex 私下提供的 URL 和签名密钥；新增 Header `Authorization`，值为 `Bearer <值>`（中间有空格）；启用签名，选 **Both**，再 Save。 | 提供私有配置值，验证签名连接测试。 |
| 6. 验收 | 首次测试保持 Pebble 打开。按一下松开，随即再次按下并保持讲话，最后松开；到 Muse 确认消息和回答。 | 必要时检查日志、投递与去重；不盲目重发确认不明的指令。 |

上面的手机菜单路径以 iPhone 为例；若没有 Index，先选 More → Index。Android 界面可能不同。

可以把这段指令直接发给 Codex：

```text
请读取 https://github.com/wli12/muse-gadget-sdk-for-pebble-ring （分支 pebble-cloudrun-pass-through）中的 README，按机器操作参考，把 Pebble Index 01 → Meta Muse 中转部署到我授权的 Google Cloud 项目。使用一次性本地蓝牙配对、Cloud Run 最小实例 0／最大实例 1，以及 Both 载荷。凭证保存在必需的、仅当前用户可读的本地文件及专用私有云存储中，不要把真实凭证或我的部署地址提交到 GitHub。请先完成可自动化的工作，一次列清需要我登录、授权和操作手机的步骤，并验证结果。
```

上传配对状态、完成云端设置后，配对电脑即可关机；日常只需手机保持蓝牙和联网。连接测试成功不等于 Muse 已回答，还要核对真实戒指消息。

### 3.2 给 Codex：部署与维护参考

<details>
<summary>展开技术操作步骤——供 Codex 或手动部署的管理员读取</summary>

#### 3.2.1 准备项目、代码和工具

使用自己的、已关联结算账户的项目；如还没有，先按[注册步骤](#44-注册-google-cloud-并准备项目)创建。最直接的方式是在配对用的同一台 Mac／Linux 电脑终端执行下面命令。也可用 Cloud Shell 部署，但蓝牙配对仍需本地电脑。

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

#### 3.2.2 只做一次 Muse 配对

**Linux 和 Mac 选一种，不需要两种都做。** 配对在本地电脑执行；若上一步选了 Cloud Shell，也需在本地配对电脑克隆相同仓库、准备 Python 和私有工作目录，配对完成后再把文件安全转移到所选部署终端。

##### 方式 A：带 BLE 的 Linux

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

##### 方式 B：Mac

本次实际使用的适配器是社区实验性工具，**不是 Meta 官方支持 macOS 的声明**。它只替换蓝牙平台适配层，复用官方配对流程；不启动通用主机命令服务。需要 macOS 蓝牙、Python 3.12 和 PyObjC wheel。

在前面创建的虚拟环境中执行：

```sh
python -m pip install --only-binary=:all: \
  -r "$BRIDGE_DIR/requirements-macos.txt"

PAIRING_DIR="$PRIVATE_WORK_DIR/muse-state"
python "$BRIDGE_DIR/mac_pairing.py" --state-dir "$PAIRING_DIR"
```

SDK token 通过隐藏输入提示提供，程序不接受命令行 token。macOS 如要求蓝牙权限，允许运行程序所用的 Terminal/Python，然后在 Muse App 按上方 Linux 选项中的相同路径添加显示的设备。工具会拒绝覆盖已经完成的配对；不要为了解决云端临时错误就删除有效身份重新配对。

配对完成后结束该工具，确认本地没有用同一身份运行的 SDK 服务，再上传。**iPhone 接 Mac 的 USB 线不是中转链路**；如果系统显示普通的“信任此电脑”，那是 USB 设备访问授权，与 Muse BLE 配对、戒指 Webhook 是不同环节。正常使用不需要保持 USB 或 Mac 在线。

如果配对与部署使用不同终端，先安全转移配对文件，再**在部署终端里**设置 `PAIRING_DIR='/absolute/path/to/muse-state'`，填转移后的实际目录；若使用同一台电脑／终端，保留上面已经设置的 `PAIRING_DIR` 即可。

#### 3.2.3 部署到 Google Cloud

使用单独的新项目或确认这些专用资源名称没有被其他应用使用。脚本会在专用桶上调整版本、soft delete 和 IAM 设置，不能把已有通用桶换名接入后盲目执行。

```sh
bash "$BRIDGE_DIR/deploy.sh" plan "$PROJECT_ID"
```

`plan` 会显示将在当前项目中创建的资源，不访问 Google、不读取凭证。确认项目和资源名称后再继续。

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

初次上传只完成一部分时，请使用[恢复步骤](#初次凭证只上传成功一部分)，不要删除或替换有效设备状态。

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

##### 云端资源名称

| 资源 | 名称 |
| --- | --- |
| 区域 | `us-central1` |
| Cloud Run 服务 | `muse-ring-bridge` |
| 私有状态桶 | `${PROJECT_ID}-muse-ring-state` |
| 构建源文件桶 | `${PROJECT_ID}-muse-ring-build` |
| Artifact Registry 仓库 | `muse-ring` |
| 运行服务账号 | `muse-ring-runtime@${PROJECT_ID}.iam.gserviceaccount.com` |
| 构建服务账号 | `muse-ring-builder@${PROJECT_ID}.iam.gserviceaccount.com` |

#### 3.2.4 设置手机并测试戒指

需要 URL：`$SERVICE_URL/index`，以及私有 `KEYS_FILE` 中的 `bearer` 和 `signing_secret`。在所选部署环境的编辑器中打开该文件（使用 Cloud Shell 时可用 Cloud Shell Editor），复制到手机对应字段；不要把文件、密钥或包含密钥的截图上传到 GitHub 或聊天。手机配置步骤直接写在下方，不需要额外的配置页面生成器。

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

Pebble 中出现笔记不代表 Webhook 已发送；应确认配置的手势，并在 Recent runs 核对实际载荷。

手机在后台接收戒指音频、排队和发出 Webhook 仍受 iOS/Pebble 的运行状态影响。Both 包含 Pebble ASR 等待；Recording 可避开这段等待，但不能保证后台即时送达。若打开 Pebble 后才开始处理，应分别记录“手机接收/发出”和“云端接收”的时间；Google 云端无法修复尚未发出的录音，也不能强制 iOS 后台 ASR 立即运行。

#### 3.2.5 验证云端服务

先执行不发聊天的连接检查：

```sh
python "$BRIDGE_DIR/verify_webhook.py" --url "$SERVICE_URL" --keys "$KEYS_FILE"
```

默认检查有效签名合成事件返回 200、错误鉴权返回 401。不会发送真实 Muse 消息。下一命令显式发送**一条**无害测试消息，再用相同 delivery ID 重放以检查去重：

```sh
python "$BRIDGE_DIR/verify_webhook.py" --url "$SERVICE_URL" --keys "$KEYS_FILE" \
  --send --report "$PRIVATE_WORK_DIR/smoke-report.json"
```

每次单独运行 `--send` 都产生新 ID，因而会新增一条消息。脚本不自动重试网络失败，不跟随重定向；报告只保留 delivery ID 和检查元数据，不含转写或密钥。

`verify_webhook.py --send` 检查文字提交，不证明语音或 Both 提交成功。镜像构建中的 `audio_test.py` 使用合成音频验证真实 FFmpeg 转换，不访问 Muse。用户已确认 Recording 和 Both 模式的真实戒指消息及回答；Both 同时显示原转写和完整可播放录音。只显示文字或仅得到 SDK ACK，均不能证明 App 是否保留并展示音频。

验收顺序应为：离线测试 → 容器 codec smoke 与 HTTP 启动 → 签名合成事件 → 一次显式真实消息及去重 → 真实戒指所选载荷。前几项不能代替最后一项。应在 Muse 中亲自确认消息位于正确账户和主聊天。

#### 3.2.6 投递状态与恢复

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

##### 初次凭证只上传成功一部分

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

#### 3.2.7 更新、密钥轮换和停用

##### 更新代码

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

##### 轮换 Webhook 密钥

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

按第 3.2.4 步用新 `KEYS_FILE` 修改手机对应手势的密钥，确认 60 秒缓存窗口过去，再执行 `publish` 和不发聊天的连通性检查。以后继续使用新的 keys 路径；不要再用旧密钥或旧设置页。若设备凭证泄露，除轮换入口密钥外，还需在 Muse 中撤销相应设备/SDK token，重新配对；改变 Webhook 密钥不能撤销已经泄露的 Muse 设备权限。

##### 回滚应用

先查看修订版，选择确知兼容当前状态格式和最终直通行为的版本；只回滚应用，不回滚令牌对象。旧镜像可能被七天清理策略删除，计划回滚时需确认它仍可用。

```sh
gcloud run revisions list --service=muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1
PREVIOUS_REVISION='your-compatible-previous-revision'
gcloud run services update-traffic muse-ring-bridge \
  --project="$PROJECT_ID" --region=us-central1 \
  --to-revisions="$PREVIOUS_REVISION=100"
```

回滚后检查 `/health`、签名测试和一次明确选择的真实消息。

##### 暂停或完全删除

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

##### 当前诊断规则

- Mac 使用实际的预编译 Python／Git，避免触发不可用 Apple 开发工具下载的系统占位程序。
- Cloud Build 403 先核对项目、API、构建身份和 IAM 生效情况，不要授予运行账号项目 Owner。
- Muse 握手失败时保留有效配对、TLS 和签名，查看受限诊断；不要盲目重置身份或重放确认不明的投递。
- 只有文字展示时，检查新手势录音的 Recent runs 实际载荷；音频展示方式由 Muse 决定。


</details>

## 4. FYI：实现与维护

### 4.1 各部分实际在哪里运行

看自己拥有的资源，只需区分 **本地 Mac／Linux 电脑**和**自己的 Google Cloud 项目**。Cloud Run 是托管运行中转程序的地方，不需要自己管理一台虚拟机；Cloud Storage 是存文件的地方，不是另一台运行程序的电脑。

| 位置 | 做什么／对应哪些文件 | 什么时候需要 |
| --- | --- | --- |
| 本地 Mac 或 Linux 电脑 | 做蓝牙配对；Mac 用 `mac_pairing.py`，Linux 用官方安装器。同一台电脑也可执行部署命令。 | 首次配对和上传状态；以后可选地做管理操作。日常使用时可以关机。 |
| Google Cloud → **Cloud Run** | 运行 `app.py`、`muse_sender.py`、`muse_transport.py`、`audio.py` 和共享的 `pebble_ring_bridge.py`，接收 Webhook 并发送给 Muse。 | 每条笔记到达时；空闲实例可缩到零。 |
| Google Cloud → **Cloud Storage** | 保存配对凭证、Webhook 密钥、租约和投递状态。“桶”就是存这些文件的容器；中转不保留笔记正文／录音。 | 在请求之间持续保存状态，即使 Cloud Run 暂无运行实例也保留。 |
| 部署终端：**本地 Mac／Linux 终端，或 Cloud Shell** | 运行 `deploy.sh`、`verify_webhook.py` 和检查。Cloud Shell 在浏览器里打开，但命令实际运行在 Google 的环境中。 | 部署、更新和检查时使用，不参与日常转发；部署终端选一种即可。 |
| Google Cloud → **Cloud Build + Artifact Registry** | 根据 `Dockerfile` 和依赖打包、测试，把生成的镜像保存起来供 Cloud Run 启动。 | 发布版本时运行构建，之后镜像保留；配对凭证不进入构建。 |
| GitHub | 保存公开源码、指南和 PR 历史。 | 分发代码／审阅，不接收戒指 Webhook。 |
| Meta Muse | 处理提交的指令并生成回答。 | 每条提交的指令；它与自己的 Mac、Google 项目是不同的服务。 |

官方介绍：[Cloud Run](https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run)、[Cloud Storage](https://docs.cloud.google.com/storage/docs/introduction)、[Cloud Shell](https://docs.cloud.google.com/shell/docs)、[Cloud Build](https://docs.cloud.google.com/build/docs/overview)。中转没有托管在 Codex/OpenAI 的机器上。GitHub 上传与 Google 部署是两件事，本仓库没有“合并即部署”的自动流程。

### 4.2 相对原始 Muse 官方示例的变化

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
| 网络目的地 | SDK 支持自定义端点和重定向 | 固定官方 Muse 地址、拒绝重定向／环境代理；固定 Google Storage 端点 |
| 部署、验证、复现 | 一个本地监听示例 | 增加容器、最小权限部署、对应模块测试及本指南 |

**固定的 SDK 版本：** [Meta 官方提交 `08f89ed`](https://github.com/facebookincubator/muse-gadget-sdk/commit/08f89ed329918c27d53b11192065ad6b2cb0e868)。这是**公开 Git 仓库的源码版本编号**，使用该版本的人都用同一个编号；不是你的 SDK token、账号、设备身份或配对码。[运行文件与对应测试](linux/examples/pebble_ring_bridge_cloudrun/README.md)。

### 4.3 最终行为与限制

| 最终行为／限制 | 对使用者意味着什么 |
| --- | --- |
| 载荷 | **Both** 在同一条 Muse 消息里提交 Pebble 转写和完整转换后的录音；也兼容仅录音、仅文字。Muse 决定如何使用／展示，可能再次转写录音。 |
| 录音 | 输入最多 **8 MiB、60 秒**，在云端本地转为 PCM16 单声道 16 kHz WAV（SDK 上限 2 MiB）；无效或过长录音整条拒绝，不截断后执行。 |
| 投递 | `delivered` 是提交确认，不等于任务完成。确认不明时先到 Muse 核对，再决定是否重发；重复请求不自动重复执行。 |
| 延迟 | Pebble／iOS 后台收录和语音识别可能延迟上传；手机发送笔记后，中转才能处理。 |
| 保存与访问 | 私有存储保存轮换凭证、Webhook 密钥和投递状态，不保存正文／录音。Google 日志可能保留请求元数据；管理员／服务商访问权限仍适用。 |
| 连接范围 | 云端转发固定连接官方 Muse／Google 地址；拒绝 Muse 重定向／环境代理，以及 Storage 端点覆盖。这是应用层限制，不是操作系统沙箱，也不限制其他 App 或服务商后台。 |
| 可用性 | 2026-11-18 的受控复验在提交前遇到 Muse Link HTTP 502，该次投递尚未确认；这不代表已确认 Meta 全局故障。部署验收前需验证一条新的真实消息。 |
| 手表 | 官方 Muse 支持 Watch，但本桥接回答的同步和通知仍未验证。 |

服务闲时缩到零，低频使用可能落入免费额度，但预算提醒不是硬性消费上限。请核对[当前定价](https://cloud.google.com/run/pricing)和[实际花费](#46-查看实际花费)。文件／测试细节和审查历史可查[代码目录](linux/examples/pebble_ring_bridge_cloudrun/README.md)及 [PR](https://github.com/wli12/muse-gadget-sdk-for-pebble-ring/pull/1)。

### 4.4 注册 Google Cloud 并准备项目

1. 打开 [Google Cloud 官方免费入口](https://cloud.google.com/free)，用已有 Google 账户登录，或按页面创建 Google 账户。符合条件的新用户可以选择官方免费试用；额度、期限和资格以该页面为准。
2. 按 Google 注册页面填写实际地区及所需信息，完成身份和付款方式验证。**即使用免费额度，项目仍需要有效的结算账户。** 详见[结算账户说明](https://docs.cloud.google.com/billing/docs/how-to/create-billing-account)与[免费试用 FAQ](https://cloud.google.com/signup-faqs)。
3. 进入 [Google Cloud Console](https://console.cloud.google.com/)，打开项目选择器，选择 **New project / 新建项目**，创建本方案专用项目；详见[创建项目](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects)。记下 **Project ID**，部署时填入 `PROJECT_ID`；它不是项目显示名称或项目数字编号。
4. 在项目的 **Billing / 结算** 页面，确认项目已关联可用结算账户；如未关联，按[关联结算账户说明](https://docs.cloud.google.com/billing/docs/how-to/modify-project)操作。项目与结算账户是两个不同概念。
5. 在 Console 右上角启动 **Cloud Shell**（已预装 `gcloud`），或在本机安装 Google Cloud CLI。Codex 操作参考会执行 `gcloud auth login` 并选择项目；BLE 配对仍在本地 Mac/Linux 完成。
6. 可为该项目设置[预算提醒](https://docs.cloud.google.com/billing/docs/how-to/budgets)。提醒型预算不会自动停止消费。少量使用可能落入免费额度，但不能保证永久零账单；试用不会自动升级成付费账户，试用到期后资源可能停止，是否升级由账户所有者决定。费用与资源限制见第 4.3 节。

### 4.5 免费试用金

**核对于 2026-11-18：** Google 当前为符合条件的新用户提供 **300 美元 Free Trial Credits，有效期 90 天**。后续复现时，请通过 [Google Cloud 免费入口](https://cloud.google.com/free)和[官方试用说明](https://docs.cloud.google.com/free/docs/free-cloud-features)核对当时的金额与资格。

试用金与每月 Free Tier 免费额度是两回事。**在试用金有效且有余额时，超出免费额度的符合条件用量可以先抵扣试用金**，因此少量超额不一定需要自己付钱。[官方 FAQ](https://cloud.google.com/signup-faqs)解释了适用范围和计费：没有手动升级时，试用金耗尽或试用到期会关闭试用结算账户并停止资源，不会自动转为扣费；手动升级付费账户后，未被覆盖的用量可能收费。请在自己的 Billing 页面确认账户状态、余额和到期日；试用金不等于永久免费。

### 4.6 查看实际花费

1. 打开 [Google Cloud 费用报告](https://console.cloud.google.com/billing/reports)，选择当前项目关联的结算账户。
2. 日期选本月，或从部署日期到今天；在 **Projects / 项目** 中只选自己的 `PROJECT_ID`，再用 **Group by → Service / 按服务分组**，分别看 Cloud Run、Cloud Storage、Cloud Build 和 Artifact Registry。
3. 同时查看使用费用、**Savings / 抵扣与折扣**以及抵扣后的 **Subtotal / 小计**；也看抵扣前费用，区分“被试用额度抵扣”和“没有资源消耗”。正式账单可切到结算周期，或查看 [Cost table / 费用明细表](https://docs.cloud.google.com/billing/docs/how-to/cost-table)。
4. 费用数据有延迟，刚部署显示 0 不代表最终账单为 0。详见[官方费用报告说明](https://docs.cloud.google.com/billing/docs/how-to/reports)。

Cloud Run 的免费额度按结算账户跨项目汇总；存储、构建、镜像存储及网络分别有自己的价格和额度。这是适合低频使用的低成本选择，不保证零账单；上方有定价参考链接。

### 4.7 许可证

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
