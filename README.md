# Yanyu-Wit 社交信息服务代理个人助手 CLI

Yanyu-Wit 支持两种运行和使用方式：作为**独立可执行二进制（推荐，免配置依赖）**运行，或通过**源码在开发环境下调试**运行。

---

## 🚀 方式一：使用二进制分发版（适合最终用户）

我们提供了将核心 Python 逻辑通过 Cython 编译为 C 扩展，并使用 PyInstaller 打包封装而成的独立单文件程序 `wit`。它内置了所有依赖及 React 前端静态网页。

### 1. 安装步骤
用户下载发布包后，在终端执行以下一键安装脚本：
```bash
./install.sh
```
该脚本会自动：
- 拷贝 `wit` 二进制文件到用户的主目录 `~/.yanyu-wit/bin/wit`。
- 将安装路径自动加入用户的 Shell 配置文件（如 `~/.zshrc` 或 `~/.bashrc`）。
- 完成环境授权并配置 execution 权限。

> [!NOTE]
> 安装成功后，如果在当前终端运行报错 `command not found: wit`，请先运行 `source ~/.zshrc`（或对应的 Shell 配置文件）以刷新环境变量，或者重新打开一个终端窗口。

### 2. 初始化与 Keycloak 登录
在终端中执行：
```bash
wit init
```
- **步骤 1**：程序将自动唤起浏览器以引导 Keycloak 登录授权（OIDC PKCE 公共客户端流）。
- **步骤 2**：在终端完成配置大模型服务商、模型名称（默认：`deepseek-v4-flash`）、API Base URL（默认：`https://api.deepseek.com`）以及 API Key。
- **步骤 3**：配置前端服务绑定端口（默认：`7020`）。

### 3. 启动前端 Web UI 界面
执行以下命令启动本地服务，程序会自动拉起后台服务并用浏览器打开前端交互界面：
```bash
wit start
```

### 4. 常用指令与命令行交互
```bash
wit --help              # 查看详细指令说明
wit -n "您的问题"        # CLI 交互模式：直接在终端向 Assistant 提问
```

---

## 🛠️ 方式二：使用源码开发调试（适合开发环境）

在开发环境下运行需要本地安装有 Python 3.12+ 以及 `uv` 依赖管理工具。

### 1. 初始化依赖与配置
在项目根目录下执行：
```bash
# 同步 Python 依赖环境
uv sync

# 初始化配置与模型信息（注意：代理可能导致浏览器回传 token 失败）
uv run yanyu-wit init

# 完成实体注册认证
uv run atr auto
uv run atr status
```

### 2. 启动后端 API 服务
在项目根目录下运行：
```bash
uv run python -m service.app
```

### 3. 启动前端页面
进入 `web` 目录并运行 React 调试服务器：
```bash
cd web
npm install
npm run dev
```

---

## 📦 如何重新打包生成 PC 单文件分发包

如果您对源码做出了修改，想要重新构建单文件分发包：
1. 确保本地安装有 `cython` 和 `pyinstaller`（若缺失，脚本会自动使用 `uv` 安装）。
2. 在项目根目录下执行打包脚本：
   ```bash
   python build_pc.py
   ```
3. 构建成功后，单文件程序将输出在 `dist/wit` 路径下，拷贝它与 `install.sh` 即可向用户分发。

---

## 🌐 方式三：SaaS 模式容器化部署指南

本指南面向系统管理员，介绍如何在服务器环境下利用 Docker 容器部署 SaaS（多租户中央化）模式服务。

### 1. 两个 Dockerfile 的作用

