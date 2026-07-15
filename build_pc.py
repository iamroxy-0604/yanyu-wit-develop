#!/usr/bin/env python3
"""
yanyu-wit PC 模式打包自动化脚本 (Cython 编译加密版 - 优化路径解析)
==================================================
1. 检查并确保安装 pyinstaller 和 cython。
2. 自动进入 web 目录编译前端静态网页资源。
3. 在项目根目录下执行 Cython 编译，将核心代码编译为本地 C 扩展二进制库（.so）。
4. 将编译好的二进制结构完整拷贝到 build/source_temp 中，并在其中删除所有 .py 源代码。
5. 在项目根目录下清理所有生成的二进制库和中间 C 文件，保持本地开发目录纯净。
6. 调用 pyinstaller，读取 wit.spec 配置文件，打包生成 Mac/Linux 单文件二进制程序。
7. 清理临时目录。
"""

import os
import sys
import subprocess
import shutil
import fnmatch
from pathlib import Path

# 定义颜色常量，便于控制台展示
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
NC = "\033[0m"

PROJECT_ROOT = Path(__file__).parent.resolve()
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_BIN = VENV_DIR / "bin"
PYTHON_BIN = VENV_BIN / "python"
PYINSTALLER_BIN = VENV_BIN / "pyinstaller"


def print_step(msg: str):
    print(f"\n{BLUE}=== 🚀 {msg} ==={NC}")


def print_info(msg: str):
    print(f"{GREEN}[INFO]{NC} {msg}")


def print_warn(msg: str):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def print_error(msg: str):
    print(f"{RED}[ERROR]{NC} {msg}")


def run_command(cmd: list[str], cwd: Path = PROJECT_ROOT, shell: bool = False, env: dict = None):
    """封装命令执行逻辑，打印命令详情并捕获错误。"""
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    print(f"👉 执行命令: {cmd_str}")
    result = subprocess.run(cmd, cwd=str(cwd), shell=shell, env=env)
    if result.returncode != 0:
        print_error(f"命令执行失败，退出码: {result.returncode}")
        sys.exit(result.returncode)


