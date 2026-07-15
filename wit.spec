# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 读取混淆后的代码目录作为源。如果尚无混淆目录，则回退为当前目录（便于单机测试打包配置）
src_dir = 'build/source_temp' if os.path.exists('build/source_temp') else '.'

# 自动扫描并添加 Cython 编译出来的二进制扩展库 (.so / .dylib)
ext_binaries = []
if os.path.exists(src_dir):
    for root, dirnames, filenames in os.walk(src_dir):
        for filename in filenames:
            if filename.endswith('.so') or filename.endswith('.dylib'):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, src_dir)
                dest_dir = os.path.dirname(rel_path)
                ext_binaries.append((full_path, dest_dir))
                print(f"[SPEC] Bundling extension binary: {full_path} -> {dest_dir}")

# 搜集可能缺失的隐式导入模块（FastAPI/Uvicorn/LangChain 等大量使用动态导入的库）
hidden_imports = []
hidden_imports += collect_submodules('uvicorn')
hidden_imports += collect_submodules('fastapi')
hidden_imports += collect_submodules('langchain')
hidden_imports += collect_submodules('langchain_openai')
hidden_imports += collect_submodules('langchain_core')
hidden_imports += collect_submodules('langgraph')
hidden_imports += collect_submodules('langgraph.checkpoint.sqlite')
hidden_imports += collect_submodules('aiosqlite')
hidden_imports += collect_submodules('cryptography')
hidden_imports += collect_submodules('rich')
hidden_imports += collect_submodules('acps_cli')
hidden_imports += collect_submodules('jwt')
hidden_imports += collect_submodules('dotenv')
hidden_imports += collect_submodules('multipart')
hidden_imports += collect_submodules('itsdangerous')
hidden_imports += collect_submodules('croniter')
hidden_imports += collect_submodules('httpx')
hidden_imports += collect_submodules('tomlkit')
hidden_imports += collect_submodules('pydantic')
hidden_imports += collect_submodules('requests')

# 自动扫描并添加项目内的所有 Python 模块（包含 Cython 编译出来的扩展库）
# 这样能避免任何因模块新增/删除或移动而导致打包遗漏的问题
target_packages = ["cli", "service", "agent", "heartbeat", "provider"]
for pkg in target_packages:
    pkg_dir = os.path.join(src_dir, pkg)
    if not os.path.exists(pkg_dir):
        continue
    for root, _, filenames in os.walk(pkg_dir):
        for filename in filenames:
            if filename.endswith('.py') or filename.endswith('.so') or filename.endswith('.dylib'):
                if filename == '__init__.py':
                    continue
                if filename == 'main.py' and 'cli' in root:
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, src_dir)
                name_without_ext = filename.split('.')[0]
                dir_rel_path = os.path.dirname(rel_path)
                if dir_rel_path:
                    mod_name = dir_rel_path.replace(os.sep, '.') + '.' + name_without_ext
                else:
                    mod_name = name_without_ext
                hidden_imports.append(mod_name)
                print(f"[SPEC] Automatically added internal hidden import: {mod_name}")


a = Analysis(
    [os.path.join(src_dir, 'cli', 'main.py')],
    pathex=[src_dir],
    binaries=ext_binaries,  # 将 Cython .so 二进制库作为扩展二进制导入
    datas=[
        ('web/dist', 'web/dist'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['notebooks', 'tests'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='wit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
