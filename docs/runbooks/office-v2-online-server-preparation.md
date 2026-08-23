# Office V2 在线服务器准备

本流程不依赖 GitHub、GHCR 或模型离线包。电脑只上传由固定 Git commit 导出的源码快照；服务器从官方容器源和 Ollama 模型源在线取得依赖，并在本机完成镜像构建。

## 1. 电脑侧产物

共同 checkpoint 建立后运行：

```powershell
pwsh -File .\scripts\export_server_source_snapshot.ps1 -Commit <full-commit>
```

输出目录包含：

- `wp2-redteam-source-<commit>.tar.gz`
- 同名 `.sha256`
- 同名 `.source.json`

源码快照不是旧式离线部署包：它不包含模型、Python wheel、Node 模块或 Docker 镜像。

## 2. 上传源码

```powershell
scp D:\hxjh\server-source-snapshots\wp2-redteam-source-<commit>.tar.gz root@<server>:/root/
scp D:\hxjh\server-source-snapshots\wp2-redteam-source-<commit>.tar.gz.sha256 root@<server>:/root/
scp D:\hxjh\server-source-snapshots\wp2-redteam-source-<commit>.tar.gz.source.json root@<server>:/root/
```

也可以用租赁平台的文件上传功能；不要求建立远端 Git 仓库。

## 3. 服务器主机检查

服务器应已安装 Docker Engine、Buildx、NVIDIA 驱动和 NVIDIA Container Toolkit。GPU 租赁镜像通常已经提供它们；先只读检查：

```bash
docker version
docker buildx version
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

若其中任一命令失败，应按租赁平台的 Ubuntu 22.04 镜像说明安装对应主机组件后再继续，不要在组件状态不明时运行项目脚本。

## 4. 校验并解压源码

```bash
cd /root
sha256sum wp2-redteam-source-<commit>.tar.gz
cat wp2-redteam-source-<commit>.tar.gz.sha256
mkdir wp2-redteam-<commit>
tar -xzf wp2-redteam-source-<commit>.tar.gz -C wp2-redteam-<commit>
cd wp2-redteam-<commit>
```

`sha256sum` 输出必须与 `.sha256` 中的值完全相同。

## 5. 在线准备模型、依赖和镜像

从 `.source.json` 读取完整 commit 与带 `sha256:` 前缀的快照摘要，然后运行：

```bash
bash scripts/server_prepare_online_office_v2.sh \
  <40-char-source-commit> \
  sha256:<source-snapshot-sha256> \
  /root/trace-g-server-build
```

脚本按以下顺序执行：

1. 拉取固定 Python 基础镜像、Node 基础镜像和 `ollama/ollama:0.32.1`。
2. 由官方 Ollama 源下载 `qwen3.5:27b-q4_K_M` 到服务器工作目录。
3. 校验模型 manifest、config、全部 layer 的摘要、顺序和字节数。
4. 按哈希锁在线下载 Python wheels；Harness 使用 `npm ci` 安装锁定依赖。
5. 构建 LangGraph Agent、DeepSeek Harness Agent、Mutator 和 Controller 四个本地镜像。
6. 写出 `online-model-verification.json` 和 `online-build-receipt.json`。

模型只下载一次。Harness 镜像继承 LangGraph Agent 镜像，Docker 复用相同模型层，不会额外复制一份 17 GB 模型数据。

## 6. 停止门

本步骤只完成服务器素材准备，不启动 Campaign。只有以下条件全部满足，才进入 Stage 6 的正式运行：

- 源码快照摘要匹配共同 checkpoint；
- 在线模型验证文件存在且成功；
- 四个镜像 ID 已写入同一 build receipt；
- LangGraph 与 Harness 使用不同 Campaign ID、数据库和 Corpus；
- 本轮未加入 Judge Runtime、黄金集、CLI 或评分快照；既有冻结计划和标签 Schema 不参与服务器运行。