def check_and_install_dependencies():
    print_step("第一步：检查/安装打包与编译依赖 (pyinstaller, cython)")
    
    # 确保运行环境在 .venv 内
    if not VENV_DIR.exists():
        print_error("未检测到本地虚拟环境 .venv，请先使用 uv sync 或 python -m venv 创建。")
        sys.exit(1)

    # 检查或安装 pyinstaller 与 cython
    packages_to_install = []
    
    # 运行 VENV 中的 python 检查模块是否可用
    try:
        subprocess.run([str(PYTHON_BIN), "-c", "import PyInstaller"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print_info("PyInstaller 已安装。")
    except subprocess.CalledProcessError:
        packages_to_install.append("pyinstaller")
        
    try:
        subprocess.run([str(PYTHON_BIN), "-c", "import Cython"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print_info("Cython 已安装。")
    except subprocess.CalledProcessError:
        packages_to_install.append("cython")

    if packages_to_install:
        print_warn(f"未检测到依赖 {packages_to_install}，正在通过 uv pip install 自动安装...")
        env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / ".venv")}
        run_command(["uv", "pip", "install"] + packages_to_install, env=env)


def build_frontend():
    print_step("第二步：构建 React 前端静态资源")
    web_dir = PROJECT_ROOT / "web"
    
    if not (web_dir / "package.json").exists():
        print_error("未在 web/ 目录下找到 package.json 文件！")
        sys.exit(1)

    print_info("开始编译前端页面...")
    # 在 web 目录下执行 npm install && npm run build
    if not (web_dir / "node_modules").exists():
        print_warn("未检测到 node_modules，正在执行 npm install...")
        run_command(["npm", "install"], cwd=web_dir)
    
    run_command(["npm", "run", "build"], cwd=web_dir)
    print_info("前端静态资源构建完成。")


def compile_code_with_cython():
    print_step("第三步：在根目录下进行 Cython 编译并装配临时打包目录")
    
    # 1. 编写临时根目录下的 setup_cython.py
    setup_code = """import os
import fnmatch
from setuptools import setup
from Cython.Build import cythonize

py_files = []
for folder in ["cli", "service", "agent", "heartbeat", "provider"]:
    for root, dirnames, filenames in os.walk(folder):
        for filename in fnmatch.filter(filenames, '*.py'):
            path = os.path.join(root, filename)
            # 排除入口点 main.py 避免 PyInstaller 无法识别启动脚本
            if filename == 'main.py' and 'cli' in root:
                continue
            # 排除包定义文件 __init__.py 保持 Python 包结构完整
            if filename == '__init__.py':
                continue
            py_files.append(path)

setup(
    ext_modules=cythonize(
        py_files,
        compiler_directives={'language_level': '3', 'annotation_typing': False},
        annotate=False
    )
)
"""
    setup_file = PROJECT_ROOT / "setup_cython.py"
    setup_file.write_text(setup_code, encoding="utf-8")
    
    print_info("正在启动本地 Cython 编译（此步骤包含 C 编译器调用，可能需要十几秒）...")
    # 在根目录执行 Cython 编译为 .so 库
    run_command([str(PYTHON_BIN), "setup_cython.py", "build_ext", "--inplace"], cwd=PROJECT_ROOT)
    
    # 2. 创建临时打包目录并复制编译结果
    temp_dir = PROJECT_ROOT / "build" / "source_temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    print_info("正在装配临时打包目录...")
    for folder in ["cli", "service", "agent", "heartbeat", "provider"]:
        shutil.copytree(PROJECT_ROOT / folder, temp_dir / folder)
    
    # 在临时目录中删除所有 .py 源代码和中间产生的 .c 文件，只保留编译好的 .so 库
    print_info("清理临时目录中的 Python 源码与 C 文件...")
    for root, dirnames, filenames in os.walk(temp_dir):
        for filename in filenames:
            file_path = Path(root) / filename
            # 保留主入口点、包定义 __init__.py 和动态链接库文件
            if filename == "main.py" and "cli" in root:
                continue
            if filename == "__init__.py":
                continue
            if filename.endswith(".so") or filename.endswith(".dylib"):
                continue
            
            # 删除其余 .py 和 .c 文件
            if filename.endswith(".py") or filename.endswith(".c"):
                file_path.unlink()
                
    # 3. 清理项目根目录下的编译残留，保持本地开发树干净
    print_step("第四步：清理开发目录下的 Cython 编译残留")
    setup_file.unlink()
    
    # 删除本地根目录下的编译生成的 .c 文件以及 .so / .dylib 文件
    for folder in ["cli", "service", "agent", "heartbeat", "provider"]:
        for root, dirnames, filenames in os.walk(PROJECT_ROOT / folder):
            for filename in filenames:
                file_path = Path(root) / filename
                if filename.endswith(".c") or filename.endswith(".so") or filename.endswith(".dylib"):
                    file_path.unlink()
                    
    # 删除本地编译产生的 build/ 临时编译目录（注意保留 build/source_temp/ ！）
    local_build_dir = PROJECT_ROOT / "build"
    if local_build_dir.exists():
        for item in local_build_dir.iterdir():
            if item.is_dir() and item.name != "source_temp":
                shutil.rmtree(item)
            elif item.is_file():
                item.unlink()
                
    print_info("本地开发目录已恢复干净。核心二进制编译版已备份至 build/source_temp 准备打包。")


def package_app():
    print_step("第五步：执行 PyInstaller 二进制单文件打包")
    
    final_wit = PROJECT_ROOT / "dist" / "wit"
    if final_wit.exists():
        print_info("清理历史生成的目标 wit 程序...")
        final_wit.unlink()

    # 运行 PyInstaller
    print_info("正在启动 PyInstaller 进行单文件打包并生成 wit 程序...")
    cmd = [
        str(PYINSTALLER_BIN),
        "--clean",
        "-y",
        "wit.spec"
    ]
    run_command(cmd)
    
    # 打包完成后，清理混淆中间目录，保持 workspace 整洁
    temp_dir = PROJECT_ROOT / "build" / "source_temp"
    if temp_dir.exists():
        print_info("清理临时编译目录 build/source_temp...")
        shutil.rmtree(temp_dir)
        
    print_step("🎉 打包流程顺利完成！")
    print_info(f"最终生成的 Mac/Linux PC 模式单文件二进制已输出至：{GREEN}dist/wit{NC}")
    print_info("可尝试在终端执行: ./dist/wit start 启动服务进行验证。")


def main():
    check_and_install_dependencies()
    build_frontend()
    compile_code_with_cython()
    package_app()


if __name__ == "__main__":
    main()