*   [Dockerfile.server](file:///Users/mac/Desktop/ACPs/acps-demo/yanyu-wit/Dockerfile.server)：**主服务端容器**。运行 FastAPI 后端网关，托管编译好的前端网页、解析 API 请求并完成数据路由。需要持续运行在后台。
*   [Dockerfile.sandbox](file:///Users/mac/Desktop/ACPs/acps-demo/yanyu-wit/Dockerfile.sandbox)：**命令隔离沙箱镜像**。所有由 LLM Agent 触发的本地脚本和命令行操作（如 `execute` 等工具）都会运行在这个沙箱环境的实例中。**只需要在宿主机上构建好镜像，主服务在运行时会动态创建和销毁其实例，无需手动运行此容器**。

### 2. 部署顺序与步骤

按照以下步骤在服务器上部署：

#### 步骤一：部署基础配套设施
SaaS 模式需要中央数据库与会话锁的支持，请提前部署并获取连接地址：
*   **PostgreSQL** (数据库持久化)
*   **Redis** (分布式锁管理)
*   **Keycloak / OIDC Provider** (注册一个 `Confidential` 类型机密客户端，并获取其 `client_secret`)

#### 步骤二：构建 Agent 沙箱隔离镜像
在宿主机上构建命令沙箱镜像，命名必须为 `yanyu-wit-sandbox:latest`：
```bash
docker build -t yanyu-wit-sandbox:latest -f Dockerfile.sandbox .
```

#### 步骤三：编写 `.env` 配置文件
在项目根目录下创建 `.env` 文件，用于注入配置变量：
```env
# 部署模式 (可选 pc / saas)
WIT_DEPLOY_MODE=saas

# 数据库与缓存
DATABASE_URL=postgresql://<user>:<password>@<db-host>:5432/yanyu_wit
REDIS_URL=redis://<redis-host>:6379/0

# OIDC 认证授权服务配置
OIDC_ISSUER_URL=http://<keycloak-host>:8080/realms/yanyu
OIDC_CLIENT_ID=yanyu-wit
OIDC_CLIENT_SECRET=YOUR_KEYCLOAK_CLIENT_SECRET
```
> 注：当前OIDC_ISSUER_URL`http://10.106.130.104:8080/realms/yanyu`，  
> 客户端密码（OIDC_CLIENT_SECRET）为 `yxoaGdAEXODBxhKX8pgWx4uFMg8wI3uc`。

#### 步骤四：构建并运行主服务端容器
1.  构建主服务镜像：
    ```bash
    docker build -t yanyu-wit-server:latest -f Dockerfile.server .
    ```
2.  挂载**宿主机 Docker 套接字**并在后台运行容器：
    ```bash
    docker run -d -p 7020:7020 --name wit-server \
      -v /var/run/docker.sock:/var/run/docker.sock \
      --env-file .env \
      yanyu-wit-server:latest
    ```
    > [!IMPORTANT]
    > **`-v /var/run/docker.sock:/var/run/docker.sock` 挂载是必须的**。主服务容器需要通过它调用宿主机的 Docker Daemon，从而为各个租户动态创建沙箱容器。

### 3. 如何指定与微调运行模式

运行模式由 `WIT_DEPLOY_MODE` 变量控制，您可以在启动主服务端容器时，通过修改环境变量任意改变其行为：

*   **完全运行为 SaaS 模式**（Postgres 数据库 + Docker 沙箱 + 强制 OIDC Confidential 登录）：
    ```bash
    docker run -d -p 7020:7020 -v /var/run/docker.sock:/var/run/docker.sock -e WIT_DEPLOY_MODE=saas --env-file .env yanyu-wit-server:latest
    ```
*   **切换为 PC 本地模式**（SQLite 数据库 + 免登录/本地账号）：
    ```bash
    docker run -d -p 7020:7020 -e WIT_DEPLOY_MODE=pc yanyu-wit-server:latest
    ```
*   **微调混合模式**：如果您想在单账号下使用数据库与 Docker 容器隔离，可以组合微调开关：
    ```bash
    docker run -d -p 7020:7020 \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -e WIT_DEPLOY_MODE=pc \
      -e WIT_FEATURE_STORAGE_ENGINE=postgresql \
      -e WIT_FEATURE_SANDBOX_TYPE=docker \
      -e DATABASE_URL="postgresql://..." \
      yanyu-wit-server:latest
    ```

